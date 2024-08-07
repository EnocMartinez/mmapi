#!/usr/bin/env python3
"""
This file implements the MetadataCollector, a class implementing metadata access / storage to a MongoDB database.

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 30/11/22
"""
import time

from pymongo import MongoClient
import datetime
import rich
import json
import pandas as pd
import os
from mmm.common import YEL, RST, load_fields_from_dict, validate_schema

try:
    from mmm.schemas import mmm_schemas, mmm_metadata
except ImportError:
    from schemas import mmm_schemas, mmm_metadata


def get_timestamp_string():
    now = datetime.datetime.utcnow().isoformat()
    now = now.split(".")[0] + "Z"
    return now


def strip_mongo_ids(doc: list or dict):
    """
    Strip _id elements from JSON structures
    :param doc: MongoDB document, can be a list a dict or a list of dicts
    :return: dict or list of dicts depending on input
    """
    if type(doc) is dict:
        if "_id" in doc.keys():
            doc.pop("_id")  # remove _id
    elif type(doc) is list:
        for d in doc:
            if "_id" in d.keys():
                d.pop("_id")  # remove _id
    else:
        raise TypeError(f"Expected dict or list of dicts, got {type(doc)}")
    return doc


def validate_key(data, key, key_type, errortype=SyntaxError):
    if key not in data.keys():
        raise errortype(f"Required key {key} not found")
    elif type(data[key]) is not key_type:
        raise errortype(f"Expected type of {key}  is {key_type}, got {type(data[key])}")




def init_metadata_collector(secrets: dict):
    """
    Initializes a Metadata Collector object from secrets file
    :param secrets: dict object from the secrets yaml file
    :returns: MetadataCollector object
    """
    assert "mmapi" in secrets.keys(), "mmapi key not found!"
    __required_keys = [
        "connection",
        "database",
        "default_author",
        "organization"
    ]
    for key in __required_keys:
        assert key in secrets["mmapi"].keys(), f"key '{key}' not found in secrets[\"mmapi\"]"

    return MetadataCollector(secrets["mmapi"]["connection"],
                             secrets["mmapi"]["database"],
                             secrets["mmapi"]["default_author"],
                             secrets["mmapi"]["organization"])


def init_metadata_collector_env():
    """
    Initializes a Metadata Collector object from environment variables
    :returns: MetadataCollector object
    """
    __required_keys = [
        "mmapi_connection",
        "mmapi_database",
        "mmapi_default_author",
        "mmapi_organization"
    ]
    for key in __required_keys:
        assert key in os.environ.keys(), f"key '{key}' not environment variables"

    return MetadataCollector(os.environ["mmapi_connection"],
                             os.environ["mmapi_database"],
                             os.environ["mmapi_default_author"],
                             os.environ["mmapi_organization"])


class MetadataCollector:
    def __init__(self, connection: str, database_name: str, default_author: str, organization: str, ensure_ids=True):
        """
        Initializes a connection to a MongoDB database hosting metadata
        :param connection: connection string
        :param database_name: database name
        :param default_author: default author (must be a people #id)
        :param organization: organization that owns the infrastructure (must be an organizations #id)
        :param ensure_ids: make sure that all elements have a unique #id
        """
        self.default_author = default_author
        self.organization = organization
        self.__connection_chain = connection
        self.__connection = MongoClient(connection, serverSelectionTimeoutMS=2000)
        self.database_name = database_name
        self.history_database_name = database_name + "_hist"
        self.db = self.__connection[self.database_name]  # database with latest data
        self.db_history = self.__connection[self.history_database_name]  # database with all archived versions
        # To create new collections, add them here
        self.collection_names = ["sensors", "stations", "variables", "qualityControl", "people", "units", "processes",
                                 "organizations", "datasets", "operations", "activities", "projects", "resources",
                                 "programmes"]
        if ensure_ids:
            self.__ensure_ids()  # make sure that all elements have a unique #id

        self.metadata_schema = mmm_metadata  # JSON schema for
        self.schemas = mmm_schemas

        # The cache stores in memory documents already retrieved from the database, this will significantly speed up
        # the system and reduce the database workload
        self.__cache_timeout_s = 300  # 5 minutes
        self.__cache = {}

        self.used_time = 0

    def __add_to_cache(self, collection, doc):
        """
        Adds a document to the cache
        :param collection:  collection
        :param doc: document to add
        :return:
        """
        doc_id = doc["#id"]
        if collection not in self.__cache.keys():
            self.__cache[collection] = {}

        self.__cache[collection][doc_id] = (time.time(), doc)

    def __get_from_cache(self, collection, doc_id):
        """
        Get a document from the cache
        :param collection:
        :param doc:
        :return: the document or None if the document is not on the cache (or timeout has expired)
        """
        if collection not in self.__cache.keys():
            return None  # Collection not found
        elif doc_id not in self.__cache[collection].keys():
            return None  # Document not found
        # get the document
        timestamp, doc = self.__cache[collection][doc_id]
        # check the timeout condition
        if time.time() - timestamp > self.__cache_timeout_s:
            del self.__cache[collection][doc_id]
            return None
        return doc


    def __ensure_ids(self):
        """
        Ensures that all elements in all collections have #id attribute set with a unique value
        :return: None
        :raises:  ValueError if duplicated elements are found or KeyError if #id is not present
        """
        errors = 0
        for c in self.collection_names:
            documents = self.get_documents(c)
            ids = []
            for doc in documents:
                try:
                    ids.append(doc["#id"])
                except KeyError:
                    rich.print(f"[red]{json.dumps(doc, indent=2)}")
                    raise KeyError("#id not found in doc")
            id_list = []
            for check_id in ids:
                if check_id not in id_list:
                    id_list.append(check_id)
                else:
                    rich.print(f"[red]ERROR: collection {c} has duplicated id='{check_id}'")
                    errors += 1
        if errors:
            raise ValueError(f"Got {errors} duplicated elements!")

    @staticmethod
    def validate_document(doc: dict, collection: str, exception=True):
        """
        This method takes a document and checks if it is valid. A document should at least contain the following fields
            #id: (string) id of the document
            #version: (int) version of the document
            #creationDate: (string) date of the first version of the document
            #modificationDate: (string) date with the latest version
        :param doc: JSON dict with document data
        :param collection: collection name
        :param exception: Wether to throw and exception on error or not
        :return: Nothing
        :raise: SyntaxError
        """
        errors = []
        errors = validate_schema(doc, mmm_metadata, errors=errors)
        if collection not in mmm_schemas.keys():
            rich.print(f"[yellow]WARNING: no schema for '{collection}'")
        else:
            errors = validate_schema(doc, mmm_schemas[collection], errors=errors)
        if errors:
            for e in errors:
                rich.print(f"[red]ERROR: {e}")

            if exception:
                raise ValueError(f"Document not valid: {str(errors)}")
            return False  # return false if exception=False
        else:
            return True  # document is valid

    @staticmethod
    def strip_metadata_fields(doc: dict) -> dict:
        """
        Takes a document and strips all metadata (id, version, author...)
        :param doc: dict
        :return: cleaned document
        """
        return {key: value for key, value in doc.items() if not key.startswith("#")}

    def get_identifiers(self, collection, history=False):
        """
        Get a list of all ids within a collection
        :param collection: collection name
        :return: list of ids
        """
        documents = self.get_documents(collection, history=False)
        return [d["#id"] for d in documents]

    def get_documents(self, collection: str, mongo_filter=None, history=False) -> list:
        """
        Return all documents in a collection
        :param collection: collectio name
        :param mongo_filter: filters to apply to the query
        :param history: search in archived documents
        :return: list of documents that match the criteria
        """
        if mongo_filter is None:
            mongo_filter = {}
        if history:
            db = self.db_history
        else:
            db = self.db

        if collection not in self.collection_names:
            raise LookupError(f"Collection {collection} not found!")

        db_collection = db[collection]
        cursor = db_collection.find(mongo_filter)
        documents = [document for document in cursor]  # convert cursor to list
        return strip_mongo_ids(documents)

    # --------- Document Operations --------- #
    def insert_document(self, collection: str, document: dict, author: str = "", force=False, force_meta=False,
                        update=False):
        """
        Adds metadata to a document and then inserts it to a collection.
        :param collection: collection name
        :param document: json doc to be inserted
        :param author: people #id of the author (if not set the default author will be set)
        :param force: insert even if the document fails the validation
        :param force_meta: insert the document metadata as is (by default new metadata is generated)
        :param update: if set update the document if a previous version existed
        :return: document with metadata
        """
        # first check that the doc's #id is not already registered

        if not author:
            author = self.default_author

        if collection not in self.collection_names:
            raise ValueError(f"Collection {collection} not valid!")

        if document["#id"] in self.get_identifiers(collection):
            if update:
                return self.replace_document(collection, document["#id"], document, force=force)
            else:
                raise NameError(f"{collection} document with id {document['#id']} already exists!")

        if not force_meta:  # By default generate the metadata
            now = get_timestamp_string()
            new_document = {
                "#id": document["#id"],
                "#version": 1,
                "#creationDate": now,
                "#modificationDate": now,
                "#author": author,
            }
            contents = {key: value for key, value in document.items() if not key.startswith("#")}
            new_document.update(contents)
        else:  # force input metadata
            new_document = document

        self.validate_document(new_document, collection, exception=(not force))
        self.db[collection].insert_one(new_document.copy())  # use copy to avoid pymongo to modify original dict
        self.db_history[collection].insert_one(new_document.copy())  # use copy to avoid pymongo to modify original dict
        return new_document

    def exists(self, collection, document_id):
        if self.db[collection].find_one({"#id": document_id}):  # if it is found
            return True
        return False

    def get_document(self, collection: str, document_id: str, version: int = 0):
        """
        Gets a single document from a collection. If version is used a specific version in the historical database
        will be fetched

        :param collection: name of the collection
        :param document_id: id of the document
        :param version: version (int)
        """
        t = time.time()
        # First check if the doc is in the cache
        doc = self.__get_from_cache(collection, document_id)
        if doc:  # if not None, we have it on cache, return it
            self.used_time += time.time() - t
            return doc

        if version:
            db = self.db_history
            rich.print("[purple]Using historical database")
        else:
            db = self.db

        if collection not in self.collection_names:
            raise LookupError(f"Collection {collection} not found!")

        db_collection = db[collection]
        mongo_filter = {"#id": document_id}
        if version:
            mongo_filter["#version"] = int(version)
        cursor = db_collection.find(mongo_filter)

        documents = [document for document in cursor]  # convert cursor to list
        n = len(documents)
        if len(documents) > 1:
            raise AssertionError(f"Got {n} multiple documents in collection {collection} with #id = '{document_id}'")
        elif len(documents) == 0:
            raise LookupError(f"Element {collection} with   with #id = '{document_id}' not found")

        doc = strip_mongo_ids(documents[0])
        # Adding document to the cache
        self.__add_to_cache(collection, doc)
        self.used_time += time.time() - t
        return doc

    def get_document_history(self, collection, document_id):
        """
        Looks for all versions of a document in the history database and returns them all.
        """
        db = self.db_history
        db_collection = db[collection]
        filter = {"#id": document_id}
        cursor = db_collection.find(filter)
        documents = [strip_mongo_ids(document) for document in cursor]  # convert cursor to list
        if len(documents) == 0:
            raise LookupError(f"Element {collection} with  filter {json.dumps(filter)} not found")

        return documents

    def replace_document(self, collection: str, document_id: str, document: dict, author=False, force=False):
        """
        Takes a document in the database, updates the metadata and adds new info. Metadata (#version, #modificationDate,
        etc.) are modified automatically. All other fields are replaced by the fields in the input document.
        :param document_id: #id
        :param collection: collection name
        :param document: elements to update
        :param author: author of the document
        :param force: If true, ignore metadata checks and insert document
        """

        if document["#id"] != document_id:
            raise ValueError("Document #id does not match with parameter id")

        if not author:
            author = self.default_author

        old_document = self.get_document(collection, document_id)  # getting old metadata
        metadata = {key: value for key, value in old_document.items() if key.startswith("#")}
        old_contents = {key: value for key, value in old_document.items() if not key.startswith("#")}
        metadata["#version"] += 1
        metadata["#modificationDate"] = get_timestamp_string()
        metadata["#author"] = author  # update author

        # keep only elements that are not metadata
        contents = {key: value for key, value in document.items() if not key.startswith("#")}

        if contents == old_contents:
            if force:
                rich.print(f"[yellow]WARNING! document {document['#id']} is identical to previous one")
            else:
                raise AssertionError("old and new document are equal, aborting")

        new_document = metadata  # start new document with metadata
        new_document.update(contents)  # add contents after metadata
        self.validate_document(new_document, collection, exception=(not force))
        self.db[collection].replace_one({"#id": document_id}, new_document.copy())
        self.db_history[collection].insert_one(new_document.copy())
        return new_document

    def delete_document(self, collection: str, document_id: str):
        """
        drops an element in collection
        :param document_id: #id
        :param collection: collection name
        """
        db_collection = self.db[collection]
        db_collection.delete_one({"#id": document_id})

    # --------- Wrappers for collections --------- #
    def get_sensor(self, identifier):
        return self.get_document("sensors", identifier)

    def get_variable(self, identifier):
        return self.get_document("variables", identifier)

    def get_station(self, identifier):
        return self.get_document("stations", identifier)

    def get_unit(self, identifier):
        return self.get_document("units_old", identifier)

    def get_quality_control(self, identifier, qartod_only=False):
        conf = self.get_document("qualityControl", identifier)
        if qartod_only:  # return only the artod field
            return {"qartod": conf["qartod"]}

    def get_people(self, identifier):
        return self.get_document("people", identifier)

    def get_organization(self, identifier):
        return self.get_document("organizations", identifier)

    def get_qc_from_sensor(self, sensor, qartod_only=False):
        """
        Takes all QC configurations from a sensor and merges it to a single dict
        :param sensor: sensor id
        :param qartod_only: is set only the qartod field is returned, useful to use directly in QC scripts
        :return: dict with all qc config
        """
        sensor = self.get_sensor(sensor)
        qc = {}
        for variable in sensor["variables"]:
            if "@qualityControl" in variable.keys():
                varconfig = self.get_quality_control(variable["@qualityControl"], qartod_only=qartod_only)
                qc[variable["@variables"]] = varconfig
        return qc

    def get_sensor_variables(self, sensor_id):
        """
        Returns a dict with all the sensor variables
        :param sensor_id:
        :return: A dict with all the variables {"var1": { ... }, "VAR2": { ...}}
        """
        variables = {}
        sensor = self.get_sensor(sensor_id)
        for variable in sensor["variables"]:
            variable_id = variable["@variables"]
            variables[variable_id] = self.get_variable(variable_id)
        return variables

    def get_polar_variables(self, sensor_id):
        """
        Returns two list with the modules and angles variables in a dataset. Both lists have the same size
        :param sensor_id: sensor identifier
        :return: two lists with same size: [module_list, angle_list]
        """
        variables = self.get_sensor_variables(sensor_id)
        modules = []
        angles = []
        for var in variables.values():
            if "polar" in var.keys() and var["polar"]["module"] == var["#id"]:
                modules.append(var["polar"]["module"])
                angles.append(var["polar"]["angle"])
        return modules, angles

    def get_log_variables(self, sensor_id):
        """
        Returns a list of all the variables that are
        :param sensor_id:
        :return: two lists with same size: [module_list, angle_list]
        """
        variables = self.get_sensor_variables(sensor_id)
        return [identifier for identifier, var in variables.items() if
                "logarithmic" in var.keys() and var["logarithmic"]]

    def get_no_average_variables(self, sensor_id):
        """
        Returns a list of all the sensor variables that have averaging disabled
        :param sensor_id: sensor identifier
        :return: two lists with same size: [module_list, angle_list]
        """
        variables = self.get_sensor_variables(sensor_id)
        return [identifier for identifier, var in variables.items() if "average" in var.keys() and not var["average"]]

    def get_people_from_role(self, sensor_id, role):
        """
        Returns a person instance based on their role on a sensor
        :param sensor_id:
        :param role: role to search
        :return:
        """

        for contact in self.get_sensor(sensor_id)["contacts"]:
            if contact["role"] == role:
                return self.get_people(contact["@people"])

        raise LookupError(f"Person with role {role} not found")

    def get_organization_from_role(self, sensor_id, role):
        """
        Returns a person instance based on their role on a sensor
        :param sensor_id:
        :param role: role to search
        :return:
        """
        for contact in self.get_sensor(sensor_id)["contacts"]:
            if contact["role"] == role:
                return self.get_organization(contact["@organizations"])
        raise LookupError(f"Organizations with role {role} not found")

    def reset_version_history(self):
        """
        USE WITH CAUTION! This method will
            1. Drop history database
            2. Reset all versions in database to v=1
            3. Copy all documents from database to history database
        :return: Nothing
        """
        self.__connection.drop_database(self.db_history)
        self.db_history = self.__connection[self.history_database_name]
        for col in self.collection_names:
            _ = self.db_history[col]  # Create collection
            docs = self.get_documents(col)
            for doc in docs:
                if doc["#version"] != 1:
                    rich.print(f"modifying {col} {doc['#id']} v={doc['#version']}")
                    # Modify directly through pymongo
                    self.db[col].replace_one({"#id": doc['#id']}, doc.copy())
                self.db_history[col].insert_one(doc)

    def get_contact_by_role(self, doc: dict, role: str) -> {dict, str}:
        """
        Loops through the contacts section in a document and returns the id and the collection type (organization or
        people) of the first contact that has a certain role.
        :param doc:
        :param role:
        :return: doc, collection
        """

        if "contacts" not in doc.keys():
            raise LookupError(f"Document with #id={doc['#id']} does not have contacts!")

        for contact in doc["contacts"]:
            if contact["role"] == role:
                if "@people" in contact.keys():
                    return self.get_document("people", contact["@people"]), "people"
                elif "@organizations" in contact.keys():
                    return self.get_document("organizations", contact["@organizations"]), "organizations"
                else:
                    raise ValueError("Contact type not valid!")
        raise LookupError(f"Contact with role '{role}' not found in document '{doc['#id']}'")

    def __check_link(self, parent_collection: str, parent_doc_id: str, target_collection: str, target_doc: str,
                     errors: list) -> list:
        """
        Checks if the document which a link is pointing really exists, ensuring the correctness of the link itself,

        :param parent_collection: Collection of the document being analyzed
        :param parent_doc_id: document ID
        :param target_collection: collection where the link points
        :param target_doc: document ID where the link points
        :param errors: list with all errors as string
        :return: error list with new errors
        """
        try:
            d = self.get_document(target_collection, target_doc)
            if not d:
                raise ValueError("Never null!")
        except LookupError:
            errors.append(f"{parent_collection}:'{parent_doc_id}' broken link {target_collection}:'{target_doc}'")
        return errors

    def __check_dict(self, collection: str, doc_id: str, doc: dict, errors: list) -> list:
        """
        Look for links within a document or document exceropt. If found, ensure that those links are correct
        :param collection: collectio name
        :param doc_id:
        :param doc: document or document excerpt
        :param errors: list of errors where new errors will be appended
        :return: errors
        """
        for key, value in doc.items():
            if type(key) != str:
                raise ValueError(f"Keys must be strings! Error when analyzing {doc_id} from collection {collection}")

            # Ensure links
            if key.startswith("@"):
                if type(value) == str:
                    errors = self.__check_link(collection, doc_id, key[1:], value, errors)
                elif type(value) == list:
                    for val in value:
                        errors = self.__check_link(collection, doc_id, key[1:], val, errors)
                else:
                    raise ValueError(f"Wrong type in {doc_id} {key}: value type {type(value)}")

            # Process other objects
            elif type(value) == dict:
                errors = self.__check_dict(collection, doc_id, value, errors)
            elif type(value) == list:
                for subvalue in value:
                    if type(subvalue) == dict:
                        errors = self.__check_dict(collection, doc_id, subvalue, errors)
        return errors

    def __warning(self, collection, doc, warnings):
        """
        Hardcoded warnings
        """
        if collection == "sensors":
            if "deployment" in doc.keys():
                w = f"{collection}:{doc['#id']} 'deployment' in Sensors is deprecated!"
                rich.print(f"[yellow]{w}")
                warnings.append(w)

            if "dataType" in doc.keys():
                if "dataSource" in doc.keys():
                    w = f"{collection}:{doc['#id']} 'dataSource' in datasets root will be ignored!"
                    rich.print(f"[yellow]{w}")
                    warnings.append(w)
        return warnings

    def healthcheck(self, collections=None):
        """
        Ensure all relations in the database. For every document validate it against the generic schema (metadata
        schema), collection schema and scan the document for broken relation (@-fields).
        """
        if collections is None:
            collections = []
        assert (type(collections) is list)
        rich.print("[cyan] ==> Ensuring all relations in MongoDB database <==")
        errors = []
        warnings = []
        if not collections:
            collections = self.collection_names

        for col in collections:
            schema = {}
            if col in self.schemas.keys():
                schema = self.schemas[col]
            else:
                rich.print(f"[yellow]Missing schema for collection {col}!")

            docs = self.get_documents(col)
            for doc in docs:
                # Validate against metadata schema and collection-specific schema
                errors = validate_schema(doc, self.metadata_schema, errors)
                if schema:
                    errors = validate_schema(doc, schema, errors, verbose=True)
                # Check relation for author
                errors = self.__check_link(col, doc["#id"], "people", doc["#author"], errors)
                # Scan the rest of the document and check its relations
                errors = self.__check_dict(col, doc["#id"], doc, errors)

                # Check if there are any warnings
                warnings = self.__warning(col, doc, warnings)

        if warnings:
            rich.print("\nWarning report")
            [rich.print(f"  [yellow]{warning}") for warning in warnings]
            rich.print(f"[yellow]Got {len(warnings)} warnings!")

        if errors:
            rich.print("\nError report")
            [rich.print(f"  [yellow]{error}") for error in errors]
            rich.print(f"[red]Got {len(errors)} errors!")
        else:
            rich.print(f"[green]\n=) =) Congratulations! You have a healthy database (= (=\n")

    def get_station_position(self, station_name, timestamp) -> (float, float, float):
        """
        Returns (latitude, longitude, depth) for a station at a particular time. It looks for all deployments of a
        station and selects the one immediately before the selected time.
        """
        hist = self.get_documents("activities", mongo_filter={"type": "deployment", "appliedTo.@stations": station_name})
        data = {
            "time": [],
            "latitude": [],
            "longitude": [],
            "depth": [],
        }
        for dep in hist:
            data["time"].append(dep["time"])
            data["latitude"].append(dep["where"]["position"]["latitude"])
            data["longitude"].append(dep["where"]["position"]["longitude"])
            data["depth"].append(dep["where"]["position"]["depth"])

        df = pd.DataFrame(data)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
        df = df.sort_index(ascending=False)

        # Loop backwards in deployments to get the first deployment before the timestamp
        for idx, row in df.iterrows():
            # If timezone is not present, force UTC
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            tsamp = pd.Timestamp(timestamp)
            if tsamp.tz is None:
                tsamp = tsamp.tz_localize("UTC")
            # Compare
            if tsamp >= idx:
                return row["latitude"], row["longitude"], row["depth"]

        raise LookupError(f"Deployment for station={station_name} not found!")

    def drop_all(self):
        """
        Deletes ALL documents from ALL collections, USE WITH CAUTION!
        """
        for col in self.collection_names:
            docs = self.get_documents(col)
            for doc in docs:
                self.delete_document(col, doc["#id"])


def get_station_deployments(mc: MetadataCollector, station: dict) -> list:
    """
    Looks for all the station deployment
    [
        ((lat, lon, depth), date1),
        ((lat, lon, depth), date2),
        ...
    ]
    """
    assert type(mc) is MetadataCollector
    if type(station) is str:
        station = mc.get_document("stations", station)
    elif type(station) is dict:
        pass
    else:
        raise ValueError(f"Wrong type in station, expected str or dict, got {type(station)}")

    station_id = station["#id"]
    deployments = []  # array of (stationId, deploymentTime)

    # Get all activities with type=deployment and involving this station
    hist = mc.get_documents("activities", mongo_filter={"type": "deployment", "appliedTo.@stations": station_id})

    for dep in hist:
        deployment_time = dep["time"]

        # The deployment station can be at the 'appliedTo' or at 'where' section
        if "position" not in dep["where"].keys():
            raise ValueError("A station deployment should ALWAYS use a 'position'")

        deployments.append((dep, deployment_time))

    if len(deployments) == 0:
        raise LookupError(f"No deployments found for station {station_id}")

    deployments = sorted(deployments, key=lambda x: x[1])  # order by time
    return deployments


def get_sensor_deployments(mc: MetadataCollector, sensor_id: str, station="") -> list:
    """
    Looks for all stations where a sensor has been deployed. If t
        [
        (station1, date1),
        (station2, date2),
        ...
    ]
    """
    assert type(mc) is MetadataCollector
    assert type(sensor_id) is str
    # Get all activities with type=deployment and involving this sensor
    deployments = mc.get_documents("activities", mongo_filter={"type": "deployment"})
    sensor_deployments = []
    for dep in deployments:
        if "@sensors" in dep["appliedTo"].keys() and sensor_id in dep["appliedTo"]["@sensors"]:
            station = dep["where"]["@stations"]
            deployment_time = dep["time"]
            sensor_deployments.append((station, deployment_time))

    sensor_deployments = sorted(sensor_deployments, key=lambda x: x[1])
    return sensor_deployments


def get_sensor_latest_deployment(mc: MetadataCollector, sensor_id: str) -> list:
    """
    Returns the last station where the sensor was deployed
    """
    deployments = get_sensor_deployments(mc, sensor_id)
    return deployments[-1][0]


def get_station_coordinates(mc: MetadataCollector, station: any) -> (float, float, float):
    """
    Looks for the latest coordinates of a station based on its deployment history. Station may be station_id (str) or
    the station document (dict)
    """
    if type(station) is str:
        station = mc.get_document("station", station)
    elif type(station) is dict:
        pass
    else:
        raise ValueError(f"Wrong type in station, expected str or dict, got {type(station)}")
    deployments = get_station_deployments(mc, station)

    for deployment, time in reversed(deployments):
        # Get the latest deployment
        latitude = deployment["where"]["position"]["latitude"]
        longitude = deployment["where"]["position"]["longitude"]
        depth = deployment["where"]["position"]["depth"]
        break

    return latitude, longitude, depth


def get_station_history(mc: MetadataCollector, name: str) -> list:
    """
    Looks for all activities with the
    """
    station_filter = {"appliedTo.@stations": name}
    activities = mc.get_documents("activities", mongo_filter=station_filter)
    history = []
    for a in activities:
        h = load_fields_from_dict(a, ["time", "type", "description", "where/position"],
                                  rename={"where/position": "position"})
        history.append(h)

    # Sort based on history
    history = sorted(history, key=lambda x: x['time'])
    return history

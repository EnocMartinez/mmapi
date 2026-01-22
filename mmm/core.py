#!/usr/bin/env python3
"""
This file contains high-level functions that connects metadata DB with other services such as CKAN, ERDDAP, etc.

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 27/10/23
"""
import logging
import pandas as pd
from mmm import MetadataCollector, CkanClient, SensorThingsApiDB, DataCollector, init_metadata_collector
import rich
from mmm.common import load_fields_from_dict, YEL, RST
from mmm.data_manipulation import open_csv, drop_duplicated_indexes
from mmm.data_sources.api import Sensor, Thing, ObservedProperty, FeatureOfInterest, Location, Datastream, \
    HistoricalLocation, set_sta_basic_auth, init_sta_cache
from mmm.metadata_collector import get_station_coordinates, get_station_history, get_sensor_deployments
from mmm.processes import average_process, inference_process
from mmm.schemas import mmapi_data_types
import numpy as np


def get_properties(doc: dict, properties: list) -> dict:
    """
    Create a new dict with some of the properties from doc
    :param doc: original doc
    :param properties: params to copy (keys)
    :return: dict with copies
    """
    assert (type(doc) is dict)
    assert (type(properties) is list)
    data = {}
    for p in properties:
        data[p] = doc[p]

    return data


def propagate_metadata_to_ckan(mc: MetadataCollector, ckan: CkanClient, log: logging.Logger,  collections: list = [], datasets: list = []):
    """
    Propagates metadata from metadata database to CKAN

    :param mc: MetadataCollector
    :param ckan: CkanClient object
    :param collections: list of collections to propagaate
    :return:

    Projects are registered as groups
    Institutions are registered as institutions
    Datasets are registered as packages
    :return:
    """
    assert (type(mc) is MetadataCollector)
    assert (type(ckan) is CkanClient)
    assert (type(collections) is list)

    if len(collections) == 0:
        collections = mc.collection_names

    log.info("Propagating data from Metadata DB to CKAN")
    log.info(f"Using the following collections: {collections}")

    # Institutions
    log.info("Propagating organizations to CKAN")
    if "organizations" in collections:

        for doc in mc.get_documents("organizations"):
            name = doc["#id"]
            image_url = ""

            if "public" in doc.keys() and doc["public"]:
                organization_id = doc["#id"].lower()

                title = doc["fullName"]
                extras = load_fields_from_dict(doc, ["ROR", "EDMO"])
                if "logoUrl" in doc.keys():
                    image_url = doc["logoUrl"]

                # Try to create all organizations, CkanClient will automatically detect if the organization needs to be
                # created, updated or ignored (if it already exists)
                ckan.organization_create(organization_id, name, title, extras=extras, image_url=image_url)
            else:
                rich.print(f"[yellow]ignoring private organization {name}...")

    # CKAN Projects
    log.info("Propagating groups to CKAN")
    if "projects" in collections:
        ckan_groups = ckan.get_group_list()

        for doc in mc.get_documents("projects"):
            if doc["type"] == "contract":
                log.debug(f"Ingoring contract project '{doc['#id']}'")
                continue

            project_id = doc["#id"].lower()
            acronym = doc["acronym"]
            name = acronym.lower()
            title = doc["title"]
            extras = {
                "grant_id": doc["funding"]["grantId"],
                "funding_call": doc["funding"]["call"],
            }
            if "dateStart" in doc.keys() and doc["dateStart"]:
                extras["start_date"] = doc["dateStart"]
            if "dateEnd" in doc.keys() and doc["dateEnd"]:
                extras["end_date"] = doc["dateEnd"]

            logo = ""
            if "logoUrl" in doc.keys():
                logo = doc["logoUrl"]
            ckan.group_create(project_id, name, acronym, description=title, extras=extras, image_url=logo)

    log.info("Propagating datasets to CKAN")
    if "datasets" in collections:
        for doc in mc.get_documents("datasets"):
            name = doc["#id"]
            if datasets and name not in datasets:
                rich.print(f"[grey42]skipping dataset {name}")
                continue
            dataset_id = name.lower()
            package_name = dataset_id
            title = doc["title"]
            description = doc["summary"]
            sensors = doc["@sensors"]
            # Avoid navigation in sensors
            sensors = [s for s in sensors if not s.endswith("-navigation")]
            station = mc.get_station(doc["@stations"])

            # If we have a timeRange constraint, get the timestamp of the deployment to query the station position
            try:
                start, end = doc["constraints"]["timeRange"].split("/")
                start = pd.Timestamp(start)
                end = pd.Timestamp(end)
                timestamp = start + (end - start) / 2 # Get the middle point of the time range
            except KeyError:
                timestamp = None

            latitude, longitude, depth = get_station_coordinates(mc, station, timestamp=timestamp)

            extras = {
                "station": station["#id"],
                "latitude": latitude,
                "longitude": longitude,
                "depth": depth,
                "sensors": ", ".join(sensors)
            }
            owner = ""
            # Assign projects to datasets using gruops
            authors = []
            roles = []
            orcids = []
            groups = []
            if "funding" in doc.keys():
                for project_id in doc["funding"]["@projects"]:
                    groups.append(project_id.lower())

            # process contacts
            for contact in doc["contacts"]:
                role = contact["role"]
                if "@people" in contact.keys():
                    people = mc.get_people(contact["@people"])
                    name = people["name"]
                    orcid = people["orcid"]
                    role = contact["role"]
                    if not isinstance(role, str):
                        role = ""

                    if not isinstance(orcid, str):
                        orcid = ""
                    roles.append(role)
                    orcids.append(orcid)
                    authors.append(name)

                elif "@organizations" in contact.keys():
                    name = mc.get_organization(contact["@organizations"])["fullName"]
                    if role == "RightsHolder":  # assign the owner organization
                        owner = contact["@organizations"].lower()

            if not owner:
                mc.warning(f"Owner not detected for dataset {dataset_id}, using platform owner")
                for contact in station["contacts"]:
                    role = contact["role"]
                    if "@organizations" in contact.keys():
                        if role == "owner":  # assign the owner organization
                            owner = contact["@organizations"].lower()

            groups = []  # assign to ckan groups
            if "funding" in doc.keys():
                for project_id in doc["funding"]["@projects"]:
                    groups.append({"id": project_id.lower()})

            org_name = mc.get_organization(owner)["fullName"]
            extras["authors"] = ", ".join(authors)
            extras["contribution"] = ", ".join(roles)
            extras["ORCID"] = ", ".join(orcids)
            extras["rights holder"] = org_name

            ckan.package_register(package_name, title, description, dataset_id, extras=extras, owner_org=owner.lower(),
                                  groups=groups)


def propagate_metadata_to_sensorthings(dc: DataCollector, collections: str, url, update=True, auth=()):
    """
    Propagates info at MetadataCollctor the SensorThings API
    """
    assert (type(dc) is DataCollector)
    assert (type(collections) is list)
    mc = dc.mc
    if auth:
        set_sta_basic_auth(auth[0], auth[1])

    # Stations as thing
    if "all" in collections or collections == []:
        collections = mc.collection_names
    mc.info(f"Propagating collections {collections}")
    mc.info(f"Using URL: {url}")

    sensor_ids = {}  # key: metadata #id, value: sensorthings ID
    things_ids = {}
    location_ids = {}
    obs_props_ids = {}
    fois = {}

    init_sta_cache(url)


    # Convert "programmes" into "FeaturesOfInterest"
    programmes = mc.get_documents("programmes")
    for programme in programmes:
        programme_id = programme["#id"]
        foi = FeatureOfInterest(
            programme_id,
            programme["description"],
            programme["geoJsonFeature"]
        )
        foi.register(url, update=update, verbose=True)
        fois[programme["#id"]] = foi.id

    sensors = mc.get_documents("sensors")
    if "sensors" in collections:
        for doc in sensors:
            sensor_id = doc["#id"]
            name = doc["#id"]
            description = doc["description"]
            keys = ["longName", "serialNumber", "instrumentType", "manufacturer", "model"]
            properties = get_properties(doc, keys)
            if "properties" in doc.keys():
                for key, value in doc["properties"].items():
                    properties[key] = doc["properties"][key]
            s = Sensor(name, description, metadata="", properties=properties)
            s.register(url, update=update, verbose=True)
            sensor_ids[sensor_id] = s.id

    if "variables" in collections:
        variables = mc.get_documents("variables")
        for doc in variables:
            name = doc["#id"]
            description = doc["description"]
            definition = doc["definition"]
            prop = {
                "standard_name": doc["standard_name"]
            }
            o = ObservedProperty(name, description, definition, properties=prop)
            o.register(url, update=update, verbose=True)
            obs_props_ids[name] = o.id

    if "stations" in collections:
        stations = mc.get_documents("stations")
        for doc in stations:
            name = doc["#id"]
            history = get_station_history(mc, name)
            deployments = [h for h in history if h["type"] == "deployment"]
            prop = load_fields_from_dict(doc, ["platformType", "manufacturer", "contacts", "emsoFacility"])

            # Register Thing without location
            description = doc["longName"]
            t = Thing(name, description, properties=prop, locations=[])
            t.register(url, update=update, verbose=True)
            things_ids[name] = t.id

            # Now process any HistoricalLocations to add all the
            for dep in deployments:
                lat = dep["position"]["latitude"]
                lon = dep["position"]["longitude"]
                depth = dep["position"]["depth"]

                loc_name = f"Location of {name} at lat={lat}, lon={lon}, depth={depth} meters"
                loc_description = dep["description"]
                location = Location(loc_name, loc_description, lat, lon, depth, things=[])
                location.register(url, update=update, verbose=True)

                histloc = HistoricalLocation(dep["time"], location, t)
                histloc.register(url, verbose=True, update=update)

    for sensor in sensors:
        rich.print(f"[green]Creating Datastreams for sensor {sensor['#id']}")
        sensor_name = sensor["#id"]
        sensor_deployments = get_sensor_deployments(mc, sensor["#id"])
        if not sensor_deployments:
            rich.print(f"[yellow]WARNING: no deployments for sensor '{sensor_name}'")
            continue
        if len(sensor_deployments) < 1:
            raise ValueError(f"Sensor {sensor['#id']} does not have a deployment!")
        stations_processed = []
        for deployment in sensor_deployments:
            station = deployment["station"]
            deployment_time = deployment["start"]
            if station in stations_processed:
                rich.print(f"[yellow]Skipping station {station}")
                continue  # already processed for this sensor
            else:
                stations_processed.append(station)
            rich.print(f"[orange1]Generating Datastreams for sensor={sensor_name} in station={station}")
            # Create full_data datastreams!
            for var in sensor["variables"]:
                varname = var["@variables"]
                units = var["@units"]
                sensor_id = sensor_ids[sensor_name]
                thing_id = things_ids[station]
                obs_prop_id = obs_props_ids[varname]
                station_doc = mc.get_document("stations", station)

                data_type = var["dataType"]

                if data_type == "timeseries":  # creating timeseries data
                    ds_name = f"{station}:{sensor_name}:{varname}:{data_type}:full"
                    units_doc = mc.get_document("units", units)
                    ds_units = load_fields_from_dict(units_doc, ["name", "symbol", "definition"])
                    properties = {
                        "dataType": "timeseries",
                        "fullData": True,
                        "defaultFeatureOfInterest": fois[station_doc["defaults"]["@programmes"]]
                    }
                    if "@qualityControl" in var.keys():
                        qc_doc = mc.get_document("qualityControl", var["@qualityControl"])
                        properties["qualityControl"] = {
                            "description": "Quality Control configuration following the QARTOD guidelines" \
                                           " (https://ioos.noaa.gov/project/qartod) and using the ioos_qc python package " \
                                           "(https://pypi.org/project/ioos-qc/)",
                            "qartod": qc_doc["qartod"]
                        }

                    ds = Datastream(ds_name, ds_name, ds_units, thing_id, obs_prop_id, sensor_id, properties=properties)
                    ds.register(url, update=update, verbose=True)

                elif data_type == "profiles":  # creating profile data
                    ds_name = f"{station}:{sensor_name}:{varname}:{data_type}:full"
                    units_doc = mc.get_document("units", units)
                    ds_units = load_fields_from_dict(units_doc, ["name", "symbol", "definition"])
                    properties = {
                        "dataType": "profiles",
                        "fullData": True,
                        "defaultFeatureOfInterest": fois[station_doc["defaults"]["@programmes"]]
                    }
                    if "@qualityControl" in var.keys():
                        qc_doc = mc.get_document("qualityControl", var["@qualityControl"])
                        properties["qualityControl"] = {
                            "description": "Quality Control configuration following the QARTOD guidelines "
                                           "(https://ioos.noaa.gov/project/qartod) using the ioos_qc pyython package "
                                           "l(https://pypi.org/project/ioos-qc/)",
                            "qartod": qc_doc["qartod"]
                        }

                    ds = Datastream(ds_name, ds_name, ds_units, thing_id, obs_prop_id, sensor_id, properties=properties)
                    rich.print(f"[cyan]Registering Datastream {ds_name}")
                    ds.register(url, update=update, verbose=True)

                elif var["dataType"] == "files":
                    ds_name = f"{station}:{sensor_name}:{varname}:{data_type}"
                    units_doc = mc.get_document("units", units)
                    ds_units = load_fields_from_dict(units_doc, ["name", "symbol", "definition"])
                    properties = {
                        "dataType": "files",
                        "defaultFeatureOfInterest": fois[station_doc["defaults"]["@programmes"]]
                    }
                    if "@qualityControl" in var.keys():
                        qc_doc = mc.get_document("qualityControl", var["@qualityControl"])
                        properties["qualityControl"] = qc_doc["qartod"]


                    ds = Datastream(ds_name, ds_name, ds_units, thing_id, obs_prop_id, sensor_id, properties=properties,
                                    observation_type="OM_Observation")
                    ds.register(url, update=update, verbose=True)
                elif var["dataType"] == "detections":
                    # The process doing the detection should register this variable
                    pass
                elif var["dataType"] == "json":
                    # The process doing the inference should register this variable
                    pass
                else:
                    raise ValueError(f"dataType={var['dataType']} not implemented!")

            # Creating average data
            for sensor_process in sensor["processes"]:
                process = mc.get_document("processes", sensor_process["@processes"])
                params = sensor_process["parameters"]
                sensor_id = sensor_ids[sensor_name]
                thing_id = things_ids[station]

                if process["type"] == "average":
                    average_process(sensor, process, params, mc, obs_props_ids, sensor_id, thing_id, url, fois,
                                    update=update)

                elif process["type"] == "json":
                    inference_process(sensor, process, mc, obs_props_ids, sensor_id, thing_id,
                                      fois[station_doc["defaults"]["@programmes"]], url, update=True)
                else:
                    rich.print(f"[red]ERROR: process type not implemented '{process['type']}'")
                    exit(-1)


def bulk_load_data(filename: str, secrets: dict, sensor_name: str, data_type, foi_name: str, average="",
                   usecs=False, no_qc=False, tmp_folder="/tmp/sta_db_copy/data", missing_data:str ="", station_name="") -> bool:
    """
    This function performs a bulk load of the data contained in the input file

    foi_id: default FeatureOfInterest
    """
    rich.print("[purple]==== Bulk load Data ====")
    rich.print(f"    filename={filename}")
    rich.print(f"    sensor={sensor_name}")
    rich.print(f"    station={station_name}")
    rich.print(f"    dataType={data_type}")
    rich.print(f"    average={average}")
    assert data_type in mmapi_data_types, f"data_type={data_type} not valid!"

    psql_conf = secrets["sensorthings"]

    if filename.endswith(".csv"):
        df = open_csv(filename)
    else:
        rich.print(f"[red]extension {filename.split('.')[-1]} not recognized")
        raise ValueError("Invalid extension")

    if df.empty:
        raise ValueError("Empty dataframe!")

    df = df.set_index("timestamp")
    df = df.sort_index()

    if no_qc:
        for var in df.columns:
            if var.endswith("_QC"):
                continue
            elif var + "_QC" in df.columns:
                # we already have a qc column
                continue
            else:
                rich.print(f"[yellow]Forcing QC=2 for {var}")
                df[var + "_QC"] = 2

    if data_type not in ["profiles", "detections"]:
        df = drop_duplicated_indexes(df, keep="first")

    elif data_type == "detections":
        df = df.reset_index()
        df = df.set_index(["timestamp", "datastream_id"])
        dup_idx = df[df.index.duplicated(keep="first")]
        df = df.drop(dup_idx.index)
        df = df.reset_index()
        df = df.set_index("timestamp")

    df = df.sort_index(ascending=True)
    # Force qc in the upper case -> TEMP_qc -> TEMP_QC
    for col in df.columns:
        if col.endswith("_qc"):
            df = df.rename(columns={col: col.replace("_qc", "_QC")})

    db = SensorThingsApiDB(psql_conf["host"], psql_conf["port"], psql_conf["database"], psql_conf["user"],
                                 psql_conf["password"], logging.getLogger(), timescaledb=True)
    mc = init_metadata_collector(secrets, log=db.logger)
    tstart = df.index.min()
    tend = df.index.max()
    if tstart.tz is None:
        tstart = tstart.tz_localize("utc")
    if tend.tz is None:
        tend = tend.tz_localize("utc")
    rich.print("Timestamps ", tstart, tend)

    if not station_name:
        deployments = mc.get_sensor_deployments(sensor_name, interval=(tstart, tend))
        stations = [d["station"] for d in deployments]
        if len(np.unique(stations)) > 1:
            raise ValueError("Multiple stations in the deployments for the selected period!")
        station_name = deployments[0]["station"]

    elif station_name not in mc.get_identifiers("stations"):
        raise ValueError(f"Station name '{station_name}' not vlaid!")


    if missing_data:
        df = db.get_missing_data(df, sensor_name, bool(average), data_type, missing_data)
        if df.empty:
            raise ValueError("No missing data found!")

    foi_id = db.value_from_query(f'select "ID" from "FEATURES" where "NAME" = \'{foi_name}\';')

    if data_type == "timeseries":
        if not average:  # timeseries with full data
            datastreams_conf = db.get_datastream_config(sensor=sensor_name, station=station_name,  data_type=data_type, full_data=True)
            datastreams = {
                row["variable_name"]: row["datastream_id"] for _, row in datastreams_conf.iterrows()
            }
            df = drop_duplicated_indexes(df)
            db.inject_to_timeseries(df, datastreams, tmp_folder=tmp_folder, usecs=usecs)

        else:  # averaged timeseries
            datastreams_conf = db.get_datastream_config(sensor=sensor_name, data_type=data_type, average_period=average)
            datastreams = {
                row["variable_name"]: row["datastream_id"] for _, row in datastreams_conf.iterrows()
            }
            db.inject_to_observations(df, datastreams, foi_id, average, tmp_folder=tmp_folder)

    elif data_type == "profiles":
        if not average:  # profiles with full data
            datastreams_conf = db.get_datastream_config(sensor=sensor_name, data_type=data_type, full_data=True)
            datastreams = {
                row["variable_name"]: row["datastream_id"] for _, row in datastreams_conf.iterrows()
            }
            db.inject_to_profiles(df, datastreams, tmp_folder=tmp_folder)
        else:  # averaged profiles
            datastreams_conf = db.get_datastream_config(sensor=sensor_name, data_type=data_type, average_period=average)
            datastreams = {
                row["variable_name"]: row["datastream_id"] for _, row in datastreams_conf.iterrows()
            }
            db.inject_to_observations(df, datastreams, foi_id, average, profile=True)

    elif data_type == "detections":
        db.inject_to_detections(df, tmp_folder=tmp_folder, usecs=usecs)

    elif data_type == "files":
        db.inject_to_files(df, tmp_folder=tmp_folder, usecs=usecs)

    elif data_type == "json":
        db.inject_to_json(df, usecs=usecs)

    else:
        raise ValueError("This should never happen!")


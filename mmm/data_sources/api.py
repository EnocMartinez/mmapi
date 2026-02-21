#!/usr/bin/env python3
"""

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 23/3/21
"""
import logging

import pandas as pd
import requests
import json
import logging as log
import rich

_all_ = ["Sensor", "Thing"]

_api_cache = {}

sta_auth = ()

verify_ssl = True

def set_sta_basic_auth(user, password):
    global sta_auth
    sta_auth = (user, password)


def set_verify_ssl(verify: bool):
    """
    Enables or disables the verification of SSL in requests
    :param verify: True / False
    :return:
    """
    global verify_ssl
    verify_ssl = verify
    

def strip_nans(mydict: dict) -> dict:
    """
    Loops through all elements in a dict and replaces nan for string "NOT_AVAILBAL"
    :param mydict: input dict
    :return: dict
    """

    for key, value in mydict.items():
        if type(value) == str:
            continue
        elif np.isnan(value):
            properties[key] = "NOT_AVAILABLE"
    return mydict

def get_name_list(element: str, url: str) -> list:
    """
    Returns a list of the names registered in SensorThings
    :param element: type like 'Datastreams' or 'Things'
    :param url: STA base url
    :return: list with names
    """
    get_url = url + f"/{element}?$top=10000000&$select=name"
    r = sensorthings_get(get_url)
    names = [entry["name"] for entry in r["value"]]
    return names

def get_name_and_ids(element, url) -> (list, dict):
    """
    Similar to get_name_list but returns a tuple with (list, dict) where list is the list of names and the dict
    follows the pattern {name(str): id(int)}
    :param element:
    :param url:
    :return:
    """
    get_url = url + f"/{element}?$top=10000000&$select=name,id"
    r = sensorthings_get(get_url)
    names = [entry["name"] for entry in r["value"]]
    ids = {entry["name"]:entry["@iot.id"] for entry in r["value"]}
    return names, ids



def strip_sta_elements(data: dict, delete=[]):
    """
    Takes a dictionary and remove all SensorThings API elements (those containing ...@iot...) and all linked elements
    (Thing, Sensor, ObservedProperty)
    :param data: input data
    :param delete: list of elements to deleted
    :return: stripped dictionary
    """
    data = data.copy()
    for key in list(data.keys()):
        if "@iot" in key:
            del data[key]
        elif key in delete:
            del data[key]
    return data


def clean_dict(d):
    """
    Recursively remove all values that are empty strings, empty dictionaries,
    or empty lists from the given dictionary, including nested fields.
    """
    if not isinstance(d, dict):
        return d

    cleaned_dict = {}
    for key, value in d.items():
        if isinstance(value, dict):
            nested_dict = clean_dict(value)
            if nested_dict:  # Only add non-empty dictionaries
                cleaned_dict[key] = nested_dict
        elif isinstance(value, list):
            nested_list = [clean_dict(item) if isinstance(item, dict) else item for item in value]
            nested_list = [item for item in nested_list if item not in ("", [], {})]
            if nested_list:  # Only add non-empty lists
                cleaned_dict[key] = nested_list
        elif value not in ("", [], {}):
            cleaned_dict[key] = value

    return cleaned_dict


def compare_sta_elements(element1, element2):
    """
    Compares two SensorThings elements
    :param element1:
    :param element2:
    :return: True/False
    """
    __ignore_elements = ["Sensor", "ObservedProperty", "Thing", "resultTime", "phenomenonTime", "observedArea"]
    e1 = clean_dict(strip_sta_elements(element1, delete=__ignore_elements))
    e2 = clean_dict(strip_sta_elements(element2, delete=__ignore_elements))
    if e1 == e2:
        return True
    return False


def check_http_status(http_response, exception=True):
    """
    Checks if an HTTP transaction has been a success or not. If not, ValueError is raised
    :param http_response: HTTP response structure from requests lib
    :param exception: if true (default), an exception will be raised if the status is not OK
    :return: True if success
    :raises: ValueError if http failed
    """
    if 300 > http_response.status_code > 199:
        success = True
    else:
        success = False
        print("response code %d" % http_response.status_code)
        print("response headers:", http_response.headers)
        print("ERROR Report")
        for key, value in json.loads(http_response.text).items():
            print("    %s: %s" % (key, value))

    if exception and not success:
        raise ValueError("HTTP Error (code %d)" % http_response.status_code)

    return success


def print_http_response(resp):
    print("response code %d" % resp.status_code)
    print("response headers:", resp.headers)
    if len(resp.text) > 0:
        json_response = json.loads(resp.text)
        print(json.dumps(json_response, indent=3).replace("\\n", "\n").replace("\\t", "\t").replace("\\\"", "\""))


def sensorthings_post(url, data, endpoint="", header={"Content-type": "application/json"}, verbose=False):
    """
    Posts a JSON object to a a SensorThings API
    :param url: Service URL
    :param data: dict or string wit the data
    :param endpoint: API endpoint (optional)
    :param header: HTTP header
    :param verbose: prints more info
    """
    log.getLogger("requests").setLevel(log.WARNING)

    if type(data) == dict or type(data) == list:
        if verbose:
            print("    converting dict/list to string...")
        data = json.dumps(data, indent=2)
    elif type(data) == str:
        pass
    else:
        raise ValueError("data must be str, list or dict, got %s" % (str(type(data))))

    if endpoint:
        if url[-1] != "/":
            url = url + "/"
        url += endpoint

    if verbose:
        print("POSTing to URL", url)
    http_response = requests.post(url, data, headers=header, auth=sta_auth, verify=verify_ssl)

    if verbose:
        print_http_response(http_response)

    if http_response.status_code > 300:
        print_http_response(http_response)
        raise ValueError("HTTP ERROR")

    if len(http_response.text) < 1:
        return http_response.headers["location"]

    return http_response.text


def sensorthings_patch(url, data, endpoint="", header={"Content-type": "application/json"}, verbose=False)->bool:
    """
    Patches a JSON object to a a SensorThings API
    :param url: Service URL
    :param data: dict or string wit the data
    :param endpoint: API endpoint (optional)
    :param header: HTTP header
    :param verbose: prints more info
    """
    log.getLogger("requests").setLevel(log.WARNING)

    if type(data) == dict or type(data) == list:
        if verbose:
            print("    converting dict/list to string...")
        data = json.dumps(data, indent=2)
    elif type(data) == str:
        pass
    else:
        raise ValueError("data must be str, list or dict, got %s" % (str(type(data))))

    if endpoint:
        if url[-1] != "/":
            url = url + "/"
        url += endpoint

    if verbose:
        print("POSTing to URL", url)
    http_response = requests.patch(url, data, headers=header, auth=sta_auth, verify=verify_ssl)

    if verbose:
        print_http_response(http_response)

    if http_response.status_code > 300:
        print_http_response(http_response)
        raise ValueError("HTTP ERROR")

    return True


def sensorthings_get(baseurl, endpoint="", header={"Content-type": "application/json"}):
    """
    Gets an object to a a SensorThings API
    :param baseurl: Service base URL
    :param endpoint: API endpoint
    :param data: JSON object
    :param header: HTTP header
    :returns: identifier
    """
    if endpoint and baseurl[-1] != "/":
        baseurl = baseurl + "/"
    url = baseurl + endpoint
    http_response = requests.get(url, headers=header, auth=sta_auth, verify=verify_ssl)
    if http_response.status_code > 300:
        print_http_response(http_response)
        raise ValueError("HTTP ERROR")
    json_response = json.loads(http_response.text)
    return json_response


def sensorthings_delete(url, endpoint="", header={"Content-type": "application/json"}):
    """
    Gets an object to a a SensorThings API
    :param baseurl: Service base URL
    :param endpoint: API endpoint
    :param data: JSON object
    :param header: HTTP header
    :returns: identifier
    """
    if endpoint:
        if url[-1] != "/":
            url += "/"
        url = url + endpoint
    http_response = requests.delete(url, headers=header, auth=sta_auth)
    if http_response.status_code > 300:
        print_http_response(http_response)
        raise ValueError("HTTP ERROR")

def split_elements(list):
    for element in list:
        url = element["@iot.selfLink"]
        _api_cache[url] = element

def init_sta_cache(url):
    r = sensorthings_get(url, "Sensors")
    split_elements(r["value"])
    r = sensorthings_get(url, "FeaturesOfInterest")
    split_elements(r["value"])
    r = sensorthings_get(url, "Things")
    split_elements(r["value"])
    r = sensorthings_get(url, "Datastreams")
    split_elements(r["value"])

class AbstractSensorThings:
    def __init__(self, element_type, name="", description=""):
        """
        Generic class that all SensorThings entities inherit. It includes common features such as registration to the
        service, common data model and more.
        :param element_type: Type is the endpoint in the API
        :param name: entity's name
        :param name: entity's description
        """
        self.__valid_entity_types = ["Datastream", "Thing", "Sensor", "ObservedProperty", "FeatureOfInterest",
                                     "Observation", "Location", "HistoricalLocation", "DataArray"]

        if element_type not in self.__valid_entity_types:
            raise ValueError("Type %s is not a SensorThings API entity" % element_type)

        self.data = {}
        if name:
            self.data["name"] = name
            self.name = name
        if description:
            self.data["description"] = description
        self.type = element_type
        self.id = None  # @iot.id returned by SensorThings API, if None it has not been registered
        self.selfLink = None  # @iot.selfLink returned by SensorThings API, if None it has not been registered

    def post(self, baseurl, endpoint="", header={"Content-type": "application/json; charset=utf-8"}, verbose=False):
        """
        Posts this entity to its appropriate endpoint to register it
        :param baseurl: base url (rest will be generated)
        :return: iot.id value
        """
        if not endpoint:
            url = self.entity_url(baseurl)
        else:
            url = baseurl + "/" + endpoint
        if verbose:
            rich.print("[cyan]sending %s to %s" % (self.type, url))

        data = self.serialize()
        if verbose:
            rich.print(f"[blue]{data}")
        http_response = requests.post(url, data, headers=header, auth=sta_auth, verify=verify_ssl)
        if verbose:
            print(http_response.text)
        check_http_status(http_response)
        self.selfLink = http_response.headers["location"]
        self.id = self.selfLink.split("(")[1].replace(")", "")
        if verbose:
            print("    iot.id %s" % self.id)
            print("    self link %s" % self.selfLink)
        return self.id

    def patch(self):
        """
        Patches this entity to its appropriate endpoint to register it
        :param header:
        """
        headers = {"Content-Type": "application/json"}
        data = self.serialize()
        rich.print(self.selfLink)
        http_response = requests.patch(self.selfLink, data=data, headers=headers, auth=sta_auth, verify=verify_ssl)
        check_http_status(http_response)

    def register(self, baseurl, duplicate=True, verbose=False, update=False):
        """
        Registers the current element. If the duplicate=True, before posting the element this function will check if
        it has been registered previously. In case it was already registered and update=True, the element will be
        updated (PATCH operation).


        :param baseurl: SensorThings API base URL
        :param duplicate: if True (default) checks if there's already a sensor with the same name
        :param verbose: prints debug info
        :param update: if True, when if the element is already
        :return: new element (if registered/updated) or current element (if it already existed)
        """
        if self.type == "Observation":
            rich.print(f"[red]Can't register observation! use post instead...")

        log = logging.getLogger()

        if duplicate:  # check if the element has already been registered
            current = self.check_if_registered(baseurl)
            if current:  # element exists
                if update:
                    if compare_sta_elements(current, self.data):
                        log.debug(f'Register {self.type} "{self.name}"...already exists, update not required')
                        return current
                    else:
                        log.debug(f'Register {self.type} "{self.name}"...[yellow]updating element')
                        return self.patch()
                else:
                    log.debug(f'Register {self.type} "{self.name}"...element already exists, skipping')
                    return current


        log.debug(f'Registering {self.type} "{self.name}"...')
        try:
            resp = self.post(baseurl, verbose=False, )
        except Exception as e:
            log.error(f"Error when inserting {self.type} with name {self.name}")
            log.error(json.dumps(self.data, indent=2))
            raise e

        self.id = int(resp)
        return self.id

    def check_if_registered(self, baseurl, cache=True):
        """
        Checks if the current element has already been registered to a SensorThing API service
        :return: True or False
        """
        entity_url = self.entity_url(baseurl)
        if cache and entity_url not in _api_cache.keys():
            url = entity_url + "?$top=10000"
            http_response = requests.get(url, auth=sta_auth, verify=verify_ssl)
            check_http_status(http_response)
            registered_elements = json.loads(http_response.text)
            _api_cache[entity_url] = json.loads(http_response.text)
        else:
            registered_elements = _api_cache[entity_url]

        for e in registered_elements["value"]:
            if e["name"] == self.data["name"]:
                # if "description" in e.keys() and e["description"] != self.data["description"]:
                #     continue
                self.selfLink = e["@iot.selfLink"]
                self.id = e["@iot.id"]
                return strip_sta_elements(e)
        return False

    def entity_url(self, baseurl):
        """
        This function takes the base URL and appends the appropiate form of type. E.g.
              http://mysensorthings.com/api/ ==> http://mysensorthings.com/api/Sensors
        :param baseurl: base url of the service
        :param entity_type: type of the entity
        :return:
        """
        url = baseurl
        if baseurl[-1] != "/":
            url = baseurl + "/"

        if self.type == "ObservedProperty":
            url += "ObservedProperties"  # manually fix the plural
        elif self.type == "FeatureOfInterest":
            url += "FeaturesOfInterest"  # manually fix the plural
        else:
            url += self.type + "s"

        return url

    def print(self):
        """
        Prints its internal data structure
        :return: None
        """
        print("\nType: \"%s\"" % self.type)
        print(json.dumps(self.data, indent=4))

    def serialize(self, utf8=False):
        """
        Returns the contents in data as a python dict or as a string
        :param string: if True data is returned as string, otherwise as a python dict (default)
        :return: dict or string with all the data
        """
        if utf8:
            return json.dumps(self.data, indent=4, ensure_ascii=False).encode('utf8').decode()
        else:
            return json.dumps(self.data, indent=4)

    def to_file(self, filename):
        """
        Serializes entity data and stores it into file
        :param filename: file to store data
        """
        with open(filename, "w") as f:
            f.write(self.serialize(self.data))


class Sensor(AbstractSensorThings):
    def __init__(self, name, description, metadata, encoding_type="", properties={}):
        """
        Generates a Sensor
        :param name: Sensor's name
        :param description: Sensor's description
        :param encoding_type: Sensor's encodingType
        :param metadata: Sensor's metadata
        :param properties: Sensor's properties dictionary (optional)
        """
        AbstractSensorThings.__init__(self, "Sensor", name=name, description=description)

        self.data["encodingType"] = encoding_type
        self.data["metadata"] = metadata

        if properties:
            self.data["properties"] = properties


class ObservedProperty(AbstractSensorThings):
    def __init__(self, name, description, definition, properties={}):
        """
        Generates an ObservedProperty object
        :param name: Sensor's name
        :param description: Sensor's description
        :param definition: Sensor's definition
        """
        AbstractSensorThings.__init__(self, "ObservedProperty", name=name, description=description)
        if definition:
            self.data["definition"] = definition
        if properties:
            self.data["properties"] = properties


class Datastream(AbstractSensorThings):
    def __init__(self, name, description, uom, thing, observed_property, sensor, observation_type="", properties={}):
        """
        Generates a Datastream object
        :param thing: object
        :param name: Datastream's name (string)
        :param description: Datastream's description (string)
        :param uom: Datastream's units of measurement (dict with "symbol", "name" and "definition" keys)
        :param thing: Thing object
        :param observed_property: ObservedProperty object
        :param sensor: Sensor object
        :param observation_type: observation type definition (url string), by default OM_Measurement
        """
        AbstractSensorThings.__init__(self, "Datastream", name=name, description=description)

        __valid_observation_types = ["OM_TruthObservation", "OM_CategoryObservation", "OM_CountObservation",
                                     "OM_Observation", "OM_Measurement"]
        if not observation_type:
            observation_type = "OM_Measurement"
        assert observation_type in __valid_observation_types, f"observation {observation_type} type not valid"
        observation_type_url = "http://www.opengis.net/def/observationType/OGC-OM/2.0/" + observation_type

        # assign links
        self.sensor = sensor
        self.observed_property = observed_property
        self.thing = thing

        if "symbol" and "name" and "definition" not in uom.keys():
            raise ValueError("The following keys should be in uom dict: \"symbol\", \"name\" and \"definition\"")

        uom = {
            "name": uom["name"],
            "symbol": uom["symbol"],
            "definition": uom["definition"],

        }
        self.data["unitOfMeasurement"] = uom
        self.data["description"] = description
        if type(sensor) == Sensor:
            self.data["Sensor"] = {"@iot.id": sensor.id}
        elif type(sensor) == int:
            self.data["Sensor"] = {"@iot.id": sensor}

        if type(thing) == Thing:
            self.data["Thing"] = {"@iot.id": thing.id}
        elif type(sensor) == int:
            self.data["Thing"] = {"@iot.id": thing}

        if type(observed_property) == ObservedProperty:
            self.data["ObservedProperty"] = {"@iot.id": observed_property.id}
        elif type(observed_property) == int:
            self.data["ObservedProperty"] = {"@iot.id": observed_property}


        self.data["properties"] = {}

        self.data["observationType"] = observation_type_url

        if type(observed_property) == ObservedProperty and  "properties" in observed_property.data.keys():
            self.data["properties"] = observed_property.data["properties"]

        if properties:
            for key, value in properties.items():
                self.data["properties"][key] = value

    def generate_observation(self, value, timestamp, foi):
        """
        Generates an Observation for this DataStream
        :param value: observation value
        :param timestamp: time
        :param foi: FeatureOfInterest
        :return:
        """
        url = self.selfLink + "/Observations"
        observation_data = {
            "phenomenonTime": timestamp,
            "result": value,
            "FeatureOfInterest": {"@iot.id": foi.id}
        }
        return observation_data, url


class FeatureOfInterest(AbstractSensorThings):
    def __init__(self, name, description, feature, properties={}, encoding_type="application/vnd.geo+json"):
        """
        Generate a Feature Of Interest (FOI) object.
        :param name:  FOI's name
        :param description: FOI's description
        :param feature: FOI's
        :param properties: optional dict to include properties in the FOI
        :param encoding_type:
        """
        AbstractSensorThings.__init__(self, "FeatureOfInterest", name=name, description=description)
        self.data["feature"] = feature
        self.data["encodingType"] = encoding_type
        self.data["properties"] = properties


class Observation(AbstractSensorThings):
    def __init__(self, phenomenon_time, result, datastream, foi, parameters={}, result_time=""):
        """
        Creates an Observation object
        :param phenomenon_time: The time instant or period of when the Observation happens.
        :param result_time: The time of the Observation 's result was generated
        :param result: result of the observation (value)
        :param datastream: Datastream object
        :param foi: FeatureOfInterest object
        :param parameters: python dict with parameters (optional)
        """
        AbstractSensorThings.__init__(self, "Observation")

        if type(datastream) == int:
            datastream_id = datastream
        else:
            datastream_id = datastream.id

        if type(foi) == int:
            foi_id = foi
        else:
            foi_id = foi.id

        if not result_time:
            result_time = phenomenon_time

        self.data = {
            "resultTime": result_time,
            "phenomenonTime": phenomenon_time,
            "result": result,
            "Datastream": {"@iot.id": datastream_id},
            "FeatureOfInterest": {"@iot.id": foi_id}
        }
        if parameters:
            self.data["resultQuality"] = parameters


class Location(AbstractSensorThings):
    def __init__(self, name: str, description: str, latitude: float, longitude: float, depth: float,  things=[],
                 encodingType="application/geo+json"):
        """
        Generates a location
        :param type: location type, usually Polygon or Point
        :param coordinates: list witth the coordinates
        :param name: Location's name
        :param description: Location's description
        :param encodingType: encoding type
        """
        AbstractSensorThings.__init__(self, "Location", name=name, description=description)
        self.data["location"] = {
            "type": "Point",
            "coordinates": [longitude, latitude]
        }
        self.data["properties"] = {
            "depth": depth,
            "depth_units": "meters"
        }
        self.data["encodingType"] = encodingType
        self.data["Things"] = []
        if things:
            for t in things:
                if type(t) is int:
                    self.data["Things"].append({"@iot.id": t})
                elif type(t) is Thing:
                    self.data["Things"].append({"@iot.id": t.id})

class Thing(AbstractSensorThings):
    def __init__(self, name, description, properties={}, locations=[]):
        """
        Generates a Thing
        :param name: Thing's name
        :param description: Thing's short description
        :properties: dict with thing's properties
        :locations: list of locations (dicts)
        """
        log.debug("Creating thing %s..." % name)
        AbstractSensorThings.__init__(self, "Thing", name=name, description=description)

        if properties:
            self.data["properties"] = properties

        log.debug("Thing %s created!" % self.data["name"])
        locs = []
        for loc in locations:
            if type(loc) is int:
                locs.append({"@iot.id": loc})
            elif type(loc) is Location and loc.id:
                locs.append({"@iot.id": loc.id})

        self.data["Locations"] = locs


class HistoricalLocation(AbstractSensorThings):
    def __init__(self, timestamp, location: Location, thing: Thing):
        """
        Generates a HistoricalLocation

        :param timestamp: time of the locoation
        :param location: Location object
        :param thing: thing object
        """
        # Valid HistoricalLocation:
        # {
        #     "time": "2019-03-01T19:18:08",
        #     "Locations": [{"@iot.id": 43}],
        #                   "Thing": {"@iot.id": 67}
        # }
        name = thing.name + " at " + location.name
        AbstractSensorThings.__init__(self, "HistoricalLocation", name=name)
        self.location = location
        self._time = timestamp
        self._thing_id = thing.id
        self._location_id = location.id
        self.data = {
            "time": timestamp,
            "Locations": [{"@iot.id": location.id}],
            "Thing": {"@iot.id": thing.id},
        }

    def check_if_registered(self, baseurl, cache=True):
        """
        Overriding method, this one has no name
        :return: returns the dict with the data if true or False if not registered
        """
        entity_url = self.entity_url(baseurl)
        url = entity_url + (f"?$filter=Thing/id eq {self._thing_id} and Locations/id eq {self._location_id} and time"
                            f" eq {self._time}")
        r = sensorthings_get(url)
        if len(r["value"]) == 1:
            self.id = r["value"][0]["@iot.id"]
            return self.data
        elif len(r["value"]) > 1:
            raise ValueError(f"[red]Too many HistoricalLocations found!!")
        else:
            return False


class DataArray(AbstractSensorThings):
    def __init__(self):
        """
        Generates a DataArray for a datastream
        :param datastream: Datastream object
        """
        self.arrays = {}
        self.count = 0

    def add_observation(self, timestamp: str, result, foi, datastream_id, parameters):
        """
        Adds an observation measure to the data array
        :param timestamp: timestamp
        :param result: result
        :return:
        """
        self.count += 1
        datastream_id = int(datastream_id)
        if datastream_id not in self.arrays.keys():
            self.arrays[datastream_id] = {
                "Datastream": {"@iot.id": datastream_id},
                "components": ["phenomenonTime", "resultTime", "result",  "FeatureOfInterest/id", "parameters"],
                "dataArray@iot.count": 0,
                "dataArray": []
            }

        self.arrays[datastream_id]["dataArray"].append(
            [timestamp, timestamp, result, foi, parameters]
        )
        self.arrays[datastream_id]["dataArray@iot.count"] = len(self.arrays[datastream_id]["dataArray"])

    def inject(self, url):
        """
        Insert accumulated data into the Service
        :param url:
        :return:
        """
        self.data = [a for a in self.arrays.values()]
        header = {"Content-type": "application/json; charset=utf-8"}
        url = url + "/" + "CreateObservations"
        data = self.serialize()
        http_response = requests.post(url, data, headers=header, auth=sta_auth, verify=verify_ssl)
        check_http_status(http_response)
        resp = json.loads(http_response.text)
        responses = []
        count = 0
        for r in resp:
            if r.startswith("http"):
                responses.append(int(r.split("(")[1].split(")")[0]))
            else:
                responses.append(r)
                rich.print(f"[red]ERROR in observation {count} of {len(self.count)}: {r}")
            count += 1
        return responses
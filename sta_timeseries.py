#!/usr/bin/env python3
"""
API that returns raw data from a SensorThings Instance with a separate raw_data table. If a query is accessing regular
sensorthings data (all data that is not in raw data table), this API simply acts as a proxy. Otherwise, if a query is
trying to access data in raw_data table, the API accesses this data throuh a PostgresQL connector, formats in in JSON
and sends it back following the SensorThings API standard.

Some of the things to be taken into account:
        -> there are no observation ID in raw data, but a primary key composed by timestamp and datastream_id
    -> To generate a unique identifier for each data point, the following formula is used:
            observation_id = datastream_id * 1e10 + epoch_time (timeseries)
            observation_id = datastream_id * 2e10 + epoch_time (profiles)
            observation_id = datastream_id * 3e10 + epoch_time (detections)


author: Enoc Martínez
institufrom argparse import ArgumentParser
tion: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 6/9/22
"""
from argparse import ArgumentParser
import logging
import json
import requests
import yaml
from flask import Flask, request, Response
from flask_basicauth import BasicAuth
from flask_cors import CORS
from mmm.common import setup_log, assert_dict, GRN, BLU, MAG, CYN, WHT, YEL, RED, NRM, RST, LoggerSuperclass
import time
import os
import rich
from mmm import SensorThingsApiDB, init_metadata_collector_env
import datetime
import psycopg2
from functools import wraps  # Import wraps here
import dotenv

app = Flask("SensorThings TimeSeries")
CORS(app)
basic_auth = BasicAuth(app)

service_root = "/sta-timeseries/v1.1"


def determine_auth_requirement():
    """
    :return: True/False
    """
    if app.sta_auth:
        return True
    return False


def conditional_basicauth():
    """
    Defines a conditional way to dynamically request BasicAuth
    :return:
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Assume there's a function that determines if auth should be enabled
            enable_auth = determine_auth_requirement()
            if enable_auth:
                return basic_auth.required(f)(*args, **kwargs)
            else:
                return f(*args, **kwargs)
        return decorated_function
    return decorator


def get_datastream_id(datastream: dict):
    """
    Tries to get the Datastrea_ID of a data structure blindly, trying to find a @iot.id or its name
    """
    if "@iot.id" in datastream.keys():
        return datastream["@iot.id"]
    elif "name" in datastream.keys():
        return app.db.datastream_name_id[datastream["name"]]
    else:
        raise ValueError("Can't get DatastreamID for this query, no iot.id nor name!")


def get_foi_id(foi: dict):
    if "@iot.id" in foi.keys():
        return foi["@iot.id"]
    else:
        raise ValueError("Can't get DatastreamID for this query, no iot.id nor name!")


def parse_options_within_expand(expand_string):
    """
    Parses options within a expand -> Datastreams($top=1;$select=id) ->  {"$top": "1", "$select": "id"}
    """
    # Selects only what's within (...)
    expand_string = expand_string[expand_string.find("(") + 1:]
    if expand_string[-1] == ")":
        expand_string = expand_string[:-1]

    opts = {}
    nested = False
    # check if we have a nested expand
    if "$expand" in expand_string:
        nested = True
        start = expand_string.find("$expand=") + len("$expand=")
        rest = expand_string[start:]
        if "(" not in rest:
            nested_expand = rest
        else:  # process options within nested expand
            level = 0
            end = -1
            for i in range(len(rest)):
                if rest[i] == "(":
                    level += 1
                elif rest[i] == ")":
                    if level > 1:
                        level -= 1
                    else:
                        end = i
                        break
            if end < 0:
                raise ValueError("Could not parse options!")
            nested_expand = rest[:i + 1]

    if nested:
        expand_string = expand_string.replace("$expand=" + nested_expand, "").replace(";;", ";")  # delete nested expand
        opts["$expand"] = nested_expand

    if "$" in expand_string:
        for pairs in expand_string.split(";"):
            if len(pairs) > 0:
                key, value = pairs.split("=")
                opts[key] = value

    return opts


def get_expand_value(expand_string):
    next_parenthesis = expand_string.find("(")
    if next_parenthesis < 0:
        next_parenthesis = 99999  # huge number to avoid being picked
    end = min(next_parenthesis, len(expand_string))
    return expand_string[:end]


def process_url_with_expand(url, opts):
    parent = url.split("?")[0].split("/")[-1]

    # delimit where the expanded key ends
    expand_value = get_expand_value(opts["expand"])
    expand_options = parse_options_within_expand(opts["expand"])
    return parent, expand_value, expand_options


def expand_element(resp, parent_element, expanding_key, opts):
    if expanding_key != "Observations":  # we should only worry about expanding observations
        return resp

    if parent_element.startswith("Datastreams"):
        datastream = resp
        datastream_id = get_datastream_id(datastream)
        foi_id =  app.db.datastream_fois[datastream_id]

    elif parent_element.startswith("FeaturesOfInterest"):
        foi_id = get_foi_id(resp)
        datastream_id =  app.db.datastream_fois(foi_id)
    else:
        raise ValueError(f"Unexpected parent element '{parent_element}'")

    data_type, average =  app.db.get_data_type(datastream_id)
    if not average:  # If not average, data may need to be expanded
        opts = process_sensorthings_options(opts)  # from "raw" options to processed ones
        if data_type == "timeseries":
            list_data =  app.db.timescale.get_timeseries_data(datastream_id, top=opts["top"], skip=opts["skip"], debug=False, format="list",
                                        filters=opts["filter"], orderby=opts["orderBy"])
        elif data_type == "profiles":
            list_data =  app.db.timescale.get_profiles_data(datastream_id, top=opts["top"], skip=opts["skip"], debug=False, format="list",
                                        filters=opts["filter"], orderby=opts["orderBy"])

        observation_list = format_observation_list(list_data, foi_id, datastream_id, opts, data_type)
        datastream["Observations@iot.nextLink"] = generate_next_link(len(list_data), opts, datastream_id)
        datastream["Observations@iot.navigatioinLink"] = app.sta_base_url + f"/Datastreams({datastream_id})/Observations"
        datastream["Observations"] = observation_list
    return resp


def expand_query(resp, parent_element, expanding_key, opts):
    # list response, expand one by one
    if "value" in resp.keys() and type(resp["value"]) == list:
        for i in range(len(resp["value"])):
            resp["value"][i] = expand_element(resp["value"][i], parent_element, expanding_key, opts)
    elif type(resp) == list:
        for i in len(resp):
            resp[i] = expand_element(resp[i], parent_element, expanding_key, opts)

    else:  # just regular response, expand it all at once
        resp = expand_element(resp, parent_element, expanding_key, opts)

    if "$expand" in opts.keys():
        nested_options = parse_options_within_expand(opts["$expand"])
        nested_expanding_key = get_expand_value(opts["$expand"])

        if type(resp[expanding_key]) == list:
            # Expand every element within the list
            for i in range(len(resp[expanding_key])):
                element_to_expand = resp[expanding_key][i]
                nested_response = expand_query(element_to_expand, expanding_key, nested_expanding_key, nested_options)
                resp[expanding_key][i] = nested_response

        elif type(resp[expanding_key]) == dict:
            element_to_expand = resp[expanding_key]
            nested_response = expand_query(element_to_expand, expanding_key, nested_expanding_key, nested_options)
            resp[expanding_key] = nested_response

    return resp


def process_sensorthings_response(request, resp: json, mimetype="aplication/json", code=200):
    """
    process the resopnse and checks if further actions are needed (e.g. expand raw data).
    """
    if not resp:
        resp = {}
    opts = process_sensorthings_options(request.args.to_dict())
    if "expand" in opts.keys():
        parent, key, expand_opts = process_url_with_expand(request.full_path, opts)
        resp = expand_query(resp, parent, key, expand_opts)

    return generate_response(json.dumps(resp), status=200, mimetype="application/json")


def decode_expand_options(expand_string: str):
    """
    Converts from expand semicolon separated-tring options to a dict
    """
    if "(" not in expand_string:
        return {}
    expand_string = expand_string.split("(")[1]
    if expand_string[-1] == ")":
        expand_string = expand_string[:-1]
    if "$expand" in expand_string:
        raise ValueError("Unimplemented!")
    options = {}
    for option in expand_string.split(";"):
        key, value = option.split("=")
        option[key] = value
    return options


def get_sta_request(request):
    # https://my.dns.com/sta-timeseries/v1.1/something/else
    sta_url = app.sta_get_url + request.full_path.replace(service_root, "")
    app.log.debug(f"Generic query, fetching {sta_url}")
    resp = requests.get(sta_url, auth=app.sta_auth)
    code = resp.status_code
    text = resp.text.replace(app.sta_base_url, app.service_url)  # hide original URL
    return text, code


def post_sta_request(request):
    sta_url = f"{app.sta_base_url}{request.full_path}"
    app.log.debug(f"[cyan]Generic query, fetching {sta_url}")
    resp = requests.post(sta_url, request.data, headers=request.headers, auth=app.sta_auth)
    code = resp.status_code
    text = resp.text.replace(app.sta_base_url, app.service_url)  # hide original URL
    return text, code


def observations_from_raw_dataframe(df, datastream: dict):
    df["resultTime"] = df.index.strftime("%Y-%m-%dT%H:%M:%Sz")
    df = df[["resultTime", "value", "qc_flag"]]
    df.values.tolist()


def data_point_to_sensorthings(data_point: list, datastream_id: int, opts, data_type: str):
    """
    Convert a data point from the database to the SensorThings API format
    """
    base_url = app.sta_base_url
    foi_id = app.db.datastream_properties[datastream_id]["defaultFeatureOfInterest"]
    if data_type == "timeseries":
        timestamp, value, qc_flag = data_point
    elif data_type == "profiles":
        timestamp, depth, value, qc_flag = data_point
    elif data_type == "detections":
        timestamp, value = data_point
    else:
        raise ValueError("unimplemented")
    t = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    observation_id = int(1e10 * datastream_id + int(timestamp.strftime("%s")))
    observation = {
        "@iot.id": observation_id,
        "phenomenonTime": t,
        "result": value,
        "resultTime": t,
        "@iot.selfLink": f"{base_url}/Observations({observation_id})",
        "Datastream@iot.navigationLink": f"{base_url}/Datastreams({int(datastream_id)})",
        "FeatureOfInterest@iot.navigationLink": f"{base_url}/FeaturesOfInterest({int(foi_id)})"
    }
    if data_type == "profiles" or data_type == "timeseries":
        observation["resultQuality"] = {
            "qc_flag": qc_flag
        }
    if data_type == "profiles":   # add depth in profiles
        observation["parameters"] = {"depth": depth}

    if "select" in opts.keys():
        for key in observation.copy().keys():
            if key not in opts["select"]:
                del observation[key]
    return observation


def data_list_to_sensorthings(data_list, foi_id: int, datastream_id: int, opts: dict, data_type: str):
    """
    Generates a list Observations structure, such as
        {
            @iot.nextLink: ...
            value: [
                {observation 1...},
                {observation 2 ...}
                ...
            ]
         }
    """
    data = {}
    next_link = generate_next_link(len(data_list), opts, datastream_id, url=request.url)
    if next_link:
        data["@iot.nextLink"] = next_link

    data["value"] = format_observation_list(data_list, foi_id, datastream_id, opts, data_type)
    return data


def generate_next_link(n: int, opts: dict, datastream_id: int, url: str = ""):
    """
    Generate next link
    :param n: number of observations
    :param opts: options
    :param datastream_id: ID of the datasetream
    :param url: base url to modify, if not set a datastream(xx)/Observations url will be generated
    """
    if url:
        next_link = url
    else:
        next_link = app.sta_base_url + f"/Datastreams({datastream_id})/Observations"

    if "?" not in next_link:
        next_link += "?"  # add a ending ?

    if "$skip" in next_link:
        next_link = next_link.replace(f"$skip={opts['skip']}", f"$skip={opts['skip'] + opts['top']}")
    else:
        next_link = next_link.replace(f"?", f"?$skip={opts['top']}&")
    if n < opts["top"]:
        return ""

    return next_link


def format_observation_list(data_list, foi_id: int, datastream_id: int, opts: dict, data_type: str):
    """
    Formats a list of observations into a list
    """
    p = time.time()
    observations_list = []
    for data_point in data_list:
        o = data_point_to_sensorthings(data_point, datastream_id, opts, data_type)
        observations_list.append(o)
    app.log.debug(f"format {len(observations_list)} db data points took {1000*(time.time() - p):.03} msecs")
    return observations_list


def is_date(s):
    """
    checks if string is a date in the format YYYY-mm-ddTHH:MM:SS
    :param s: input string
    :return: True/False
    """
    if len(s) > 19 and s[4] == "-" and s[7] == "-" and s[10] == "T" and s[13] == ":" and s[16] == ":":
        return True

    elif len(s) == 10 and s[4] == "-" and s[7] == "-": # check pure date (YYYY-mm-dd)
        return True
    return False


def sta_option_to_postgresql(filter_string):
    """
    Takes a SensorThings API filter expression and converts it to a PostgresQL filter expression
    :param filter_string: sensorthings filter expression e.g. filter=phenomenonTime lt 2020-01-01T00:00:00z
    :return: string with postgres filter expression
    """
    # try to identify dates
    elements = filter_string.split(" ")
    sta_to_pg_conversions = {
        # map SensorThings' operators with PostgresQL's operators
        "eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=",
        # map SensorThings params with raw_data table params
        "phenomenonTime": "timestamp", "resultTime": "timestamp", "result": "value",
        # order
        "orderBy": "order by",
        # manually change resultQuality/qc_flag to qc_flag
        "resultQuality/qc_flag": "qc_flag",
        # manually change parameters/depth to depth
        "parameters/depth": "depth"
    }

    for i in range(len(elements)):
        if ")" and "(" in elements[i]:  # trapped within a function, like 'date(phenomenonTime)'
            string = elements[i].split("(")[1].split(")")[0]  # process only contents of function
            trapped = True
        else:
            trapped = False
            string = elements[i]  # process whole element

        if is_date(string):  # check if it is a date
            string = f"'{string}'"  # add ' around date)

        for old, new in sta_to_pg_conversions.items():  # total match
            if old == string:
                string = new

        if trapped:  # trapped within a function
            elements[i] = elements[i].split("(")[0] + f"({string})"  # recreate the function with new string
        else:
            elements[i] = string

    return " ".join(elements)


def process_sensorthings_options(params: dict):
    """
    Process the options of a request and store them into a dictionary.
    :param full_path: URL requested
    :param params: JSON dictionary with the query options
    :return: dict with all the options processed
    """
    __valid_options = ["$top", "$skip", "$count", "$select", "$filter", "$orderBy", "$expand"]
    sta_opts = {
        "count": True,
        "top": 100,
        "skip": 0,
        "filter": "",
        "orderBy": "order by timestamp asc"
    }
    for key, value in params.items():
        if key not in __valid_options:
            raise SyntaxError(f"Unknown option {key}, expecting one of {' '.join(__valid_options)}")
        if key == "$top":
            sta_opts["top"] = int(value)
        elif key == "$skip":
            sta_opts["skip"] = int(params["$skip"])
        elif key == "$count":
            if params["$count"].lower() == "true":
                sta_opts["count"] = True
            elif params["$count"].lower() == "false":
                sta_opts["count"] = False
            else:
                raise SyntaxError("True or False exepcted, got \"{params['$count']}\"")

        elif key == "$select":
            sta_opts["select"] = params["$select"].replace("resultQuality/qc_flag", "resultQuality").split(",")

        elif key == "$filter":
            sta_opts["filter"] = sta_option_to_postgresql(params["$filter"])

        elif key == "$orderBy":
            sta_opts["orderBy"] = "order by " + sta_option_to_postgresql(params["$orderBy"])

        elif key == "$expand":
            sta_opts["expand"] = params["$expand"]  # just propagate the expand value

    return sta_opts


def generate_response(text, status=200, mimetype="application/json", headers={}):
    """
    Finals touch before sending the result, mainly replacing FROST url with our url
    """
    if isinstance(text, dict):
        text = json.dumps(text)
    text = text.replace(app.sta_base_url, app.service_url)
    response = Response(text, status, mimetype=mimetype)
    for key, value in headers.items():
        response.headers[key] = value
    return response


@app.route(f'{service_root}/<path:path>', methods=['GET'])
@conditional_basicauth()
def generic_query(path):
    text, code = get_sta_request(request)
    if code > 300:
        if "favicon.ico" not in request.full_path:  # ignore favicon errors
            app.log.error(f"ERROR! in request '{request}' HTTP code '{code}' response '{text}'")
        return Response(text, code)
    else:
        resp = json.loads(text)
        return process_sensorthings_response(request, resp)


@app.route(f'{service_root}/', methods=['GET'])
@app.route(f'{service_root}', methods=['GET'])
@conditional_basicauth()
def generic():

    text, code = get_sta_request(request)
    opts = process_sensorthings_options(request.args.to_dict())
    try:
        json_response = json.loads(text)
    except json.decoder.JSONDecodeError:
        return Response(text, code)

    return process_sensorthings_response(request, json_response)


@app.route(f'/{service_root.split("/")[1]}', methods=['GET'])
@app.route(f'/{service_root.split("/")[1]}/', methods=['GET'])
@conditional_basicauth()
def no_version():
    d = {
        "success": False,
        "message": f"No API version found, try {request.url}/v1.1"
    }
    return generate_response(d)

@app.route(f'{service_root}/Observations(<int:observation_id>)', methods=['GET'])
@conditional_basicauth()
def get_observation(observation_id):
    """
    Observations
    """
    try:
        full_path = request.full_path
        opts = process_sensorthings_options(request.args.to_dict())
        app.log.info(f"Received Observations GET: {full_path}")
        if observation_id < 1e10:
            text, code = get_sta_request(request)
            return process_sensorthings_response(request, json.loads(text), sta_opts=opts)
        else:
            datastream_id = int(observation_id / 1e10)
            struct_time = time.localtime(observation_id - 1e10 * datastream_id)
            timestamp = time.strftime('%Y-%m-%dT%H:%M:%Sz', struct_time)
            filters = f"timestamp = '{timestamp}'"
            data = app.db.get_raw_data(datastream_id, filters=filters, debug=True, format="list")[0]
            observation = data_point_to_sensorthings(data, datastream_id, opts)
            return generate_response(json.dumps(observation), 200, mimetype='application/json')
    except SyntaxError as e:
        error_message = {
            "code": 400,
            "type": "error",
            "message": str(e)
        }
        return generate_response(json.dumps(error_message), 400, mimetype='application/json')


@app.route(f'{service_root}/Observations', methods=['GET'])
@conditional_basicauth()
def get_observations():
    """
    Get generic Observations. Probably filtered by datastream, so we need to check the filter
    """
    try:
        full_path = request.full_path
        opts = process_sensorthings_options(request.args.to_dict())
        # Observations targets ALL observations, which we have scattered across several tables, so let's check which
        # datastream we have in the filter
        if "filter" in opts.keys() and "Datastream/id = " in opts["filter"]:
            # Check if datastream is equal

            # We are not yet parsing complex operations, so raise an error and return unimplemented
            for operator in ["and", "or", "not"]:
                if operator in opts["filter"]:
                    app.log.error(f"Found {operator} logic operators not implemented!")
                    return generate_response(json.dumps({"status": "unimplemented"}), 500,
                                             mimetype='application/json')

            # easy-peasy, just one datastream
            datastream_id = int(opts["filter"].split(" = ")[1])
            app.log.debug(f"Get /Observations for datastream={datastream_id}")
            opts["filter"] = ""  # remove the datastream filter
            return datastreams_observations_get(datastream_id, opts=opts)

        app.log.error(f"Query without Received Observations GET: {full_path}")
        return generate_response(json.dumps({
            "status": "unimplemented"
        }), 500, mimetype='application/json')
    except SyntaxError as e:
        error_message = {
            "code": 400,
            "type": "error",
            "message": str(e)
        }
        return generate_response(json.dumps(error_message), 400, mimetype='application/json')


@app.route(f'{service_root}/Sensors(<int:sensor_id>)/Datastreams(<int:datastream_id>)/Observations', methods=['GET'])
@conditional_basicauth()
def sensors_datastreams_observations(sensor_id, datastream_id):
    return datastreams_observations_get(datastream_id)


@app.route(f'{service_root}/Datastreams(<int:datastream_id>)', methods=['GET'])
@conditional_basicauth()
def just_datastreams(datastream_id):
    rich.print(f"[green]Got a datastream request: {request.path}")
    text, code = get_sta_request(request)
    resp = json.loads(text)
    return process_sensorthings_response(request, resp)


@app.route(f'{service_root}/Datastreams(<int:datastream_id>)/Observations', methods=['GET'])
@conditional_basicauth()
def datastreams_observations_get(datastream_id, opts=None):
    try:
        if not opts:
            opts = process_sensorthings_options(request.args.to_dict())
        data_type, average = app.db.get_data_type(datastream_id)
        app.log.debug(f"{CYN}Datastreams({datastream_id})/Observations query! dataType={data_type}, average={average}")

        # Averaged data is stored in regular "OBSERVATIONS" table, so it can be managed directly by SensorThings API
        if average:
            init = time.time()
            text, code = get_sta_request(request)
            d = json.loads(text)
            if code < 300:
                app.log.debug(f"{CYN} SensorThings query (with {len(d['value'])} points) took {time.time()-init:.03} "
                              f"sec{RST}")
            return process_sensorthings_response(request, json.loads(text))

        foi_id = app.db.datastream_properties[datastream_id]["defaultFeatureOfInterest"]
        init = time.time()
        if data_type == "timeseries":
            list_data = app.db.timescale.get_timeseries_data(
                datastream_id, top=opts["top"], skip=opts["skip"], debug=False, format="list", filters=opts["filter"],
                orderby=opts["orderBy"])
        elif data_type == "profiles":
            list_data = app.db.timescale.get_profiles_data(
                datastream_id, top=opts["top"], skip=opts["skip"], debug=False, format="list", filters=opts["filter"],
                orderby=opts["orderBy"])
        elif data_type == "detections":
            list_data = app.db.timescale.get_detections_data(
                datastream_id, top=opts["top"], skip=opts["skip"], debug=False, format="list", filters=opts["filter"],
                orderby=opts["orderBy"])
        else:
            rich.print(f"[red]unimplemented")
            raise ValueError("unimplemented")

        app.log.debug(f"Get data from '{data_type}' hypertable took {1000*(time.time() - init):.03} msecs")
        text = data_list_to_sensorthings(list_data, foi_id, datastream_id, opts, data_type)
        app.log.info(
            f" {data_type} query total time ({len(list_data)} points) took {100*(time.time() - init):.03} msecs")
        return generate_response(json.dumps(text), status=200, mimetype='application/json')

    except SyntaxError as e:
        error_message = {
            "code": 400,
            "type": "error",
            "message": str(e)
        }
        return generate_response(json.dumps(error_message), 400, mimetype='application/json')

    except psycopg2.errors.InFailedSqlTransaction as db_error:
        app.log.error("psycopg2.errors.InFailedSqlTransaction" )
        error_message = {
            "code": 500,
            "type": "error",
            "message": f"Internal database connector error: {db_error}"
        }
        app.db.connection.rollback()
        return generate_response(json.dumps(error_message), 500, mimetype='application/json')


@app.route(f'{service_root}/Datastreams(<int:datastream_id>)/Observations', methods=['POST'])
@basic_auth.required
def datastreams_observations_post(datastream_id):
    data = json.loads(request.data.decode())
    return observation_post_handler(data, datastream_id)

@app.route(f'{service_root}/Observations', methods=['POST'])
@basic_auth.required
def observations_post():
    data = json.loads(request.data.decode())

    keys = {
        "Datastream/@iot.id": int,
    }
    try:
        assert_dict(data, keys, verbose=True)
    except AssertionError:
        rich.print(f"[red]Could not find Datastream ID in Observation!")
        return {"code": 400, "type": "error", "message": "Could not find Datastream ID in Observations! "}

    datastream_id = data["Datastream"]["@iot.id"]
    data.pop("Datastream")
    return observation_post_handler(data, datastream_id)


def observation_post_handler(data: dict, datastream_id: int):
    data_type, average =  app.db.get_data_type(datastream_id)
    # Averaged data and files are stored in regular "OBSERVATIONS" table, so it can be managed directly by SensorThings API
    if average or data_type not in ["timeseries", "profiles", "detections"]:
        rich.print("[purple]Forward Observation to FROST")
        init = time.time()
        _, code = post_sta_request(request)
        if code < 300:
            app.log.debug(f"{CYN} SensorThings query took {time.time()-init:.03} sec{RST}")
        # no text in FROST response
        return process_sensorthings_response(request, {})

    # Process "special" data types (timeseries, profiles or detections)

    try:
        if data_type == "timeseries":
            rich.print("[cyan]Inject to Timeseries")
            errmsg = inject_timeseries(data, datastream_id)
        elif data_type == "profiles":
            rich.print("[white]Inject to Profiles")
            errmsg = inject_profiles(data, datastream_id)
        elif data_type == "detections":
            rich.print("[green]Inject to Detections")
            errmsg = inject_detections(data, datastream_id)
        else:
            raise ValueError(f"This should never ever happen!   ")
    except psycopg2.errors.UniqueViolation as e:
        # unique key violated, we're fine, it's the users fault!
        app.log.error("Unique violation error: {str(e)}")
        pass
    except psycopg2.errors.InFailedSqlTransaction as db_error:  # Handle DB errors
        rich.print(f"[red]")
        app.log.error("psycopg2.errors.InFailedSqlTransaction")
        error_message = {
            "code": 500,
            "type": "error",
            "message": f"Internal database connector error: {db_error}"
        }
        app.db.connection.rollback()
        return generate_response(json.dumps(error_message), 500, mimetype='application/json')

    if errmsg:  # manage errors (probably formatting)
        r = {"status": "error", "message": errmsg.replace("\"", "") }
        rich.print(f"[red]Insertion to datastream={datastream_id} failed!!")
        rich.print(f"[red]{errmsg}")
        return generate_response(json.dumps(r), status=400, mimetype='application/json')

    observation_url = generate_observation_url(data_type, datastream_id, data["resultTime"])
    rich.print(f"[green]Insertion to datastream={datastream_id} finished!")
    return generate_response("", status=200, mimetype='application/json', headers={"Location": observation_url})


def inject_timeseries(data: dict, datastream_id: int) -> str:
    keys = {
        "resultTime": str,
        "result": float,
        "resultQuality/qc_flag": int,
    }
    try:
        assert_dict(data, keys, verbose=True)
    except AssertionError as e:
        rich.print(f"[red]Wrong data format! {e}")
        err_msg = {
            "code": 400,
            "type": "error",
            "message": "Profiles data not properly formatted! expected fields 'resultTime' (str), 'value' (float)," \
                       " and 'resultQuality/qc_flag' (int)"
        }
        return err_msg

    return  app.db.timescale.insert_to_timeseries(data["resultTime"], data["result"], data["resultQuality"]["qc_flag"],
                                      datastream_id)



def inject_profiles(data, datastream_id):
    rich.print("")
    keys = {
        "resultTime": str,
        "result": float,
        "parameters/depth": float,
        "resultQuality/qc_flag": int,
    }
    try:
        assert_dict(data, keys, verbose=True)
    except AssertionError as e:
        rich.print(f"[red]Wrong data format! {e}")
        err_msg = {
            "code": 400,
            "type": "error",
            "message": "Profiles data not properly formatted! expected fields 'resultTime' (str), 'value' (float)," \
                       " 'parameters/depth' (float) and 'resultQuality/qc_flag' (int)"
        }
        return err_msg
    return  app.db.timescale.insert_to_profiles(data["resultTime"], data["parameters"]["depth"], data["result"],
                                    data["resultQuality"]["qc_flag"], datastream_id)


def inject_detections(data, datastream_id):
    keys = {
        "resultTime": str,
        "result": int,
    }
    try:
        assert_dict(data, keys, verbose=True)
    except AssertionError as e:
        rich.print(f"[red]Wrong data format! {e}")
        err_msg = {
            "code": 400,
            "type": "error",
            "message": "Detections data not properly formatted! expected fields 'resultTime' (str), 'value' (int)"
        }
        return err_msg

    return  app.db.timescale.insert_to_detections(data["resultTime"], data["result"], datastream_id)


def generate_observation_url(data_type: str, datastream_id, timestamp):
    data_type_digit = {
        "timeseries": "1",
        "profiles": "2",
        "detections": "3"
    }
    prefix = data_type_digit[data_type]
    t = datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    observation_id = int(1e10 * datastream_id + int(t.strftime("%s")))
    return app.service_url + f"/Observations({prefix}{observation_id})"


@app.after_request
def add_cors_headers(response):
    response.headers.add('access-control-allow-origin', '*')
    return response


def run_sta_timeseries_api(env_file="", log=None, port=5000):
    if not log:
        log = setup_log("STA-TS")
    app.log = LoggerSuperclass(log, "STA-TS", colour=CYN)

    if env_file:
        # override os.environ with file enironment vars
        environ = dotenv.dotenv_values(env_file)
    else:
        environ = os.environ

    required_env_variables = ["STA_DB_HOST", "STA_DB_USER", "STA_DB_PORT", "STA_DB_PASSWORD", "STA_DB_NAME",
                              "STA_BASE_URL", "STA_URL_GET", "STA_TS_ROOT_URL", "STA_DB_BASICAUTH"]

    # If basicauth is active, user and password are also required!
    if "STA_DB_BASICAUTH" in environ.keys() and environ["STA_DB_BASICAUTH"].lower() == "true":
        app.log.info("BasicAuth is activated")
        required_env_variables += ["STA_TS_USER", "STA_TS_PASSWORD"]

    for key in required_env_variables:
        if key not in environ.keys():
            raise EnvironmentError(f"Environment variable '{key}' not set!")

    db_name = environ["STA_DB_NAME"]
    db_port = environ["STA_DB_PORT"]
    db_user = environ["STA_DB_USER"]
    db_password = environ["STA_DB_PASSWORD"]
    db_host = environ["STA_DB_HOST"]
    app.service_url = environ["STA_TS_ROOT_URL"]

    app.sta_base_url = environ["STA_BASE_URL"]  # URL to get SensorThings Data
    app.sta_get_url = environ["STA_URL_GET"]
    app.log.info(f"SensorThings Base URL: {app.sta_base_url}")
    app.log.info(f"SensorThings Get URL: {app.sta_get_url}")

    if environ["STA_DB_BASICAUTH"].lower() == "true":
        app.sta_auth = (environ["STA_TS_USER"], environ["STA_TS_PASSWORD"])
        app.config['BASIC_AUTH_USERNAME'] = environ["STA_TS_USER"]
        app.config['BASIC_AUTH_PASSWORD'] = environ["STA_TS_PASSWORD"]

        print_pwd = "*" * len(app.config['BASIC_AUTH_PASSWORD'][:-4])
        print_pwd += app.config['BASIC_AUTH_PASSWORD'][-4:]
        app.log.info(f"STA User: '{app.config['BASIC_AUTH_USERNAME']}'")
        app.log.info(f"STA password: '{print_pwd}'")

    elif environ["STA_DB_BASICAUTH"].lower() == "false":
        app.sta_auth = ()
    else:
        raise ValueError(f"Expected true or false, got {environ['STA_DB_BASICAUTH']}")

    if "STA_TS_DEBUG" in environ.keys():
        app.log.setLevel(logging.DEBUG)
        app.log.debug("Setting log to DEBUG level")

    app.log.info(f"Service sta_ts_url: {app.service_url}")
    app.log.info(f"SensorThings sta_base_url: {app.sta_base_url}")
    app.log.info("Setting up DB connector")
    app.db = SensorThingsApiDB(db_host, db_port, db_name, db_user, db_password, app.log, timescaledb=True)
    app.log.info("Getting sensor list...")
    app.log.info(f"service root is {service_root}")
    app.run(host="0.0.0.0", debug=False, port=port)


if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("-e", "--environment", help="Get environment variables from file", type=str, default="")
    args = argparser.parse_args()
    run_sta_timeseries_api(env_file=args.environment)
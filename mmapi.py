#!/usr/bin/env python3
"""
This script provides the flask methods for the Marine Metadata API.

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 24/1/23
"""
import datetime
from dateutil.relativedelta import relativedelta
from argparse import ArgumentParser
import yaml
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from mmm import MetadataCollector, init_metadata_collector_env, init_metadata_collector
from mmm.common import setup_log
from mmm.schemas import mmm_schemas, mmm_metadata
import json
import os
from flask_basicauth import BasicAuth

app = Flask(__name__)
basic_auth = BasicAuth(app)
CORS(app)
app.config['BASIC_AUTH_USERNAME'] = 'admin'
app.config['BASIC_AUTH_PASSWORD'] = 'secret123'
app.config['BASIC_AUTH_FORCE'] = True  # Require auth for all routes


def run_metadata_api(secrets: str|dict,  log, mc):
    if type(secrets) is dict:
        pass
    elif type(secrets) is str:
        if not os.path.exists(secrets):
            raise FileNotFoundError(f"Could not find config file '{secrets}'")

        with open(secrets) as f:
            secrets = yaml.safe_load(f)["secrets"]

    app.log = log
    app.log.info("starting Metadata API")
    # embed MetadataCollector to app
    app.mc = mc
    app.mmapi_url = secrets["mmapi"]["root_url"]
    if "port" not in secrets["mmapi"].keys():
        port = 8080
    else:
        port = secrets["mmapi"]["port"]
    print("Available endpoints:")
    for rule in app.url_map.iter_rules():
        print(f"{rule.endpoint}: {rule.rule} [{', '.join(rule.methods)}]")

    app.config["BASIC_AUTH_USERNAME"] = secrets["mmapi"]["basic_auth_user"]
    app.config["BASIC_AUTH_PASSWORD"] = secrets["mmapi"]["basic_auth_user"]
    app.config['BASIC_AUTH_FORCE'] = True  # Require auth for all routes

    app.run(host="0.0.0.0", port=port, debug=False)
    return app


def api_error(message, code=400):
    app.log.error(message)
    json_error = {"error": True, "code": code, "message": message}
    return Response(json.dumps(json_error), status=code, mimetype="application/json")


@app.route('/', methods=['GET'])
def root():
    return api_error({
        "message": f"No version defined! try {app.mmapi_url}/v1.0"
    })


@app.route('/mmapi/v1.0/', methods=['GET'])
def default_index():
    d = {c: f"{app.mmapi_url}/mmapi/v1.0/{c}" for c in app.mc.collection_names}
    return Response(json.dumps(d), status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/<path:collection>', methods=['GET'])
def get_collection(collection: str):
    try:
        opts = request.args.to_dict()
        documents = app.mc.get_documents(collection)
        for key, value in opts.items():
            documents = [doc for doc in documents if key in doc.keys() and doc[key] == value]

    except LookupError:
        return api_error(f"Collection not '{collection}', valid collection names {app.mc.collection_names}")
    return Response(json.dumps(documents), status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/<path:collection>/ids', methods=['GET'])
def get_collection_ids(collection: str):
    try:
        opts = request.args.to_dict()
        documents = app.mc.get_documents(collection)
        for key, value in opts.items():
            documents = [doc["#id"] for doc in documents if key in doc.keys() and doc[key] == value]

    except LookupError:
        return api_error(f"Collection not '{collection}', valid collection names {app.mc.collection_names}")
    return Response(json.dumps(documents), status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/<path:collection>', methods=['POST', 'PATCH'])
def post_to_collection(collection: str):
    document = json.loads(request.data)
    app.log.debug(f"Checking if collection {collection} exists...")
    if collection not in app.mc.collection_names:
        return api_error(f"Collection not '{collection}', valid collection names {app.mc.collection_names}")

    if "#id" not in document.keys():
        return api_error(f"Field #id not found in document")

    document_id = document["#id"]
    # identifiers = app.mc.get_identifiers(collection)
    # if document_id in identifiers:
    #     return api_error(f"Document with #id={document_id} already exists in collection '{collection}'")

    app.log.info(f"Adding document {document_id} to collection '{collection}'")
    try:
        inserted_document = app.mc.insert_document(collection, document, update=True)
    except Exception as e:
        return api_error(f"Unexpected error while inserting document: {e}", code=500)

    return Response(json.dumps(inserted_document), status=200, mimetype="application/json")

@app.route('/mmapi/v1.0/validate/<path:collection>', methods=['POST', 'PATCH'])
def post_to_validate(collection: str):
    document = json.loads(request.data)
    app.log.debug(f"Checking if collection {collection} exists...")
    if collection not in app.mc.collection_names:
        return api_error(f"Collection not '{collection}', valid collection names {app.mc.collection_names}")

    if "#id" not in document.keys():
        return api_error(f"Field #id not found in document")

    try:
        app.mc.validate_document(document, collection, exception=True, metadata=False)
    except Exception as e:
        payload = {"success": False, "message": str(e)}
        return Response(json.dumps(payload), status=200, mimetype="application/json")
    return Response({"success": True}, status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/<path:collection>/<path:document_id>', methods=['PUT'])
def put_to_collection(collection: str, document_id: str):
    document = json.loads(request.data)
    app.log.debug(f"Checking if collection {collection} exists...")

    if collection not in app.mc.collection_names:
        return api_error(f"Collection not '{collection}', valid collection names {mc.collection_names}")

    if "#id" not in document.keys():
        return api_error(f"Field #id not found in document")

    identifiers = app.mc.get_identifiers(collection)
    if document_id not in identifiers:
        return api_error(f"Document with #id={document_id} does not exist in collection '{collection}', use PUT instead")

    app.log.info(f"Adding document {document_id} to collection '{collection}'")
    try:
        inserted_document = app.mc.replace_document(collection, document_id, document)

    except AssertionError:
        return api_error(f"No changes detected")
    except Exception as e:
        return api_error(f"Unexpected error while replacing document: {e}", code=500)

    return Response(json.dumps(inserted_document), status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/<path:collection>/<path:identifier>', methods=['GET'])
def get_by_id(collection: str, identifier: str):
    if collection not in app.mc.collection_names:
        error_msg = f"Collection not '{collection}', valid collection names {mc.collection_names}"
        json_error = {"error": True, "code": 400,  "message": error_msg}
        return Response(json.dumps(json_error), status=400, mimetype="application/json")
    try:
        document = app.mc.get_document(collection, identifier)
    except LookupError:
        error_msg = f"Document with #id={identifier} does not exist in collection '{collection}', use PUT instead"
        json_error = {"error": True, "code": 400,  "message": error_msg}
        return Response(json.dumps(json_error), status=404, mimetype="application/json")

    return Response(json.dumps(document), status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/schemas', methods=['GET'])
def get_all_schemas():
    all_schemas = {}
    for key, schema in mmm_schemas.items():
        all_schemas[key] = schema
    return Response(json.dumps(all_schemas), status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/metadata_schema', methods=['GET'])
def get_meta_schema():
    return Response(json.dumps(mmm_metadata), status=200, mimetype="application/json")


@app.route('/mmapi/activity_types', methods=['GET'])
def get_activity_types():
    schema = mmm_schemas["activities"]["properties"]["type"]["enum"]
    return Response(json.dumps(schema), status=200, mimetype="application/json")

@app.route('/mmapi/operation_types', methods=['GET'])
def get_operation_types():
    schema = mmm_schemas["operations"]["properties"]["type"]["enum"]
    return Response(json.dumps(schema), status=200, mimetype="application/json")

@app.route('/mmapi/operation_role_types', methods=['GET'])
def get_operation_role_types():
    schema = mmm_schemas["operations"]["properties"]["participants"]["items"]["properties"]["roles"]["items"]["enum"]
    return Response(json.dumps(schema), status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/schemas/<path:collection>', methods=['GET'])
def get_schema(collection: str):
    if collection not in app.mc.collection_names:
        error_msg = f"Collection not '{collection}', valid collection names {mc.collection_names}"
        json_error = {"error": True, "code": 400,  "message": error_msg}
        return Response(json.dumps(json_error), status=400, mimetype="application/json")

    schema = mmm_schemas[collection]
    return Response(json.dumps(schema), status=200, mimetype="application/json")

@app.route('/mmapi/v1.0/<path:collection>/<path:identifier>/history', methods=['GET'])
def get_document_history(collection: str, identifier: str):
    if collection not in app.mc.collection_names:
        error_msg = f"Collection not '{collection}', valid collection names {mc.collection_names}"
        json_error = {"error": True, "code": 400,  "message": error_msg}
        return Response(json.dumps(json_error), status=400, mimetype="application/json")
    try:
        documents = app.mc.get_document_history(collection, identifier)
    except LookupError:
        error_msg = f"Document with #id={identifier} does not exist in collection '{collection}', use PUT instead"
        json_error = {"error": True, "code": 400,  "message": error_msg}
        return Response(json.dumps(json_error), status=404, mimetype="application/json")
    return Response(json.dumps(documents), status=200, mimetype="application/json")


@app.route('/mmapi/v1.0/<path:collection>/<path:identifier>/history/<path:version>', methods=['GET'])
def get_history_by_id(collection: str, identifier: str, version: int):
    if collection not in app.mc.collection_names:
        error_msg = f"Collection not '{collection}', valid collection names {mc.collection_names}"
        json_error = {"error": True, "code": 400,  "message": error_msg}
        return Response(json.dumps(json_error), status=400, mimetype="application/json")
    try:
        document = app.mc.get_document(collection, identifier, version=version)
    except LookupError:
        error_msg = f"Document with #id={identifier} does not exist in collection '{collection}', use PUT instead"
        json_error = {"error": True, "code": 400,  "message": error_msg}
        return Response(json.dumps(json_error), status=404, mimetype="application/json")

    return Response(json.dumps(document), status=200, mimetype="application/json")

@app.route('/mmapi/v1.0/deployedSensors/<station_id>', methods=['GET'])
def get_deployed_sensors_by_stations(station_id: str):
    mc = app.mc
    import rich
    sensor_ids = mc.get_identifiers("sensors")
    app.log.info("Getting last deployment for every sensor...")
    sensors = []
    for sensor_id in sensor_ids:
        try:
            deployment_station, time, active = mc.get_last_sensor_deployment(sensor_id)
        except LookupError:
            app.log.info(f"No sensor deployment for {sensor_id}")
            continue

        if active and deployment_station == station_id:
            sensors.append(sensor_id)

    return Response(json.dumps(sensors), status=200, mimetype="application/json")

@app.route('/mmapi/v1.0/projects_timeline', methods=['GET'])
def project_timeline():
    """
    Returns a time-series (grafana-like format) with all active projects and their respective months number.
    Only return projects that are european and national and that did not en before the last 4 months
    """
    show_all = False

    p = request.args.get('showAll')
    if p and p.lower() == "true":
        show_all = True

    projects = app.mc.get_documents("projects")
    # Keep only projects with start and end date
    projects = [p for p in projects if p["dateStart"] and p["dateEnd"]]
    resp = []
    for p in projects:
        p["start"] = datetime.datetime.strptime(p["dateStart"], "%Y-%m-%d")
        p["end"] = datetime.datetime.strptime(p["dateEnd"], "%Y-%m-%d")
        p["count"] = 0
        # Mark active projects those european or national projects that have an end date less than 4 months ago
        if p["type"] == "contract" or p["end"] < datetime.datetime.now() + relativedelta(months=4):
            p["active"] = False
        else:
            p["active"] = True

    start = datetime.datetime.strptime("2010-01-01", "%Y-%m-%d")
    end = datetime.datetime.strptime("2030-01-01", "%Y-%m-%d")
    ctime = start

    while ctime <= end:
        activity = False  # flag that determines if there was activity in this period
        data = {}
        for proj in projects:
            if not proj["active"] and not show_all:
                continue

            name = proj["acronym"]
            if ctime < proj["start"]:
                pass
            elif ctime > proj["end"]:
                pass
            else:
                activity = True
                proj["count"] += 1
                data[name] = proj["count"]
        if activity:
            data["time"] = ctime.strftime("%Y-%m-%d %H:%M:%S")
            resp.append(data)
        ctime += relativedelta(months=1)

    return Response(json.dumps(resp), status=200, mimetype="application/json")


@app.route('/endpoints', methods=['GET'])
def list_endpoints():
    """Endpoint to list all available endpoints"""
    base_url = request.host_url  # Includes scheme and host with trailing slash
    endpoints = []
    for rule in app.url_map.iter_rules():
        # Filter out static routes
        if rule.endpoint != 'static':
            endpoints.append({
                'endpoint': rule.endpoint,
                'methods': list(rule.methods - {'OPTIONS', 'HEAD'}),
                'url': base_url + str(rule)
            })
    return jsonify({'endpoints': endpoints})


if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("-s", "--secrets", help="Initialize from secrets yaml file", type=str, default="secrets.yaml")
    args = argparser.parse_args()

    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    log = setup_log("Metadata API")
    mc = init_metadata_collector(secrets, log=log)
    run_metadata_api(secrets,  log, mc)

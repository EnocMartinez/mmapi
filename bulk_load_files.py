#!/bin/env python3
"""

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 09/09/24
"""
import os.path
from argparse import ArgumentParser
import pandas as pd
import json
from mmm.common import file_list, setup_log, timestamp_from_filename
from mmm import init_metadata_collector, init_data_collector, bulk_load_data, DataCollector
import yaml
import logging


def bulk_load_files(dc: DataCollector, files: list, path: str, sensor_name: str, destination: str, foi_id: int,
                    log: logging.Logger, do_not_send=False, usecs=False, no_date=False, station=""):

    sta_data = { # SensorThings data
        "timestamp": [],
        "results": [],
        "datastream_id": [],
        "foi_id": [],
        "parameters": []
    }

    fs_data = {  # FileSystem data
        "timestamp": [],
        "src": [],
        "dst": []
    }

    valid_exts = ["png", "jpg", "jpeg", "wav", "flac"]
    for file in files:
        extension = file.split(".")[-1]
        if extension.lower() not in valid_exts:
            raise ValueError(f"File {file} not a picture!! expected one of the following extensions: {valid_exts}")
        t = timestamp_from_filename(file)
        sta_data["timestamp"].append(t)

        # full filename in the data server
        if no_date:
            file_dest = os.path.join(destination, os.path.basename(file))
        else:
            file_dest = os.path.join(destination,  t.strftime("%Y/%m/%d"), os.path.basename(file))

        sta_data["results"].append(dc.fileserver.path2url(file_dest))
        sta_data["datastream_id"].append(datastream_id)
        sta_data["foi_id"].append(foi_id)
        sta_data["parameters"].append(json.dumps({"size": os.path.getsize(file)}))

        fs_data["timestamp"].append(t)
        fs_data["src"].append(file)
        fs_data["dst"].append(file_dest)

    sta_df = pd.DataFrame(sta_data)
    sta_df["timestamp"] = pd.to_datetime(sta_df["timestamp"])
    sta_df = sta_df.set_index("timestamp")
    sta_df = sta_df.sort_index()

    fs_df = pd.DataFrame(fs_data)
    fs_df["timestamp"] = pd.to_datetime(fs_df["timestamp"])
    fs_df = fs_df.set_index("timestamp")
    fs_df = fs_df.sort_index()

    # Convert first and last timestamp to strings
    init = pd.to_datetime(fs_df.index.values[0]).strftime("%Y%m%d-%H%M%S")
    end = pd.to_datetime(fs_df.index.values[-1]).strftime("%Y%m%d-%H%M%S")
    csv_filename = sensor_name + "_" + init + "_to_" + end + ".csv"

    log.info(f"Creating {csv_filename}...")

    if usecs:
        time_format = "%Y-%m-%dT%H:%M:%S.%fZ"
    else:
        time_format = "%Y-%m-%dT%H:%M:%SZ"

    sta_df.to_csv(csv_filename, date_format=time_format)

    if do_not_send:
        log.warning("Assuming files are already in the server!")
    else:
        log.info("Sending all files")
        dc.fileserver.bulk_send(list(fs_df["src"]), list(fs_df["dst"]))

    bulk_load_data(csv_filename, secrets, sensor_name, "files", foi_name, usecs=usecs, station_name=station)



if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("path", help="Source path", type=str)
    argparser.add_argument("sensor_name", help="Sensor name as registered in SensorThings", type=str)
    argparser.add_argument("destination", help="Destination path in the fileserver", type=str)
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False,
                           default="secrets.yaml")
    argparser.add_argument("-f", "--foi", help="Feature of Interest to be used", type=str, default="")
    argparser.add_argument("-d", "--dry-run", help="Do not perform any changes", action="store_true")
    argparser.add_argument("-n", "--split", help="Split files into N-length arrays", type=int, default=2000)
    argparser.add_argument("--do-not-send", help="Do not send files, assuming they are already in the server",
                           action="store_true")
    argparser.add_argument("--no-date", help="Do not add the YEAR/MONTH/DAY in the destination path",
                           action="store_true")

    argparser.add_argument("--usecs", help="use microsecond precision", action="store_true")
    argparser.add_argument("--station-name", help="Ignore metadata records and assign data to station", type=str,
                           required=False)

    args = argparser.parse_args()
    all_files = file_list(args.path)
    all_files = sorted(all_files)

    # Looking for sensor
    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]
    log = setup_log("file_ingestor")
    mc = init_metadata_collector(secrets, log=log)
    dc = init_data_collector(secrets, log=log, mc=mc)
    log.info(f"Getting sensor_id for sensor '{args.sensor_name}'")
    sensor_id = dc.sta.value_from_query(f'select "ID" from "SENSORS" where "NAME" = \'{args.sensor_name}\';')
    log.info(f"Sensor ID for '{args.sensor_name}' = {sensor_id}")
    log.info(f"Getting files datastreams")
    q = f'''
        select "ID" from "DATASTREAMS" 
            where "SENSOR_ID" = {sensor_id} and
            "PROPERTIES"->>'dataType' = 'files';
    '''
    datastream_id = dc.sta.value_from_query(q)
    log.info(f"Datastream for files in sensor {args.sensor_name} is {datastream_id}")

    if args.foi:
        if str(args.foi).isdecimal():
            foi_id = args.foi
            foi_name = dc.sta.value_from_query(f'select "NAME" from "FEATURES" where "ID" = \'{foi_id}\';')
        else:
            foi_name = args.foi
            foi_id = dc.sta.value_from_query(f'select "ID" from "FEATURES" where "NAME" = \'{foi_name}\';')

        log.info(f"Using user-defined FOI: {foi_name} id={foi_id}")
    else:
        log.info(f"No FOI provided, trying to get default FeatureOfInterest")
        props = dc.sta.datastream_properties[datastream_id]
        if "defaultFeatureOfInterest" in props.keys():
            foi_id = props["defaultFeatureOfInterest"]
            foi_name = dc.sta.value_from_query(f'select "NAME" from "FEATURES" where "ID" = \'{foi_id}\';')
        else:
            raise ValueError(f"Default FeatureOfInterest not specified in datastream {datastream_id}")

    # Split files into smaller arrays
    i = 0

    # Make sure that we are not using another sensor's folder
    destination = args.destination
    sensors = dc.sta.list_from_query('select "NAME" FROM "SENSORS";')
    for s in sensors:
        if s == args.sensor_name:
            if s not in args.destination:
                destination = os.path.join(destination, args.sensor_name)
        elif s in args.destination:
            raise ValueError(f"Found a conflicting sensor name in path! destination {destination} includes an "
                             f"unselected sensor {s}")

    if args.sensor_name not in destination:
        raise ValueError(f"Sensor name {args.sensor_name} not found in destination path: {args.sensor_name}")

    bulk_load_files(dc,all_files, args.path, args.sensor_name, destination, foi_id, log, do_not_send=args.do_not_send,
                    usecs=args.usecs, no_date=args.no_date, station=args.station_name)

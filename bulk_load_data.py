#!/usr/bin/env python3
"""
This script loads bulk data into SensorThings Databsase

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez#upc.edu
license: MIT
created: 21/09/2023
"""
from argparse import ArgumentParser
from mmm import MetadataCollector, CkanClient, propagate_metadata_to_sensorthings
from mmm.metadata_collector import init_metadata_collector
import yaml
import rich

from mmm.core import load_fields_from_dict, bulk_load_data

if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False, default="secrets.yaml")
    argparser.add_argument("-n", "--no-qc", help="Put an NO_QC flag to all empty QC", action="store_true")
    argparser.add_argument("file", help="Data file", type=str)
    argparser.add_argument("sensor_id", help="Sensor ID", type=str)
    argparser.add_argument("-a", "--average", help="Averaged data (period must be specified)", type=str, default="")
    argparser.add_argument("-d", "--detections", help="Detections data", action="store_true")
    argparser.add_argument("-t", "--timeseries", help="Timeseries data", action="store_true")
    argparser.add_argument("-p", "--profiles", help="Profile data", action="store_true")
    argparser.add_argument("-j", "--json", help="JSON-like data, such as AI-inference data", action="store_true")
    argparser.add_argument("-f", "--files", help="Files data (register the paths)", action="store_true")
    argparser.add_argument("--usecs", help="use microsecond precision", action="store_true")
    argparser.add_argument("-F", "--foi", help="FeatureOfInterest ID to assign to the Observations", type=str, required=False)
    argparser.add_argument("--missing-data", help="Inject data that is not already in the database ('direct' or 'hourly')",  type=str, required=False)
    argparser.add_argument("--station-name", help="Ignore metadata records and assign data to station", type=str,
                           required=False)
    args = argparser.parse_args()
    
    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    mc = init_metadata_collector(secrets)

    url = secrets["sensorthings"]["url"]

    if int(args.timeseries) + int(args.profiles) + int(args.detections) + int(args.json) + int(args.files) != 1:
        raise ValueError("ONE data type must be selected")

    if args.profiles:
        data_type = "profiles"
    elif args.detections:
        data_type = "detections"
    elif args.timeseries:
        data_type = "timeseries"
    elif args.json:
        data_type = "json"
    elif args.files:
        data_type = "files"
    else:
        raise ValueError(f"Unimplemented type!")

    rich.print(f"[cyan]Bulk load data from sensor {args.sensor_id} file {args.file}")

    if args.missing_data:
        assert args.missing_data in ["hourly", "direct"], f"Expected 'hourly' or 'direct', but got {args.missing_data} instead "

    bulk_load_data(args.file, secrets, args.sensor_id, data_type, args.foi, average=args.average, no_qc=args.no_qc,
                   usecs=args.usecs, missing_data=args.missing_data, station_name=args.station_name)




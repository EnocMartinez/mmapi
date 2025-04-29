#!/usr/bin/env python3
"""
Resample datasets

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
"""

from argparse import ArgumentParser
import yaml
import rich
from mmm import init_metadata_collector, setup_log
from mmm.data_manipulation import open_csv
from mmm.resampler import resample
import pandas as pd

if __name__ == "__main__":
    # Adding command line options #
    argparser = ArgumentParser()
    argparser.add_argument("input", help="Folder containing of files to be merged")
    argparser.add_argument("output", help="Output file", type=str)
    argparser.add_argument("sensor", help="SensorID", type=str)
    argparser.add_argument("--period", help="Resampling period (defaults to 30 min)", type=str, default="30min")
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False, default="secrets.yaml")
    argparser.add_argument("-p", "--profiles", help="process as profile data", action="store_true")
    args = argparser.parse_args()

    log = setup_log("resampler")

    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]
    print("init...")
    mc = init_metadata_collector(secrets, log=log)

    rich.print("[green]Resampling dataset to %s..." % args.period)
    df = open_csv(args.input)
    df = df.set_index("timestamp")
    df = df.sort_index()

    if args.profiles:
        depths = df["depth"].unique()
        averages = []
        for depth in depths:
            rich.print(f"Averaging data at {depth} m depth")
            ddf = df[df["depth"] == depth]
            avg_df = resample(mc, args.sensor, ddf, args.period)
            averages.append(avg_df)
        avg_df = pd.concat(averages)
    else:
        avg_df = resample(mc, args.sensor, df, args.period)

    if args.output:
        print("Saving to file %s..." % args.output, end="")
        avg_df.to_csv(args.output)
        rich.print("[green]done!")

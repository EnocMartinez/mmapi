#!/usr/bin/env python3
"""

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 11/6/24
"""
import os
from argparse import ArgumentParser
import pandas as pd
import rich
import yaml

import mmm
from mmm import init_metadata_collector, setup_log
from mmm.common import assert_type
from mmm.data_manipulation import open_csv
from mmm.quality_control import dataset_qc


def apply_qc_to_csv(mc: mmm.MetadataCollector, inp, out, sensor_id, save="")->pd.DataFrame:
    """
    Takes a data file and applies quality control to this file
    """
    assert_type(mc, mmm.MetadataCollector)

    sensor = mc.get_document("sensors", sensor_id)
    qc_conf = {}
    for var in sensor["variables"]:
        if "@qualityControl" in var.keys():
            qc_params = mc.get_document("qualityControl", var["@qualityControl"])
            qc_conf[var["@variables"]] = {"qartod": qc_params["qartod"]}

    df = open_csv(inp)
    df = df.set_index("timestamp")
    if save:
        if sensor["#id"] not in save:
            save = os.path.join(save, sensor["#id"])
        rich.print(f"Creating QC plot folder: {save}")
        os.makedirs(save, exist_ok=True)

    df = dataset_qc(mc, sensor, df, paralell=True, save=save)
    print(df)
    rich.print(f"storing CSV...")
    df.to_csv(out)


if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("input", type=str, help="input file", default="")
    argparser.add_argument("output", type=str, help="output file", default="")
    argparser.add_argument("sensor", type=str, help="sensor", default="")
    argparser.add_argument("--save", type=str, help="Folder to save QC plots", default="")
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False, default="secrets.yaml")
    args = argparser.parse_args()

    log = setup_log("qc")

    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]
    print("init...")
    mc = init_metadata_collector(secrets, log=log)

    args = argparser.parse_args()
    apply_qc_to_csv(mc, args.input, args.output, args.sensor, save=args.save)

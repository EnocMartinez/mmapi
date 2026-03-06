#!/usr/bin/env python3
"""
Script that takes data from a SensorThings database and generates  NetCDF data

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 4/10/23
"""

from argparse import ArgumentParser
from mmm import DataCollector, setup_log, CkanClient
import yaml
import rich
import logging
from mmm.common import RED, RST
from mmm.metadata_collector import init_metadata_collector
import os

# Structure to store the generated resources. Some datasets may have dependencies

# TODO fix dataset generation time coverage
# TODO fix broken --overwrite flag

def generate_dataset(dataset_id: str, service_name: str, time_start: str, time_end: str, secrets, log: logging.Logger,
                     current=True, format:str= "", verbose=False, erddap_config=False, overwrite=False, deliver=True,
                     resources=[]):
    """
    Generate a dataset following the configuration in the metadata database dataset register.
    :param dataset_id: id of the dataset register
    :param time_start:
    :param time_end:
    :param out_folder: output folder where the datasets will be generated. If null, use 'dataset_id' as folder name.
    :param secrets: secrets.yaml file
    :param secrets: For periodic datasets generate the current file, e.g. today's file or this monthly file
    :return: list of filenames
    """

    with open(secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    if not log:
        log = setup_log("gen_dataset", log_level="info")

    if verbose:
        log.setLevel(logging.DEBUG)

    mc = init_metadata_collector(secrets, log=log)
    dc = DataCollector(secrets, log, mc=mc)

    dc.generate_dataset(dataset_id, service_name, time_start, time_end, fmt=format, current=current, secrets=secrets,
                        overwrite=overwrite, erddap_config=erddap_config, resources=resources, deliver=deliver,)

def list_datasets(secrets, verbose=False):
    with open(secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    log = setup_log("sta_to_emso")
    mc = init_metadata_collector(secrets, log=log)
    datasets = mc.get_documents("datasets")
    for dataset in datasets:
        services = list(dataset["export"].keys())
        rich.print(f"'{dataset['#id']}' - services: {services}")


if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("dataset_id", help="Dataset ID", type=str)
    argparser.add_argument("services", help="Service name (e.g. ERDDAP, CKAN, etc.)", nargs="+", type=str)
    argparser.add_argument("--current", help="Generate the current file (e.g. current day or current month)", action="store_true")
    argparser.add_argument("--list", help="List registered datasets and exit", action="store_true")
    argparser.add_argument("--local", help="Do not send to destination server", action="store_true")
    argparser.add_argument("-v", "--verbose", help="verbose output", action="store_true")
    argparser.add_argument("-F", "--force", help="Overwrite any existing dataset", action="store_true")
    argparser.add_argument("-e", "--erddap", help="Configure dataset in erddap", action="store_true")
    argparser.add_argument("-r", "--resources", help="Generate only resources within list", type=str, nargs="+")
    argparser.add_argument("-o", "--overwrite", help="Overwrite existing datasets", action="store_true")
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False,
                           default="secrets.yaml")
    argparser.add_argument("-t", "--time-range", help="Time range with ISO notation, like 2022-01-01/2023-01-01",
                           type=str, required=False, default="")
    argparser.add_argument("-p", "--period", help="period to generate files, 'day', 'month' or 'year'. If not set a "
                                                  "single big file will be generated", type=str,
                           required=False, default="")

    argparser.add_argument("-f", "--format",type=str,  required=False, default="",
                           help="Suggest format such as netcdf, csv, etc. May not work for all datasets")

    args = argparser.parse_args()

    deliver = True
    if args.local:
        deliver = False

    if args.time_range:
        tstart, tend = args.time_range.split("/")
    else:
        tstart = ""
        tend = ""

    log = setup_log("gen_dataset", log_level="info")

    for service in args.services:
        generate_dataset(args.dataset_id, service, tstart, tend, args.secrets, log, format=args.format,
                         current=args.current, verbose=args.verbose, erddap_config=args.erddap,
                         overwrite=args.overwrite, resources=args.resources, deliver=deliver)
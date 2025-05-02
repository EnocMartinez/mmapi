#!/usr/bin/env python3
"""
This script updates data from metadata database and registers it to CKAN

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez#upc.edu
license: MIT
created: 21/09/2023
"""
from argparse import ArgumentParser
import yaml
import rich
from mmm.metadata_collector import init_metadata_collector
from mmm import CkanClient, propagate_metadata_to_ckan
from mmm.common import run_over_ssh, run_subprocess, setup_log
from mmm.fileserver import FileServer

if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False, default="secrets.yaml")
    argparser.add_argument("-c", "--collections", help="Only use certain collections", type=str, nargs="+", default=[])
    argparser.add_argument("-d", "--datasets", help="Propagate only datasets in list", type=str, nargs="+", default=[])

    args = argparser.parse_args()
    
    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]
    collections = args.collections
    if not args.collections:
        collections = ["datasets", "organizations", "projects"]
    else:
        collections = args.collections

    mc = init_metadata_collector(secrets)

    proj = secrets["ckan"]["project_logos"]
    org = secrets["ckan"]["organization_logos"]
    log = setup_log("Meta2Ckan")

    fileserver = FileServer(secrets["fileserver"], log)
    ckan = CkanClient(mc, secrets["ckan"]["url"], secrets["ckan"]["api_key"], fileserver, log)
    propagate_metadata_to_ckan(mc, ckan, collections, datasets=args.datasets)

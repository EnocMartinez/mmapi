#!/usr/bin/env python3
"""
Looks what datasets are available in FileServer and updates the CKAN registry if needed

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez#upc.edu
license: MIT
created: 21/09/2023
"""
from argparse import ArgumentParser
from mmm import MetadataCollector, CkanClient, propagate_metadata_to_sensorthings, setup_log, init_data_collector
import yaml
import rich
import os

from mmm.common import run_over_ssh, YEL, RST
from mmm.fileserver import FileServer
from mmm.metadata_collector import init_metadata_collector
import logging

if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("path", help="Path in the fileserver to scan", type=str)
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False, default="secrets.yaml")
    argparser.add_argument("-v", "--verbose", help="Verbose output", action="store_true")
    args = argparser.parse_args()

    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    log = setup_log("ckan_datasets")
    fileserver_host = secrets["fileserver"]["host"]
    files = run_over_ssh(fileserver_host, f"ls {args.path}")
    files = [f for f in  files.split("\n") if f]  # split and delete blank lines

    mc =init_metadata_collector(secrets)
    fileserver = FileServer(secrets["fileserver"], log)

    ckan = CkanClient(mc, secrets["ckan"]["url"], secrets["ckan"]["api_key"])
    dataset_ids = mc.get_identifiers("datasets")
    ckan_datasets = ckan.get_packages()
    rich.print(f"CKAN packages: {ckan_datasets}")
    for basename in files:
        filepath = os.path.join(args.path, basename)
        url = fileserver.path2url(filepath)

        # Now let's check if the filename is the ID of a dataset
        dataset_id, extension = basename.split(".")
        if dataset_id in dataset_ids:
            log.info(f"Found dataset file for {dataset_id}")

            # ID of CKAN's dataset (package) is the same ID in lowercase
            package_id = dataset_id.lower()
            # For the resource_id use the same ID as the dataset + the extension, we are assuming that we have only one
            # file per dataset
            resource_id = dataset_id.lower() + "_" + extension.lower()

            doc = mc.get_document("datasets", dataset_id)
            name = doc["title"]
            description = doc["summary"]

            log.debug("Now check if this dataset is registered in CKAN")
            if dataset_id.lower() not in ckan_datasets:
                log.warning(YEL + f"dataset {dataset_id} not registered!!" + RST)

            if not ckan.check_if_resource_exists(resource_id):
                log.info("Registering file as a new resource...")

                if extension == "nc":
                    extension = "NetCDF"

                ckan.resource_create(package_id, resource_id, description, name, resource_url=url, format=extension)
            else:
                log.info(f"Resource for dataset {dataset_id} already exists")


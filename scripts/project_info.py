#!/usr/bin/env python3
"""
Get project info from:
    CORDIS Gets data from cordis (https://cordis.europa.eu) and returns project info in JSON format
    AEI: Gets data from Agencia Española de Investigación (https://www.aei.gob.es/).

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 27/01/2026
"""


import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory (project root)
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))

# Add the parent directory to the sys.path
sys.path.insert(0, parent_dir)


from argparse import ArgumentParser

from mmm.aei import aei_project
from mmm.cordis import get_cordis_metadata, assign_orgs_to_project
import rich
import json
from mmm import init_metadata_collector, init_metadata_collector_env
import yaml

if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("source", type=str, help="Project source, must be 'cordis' or 'aei'", default="")
    argparser.add_argument("project_id", type=str, help="Project ID to fetch in CORDIS", default="")
    argparser.add_argument("-a", "--acronym", type=str, help="Project acronym (mandatory for AEI)")
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False,
                           default="secrets.yaml")
    argparser.add_argument("-e", "--environment", action="store_true", help="Initialize from environment variables")
    argparser.add_argument("--force", action="store_true", help="skips insert question", default=False)
    argparser.add_argument("--clear", action="store_true", help="clears all prevoius downloads", default=False)

    args = argparser.parse_args()

    assert args.source in ["cordis", "aei"]
    if args.environment:
        mc = init_metadata_collector_env()
    elif args.secrets:
        with open(args.secrets) as f:
            secrets = yaml.safe_load(f)["secrets"]
            mc = init_metadata_collector(secrets)
    else:
        raise ValueError("Metadata API needs to be configured using environment variables or yaml file!")

    if args.source == "cordis":
        data = get_cordis_metadata(args.project_id)

        with open(args.secrets) as f:
            secrets = yaml.safe_load(f)["secrets"]
            staconf = secrets["sensorthings"]


        data = assign_orgs_to_project(mc, data)



    elif args.source == "aei":
        if not args.acronym:
            raise ValueError("Acronym is mandatory for AEI projects")
        data = aei_project(mc, args.project_id, args.acronym, force=args.force)

    rich.print(data)
    if not args.force:
        rich.print("[cyan]Store this information into database? (yes/on)")
        response = input()
        if response != "yes":
            rich.print("[red]Aborting")
            exit()
    else:
        rich.print("[purple]Ingesting into database (forced with cli arguments)")
    mc.insert_document("projects", data, update=True)

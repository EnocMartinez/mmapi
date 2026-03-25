#!/usr/bin/env python3
"""

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 24/1/23
"""
import os
from argparse import ArgumentParser
import yaml
import json
import rich
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.insert(0, parent_dir)

import mmm.schemas
from mmm.metadata_collector import MetadataCollector, init_metadata_collector


def ensure_element(collection, identifier, mc: MetadataCollector):
    rich.print(f"Ensuring {collection} id='{identifier}'", end="")
    try:
        doc = mc.get_document(collection, identifier)
    except Exception:
        rich.print("[red]Could not load document")
        raise ValueError(f"Document '{collection}':'{identifier}' does not exist!")
    rich.print("[green]ok!")
    return doc


def process(key: str, value: any, mc: MetadataCollector):
    # If value is a dict, process all its elements
    if key.startswith("@") and key[1:] in mc.collection_names:
        # This is a link, only valid for str and lists
        collection = key[1:]
        if type(value) is str:  # simple relation
            rich.print(f"[cyan]processing single element")
            doc_id = value
            ensure_element(collection, doc_id, mc)
        elif type(value) is list:
            rich.print(f"[white]processing lists:", end="")
            for doc_id in value:
                rich.print(f"[blue]{value}", end="")
                ensure_element(collection, doc_id, mc)
            rich.print("")
        else:
            raise ValueError(f"Element {type(value)} not valid, expected list or str")

    if type(value) is dict:
        for subkey, subvalue in value.items():
            process(subkey, subvalue, mc)

    if type(value) is list:
        for element in value:
            if type(element) is dict:
                for subkey, subvalue in element.items():
                    process(subkey, subvalue, mc)


if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False,
                           default="secrets.yaml")
    argparser.add_argument("--contents-only", required=False, action="store_true")
    argparser.add_argument("collection", help="new identifier", type=str)
    argparser.add_argument("old", help="old identifier", type=str)
    argparser.add_argument("new", help="new identifier", type=str)

    args = argparser.parse_args()

    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    mc = init_metadata_collector(secrets)

    old = args.old
    new = args.new
    collection = args.collection

    if not args.contents_only:
        rich.print(f"Update identifier '{old}' to '{new}' in collection '{args.collection}'")
        doc = ensure_element(args.collection, old, mc)

        old_doc = doc.copy()
        doc_ids = mc.get_identifiers(collection)
        if new in doc_ids:
            raise ValueError(f"doc '{collection}/{new}' already exists!")

        # Update the id in the document
        doc["#id"] = new

        mc.insert_document(collection, doc)
        mc.delete_document(collection, old)

    local_file = f"Metadata/metadata/{collection}/{old}.json"
    if os.path.exists(local_file):
        rich.print(f"removing local file {local_file}")
        os.remove(local_file)

    # Now check the schemas to get
    schemas = mmm.schemas.mmm_schemas
    modify = []

    for current_collection, schema in schemas.items():
        target = f"@{collection}"
        schema_str = json.dumps(schema)
        rich.print(f"looking for {target} in {current_collection}...", end="")
        if target in schema_str:
            rich.print("[green]yes")
            modify.append(current_collection)
        else:
            rich.print("[yellow]no")

    for col in modify:
        for doc in mc.get_documents(col):
            rich.print(f"updating {col}/{doc['#id']}")
            doc_str = json.dumps(doc)
            if old in doc_str:
                new_doc_str = doc_str.replace(f'"{old}"', f'"{new}"')
                rich.print(new_doc_str)
                new_doc = json.loads(new_doc_str)
                mc.replace_document(col, new_doc["#id"], new_doc)

    mc.delete_document(args.collection, old)


#!/usr/bin/env python3
"""
This script connects to a PostgresQL database and converts from the database to JSON files. Can be used to perform simple
CRUD operations from local files.

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez#upc.edu
license: MIT
created: 21/09/2023
"""
from argparse import ArgumentParser, ArgumentError

from mmm import setup_log
from mmm.metadata_collector import MetadataCollector, init_metadata_collector
import yaml
import rich
import os
import time
import json


def load_from_database(mc, subset=[], history=False, verbose=False):
    """
    Loads ALL data from database into dict
    :param mc: MetadataCollector
    :param history: Use history database
    :param verbose: print additional info
    :return: dictionary with all data
    """
    if verbose:
        t = time.time()

    db_data = {}
    if subset:
        collections = subset
    else:
        collections = mc.collection_names
    for collection in collections:
        db_data[collection] = {}
        docs = mc.get_documents(collection, history=history)
        for doc in docs:
            doc_id = doc["#id"]
            db_data[collection][doc_id] = doc
    if verbose:
        rich.print(f"Load all data took {(time.time() - t)*1000:0.1f} msecs")
    return db_data


def load_from_filesystem(folder, subset=[], history=False):
    """
    Loads ALL data from database into dict
    :param folder: metadata folder
    :return: dictionary with all data
    """
    fs_data = {}
    folders = [os.path.join(folder, f) for f in os.listdir(folder)]
    for folder in folders:
        collection_name = os.path.basename(folder)
        if subset and collection_name not in subset:
            continue
        files = [os.path.join(folder, file) for file in os.listdir(folder)]
        files = sorted(files)
        fs_data[collection_name] = {}
        for file in files:
            with open(file) as f:
                try:
                    filedata = json.load(f)
                except json.decoder.JSONDecodeError:
                    rich.print(f"[red]ERROR!! could not load fild {file}, JSON decode error")
                    exit()

            file_id = filedata["#id"]
            if file_id != os.path.basename(file).split(".")[0]:
                rich.print(f"[red]ERROR!! File '{file}' has ID='{file_id}', but it does not match with filename!")
                exit()

            if history:  # in history we have multiple instances with the same id, so add version at the end
                file_id += f"#v{filedata['#id']}"
            fs_data[collection_name][file_id] = filedata
    return fs_data


def store_to_filesystem(data, folder, verbose=False, subset=[], history=False):
    """
    Takes a data dict and stores all objects to the filesystem
    :param folder: folder where to store all the data
    :param verbose: print additional info
    :param data: dict with all the data
    """
    t = time.time()
    n = 0
    for collection in data.keys():
        if subset and collection not in subset:
            continue
        folder_path = os.path.join(folder, collection)
        os.makedirs(folder_path, exist_ok=True)
        for document_id, document in data[collection].items():
            if history:
                version = document["#version"]
                document_filename = os.path.join(folder_path, document_id) + f".v{version}.json"
            else:
                document_filename = os.path.join(folder_path, document_id) + ".json"
            with open(document_filename, "w") as f:
                f.write(json.dumps(document, indent=2, ensure_ascii=False))
            n += 1

    if verbose:
        rich.print(f"{n} written to filesystem took {(time.time() - t)*1000:0.1f} msecs")


def clear_temporal_files(folders: list):
    """
    Removes all temporal files in metadata folder
    :param folder: folder whose content will be erased
    """
    n = 0
    nfolders = 0
    for folder in folders:
        folders = [os.path.join(folder, f) for f in os.listdir(folder)]
        for folder in folders:
            files = [os.path.join(folder, file) for file in os.listdir(folder)]
            for file in files:
                os.remove(file)
                n += 1
            os.rmdir(folder)
            nfolders += 1

    rich.print(f"Deleted {nfolders} folders")
    rich.print(f"Deleted {n} files")


def compare_dicts(doc1: dict, doc2: dict, metadata=False) -> bool:
    """
    Compares two dicts and returns True if there are differences, False if they are equal
    If metadata is False, remove all metadata elements
    :param doc1: dict1
    :param doc2: dict2
    :param metadata: flag that indicates if metadata should also be compared (default False)
    :returns: True if dicts are equal, false if there are differences
    """
    if not metadata:
        # comparing without metadata
        doc1 = MetadataCollector.strip_metadata_fields(doc1)
        doc2 = MetadataCollector.strip_metadata_fields(doc2)
        return doc1 == doc2

    return doc1 == doc2


def compoare_fs_to_db(db_data, fs_data) -> list:
    """
    Compares data in filesystem with data in the database
    :param db_data: data from database
    :param fs_data: data from filesystem
    :return: dict with differences
    """
    diff = []
    for collection in fs_data.keys():
        for doc_id, doc in fs_data[collection].items():
            if doc_id not in db_data[collection].keys():
                rich.print(f"[cyan]{collection} {doc_id} new document!")
                diff.append({"collection": collection, "doc_id": doc_id, "action": "create"})
            elif not compare_dicts(db_data[collection][doc_id], fs_data[collection][doc_id]):
                rich.print(f"[green]{collection} {doc_id} modified!")
                diff.append({"collection": collection, "doc_id": doc_id, "action": "replace"})

        for doc_id, doc in db_data[collection].items():
            if doc_id not in fs_data[collection].keys():
                rich.print(f"[red]{collection} {doc_id} deleted")
                diff.append({"collection": collection, "doc_id": doc_id, "action": "delete"})
    return diff


if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False, default="secrets.yaml")
    argparser.add_argument("-g", "--get", help="Get all metadata from a database and stores it to JSON files", action="store_true")
    argparser.add_argument("-p", "--put", help="Puts all metadata from folder and  and stores it to JSON", action="store_true")
    argparser.add_argument("-S", "--status", help="Shows documents that have been modified", action="store_true")
    argparser.add_argument("--clear", help="Clear temporal files", action="store_true")
    argparser.add_argument("-c", "--collections", help="Only use certain collections", nargs="+", default=[])
    argparser.add_argument("-f", "--folder", help="folder to store all metadata, by default 'Metadata'", type=str, default="Metadata")
    argparser.add_argument("-v", "--verbose", help="Show more info", action="store_true")
    argparser.add_argument("--clear-history", help="Delete ALL history and reset version to 1", action="store_true")
    argparser.add_argument("--healthcheck", help="Ensure all relations", action="store_true")
    argparser.add_argument("--force", help="Force put, ignore all checks", action="store_true")
    argparser.add_argument("-m", "--force-metadata", help="Force the metadata as is in the document", action="store_true")

    args = argparser.parse_args()
    log = setup_log("metamanager")
    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]
    print("init...")
    mc = init_metadata_collector(secrets, log=log)

    db_name = "metadata"
    db_name_hist = db_name + "_hist"
    folder = os.path.join(args.folder, db_name)
    folder_hist = os.path.join(args.folder, db_name_hist)

    collections = mc.collection_names
    if args.collections:
        collections = args.collections

    if args.healthcheck:
        mc.healthcheck(collections=collections)
        exit()

    if args.clear_history:
        rich.print(f"[red]WARNING! This will erase ALL history records, do you want to continue? (yes/no)")
        response = input()
        if response != "yes":
            rich.print("cancelling request")
            exit()
        mc.reset_version_history()
        rich.print("Sync filesystem with Database...", end="")
        db_data = load_from_database(mc, subset=collections, verbose=args.verbose)
        store_to_filesystem(db_data, folder, verbose=args.verbose, subset=collections, history=False)
        db_data = load_from_database(mc, subset=collections, verbose=args.verbose, history=True)
        store_to_filesystem(db_data, folder_hist, verbose=args.verbose, subset=collections, history=True)
        rich.print("[green]done!")
        exit()

    force_meta = False
    if args.force_metadata:
        force_meta = True

    os.makedirs(folder, exist_ok=True)
    if args.get and args.put:
        raise ArgumentError("Only get or put arguments can be set!")
    init = time.time()
    if args.clear:
        rich.print("[red]Deleting all files!")
        clear_temporal_files([folder, folder_hist])
        exit()

    if args.get:
        init = time.time()
        rich.print(f"Getting all data from database {mc.db_name} and storing to {folder}...", end="")
        db_data = load_from_database(mc, subset=collections, verbose=args.verbose)
        store_to_filesystem(db_data, folder, verbose=args.verbose, subset=collections, history=False)
        rich.print("[green]ok!")

        rich.print(f"Getting all data from database {mc.db_hist_name    } and storing to {folder_hist}...", end="")
        db_data = load_from_database(mc, subset=collections, verbose=args.verbose, history=True)
        store_to_filesystem(db_data, folder_hist, verbose=args.verbose, subset=collections, history=True)
        rich.print("[green]ok!")
        rich.print(f"[green]done! took {1000*(time.time() - init):.03} msecs")
        exit()

    diff = []  # list of elements to be updated
    if args.put or args.status:
        db_data = load_from_database(mc, subset=collections)
        fs_data = load_from_filesystem(folder, subset=collections)
        diff = compoare_fs_to_db(db_data, fs_data)

        # checking also historical data
        db_data_hist = load_from_database(mc, subset=collections, history=True)
        fs_data_hist = load_from_filesystem(folder_hist, subset=collections, history=True)

    if args.put:
        replaced = 0
        inserted = 0
        deleted = 0

        diff = compoare_fs_to_db(db_data, fs_data)

        for record in diff:
            collection = record["collection"]
            document_id = record["doc_id"]
            action = record["action"]
            if collection not in collections:
                continue
            rich.print(f"action: {action}")
            if action == "replace":
                mc.replace_document(collection, document_id,  fs_data[collection][document_id], force=args.force)
                mc.get_document(collection, document_id)  # update the local document
                replaced += 1
            elif action == "create":
                mc.insert_document(collection, fs_data[collection][document_id], force=args.force)
                inserted += 1

            elif action == "delete":
                rich.print(f"[red]Do you want to delete document '{document_id}'? ctrl+c to exit")
                input()
                mc.delete_document(collection, document_id)
                deleted += 1
            else:
                raise ValueError("Unknwon operation")

        rich.print(f"MetadataManager summary:")
        rich.print(f"    Documents inserted: {inserted}")
        rich.print(f"    Documents updated:  {replaced}")
        rich.print(f"    Documents deleted:  {deleted}")

    rich.print(f"[cyan]All tasks finished, took {1000*(time.time() - init):.03f} msecs")

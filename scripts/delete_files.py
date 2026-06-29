"""
Delete duplicated images found when generating datasets
"""
from argparse import ArgumentParser
import pandas as pd
import rich
import os
import sys
import yaml
import numpy as np
import time
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory (project root)
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))

# Add the parent directory to the sys.path
sys.path.insert(0, parent_dir)
from mmm import setup_log, SensorThingsApiDB
from mmm.data_sources.postgresql import sql_list
from mmm.common import process_time_range, run_over_ssh

pd.set_option('display.max_colwidth', None)


def confirm(prompt="Do you want to continue?"):
    while True:
        rich.print(f"{prompt}" + " (y/n): ", end="")
        answer = input().strip().lower()
        if answer in ("y", "yes"):
            return True
        elif answer in ("n", "no"):
            return False
        print("Please enter 'y' or 'n'.")


if __name__ == "__main__":

    argparser = ArgumentParser(
        description="This script looks for file data in the SensorThings DB and deletes those entries in the DB AND in the file server"
    )
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False,
                           default="secrets.yaml")
    argparser.add_argument("-d", "--dry-run", action="store_true", help="test the command but do not perform changes")
    argparser.add_argument("sensor_id", help="sensor ID", type=str)
    argparser.add_argument("timerange", help="time range", type=str)
    args = argparser.parse_args()

    log = setup_log("delete_files")
    with open(args.secrets) as f:
        secrets = yaml.safe_load(f.read())

    psql_conf = secrets["secrets"]["sensorthings"]
    db = SensorThingsApiDB(psql_conf["host"], psql_conf["port"], psql_conf["database"], psql_conf["user"],
                           psql_conf["password"], log, timescaledb=True)

    host = secrets["secrets"]["fileserver"]["host"]
    basepath = secrets["secrets"]["fileserver"]["path_links"][0]
    if not basepath.endswith("/"):
        basepath += "/"
    baseurl = secrets["secrets"]["fileserver"]["baseurl"]

    sensor_id = args.sensor_id
    start_time, end_time = process_time_range(args.timerange)
    log.info(f"Deleting files from sensor \"{sensor_id}\" from {start_time} to {end_time}")

    q = f"""
    select * from "OBSERVATIONS" where 
        
        "DATASTREAM_ID" IN (select "ID" from "DATASTREAMS" where
                "SENSOR_ID" = (select "ID" from "SENSORS" where "NAME" = '{sensor_id}')
                and "PROPERTIES"->>'dataType' = 'files'
            )
        and "PHENOMENON_TIME_START" between '{start_time}' and '{end_time}'
    """

    df = db.dataframe_from_query(q)

    if len(df) < 1:
        log.warning("No files to delete!")
        exit()

    log.info(f"Got {len(df)} file to delete")
    log.info("Some sample files: ")

    for index in [0, 1, len(df) -2, len(df)-1]:
        rich.print(f'i={index} \t {df.iloc[index]["RESULT_STRING"]}')


    url_list = df["RESULT_STRING"].tolist()

    file_list = [f.replace(baseurl, basepath) for f in url_list]

    folders = [os.path.dirname(f) for f in file_list]
    folders = list(np.unique(folders))
    folders = [str(f) for f in folders]
    rich.print(folders)

    if args.dry_run:
        log.info("Running in dry mode! no changes will be performed")
    if not confirm("\n\n[red]THIS IS A DESTRUCTIVE OPERATION. Do you want to delete these files? this can't be reversed!"):
        log.info("Cancelling by user request")

    log.info("Deleting info from database...")
    t = time.time()
    q = f"""
        delete from "OBSERVATIONS" where 
    
        "DATASTREAM_ID" IN (select "ID" from "DATASTREAMS" where
                "SENSOR_ID" = (select "ID" from "SENSORS" where "NAME" = '{sensor_id}')
                and "PROPERTIES"->>'dataType' = 'files'
            )
        and "PHENOMENON_TIME_START" between '{start_time}' and '{end_time}'
    """

    if args.dry_run:
        q = q.replace("delete", "select *")

    db.exec_query(q, fetch=False)
    log.info(f"Delete {len(df)} db entries took {time.time() - t:.03f} seconds")

    log.info("Removing files from disk")

    file_list = np.array(file_list)

    max_size = 100
    num_chunks = int(np.ceil(len(file_list) / max_size))

    # 3. Split the array

    for chunk in np.array_split(file_list, num_chunks):
        files = " ".join(chunk)
        cmd = f"rm {files}"
        if args.dry_run:
            cmd = "ls" + cmd[2:]  # convert remove to list
        run_over_ssh(host, cmd)

    log.info(f"Delete {len(df)} files took {time.time() - t:.03f} seconds")

    log.info("Removing empty dirs")

    for folder in folders:
        run_over_ssh(host, f"rmdir {folder}")





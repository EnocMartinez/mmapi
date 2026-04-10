#!/usr/bin/env python3
"""
Gets ALL files from the STA database

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 27/01/2026
"""
from argparse import ArgumentParser
import sys
import yaml
import os
import time
import numpy as np
import rich
import pandas as pd
from ansible.plugins.loader import module_utils_loader

# Add parent dir
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)))
from mmm import init_data_collector, setup_log
from mmm.parallelism import multiprocess
from mmm.common import check_url


def bulk_check_urls(times: np.ndarray, urls: np.ndarray, sensor_ids: np.ndarray):
    assert len(times) == len(urls) == len(sensor_ids)
    errors = []
    for t, url, sid in zip(times, urls, sensor_ids):
        if not check_url(url):
            errors.append({"time": t, "url": url, "sensor_id": sid})
    return errors

if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False,
                           default="secrets.yaml")
    args = argparser.parse_args()


    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    log = setup_log("check_file_urls")
    dc = init_data_collector(secrets, log)
    log.info("Getting all URLs from STA database...")

    fake_url = "https://files.obsea.es/pictures/IPC608_CD3F_110/2025/02/22/20250222-102701-IPC608_CD3F_11d0.jpg"


    t = time.time()
    df = dc.sta.dataframe_from_query(f"""
    select "OBSERVATIONS"."PHENOMENON_TIME_START" as time, "SENSORS"."NAME" as sensor_id, "RESULT_STRING" as url from "OBSERVATIONS", "SENSORS", "DATASTREAMS" where
        "DATASTREAM_ID" in (select "ID" from "DATASTREAMS" where "PROPERTIES"->>'dataType' = 'files')
        and "DATASTREAMS"."SENSOR_ID" = "SENSORS"."ID"
        and "OBSERVATIONS"."DATASTREAM_ID" = "DATASTREAMS"."ID"
        and "OBSERVATIONS"."PHENOMENON_TIME_START" between '2021-01-01' and '2022-01-01'
    limit 200000
    ;        
    """)
    df.to_csv("all_files.csv", index=False)
    log.info(f"Query time {time.time()-t:.02f} secs")
    log.info(f"Got {len(df)} files to check")

    data = zip(df["time"], df["url"], df["sensor_id"])

    chunks = 1000
    times = np.array_split(df["time"], chunks)
    urls = np.array_split(df["url"], chunks)
    sensor_ids = np.array_split(df["sensor_id"], chunks)

    arguments = []
    for i in range(chunks):
        arguments.append((times[i], urls[i], sensor_ids[i]))

    errors = multiprocess(arguments, bulk_check_urls, max_workers=20)
    errors = [x for sublist in errors for x in sublist]
    rich.print(errors)
    errors = pd.DataFrame(errors)

    log.info(f"URL check time {time.time() - t:.02f} secs")
    rich.print(f"[red]Wrong URLs {len(errors)}/{len(df)}")
    rich.print(f"[green]correct URLs {len(df) - len(errors)}/{len(df)} ")

    print(errors)
    if not errors.empty:
        errors.to_csv("all_errors.csv", index=False)


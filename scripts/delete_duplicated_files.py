"""
Delete duplicated images found when generating datasets
"""
from argparse import ArgumentParser
import pandas as pd
import rich
import os
import sys
import yaml
from cloudinit.distros import fetch

current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory (project root)
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))

# Add the parent directory to the sys.path
sys.path.insert(0, parent_dir)
from mmm import setup_log, SensorThingsApiDB
from mmm.data_sources.postgresql import sql_list

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
        description='This script reads a CSV, like "DUPLICATED-FIRST.csv" and deletes all its entries, including FileServer files and database rows',
        epilog="⚠️ WARNING: This action is destructive, use with caution!"
    )
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False,
                           default="secrets.yaml")
    argparser.add_argument("csv", help="CSV file", type=str)
    args = argparser.parse_args()

    log = setup_log("clean_dups")
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


    df_all = pd.read_csv(args.csv)
    df_all = df_all[["timestamp", "sensor_id", "value"]]

    for sensor in df_all["sensor_id"].unique():
        rich.print(f"[cyan]Deleting data from sensor '{sensor}'")
        df2 = df_all[df_all["sensor_id"] == sensor]

        rich.print(df2)

        n_entries = len(df2)
        n_timestamp = len(df2["timestamp"].unique())
        n_files = len(df2["value"].unique())

        rich.print(f"      entries={n_entries}")
        rich.print(f"    timestamp={n_timestamp}")
        rich.print(f"       files={n_files}")

        if n_files == n_timestamp and n_entries == 2 * n_files:
            rich.print("Probably we have duplicated database entries, suggestion:do not to remove the files!")
        if n_files == n_timestamp == n_entries :
            rich.print("Probably we have duplicated files, suggestion: delete DB entries and files!")

        df2 = df2[df2.duplicated(subset=["timestamp"], keep="first")]

        rich.print(f"working with {len(df2)} entries")

        if not confirm("continue?"):
            exit(1)

        rich.print(f"[cyan]ALL files listed below will be deleted from the fileserver AND from the database for sensor '{sensor}'")
        if not confirm():
            exit(1)

        query = f"""
                select "ID", "RESULT_STRING" from "OBSERVATIONS" where
                "DATASTREAM_ID" in (
                    select "ID" from "DATASTREAMS" where
                        "SENSOR_ID" = (select "ID" from "SENSORS" where "NAME" = '{sensor}')
                        and "PROPERTIES"->>'dataType' = 'files'
                    )
                and  "RESULT_STRING" in {sql_list(df2['value'].to_list())} ;"""

        db_entries = db.dataframe_from_query(query)
        rich.print(db_entries)
        query = query.replace('select "ID", "RESULT_STRING"', 'delete')
        if not confirm("[yellow]Do you want to delete ALL database entries above?"):
            exit(1)
        db.exec_query(query, fetch=False)

        rich.print(f"[green]{len(df2['value'])} DB entries deleted successfully")


        files = [f.replace(baseurl, basepath) for f in df2["value"]]
        files = " ".join(files)
        os.system(f"ssh {host} ls -l {files}")

        if not confirm("[yellow]Do you want to delete ALL files form filesysetm?"):

            r = os.system(f"ssh {host} rm {files}")
            if r == 0:
                rich.print(f"[green]{len(df2['value'])} files deleted successfully")
            else:
                rich.print(f"[red]Error deleting")
        else:
            rich.print("Files maintained")

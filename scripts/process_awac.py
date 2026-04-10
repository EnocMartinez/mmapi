#!/usr/bin/env python3
"""

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 3/12/23
"""

from argparse import ArgumentParser
import pandas as pd
import rich
from rich.progress import  Progress
import numpy as np
from mmm.data_manipulation import open_csv

ignore_std = ["CDIR"]


def process_awac(input_file: str, output: str, std: bool = False) -> str:
    try:
        df = open_csv(input_file, time_format="%Y-%m-%d %H:%M:%S%z")
    except ValueError as e:
        rich.print(str(e))
        rich.print("[red]Could not open dataset!!!")
        exit(-1)

    colnames = df.columns

    # extract depths
    depths = [v.split("_")[1].replace("m", "") for v in colnames]
    depths = np.unique(depths)
    depths = depths.astype(float).round(2)  # Use only two decimals in floats
    depths = sorted(depths)

    # extract varnames
    varnames = [v.split("_")[0] for v in colnames]
    varnames = np.unique(varnames)

    datadict = {"timestamp": [], "depth": []}
    # initialize variables
    for v in varnames:
        datadict[v] = []  # init empty list
        datadict[v + "_qc"] = []  # init empty list
        if std and v[:4] not in ignore_std:
            datadict[v + "_std"] = []  # init empty list
    with Progress() as progress:
        task = progress.add_task("processing rows", total=len(df))
        for timestamp, row in df.iterrows():
            progress.advance(task, 1)
            for d in depths:
                datadict["timestamp"].append(timestamp)
                datadict["depth"].append(d)
                for v in varnames:
                    source = f"{v}_{int(d)}m"
                    source_qc = f"{v}_{int(d)}m_qc"
                    source_std = f"{v}_{int(d)}m_std"
                    datadict[v].append(row[source])

                    qc = row[source_qc]
                    if not np.isnan(qc):
                        qc = row[source_qc].astype(int)
                    datadict[v + "_qc"].append(qc)
                    vstd = v + "_std"
                    if std:
                        if v[:4] in ignore_std:
                            pass
                        else:
                            datadict[vstd].append(row[source_std])

    newdf = pd.DataFrame(datadict)
    newdf["timestamp"] = newdf["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%Sz")
    newdf = newdf.sort_index()
    if "CDIR_std" in newdf.columns:
        del newdf["CDIR_std"]
    rich.print(newdf)
    rich.print(f"Saving to CSV {output}...", end="")
    newdf.to_csv(output, index=False)
    rich.print("[green]done!")


if __name__ == "__main__":
    argparser = ArgumentParser()
    argparser.add_argument("input", type=str, help="input file", default="")
    argparser.add_argument("output", type=str, help="output file", default="")
    argparser.add_argument("--std", action="store_true", help="standard deviation")
    args = argparser.parse_args()

    process_awac(args.input, args.output, args.std)
    
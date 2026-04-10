"""
Processes *.raw data from a SBE37SI sensor and saves it as a CSV file. Expects the following headerless format:

      13.5662,  4.47903,   19.491,  38.0988, 1506.045, 02 Mar 2025, 00:00:13, 305517

    which is:
        temp,conductivity,pressure,salinity,sound velocity, date, time, count

"""


from argparse import ArgumentParser
import os
import rich
import pandas as pd
import sys

try:
    from mmm.parallelism import multiprocess
except ModuleNotFoundError:
    # Get the directory of the current script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Get the parent directory (project root)
    parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
    # Add the parent directory to the sys.path
    sys.path.insert(0, parent_dir)
    from mmm.parallelism import multiprocess

def process_sbe37_raw_file(file) -> pd.DataFrame:
    data = {
        "timestamp": [],
        "TEMP": [],
        "CNDC": [],
        "PRES": [],
        "PSAL": [],
        "SVEL": []
    }
    try:
        with open(file, encoding="utf8") as f:
            lines = f.readlines()
    except UnicodeError:
        rich.print(f"[red]Can't process file {file}")
        return pd.DataFrame()

    for i, line in enumerate(lines):
        try:
            line = line.strip()
            temp, cndc, pres, psal, svel, date, time, count = line.split(",")
            temp = float(temp)
            cndc = float(cndc)
            pres = float(pres)
            psal = float(psal)
            svel = float(svel)
            timestamp = pd.to_datetime(f"{date.lstrip()} {time.lstrip()}", format="%d %b %Y %H:%M:%S", utc=True)


            data["timestamp"].append(timestamp)
            data["TEMP"].append(temp)
            data["CNDC"].append(cndc)
            data["PRES"].append(pres)
            data["PSAL"].append(psal)
            data["SVEL"].append(svel)

        except ValueError as e:
            rich.print(e)
            rich.print(f"[red]Error in {file} line {i}")

    df = pd.DataFrame(data)
    df = df.set_index("timestamp").sort_index(ascending=True)
    return df


if __name__ == "__main__":
    # Adding command line options #
    argparser = ArgumentParser()
    argparser.add_argument("input", help="Folder containing of files to be merged")
    argparser.add_argument("output", help="Output file", type=str)
    args = argparser.parse_args()

    assert os.path.isdir(args.input), "Expected directory"

    files = [os.path.join(args.input, f) for f in os.listdir(args.input) if f.endswith(".raw")]
    files = sorted(files)
    arguments = [(file,) for file in files]
    dataframes = multiprocess(arguments, process_sbe37_raw_file)
    print("Merging dataframes... ")
    df = pd.concat(dataframes)
    df = df.sort_index()

    start = pd.Timestamp("2020-01-01T00:00:00Z", tz="utc")
    end = pd.Timestamp.utcnow()

    print(start)
    print(end)
    df = df[start:end]
    print(df)

    df = df.dropna(how="any")
    print(df)

    df = df[df["PRES"] > 15]
    print(df)

    df.to_csv(args.output)

    rich.print("[green]Done!")









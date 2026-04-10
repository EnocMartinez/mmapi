import rich
import os
import pandas as pd
from mmm.common import file_list
from mmm.data_manipulation import open_csv

folder = "AWAC"

files = file_list(folder)
wave_file = [f for f in files if f.endswith(".wap")]
curr_file = [f for f in files if f.endswith(".wpa")]

curr = {
    "timestamp": [], "depth": [], "CSPD": [], "CDIR": [], "UCUR": [], "VCUR": [], "ZCUR": [], "BEAM1": [],
    "BEAM2": [], "BEAM3": []
}

technical = {
    "timestamp": [], "ERRC": [], "STAT": [], "BATT": [], "SVEL": [], "HEAD": [], "PTCH": [], "ROLL": [], "PRES": [],
    "TEMP": [], "AIN1": [], "AIN2": []
}

waves = {
    "timestamp": [], "VHM0": [], "VAVH": [], "VH110": [], "VZMX": [], "VTM02": [], "VTPK": [],   "VTZA": [], "VPED": [], "VPSP": [],
    "VMDR": [], "UNDX": []
}

def process_current_file(file):
    rich.print(f"Processing {file}")

    with open(file) as f:
        lines = f.readlines()
        for line in lines:
            line = line.replace("\n", "")
            splits = [s for s in line.split(" ") if s]
            if len(splits) == 19: # close previous file and create new
                month, day, year, hour, minute, second, errc, stat, batt, svel, head, ptch, roll, pres, temp, ain1, ain2, beams, cells = splits
                timestamp = pd.Timestamp(f"{year}-{month}-{day}T{hour}:{minute}:{second}Z")
                technical["timestamp"].append(timestamp)
                technical["ERRC"].append(float(errc))
                technical["STAT"].append(float(stat))
                technical["BATT"].append(float(batt))
                technical["SVEL"].append(float(svel))
                technical["HEAD"].append(float(head))
                technical["PTCH"].append(float(ptch))
                technical["ROLL"].append(float(roll))
                technical["PRES"].append(float(pres))
                technical["TEMP"].append(float(temp))
                technical["AIN1"].append(float(ain1))
                technical["AIN2"].append(float(ain2))

            elif len(splits) == 10:
                cellnum, distance, cspd, cdir, ucur, vcur, zcur, beam1, beam2, beam3 = splits
                curr["timestamp"].append(timestamp)
                curr["depth"].append(20 - int(cellnum))
                curr["CSPD"].append(float(cspd))
                curr["CDIR"].append(float(cdir))
                curr["UCUR"].append(float(ucur))
                curr["VCUR"].append(float(vcur))
                curr["ZCUR"].append(float(zcur))
                curr["BEAM1"].append(float(beam1))
                curr["BEAM2"].append(float(beam2))
                curr["BEAM3"].append(float(beam3))


def process_wave_file(file):
    rich.print(f"Processing {file}")
    with open(file) as f:
        lines = f.readlines()
        for line in lines:
            line = line.replace("\n", "")
            splits = [s for s in line.split(" ") if s]
            if len(splits) == 23: # close previous file and create new

                month, day, year, hour, minute, second, VHM0, VAVH, VH110, VZMX, VTM02, VTPK, VTZA, VPED, VPSP, VMDR, UNDX, mean_pres, no_detects, bad_detects, current_speed, current_dir, error_code = splits
                timestamp = pd.Timestamp(f"{year}-{month}-{day}T{hour}:{minute}:{second}Z")
                waves["timestamp"].append(timestamp)
                waves["VHM0"].append(float(VHM0))
                waves["VAVH"].append(float(VAVH))
                waves["VH110"].append(float(VH110))
                waves["VZMX"].append(float(VZMX))
                waves["VTM02"].append(float(VTM02))
                waves["VTPK"].append(float(VTPK))
                waves["VTZA"].append(float(VTZA))
                waves["VPED"].append(float(VPED))
                waves["VPSP"].append(float(VPSP))
                waves["VMDR"].append(float(VMDR))
                waves["UNDX"].append(float(UNDX))

#
# for f in wave_file:
#     process_wave_file(f)
#
# df = pd.DataFrame(waves)

df = pd.read_csv("awac_waves_qc.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.set_index("timestamp")
df = df.sort_index()
df = df[~df.index.duplicated(keep=False)]
df = df.reset_index()
df.to_csv("awac_waves.csv", index=False)

df = pd.read_csv("awac_waves.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.set_index("timestamp")
df = df.sort_index()

times = pd.read_csv("awac_times_technical.csv")
times["timestamp"] = pd.to_datetime(times["timestamp"])
times = times.set_index("timestamp")

print("==== before")
print(df)
df = df.loc[df.index.difference(times.index)]
print("==== after")
df = df[~df.index.duplicated(keep=False)]

print(df)

df.to_csv("awac_waves_nodups.csv")


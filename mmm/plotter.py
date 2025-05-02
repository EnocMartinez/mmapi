"""
Automatically generate plot for timeseries and trajectories

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 27/10/23
"""
import rich
import yaml
import math
from mmm.data_manipulation import open_csv
from mmm import MetadataCollector, init_metadata_collector
import pandas as pd
import pandas as pd
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import emso_metadata_harmonizer as emh
import numpy as np

qc_flags = {
    "good": 1,
    "not_applied": 2,
    "suspicious": 3,
    "bad": 4,
    "missing": 9
}

__qc_colors = {
    "good": "green",
    "not_applied": "grey",
    "suspicious": "gold",
    "bad": "red",
    "missing": "black"
}
__qc_sizes = {
    "good": 0.5,
    "not_applied": 1,
    "suspicious": 4,
    "bad": 4,
    "missing": 4
}

def plot_trajectory(df, dataset_id):
    # Example DataFrame with latitude and longitude
    data = {
        "latitude": df["LATITUDE"],
        "longitude": df["LONGITUDE"],
    }
    df = pd.DataFrame(data)

    # Example DataFrame with latitude and longitude

    df = pd.DataFrame(data)

    # Create a plot with Cartopy
    fig = plt.figure(figsize=(10, 8))
    ax = plt.axes(projection=ccrs.PlateCarree())  # Use PlateCarree for simple lat/lon projections
    # ax.set_extent([-130, -70, 20, 50], crs=ccrs.PlateCarree())  # Adjust extent to show relevant region

    # Add features to the map
    ax.add_feature(cfeature.COASTLINE, linewidth=1)
    ax.add_feature(cfeature.BORDERS, linestyle=':')
    ax.add_feature(cfeature.STATES, linestyle='--', edgecolor='gray')
    ax.add_feature(cfeature.LAND, edgecolor='black', facecolor='lightgray')
    ax.add_feature(cfeature.OCEAN, facecolor='lightblue')

    # Plot the trajectory
    ax.plot(df['longitude'], df['latitude'], color='blue', marker='.', transform=ccrs.PlateCarree(),
            label=f'trajectory')

    # Add labels
    # ax.set_title('Trajectory Map', fontsize=16)
    ax.legend()


def plot_timeseries(df):
    varlist = [c for c in df.columns if not c.endswith("_QC") and not c.endswith("_U") and not c.endswith("_STD")]
    varlist = [c for c in varlist if c not in ["LATITUDE", "LONGITUDE", "DEPTH", "SENSOR_ID", "TIME"]]

    # Determine number of rows and columns for the subplot grid
    num_vars = len(varlist)
    ncols = 2
    nrows = math.ceil(num_vars / ncols)  # Calculate the number of rows needed
    fig, axes = plt.subplots(nrows, ncols, figsize=(12, 3 * nrows), sharex=True)

    # Flatten axes array to make it easier to iterate
    axes = axes.flatten()

    dataframe = df
    # Plot each variable in a separate subplot
    for i, column in enumerate(varlist):
        qc_column = column + "_QC"
        ax = axes[i]
        if qc_column:
            df = dataframe
            for flag in qc_flags.keys():

                df = df[[column, qc_column]]
                df = df.dropna(how="any")
                df[qc_column] = df[qc_column].fillna(0)
                df[qc_column] = df[qc_column].astype(np.int8)
                df_flag = df[df[qc_column] == qc_flags[flag]]

                flag_count = len(df_flag.index.values)
                if flag_count <= 0:
                    continue
                percent = 100 * flag_count / len(df.index.values)
                label = column + " " + flag + " (%.02f%%)" % percent
                ax.scatter(x=df_flag.index.values, y=df_flag[column].values, color=__qc_colors[flag], marker='o',
                           s=__qc_sizes[flag], label=label)
                ax.tick_params("x", rotation=45)
                ax.legend()

        else:
            ax.scatter(df.index, df[column], label=column, color=__qc_colors[qc_column["not_applied"]], marker=".", size=2)
            ax.set_ylabel(column.capitalize(), fontsize=12)
            ax.tick_params("x", rotation=45)
            ax.legend()

    # Turn off unused subplots (for odd numbers of variables)
    for j in range(num_vars, len(axes)):
        axes[j].axis("off")

    # Adjust layout
    plt.tight_layout()


def open_data_file(filename):
    if filename.endswith(".csv"):
        df = pd.read_csv(filename)
        df["TIME"] = pd.to_datetime(df["TIME"])
        df = df.set_index("TIME")
    elif filename.endswith(".nc"):
        wf = emh.metadata.dataset.load_nc_data(filename)
        df = wf.data
        df = df.set_index("TIME")
    return df

def auto_plotter(filename, dataset_id):
    """
    creates an automatic plot with the data inside the filename
    :param filename:
    :return:
    """
    df = open_data_file(filename)
    if "LATITUDE" in df.columns and "LONGITUDE" in df.columns:  # make sure that we have lat and lon
        if len(np.unique(df["LATITUDE"].values)) > 1 or len(np.unique(df["LONGITUDE"].values)) > 1:
            # this is a trajectory!
            plot_trajectory(df, dataset_id)
        else:
            # fixed-point timeseries
            plot_timeseries(df)
    else:
        plot_timeseries(df)

    splits = filename.split(".")
    new_filename = ".".join(splits) + ".png"
    print(new_filename)
    plt.savefig(new_filename)
    return new_filename



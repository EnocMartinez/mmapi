"""
Automatically generate plot for timeseries and trajectories

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 27/10/23
"""
import os
from os.path import exists

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
import zipfile
import random
from PIL import Image


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

def plot_trajectory(df, dataset):
    # Example DataFrame with latitude and longitude
    data = {
        "latitude": df["LATITUDE"],
        "longitude": df["LONGITUDE"],
    }
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


    station_id = dataset["@stations"]
    ax.set_title(dataset["title"], fontsize=16)

    # Plot the trajectory
    ax.plot(df['longitude'], df['latitude'], color='blue', marker='.', transform=ccrs.PlateCarree(),
            label=f'{station_id} trajectory')

    # Add labels

    gl = ax.gridlines(draw_labels=True, linewidth=0.5, color='gray', alpha=0.5, linestyle='--')
    gl.top_labels = False  # Disable labels on top
    gl.right_labels = False  # Disable labels on right
    gl.xlabel_style = {'size': 10}
    gl.ylabel_style = {'size': 10}
    ax.legend()
    # lat_margin = abs(10000*(df["longitude"].max() - df["longitude"].min()))
    # lon_margin = abs(10000 * (df["longitude"].max() - df["longitude"].min()))
    # ax.set_xlim(df["longitude"].min() - lon_margin, df["longitude"].max() + lon_margin)
    # ax.set_ylim(df["latitude"].min() - lat_margin, df["latitude"].max() + lat_margin)
    # ax.set_xlim(df["longitude"].min(), df["longitude"].max())
    # ax.set_ylim(df["latitude"].min(), df["latitude"].max())

    plt.tight_layout()
    return plt


def plot_timeseries(df):
    df = df.dropna(axis=1, how='all')  # drop columns with ALL nans
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
    return plt


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

def auto_plotter(filename: str, resource_id: str, dataset: dict):
    """
    creates an automatic plot with the data inside the filename
    """
    plot_filename = dataset["#id"] + "_" + resource_id + ".png"

    if filename.endswith(".zip"):
        return mosaic_from_zip(filename, plot_filename)

    df = open_data_file(filename)
    if "LATITUDE" in df.columns and "LONGITUDE" in df.columns:  # make sure that we have lat and lon
        if len(np.unique(df["LATITUDE"].values)) > 1 or len(np.unique(df["LONGITUDE"].values)) > 1:
            # this is a trajectory!
            plt = plot_trajectory(df, dataset)
        else:
            # fixed-point timeseries
            plt = plot_timeseries(df)
    else:
        plt = plot_timeseries(df)

    plt.savefig(plot_filename, dpi=300)
    return plot_filename



def mosaic_from_zip(filename, mosaic_filename):
    # Extract all contents to a directory
    folder = ".delete_me"
    os.makedirs(folder, exist_ok=True)
    with zipfile.ZipFile(filename, 'r') as zip_ref:
        files = zip_ref.namelist()
        pictures = [f for f in files if f.split(".")[-1].lower() in ["jpeg", "jpg", "png"]]
        if len(pictures) < 4:
            raise ValueError(f"Cannot create mosaic from zip file with {len(pictures)} pictures inside!")
        # Select 4 random pictures
        pictures = random.sample(pictures, 4)

        for pic in pictures:
            zip_ref.extract(pic, folder)

        pictures = [os.path.join(folder, p) for p in pictures]

    # Now create a mosaic 4x4 mosaic with the size of the first selected picture

    # Resize images to fit within a box while preserving aspect ratio
    img1, img2, img3, img4 = [Image.open(p) for p in pictures]

    w = int(img1.width / 4)
    h = int(img1.height / 4)
    max_size = (w, h)

    img1.thumbnail(max_size, Image.Resampling.LANCZOS)
    img2.thumbnail(max_size, Image.Resampling.LANCZOS)
    img3.thumbnail(max_size, Image.Resampling.LANCZOS)
    img4.thumbnail(max_size, Image.Resampling.LANCZOS)

    # Create a new blank image for the mosaic
    mosaic = Image.new('RGB', (2*w + 10, 2*h + 10), 'white')

    # Paste images centered in each quadrant
    def paste_centered(mosaic, img, x_offset, y_offset, x_size, y_size):
        # Calculate position to center the image
        x = x_offset + (x_size - img.width) // 2
        y = y_offset + (y_size - img.height) // 2
        mosaic.paste(img, (x, y))

    paste_centered(mosaic, img1, 0, 0, w+10, h+10)  # Top-left
    paste_centered(mosaic, img2, w+10, 0, w+10, h+10)  # Top-right
    paste_centered(mosaic, img3, 0, h+10, w+10, h+10)  # Bottom-left
    paste_centered(mosaic, img4, w+10, h+10, w+10, h+10)  # Bottom-right

    mosaic.save(mosaic_filename)
    return mosaic_filename

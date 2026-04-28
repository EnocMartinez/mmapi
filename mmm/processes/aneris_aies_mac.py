#!/usr/bin/env python3
"""
Register the metadata for inference data coming from AI algorithms. Currently, this is limited to YOLOv8 for fish
detections in pictures

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 19/2/24
"""

from mmm import MetadataCollector
import rich
from mmm.common import load_fields_from_dict, assert_type
from mmm.data_sources.api import Datastream
from mmm.metadata_collector import get_sensor_deployments
import numpy as np
import logging


def get_sensor_process_parameters(process_id, sensor)->dict:
    """
    Returns the parameters for a specific process of a sensor
    :param process_id:
    :param sensor:
    :return:
    """
    for p in sensor["processes"]:
        if p["@processes"] == process_id:
            return p["parameters"]
    raise LookupError(f"process {process_id} not found in sensor {sensor['name']}")

def aneris_aies_mac_process_20260414(sensor: dict, process: dict, mc: MetadataCollector, obs_props_ids: dict, sensor_id: int,
                      thing_id: int, foi_id: int, url: str, log: logging.Logger, update=True):
    """
    Registers the Datastreams for Object Detection inference. The output is expected to be an integer number of
    detections.
    """
    assert_type(log, logging.Logger)
    __required_fields = ["variableNames", "name"]
    for k in __required_fields:
        if k not in process.keys():
            log.error(f"[red]ERROR, expected key {k} in inference configuration")
    deployments = get_sensor_deployments(mc, sensor["#id"])
    processed_stations = []
    for dep in deployments:
        station = dep["station"]
        time = dep["start"]
        if station in processed_stations:
            # Already processed
            continue
        sensor_name = sensor["#id"]
        log.info(f"Registering aneris_aies_mac_process_20260414 Datastreams for {sensor_name}")

        obs_prop_id = obs_props_ids["FATX"]
        units_doc = mc.get_document("units", "dimensionless")

        process_id = process["#id"]

        name = f"{station}:{sensor_name}:fish_abundance:{process_id}"
        description = f"Fish abundance detected from the pictures from camera {sensor_name} at {station}"
        properties = {
            "fullData": True,
            "dataType": "json",
            "algorithm": "aneris_aies_mac_process_20260414",
            "results": {
                "UCIQE": "Underwater Color Image Quality Evaluator: Evaluates color quality. (float)",
                "UICM": "Underwater Image Colorfulness Measure: Measures color richness. (float)",
                "UISM": "Underwater Image Sharpness Measure: Measures sharpness. (float)",
                "UIConM": "Underwater Image Contrast Measure: Measures contrast. (float)",
                "UIQM": "Underwater Image Quality Measure: Overall quality score. (float)",
                "detections": {
                    "Species": "Detected species",
                    "Confidence": "Confidence level of detection (between 0 and 1). (float)",
                    "box": "Bounding box coordinates `[x1, y1, x2, y2]`. (list)",
                    "area": "Area of the segmented region (in pixels). (float)",
                    "shapefactorE": "Shape factor based on ellipse (width/length ratio). (float)",
                    "length": "Length of the segmented region (in pixels). (float)",
                    "width": "Width of the segmented region (in pixels). (float)",
                    "shapefactorF": "Shape factor based on perimeter and area. (float)",
                    "contour": "Coordinates of contour points of the segmented region. (list)",
                    "efd_1 to efd_n": "Elliptic Fourier Descriptors: Coefficients describing the contour shape. n=29 (float)",
                    "mean_color_R": "Mean value of the **Red** channel in the segmented region. (float)",
                    "mean_color_G": "Mean value of the **Green** channel in the segmented region. (float)",
                    "mean_color_B": "Mean value of the **Blue** channel in the segmented region. (float)",
                    "std_color_R": "Standard deviation of the **Red** channel in the segmented region. (float)",
                    "std_color_G": "Standard deviation of the **Green** channel in the segmented region. (float)",
                    "std_color_B": "Standard deviation of the **Blue** channel in the segmented region. (float)",
                    "mean_H": "Mean **Hue** (H) in the HSV color space. (float)",
                    "mean_S": "Mean **Saturation** (S) in the HSV color space. (float)",
                    "mean_V": "Mean **Value** (V) in the HSV color space. (float)",
                    "dominant_color_RGB": "Dominant color in the segmented region (format `[R, G, B]`). (list)",
                    "dominant_hue": "Dominant hue (H) of the dominant color (value between 0 and 179). (float)",
                    "mean_rgb_global": "Global mean of RGB channels in the segmented region. (float)",
                    "haralick_n": "Haralick Textures: Texture features (e.g., contrast, energy). n= from 1 to 13 (float)"
                  }
            }
        }


        ds_units = load_fields_from_dict(units_doc, ["name", "symbol", "definition"])
        ds = Datastream(name, description, ds_units, thing_id, obs_prop_id, sensor_id, properties=properties,
                        observation_type="OM_Observation")  # generic observation type, will be used to store json data
        ds.register(url, update=update, verbose=True)



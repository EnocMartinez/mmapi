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

def inference_process(sensor: dict, process: dict, mc: MetadataCollector, obs_props_ids: dict, sensor_id: int,
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
        log.info(f"Registering inference Datastreams for {sensor_name}")
        variables = mc.get_documents("variables")
        classes = {}  # key taxa name (standard_name),

        # If merge not defined, just leave it empty to avoid key exceptions
        if "merge" not in process:
            process["merge"] = {}

        # Get the name of all the classes
        class_names = process["variableNames"]

        # update with rename list
        for old, new in process["rename"].items():
            class_names.remove(old)
            class_names.append(new)
        class_names = list(np.unique(class_names)) # delete duplicates

        for detection_class in class_names:
            if detection_class in process["ignore"] :
                # Do not register as detections classes marked as ignore
                continue

            found = False
            for var in variables:
                if var["standard_name"] == detection_class:
                    classes[detection_class] = var
                    found = True
            if not found:
                raise ValueError(f"Inference class '{detection_class}' not found")


        # Now, let's register the datastreams
        # First a datastream where all the detections with probabilities will be generated
        obs_prop_id = obs_props_ids["FATX"]
        units_doc = mc.get_document("units", "dimensionless")

        process_id = process["#id"]

        name = f"{station}:{sensor_name}:fish_abundance:{process_id}"
        description = f"Fish abundance detected from the pictures from camera {sensor_name} at {station}"
        properties = {
            "fullData": True,
            "dataType": "json",
            "modelName": process_id,
            "weights": process["weights"],
            "algorithm": process["algorithm"],
            "results": {
                "taxa": "Taxa of the identified object",
                "confidence": "probability that an anchor box contains an object",
                "bounding_box_xyxy": "normalized bounding box as [xmin, ymin, xmax, ymax]"
            },
            # parameters to be passed to the actual algorithm
            "parameters": get_sensor_process_parameters(process_id, sensor),
            "classes": process["variableNames"], # class list
            "ignore": process["ignore"],  # classes to ignore
            "rename": process["rename"]    # key old name, value new name
        }

        ds_units = load_fields_from_dict(units_doc, ["name", "symbol", "definition"])
        ds = Datastream(name, description, ds_units, thing_id, obs_prop_id, sensor_id, properties=properties,
                        observation_type="OM_Observation")  # generic observation type, will be used to store json data
        ds.register(url, update=update, verbose=True)

        # Now register species one by one
        units_doc = mc.get_document("units", "dimensionless")
        for taxa_name, variable in classes.items():
            taxa_normalized = taxa_name.lower().replace(" ", "_").replace(".", "")
            name = f"{station}:{sensor_name}:{taxa_normalized}:{process_id}:detections"
            description = f"Detections of {taxa_name} in pictures from camera {sensor_name} at {station}"
            properties = {
                "fullData": True,
                "dataType": "detections",
                "modelName": process_id,
                "algorithm": process["algorithm"],
                "weights": process["weights"],
                # "trainingConfig": process["trainingConfig"],
                # "trainingData": process["trainingData"],
                "standardName": taxa_name,
                "defaultFeatureOfInterest": foi_id,
            }
            obs_prop_id = obs_props_ids[variable["#id"]]
            ds_units = load_fields_from_dict(units_doc, ["name", "symbol", "definition"])
            ds = Datastream(name, description, ds_units, thing_id, obs_prop_id, sensor_id, properties=properties,
                            observation_type="OM_CountObservation")
            ds.register(url, update=update, verbose=True)

        processed_stations.append(station)

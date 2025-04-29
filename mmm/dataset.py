#!/usr/bin/env python3
"""
Simple structure that stores dataset information

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 12/6/24
"""
import logging
from datetime import datetime

import jsonschema
import pandas as pd
import os
from .schemas import dataset_exporter_conf, mmm_schemas
from .fileserver import FileServer, send_file
from emso_metadata_harmonizer import erddap_config
import time
from mmm.common import validate_schema, LoggerSuperclass, CYN, GRN, assert_type, run_over_ssh, run_subprocess
import logging


class DatasetObject(LoggerSuperclass):
    def __init__(self, conf: dict, filename: str, service_name: str, tstart: pd.Timestamp | str, tend: pd.Timestamp | str,
                 fmt: str, log: logging.Logger):
        """
        This object contains all the metadata related to a dataset (or data file) and provides methods to deliver,
        update it.

        :param conf: Dataset configuration dict, must be compliant with dataset schema
        :param filename: file that contains the actual data
        :param service_name: the name of the service used to export the data. This service must be listed in conf
        :param tstart: time start
        :param tend: time end
        :param fmt: export format (by default format in conf)
        """
        self.log = log
        LoggerSuperclass.__init__(self, log, "Dataset", colour=CYN)

        assert_type(conf, dict)
        validate_schema(conf, mmm_schemas["datasets"], [])

        assert os.path.isfile(filename), f"file '{filename}' does not exist!"

        # Convert strings
        if type(tstart) is str:
            tstart = pd.Timestamp(tstart)
        if type(tend) is str:
            tend = pd.Timestamp(tend)

        assert type(tstart) is pd.Timestamp
        assert type(tend) is pd.Timestamp

        self.info(f"Creating dataset from {tstart} to {tend}")

        self.filename = filename
        self.conf = conf
        self.dataset_id = conf["#id"]
        self.fmt = fmt
        self.tstart = tstart
        self.tend = tend
        self.url = ""

        self.ctime = pd.Timestamp(os.path.getctime(filename))
        self.size = os.path.getsize(filename)

        tfmt = "%Y-%m-%d"
        basename = os.path.basename(filename)
        self.data_object_id = basename + "_" + self.tstart_str(tfmt) + "_" + self.tstart_str(tfmt) + "_" + fmt
        self.service_name = service_name

        # Store the configuration for all export services, we don't know yet to which service the data object
        # will be delivered.
        config = conf["export"][service_name]
        self.exporter = DataExporter(config, self.dataset_id, self.log)

        self.delivered = False  # will be set to True once the data object has been sent

        self.erddap_configured = False
        self.erddap_dataset_id = ""

    def tstart_str(self, fmt="%Y-%m-%dT%H:%M:%SZ"):
        return self.tstart.strftime(fmt)

    def tend_str(self, fmt="%Y-%m-%dT%H:%M:%SZ"):
        return self.tend.strftime(fmt)

    def deliver(self, fileserver: FileServer = None):
        """
        Delivers a dataset to the export service as configured in __init__
        :param fileserver: FileServer to convert from filesystem tu public HTTP URL. If no URL is needed, leave it blank
        :return: URL (if fileserver is passed) or filesystem path
        """
        if fileserver:
            assert type(fileserver) is FileServer, f"expected type FileServer, got {type(fileserver)}"

        if self.delivered:
            raise ValueError("Dataset already delivered!")
        exporter = self.exporter
        self.url = exporter.deliver_dataset(self.filename, self.tstart, fileserver=fileserver)
        self.delivered = True
        return self.url

    def configure_erddap(self, datasets_xml, dataset_path):
        """
        Configure an ERDDAP dataset based on the dataset configuration and dataset NetCDF file. This only works for
        EMSO-compliant NetCDF files
        :param datasets_xml: path to the ERDDAPs datasets.xml file
        :param dataset_path: Path where the datasets will be stored. May differ from filesystem path since erddap is
                             dockerized.
        """
        if self.fmt != "netcdf":
            raise ValueError(f"Unimplemented ERDDAP configuration for dataset with type '{self.fmt}'")

        if self.service_name != "erddap":
            raise ValueError(f"ERDDAP exporter not configured for this dataset!")

        try:
            self.erddap_dataset_id = self.conf["identifier"]
        except KeyError:
            # If not set, use generic dataset_id
            self.erddap_dataset_id = self.dataset_id

        # configure erddap using the emso_metadata_harmonizer tool
        erddap_config(self.filename, self.erddap_dataset_id, dataset_path, datasets_xml_file=datasets_xml)
        self.erddap_configured = True

    def configure_erddap_remotely(self, datasets_xml, big_parent_directory="", erddap_uid=None, erddap_datasets_path="/datasets"):
        """
        This configures a remote erddap, the same as configure_erddap, but uses scp to get the datasets.xml file
        and then to send it back to the server hosting the erddap.
        :param datasets_xml: path to the ERDDAPs datasets.xml file
        :param dataset_path: Path where the datasets will be stored. May differ from filesystem path since erddap is
                             dockerized.
        :param big_parent_directory: ERDDAP's big parent directory, used to create a hard flag for auto-reload
        :param erddap_datasets_path: path where all the datasets are accessed from the ERDDAP's point of view. If ERDDAP
                                     is containerized this is the path within the container.
        :return:
        """

        # Step 1: Create a remote copy of datasets.xml
        self.info("Creating a remote copy of datasets.xml")
        t = datetime.now()
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        basename = "." + os.path.basename(datasets_xml).replace(".xml", "_") + now  + ".bckp"
        self.info(f"Creating a remote copy of datasets.xml -> {basename}")
        bckp_file = os.path.join(os.path.dirname(datasets_xml), basename)
        run_over_ssh(self.exporter.host, f"cp {datasets_xml} {bckp_file}")

        # Step 2: Download the datasets.xml to temp folder
        self.info("Downloading datasets.xml")
        local_datasets_xml = os.path.join("temp", os.path.basename(datasets_xml))
        os.makedirs("temp", exist_ok=True)
        run_subprocess(f"scp {self.exporter.host}:{datasets_xml} {local_datasets_xml}", fail_exit=True)

        # Step 3: call the erddap_config from emso_metadata_harmonizer tool
        self.info("Configuring datasets.xml with emso_metadata_harmonizer")
        self.configure_erddap(local_datasets_xml, os.path.join(erddap_datasets_path, self.dataset_id))

        # Step 4: send back the datasets.xml to the server
        self.info("Sending back the datasets.xml")
        run_subprocess(f"scp {local_datasets_xml} {self.exporter.host}:{datasets_xml}", fail_exit=True)

        # Step 5: force dataset reload remotely
        if big_parent_directory and erddap_uid:
            remote_dataset_hard_flag = os.path.join(big_parent_directory, "hardFlag", self.dataset_id)
            # Brute force approach!  touch file as another user
            cmd = f"sudo -u \\#{erddap_uid} touch {remote_dataset_hard_flag}"
            run_over_ssh(self.exporter.host, cmd, fail_exit=True)
            self.info("Hard flag set remotely! dataset should be available soon")
        else:
            self.warning("ERDDAP big parent directory not set, ERDDAP must be reloaded manually!")

        # All done!


    def reload_erddap_dataset(self, big_parent_directory):
        """
        Creates a hardFlag to tell ERDDAP to reload a dataset ASAP
        :param big_parent_directory: ERDDAP's big parent directory
        :return: nothing
        """
        dataset_hard_flag = os.path.join(big_parent_directory, "hardFlag", self.dataset_id)
        with open(dataset_hard_flag, "w") as f:
            f.write("1")

        while os.path.exists(dataset_hard_flag):
            time.sleep(1)
            print("waiting for erddap to load the dataset...")

    def __repr__(self):
        """
        Return stats of the dataset object
        """
        string = ""
        string += f"=== DatasetObject {id(self)} ===\n"
        string += f"   DatasetID: {self.dataset_id}\n"
        string += f"       format: {self.fmt}\n"
        string += f"   time start: {self.tstart_str()}\n"
        string += f"     time end: {self.tend_str()}\n\n"
        string += f"-------- file --------\n"
        string += f"     filename: {self.filename}\n"
        string += f"         size: {self.size / (1024 * 1024):.02f} MB\n"
        string += f"          url: {self.url}\n"
        string += f"    delivered: {self.delivered}\n"
        string += f"-----------------------------------------"
        return string


class DataExporter(LoggerSuperclass):
    def __init__(self, conf, dataset_id, log):
        """
        Class to export datasets from a datasource and deliver them to the proper service
        """
        LoggerSuperclass.__init__(self, log, "Exporter", colour=GRN)
        jsonschema.validate(conf, schema=dataset_exporter_conf)
        self.period = conf["period"]
        self.host = conf["host"]
        self.format = conf["format"]

        if self.period.lower() == "none":
            self.path = conf["path"]
        elif dataset_id not in conf["path"]:
            self.path = os.path.join(conf["path"], dataset_id)
        else:
            self.path = conf["path"]

    def deliver_dataset(self, filename, timestamp: pd.Timestamp, fileserver: FileServer, url_required=True)->str:
        """
        Takes a dataset (already generated) and delivers it according to the dataset's configuration
        :param filename: dataset to deliver
        :param timestamp: timestamp used to generate folders
        """
        # TODO: This only works if FileServer and ERDDAP are on the same VM
        if fileserver:
            assert type(fileserver) is FileServer, "Expected FileServer object"
            # assert fileserver.host == self.host, f"DataExporter ({fileserver.host}) and FileServer ({self.host})have different hosts, not implemented "

        self.info(f"Delivering {os.path.basename(filename)} to {self.host}:{self.path}")

        assert type(filename) is str
        assert type(timestamp) is pd.Timestamp

        assert os.path.isfile(filename), f"Not a file: '{filename}"
        # First, construct the path
        path = self.path  # start with base path
        path = self.generate_path(path, self.period, timestamp)
        if fileserver and fileserver.host == self.host:
            result = fileserver.send_file(path, filename, indexed=url_required)
        else:
            result = send_file(filename, path, self.host)
        return result

    @staticmethod
    def generate_path(path, period, timestamp):
        if not period:
            return path
        # Now, let's check the file tree level
        tree = ""
        if period == "daily":
            tree = timestamp.strftime("%Y/%m")
        elif period == "monthly":
            tree = timestamp.strftime("%Y")
        elif period == "yearly":
            pass
        elif period == "none":
            pass
        else:
            raise ValueError("This should never happen, schema not honored!")
        if tree:
            path = os.path.join(path, tree)
        return path
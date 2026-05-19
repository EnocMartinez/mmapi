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
from .metadata_collector import MetadataCollector
from .schemas import mmm_schemas
from .fileserver import FileServer, send_file
from emso_metadata_harmonizer import erddap_config
import time
from mmm.common import validate_schema, LoggerSuperclass, CYN, GRN, assert_type, run_over_ssh, run_subprocess
import logging


class DatasetObject(LoggerSuperclass):
    def __init__(self, mc: MetadataCollector, fileserver: FileServer, conf: dict, filename: str, service_name: str, resource: dict,
                 tstart: pd.Timestamp, tend: pd.Timestamp, fmt: str, log: logging.Logger, delivered=False):
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

        assert_type(mc, MetadataCollector)
        assert_type(fileserver, FileServer)
        assert_type(conf, dict)
        assert_type(filename, str)
        assert_type(service_name, str)
        assert_type(resource, dict)
        assert_type(tstart, pd.Timestamp)
        assert_type(tend, pd.Timestamp)
        assert_type(fmt, str)
        assert(delivered, bool)

        init = time.time()
        validate_schema(conf, mmm_schemas["datasets"], [])
        self.debug(f"Validating schema took {1000*(time.time() - init):.01f} ms")

        self.mc = mc
        self.fileserver = fileserver
        self.conf = conf
        self.filename = filename
        self.service_name = service_name
        self.resource = resource
        self.tstart = tstart
        self.tend = tend
        self.fmt = fmt
        self.delivered = delivered  # will be set to True once the data object has been sent

        self.dataset_id = conf["#id"]
        self.resource_id = resource["id"]

        self.url = ""

        if delivered:
            # File should be available at destination
            self.debug("Ensuring that remote file exists")
            output = run_over_ssh(self.fileserver.host, f"ls -l --full-time {filename}")
            if not output:
                raise AssertionError(f"File {filename} not found in {self.fileserver.host}")
            self.delivered = True

            # Get the creation time remotely
            date, time_t, tz = output.split(" ")[5:8]
            time_str = f"{date}T{time_t}{tz}"
            self.ctime = pd.Timestamp(time_str)
            self.size = output.split(" ")[4]
        else:
            # File should be local
            assert os.path.isfile(filename), f"file '{filename}' does not exist!"
            self.ctime = pd.Timestamp(os.path.getctime(filename))
            self.size = os.path.getsize(filename)

        self.exporter = DataExporter(resource, self.dataset_id, self.fileserver, self.log)

    def tstart_str(self, fmt="%Y-%m-%dT%H:%M:%SZ"):
        return self.tstart.strftime(fmt)

    def tend_str(self, fmt="%Y-%m-%dT%H:%M:%SZ"):
        return self.tend.strftime(fmt)

    def deliver_and_register(self):
        """
        Delivers a dataset to the export service as configured in __init__
        :param fileserver: FileServer to convert from filesystem tu public HTTP URL. If no URL is needed, leave it blank
        :return: URL (if fileserver is passed) or filesystem path
        """

        if self.service_name == "local":
            self.warning("Ignoring register and deliver local dataset!")
            return self.filename

        if not self.delivered:
            required_url = False
            if self.service_name == "fileserver":
                # fileserver does require an url
                required_url = True
            path_or_url = self.exporter.deliver_dataset(self.filename, self.tstart, url_required=required_url)
            self.delivered = True
        else:
            path_or_url = self.fileserver.path2url(self.filename)
            self.info("Dataset already delivered!")

        if path_or_url.startswith("https://") or path_or_url.startswith("http://"):
            self.url = path_or_url


        self.debug(f"   dataset = {self.conf['#id']}")
        self.debug(f"   service_name = {self.service_name}")
        self.debug(f"   resource = {self.resource['id']}")
        self.debug(f"   format = {self.fmt}")
        self.debug(f"   tstart = {self.tstart}")
        self.debug(f"   tend = {self.tend}")

        if self.service_name == "fileserver":
            path = self.fileserver.url2path(self.url)
        else:
            path = ""  # for other services datasets are not reachable directly via URL

        host = self.resource["host"]
        self.mc.dataset_register(self.dataset_id, self.resource_id, self.service_name, self.fmt, self.tstart,
                                 self.tend, self.url, path, host)
        return path_or_url

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
        self.info(f"Integrating {self.filename} into {dataset_path}")
        erddap_config(self.filename, self.erddap_dataset_id, dataset_path, datasets_xml_file=datasets_xml, recursive=True)
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
            self.info("waiting for erddap to load the dataset...")

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
    def __init__(self, conf: dict, dataset_id: str, fileserver: FileServer, log: logging.Logger,):
        """
        Class to export datasets from a datasource and deliver them to the proper service
        """
        assert_type(conf, dict)
        assert_type(dataset_id, str)
        assert_type(fileserver, FileServer)
        assert_type(log, logging.Logger)

        LoggerSuperclass.__init__(self, log, "Exporter", colour=GRN)
        self.period = conf["period"]
        self.host = conf["host"]
        self.fmt = conf["format"]

        self.fileserver = fileserver

        if self.period.lower() == "none":
            self.path = conf["path"]
        elif dataset_id not in conf["path"]:
            self.path = os.path.join(conf["path"], dataset_id)
        else:
            self.path = conf["path"]

    def deliver_dataset(self, filename, timestamp: pd.Timestamp, url_required=True)->str:
        """
        Takes a dataset (already generated) and delivers it according to the dataset's configuration
        :param filename: dataset to deliver
        :param timestamp: timestamp used to generate folders
        """
        # TODO: This only works if FileServer and ERDDAP are on the same VM
        assert_type(filename, str)
        assert_type(timestamp, pd.Timestamp)
        assert_type(url_required, bool)
            # assert fileserver.host == self.host, f"DataExporter ({fileserver.host}) and FileServer ({self.host})have different hosts, not implemented "

        self.info(f"Delivering {os.path.basename(filename)} to {self.host}:{self.path}")

        assert type(filename) is str
        assert type(timestamp) is pd.Timestamp

        assert os.path.isfile(filename), f"Not a file: '{filename}"
        # First, construct the path
        path = self.path  # start with base path
        path = self.generate_path(path, self.period, timestamp)
        self.info(f"Sending file to {path}")
        if self.fileserver.host == self.host:
            self.debug(f"Using self.fileserver.send_file")
            result = self.fileserver.send_file(path, filename, indexed=url_required)
        else:
            self.debug(f"Using standalone send_file")
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
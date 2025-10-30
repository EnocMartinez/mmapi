#!/usr/bin/env python3
"""
This file implements the DataCollector, a class implementing generic data access and delivery.

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 30/11/22
"""
import datetime
import logging
import json

import emso_metadata_harmonizer.metadata
import numpy as np
import pandas as pd
import rich

from .darwin_core import DarwinCoreArchive
from .data_sources import SensorThingsApiDB
from .ckan import CkanClient
from .common import run_subprocess, check_url, run_over_ssh, LoggerSuperclass, assert_types, GRN, RST, \
    assert_type
from .data_manipulation import open_csv, merge_dataframes_by_columns, merge_dataframes, calculate_time_intervals
from .metadata_collector import MetadataCollector, init_metadata_collector
from .fileserver import FileServer
import os
import emso_metadata_harmonizer as mh
from mmm.dataset import DatasetObject
from mmm.schemas import dataset_exporter_formats, valid_dataset_services


def init_data_collector(secrets: dict, log: logging.Logger, mc: MetadataCollector = None,
                        sta: SensorThingsApiDB = None):
    return DataCollector(secrets, log, mc=mc, sta=sta)

def get_current_dateset_dates(period)-> (pd.Timestamp, pd.Timestamp):
    """
    Gets the dates for the current dataset, e.g. if monthly and now is 2024-12-12 start=2024-12-01 end=2025-01-01
    :param period:
    :return:  time_start, time_end
    """
    assert_type(period, str)
    # Convert from plain-text to pandas-like time period
    now = pd.Timestamp.utcnow()
    if period == "daily":
        time_start = now.floor("1D")
        time_end = time_start + pd.to_timedelta("1D")
    elif period == "monthly":
        time_start = now.floor("1D") + pd.offsets.MonthBegin(-1)
        time_end = time_start + pd.offsets.MonthBegin(1)

    elif period == "yearly":
        time_start = now.floor("1D") + pd.offsets.YearBegin(-1)
        time_end = time_start + pd.offsets.YearBegin(1)
    else:
        raise ValueError(f"Period not valied: '{period}'")
    return time_start, time_end


class DataCollector(LoggerSuperclass):
    """
    This class implements all methods to collect data from Databases and FileSystems, generate datasets and
    deliver them to the proper service.
    """

    def __init__(self, secrets: dict, log, mc: MetadataCollector = None, sta: SensorThingsApiDB = None):
        self.log = log
        LoggerSuperclass.__init__(self, log, "DC")
        if not mc:
            self.mc = init_metadata_collector(secrets, log=log)
        else:
            self.mc = mc

        if not sta:
            staconf = secrets["sensorthings"]
            self.sta = SensorThingsApiDB(staconf["host"], staconf["port"], staconf["database"], staconf["user"],
                                         staconf["password"], log, timescaledb=True)
        else:
            self.sta = sta
        self.fileserver = FileServer(secrets["fileserver"], log)

        try:
            self.ckan = CkanClient(mc, secrets["ckan"]["url"], secrets["ckan"]["api_key"], self.fileserver, log)
        except KeyError:
            self.warning("Could not initialize CKAN")
            self.ckan = None

        self.emso = None  # by default, do not initialize emso metadata

    def dataset_filename(self, dataset: dict, fmt: str, tstart: pd.Timestamp, tend: pd.Timestamp,
                         tmp_folder="temp") -> str:
        """
        Based on the configuration, generate the dataset filename
        """
        assert type(dataset) is dict
        assert type(tstart) is pd.Timestamp
        assert type(tend) is pd.Timestamp

        # convert from dataset_exporter_formats to real extensions
        extensions = {
            "netcdf": ".nc",
            "csv": ".csv",
            "zip": ".zip",
            "dwca": ".zip"
        }

        dataset_id = dataset["#id"]
        os.makedirs(tmp_folder, exist_ok=True)
        filename = dataset_id

        if tstart > pd.Timestamp("1900", tz="utc") and tstart.strftime("%Y%m%d") not in filename:
            filename +=  "_" + tstart.strftime("%Y%m%d") + "_" + tend.strftime("%Y%m%d")
        filename += extensions[fmt]
        return os.path.join(tmp_folder, filename)

    def get_dataset_type(self, dataset) -> (str, bool|None, str):
        """
        From the information of a dataset determines the dataType of the dataset, the fullData flag and the period.
        If fullData is defined it will be returned, if it's not defined a None value will be returned.
        If there's an average period will be returned in third place
        :return: (dataType, fullData, period)
        """
        assert_type(dataset, dict)
        data_type = dataset["dataType"]
        try:
            full_data = dataset["dataSourceOptions"]["fullData"]
        except KeyError as e:
            full_data = None
        try:
            period = dataset["dataSourceOptions"]["averagePeriod"]
        except KeyError:
            period = ""

        # make some checks

        if data_type in ["timeseries", "profiles"] and not full_data and not period:
            self.error(f"Error in dataset '{dataset['#id']}' timeseries or profiles dataset must be or fullData=True "
                       f"OR fullData=False and averagePeriod=<period>", exception=ValueError)

        return data_type, full_data, period

    def get_dataset_datastream_ids(self, dataset) -> list:
        """
        Return a list of datastream_ids that are relevant to a specific dataset
        :param dataset:  dataset configuration
        :return: list of IDs
        """
        assert_type(dataset, dict)
        data_type, full_data, average_period = self.get_dataset_type(dataset)

        sensors = dataset["@sensors"]
        station = dataset["@stations"]
        # Extract list of variables
        variables = []
        if "@variables" in dataset.keys():
            variables = dataset["@variables"]

        self.debug(f"Getting datastreams for station={station} sensors={sensors} and variables={variables}")
        # Construct the query
        sensors_str = ",".join([f"'{s}'" for s in sensors])
        q = f"""
        select "ID" from "DATASTREAMS" where
            "SENSOR_ID" in (select "ID" from "SENSORS" where "NAME" in ({sensors_str}))
            and "THING_ID" = (select "ID" from "THINGS" where "NAME" = '{station}')
            and "PROPERTIES"->>'dataType' = '{data_type}'
        """

        if type(full_data) != type(None):
            q += f"and \"PROPERTIES\"->>'fullData' = '{str(full_data).lower()}' \n"

        if average_period:
            q += f"and \"PROPERTIES\"->>'averagePeriod' = '{average_period}' \n"

        if variables:
            variables_str = ",".join([f"'{v}'" for v in variables])  # convert to 'VAR1', 'VAR2'...
            q += f'and "OBS_PROPERTY_ID" in (select "ID" from "OBS_PROPERTIES" where "NAME" in ({variables_str}))'

        q += ";"
        return self.sta.list_from_query(q, debug=False)

    def get_dataset_time_coverage(self, dataset: dict) -> (pd.Timestamp, pd.Timestamp):
        """
        Looks for the first timestamp where there is data from a dataset
        :param dataset: dataset configuration
        :return:
        """
        assert_type(dataset, dict)
        # Step 1: Get the list of datastreams that will be used in this dataset
        datastream_ids = self.get_dataset_datastream_ids(dataset)
        self.debug(f"dataset {dataset['#id']} uses the following datastream_ids = {datastream_ids}")
        data_type, full_data, avg_period = self.get_dataset_type(dataset)

        # If timeseries with no average
        if data_type in ["timeseries", "profiles", "detections"] and not avg_period:
            # Get the data directly from the hypertable. Use data_type as table name
            datastream_ids_str = ",".join([str(d) for d in datastream_ids])
            q = f"""
                select timestamp from {data_type} where datastream_id in ({datastream_ids_str})
                order by timestamp asc limit 1;
                """
            time_start = self.sta.value_from_query(q)
            time_end = self.sta.value_from_query(q.replace(" asc ", " desc "))
        else:
            # Get the data directly from the OBSERVATIONS table
            datastream_ids_str = ",".join([str(d) for d in datastream_ids])
            # Get the data directly from the table. Use data_type as table name
            q = f"""
                select "PHENOMENON_TIME_START" from "OBSERVATIONS" where "DATASTREAM_ID" in ({datastream_ids_str})
                    order by "PHENOMENON_TIME_START" asc limit 1;
                """
            time_start = self.sta.value_from_query(q, debug=True)
            time_end = self.sta.value_from_query(q.replace(" asc ", " desc "), debug=True)
        self.debug(f"First timestamp: {time_start}")
        self.debug(f"Last timestamp: {time_end}")
        return pd.Timestamp(time_start), pd.Timestamp(time_end)

    def generate_dataset(self, dataset: str | dict, service_name: str, time_start: pd.Timestamp|str = None,
                         time_end: pd.Timestamp|str = None, fmt: str = "", current=False, overwrite=False,
                         erddap_config=False, secrets={}, resources=[], deliver=True):
        """

        :param dataset: dataset identifier (as stored in metadata database
        :param service_name: name of the service where it will be exported
        :param time_start: first timestamp of the dataset
        :param time_end: last timestamp of the dataset
        :param fmt: overrisde configured format (e.g. create a CSV instead of a netcdf)
        :return:
        """
        assert_type(service_name, str)
        assert_types(dataset, [dict, str])
        assert_types(time_start, [pd.Timestamp, str, type(None)])
        assert_types(time_end, [pd.Timestamp, str, type(None)])
        assert service_name in valid_dataset_services, f"Service '{service_name}' not recognized!"

        if type(dataset) is str:
            conf = self.mc.get_document("datasets", dataset)
        else:
            conf = dataset
        dataset_id = conf["#id"]
        self.info(f"=====> Creating dataset {GRN}{dataset_id} {RST}from {time_start} to {time_end} <=====")

        assert service_name in conf["export"].keys(), f"Dataset {dataset_id} doesn't have export configuration for service '{service_name}'"

        if service_name == "ckan":
            # CKAN only points to the FileServer, no need to create it here
            if not self.ckan:
                self.error("CKAN not initialized!", exception=ValueError)
            return self.ckan.process_mmapi_dataset(conf, resources=resources)

        # Force the start and end in the current period, e.g. if "monthly" and now is 2024-12-12 the period
        # will be from 2024-12-01T00:00:00Z to 2025-01-01T00:00:00Z
        if current:
            try:
                period = conf["export"][service_name]["period"]
            except KeyError:
                raise ValueError(f"Could not access period for dataset_id='{dataset_id}' service='{service_name}'")

            time_start, time_end = get_current_dateset_dates(period)

        if not time_start and not time_end:
            # No time range supplied, trying to extract it from the dataset constraints
            try:
                trange = conf["constraints"]["timeRange"]
                time_start, time_end = trange.split("/")
            except KeyError:
                self.warning("Time range not defined! Look for first and last measures")
                time_start, time_end = self.get_dataset_time_coverage(conf)
                self.info(f"Getting data from {time_start} to {time_end}")
            except Exception as e:
                raise e

        if type(time_start) is str:
            time_start = pd.Timestamp(time_start)
        if type(time_end) is str:
            time_end = pd.Timestamp(time_end)

        if not time_start.tzinfo:
            time_start = pd.Timestamp.tz_localize(time_start, "utc")
        if not time_end.tzinfo:
            time_end = pd.Timestamp.tz_localize(time_end, "utc")

        if time_start and time_end and time_start > time_end:
            raise ValueError(f"Time start={time_start} greater than time end={time_end}")
        datasets = []

        self.info(f"Creating resource for service {GRN}{service_name}{RST} and dataset {GRN}{dataset_id}{RST}")
        for resource in conf["export"][service_name]["resources"]:
            resource_id = resource["id"]
            if resources and  resource_id not in resources:
                self.warning(f"Ignoring resource {resource_id}")
                continue
            else:
                self.info(f"Keeping resource {resource_id}")

            if resource["period"] == "none":
                d = self.generate_dataset_file(conf, service_name, resource, time_start, time_end, fmt=fmt, overwrite=overwrite)
                if d:
                    datasets.append(d)
            else:
                ds = self.generate_dataset_tree(conf, service_name, resource, time_start=time_start, time_end=time_end, fmt=fmt, overwrite=overwrite)
                datasets += ds

        register = False
        if service_name == "fileserver":
            register = True  # Only store reg

        if deliver:
            for dataset in datasets:
                if dataset:
                    # Deliver and register dataset in fileserver_datasets_registry
                    dataset.deliver_and_register(register=register)
        else:
            for dataset in datasets:
                self.info(f"Local file stored in {dataset.filename}")
                self.warning(f"Not registering in metadata database datasets in fileserver_dataset_registry!")

        if service_name == "erddap" and erddap_config:
            self.info("Trying to autoconfigure ERDDAP dataset (using last dataset)")
            dataset.configure_erddap_remotely(
                secrets["erddap"]["datasets_xml"],
                big_parent_directory=secrets["erddap"]["big_parent_directory"],
                erddap_uid=secrets["erddap"]["uid"]
            )
        # Avoid None datasets
        datasets = [d for d in datasets if d]
        return datasets

    def generate_dataset_tree(self,  dataset: dict, service_name: str, resource: dict, time_start: pd.Timestamp = None,
                              time_end: pd.Timestamp = None, fmt: str="", overwrite=False):
        assert_type(service_name, str)
        assert_types(dataset, [dict, str])
        assert_types(time_start, [pd.Timestamp, type(None)])
        assert_types(time_end, [pd.Timestamp, type(None)])
        conf = dataset

        if service_name not in conf["export"].keys():
            raise ValueError(f"Dataset {conf['#id']} doesn't have export configuration for service '{service_name}'")

        # check the dataset constraints
        if "constraints" in conf.keys() and "timeRange" in conf["constraints"].keys():
            ctime_start = pd.Timestamp(conf["constraints"]["timeRange"].split("/")[0])
            ctime_end = pd.Timestamp(conf["constraints"]["timeRange"].split("/")[1])

            if ctime_start > time_start:
                time_start = ctime_start
                self.warning(f"Dataset constraint Forces start time to {ctime_start}")
            if ctime_end < time_end:
                time_end = ctime_end
                self.warning(f"Dataset constraint Forces end time to {ctime_end}")
        else:
            # get the minimum and maximum time in the data
            coverage_start, coverage_end = self.get_dataset_time_coverage(dataset)

            if coverage_start > time_start:
                time_start = coverage_start
            if coverage_end < time_end:
                time_end = coverage_end

        self.info(f"Generating datasets from {time_start} to {time_end}")

        # Get the period
        intervals = calculate_time_intervals(time_start, time_end, resource["period"])

        datasets = []
        for tstart, tend in intervals:
            d = self.generate_dataset_file(conf, service_name, resource, tstart, tend, fmt=fmt, overwrite=overwrite)
            datasets.append(d)

        return datasets

    def generate_dataset_file(self, dataset: dict, service_name: str, resource: dict, time_start: pd.Timestamp,
                              time_end: pd.Timestamp, fmt: str = "", overwrite=False) -> DatasetObject:
        """
        Generates a dataset based on its configuration stored in Metadata DB
        :param dataset: #id of the dataset
        :param service_name: Name of the service that will be used to export the dataset
        :param time_start: dataset time start
        :param time_end: dataset time end
        :param fmt: overwrite original format (e.g. csv instead of netcdf)
        :return: Dataset file
        """
        assert_type(dataset, dict)
        assert_type(service_name, str)
        assert_type(resource, dict)
        assert_type(time_start, pd.Timestamp)
        assert_type(time_end, pd.Timestamp)
        conf = dataset

        self.debug(f"Generating dataset file from {time_start} to {time_end}")
        self.debug(f"Exporting to {service_name}")

        # Convert service ID to dict
        if service_name not in conf["export"].keys():
            raise ValueError(f"Dataset {conf['#id']} doesn't have export configuration for service '{service_name}'")

        # check the dataset constraints
        if "constraints" in conf.keys() and "timeRange" in conf["constraints"].keys():
            ctime_start = pd.Timestamp(conf["constraints"]["timeRange"].split("/")[0])
            ctime_end = pd.Timestamp(conf["constraints"]["timeRange"].split("/")[1])

            if ctime_start > time_start:
                time_start = ctime_start
                self.warning(f"[yellow]WARNING: Dataset constraint Forces start time to {ctime_start}")
            if ctime_end < time_end:
                time_end = ctime_end
                self.warning(f"[yellow]WARNING: Dataset constraint Forces end time to {ctime_end}")
        # Generate the dataset filename

        if not fmt:
            fmt = resource["format"]
        else:
            assert fmt in dataset_exporter_formats, f"Format '{fmt}' not allowed"

        # Check if the data already exists

        dataset_resource_id = resource["id"]

        if not overwrite and self.mc.dataset_resource_exists(dataset_resource_id):
            if overwrite:
                # Just throw a warning and continue
                self.warning(f"Dataset resource already exists: '{dataset_resource_id}', overwriting it!")
            else:
                self.error(f"Data resource already exists '{dataset_resource_id}', use the --overwrite flag to overwrite it")
                return
        else:
            self.info(f"Creating new dataset resource: {dataset_resource_id}")

        if fmt == "csv":
            filename, delivered = self.csv_from_sta(conf, resource, time_start, time_end)
        elif fmt == "netcdf":
            filename, delivered = self.netcdf_from_sta(conf, resource, time_start, time_end)
        elif fmt == "zip":
            filename, delivered = self.zip_from_filesystem(conf, resource, time_start, time_end, overwrite=overwrite)
        elif fmt == "dwca":
            filename, delivered = self.darwin_core_from_sta(conf, resource, time_start, time_end, overwrite=overwrite)
        else:
            raise ValueError(f"Unknown dataSource format '{fmt}'")

        if not filename:
            self.warning("No dataset created! Maybe it already exists?")
            return None

        obj = DatasetObject(self.mc, self.fileserver, conf, filename, service_name, resource, time_start, time_end, fmt, self.log, delivered=delivered)
        return obj

    def dataframe_from_sta(self, conf: dict, station: dict, sensor: dict, resource: dict, time_start: pd.Timestamp,
                           time_end: pd.Timestamp) -> pd.DataFrame:
        data_type = resource["dataType"]
        if data_type == "timeseries":
            return self.dataframe_from_sta_timeseries(conf, resource, station, sensor, time_start, time_end)
        elif data_type == "detections":
            df = self.dataframe_from_sta_detections(conf, resource, station, sensor, time_start, time_end)
            return df
        elif data_type == "profiles":
            return self.dataframe_from_sta_profiles(conf, station, sensor, time_start, time_end)
        elif data_type == "files":
            return self.dataframe_from_sta_observations(conf, station, sensor, time_start, time_end)
        elif data_type == "json":
            return self.dataframe_from_sta_json(conf, station, sensor, time_start, time_end)
        else:
            df = None
            self.error(f"Unimplemented data type {conf['dataType']}", exception=ValueError)
        return df

    def dataframe_from_sta_detections(self, conf: dict, resource: dict, station: dict, sensor: dict, time_start: pd.Timestamp,
                                      time_end: pd.Timestamp):
        """
        Return all the detections from a sensor
        """
        sensor_name = sensor["#id"]
        station_name = station["#id"]
        self.info(f"dataframe_from_sta_detections station {station_name} sensor {sensor_name}")
        time_periods = []
        if "constraints" in conf.keys() and "fieldOfView" in conf["constraints"]:
            # Process fieldOfView constraint
            deployments = self.mc.get_sensor_deployments(sensor)
            # Keep only periods where the camera was looking to the chosen fieldOfView
            for deployment in deployments:
                if "fieldOfView" not in deployment.keys():
                    self.error(f"Deployment has no fieldOfView! sensor={sensor_name} activity_id={deployment['id']}",
                               exception=ValueError)
                if conf["constraints"]["fieldOfView"]["@programmes"] == deployment["fieldOfView"]["@programmes"]:
                    if deployment["end"]:
                        end = deployment["end"]
                    else:
                        end = time_end
                    time_periods.append((deployment["start"], end))
                else:
                    # No deployment with the fieldOfView of interest!
                    pass
        else:
            time_periods = [(time_start, time_end)]

        self.info(f"Sensor {sensor_name} time periods {time_periods}")
        dataframes = []
        for time_start, time_end in time_periods:
            model_name = ""
            self.info(f"Getting detections with sensor={sensor_name} thing={station_name} from {time_start} to {time_end}")
            try:
                model_name = conf["constraints"]["@processes"]
                self.info(f"Using data from AI process {model_name}")
            except KeyError:
               self.error("AI process not defined in 'detections' dataset!", exception=ValueError)

            self.debug(f"getting datastream_id where dataType=json and sensor={sensor_name} and station={station_name}")
            # First get all the times where we have inferences. Let's assume that we only have one datastream that
            # matches data_type=json and model_name=<AI model>
            q = f""" select \"ID\" from \"DATASTREAMS\" 
                where 
                    \"SENSOR_ID\" = (select \"ID\" from \"SENSORS\" where \"NAME\" = '{sensor_name}')
                    and \"THING_ID\" = (select \"ID\" from \"THINGS\" where \"NAME\" = '{station_name}')
                    and \"PROPERTIES\"->>'dataType' = 'json'
                    and \"PROPERTIES\"->>'modelName' = '{model_name}'
                ;"""
            try:
                inference_datastream = self.sta.value_from_query(q)
            except LookupError:
                return pd.DataFrame()  # return empty dataframe

            self.debug(f"Pictures datastream_id = {inference_datastream}")
            df_inf = self.sta.dataframe_from_query(f'''
                select
                    "PHENOMENON_TIME_START" as timestamp,
                    "PARAMETERS"->>'sourceImage' as "SourceImage",
                    "PARAMETERS"->>'processedImage' as "ProcessedImage"                    
                from "OBSERVATIONS"
                where 
                    "DATASTREAM_ID" = {inference_datastream} and
                    "PHENOMENON_TIME_START" between '{time_start}' and '{time_end}'
                ;
                ''')
            # Adding SENSOR_ID
            df_inf["SENSOR_ID"] = sensor_name
            df_inf = df_inf.set_index("timestamp")

            taxa_dict = self.sta.dict_from_query(
                f"""
                select \"PROPERTIES\"->>'standardName' as taxa, \"ID\"  from \"DATASTREAMS\"
                    where	
                    \"SENSOR_ID\" = (select \"ID\" from \"SENSORS\" where \"NAME\" = '{sensor_name}')
                    and \"THING_ID\" = (select \"ID\" from \"THINGS\" where \"NAME\" = '{station_name}')
                    and \"PROPERTIES\"->>'dataType' = 'detections'
                    and \"PROPERTIES\"->>'modelName' = '{model_name}'
             """)

            for taxa, datastream_id in taxa_dict.items():
                self.debug(f"Getting taxa='{taxa}' with ID={datastream_id}")
                df = self.sta.dataframe_from_query(f'''
                    select timestamp, value as "{taxa}" from detections
                    where datastream_id = {datastream_id} and timestamp between '{time_start}' and '{time_end}';
                ''').set_index("timestamp")
                if df.empty:
                    df_inf[taxa] = 0
                else:
                    df_inf = df_inf.join(df, "timestamp", "left")

                df_inf[taxa] = df_inf[taxa].replace(np.nan, 0).astype(int)
            dataframes.append(df_inf)
        if not dataframes:
            return pd.DataFrame()
        df = pd.concat(dataframes).sort_index()
        return df

    def dataframe_from_sta_timeseries(self, conf: dict, resource: dict, station: dict, sensor: dict, time_start: pd.Timestamp = None,
                                      time_end: pd.Timestamp = None):
        """
        Returns a DataFrame for a specific Sensor in a specific time interval
        """

        data_type = resource["dataType"]
        sensor_name = sensor["#id"]
        station_name = station["#id"]

        variables = []  # by default all variables will be used
        if "@variables" in conf.keys():
            variables = conf["@variables"]

        if "averagePeriod" not in resource.keys():
            full_data = True
        else:
            full_data = False
            avg_period = resource["averagePeriod"]


        # Get the THING_ID from SensorThings based on the Station name
        thing_id = self.sta.value_from_query(
            f'select "ID" from "THINGS" where "NAME" = \'{station_name}\';'
        )
        sensor_id = self.sta.value_from_query(
            f'select "ID" from "SENSORS" where "NAME" = \'{sensor_name}\';'
        )
        # Super query that returns all varname and datastream_id  for one station-sensor combination
        # Results are stored as a DataFrame
        query = f'''select 
                "OBS_PROPERTIES"."NAME" as varname, 
                "DATASTREAMS"."ID" as datastream_id                    
            from  
                "DATASTREAMS"
            left join 
                "OBS_PROPERTIES"
            on 
                "DATASTREAMS"."OBS_PROPERTY_ID" = "OBS_PROPERTIES"."ID"
            where 
                "DATASTREAMS"."SENSOR_ID" = {sensor_id} and "DATASTREAMS"."THING_ID" = {thing_id} 
                and "DATASTREAMS"."PROPERTIES"->>'dataType' = '{data_type}'
                and ("DATASTREAMS"."PROPERTIES"->>'fullData')::boolean = {full_data}                    
            '''

        if not full_data:
            # if we are dealing with an average, we need to make sure that the average period matches
            query += f'\r\n\t\t and "DATASTREAMS"."PROPERTIES"->>\'averagePeriod\' = \'{avg_period}\''

        query += ";"
        datastreams = self.sta.dataframe_from_query(query)
        sensor_dataframes = []
        for idx, ds in datastreams.iterrows():
            # ds is a dict with 'varname', 'datastream_id' and 'data_type'
            datastream_id = ds["datastream_id"]
            varname = ds["varname"]
            if variables and varname not in variables:
                continue

            # Query all data from the datastream_id during the time range and assign proper variable name
            if full_data:
                q = (
                    f'''
                    select timestamp, value as "{varname}", qc_flag as "{varname + "_QC"}" 
                    from timeseries 
                    where datastream_id = {datastream_id}
                    and timestamp between \'{time_start}\' and \'{time_end}\';                     
                    '''
                )
            else:
                # Query the regular OBSERVATIONS table
                q = (f'''
                    select
                        "PHENOMENON_TIME_START" as timestamp,
                        "RESULT_NUMBER" as "{varname}",
                        "RESULT_QUALITY"->>'qc_flag' as "{varname + "_QC"}",
                        "RESULT_QUALITY"->>'stdev' as "{varname + "_STD"}"
                    from
                        "OBSERVATIONS"
                    where
                        "DATASTREAM_ID" = {datastream_id}
                        and "PHENOMENON_TIME_START" between \'{time_start}\' and \'{time_end}\';
                ''')
            df = self.sta.dataframe_from_query(q, debug=False)
            sensor_dataframes.append(df)
        if not sensor_dataframes:
            return pd.DataFrame()  # return empty dataframe
        df = merge_dataframes_by_columns(sensor_dataframes)
        df = df.rename(columns={"timestamp": "TIME", "depth": "DEPTH"})
        df = df.set_index("TIME")
        df = df.sort_index(ascending=True)
        return df

    def dataframe_from_sta_profiles(self, conf: dict, station: dict, sensor: dict, time_start: pd.Timestamp = None,
                                      time_end: pd.Timestamp = None):
        """
        Returns a DataFrame for a specific Sensor in a specific time interval
        """

        data_type = conf["dataType"]
        sensor_name = sensor["#id"]
        station_name = station["#id"]

        variables = []  # by default all variables will be used
        if "@variables" in conf.keys():
            variables = conf["@variables"]

        try:
            full_data = conf["dataSourceOptions"]["fullData"]
        except KeyError:
            self.error("[red]dataSourceOptions/fullData not found in dataset configuration!", exception=KeyError)

        # Get the THING_ID from SensorThings based on the Station name
        thing_id = self.sta.value_from_query(
            f'select "ID" from "THINGS" where "NAME" = \'{station_name}\';'
        )
        sensor_id = self.sta.value_from_query(
            f'select "ID" from "SENSORS" where "NAME" = \'{sensor_name}\';'
        )
        # Super query that returns all varname and datastream_id  for one station-sensor combination
        # Results are stored as a DataFrame
        query = f'''select 
                "OBS_PROPERTIES"."NAME" as varname, 
                "DATASTREAMS"."ID" as datastream_id                    
            from  
                "DATASTREAMS"
            left join 
                "OBS_PROPERTIES"
            on 
                "DATASTREAMS"."OBS_PROPERTY_ID" = "OBS_PROPERTIES"."ID"
            where 
                "DATASTREAMS"."SENSOR_ID" = {sensor_id} and "DATASTREAMS"."THING_ID" = {thing_id} 
                and "DATASTREAMS"."PROPERTIES"->>'dataType' = '{data_type}'
                and ("DATASTREAMS"."PROPERTIES"->>'fullData')::boolean = {full_data}                    
            '''

        if not full_data:
            # if we are dealing with an average, we need to make sure that the average period matches
            avg_period = conf["dataSourceOptions"]["averagePeriod"]
            query += f'\r\n\t\t and "DATASTREAMS"."PROPERTIES"->>\'averagePeriod\' = \'{avg_period}\''

        query += ";"
        datastreams = self.sta.dataframe_from_query(query)
        sensor_dataframes = []
        for idx, ds in datastreams.iterrows():
            # ds is a dict with 'varname', 'datastream_id' and 'data_type'
            datastream_id = ds["datastream_id"]
            varname = ds["varname"]
            if variables and varname not in variables:
                self.warning(f"Ignoring variable {varname}")
                continue

            # Query all data from the datastream_id during the time range and assign proper variable name
            if full_data:
                q = (
                    f'''
                    select timestamp, depth, value as "{varname}", qc_flag as "{varname + "_QC"}" 
                    from profiles 
                    where datastream_id = {datastream_id}
                    and timestamp between \'{time_start}\' and \'{time_end}\';                     
                    '''
                )
            else:
                # Query the regular OBSERVATIONS table
                q = (f'''
                    select
                        "PHENOMENON_TIME_START" as timestamp,
                        "PARAMETERS"->>'depth' as depth,
                        "RESULT_NUMBER" as "{varname}",
                        "RESULT_QUALITY"->>'qc_flag' as "{varname + "_QC"}",
                        "RESULT_QUALITY"->>'stdev' as "{varname + "_STD"}"
                    from
                        "OBSERVATIONS"
                    where
                        "DATASTREAM_ID" = {datastream_id}
                        and "PHENOMENON_TIME_START" between \'{time_start}\' and \'{time_end}\';
                ''')
            df = self.sta.dataframe_from_query(q, debug=False)
            # b = df.copy(deep=True)
            # b = df.set_index("timestamp", inplace=False)
            # print(b["2021-01-24T23:50:00Z":"2021-01-24T23:59:13Z"])
            sensor_dataframes.append(df)

        df = merge_dataframes_by_columns(sensor_dataframes, timestamp=["timestamp", "depth"])
        df = df.rename(columns={"timestamp": "TIME", "depth": "DEPTH"})
        df = df.set_index("TIME")
        df = df.sort_index(ascending=True)
        return df

    def dataframe_from_sta_observations(self, conf: dict, station: dict, sensor: dict, time_start: pd.Timestamp = None,
                                      time_end: pd.Timestamp = None):
        """
        Returns a DataFrame for a specific Sensor in a specific time interval
        """

        data_type = conf["dataType"]
        sensor_name = sensor["#id"]
        station_name = station["#id"]

        variables = []  # by default all variables will be used
        if "@variables" in conf.keys():
            variables = conf["@variables"]


        # Get the THING_ID from SensorThings based on the Station name
        thing_id = self.sta.value_from_query(
            f'select "ID" from "THINGS" where "NAME" = \'{station_name}\';'
        )
        sensor_id = self.sta.value_from_query(
            f'select "ID" from "SENSORS" where "NAME" = \'{sensor_name}\';'
        )
        # Super query that returns all varname and datastream_id  for one station-sensor combination
        # Results are stored as a DataFrame
        query = f'''select 
                "OBS_PROPERTIES"."NAME" as varname, 
                "DATASTREAMS"."ID" as datastream_id                    
            from  
                "DATASTREAMS"
            left join 
                "OBS_PROPERTIES"
            on 
                "DATASTREAMS"."OBS_PROPERTY_ID" = "OBS_PROPERTIES"."ID"
            where                
                "DATASTREAMS"."SENSOR_ID" = {sensor_id} and "DATASTREAMS"."THING_ID" = {thing_id}                                    
            '''

        query += ";"
        datastreams = self.sta.dataframe_from_query(query)
        sensor_dataframes = []
        for idx, ds in datastreams.iterrows():
            # ds is a dict with 'varname', 'datastream_id' and 'data_type'
            datastream_id = ds["datastream_id"]
            varname = ds["varname"]
            if variables and varname not in variables:
                continue

            data_columns = {
                "json": "RESULT_JSON",
                "files": "RESULT_STRING"
            }
            col = data_columns[conf["dataType"]]

            # Query the regular OBSERVATIONS table
            q = (f'''
                select
                    "PHENOMENON_TIME_START" as timestamp,
                    "{col}" as "{varname}"      
                from
                    "OBSERVATIONS"
                where
                    "DATASTREAM_ID" = {datastream_id}
                    and "PHENOMENON_TIME_START" between \'{time_start}\' and \'{time_end}\';
            ''')
            df = self.sta.dataframe_from_query(q, debug=False)

            if not df.empty:
                sensor_dataframes.append(df)

        if not sensor_dataframes:
            return pd.DataFrame()  # return an empty dataframe

        df = merge_dataframes_by_columns(sensor_dataframes)
        df = df.rename(columns={"timestamp": "TIME"})
        df = df.set_index("TIME")
        df = df.sort_index(ascending=True)
        return df

    def dataframe_from_sta_json(self, conf, station:dict, sensor:dict, time_start: pd.Timestamp = None, time_end: pd.Timestamp = None):
        """
        Return all the detections from a sensor
        """
        sensor_name = sensor["#id"]
        station_name = station["#id"]
        self.info(f"dataframe_from_sta_json station {station_name} sensor {sensor_name}")
        time_periods = []
        if "constraints" in conf.keys() and "fieldOfView" in conf["constraints"]:
            # Process fieldOfView constraint
            deployments = self.mc.get_sensor_deployments(sensor)
            # Keep only periods where the camera was looking to the chosen fieldOfView
            for deployment in deployments:
                if "fieldOfView" not in deployment.keys():
                    self.error(f"Deployment has no fieldOfView! sensor={sensor_name} activity_id={deployment['id']}",
                               exception=ValueError)
                if conf["constraints"]["fieldOfView"]["@programmes"] == deployment["fieldOfView"]["@programmes"]:
                    if deployment["end"]:
                        end = deployment["end"]
                    else:
                        end = time_end
                    time_periods.append((deployment["start"], end))
                else:
                    # No deployment with the fieldOfView of interest!
                    pass
        else:
            time_periods = [(time_start, time_end)]

        self.info(f"Sensor {sensor_name} time periods {time_periods}")
        dataframes = []
        for time_start, time_end in time_periods:
            model_name = ""
            self.info(f"Getting JSON with sensor={sensor_name} thing={station_name} from {time_start} to {time_end}")
            try:
                model_name = conf["constraints"]["@processes"]
                self.info(f"Using data from AI process {model_name}")
            except KeyError:
               self.error("AI process not defined in 'detections' dataset!", exception=ValueError)

            self.debug(f"getting datastream_id where dataType=json and sensor={sensor_name} and station={station_name}")
            # First get all the times where we have inferences. Let's assume that we only have one datastream that
            # matches data_type=json and model_name=<AI model>
            q = f""" select \"ID\" from \"DATASTREAMS\" 
                where 
                    \"SENSOR_ID\" = (select \"ID\" from \"SENSORS\" where \"NAME\" = '{sensor_name}')
                    and \"THING_ID\" = (select \"ID\" from \"THINGS\" where \"NAME\" = '{station_name}')
                    and \"PROPERTIES\"->>'dataType' = 'json'
                    and \"PROPERTIES\"->>'modelName' = '{model_name}'
                ;"""
            try:
                inference_datastream = self.sta.value_from_query(q)
            except LookupError:
                return pd.DataFrame()  # return empty dataframe

            self.debug(f"Pictures datastream_id = {inference_datastream}")
            df = self.sta.dataframe_from_query(f'''
                select
                    "PHENOMENON_TIME_START" as timestamp,
                    "PARAMETERS"->>'sourceImage' as "sourceImage",
                    "RESULT_JSON" as json,
                    "FEATURES"."NAME" as foi
                                        
                from "OBSERVATIONS", "FEATURES"
                where 
                    "OBSERVATIONS"."FEATURE_ID" = "FEATURES"."ID" and
                    "DATASTREAM_ID" = {inference_datastream} and
                    "PHENOMENON_TIME_START" between '{time_start}' and '{time_end}'
                ;
                ''')
            dataframes.append(df)
        df = pd.concat(dataframes)
        return df


    def netcdf_from_sta(self, conf: dict, resource: dict, time_start: pd.Timestamp = None, time_end: pd.Timestamp = None):
        """
        Creates a NetCDF file according to the configuration
        :param conf:
        :param time_start: time start to filter the data
        :param time_end: time start
        :return: generated NetCDF filename
        """
        self.debug("Creating NetCDF dataset")
        station = self.mc.get_document("stations", conf["@stations"])
        variables = []  # by default all variables will be used
        if "@variables" in conf.keys():
            variables = conf["@variables"]

        dataframes = []  # list with a dataframe per variable
        metadata = []    # list of a metadata dict per variable

        for sensor_name in conf["@sensors"]:
            self.info(f"Getting {sensor_name} data from {time_start} to {time_end}")
            sensor = self.mc.get_document("sensors", sensor_name)
            df = self.dataframe_from_sta(conf, station, sensor, resource, time_start=time_start, time_end=time_end)
            if df.empty:
                self.debug(f"no data for {sensor['#id']}  from {time_start} to {time_end}")
                tstart = None
                tend = None
            else:
                # now select real values of time start and time end
                tstart = pd.Timestamp(df.index.values[0])
                tend = pd.Timestamp(df.index.values[-1])
                dataframes.append(df)
                # Get the real-time start/time end
                m = self.metadata_harmonizer_conf(conf, sensor, station, variables, tstart=tstart, tend=tend)
                metadata.append(m)

        if all([df.empty for df in dataframes]):
            self.warning(f"ALL dataframes from {time_start} to {time_end} are empty!, skipping")
            return "", False

        self.info("Generating filename...")
        filename = self.dataset_filename(conf, "netcdf", time_start, time_end)
        self.info("Calling NetCDF wrapper...")
        filename = self.call_dataset_generator(conf, dataframes, metadata, output=filename)
        self.info(f"Dataset {filename} generated!")
        return filename, False

    def darwin_core_from_sta(self, conf, resource, time_start: pd.Timestamp, time_end: pd.Timestamp, overwrite=False):
        """
        Creates a Darwin Core Archive from JSON data from an AI model
        :param conf:
        :param time_start: time start to filter the data
        :param time_end: time start
        :return: generated NetCDF filename
        """
        self.info("Creating Darwin Core Archive dataset")
        assert resource["dataType"] == "json", f"Darwin Core only works with JSON data, got '{conf['dataType']}'"
        assert_type(conf, dict)
        assert_type(resource, dict)
        assert_type(time_start,  pd.Timestamp)
        assert_type(time_end,  pd.Timestamp)

        station = self.mc.get_document("stations", conf["@stations"])
        station_name = station["#id"]
        variables = []  # by default all variables will be used
        if "@variables" in conf.keys():
            variables = conf["@variables"]

        dataframes = []  # list with a dataframe per variable
        metadata = []    # list of a metadata dict per variable
        events = []

        if len(conf["@sensors"]) > 1:
            self.error("Unimplemented DwC-A for datasets with multiple sensors!", exception=ValueError)

        sensor_name = conf["@sensors"][0]
        self.info(f"Getting {sensor_name} data from {time_start} to {time_end}")
        sensor = self.mc.get_document("sensors", sensor_name)
        df = self.dataframe_from_sta_json(conf, station, sensor, time_start=time_start, time_end=time_end)
        dwca = DarwinCoreArchive(self.mc, df, sensor, station, conf, time_start, time_end, self.log)
        filename = self.dataset_filename(conf, "dwca", time_start, time_end)
        dwca.create_archive(filename)
        return filename, False



    def csv_from_sta(self, conf, resource, time_start: pd.Timestamp, time_end: pd.Timestamp):
        """
        Generates a CSV file from a SensorThings Database
        """
        filename = self.dataset_filename(conf, "csv", time_start, time_end)
        station = self.mc.get_document("stations", conf["@stations"])
        dataframes = []  # list with a dataframe per variable
        data_type = resource["dataType"]

        for sensor_name in conf["@sensors"]:
            sensor = self.mc.get_document("sensors", sensor_name)

            # Check if this sensor has any datastream with the assigned type
            q = f"""
                select count(*) from "DATASTREAMS" 
                where "SENSOR_ID" = (select "ID" from "SENSORS" where "NAME" = '{sensor_name}') 
                and "PROPERTIES"->>'dataType' = '{data_type}'; 
            """
            if self.sta.value_from_query(q) < 1:
                self.info(f"No data for type {data_type} for sensor {sensor_name}")
                continue

            df = self.dataframe_from_sta(conf, station, sensor, resource, time_start, time_end)

            if df.empty:
                self.error(f"No data for sensor={sensor_name}  between {time_start} and {time_end}")
                continue

            if len(conf["@sensors"]) > 1:
                df["SENSOR_ID"] = sensor_name
            dataframes.append(df)

        try:
            merge_sensors = conf["dataSourceOptions"]["mergeSensors"]
        except KeyError:
            merge_sensors = False
            pass

        if all([df.empty for df in dataframes]):
            self.warning(f"ALL dataframes from {time_start} to {time_end} are empty!, skipping")
            raise LookupError("no data")

        if merge_sensors:
            # If merge sensors, merge dataframe by index ignoring SENSOR_ID
            for df in dataframes:
                del df["SENSOR_ID"]
            df = merge_dataframes_by_columns(dataframes)
        else:
            df = merge_dataframes(dataframes)

        df = df.sort_index()
        df.to_csv(filename)
        return filename, False

    def zip_from_filesystem(self, conf, resource, time_start, time_end, overwrite=False) -> (str, bool):
        """
        Compresses all files in the fileserver into a zip file. Since millions of files can be compressed, a small
        bash script will be generated and transferred to the fileserver and executed there. Then the file will be
        transferred to the machine running MMAPI

        :return filename, delivered
        """

        # Create the dataset in /var/tmp
        self.info(f"Creating ZIP dataset, ID: {conf['#id']}, from {time_start} to {time_end}")

        dataset_id = conf["#id"]
        resource_id = resource["id"]
        tmp_folder = f"/var/tmp/{datetime.datetime.now().strftime('%s')}/{dataset_id}"
        remote_filename = self.dataset_filename(conf, "zip", time_start, time_end,
                                                tmp_folder="/var/tmp")

        # Check if the ZIP already exists, it may save a lot of time
        if check_url(self.fileserver.path2url(os.path.join(resource["path"], os.path.basename(remote_filename)))):
            if overwrite:
                self.warning(f"Overwriting previous dataset {remote_filename}")
            else:
                self.warning(f"File {remote_filename} already exists and available online!")
                return "", False

        # First step, get all files indexed in the SensorThings database
        datastream_ids = []

        for sensor in conf["@sensors"]:
            sensor_id = self.sta.sensor_id_name[sensor]
            # Get the Datastream ID of the files
            df = self.sta.dataframe_from_query(f'''
             select
                 "ID" from "DATASTREAMS" 
             where 
                 "PROPERTIES"->>'dataType' = 'files'
                 and "SENSOR_ID" = {sensor_id}; 
             ''', debug=False)

            files_ids = df["ID"].values  # convert dataframe to list

            for i in files_ids:
                datastream_ids.append(str(i))

        if len(datastream_ids) == 0:
            raise ValueError(f"No valid Datastreams found for sensors={conf['@sensors']} with dataType=files")

        # Now let's query for all registered files in the database matching the datastreams
        self.warning("LIMITING NUMBER OF FILES TO ONLY 100!!!")
        df = self.sta.dataframe_from_query(f'''
         select 
            "OBSERVATIONS"."PHENOMENON_TIME_START" as time,
            "OBSERVATIONS"."PHENOMENON_TIME_END" as time_end,
            "SENSORS"."NAME" as sensor,
            "OBSERVATIONS"."RESULT_STRING" as urls
        from "OBSERVATIONS", "DATASTREAMS", "SENSORS"             
        where            
            "DATASTREAM_ID" IN ({', '.join(datastream_ids)}) and
            "OBSERVATIONS"."DATASTREAM_ID" = "DATASTREAMS"."ID" and
            "SENSORS"."ID" = "DATASTREAMS"."SENSOR_ID" and
            "OBSERVATIONS"."PHENOMENON_TIME_START" between \'{time_start}\' and \'{time_end}\'        
        limit 100
        ;
        ''', debug=False)

        if df.empty:
            self.error(f"could not generate dataset {dataset_id}:{resource_id}", exception=ValueError)

        # Now we will do the following:
        # 1. Create a temporal folder in /var/temp/<epochtime>
        # 2. Create one folder per sensor:
        #     /var/temp/<epochtime>/<sensor1>
        #     /var/temp/<epochtime>/<sensor2>
        # 3. To avoid errors due to long comands over ssh, we will put all the required stuff inside a bash script
        #    and send it to the server the script will have:
        #    3.1 copy all files in the DataFrame to its sensor folder
        #    3.2 create index.csv file
        #    3.3 compress to zip
        #    3.3 send the zip file to its destination
        #    3.4 delete temporal files

        files = list(df["urls"])  # List of all files to be compressed

        if len(files) < 1:
            raise ValueError(f"No files to be zipped!")
        elif len(files) == 1:
            self.warning(f"Just one file!")
            raise ValueError(f"Just one file to be zipped?")

        # Now convert the file URLs to filesystem paths
        files = [self.fileserver.url2path(f) for f in files]
        df["src_files"] = files
        dst_files = []
        for _, row in df.iterrows():
            sensor = row["sensor"]
            dst_files.append(sensor + "/" + os.path.basename(row["src_files"]))

        df["files"] = dst_files

        dfcsv = df.copy()
        dfcsv = dfcsv
        del dfcsv["src_files"]
        dfcsv.to_csv("index.csv", index=False)
        # If the command is too long it cannot be sent via ssh and will raise an OSError, we create a temporal script
        # with the command and send it to the host
        script_name = os.path.basename(remote_filename).split(".")[0] + ".sh"
        self.info(f"Creating zip script {script_name}...")
        sensors = df["sensor"].unique()  # get list of sensors with data

        # create sensor folders
        for sensor in sensors:
            run_over_ssh(self.fileserver.host, f"mkdir -p {tmp_folder}/{sensor}")
        # Send index.csv file
        self.fileserver.send_file(tmp_folder, "index.csv", indexed=False)

        os.remove("index.csv")

        cmd = "#!/bin/bash\n"
        cmd += "set -o errexit"
        cmd += "set -o nounset"
        cmd += "echo 'Auto-generated script from MMAPI, compressing files into a zip file'\n"
        cmd += f"cd {tmp_folder}\n"
        for _, row in df.iterrows():
            source = row["src_files"]
            dest = row["files"]
            cmd += f"cp {source} {dest}\n"
        cmd += f"zip -9 -r {remote_filename} index.csv {' '.join(sensors)}\n"
        for sensor in sensors:
            cmd += f"rm {sensor}/* \n"
            cmd += f"rmdir {sensor}\n"
        cmd += f"rm {tmp_folder}/index.csv\n"
        cmd += f"rm {tmp_folder}/{script_name}\n"
        cmd += f"rmdir {tmp_folder}\n"

        self.info(f"Creating script {script_name}")
        with open(script_name, "w") as f:
            f.write(cmd)  # write the command to the script
        os.chmod(script_name, 0o775)

        self.info(f"Delivering script...")
        script_dest = os.path.join(f"{tmp_folder}")
        self.fileserver.send_file(script_dest, script_name, indexed=False)
        self.info(f"Creating zip file with {len(files)} files, this may take a while...")
        # Run the script!
        run_over_ssh(self.fileserver.host, script_dest + "/" + script_name, fail_exit=True)

        # Check if the size is coherent
        a = run_over_ssh(self.fileserver.host, f"ls -l {remote_filename}")
        size = int(a.split(" ")[4]) # size is column 5 of ls -l command
        if size < 3000:

            self.warning(f"first url: {df['urls'].values[0]}")
            self.warning(f"last  url: {df['urls'].values[-1]}")
            self.error("ZIP file looks empty! less than 3k means there's nothing inside", exception=ValueError)


        # At this point the file should be created
        # if the destination and the fileserver are the same (very likely), just copy from temp folder to the definitive
        # folder
        if self.fileserver.host == resource["host"]:
            dest = resource["path"]
            src = remote_filename
            self.info(f"Moving inside fileserver from {src} to {dest}")
            filename = os.path.join(dest, os.path.basename(src))
            run_over_ssh(self.fileserver.host, f"mkdir -p {os.path.dirname(filename)}")
            run_over_ssh(self.fileserver.host, f"mv {src} {filename}")
            delivered = True

        else:
            # Download to this machine
            self.error("Not implemented!!", exception=ValueError)
            delivered = False

        os.remove(script_name)
        return filename, delivered

    def metadata_harmonizer_conf(self, dataset, sensor: dict, station: dict, variable_ids: list,
                                 default_data_mode="real-time", os_data_type="OceanSITES time-series data",
                                 tstart: pd.Timestamp = None, tend: pd.Timestamp = None) -> dict:
        """
        This method returns the configuration required by the Metadata Harmonizer tool from the Metadata DB
        :param dataset: sensor dict from Metadata DB database
        :param sensor: sensor dict from Metadata DB database
        :param station: station dict from Metadata DB database
        :param variable_ids: list of variables to be included in the dataset
        :param default_data_mode: Default data mode
        :param os_data_type: OceanSITES data type, probably by default time-series data
        :return:
        """
        if tstart:
            assert_type(tstart, pd.Timestamp)
        if tend:
            assert_type(tend, pd.Timestamp)


        if not variable_ids:  # By default, use ALL variables
            variable_ids = [dic["@variables"] for dic in sensor["variables"]]

        variables = [self.mc.get_document("variables", v) for v in variable_ids]

        # Get minimum info (PI and owner)
        pi, _ = self.mc.get_contact_by_role(dataset, "ProjectLeader")
        owner, _ = self.mc.get_contact_by_role(station, "owner")

        # Put all people involved in an array
        people = []
        roles = []
        for c in dataset["contacts"]:
            role = c["role"]
            if "@organizations" in c.keys():
                continue
            name = self.mc.get_document("people", c["@people"])["name"]
            people.append(name)
            roles.append(role)

        # Put funding information
        project_names = []
        project_codes = []
        if "funding" in dataset.keys():
            for project_id in dataset["funding"]["@projects"]:
                project = self.mc.get_document("projects", project_id)
                project_names.append(project["acronym"])
                project_codes.append(project["funding"]["grantId"])
        # Get the OceanSITES Data Mode
        data_mode = default_data_mode
        if "dataMode" in dataset.keys():
            data_mode = dataset["dataMode"]
        data_mode_dict = {"real-time": "R", "delayed": "D", "mixed": "M", "provisional": "P"}
        dm = data_mode_dict[data_mode]
        # global attributes
        gl = {
            "*title": dataset["title"],
            "*summary": dataset["summary"],
            "*institution_edmo_code": owner["EDMO"].split("/")[-1],  # just the code, not the full URL
            "$emso_facility": "None",
            "~network": "None",
            "*source": station["platformType"]["label"],
            "$data_type": os_data_type,
            "$data_mode": dm,
            "*principal_investigator": pi["name"],
            "*principal_investigator_email": pi["email"],
            "funding_project_names": project_names,
            "funding_project_codes": project_codes
        }

        if "emsoFacility" in station.keys():
            gl["$emso_facility"] = station["emsoFacility"]
            if station["emsoFacility"] != "None":
                gl["~network"] = "EMSO"

        # Create dictionary where var_id is the key and the value is the units doc
        units = {}
        for var_id in variable_ids:
            found = False
            for var in sensor["variables"]:
                if var["@variables"] == var_id:
                    units[var_id] = self.mc.get_document("units", var["@units"])
                    found = True
            if not found:
                raise LookupError(f"variable {var_id} not found in sensor {sensor['#id']}!")
        var_metadata = {}
        for variable in variables:
            var_id = variable["#id"]
            var_metadata[var_id] = {
                "*long_name": variable["description"],
                "*sdn_parameter_uri": variable["definition"],
                "~sdn_uom_uri": units[var_id]["definition"],
                "~standard_name": variable["standard_name"],
            }
        sensor_metadata = {
            "*sensor_model_uri": sensor["model"]["definition"],
            "*sensor_serial_number": sensor["serialNumber"],
            "$sensor_mount": "mounted_on_fixed_structure",
            "$sensor_orientation": "upward"
        }

        unknown = "http://vocab.nerc.ac.uk/collection/L22/current/TOOLZZZ/"
        if not sensor_metadata["*sensor_model_uri"]:
            self.warning("Sensor model not defined! setting to unknown (SDN::L22:TOOLZZZ")
            sensor_metadata["*sensor_model_uri"] = unknown

        latitude, longitude, depth = self.mc.get_station_position(station["#id"], tstart)
        coordinates = {
            "depth": depth,
            "latitude": latitude,
            "longitude": longitude
        }

        # Now build to document
        d = {
            "global": gl,
            "variables": var_metadata,
            "sensor": sensor_metadata,
            "coordinates": coordinates
        }
        return d


    def call_dataset_generator(self, conf: dict, dataframes: list, metadata: list, output="output.nc"):
        """
        Dump dataframes and metadata to temporal files and calls the datasets generator
        :param dataframes:
        :param metadata:
        :param output:
        :return:
        """
        assert (len(dataframes) == len(metadata))
        # The metadata expander handles special cases where the variables listed do not math with the data columns,
        # like AI-produced data for object detections.
        metadata_expander = {
            # Key -> variable name, value -> function that processes the metadata with conf, meta data args
            "FATX": self.fatx_metadata_expander,
        }

        if not self.emso:
            self.emso = emso_metadata_harmonizer.metadata.EmsoMetadata()
        dataframes = [df.reset_index() for df in dataframes]


        for i, (meta, df) in enumerate(zip(metadata, dataframes)):
            # Expand metadata according to the metadata_expander rules
            for varname, handler in metadata_expander.items():
                if varname in meta["variables"].keys():
                    meta = handler(conf, meta, df)

            df.to_csv(f"data_{i:02d}.csv")
            with open(f"meta_{i:02d}.json", "w") as f:
                json.dump(meta, f, indent=4)

        mh.generate_dataset(dataframes, metadata, output=output, emso_metadata=self.emso)
        return output


    def fatx_metadata_expander(self, conf: dict, meta: dict, df: pd.DataFrame):
        """
        FATX is used for datasets where fish abundance has been estimated probably with an object detection algorithm.
        It is assumed that it is an underwater_photography dataset.
        """
        assert_type(conf, dict)
        assert_type(meta, dict)
        assert_type(df, pd.DataFrame)
        try:
            ai_model = conf["constraints"]["@processes"]
        except KeyError:
            self.error("Could not find @processes reference in FATX metadata!", exception=ValueError)
        self.info(f"FATX metadata expander with model '{ai_model}'")
        process = self.mc.get_document("processes", ai_model)
        variables = process["variableNames"]

        # rename what needs to be renamed
        if "rename" in process.keys():
            for i, var in enumerate(variables):
                if var in process["rename"].keys():
                    variables[i] = process["rename"][var]

        if "ignore" in process.keys():
            for var in process["ignore"]:
                if var in variables:
                    del variables[variables.index(var)]

        var_list = list(np.unique(variables))

        db_variables = self.mc.get_documents("variables")
        # create a dictionary with standard_name as key and doc as value
        variable_dict = {doc["standard_name"]: doc for doc in db_variables if doc["standard_name"]}

        meta["variables"] = {}
        for var in var_list:
            variable_doc = variable_dict[var]
            meta["variables"][var] = {
                "*long_name": f"abundance of {var} detected by AI model '{ai_model}'",
                "*sdn_parameter_uri": variable_doc["definition"],
                "~sdn_uom_uri": "Dimensionless",
                "~standard_name": var,
            }

        if "SourceImage" in df.columns:
            variable_doc = self.mc.get_document("variables", "underwater_photography")
            meta["variables"][var] = {
                "*long_name": f"underwater images",
                "*sdn_parameter_uri": variable_doc["definition"],
                "~sdn_uom_uri": "Dimensionless",
                "~standard_name": variable_doc["standard_name"]
            }

        if "ProcessedImage" in df.columns:
            variable_doc = self.mc.get_document("variables", "underwater_photography")
            meta["variables"][var] = {
                "*long_name": f"underwater images with AI detections",
                "*sdn_parameter_uri": variable_doc["definition"],
                "~sdn_uom_uri": "Dimensionless",
                "~standard_name": variable_doc["standard_name"]
            }

        return meta

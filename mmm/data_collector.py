#!/usr/bin/env python3
"""
This file implements the DataCollector, a class implementing generic data access and delivery.

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 30/11/22
"""
import logging
import emso_metadata_harmonizer.metadata
import numpy as np
import pandas as pd
import rich
from .data_sources import SensorThingsApiDB
from .ckan import CkanClient
from .common import run_subprocess, check_url, detect_common_path, run_over_ssh, LoggerSuperclass, assert_types, \
    assert_type
from .data_manipulation import open_csv, merge_dataframes_by_columns, merge_dataframes, calculate_time_intervals
from .metadata_collector import MetadataCollector, init_metadata_collector
from .fileserver import FileServer
import os
import emso_metadata_harmonizer as mh
from .schemas import dataset_exporter_conf, dataset_exporter_formats
from mmm import DatasetObject


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
            "zip": ".zip"
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
                         time_end: pd.Timestamp|str = None, fmt: str = "", current=False) -> list:
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

        if type(dataset) is str:
            conf = self.mc.get_document("datasets", dataset)
        else:
            conf = dataset
        dataset_id = conf["#id"]
        self.info(f"Creating dataset {dataset_id} from {time_start} to {time_end}")

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

        # Check if we need to create a single file or a tree of smaller files:
        if conf["export"][service_name]["period"] == "none":
            d = self.generate_dataset_file(conf, service_name, time_start, time_end, fmt=fmt)
            return [d]
        else:
            return self.generate_dataset_tree(conf, service_name, time_start=time_start, time_end=time_end, fmt=fmt)

    def generate_dataset_tree(self,  dataset: dict, service_name: str, time_start: pd.Timestamp = None,
                              time_end: pd.Timestamp = None, fmt: str = ""):
        assert_type(service_name, str)
        assert_types(dataset, [dict, str])
        assert_types(time_start, [pd.Timestamp, type(None)])
        assert_types(time_end, [pd.Timestamp, type(None)])
        conf = dataset

        if service_name not in conf["export"].keys():
            raise ValueError(f"Dataset {conf['#id']} doesn't have export configuration for service '{service_name}'")

        service = conf["export"][service_name]

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
        intervals = calculate_time_intervals(time_start, time_end, service["period"])

        datasets = []
        for tstart, tend in intervals:
            try:
                d = self.generate_dataset_file(conf, service_name, tstart, tend, fmt=fmt)
                datasets.append(d)
            except LookupError:
                continue
        return datasets

    def generate_dataset_file(self, dataset: dict, service_name: str, time_start: pd.Timestamp,
                              time_end: pd.Timestamp, fmt: str = "") -> DatasetObject:
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
        assert_type(time_start, pd.Timestamp)
        assert_type(time_end, pd.Timestamp)
        conf = dataset

        self.debug(f"Generating dataset file from {time_start} to {time_end}")
        self.debug(f"Exporting to {service_name}")

        # Convert service ID to dict
        if service_name not in conf["export"].keys():
            raise ValueError(f"Dataset {conf['#id']} doesn't have export configuration for service '{service_name}'")

        service = conf["export"][service_name]

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
            fmt = service["format"]
        else:
            assert fmt in dataset_exporter_formats, f"Format '{fmt}' not allowed"
        if fmt == "csv":
            filename = self.csv_from_sta(conf, time_start, time_end)
        elif fmt == "netcdf":
            filename = self.netcdf_from_sta(conf, time_start, time_end)
        elif fmt == "zip":
            filename = self.zip_from_filesystem(conf, time_start, time_end)
        else:
            raise ValueError(f"Unknown dataSource format '{fmt}'")
        obj = DatasetObject(conf, filename, service_name, time_start, time_end, fmt, self.log)
        return obj

    def dataframe_from_sta(self, conf: dict, station: dict, sensor: dict, time_start: pd.Timestamp,
                           time_end: pd.Timestamp) -> pd.DataFrame:
        if conf["dataType"] == "timeseries":
            return self.dataframe_from_sta_timeseries(conf, station, sensor, time_start, time_end)
        elif conf["dataType"] == "detections":
            return self.dataframe_from_sta_detections(conf, station, sensor, time_start, time_end)
        elif conf["dataType"] == "profiles":
            return self.dataframe_from_sta_profiles(conf, station, sensor, time_start, time_end)
        elif conf["dataType"] == "files":
            return self.dataframe_from_sta_observations(conf, station, sensor, time_start, time_end)
        elif conf["dataType"] == "mixed":

            conf_copy = conf.copy()
            conf_copy["dataType"] = "files"  # force dataType to files

            files =  self.dataframe_from_sta_observations(conf_copy, station, sensor, time_start, time_end)

            conf_copy = conf.copy()
            conf_copy["dataType"] = "timeseries"  # force dataType to files
            conf_copy["dataSourceOptions"]["fullData"] = True
            timeseries = self.dataframe_from_sta_timeseries(conf_copy, station, sensor, time_start, time_end)
            df = merge_dataframes_by_columns([timeseries, files])
            return df
        else:
            self.error(f"Unimplemented data type {conf['dataType']}", exception=ValueError)

    def dataframe_from_sta_detections(self, conf: dict, station: dict, sensor: dict, time_start: pd.Timestamp,
                                      time_end: pd.Timestamp):
        """
        Return all the detections from a sensor
        """
        sensor_name = sensor["#id"]
        station_name = station["#id"]


        time_periods = []

        if "fieldOfView" in conf["constraints"]:
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
               self.error("AI process not defined in 'detections' dataset!", exception=True)

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

    def dataframe_from_sta_timeseries(self, conf: dict, station: dict, sensor: dict, time_start: pd.Timestamp = None,
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
        print(df)
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


    def netcdf_from_sta(self, conf, time_start: pd.Timestamp = None, time_end: pd.Timestamp = None):
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
            df = self.dataframe_from_sta(conf, station, sensor, time_start=time_start, time_end=time_end)
            if df.empty:
                self.debug(f"no data for {sensor['#id']}  from {time_start} to {time_end}")
                tstart = None
                tend = None
            else:
                # now select real values of time start and time end
                tstart = pd.Timestamp(df.index.values[0])
                tend = pd.Timestamp(df.index.values[-1])
            dataframes.append(df)
            # Get the real time start/time end
            m = self.metadata_harmonizer_conf(conf, sensor, station, variables, tstart=tstart, tend=tend)
            metadata.append(m)

        if all([df.empty for df in dataframes]):
            self.warning(f"ALL dataframes from {time_start} to {time_end} are empty!, skipping")
            raise LookupError("no data")

        self.info("Generating filename...")
        filename = self.dataset_filename(conf, "netcdf", time_start, time_end)
        self.info("Calling NetCDF wrapper...")
        for idx, (data, meta) in enumerate(zip(dataframes, metadata)):
            data.to_csv(f"data_{idx:02d}.csv")
            with open(f"meta_{idx:02d}.json", "w") as f:
                import json
                f.write(json.dumps(meta, indent=2))


        filename = self.call_dataset_generator(dataframes, metadata, output=filename)
        self.info(f"Dataset {filename} generated!")
        return filename

    def csv_from_sta(self, conf, time_start: pd.Timestamp, time_end: pd.Timestamp):
        """
        Generates a CSV file from a SensorThings Database
        """
        filename = self.dataset_filename(conf, "csv", time_start, time_end)
        station = self.mc.get_document("stations", conf["@stations"])
        dataframes = []  # list with a dataframe per variable

        for sensor_name in conf["@sensors"]:
            sensor = self.mc.get_document("sensors", sensor_name)
            df = self.dataframe_from_sta(conf, station, sensor, time_start, time_end)
            if df.empty:
                self.warning(f"No data for sensor={sensor_name}  between {time_start} and {time_end}")
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
        return filename

    def zip_from_filesystem(self, conf, time_start, time_end) -> str:
        """
        Compresses all files in the fileserver into a zip file. Since millions of files can be compressed, a small
        bash script will be generated and transfered to the fileserver and executed there. Then the file will be
        transfered to the machine running MMAPI
        """

        # Create the dataset in /var/tmp
        remote_filename = self.dataset_filename(conf, "zip", time_start, time_end, tmp_folder="/var/tmp")
        self.info(f"Creating ZIP dataset, ID: {conf['#id']}, from {time_start} to {time_end}")

        # First step, get all files indexed in the SensorThings database
        datastream_ids = []
        #for sensor in conf["@sensors"]:
        if len(conf["@sensors"])!=1:
            raise ValueError("ZIP dataset with multiple sensors not supported")

        sensor = conf["@sensors"][0]
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
            raise ValueError(f"No valid Datastreams found for sensor={sensor} with dataType=files")

        # Now let's query for all registered files in the database matching the datastreams
        df = self.sta.dataframe_from_query(f'''
        select "RESULT_STRING" as files from "OBSERVATIONS"             
        where            
            "DATASTREAM_ID" IN ({", ".join(datastream_ids)})
            and "PHENOMENON_TIME_START" between \'{time_start}\' and \'{time_end}\';
        ''', debug=False)

        files = list(df["files"])  # List of all files to be compressed
        if len(files) < 1:
            raise ValueError(f"No files to be zipped!")
        elif len(files) == 1:
            self.warning(f"Just one file!")
            raise ValueError(f"Just one file to be zipped?")

        # Now convert the file URLs to filesystem paths
        files = [self.fileserver.url2path(f) for f in files]

        # Try to remove path until the sensor name
        basepath = detect_common_path(files)
        self.debug(f"all files start with path: '{basepath}'")
        # Find until the sensor name and keep it in the files
        idx = basepath.find(sensor)
        if idx < 0:
            raise ValueError(f"sensor_name='{sensor}' not in basepath='{basepath}'!")
        basepath = basepath[:idx]  # basepath
        self.info(f"Base path for the dataset is {basepath}")

        # Erase basepath, so we keep the basepath
        files = [f.replace(basepath, "") for f in files]

        # Make sure that relative paths are not interpreted as absolute now
        for i in range(len(files)):
            if files[i].startswith("/"):
                files[i] = files[i][1:]

        # If the command is too long it cannot be sent via ssh and will raise an OSError, we create a temporal script
        # with the command and send it to the host
        script_name = os.path.basename(remote_filename).split(".")[0] + ".sh"
        self.info(f"Creating zip script {script_name}...")

        cmd = "#!/bin/bash\n"
        cmd += "echo 'Auto-generated script from MMAPI, compressing files into a zip file'\n"
        cmd += f"cd {basepath}\n"
        cmd += f"mkdir -p {os.path.dirname(remote_filename)}\n"
        cmd += f"zip -r {remote_filename} {' '.join(files)}\n"

        with open(script_name, "w") as f:
            f.write(cmd)  # write the command to the script
        os.chmod(script_name, 0o775)

        self.info(f"Delivering script...")
        script_dest = os.path.join(f"/var/tmp/{script_name}")
        self.fileserver.send_file("/var/tmp", script_name, indexed=False)

        self.info(f"Creating zip file with {len(files)} files, this may take a while...")
        run_over_ssh(self.fileserver.host, script_dest, fail_exit=True)

        # Now get the file
        self.info(f"get zip file from server...")
        filename = self.fileserver.recv_file(remote_filename, "tmpdata")

        # delete remote file
        run_over_ssh(self.fileserver.host, f"rm {remote_filename}")
        # delete local file
        os.remove(script_name)
        run_over_ssh(self.fileserver.host, f"rm {script_dest}")
        return filename

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

    def upload_datafile_to_ckan(self, ckan, dataset: DatasetObject):
        """
        Takes a dataset in the FileServer and publish it to CKAN as a resource
        """
        self.info(f"Uploading file to ckan {dataset.url}")
        assert type(ckan) is CkanClient
        assert type(dataset) is DatasetObject

        # The ID of the resource will be the filename in lower case with _<format>
        resource_id = os.path.basename(dataset.filename).replace(".", "_").lower()
        package_id = dataset.dataset_id.lower()

        name = f"{dataset.dataset_id} data from {dataset.tstart_str('%Y-%m-%d')} to {dataset.tend_str('%Y-%m-%d')}"
        description = f"Data in {dataset.fmt} format from {dataset.tstart_str()} to {dataset.tend_str()}"
        self.info(f"registering dataset url: {dataset.url}")
        return ckan.resource_create(
            package_id,
            resource_id,
            description=description,
            name=name,
            format=dataset.fmt,
            resource_url=dataset.url)

    def call_dataset_generator(self, dataframes: list, metadata: list, output="output.nc"):
        """
        Dump dataframes and metadata to temporal files and calls the datasets generator
        :param dataframes:
        :param metadata:
        :param output:
        :return:
        """
        assert (len(dataframes) == len(metadata))
        if not self.emso:
            self.emso = emso_metadata_harmonizer.metadata.EmsoMetadata()
        dataframes = [df.reset_index() for df in dataframes]
        mh.generate_dataset(dataframes, metadata, output=output, emso_metadata=self.emso)
        return output

#!/usr/bin/env python3
"""

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 24/5/24
"""
import pandas as pd
import numpy as np
from rich.progress import Progress
from .postgresql import PgDatabaseConnector
from .timescaledb import TimescaleDB
from ..common import LoggerSuperclass, reverse_dictionary, dataframe_to_dict, rm_remote_files, rsync_files, assert_dict, \
    assert_type
import rich
import os
import time
import gc
from ..data_manipulation import slice_dataframes, detect_data_gaps_by_period, detect_data_gaps_direct
from ..schemas import mmapi_data_types


class SensorThingsApiDB(PgDatabaseConnector, LoggerSuperclass):
    def __init__(self, host, port, db_name, db_user, db_password, logger, timescaledb=False, timeout=60):
        """
        initializes  DB connector specific for SensorThings API database (FROST implementation)
        :param host:
        :param port:
        :param db_name:
        :param db_user:
        :param db_password:
        :param logger:
        """
        PgDatabaseConnector.__init__(self, host, port, db_name, db_user, db_password, logger)
        self.host = host
        os.makedirs("tmpdata", exist_ok=True)
        LoggerSuperclass.__init__(self, logger, "STA DB")
        self.info("Initialize database connector...")
        tinit = time.time()
        if (    # Make sure that all this tables exist
                not self.check_if_table_exists("OBSERVATIONS")
                or not self.check_if_table_exists("DATASTREAMS")
                or not self.check_if_table_exists("SENSORS")
                or not self.check_if_table_exists("FEATURES")
                or not self.check_if_table_exists("THINGS")
        ):
            if time.time() - tinit > timeout:
                self.error("Timeout error, could not setup SensorThingsApiDB")
                raise TimeoutError("Database not initialize")
            else:
                self.info("Waiting until FROST-Server sets up...")
            time.sleep(10)

        self.__sensor_properties = {}

        if timescaledb:
            self.timescale = TimescaleDB(self, logger)
        else:
            self.timescale = None

        # This dicts provide a quick way to get the relations between elements in the database without doing a query
        # they are initialized with __initialize_dicts()
        self.datastream_id_sensor_name = {}  # key: datastream_id, value: sensor name
        self.sensor_id_name = {}  # key: sensor id, value: name
        self.thing_id_name = {}  # key: sensor id, value: name
        self.datastream_name_id = {}  # key: datastream name, value: datastream id
        self.obs_prop_name_id = {}  # key: observe  d property name, value: observed property id
        self.datastream_fois = {}  # key datastream_id , value: datastream's default foi
        self.datastream_properties = {}

        # dictionaries where key is ID and value is name
        self.sensor_name_id = {}
        self.datastream_id_name = {}
        self.thing_name_id = {}
        self.obs_prop_id_name = {}

        # dictionaries where sensors key is name and value is ID
        self.initialize_dicts()

        self.__last_observation_index = -1
        self.get_last_observation_id()

        self.add_unique_name_constraints()
        self.add_unique_observation_index()

    def initialize_dicts(self):
        """
        Initialize the dicts used for quickly access to relations without querying the database
        """
        self.info("Initializing internal structures...")
        # DATASTREAM -> SENSOR relation
        query = """
            select "DATASTREAMS"."ID" as datastream_id, "SENSORS"."ID" as sensor_id, "SENSORS"."NAME" as sensor_name, 
            "DATASTREAMS"."NAME" as datastream_name, "DATASTREAMS"."PROPERTIES" as properties
            from "DATASTREAMS", "SENSORS" 
            where "DATASTREAMS"."SENSOR_ID" = "SENSORS"."ID" order by datastream_id asc;"""
        df = self.dataframe_from_query(query)

        for idx, row in df.iterrows():
            if "defaultFeatureOfInterest" in row["properties"].keys():
                self.datastream_fois[row["datastream_id"]] = row["properties"]["defaultFeatureOfInterest"]

            self.datastream_properties[row["datastream_id"]] = row["properties"]

        # key: datastream_id ; value: sensor name
        self.datastream_id_sensor_name = dataframe_to_dict(df, "datastream_id", "sensor_name")
        datastream_name_dict = dataframe_to_dict(df, "datastream_id", "datastream_name")

        # SENSOR ID -> SENSOR NAME
        self.sensor_id_name = self.get_sensors()  # key: sensor_id, value: sensor_name
        self.thing_id_name = self.get_things()  # key: sensor_id, value: sensor_name

        # DATASTREAM_NAME -> DATASTREAM_ID
        df = self.dataframe_from_query(f'select "ID", "NAME" from "DATASTREAMS";')
        self.datastream_name_id = dataframe_to_dict(df, "NAME", "ID")

        # OBS_PROPERTY NAME -> OBS_PROPERTY ID
        df = self.dataframe_from_query('select "ID", "NAME" from "OBS_PROPERTIES";')
        self.obs_prop_name_id = dataframe_to_dict(df, "NAME", "ID")

        # dictionaries where key is ID and value is name
        self.sensor_name_id = reverse_dictionary(self.sensor_id_name)
        self.datastream_id_name = reverse_dictionary(self.datastream_name_id)
        self.thing_name_id = reverse_dictionary(self.thing_id_name)
        self.obs_prop_id_name = reverse_dictionary(self.obs_prop_name_id)


    def get_sensor_datastreams(self, sensor_id):
        """
        Returns a dataframe with all datastreams belonging to a sensor
        :param sensor_id: ID of a sensor
        :return: dataframe with datastreams ID, NAME and PROPERTIES
        """
        query = (f'select "ID" as id , "NAME" as name, "THING_ID" as thing_id, "OBS_PROPERTY_ID" AS obs_prop_id,'
                 f' "PROPERTIES" as properties from "DATASTREAMS" where "SENSOR_ID" = {sensor_id};')
        df = self.dataframe_from_query(query)
        return df

    def get_sensors(self):
        """
        Returns a dictionary with sensor's names and their id, e.g. {"SBE37": 3, "AWAC": 5}
        :return: dictionary
        """
        df = self.dataframe_from_query('select "ID", "NAME" from "SENSORS";')
        return dataframe_to_dict(df, "NAME", "ID")

    def get_things(self):
        df = self.dataframe_from_query('select "ID", "NAME" from "THINGS";')
        return dataframe_to_dict(df, "NAME", "ID")

    def get_sensor_properties(self):
        """
        Returns a dictionary with sensor's names and their properties
        :return: dictionary
        """
        if self.__sensor_properties:
            return self.__sensor_properties
        df = self.dataframe_from_query('select "NAME", "PROPERTIES" from "SENSORS";')
        self.__sensor_properties = dataframe_to_dict(df, "NAME", "PROPERTIES")
        return self.__sensor_properties

    def get_datastream_sensor(self, fields=["ID", "SENSOR_ID"]):
        select_fields = ", ".join(fields)
        df = self.dataframe_from_query(f'select {select_fields} from "DATASTREAMS";')
        return dataframe_to_dict(df, "NAME", "SENSOR_ID")

    def get_datastream_properties(self, fields=["ID", "PROPERTIES"]):
        select_fields = f'"{fields[0]}"'
        for f in fields[1:]:
            select_fields += f', "{f}"'

        df = self.dataframe_from_query(f'select {select_fields} from "DATASTREAMS";')
        return dataframe_to_dict(df, "ID", "PROPERTIES")

    def get_data(self, identifier, time_start: str, time_end: str):
        """
        Access the 0BSERVATIONS data table and exports all data between time_start and time_end
        :param identifier: datasream name (str) or datastream id (int)
        :param time_start: start time
        :param time_end: end time  (not included)
        """
        if type(identifier) == int:
            pass
        elif type(identifier) == str:  # if string, convert from name to ID
            identifier = self.datastream_name_id[identifier]

        query = f' select ' \
                f'    "PHENOMENON_TIME_START" AS timestamp, ' \
                f'    "RESULT_NUMBER" AS value,' \
                f'    ("RESULT_QUALITY" ->> \'qc_flag\'::text)::integer AS qc_flag,' \
                f'    ("RESULT_QUALITY" ->> \'stdev\'::text)::double precision AS stdev ' \
                f'from "OBSERVATIONS" ' \
                f'where "OBSERVATIONS"."DATASTREAM_ID" = {identifier} ' \
                f'and "PHENOMENON_TIME_START" >= \'{time_start}\' and  "PHENOMENON_TIME_START" < \'{time_end}\'' \
                f'order by timestamp asc;'

        df = self.dataframe_from_query(query)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        if not df.empty and np.isnan(df["stdev"].max()):
            self.debug(f"Dropping stdev for {self.datastream_id_name[identifier]}")
            del df["stdev"]
        return df.set_index("timestamp")

    def dict_from_query(self, query, debug=False):
        response = self.exec_query(query, debug=debug, fetch=True)
        if len(response) == 0:
            return {}
        elif len(response[0]) != 2:
            raise ValueError(f"Expected two fields in response, got {len(response[0])}")

        return {key: value for key, value in response}

    def inject_to_timeseries(self, df, datastreams, max_rows=100000, disable_triggers=False, usecs=False,
                             tmp_folder="/tmp/sta_db_copy/data", tmp_folder_db="/tmp/sta_db_copy/data"):
        """
        Inject all data in df into the timeseries table via SQL copy
        """

        init = time.time()
        os.makedirs(tmp_folder, exist_ok=True)
        os.chown(tmp_folder, os.getuid(), os.getgid())
        rich.print("Splitting input dataframe into smaller ones")
        rows = int(max_rows / len(datastreams))
        dataframes = slice_dataframes(df, max_rows=rows)
        files = self.dataframes_to_timeseries_csv(dataframes, datastreams, tmp_folder, usecs=usecs)
        rich.print("Generating all files took %0.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            t = time.time()
            rich.print(f"rsync files to remote server '{self.host}'...", end="")
            rsync_files(self.host, tmp_folder, files)
            rich.print(f"[green]done![/green] took {time.time() - t:.02f} s")

        if disable_triggers:
            self.disable_all_triggers()

        with Progress() as progress:
            task1 = progress.add_task("SQL COPY to timeseries hypertable...", total=len(dataframes))
            for file in files:
                file = file.replace(tmp_folder, tmp_folder_db)
                self.sql_copy_csv(file, "timeseries")
                progress.advance(task1, advance=1)

        if disable_triggers:
            self.enable_all_triggers()

        with Progress() as progress:
            task1 = progress.add_task("remove temp files...", total=len(dataframes))
            for file in files:
                os.remove(file)
                progress.advance(task1, advance=1)

        rich.print("[magenta]Inserting all via SQL COPY took %.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            rm_remote_files(self.host, files)

    def inject_to_profiles(self, df, datastreams, max_rows=100000, disable_triggers=False,
                           tmp_folder="/tmp/sta_db_copy/data", tmp_folder_db="/tmp/sta_db_copy/data"):
        """
        Inject all data in df into the timeseries table via SQL copy
        """

        init = time.time()
        os.makedirs(tmp_folder, exist_ok=True)
        os.chown(tmp_folder, os.getuid(), os.getgid())

        rich.print("Splitting input dataframe into smaller ones")
        rows = int(max_rows / len(datastreams))
        dataframes = slice_dataframes(df, max_rows=rows)
        files = self.dataframes_to_profile_csv(dataframes, datastreams, tmp_folder)
        rich.print("Generating all files took %0.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            t = time.time()
            rich.print("rsync files to remote server...", end="")
            rsync_files(self.host, tmp_folder, files)
            rich.print(f"[green]done![/green] took {time.time() - t:.02f} s")

        if disable_triggers:
            self.disable_all_triggers()

        with Progress() as progress:
            task1 = progress.add_task("SQL COPY to profiles hypertable...", total=len(dataframes))
            for file in files:
                file = file.replace(tmp_folder, tmp_folder_db)
                self.sql_copy_csv(file, "profiles")
                progress.advance(task1, advance=1)

        if disable_triggers:
            self.enable_all_triggers()

        with Progress() as progress:
            task1 = progress.add_task("remove temp files...", total=len(dataframes))
            for file in files:
                os.remove(file)
                progress.advance(task1, advance=1)

        rich.print("[magenta]Inserting all via SQL COPY took %.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            rm_remote_files(self.host, files)

    def inject_to_detections(self, df, max_rows=100000, disable_triggers=False, tmp_folder="/tmp/sta_db_copy/data",
                             tmp_folder_db="/tmp/sta_db_copy/data", usecs=False):
        """
        Inject all data in df into the timeseries table via SQL copy
        """

        init = time.time()
        os.makedirs(tmp_folder, exist_ok=True)
        os.chown(tmp_folder, os.getuid(), os.getgid())

        rich.print("Splitting input dataframe into smaller ones")
        rows = int(max_rows)
        dataframes = slice_dataframes(df, max_rows=rows)
        files = self.dataframes_to_detections_csv(dataframes, tmp_folder, usecs=usecs)
        rich.print("Generating all files took %0.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            t = time.time()
            rich.print("rsync files to remote server...", end="")
            rsync_files(self.host, tmp_folder, files)
            rich.print(f"[green]done![/green] took {time.time() - t:.02f} s")

        if disable_triggers:
            self.disable_all_triggers()

        with Progress() as progress:
            task1 = progress.add_task("SQL COPY to profiles hypertable...", total=len(dataframes))
            for file in files:
                file = file.replace(tmp_folder, tmp_folder_db)
                self.sql_copy_csv(file, "detections")
                progress.advance(task1, advance=1)

        if disable_triggers:
            self.enable_all_triggers()

        with Progress() as progress:
            task1 = progress.add_task("remove temp files...", total=len(dataframes))
            for file in files:
                os.remove(file)
                progress.advance(task1, advance=1)

        rich.print("[magenta]Inserting all detections via SQL COPY took %.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            rm_remote_files(self.host, files)

    # TODO: Merge inject_to_files, inject_to_inference, inject_to_observations into a single function!
    def inject_to_files(self, df, max_rows=10000, disable_triggers=False, tmp_folder="/tmp/sta_db_copy/data",
                        tmp_folder_db="/tmp/sta_db_copy/data", usecs=False):
        """
        Inject all data in df into the timeseries table via SQL copy. This function expects a DataFrame with the
        timestamp as index and the following columns:
            results (file URL), datastream_id, foi_id and parameters (optional dict with additional parameters)

        """
        init = time.time()
        os.makedirs(tmp_folder, exist_ok=True)
        os.chown(tmp_folder, os.getuid(), os.getgid())

        rich.print("Splitting input dataframe into smaller ones")
        rows = int(max_rows)
        dataframes = slice_dataframes(df, max_rows=rows)
        files = self.dataframes_to_files_csv(dataframes, tmp_folder, usecs=usecs)
        rich.print("Generating all files took %0.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            t = time.time()
            rich.print("rsync files to remote server...", end="")
            rsync_files(self.host, tmp_folder, files)
            rich.print(f"[green]done![/green] took {time.time() - t:.02f} s")

        with Progress() as progress:
            task1 = progress.add_task("SQL COPY to OBSERVATIONS ...", total=len(dataframes))
            for file in files:
                file = file.replace(tmp_folder, tmp_folder_db)
                self.sql_copy_csv(file, "OBSERVATIONS")
                progress.advance(task1, advance=1)

        with Progress() as progress:
            task1 = progress.add_task("remove temp files...", total=len(dataframes))
            for file in files:
                os.remove(file)
                progress.advance(task1, advance=1)

        rich.print("[magenta]Inserting all files via SQL COPY took %.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            rm_remote_files(self.host, files)

        # Update OBSERVATIONs count
        self.update_observations_id_seq()

    def inject_to_json(self, df, max_rows=10000, tmp_folder="/tmp/sta_db_copy/data",
                            tmp_folder_db="/tmp/sta_db_copy/data", usecs=False):
        """
        Inject all data in df into the timeseries table via SQL copy
        """

        init = time.time()
        os.makedirs(tmp_folder, exist_ok=True)
        os.chown(tmp_folder, os.getuid(), os.getgid())

        rich.print("Splitting input dataframe into smaller ones")
        rows = int(max_rows)
        dataframes = slice_dataframes(df, max_rows=rows)
        files = self.dataframes_to_json_csv(dataframes, tmp_folder, usecs=usecs)
        rich.print("Generating all files took %0.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            t = time.time()
            rich.print("rsync files to remote server...", end="")
            rsync_files(self.host, tmp_folder, files)
            rich.print(f"[green]done![/green] took {time.time() - t:.02f} s")

        with Progress() as progress:
            task1 = progress.add_task("SQL COPY to OBSERVATIONS ...", total=len(dataframes))
            for file in files:
                file = file.replace(tmp_folder, tmp_folder_db)
                self.sql_copy_csv(file, "OBSERVATIONS")
                progress.advance(task1, advance=1)

        with Progress() as progress:
            task1 = progress.add_task("remove temp files...", total=len(dataframes))
            for file in files:
                os.remove(file)
                progress.advance(task1, advance=1)

        rich.print("[magenta]Inserting all json via SQL COPY took %.02f seconds" % (time.time() - init))

        if self.host != "localhost" and self.host != "127.0.0.1":
            rm_remote_files(self.host, files)

        # Update OBSERVATIONs count
        self.update_observations_id_seq()

    def inject_to_observations(self, df: pd.DataFrame, datastreams: dict,  foi_id: int, avg_period: str,
                               max_rows=10000, disable_triggers=False, tmp_folder="/tmp/sta_db_copy/data",
                               tmp_folder_db="/tmp/sta_db_copy/data", profile=False):
        """
        Injects all data in a dataframe using SQL copy.
        """
        init = time.time()
        os.makedirs(tmp_folder, exist_ok=True)
        os.chown(tmp_folder, os.getuid(), os.getgid())

        rich.print("Splitting input dataframe into smaller ones")
        rows = int(max_rows / len(datastreams))
        dataframes = slice_dataframes(df, max_rows=rows)

        files = self.dataframes_to_observations_csv(dataframes, datastreams, tmp_folder, foi_id, avg_period=avg_period,
                                                    profile=profile)
        rich.print(f"Generating all files took {time.time() - init:0.02f} seconds")

        if self.host != "localhost" and self.host != "127.0.0.1":
            t = time.time()
            rich.print("rsync files to remote server...", end="")
            rsync_files(self.host, tmp_folder, files)
            rich.print(f"[green]done![/green] took {time.time() - t:.02f} s")

        with Progress() as progress:
            task1 = progress.add_task("SQL COPY to OBSERVATIONS table...", total=len(dataframes))
            for file in files:
                # convert from local to remote path
                file = file.replace(tmp_folder, tmp_folder_db)
                self.sql_copy_csv(file, "OBSERVATIONS")
                progress.advance(task1, advance=1)

        rich.print("Forcing PostgreSQL to update Observation ID...")
        self.exec_query("select setval('\"OBSERVATIONS_ID_seq\"', (select max(\"ID\") from \"OBSERVATIONS\") );")

        with Progress() as progress:
            task1 = progress.add_task("remove temp files...", total=len(dataframes))
            for file in files:
                os.remove(file)
                progress.advance(task1, advance=1)

        if self.host != "localhost" and self.host != "127.0.0.1":
            rm_remote_files(self.host, files)

        # Update OBSERVATION count
        self.update_observations_id_seq()

    def dataframes_to_observations_csv(self, dataframes: list, column_mapper: dict, folder: str, feature_id: int,
                                       avg_period: str = "", profile=False):
        """
        Write dataframes into local csv files ready for sql copy following the syntax in table OBSERVATIONS
        """
        files = []
        i = 0
        with Progress() as progress:
            task = progress.add_task("converting dataframes to OBSERVATIONS csv", total=len(dataframes))
            for dataframe in dataframes:
                progress.advance(task, 1)
                file = os.path.join(folder, f"observations_copy_{i:04d}.csv")
                i += 1
                self.format_csv_sta(dataframe, column_mapper, file, feature_id, avg_period=avg_period, profile=profile)
                files.append(file)

        return files

    def dataframes_to_timeseries_csv(self, dataframes: list, column_mapper: dict, folder: str, usecs=False):
        """
        Write dataframes into local csv files ready for sql copy following the syntax in table OBSERVATIONS
        """
        i = 0
        files = []
        with Progress() as progress:
            task = progress.add_task("converting data to timeseries csv", total=len(dataframes))
            for dataframe in dataframes:
                progress.advance(task, 1)
                file = os.path.join(folder, f"timeseries_copy_{i:04d}.csv")
                i += 1
                rich.print(f"format timeseries CSV {i:04d} of {len(dataframes)}")
                self.format_timeseries_csv(dataframe, column_mapper, file, usecs=usecs)
                files.append(file)
        return files

    def dataframes_to_detections_csv(self, dataframes: list, folder: str, usecs=False):
        """
        Write dataframes into local csv files ready for sql copy following the syntax in table OBSERVATIONS
        """
        i = 0
        files = []
        with Progress() as progress:
            task = progress.add_task("converting data to detections csv", total=len(dataframes))
            for dataframe in dataframes:
                progress.advance(task, 1)
                file = os.path.join(folder, f"timeseries_copy_{i:04d}.csv")
                i += 1
                rich.print(f"format timeseries CSV {i:04d} of {len(dataframes)}")
                self.format_detections_csv(dataframe, file, usecs=usecs)
                files.append(file)
        return files

    def dataframes_to_files_csv(self, dataframes: list, folder, usecs=False):
        i = 0
        files = []
        with Progress() as progress:
            task = progress.add_task("converting data to 'files' csv", total=len(dataframes))
            for dataframe in dataframes:
                progress.advance(task, 1)
                file = os.path.join(folder, f"files_copy_{i:04d}.csv")
                i += 1
                rich.print(f"format timeseries CSV {i:04d} of {len(dataframes)}")
                self.format_files_csv(dataframe, file, usecs=usecs)
                files.append(file)
        return files

    def dataframes_to_json_csv(self, dataframes: list, folder, usecs=False):
        i = 0
        files = []
        with Progress() as progress:
            task = progress.add_task("converting data to 'json' csv", total=len(dataframes))
            for dataframe in dataframes:
                progress.advance(task, 1)
                file = os.path.join(folder, f"files_copy_{i:04d}.csv")
                i += 1
                rich.print(f"format timeseries CSV {i:04d} of {len(dataframes)}")
                self.format_json_csv(dataframe, file, usecs=usecs)
                files.append(file)
        return files

    def dataframes_to_profile_csv(self, dataframes: list, column_mapper: dict, folder: str):
        """
        Write dataframes into local csv files ready for sql copy following the syntax in table OBSERVATIONS
        """
        i = 0
        files = []
        with Progress() as progress:
            task = progress.add_task("converting data to profiles csv", total=len(dataframes))
            for dataframe in dataframes:
                progress.advance(task, 1)
                file = os.path.join(folder, f"profile_copy_{i:04d}.csv")
                i += 1
                rich.print(f"format profile CSV {i:04d} of {len(dataframes)}")
                self.format_profile_csv(dataframe, column_mapper, file)
                files.append(file)
        return files

    def format_csv_sta(self, df_in, column_mapper, filename, feature_id, avg_period: str = "", profile=False):
        """
        Takes a dataframe and arranges it accordingly to the OBSERVATIONS table from a SensorThings API, preparing the
        data to be inserted by a COPY statement
        :param df_in: input dataframe
        :param column_mapper: structure that maps datastreams with dataframe columns
        :param filename: name of the file to be generated
        :param feature_id: ID of the FeatureOfInterst
        :param avg_period: if set, the phenomenon time end will be timestamp + avg_period to generate a timerange.
                           used in averaged data.
        """
        init = False
        if self.__last_observation_index < 0:  # not initialized
            self.__last_observation_index = self.get_last_observation_id()

        for colname, datastream_id in column_mapper.items():
            if colname not in df_in.columns:
                continue

            df = df_in.copy(deep=True)
            quality_control = False
            stdev = False
            keep = ["timestamp", colname]
            if colname + "_QC" in df_in.columns:
                quality_control = True
                keep += [colname + "_QC"]

            if colname + "_std" in df_in.columns:
                stdev = True
                keep += [colname + "_std"]

            if profile:
                keep += ["depth"]

            df["timestamp"] = df.index.values
            for c in df.columns:
                if c not in keep:
                    del df[c]

            if df.empty:
                rich.print(f"[yellow]Got empty dataframe for {colname}")
                continue

            df["PHENOMENON_TIME_START"] = np.datetime_as_string(df["timestamp"], unit="s", timezone="UTC")

            if avg_period:  # if we have the average period
                df["PHENOMENON_TIME_END"] = np.datetime_as_string(df["timestamp"] + pd.to_timedelta(avg_period),
                                                                  unit="s", timezone="UTC")
            else:
                df["PHENOMENON_TIME_END"] = df["PHENOMENON_TIME_START"]
            df["RESULT_TIME"] = df["PHENOMENON_TIME_START"]
            df["RESULT_TYPE"] = 0
            df["RESULT_NUMBER"] = df[colname]
            df["RESULT_BOOLEAN"] = np.nan
            df["RESULT_JSON"] = np.nan
            df["RESULT_STRING"] = df[colname].astype(str)
            df["RESULT_QUALITY"] = "{\"qc_flag\": 2}"
            df["VALID_TIME_START"] = np.nan
            df["VALID_TIME_END"] = np.nan
            if profile:
                df["PARAMETERS"] = ""  # in case of profile we need to add depth as parameter
            else:
                df["PARAMETERS"] = np.nan
            df["DATASTREAM_ID"] = datastream_id
            df["FEATURE_ID"] = feature_id
            df["ID"] = np.arange(0, len(df.index.values), dtype=int) + self.__last_observation_index + 1
            self.__last_observation_index = df["ID"].values[-1]

            # Quality control and standard deviation
            for i in range(0, len(df.index.values)):
                qc_value = np.nan
                std_value = np.nan
                if stdev:
                    std_value = df[colname + "_std"].values[i]
                if qc_value:
                    qc_value = df[colname + "_QC"].values[i]
                if not np.isnan(qc_value) and stdev and not np.isnan(std_value):
                    # If we have QC and STD, put both
                    df["RESULT_QUALITY"].values[i] = "{\"qc_flag\": %d, \"stdev\": %f}" % \
                                                     (df[colname + "_QC"].values[i], df[colname + "_std"].values[i])
                elif not np.isnan(qc_value):
                    # If we only have QC, put it
                    if df[colname + "_QC"].values[i]:
                        df["RESULT_QUALITY"].values[i] = "{\"qc_flag\": %d}" % (df[colname + "_QC"].values[i])
                elif not np.isnan(std_value):
                    # If we only have STD, put it
                    if df[colname + "_std"].values[i]:
                        df["RESULT_QUALITY"].values[i] = "{\"stdev\": %d}" % (df[colname + "_std"].values[i])

            if profile:
                df["depth"] = df["depth"].values.astype(float).round(2)  # force conversion to integer
                for i in range(0, len(df.index.values)):
                    df["PARAMETERS"].values[i] = "{\"depth\": %.02f}" % (df["depth"].values[i])

            del df["timestamp"]
            del df[colname]
            if quality_control:
                del df[colname + "_QC"]
            if stdev:
                del df[colname + "_std"]
            if profile:
                del df["depth"]

            if not init:
                df_final = df
                init = True
            else:
                df_final = pd.concat([df_final, df])

        df_final.to_csv(filename, index=False)

    @staticmethod
    def harmonize_quality_control(df):
        """
        Ensures that all QC columns are in upper case, like TEMP_QC
        """
        for col in df.columns:
            if col.endswith("_QC"):
                df = df.rename(columns={col: col.replace("_qc", "_QC")})
        return df

    def format_timeseries_csv(self, df_in, column_mapper, filename, usecs=False):
        """
        Format from a regular dataframe to a Dataframe ready to be copied into a TimescaleDB simple table
        :param df_in:
        :param column_mapper:
        :return:
        """
        df_final = None
        init = False
        for colname, datastream_id in column_mapper.items():
            if colname not in df_in.columns:  # if column is not in dataset, just ignore this datastream
                continue
            df = df_in.copy(deep=True)
            df = self.harmonize_quality_control(df)

            if colname + "_QC" not in df.columns:
                raise ValueError(f"Variable {colname} does not have QC column")

            keep = ["timestamp", colname, colname + "_QC"]
            df["timestamp"] = df.index.values
            df = df[keep]
            df = df.dropna(subset=[colname], how='all')  # drop NaNs in column name
            if not usecs:
                df["time"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            else:
                df["time"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            df["datastream_id"] = datastream_id
            df = df.set_index("time")
            df = df.rename(columns={colname: "value", colname + "_QC": "qc_flag", colname + "_QC": "qc_flag"})
            df["qc_flag"] = df["qc_flag"].values.astype(int)
            del df["timestamp"]
            if not init:
                df_final = df
                init = True
            else:
                df_final = pd.concat([df_final, df])
        df_final.to_csv(filename)
        del df_final
        gc.collect()

    def format_profile_csv(self, df_in, column_mapper, filename):
        """
        Format from a regular dataframe to a Dataframe ready to be copied into a TimescaleDB simple table
        :param df_in:
        :param column_mapper:
        :return:
        """
        df_final = None
        init = False
        for colname, datastream_id in column_mapper.items():
            if colname not in df_in.columns:  # if column is not in dataset, just ignore this datastream
                continue
            df = df_in.copy(deep=True)
            df = self.harmonize_quality_control(df)
            keep = ["timestamp", "depth", colname, colname + "_QC"]
            df["timestamp"] = df.index.values
            df = df[keep]
            df = df.dropna(subset=[colname], how='all')  # drop NaNs in column name
            df["time"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            df["datastream_id"] = datastream_id
            df = df.set_index("time")
            df = df.rename(columns={colname: "value", colname + "_QC": "qc_flag"})
            df["qc_flag"] = df["qc_flag"].values.astype(int)
            del df["timestamp"]
            if not init:
                df_final = df
                init = True
            else:
                df_final = pd.concat([df_final, df])
        df_final.to_csv(filename)
        del df_final
        gc.collect()

    def format_detections_csv(self, df_in, filename, usecs=False):
        """
        Format from a regular dataframe to a Dataframe ready to be copied into a TimescaleDB simple table
        :param df_in:
        :param filename:
        :return:
        """
        df = df_in.copy(deep=True)
        df = df.rename(columns={"results": "value"})
        df["timestamp"] = df.index.values
        df = df[["timestamp", "value", "datastream_id"]]
        df = df.dropna(subset=["value"], how='all')  # drop NaNs in column name

        if usecs:
            df["time"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        else:
            df["time"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

        df = df.set_index("time")
        df["value"] = df["value"].values.astype(int)
        del df["timestamp"]
        df.to_csv(filename)
        del df
        gc.collect()

    def format_files_csv(self, df_in, filename, usecs=False):
        """
        Takes a dataframe and arranges it accordingly to the OBSERVATIONS table from a SensorThings API, preparing the
        data to be inserted by a COPY statement
        :param df_in: input dataframe
        :param column_mapper: structure that maps datastreams with dataframe columns
        :param filename: name of the file to be generated
        :param feature_id: ID of the FeatureOfInterst
        :param avg_period: if set, the phenomenon time end will be timestamp + avg_period to generate a timerange.
                           used in averaged data.
        """
        if self.__last_observation_index < 0:  # not initialized
            self.__last_observation_index = self.get_last_observation_id()

        df = df_in.copy(deep=True)
        df = df.dropna(subset=["results"], how='all')  # drop NaNs in column name

        if usecs:
            df["PHENOMENON_TIME_START"] = np.datetime_as_string(df.index.values, unit="us", timezone="UTC")
        else:
            df["PHENOMENON_TIME_START"] = np.datetime_as_string(df.index.values, unit="s", timezone="UTC")

        if "timeEnd" in df.columns:  # if we have the average period
            if usecs:
                df["PHENOMENON_TIME_END"] = np.datetime_as_string(df["timeEnd"], unit="us", timezone="UTC")
            else:
                df["PHENOMENON_TIME_END"] = np.datetime_as_string(df["timeEnd"], unit="s", timezone="UTC")
        else:
            df["PHENOMENON_TIME_END"] = df["PHENOMENON_TIME_START"]
        df["RESULT_TIME"] = df["PHENOMENON_TIME_START"]
        df["RESULT_TYPE"] = 3  # Strings are type 3 (2 is json)
        df["RESULT_NUMBER"] = np.nan
        df["RESULT_BOOLEAN"] = np.nan
        df["RESULT_JSON"] = np.nan
        df["RESULT_STRING"] = df["results"].astype(str)
        df["RESULT_QUALITY"] = np.nan
        df["VALID_TIME_START"] = np.nan
        df["VALID_TIME_END"] = np.nan
        if "parameters" in df.columns:
            values = []
            for v in df["parameters"].values:
                # Force JSON structures to be like: "{\"key\": \"value\"}"
                v = v.replace("'", "\"")
                values.append(v)

            df["PARAMETERS"] = values
        else:
            df["PARAMETERS"] = np.nan
        df["DATASTREAM_ID"] = df["datastream_id"]
        df["FEATURE_ID"] = df["foi_id"]
        df["ID"] = np.arange(0, len(df.index.values), dtype=int) + self.__last_observation_index + 1
        self.__last_observation_index = df["ID"].values[-1]

        # Keep only columns as in the Database

        df = df[["PHENOMENON_TIME_START", "PHENOMENON_TIME_END", "RESULT_TIME", "RESULT_TYPE", "RESULT_NUMBER",
                 "RESULT_BOOLEAN", "RESULT_JSON", "RESULT_STRING", "RESULT_QUALITY", "VALID_TIME_START",
                 "VALID_TIME_END", "PARAMETERS", "DATASTREAM_ID", "FEATURE_ID", "ID"]]
        df.to_csv(filename, index=False)

    def format_json_csv(self, df_in, filename, usecs=False):
        """
        Takes a dataframe and arranges it accordingly to the OBSERVATIONS table from a SensorThings API, preparing the
        data to be inserted by a COPY statement
        :param df_in: input dataframe
        :param column_mapper: structure that maps datastreams with dataframe columns
        :param filename: name of the file to be generated
        :param feature_id: ID of the FeatureOfInterst
        :param avg_period: if set, the phenomenon time end will be timestamp + avg_period to generate a timerange.
                           used in averaged data.
        """
        if self.__last_observation_index < 0:  # not initialized
            self.__last_observation_index = self.get_last_observation_id()

        df = df_in.copy(deep=True)
        df = df.dropna(subset=["results"], how='all')  # drop NaNs in column name

        if usecs:
            df["PHENOMENON_TIME_START"] = np.datetime_as_string(df.index.values, unit="us", timezone="UTC")
        else:
            df["PHENOMENON_TIME_START"] = np.datetime_as_string(df.index.values, unit="s", timezone="UTC")

        if "timeEnd" in df.columns:  # if we have the average period
            df["PHENOMENON_TIME_END"] = np.datetime_as_string(df["timeEnd"], unit="s", timezone="UTC")
        else:
            df["PHENOMENON_TIME_END"] = df["PHENOMENON_TIME_START"]
        df["RESULT_TIME"] = df["PHENOMENON_TIME_START"]
        df["RESULT_TYPE"] = 2  # Strings are type 3 (2 is json)
        df["RESULT_NUMBER"] = np.nan
        df["RESULT_BOOLEAN"] = np.nan
        values = []
        for v in df["results"].values:
            # Force JSON structures to be like: "{\"key\": \"value\"}"
            v = v.replace("'", "\"")
            values.append(v)

        df["RESULT_JSON"] = values
        df["RESULT_STRING"] = np.nan
        df["RESULT_QUALITY"] = np.nan
        df["VALID_TIME_START"] = np.nan
        df["VALID_TIME_END"] = np.nan
        if "parameters" in df.columns:
            values = []
            for v in df["parameters"].values:
                # Force JSON structures to be like: "{\"key\": \"value\"}"
                v = v.replace("'", "\"")
                values.append(v)

            df["PARAMETERS"] = values
        else:
            df["PARAMETERS"] = np.nan
        df["DATASTREAM_ID"] = df["datastream_id"]
        df["FEATURE_ID"] = df["foi_id"]
        df["ID"] = np.arange(0, len(df.index.values), dtype=int) + self.__last_observation_index + 1
        self.__last_observation_index = df["ID"].values[-1]

        # Keep only columns as in the Database

        df = df[["PHENOMENON_TIME_START", "PHENOMENON_TIME_END", "RESULT_TIME", "RESULT_TYPE", "RESULT_NUMBER",
                 "RESULT_BOOLEAN", "RESULT_JSON", "RESULT_STRING", "RESULT_QUALITY", "VALID_TIME_START",
                 "VALID_TIME_END", "PARAMETERS", "DATASTREAM_ID", "FEATURE_ID", "ID"]]
        df.to_csv(filename, index=False)

    def sql_copy_csv(self, filename, table="OBSERVATIONS", delimiter=","):
        """
        Execute a COPY query to copy from a local CSV file to a database
        :return:
        """
        query = "COPY public.\"%s\" FROM '%s' DELIMITER '%s' CSV HEADER;" % (table, filename, delimiter)
        self.exec_query(query, fetch=False)

    def get_last_observation_id(self):
        """
        Gets last observation in database
        :return:
        """
        query = "SELECT \"ID\" FROM public.\"OBSERVATIONS\" ORDER BY \"ID\" DESC LIMIT 1"
        df = self.dataframe_from_query(query)
        #  Check if the table is empty
        if df.empty:
            return 0
        return int(df["ID"].values[0])

    def get_data_type(self, datastream_id):
        """
        Returns the data type of a datastream
        :param datastream_id: (int) id of the datastream
        :returns: (data_type: str, average: bool)
        """
        props = self.datastream_properties[datastream_id]
        data_type = props["dataType"]
        if "averagePeriod" in props.keys():
            average = True
        else:
            average = False
        return data_type, average

    def get_datastream_id(self, sensor: str, station: str, variable: str, data_type: str,  average: str = ""):
        """
        Returns the ID  of a datastream that matches sensor name, station and data type.
        """
        assert data_type in mmapi_data_types
        assert type(sensor) is str
        assert type(station) is str
        assert type(data_type) is str
        assert type(variable) is str
        assert type(average) is str

        if data_type in ["timeseries", "profiles"]:
            if not average:  # if not average, assume fullData
                avg = 'and ("PROPERTIES"->>\'fullData\')::boolean = true'
            else:
                avg = f'and ("PROPERTIES"->>\'fullData\')::boolean = false and "PROPERTIES"->>\'averagePeriod\' = \'{average}\''
        else:
            avg = ""  # for files, detections and json it makes no sense to flag the fullData

        query = f'''select "ID" from "DATASTREAMS" where
         "SENSOR_ID" = (select "ID" from "SENSORS" where "NAME" = \'{sensor}\') 
         and "THING_ID" = (select "ID" from "THINGS" where "NAME" = \'{station}\')
         and "OBS_PROPERTY_ID" = (select "ID" from "OBS_PROPERTIES" where "NAME" = \'{variable}\')
         and "PROPERTIES"->>\'dataType\' = \'{data_type}\'
         {avg}
         ;'''
        return self.value_from_query(query)

    def drop_all(self):
        """
        Deletes ALL documents from ALL tables, USE WITH CAUTION!
        """
        tables = ["profiles", "detections", "timeseries", "OBSERVATIONS", "DATASTREAMS", "SENSORS", "FEATURES", "THINGS",
                  "LOCATIONS", "HIST_LOCATIONS"]
        for table in tables:
            self.exec_query(f"delete from \"{table}\";", fetch=False)

    def add_unique_name_constraints(self):
        __unique_list = ["DATASTREAMS", "SENSORS", "THINGS", "FEATURES", "OBS_PROPERTIES"]
        for table in __unique_list:
            # First, check if it already exists
            constraint_name = table.lower() + "_unique_name"
            constraint_query = f"alter table \"{table}\" add constraint {constraint_name} unique (\"NAME\")"
            self.add_constraint(constraint_name, constraint_query)

    def add_unique_observation_index(self):
        """
        Adds unique index to OBSERVATIONS table to avoid duplicated measures. Two indexes are created, one for
        regular data when parameters=null, and another one for profiles (parameters!=null).
        """
        q = ('CREATE UNIQUE INDEX observations_unique_time_datastream_idx ON "OBSERVATIONS" ("PHENOMENON_TIME_START", '
             '"DATASTREAM_ID") WHERE  "PARAMETERS" IS NULL;')
        self.add_index("observations_unique_time_datastream_idx", "OBSERVATIONS", q)
        q = ('CREATE UNIQUE INDEX observations_unique_time_datastream_param_idx ON "OBSERVATIONS" ('
             '"PHENOMENON_TIME_START", "DATASTREAM_ID", "PARAMETERS") WHERE  "PARAMETERS" IS NOT NULL;')
        self.add_index("observations_unique_time_datastream_param_idx", "OBSERVATIONS", q)

    def update_observations_id_seq(self):
        """
        Updates the sequence counter for Observations ID. This is required after bulk loading data to OBSERVATIONS
        table
        """
        self.value_from_query('select setval(\'"OBSERVATIONS_ID_seq"\', (select max("ID") from "OBSERVATIONS") );')

    def get_datastream_config(self, sensor="", station="", data_type="", average_period="", full_data=False):
        """
        returns a dataframe with the following columns:
            datastream_id, datastream_name, variable_id, variable_name, data_type, and average_period

        :param sensor: If set, get only the datastreams for this sensor
        :param data_type: get only datastreams with this data_type
        :param full_data: return only full data datastreams (not averaged)
        :param average: if set, get only the dataframes with averaged with this period or use "full" to get the full data

        :returns: DataFrame with the following columns:
            datastream_id, datastream_name, variable_id, variable_name, data_type, full_data, average_period
        """
        if sensor:
            if type(sensor) is str:
                sensor_id = self.sensor_id_name[sensor]
            elif type(sensor) is int:
                sensor_id = sensor

        if station:
            station_id = self.value_from_query(f'select "ID" from "THINGS" where "NAME" = \'{station}\';')

        query = '''
            select 
                "DATASTREAMS"."ID" as datastream_id,
                "DATASTREAMS"."NAME" as datastream_name,
                prop."NAME" as variable_name,
                prop."ID" as variable_id,
                "DATASTREAMS"."PROPERTIES"->>'dataType' as data_type,
                ("DATASTREAMS"."PROPERTIES"->>'fullData')::boolean as full_data, 
                "DATASTREAMS"."PROPERTIES"->>'averagePeriod' as average_period
            from "DATASTREAMS"
            left join (select * from "OBS_PROPERTIES") as prop	
            on prop."ID" = "DATASTREAMS"."OBS_PROPERTY_ID"
            ;
        '''
        if sensor:
            query = query.replace(";", f'where "SENSOR_ID" = {sensor_id};')
        if station:
            query = query.replace(";", f' and "THING_ID" = {station_id};')

        df = self.dataframe_from_query(query)
        # Now filter the results based on type, average or fullData
        if data_type:
            df = df[df["data_type"] == data_type]

        if average_period:
            df = df[df["average_period"] == average_period]

        if full_data:
            df = df[df["full_data"] == full_data]

        return df

    def check_data_integrity(self):
        self.timescale.check_data_in_observations()
        self.timescale.check_data_in_hypertables()

    def get_missing_data(self, df: pd.DataFrame, sensor_name: str, average: bool, data_type: str, fill_type) -> pd.DataFrame:
        """
        Takes input dataframe, compares its content with the data in the database, and returns a dataframe with the
        data not already in the database. The comparison is made by timestamp and the data type.
        :param df: input dataframe
        :param sensor_name: name of the sensor
        :param average: bool to mark as average
        :param data_type:
        :param fill_type: 'direct' to directly fill the missing data or 'hourly' to
        :return: dataframe with the missing data
        """

        assert_type(df, pd.DataFrame)
        assert_type(sensor_name, str)
        assert_type(data_type, str)
        assert fill_type in ["direct", "hourly"], f"Expected 'direct' or 'hourly', got '{fill_type}'"
        rows = len(df)
        self.info("Processing dataframe to select missing data in the database")
        df = df.sort_index(ascending=True)
        tstart = df.index.values[0]
        tend = df.index.values[-1]
        self.debug(f"Getting timestamps for '{sensor_name}' type '{data_type}' from '{tstart}' to '{tend}'")

        datastream_ids = self.list_from_query(f"""
        select "ID" from "DATASTREAMS" where "SENSOR_ID" = 
            (select "ID" from "SENSORS" where "NAME" = '{sensor_name}')
            and "PROPERTIES"->>'dataType' = '{data_type}'
            ;""")
        datastream_ids = ", ".join([str(d) for d in datastream_ids])
        if not average and data_type in ["timeseries", "profiles", "detections"]:
            # data_type is the same as table name
            query = f"""
                select timestamp from {data_type} where datastream_id in ({datastream_ids}) and timestamp between
                '{tstart}' and '{tend}' order by timestamp asc;
            """
        else:
            # go to the generic OBSERVATIONS table
            query = f"""
                select distinct "PHENOMENON_TIME_START" from "OBSERVATIONS" where "DATASTREAM_ID" in ({datastream_ids}) and
                "PHENOMENON_TIME_START" between '{tstart}' and '{tend}' order by "PHENOMENON_TIME_START" asc;
            """

        times = self.dataframe_from_query(query)
        if "PHENOMENON_TIME_START" in times.columns:
            times = times.rename(columns={"PHENOMENON_TIME_START": "timestamp"})
        times["timestamp"] = pd.to_datetime(times["timestamp"])
        times = times.set_index("timestamp")

        if fill_type == "direct":
            df_out = detect_data_gaps_direct(df, times)
        else:
            df_out = detect_data_gaps_by_period(df, times, "1h")  # check data gaps by period

        self.info(f"get_missing_data: Detected {len(df_out)} rows missing, {100*(len(df_out)/rows):.02f} %%")
        return df_out

#!/usr/bin/env python3
"""

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 21/9/23
"""

import os
import logging
from logging.handlers import TimedRotatingFileHandler
import jsonschema
import pandas as pd
import rich
import requests
import subprocess
import socket
import hashlib
import netCDF4

# Color codes
GRN = "\x1B[32m"
RST = "\033[0m"
BLU = "\x1B[34m"
YEL = "\x1B[33m"
RED = "\x1B[31m"
MAG = "\x1B[35m"
CYN = "\x1B[36m"
WHT = "\x1B[37m"
NRM = "\x1B[0m"
PRL = "\033[95m"

colors = [GRN, RST, BLU, YEL, RED, MAG, CYN, WHT, NRM, PRL, RST]

qc_flags = {
    "good": 1,
    "not_applied": 2,
    "suspicious": 3,
    "bad": 4,
    "missing": 9
}


def setup_log(name, path="log", log_level="debug"):
    """
    Setups the logging module
    :param name: log name (.log will be appended)
    :param path: where the logs will be stored
    :param log_level: log level as string, it can be "debug, "info", "warning" and "error"
    """

    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Check arguments
    if len(name) < 1 or len(path) < 1:
        raise ValueError("name \"%s\" not valid", name)
    elif len(path) < 1:
        raise ValueError("name \"%s\" not valid", name)

    # Convert to logging level
    if log_level == 'debug':
        level = logging.DEBUG
    elif log_level == 'info':
        level = logging.INFO
    elif log_level == 'warning':
        level = logging.WARNING
    elif log_level == 'error':
        level = logging.ERROR
    else:
        raise ValueError("log level \"%s\" not valid" % log_level)

    if not os.path.exists(path):
        os.makedirs(path)

    filename = os.path.join(path, name)
    if not filename.endswith(".log"):
        filename += ".log"
    print("Creating log", filename)
    print("name", name)

    logger = logging.getLogger()
    logger.setLevel(level)
    log_formatter = logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)-7s: %(message)s',
                                      datefmt='%Y/%m/%d %H:%M:%S')
    handler = TimedRotatingFileHandler(filename, when="midnight", interval=1, backupCount=7)
    handler.setFormatter(log_formatter)
    logger.addHandler(handler)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(log_formatter)
    logger.addHandler(consoleHandler)

    logger.info("")
    logger.info(f"===== {name} =====")

    return logger


def ask_user_input(text: str) -> bool:
    """
    Ask the user a yes/no question and return True for yes, False for no.
    Keeps asking until the user enters a valid response.
    """
    prompt = f"{text} (y/n)"
    while True:
        rich.print(prompt)
        response = input().strip().lower()

        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False

        rich.print("[yellow]Please answer with 'y' or 'n'.")


def file_list(dir_name) -> list:
    """ create a list of file and sub directories names in the given directory"""
    assert os.path.isdir(dir_name), f"{dir_name} is not a directory!"
    list_of_files = os.listdir(dir_name)
    all_files = list()
    for entry in list_of_files:
        full_path = os.path.join(dir_name, entry)
        if os.path.isdir(full_path):
            all_files = all_files + file_list(full_path)
        else:
            all_files.append(full_path)
    return all_files


def dir_list(dir_name) -> list:
    """ create a list of file and sub directories names in the given directory"""
    assert os.path.isdir(dir_name), f"{dir_name} is not a directory!"
    sublist = os.listdir(dir_name)
    all_files = list()  # all files and folders
    for entry in sublist:
        full_path = os.path.join(dir_name, entry)
        all_files.append(full_path)
        if os.path.isfile(entry):
            continue
        if os.path.isdir(full_path):
            all_files = all_files + dir_list(full_path)
    all_files = list(reversed(all_files))
    return all_files

def timestamp_from_filename(filename)-> pd.Timestamp:
    """
    Tries to detect a timestamp from a filename based on common patterns
    :param filename:
    :return: Datetime object
    """
    # Keep only the basename
    filename = os.path.basename(filename)
    # Make sure to convert all spaces and underscores to hyphens
    filename = filename.replace("_", "-").replace(" ", "-")

    patterns = [
        ("%Y%m%d-%H%M%S.%f", "20201231-235959.123456"),  # microsecond
        ("%Y%m%d-%H%M%S.%f", "20201231-235959.123"),  # millisecond
        ("%Y%m%d-%H%M%S", "20201231-235959"),
        ("%Y-%m-%dT%H:%M:%S", "2020-12-31T23:59:59"),
    ]
    for pattern, example in patterns:
        try:
            f = filename[:len(example)]  # get only the beginning
            return pd.to_datetime(f, format=pattern)
        except ValueError:
            continue
    raise ValueError(f"Could not convert {filename} to timestamp")


class LoggerSuperclass:
    def __init__(self, logger: logging.Logger, name: str, colour=NRM):
        """
        SuperClass that defines logging as class methods adding a heading name
        """
        self.logger_name = name
        self.logger = logger
        if not logger:
            self.logger = logging  # if not assign the generic module
        self.log_colour = colour

    def warning(self, *args):
        mystr = YEL + "[%s] " % self.logger_name + str(*args) + RST
        self.logger.warning(mystr)

    def error(self, *args, exception: any = False):
        mystr = "[%s] " % self.logger_name + str(*args)
        self.logger.error(RED + mystr + RST)
        if exception:
            if isinstance(exception(), Exception):
                raise exception(mystr)
            else:
                raise ValueError(mystr)


    def debug(self, *args):
        mystr = self.log_colour + "[%s] " % self.logger_name + str(*args) + RST
        self.logger.debug(mystr)

    def info(self, *args):
        mystr = self.log_colour + "[%s] " % self.logger_name + str(*args) + RST
        self.logger.info(mystr)

    def setLevel(self, level):
        self.logger.setLevel(level)


def reverse_dictionary(data):
    """
    Takes a dictionary and reverses key-value pairs
    :param data: any dict
    :return: reversed dictionary
    """
    return {value: key for key, value in data.items()}


def normalize_string(instring, lower_case=False):
    """
    This function takes a string and normalizes by replacing forbidden chars by underscores.The following chars
    will be replaced: : @ $ % & / + , ; and whitespace
    :param instring: input string
    :return: normalized string
    """
    forbidden_chars = [":", "@", "$", "%", "&", "/", "+", ",", ";", " ", "-"]
    outstring = instring
    for char in forbidden_chars:
        outstring = outstring.replace(char, "_")
    if lower_case:
        outstring = outstring.lower()
    return outstring


def dataframe_to_dict(df, key, value):
    """
    Takes two columns of a dataframe and converts it to a dictionary
    :param df: input dataframe
    :param key: column name that will be the key
    :param value: column name that will be the value
    :return: dict
    """

    keys = df[key]
    values = df[value]
    d = {}
    for i in range(len(keys)):
        d[keys[i]] = values[i]
    return d


def reverse_dictionary(data):
    """
    Takes a dictionary and reverses key-value pairs
    :param data: any dict
    :return: reversed dictionary
    """
    return {value: key for key, value in data.items()}


def run_over_ssh(host, cmd, fail_exit=False):
    if host == "localhost" or host == os.uname().nodename:
        return run_subprocess(cmd, fail_exit=fail_exit)
    else:
        if type(cmd) is list:  # convert list to str
            cmd = " ".join(cmd)
        cmd = ["ssh", host, cmd]
        return run_subprocess(cmd, fail_exit=fail_exit)


def run_subprocess(cmd, fail_exit=True):
    """
    Runs a command as a subprocess. If the process retunrs 0 returns True. Otherwise prints stderr and stdout and returns False
    :param cmd: command (list or string)
    :return: True/False
    """
    assert (type(cmd) is list or type(cmd) is str)
    if type(cmd) is list:
        cmd_list = cmd
    else:
        cmd_list = cmd.split(" ")
    cmd_list = [part for part in cmd_list if part]  # avoid empty strings
    proc = subprocess.run(cmd_list, capture_output=True)
    stdout = proc.stdout.decode()
    if proc.returncode != 0:
        rich.print(f"\n[red]ERROR while running command '{cmd}'")
        if proc.stdout:
            rich.print(f"subprocess stdout:")
            rich.print(f">[bright_black]    {stdout}")
        if proc.stderr:
            rich.print(f"subprocess stderr:")
            rich.print(f">[bright_black] {proc.stderr.decode()}")

        if fail_exit:
            raise ValueError(f"command failed: '{cmd_list}'")
    return stdout


def __get_field(doc: dict, key: str):
    if "/" not in key:
        if key in doc.keys():
            return True, doc[key]
        else:
            return False, None
    else:
        keys = key.split("/")
        if keys[0] not in doc.keys():
            return False, None
        return __get_field(doc[keys[0]], "/".join(keys[1:]))


def load_fields_from_dict(doc: dict, fields: list, rename: dict = {}) -> dict:
    """
    Takes a document from metadata database and returns all fields in list. If a field in the list is not there, ignore it:

        doc = {"a": 1, "b": 1  "c": 1} and fields = ["a", "b", "d"]
            return {"a": 1, "b": 1 }

    Nested fields are described with / e.g. {"parent": {"son": 1}} -> "parent/son"

    With rename  the output fields can be renamed

    """
    assert type(doc) is dict
    assert type(fields) is list
    results = {}
    for field in fields:
        success, result = __get_field(doc, field)
        if success:
            results[field] = result

    for key, value in rename.items():
        if key in results.keys():
            results[value] = results.pop(key)

    return results


def check_url(url):
    """
    Checks if a URL is reachable without downloading its contents
    """
    assert type(url) is str, f"Expected string got {type(url)}"
    try:
        response = requests.head(url)
        if response.status_code == 200:
            return True
        else:
            return False
    except requests.ConnectionError:
        return False


def download_file(url: str, output: str):
    assert_type(url,  str)
    assert_type(output, str)
    dirname = os.path.dirname(output)
    if dirname and not os.path.exists(dirname):
        os.makedirs(dirname)
    # Send a GET request to the URL
    response = requests.get(url, stream=True)

    # Check if the request was successful
    if response.status_code == 200:
        # Open the local file for writing
        with open(output, "wb") as file:
            # Write the file in chunks to avoid memory overload
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:  # Only write if the chunk is not empty
                    file.write(chunk)
    else:
        raise ValueError(f"Could not donwload file {url}, http_code = {response.status_code}")


def rsync_files(host: str, folder, files: list):
    """
    Uses rsync to copy some files to a remote folder
    """
    assert type(host) is str, "invalid type"
    assert type(folder) is str, "invalid type"
    assert type(files) is list, "invalid type"
    assert len(files) > 0 , "File list is empty!"
    if socket.gethostname() == host:
        rich.print("Using localhost!")
        run_subprocess(f"rsync -azh {' '.join(files)} {folder}")
    else:
        run_subprocess(["ssh", host, f"mkdir -p {folder} -m=777"], fail_exit=True)
        run_subprocess(f"rsync -azh {' '.join(files)} {host}:{folder}")


def rm_remote_files(host, files):
    """
    Runs remove file over ssh
    """
    assert type(host) is str, "invalid type"
    assert type(files) is list, "invalid type"
    if host == socket.gethostname():

        files = [f for f in files if os.path.exists(f)]
        if len(files) > 0:
            run_subprocess(f"rm {' '.join(files)}", fail_exit=True)
    else:
        run_subprocess(["ssh", host, f"rm {' '.join(files)}"], fail_exit=True)


def assert_dict(conf: dict, required_keys: dict, verbose=False):
    """
    Checks if all the expected keys in a dictionary are there. The expected format is field name as key and type as
    value:
        { "name": str, "importantNumber": int}

    One level of nesting is supported:
    value:
        { "someData/nestedData": str}
    expects something like
        {
        "someData": {
            "nestedData": "hi"
            }
        }

    :param conf: dict with configuration to be checked
    :param required_keys: dictionary with required keys
    :raises: AssertionError if the input does not match required_keys
    """
    for key, expected_type in required_keys.items():
        if "/" in key:
            pass
        elif key not in conf.keys():
            raise AssertionError(f"Required key \"{key}\" not found in configuration")

        # Check for nested dicts
        if "/" in key:
            parent, son = key.split("/")
            if parent not in conf.keys():
                msg =f"Required key \"{parent}\" not found!"
                if verbose:
                    rich.print(f"[red]{msg}")
                raise AssertionError(msg)

            if type(conf[parent]) != dict:
                msg = f"Value for key \"{parent}\" wrong type, expected type dict, but got {type(conf[parent])}"
                if verbose:
                    rich.print(f"[red]{msg}")
                raise AssertionError(msg)
            if son not in conf[parent].keys():
                msg =f"Required key \"{son}\" not found in configuration/{parent}"
                if verbose:
                    rich.print(f"[red]{msg}")
                raise AssertionError(msg)
            value = conf[parent][son]
        else:
            value = conf[key]

        if type(value) != expected_type:
            msg = f"Value for key \"{key}\" wrong type, expected type {expected_type}, but got '{type(value)}'"
            if verbose:
                rich.print(f"[red]{msg}")
            raise AssertionError(msg)


def validate_schema(doc: dict, schema: dict, errors=[], verbose=False) -> list:
    error_list = errors
    errors = []
    if "$id" not in schema.keys():
        raise ValueError("Schema not valid!! missing $id field")

    if verbose:
        rich.print(f"   Validating doc='{doc['#id']}' against schema {schema['$id']}")

    try:  # validate against metadata schema
        jsonschema.validate(doc, schema=schema)
    except jsonschema.ValidationError as e:
        txt = f"[red]Document='{doc['#id']}' not valid for schema '{schema['$id']}'[/red]. Cause: {e.message}"
        errors.append(txt)

    # Now apply custom rules
    if schema["$id"] == "mmm:activities":
        # Sensor deployments must be attached to a station
        if "@sensors" in doc["appliedTo"].keys() and doc["type"] == "deployment":
            if "where" not in doc.keys() or "@stations" not in doc["where"].keys():
                errors.append(f"[red]Document='{doc['#id']}' sensor deployment MUST reference a station!")

        # Stations deployments must have a position
        if "@stations" in doc["appliedTo"].keys() and doc["type"] == "deployment":
            if "where" not in doc.keys() or "position" not in doc["where"].keys():
                errors.append(f"[red]Document='{doc['#id']}' station deployment MUST have GPS coordinates!")

        # Make sure that each activity points to a single sensor/station/resource
        keys = doc["appliedTo"].keys()
        count = int("@stations" in keys) + int("@resources" in keys) + int("@sensors" in keys)
        if count != 1:
            errors.append(f"[red]Expected only ONE of @resources @station or @sensors")

    if verbose and errors:
        for e in errors:
            rich.print(e)
    error_list = error_list + errors
    return error_list


def retrieve_url(url, output="", attempts=3, timeout=5):
    """
    Tries to retrieve an URL and store its contents into output. It will try to get the URL for n attempts.
    :param url:
    :param output:
    :param attempts:
    :param timeout:
    :return:
    """
    exc = None
    success = False
    while not success and attempts > 0:
        try:
            response = requests.get(url, timeout=timeout, stream=True)
            success = True
        except Exception as e:
            exc = e
            attempts -= 1
    # Open the output file and make sure we write in binary mode

    if output and success:
        with open(output, 'wb') as fh:
            # Walk through the request response in chunks of 1024 * 1024 bytes, so 1MiB
            for chunk in response.iter_content(1024 * 1024):
                fh.write(chunk)
    elif not success:
        rich.print(f"[red]Could not retrieve URL {url}")
        raise exc


def assert_type(obj, valid_type):
    """
    Asserts that obj is of type <valid_type>
    :param obj:  any object
    :param valid_type:  any type
    """
    assert isinstance(obj, valid_type), f"Expected {valid_type}, but got {type(obj)} instead"


def assert_types(obj, valid_types: list):
    """
    Asserts that obj is of type <valid_type>
    :param obj:  any object
    :param valid_types:  list of types
    """
    assert isinstance(valid_types, list), "valid_types should be a list of types!"
    valid_string = ", ".join([str(t) for t in valid_types])
    valid_string = valid_string.replace("<class ", "").replace(">", "")
    assert type(obj) in valid_types, f"Expected on of {valid_string}, but got {type(obj)} instead"


def __get_nested_dict(d, path):
    """Get a value from a nested dict using '/' separated path."""
    keys = path.split("/")
    for key in keys:
        if not isinstance(d, dict) or key not in d:
            return None, False
        d = d[key]
    return d, True

def __set_nested_dict(d, path, value):
    """Set a value in a nested dict using '/' separated path."""
    keys = path.split("/")
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def populate_dict(src: dict, dest: dict, terms: dict):
    """
    Populate dict 'src' with values from dict 'b' based on a source/destination mapping.

    Args:
        src (dict): Source dictionary to populate.
        dest (dict): Destination dictionary to read values from.
        terms (dict): Mapping of source paths (in 'b') to destination paths (in 'a').
                      Use '/' as separator for nested keys (e.g. "nested/element").
                      Keys not found in 'b' are silently skipped.

    Example:
        src = {"a": "1", "nested": {"element": "2"}}
        terms = {"a": "newa", "nested/element": "nest/el"}
        dest = {}
        populate_args(src, dest, terms)
        # dest -> {"newa": "1", "nest": {"el": "2"}}
    """
    assert_type(dest, dict)
    assert_type(src, dict)
    assert_type(terms, dict)
    for source, destination in terms.items():
        value, found = __get_nested_dict(src, source)
        if found:
            __set_nested_dict(dest, destination, value)


def str_to_timerange(str_time_range):
    assert_type(str_time_range, str)

    if "/" not in str_time_range:
        raise ValueError(
            "Time range must be specified as start/end, e.g. 2023-01-01/2023-01-02"
        )
    start_str, end_str = str_time_range.split("/")

    start = pd.Timestamp(start_str)
    end = pd.Timestamp(end_str)

    if start.tz is None:
        start = start.tz_localize("UTC")
    if end.tz is None:
        end = end.tz_localize("UTC")

    if end <= start:
        raise ValueError("End time must be greater than start time")

    return start, end


def process_time_range(tr: str):
    """
    Converts a time range string (e.g. "2024-01-01/2025-01-01") to tuple of pd.Timestamp. If string is empty return
    (None, None) tuple
    :param tr: str
    :return: start, end (pd.Timestamp, pd.Timestamp)
    """
    assert_types(tr, [str, type(None)])
    if not tr:
        return (None, None)

    start, end = tr.split("/")
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    assert start <= end, f"start is greater than end!  ({start} < {end})"
    return start, end


def get_linked_resource_conf( dataset_conf: dict, link: str):
    """
    In a linked dataset to $fileserver, get the configuration
    :param dataset_conf:
    :param link:
    :return:
    """
    logger = logging.getLogger()
    service, resource_id = link[1:].split("/")  # skip $
    fileserver_conf = {}
    for fileserver_resource in dataset_conf["export"]["fileserver"]["resources"]:
        if fileserver_resource["id"] == resource_id:
            fileserver_conf = fileserver_resource
            break

    if not fileserver_conf:
        logger.error(f"Fileserver conf {link} not found!")
        raise LookupError(f"Fileserver conf {link} not found!")

    return fileserver_conf, service


def human_readable_bytes(num_bytes: int) -> str:
    """
    Convert a byte count to a human-readable string with SI units.

    Args:
        num_bytes: Number of bytes (non-negative integer).

    Returns:
        A string like "10.4 MB" or "321.5 kB".

    Examples:
        >>> human_readable_bytes(10000000)
        '10.0 MB'
        >>> human_readable_bytes(321456)
        '321.5 kB'
        >>> human_readable_bytes(1123123123)
        '1.1 GB'
        >>> human_readable_bytes(0)
        '0 B'
    """
    if num_bytes < 0:
        raise ValueError("Number of bytes must be non-negative")

    units = ["B", "kB", "MB", "GB", "TB"]   # extend if needed (PB, etc.)
    unit_index = 0
    value = float(num_bytes)

    # Move up to the next unit while the value is >= 1000 and we have more units.
    while value >= 1000 and unit_index < len(units) - 1:
        value /= 1000.0
        unit_index += 1

    # If the value is exactly an integer, we could show no decimals, but the spec asks for one decimal.
    return f"{value:.1f} {units[unit_index]}"


def get_file_md5(filename):
    md5_hash = hashlib.md5()
    with open(filename, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b""):
            md5_hash.update(chunk)

    return md5_hash.hexdigest()



def extract_netcdf_metadata(file_path: str) -> dict:
    """
    Opens a NetCDF file, extracts global and variable metadata,
    and closes the file immediately. Does not load any array data.
    """
    metadata = {}
    with netCDF4.Dataset(file_path, mode='r') as ds:
        # Extract global attributes efficiently
        metadata["global"] = {attr: ds.getncattr(attr) for attr in ds.ncattrs()}

        # Build the vocabulary dictionary for all variables
        metadata["variables"] = {
            var_name: {attr: var.getncattr(attr) for attr in var.ncattrs()}
            for var_name, var in ds.variables.items()
        }

    return metadata
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
import urllib
from logging.handlers import TimedRotatingFileHandler

import jsonschema
import rich
import requests
import subprocess

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
RST = "\033[0m"


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


class LoggerSuperclass:
    def __init__(self, logger: logging.Logger, name: str, colour=NRM):
        """
        SuperClass that defines logging as class methods adding a heading name
        """
        self.__logger_name = name
        self.__logger = logger
        if not logger:
            self.__logger = logging  # if not assign the generic module
        self.__log_colour = colour

    def warning(self, *args):
        mystr = YEL + "[%s] " % self.__logger_name + str(*args) + RST
        self.__logger.warning(mystr)

    def error(self, *args, exception: any = False):
        mystr = "[%s] " % self.__logger_name + str(*args)
        self.__logger.error(RED + mystr + RST)
        if exception:
            if isinstance(exception(), Exception):
                raise exception(mystr)
            else:
                raise ValueError(mystr)


    def debug(self, *args):
        mystr = self.__log_colour + "[%s] " % self.__logger_name + str(*args) + RST
        self.__logger.debug(mystr)

    def info(self, *args):
        mystr = self.__log_colour + "[%s] " % self.__logger_name + str(*args) + RST
        self.__logger.info(mystr)

    def setLevel(self, level):
        self.__logger.setLevel(level)


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


def run_subprocess(cmd, fail_exit=False):
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
    proc = subprocess.run(cmd_list, capture_output=True)
    if proc.returncode != 0:
        rich.print(f"\n[red]ERROR while running command '{cmd}'")
        if proc.stdout:
            rich.print(f"subprocess stdout:")
            rich.print(f">[bright_black]    {proc.stdout.decode()}")
        if proc.stderr:
            rich.print(f"subprocess stderr:")
            rich.print(f">[bright_black] {proc.stderr.decode()}")

        if fail_exit:
            raise ValueError(f"command failed: '{cmd_list}'")

        return False
    return True


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


def detect_common_path(paths):
    """
    Returns the common prefix in a list of strings
    """
    rich.print(paths)
    path_splits = [p.split("/") for p in paths]  # list of lists of paths
    i = -1
    loop = True
    while loop:
        i += 1
        compare = path_splits[0][i]
        for p in path_splits[1:]:
            if len(p) <= i or compare != p[i]:
                loop = False
                break

    common_path = "/".join(path_splits[0][:i]) + "/"
    return common_path


def rsync_files(host: str, folder, files: list):
    """
    Uses rsync to copy some files to a remote folder
    """
    assert type(host) is str, "invalid type"
    assert type(folder) is str, "invalid type"
    assert type(files) is list, "invalid type"
    run_subprocess(["ssh", host, f"mkdir -p {folder} -m=777"], fail_exit=True)
    run_subprocess(f"rsync -azh {' '.join(files)} {host}:{folder}")


def rm_remote_files(host, files):
    """
    Runs remove file over ssh
    """
    assert type(host) is str, "invalid type"
    assert type(files) is list, "invalid type"
    run_subprocess(["ssh", host, f"rm  {' '.join(files)}"], fail_exit=True)


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


def validate_schema(doc: dict, schema: dict, errors: list, verbose=False) -> list:
    if "$id" not in schema.keys():
        raise ValueError("Schema not valid!! missing $id field")

    if verbose:
        rich.print(f"   Validating doc='{doc['#id']}' against schema {schema['$id']}")

    try:  # validate against metadata schema
        jsonschema.validate(doc, schema=schema)
    except jsonschema.ValidationError as e:
        txt = f"[red]Document='{doc['#id']}' not valid for schema '{schema['$id']}'[/red]. Cause: {e.message}"
        errors.append(txt)
        if verbose:
            rich.print(txt)
    return errors


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


def assert_types(obj, valid_types):
    """
    Asserts that obj is of type <valid_type>
    :param obj:  any object
    :param valid_types:  list of types
    """
    assert isinstance(valid_types, list), "valid_types should be a list of types!"
    valid_string = ", ".join([str(t) for t in valid_types])
    valid_string = valid_string.replace("<class ", "").replace(">", "")
    assert type(obj) in valid_types, f"Expected on of {valid_string}, but got {type(obj)} instead"
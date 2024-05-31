#!/usr/bin/env python3
"""
FileServer implementation, delivers files to the remote fileserver, where files are exposed via HTTP. Contains methods
to convert from paths to urls and vice-versa.

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 23/3/21
"""

import os
import shutil
import rich
from .common import run_subprocess


class FileServer:
    def __init__(self, conf: dict):
        """
        Simple and stupid class that converts paths to urls and urls to paths
z
        :param basepath: root path of the fileserver
        :param baseurl: root url of the fileserver
        :param host: hostname of the fileserver
        """
        for key in ["host", "basepath", "baseurl"]:
            assert key in conf.keys(), f"expected {key} in configuration"

        self.basepath = conf["basepath"]
        self.baseurl = conf["baseurl"]
        self.host = conf["host"]

        self.path_alias = []  # Links to the real path
        if "path_links" in conf.keys():
            self.path_links = conf["path_links"]

    def path2url(self, path: str):
        assert type(path) is str, "expected string"

        for link in self.path_links:
            # If there is a softlink to the path, replace with the real path
            if link in path:
                path = path.replace(link, self.basepath)
        return path.replace(self.basepath, self.baseurl)

    def url2path(self, url: str):
        assert type(url) is str, "expected string"
        assert url.startswith("http://") or url.startswith("https://"), f"URL not valid '{url}'"
        return url.replace(self.baseurl, self.basepath)

    def send_file(self, path: str, file: str):
        """
        Sends a file to the FileServer
        :param path: path to deliver the file
        :param: file: filename
        :returns: URL of the files
        """

        if not os.path.exists(file):
            raise ValueError(f"file {file} does not exist!")

        # If we are in the 'host' machine, simply copy it
        dest_file = os.path.join(path, os.path.basename(file))
        if os.uname().nodename == self.host:
            rich.print(f"[gold1]Local copy from {file} to {dest_file}")
            os.makedirs(os.path.dirname(dest_file), exist_ok=True)
            shutil.copy2(file, dest_file)
        else:
            rich.print(f"[gold1]Delivering dataset {file} to {self.host}:{dest_file}")
            # Creating folder (just in case)
            run_subprocess(["ssh", self.host, f"mkdir -p {path}"])
            # Run rsync process
            run_subprocess(["rsync", file, f"{self.host}:{dest_file}"])

        return self.path2url(dest_file)

#!/usr/bin/env python3
"""

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 27/5/24
"""
import logging
import shutil
from argparse import ArgumentParser
import unittest
import os
import sys
import rich
from threading import Thread
import yaml
import time
import requests
import numpy as np
import pandas as pd
import json
import psycopg2
from PIL import Image, ImageDraw

try:
    from mmm import init_metadata_collector, setup_log, init_data_collector, bulk_load_data, propagate_metadata_to_ckan, \
        CkanClient, get_station_deployments
    from mmm.common import  download_file
except ModuleNotFoundError:
    # Get the directory of the current script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Get the parent directory (project root)
    parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))

    # Add the parent directory to the sys.path
    sys.path.insert(0, parent_dir)

    from mmm import (init_metadata_collector, setup_log, init_data_collector, propagate_metadata_to_sensorthings,
                     bulk_load_data, propagate_metadata_to_ckan, CkanClient, get_station_deployments)
    from mmm.common import GRN, RST, LoggerSuperclass, run_subprocess, file_list, dir_list, check_url, retrieve_url, download_file
    from mmapi import run_metadata_api
    from sta_timeseries import run_sta_timeseries_api


logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)

def get_json(url, params={}):
    r = requests.get(url, params=params)
    if r.status_code > 299:
        rich.print(f"[red]{url}")
        raise ConnectionError(f"HTTP error='{r.status_code}' at url={url}")
    return json.loads(r.text)


def post_json(url, data):
    headers = {"Content-Type": "application/json"}
    r = requests.post(url, headers=headers, data=json.dumps(data))
    if r.status_code > 299:
        raise ConnectionError(f"HTTP error='{r.status_code}' at url={url}")


def patch_json(url, data):
    headers = {"Content-Type": "application/json"}
    r = requests.patch(url, headers=headers, data=json.dumps(data))
    if r.status_code > 299:
        raise ConnectionError(f"HTTP error='{r.status_code}' at url={url}")


class TestMMAPI(unittest.TestCase, LoggerSuperclass):
    @classmethod
    def setUpClass(cls):

        cls.secrets = "secrets-test.yaml"
        with open(cls.secrets) as f:
            conf = yaml.safe_load(f)["secrets"]

        log = setup_log("mmapi-test")
        log.setLevel(logging.INFO)
        LoggerSuperclass.__init__(cls, log, "test")

        with open(cls.secrets) as f:
            cls.conf = yaml.safe_load(f)["secrets"]

        cls.mmapi_url = cls.conf["mmapi"]["root_url"] + "/mmapi/v1.0"
        cls.sta_url = cls.conf["sensorthings"]["url"]

        with open("sta-timeseries.env") as f:
            for line in f.readlines():
                line = line.strip()
                if "STA_TS_ROOT_URL" in line:
                    cls.sta_ts_url = line.split("=")[1].replace("\"", "")
                    break

        os.makedirs("erddapData", mode=0o777, exist_ok=True)
        # Create volumes for all docker services
        with open("docker-compose.yaml") as f:
            docker_config = yaml.safe_load(f)

        cls.docker_volumes = []
        for name, service in docker_config["services"].items():
            for key, value in service.items():
                if key == "volumes":
                    for volume in value:
                        try:
                            src, dst = volume.split(":")
                        except ValueError:
                            src, dst, prm = volume.split(":")
                        if "." not in os.path.basename(src):  # if dot in name assume its a folder
                            os.makedirs(src, exist_ok=True, mode=0o777)

                        cls.docker_volumes.append(src)

        cls.local_csv_data = "/var/tmp/mmapi/tmpdata/"

        # Make sure that ERDDAP has a clean datasets.xml file
        shutil.copy2("conf/datasets.xml.default", "conf/datasets.xml" )

        log.info("Delete all files and folders in fileserver")
        folder = docker_config["services"]["fileserver"]["volumes"][0].split(":")[0]

        files = file_list(folder)
        for f in files:
            os.remove(f)
        dirs = os.listdir(folder)
        dirs = dir_list(folder)
        dirs = sorted(dirs, reverse=True)
        for d in dirs:
            os.rmdir(d)

        log.info("Setting up system start docker compose... (this may take a while)")
        #run_subprocess("docker compose up -d --build", fail_exit=True)
        run_subprocess("docker compose up -d", fail_exit=True)

        # make sure that all servieces are up and running with at least a quick get
        urls = {
            "ERDDAP": "http://localhost:8090/erddap/index.html",
            "SensorThings": "http://localhost:8080/FROST-Server/v1.1/Sensors",
            "CKAN": "http://localhost:5001/api/3/"
        }
        timeout = 120
        tinit = time.time()
        for service, url in urls.items():
            code = 404
            rich.print(f"Trying to reach service [cyan]{service}[/cyan]...", end="")
            while code > 300:
                try:
                    r = requests.get(url, timeout=5)
                    code = r.status_code
                except requests.exceptions.RequestException:
                    pass
                if time.time() - tinit > timeout:
                    rich.print("[red]Timeout error!")
                    raise TimeoutError("Could not connect to service")

                if code < 300:
                    rich.print(f"[green]success!")
                else:
                    time.sleep(2)

        log.info("Setup CkanClient")
        log.info("Let's do somethings quick and dirty to get the ckan_admin key")
        os.system("docker exec ckan-test ckan user token add ckan_admin tk1 | tail -n 1 | sed 's/\t//g' >  ckan.key")
        # If success, we should have now the API key in the ckan.key file
        with open("ckan.key") as f:
            ckan_key = f.read().strip()


        if len(ckan_key) < 10:
            raise ValueError(f"CKAN TOKEN too short! '{ckan_key}'")
        conf["ckan"]["api_key"] = ckan_key

        log.info("Setup Metadata Collector...")
        cls.mc = init_metadata_collector(conf, log=log)
        cls.log = log
        log.info("Setup Data Collector...")
        cls.dc = init_data_collector(conf, log, mc=cls.mc)
        cls.stadb = cls.dc.sta

        log.info("Clearing Metadata DB database...")
        cls.mc.drop_all()
        cls.dc.sta.drop_all()
        cls.ckan = cls.dc.ckan

        #cls.ckan = CkanClient(cls.mc, cls.conf["ckan"]["url"], ckan_key)

    def test_01_launch_metadata_api(self):
        """Run all tests for MMAPI in a sequential manner"""
        self.log.info("Launching Metadata API in a dedicate thread...")
        #     run_flask_app(secrets, args.environment, log, mc, thread=True)
        mapi = Thread(target=run_metadata_api, args=(self.secrets, self.log, self.mc), daemon=True)
        mapi.start()
        time.sleep(0.5)
        d = get_json(self.mmapi_url)
        self.assertIsInstance(d, dict)

    def test_02_add_unit(self):
        """adds degC as unit"""
        self.log.info("Inserting several 'units' via API")
        data = {
            "#id": "degrees_celsius",
            "name": "degrees Celsius2",
            "symbol": "degC",
            "definition": "https://vocab.nerc.ac.uk/collection/P06/current/UPAA/",
            "type": "linear"
        }
        a = self.mc.insert_document("units", data)
        self.assertIsInstance(a, dict)

        with self.assertRaises(NameError) as cm:
            self.mc.insert_document("units", data)
        the_exception = cm.exception
        self.assertEqual(type(the_exception), NameError)

        url = self.mmapi_url + f"/units/{data['#id']}"
        units = get_json(url)
        self.assertEqual(data["symbol"], units["symbol"])

        # Make sure that update works

    def test_03_add_via_api(self):
        """adding units and variables via API"""
        d = {
            "#id": "siemens_per_metre",
            "name": "siemens per metre1",
            "symbol": "S/m",
            "definition": "https://vocab.nerc.ac.uk/collection/P06/current/UECA/",
            "type": "linear"
        }
        post_json(self.mmapi_url + "/units", d)

        # Ensure history
        d = {
            "#id": "siemens_per_metre",
            "name": "siemens per metre2",
            "symbol": "S/m",
            "definition": "https://vocab.nerc.ac.uk/collection/P06/current/UECA/",
            "type": "linear"
        }
        self.mc.replace_document("units", d["#id"], d)

        d1 = get_json(self.mmapi_url + "/units/siemens_per_metre/history/1")
        d2 = get_json(self.mmapi_url + "/units/siemens_per_metre/history/2")

        self.assertEqual(d1["name"], "siemens per metre1")
        self.assertEqual(d2["name"], "siemens per metre2")

        # Now delete siemens_per_metre from current database, but not from history
        self.mc.delete_document("units", "siemens_per_metre")

        # Now insert it again, we should keep version history!

        d = {
            "#id": "siemens_per_metre",
            "name": "siemens per metre",
            "symbol": "S/m",
            "definition": "https://vocab.nerc.ac.uk/collection/P06/current/UECA/",
            "type": "linear"
        }
        resp = post_json(self.mmapi_url + "/units", d)
        rich.print(resp)


        # Make sure that we can access the v3 from the history endpoint
        d3 = get_json(self.mmapi_url + "/units/siemens_per_metre/history/3")
        self.assertEqual(d3["name"], "siemens per metre")

        d = {
            "#id": "TEMP",
            "standard_name": "sea_water_temperature",
            "description": "in situ sea water temperature",
            "definition": "https://vocab.nerc.ac.uk/collection/P01/current/TEMPST01",
            "cf_compliant": True,
            "type": "environmental"
        }
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "CNDC",
            "standard_name": "sea_water_electrical_conductivity",
            "description": "sea water electrical conductivity",
            "definition": "https://vocab.nerc.ac.uk/collection/P01/current/CNDCST01/",
            "cf_compliant": True,
            "type": "environmental"
        }
        post_json(self.mmapi_url + "/variables", d)
        d = {
            "#id": "OBSEA:CTD:TEMP",
            "instrumentType": "CTD",
            "qartod": {
                "gross_range_test": {
                    "fail_span": [8, 40],
                    "suspect_span": [10, 32]
                },
                "climatology_test": {
                    "config": [
                        {
                            "vspan": [10.5, 16],
                            "tspan": [1, 3],
                            "period": "month",
                            "zspan": [0, 100]
                        },
                        {
                            "vspan": [12.3, 22],
                            "tspan": [4, 6],
                            "period": "month",
                            "zspan": [0, 100]
                        },
                        {
                            "vspan": [16, 28.2],
                            "tspan": [7, 9],
                            "period": "month",
                            "zspan": [0, 100]
                        },
                        {
                            "vspan": [12.3, 24],
                            "tspan": [10, 12],
                            "period": "month",
                            "zspan": [0, 100]
                        }
                    ]
                },
                "flat_line_test": {
                    "tolerance": 1e-05,
                    "suspect_threshold": 100,
                    "fail_threshold": 300
                },
                "rate_of_change_test": {
                    "threshold": 0.01
                },
                "spike_test": {
                    "suspect_threshold": 1,
                    "fail_threshold": 3
                }
            }
        }
        post_json(self.mmapi_url + "/qualityControl", d)
        d = {
            "#id": "OBSEA:CTD:CNDC",
            "instrumentType": "CTD",
            "qartod": {
                "gross_range_test": {
                    "fail_span": [1, 6.5],
                    "suspect_span": [2, 6]
                },
                "flat_line_test": {
                    "tolerance": 1e-06,
                    "suspect_threshold": 100,
                    "fail_threshold": 300
                },
                "spike_test": {
                    "suspect_threshold": 0.05,
                    "fail_threshold": 0.2
                },
                "rate_of_change_test": {
                    "threshold": 0.001
                }
            }
        }
        post_json(self.mmapi_url + "/qualityControl", d)

    def test_04_assert_schema(self):
        """trying to insert a non-compliant document to catch the exception"""
        self.info("Inserting an erroneous unit")
        d = {
            "#id": "CNDC2",
            "standard_nam2e": "sea_water_electrical_conductivity",
            "descriptiodn": "sea water electrical conductivity",
            "definition": "https://vocab.nerc.ac.uk/collection/P01/current/CNDCST01/",
            "cf_compdliant": True,
            "type": "environmental"
        }
        with self.assertRaises(ValueError) as cm:
            self.mc.insert_document("variables", d)

        d = {
            "#id": "CNDC",
            "standard_name": "sea_water_electrical_conductivity",
            "description": "sea water electrical conductivity UPDATED",
            "definition": "https://vocab.nerc.ac.uk/collection/P01/current/CNDCST01/",
            "cf_compliant": True,
            "type": "environmental"
        }
        self.info("Updating a document")
        self.mc.insert_document("variables", d, update=True)

    def test_05_add_process(self):
        """Adding average process"""
        avg = {
            "#id": "average",
            "type": "average",
            "name": "Simple average the measure",
            "description": "Averages sensor variables over a period of time (period parameter, e.g. 30min, 1day). If a "
                           "certain variable should not be averaged, add it to the ignore list",
            "reference": "https://en.wikipedia.org/wiki/Average",
            "parameters": {
                "period": "period to average (string)",
                "ignore": "list of variables to ignore when averaging"
            }
        }
        self.mc.insert_document("processes", avg)

    def test_06_add_organization_people(self):
        """Adding organization process"""
        d = {
            "#id": "upc",
            "fullName": "Universitat Politècnica de Catalunya",
            "acronym": "UPC",
            "ROR": "https://ror.org/03mb6wj31",
            "EDMO": "https://edmo.seadatanet.org/report/2150",
            "public": True,
            "logoUrl": "http://testfiles.obsea.es/files/other/logos/upc.png",
            "alternativeNames": []
        }
        self.mc.insert_document("organizations", d)
        d = {
            "#id": "enoc_martinez",
            "name": "Enoc Martinez",
            "givenName": "Enoc",
            "familyName": "Martinez",
            "orcid": "0000-0003-1233-7105",
            "email": "enoc.martinez@upc.edu",
            "@organizations": "upc"
        }
        self.mc.insert_document("people", d)

    def test_07_station(self):
        """Register station and its deployment"""
        d = {
            "#id": "OBSEA",
            "shortName": "OBSEA",
            "longName": "OBSEA Expandable Seafloor Observatory",
            "description": "OBSEA is a cabled seafloor observatory deployed at the North-west mediterranean (Vilanova i"
                           " la Geltrú, Spain)",
            "platformType": {
                "definition": "http://vocab.nerc.ac.uk/collection/L06/current/48/",
                "label": "mooring"
            },
            "emsoFacility": "OBSEA",
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "dataManager"
                },
                {
                    "@organizations": "upc",
                    "role": "owner"
                },
            ],
            "defaults": {
                "@programmes": "OBSEA"
            }
        }
        self.mc.insert_document("stations", d)

        d = {
            "#id": "OBSEA_deployment_20090519",
            "name": "OBSEA initial deployment",
            "time": "2009-05-19T00:00:00Z",
            "type": "deployment",
            "appliedTo": {
                "@stations": "OBSEA"
            },
            "where": {
                "position": {
                    "depth": 20.0,
                    "latitude": 41.18212,
                    "longitude": 1.75257
                }
            },
            "description": "Initial OBSEA deployment"
        }  # OBSEA deployment
        self.mc.insert_document("activities", d)

        d = {
            "#id": "OBSEA_deployment_20150519",
            "name": "OBSEA second deployment",
            "time": "2015-05-19T00:00:00Z",
            "type": "deployment",
            "appliedTo": {
                "@stations": "OBSEA"
            },
            "where": {
                "position": {
                    "depth": 20.0,
                    "latitude": 41.18212,
                    "longitude": 1.75257
                }
            },
            "description": "Initial OBSEA deployment"
        }  # OBSEA older deployment
        self.mc.insert_document("activities", d)
        self.assertEqual(len(get_station_deployments(self.mc, "OBSEA")), 2)

    def test_08_add_sensor(self):
        """Adding sensor"""
        d = {
            "#id": "SBE37",
            "description": "SBE37 CTD sensor at OBSEA",
            "shortName": "SBE37",
            "serialNumber": "37SMP47472-5496",
            "longName": "CTD SBE37 at OBSEA",
            "instrumentType": {
                "id": "CTD",
                "definition": "http://vocab.nerc.ac.uk/collection/L05/current/130",
                "label": "CTD"
            },
            "manufacturer": {
                "definition": "http://vocab.nerc.ac.uk/collection/L35/current/MAN0013/",
                "label": "Sea-Bird Scientific"
            },
            "model": {
                "definition": "http://vocab.nerc.ac.uk/collection/L22/current/TOOL1457/",
                "label": "SBE 37 MicroCat SMP-CTP"
            },
            "variables": [
                {
                    "@variables": "TEMP",
                    "@units": "degrees_celsius",
                    "@qualityControl": "OBSEA:CTD:TEMP",
                    "dataType": "timeseries"
                },
                {
                    "@variables": "CNDC",
                    "@units": "siemens_per_metre",
                    "@qualityControl": "OBSEA:CTD:CNDC",
                    "dataType": "timeseries"
                },
                {
                    "@variables": "TEMP",
                    "@units": "degrees_celsius",
                    "@qualityControl": "OBSEA:CTD:TEMP",
                    "dataType": "profiles"
                },
                {
                    "@variables": "CNDC",
                    "@units": "siemens_per_metre",
                    "@qualityControl": "OBSEA:CTD:CNDC",
                    "dataType": "profiles"
                },
            ],
            "processes": [
                {
                    "@processes": "average",
                    "parameters": {
                        "period": "30min",
                        "ignore": []
                    }
                },
                {
                    "@processes": "average",
                    "parameters": {
                        "period": "1day",
                        "ignore": []
                    }
                }
            ],
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "dataManager"
                }
            ],
            "dataMode": "real-time"
        }  # SBE37
        self.mc.insert_document("sensors", d)
        d = {
            "#id": "SBE16",
            "description": "SBE16 CTD sensor at OBSEA",
            "shortName": "SBE16",
            "serialNumber": "16XXXX",
            "longName": "CTD SBE16 at OBSEA",
            "instrumentType": {
                "id": "CTD",
                "definition": "http://vocab.nerc.ac.uk/collection/L05/current/130",
                "label": "CTD"
            },
            "manufacturer": {
                "definition": "http://vocab.nerc.ac.uk/collection/L35/current/MAN0013/",
                "label": "Sea-Bird Scientific"
            },
            "model": {
                "definition": "http://vocab.nerc.ac.uk/collection/L22/current/TOOL1457/",
                "label": "SBE 16 MicroCat SMP-CTP"
            },
            "variables": [
                {
                    "@variables": "TEMP",
                    "@units": "degrees_celsius",
                    "@qualityControl": "OBSEA:CTD:TEMP",
                    "dataType": "timeseries"
                },
                {
                    "@variables": "CNDC",
                    "@units": "siemens_per_metre",
                    "@qualityControl": "OBSEA:CTD:CNDC",
                    "dataType": "timeseries"
                },
                {
                    "@variables": "TEMP",
                    "@units": "degrees_celsius",
                    "@qualityControl": "OBSEA:CTD:TEMP",
                    "dataType": "profiles"
                },
                {
                    "@variables": "CNDC",
                    "@units": "siemens_per_metre",
                    "@qualityControl": "OBSEA:CTD:CNDC",
                    "dataType": "profiles"
                },
            ],
            "processes": [
                {
                    "@processes": "average",
                    "parameters": {
                        "period": "30min",
                        "ignore": []
                    }
                },
                {
                    "@processes": "average",
                    "parameters": {
                        "period": "1day",
                        "ignore": []
                    }
                }
            ],
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "dataManager"
                }
            ],
            "dataMode": "real-time"
        }  # SBE16
        self.mc.insert_document("sensors", d)

        d = {
            "#id": "sbe37depl",
            "name": "SBE37 deployment",
            "time": "2020-01-01T00:00:00Z",
            "type": "deployment",
            "appliedTo": {
                "@sensors": "SBE37"
            },
            "where": {
                "@stations": "OBSEA"
            },
            "description": "Desplegat el SBE37 a l'OBSEA"
        }  # SBE37 deployment
        self.mc.insert_document("activities", d)
        d = {
            "#id": "sbe16depl",
            "name": "SBE16 deployment",
            "time": "2022-01-01T00:00:00Z",
            "type": "deployment",
            "appliedTo": {
                "@sensors": "SBE16"
            },
            "where": {
                "@stations": "OBSEA"
            },
            "description": "Desplegat el SBE16 a l'OBSEA"
        }  # SBE16 deployment
        self.mc.insert_document("activities", d)

    def test_09_programme(self):
        """insert programme"""
        d = {
            "#id": "OBSEA",
            "description": "Long term monitoring of the OBSEA underwater observatory area",
            "@projects": [],
            "geoJsonFeature": {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "coordinates": [
                        [
                            [
                                1.7515221855838945,
                                41.183341295396275
                            ],
                            [
                                1.7515221855838945,
                                41.181307814018794
                            ],
                            [
                                1.7538603482693702,
                                41.181307814018794
                            ],
                            [
                                1.7538603482693702,
                                41.183341295396275
                            ],
                            [
                                1.7515221855838945,
                                41.183341295396275
                            ]
                        ]
                    ],
                    "type": "Polygon"
                }
            }
        }
        d2 = {
            "#id": "OBSEA_Biotop",
            "description": "Biotope in front of OBSEA",
            "@projects": [],
            "geoJsonFeature": {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "coordinates": [
                        [
                            [
                                1.7515221855838945,
                                41.183341295396275
                            ],
                            [
                                1.7515221855838945,
                                41.181307814018794
                            ],
                            [
                                1.7538603482693702,
                                41.181307814018794
                            ],
                            [
                                1.7538603482693702,
                                41.183341295396275
                            ],
                            [
                                1.7515221855838945,
                                41.183341295396275
                            ]
                        ]
                    ],
                    "type": "Polygon"
                }
            }
        }
        self.mc.insert_document("programmes", d)
        self.mc.insert_document("programmes", d2)

    def test_10_add_profile_sensor(self):
        """Adding a sensor with profile data"""
        d = {
            "#id": "degrees_north",
            "name": "degrees north",
            "symbol": "deg",
            "definition": "https://vocab.nerc.ac.uk/collection/P06/current/UAAA/",
            "type": "linear"
        }
        post_json(self.mmapi_url + "/units", d)

        d = {
            "#id": "meters_per_second",
            "name": "meters per second",
            "symbol": "m/s",
            "definition": "https://vocab.nerc.ac.uk/collection/P06/current/UVAA/",
            "type": "linear"
        }
        post_json(self.mmapi_url + "/units", d)

        d = {
            "#id": "CDIR",
            "standard_name": "sea_water_velocity_to_direction",
            "description": "direction towards the sea water is flowing (currents)",
            "definition": "https://vocab.nerc.ac.uk/collection/P01/current/LCDAAP01/",
            "polar": {
                "module": "CSPD",
                "angle": "CDIR"
            },
            "cf_compliant": True,
            "type": "environmental"
        }  # CDIR
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "CSPD",
            "standard_name": "sea_water_speed",
            "description": "Horizontal velocity of the water column (currents)",
            "definition": "https://vocab.nerc.ac.uk/collection/P01/current/LCSAAP01",
            "polar": {
                "module": "CSPD",
                "angle": "CDIR"
            },
            "cf_compliant": True,
            "type": "environmental"
        }  # CSPD
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "UCUR",
            "standard_name": "eastward_sea_water_velocity",
            "description": "eastward velocity of water current in the water body",
            "definition": "https://vocab.nerc.ac.uk/collection/P01/current/LCEWZZ01/",
            "cf_compliant": True,
            "type": "environmental"
        }  # UCUR
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "VCUR",
            "standard_name": "northward_sea_water_velocity",
            "description": "northward velocity of water current in the water body",
            "definition": "https://vocab.nerc.ac.uk/collection/P01/current/LCNSZZ01/",
            "cf_compliant": True,
            "type": "environmental"
        }  # VCUR
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "AWAC",
            "shortName": "AWAC",
            "longName": "AWAC-AST 1 MHz ADCP",
            "description": "AWAC-AST 1 MHz current profiler at OBSEA",
            "serialNumber": "WAV 5931",
            "instrumentType": {
                "label": "Current Profiler",
                "id": "ADCP",
                "definition": "http://vocab.nerc.ac.uk/collection/L05/current/115"
            },
            "manufacturer": {
                "label": "Nortek",
                "definition": "http://vocab.nerc.ac.uk/collection/L35/current/MAN0068/"
            },
            "model": {
                "label": "AWAC-AST 1 MHz",
                "definition": "http://vocab.nerc.ac.uk/collection/L22/current/TOOL0897/"
            },
            "variables": [
                {
                    "@variables": "CSPD",
                    "@units": "meters_per_second",
                    "dataType": "profiles"
                },
                {
                    "@variables": "CDIR",
                    "@units": "degrees_north",
                    "dataType": "profiles"
                },
                {
                    "@variables": "UCUR",
                    "@units": "meters_per_second",
                    "dataType": "profiles"
                },
                {
                    "@variables": "VCUR",
                    "@units": "meters_per_second",
                    "dataType": "profiles"
                }
            ],
            "processes": [
                {
                    "@processes": "average",
                    "parameters": {
                        "period": "30min",
                    }
                }
            ],
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "dataManager"
                },
                {
                    "@organizations": "upc",
                    "role": "owner"
                }
            ],
            "dataMode": "real-time"
        }  # AWAC sensor
        post_json(self.mmapi_url + "/sensors", d)

        d = {
            "#id": "awacdeployment",
            "name": "SBE37 deployment",
            "time": "2023-01-01T00:00:00Z",
            "type": "deployment",
            "appliedTo": {
                "@sensors": "AWAC"
            },
            "where": {
                "@stations": "OBSEA"
            },
            "description": "Desplegat el SBE37 a l'OBSEA"
        }  # AWAC deployment
        self.mc.insert_document("activities", d)

    def test_11_add_camera(self):
        """Adding a Camera, that will produce files, inference and detections data"""

        d = {
            "#id": "dimensionless",
            "name": "Dimensionless",
            "symbol": "Dmnless",
            "definition": "https://vocab.nerc.ac.uk/collection/P06/current/UUUU",
            "type": "linear"
        }  # Dimensionless uints
        post_json(self.mmapi_url + "/units", d)

        d = {
            "#id": "underwater_photography",
            "standard_name": "underwater_photography",
            "description": "underwater photography",
            "definition": "http://vocab.nerc.ac.uk/collection/P03/current/UWPH/",
            "cf_compliant": False,
            "type": "environmental"
        }  # underwater photography
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "FATX",
            "standard_name": "fish_abundance",
            "description": "Fish abundance in water bodies",
            "definition": "https://vocab.nerc.ac.uk/collection/P02/current/FATX/",
            "cf_compliant": False,
            "type": "environmental"
        }  # FATX (fish abundance)
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "diplodus_vulgaris",
            "description": "Abundance of Diplodus vulgaris in sea water",
            "standard_name": "Diplodus vulgaris",
            "definition": "https://www.marinespecies.org/aphia.php?p=taxdetails&id=127054",
            "type": "biodiversity",
            "cf_compliant": False,
            "worms_id": "127054"
        }  # Diplodus vulgaris
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "chromis_chromis",
            "description": "Abundance of Chromis chromis in sea water",
            "standard_name": "Chromis chromis",
            "definition": "https://www.marinespecies.org/aphia.php?p=taxdetails&id=127000",
            "type": "biodiversity",
            "cf_compliant": False,
            "worms_id": "127000"
        }  # Chromis chromis
        post_json(self.mmapi_url + "/variables", d)

        d = {
            "#id": "YOLOv8",
            "name": "YOLOv8l_18sp_2361img",
            "algorithm": "YOLOv8",
            "type": "json",
            "description": "YOLOv8 large trained with 18 species, 2361 images, learning rate 0.000375 and image size 1920 px",
            "notes": "Variable names correspond to @variables standard_name field",
            "weights": "https://my.url/file",
            "trainingConfig": "https://my.url/filex",
            "trainingData": "",
            "variableNames": [
                "Chromis chromis",
                "Diplodus vulgaris",
                "Diver"
            ],
            "ignore": [
                "Diver"
            ],
            "rename": {},
            "parameters": {}
        }  # YOLOv8 process
        post_json(self.mmapi_url + "/processes", d)

        d = {
            "#id": "IPC608",
            "shortName": "IPC608_8B64_165",
            "serialNumber": "IPC60820221105AAWRK84213991",
            "description": "IPC608 underwater camera",
            "longName": "IPC608 underwater camera",
            "instrumentType": {
                "definition": "http://vocab.nerc.ac.uk/collection/L05/current/311",
                "label": "cameras",
                "id": "cameras"
            },
            "manufacturer": {
                "definition": "LINOVISION",
                "label": "LINVISION"
            },
            "model": {
                "definition": "",
                "label": "IPC608UW-10"
            },
            "variables": [
                {
                    "@variables": "FATX",
                    "@units": "dimensionless",
                    "dataType": "detections"
                },
                {
                    "@variables": "underwater_photography",
                    "@units": "dimensionless",
                    "dataType": "files"
                }
            ],
            "processes": [
                {
                    "@processes": "YOLOv8",
                    "parameters": {}
                }
            ],
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "dataManager"
                },
                {
                    "@organizations": "upc",
                    "role": "owner"
                }
            ],
            "dataMode": "real-time"
        }  # IPC608 camera
        post_json(self.mmapi_url + "/sensors", d)

        d = {
            "#id": "ipc_camera_deployment",
            "name": "IPC608 deployment",
            "time": "2023-01-01T00:00:00Z",
            "type": "deployment",
            "appliedTo": {
                "@sensors": "IPC608"
            },
            "where": {
                "@stations": "OBSEA"
            },
            "fieldOfView": {
                "@programmes": "OBSEA_Biotop"
            },
            "description": "Desplegat el IPC608 a l'OBSEA"
        }  # IPC608 camera deployment
        post_json(self.mmapi_url + "/activities", d)

    def test_12_add_projects(self):
        d = {
            "#id": "Geo-INQUIRE",
            "type": "european",
            "acronym": "Geo-INQUIRE",
            "title": "Geosphere INfrastructures for QUestions into Integrated REsearch",
            "totalBudget": 13923475.77,
            "dateStart": "2022-10-01",
            "dateEnd": "2026-09-30",
            "active": True,
            "funding": {
                "grantId": "101058518",
                "@organizations": "ec",
                "call": "HORIZON-INFRA-2021-SERV-01",
                "coordinator": "GFZ",
                "partnershipType": "thirdParty"
            },
            "ourBudget": 43750.0,
            "logoUrl": ""
        }
        self.mc.insert_document("projects", d)

    def test_13_add_datasets(self):
        d = {
            "#id": "obsea_ctd_full",
            "title": "CTD data at OBSEA Underwater Observatory full data",
            "summary": "CTD data measured at OBSEA underwater observatory full data",
            "@sensors": [
                "SBE37",
                "SBE16"
            ],
            "@stations": "OBSEA",
            "dataType": "timeseries",
            "dataSourceOptions": {
                "fullData": True
            },
            "export": {
                "erddap": {
                    "resources": [{
                        "id": "OBSEA_CTD_full",
                        "host": "localhost",
                        "path": "./datasets",
                        "period": "daily",
                        "format": "netcdf",
                        "dataType": "timeseries"
                    }]
                },
                "fileserver": {
                    "resources": [{
                        "host": "localhost",
                        "path": "/var/tmp/mmapi/volumes/files/datasets/obsea_ctd_full",
                        "period": "monthly",
                        "format": "netcdf",
                        "id": "netcdf_dataset",
                        "dataType": "timeseries"
                    }]
                },
                "ckan": {
                    "resources": [{
                        "id": "netcdf_dataset",
                        "link": "$fileserver/netcdf_dataset",
                        "title": "CTD data at OBSEA observatory full data",
                        "description": "data from various CTD sensors at OBSEA observatory full data"
                    }]
                }
            },
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "DataCurator"
                },
                {
                    "@people": "enoc_martinez",
                    "role": "ProjectLeader"
                },
                {
                    "@organizations": "upc",
                    "role": "RightsHolder"
                }
            ],
            "funding": {
                "@projects": [
                    "Geo-INQUIRE"
                ]
            }
        }  # obsea_ctd_full
        self.mc.insert_document("datasets", d)

        d = {
            "#id": "obsea_ctd_30min",
            "title": "CTD data at OBSEA Underwater Observatory 30 min average",
            "summary": "CTD data measured at OBSEA underwater observatory averaged every 30min",
            "@sensors": [
                "SBE37",
                "SBE16"
            ],
            "@stations": "OBSEA",
            "dataType": "timeseries",
            "dataSourceOptions": {
                "fullData": False,
                "averagePeriod": "30min"
            },
            "export": {
                "erddap": {
                    "resources": [{
                        "id": "OBSEA_CTD_30min",
                        "host": "localhost",
                        "path": "./datasets",
                        "period": "daily",
                        "format": "netcdf",
                        "dataType": "timeseries",
                        "averagePeriod": "30min"
                    }]
                },
                "fileserver": {
                    "resources": [{
                        "id": "netcdf_dataset",
                        "host": "localhost",
                        "path": "/var/tmp/mmapi/volumes/files/datasets/obsea_ctd_30min",
                        "period": "yearly",
                        "format": "netcdf",
                        "dataType": "timeseries"
                    }]
                },
                "ckan": {
                    "resources": [{
                        "id": "ctd",
                        "link": "$fileserver/netcdf_dataset",
                        "title": "CTD data at OBSEA observatory 30 min average",
                        "description": "data from various CTD sensors at OBSEA observatory averaged every 30min"
                    }]
                }
            },
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "DataCurator"
                },
                {
                    "@people": "enoc_martinez",
                    "role": "ProjectLeader"
                },
                {
                    "@organizations": "upc",
                    "role": "RightsHolder"
                }
            ],
            "funding": { "@projects": ["Geo-INQUIRE"] }
        }  # obsea_ctd_30min

        self.mc.insert_document("datasets", d)

        d = {
            "#id": "IPC608_pics",
            "title": "Underwater photography at OBSEA",
            "summary": "Underwater photography at OBSEA",
            "@sensors": [
                "IPC608"
            ],
            "@stations": "OBSEA",
            "dataSourceOptions": {
                "host": "localhost"
            },
            "dataType": "files",
            "export": {
                "fileserver": {
                    "resources": [{
                        "id": "zip_pics",
                        "host": "localhost",
                        "path": "/var/tmp/mmapi/volumes/files/datasets/IPC608_pics",
                        "period": "yearly",
                        "format": "zip",
                        "dataType": "files"
                    }]
                },
                "ckan": {
                    "resources": [{
                        "id": "zip",
                        "link": "$fileserver/zip_pics",
                        "title": "Pictures from camera IPC608 at OBSEA",
                        "description": "pictures taken from a IPC608 camera at OBSEA"
                    }]
                }
            },
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "DataCurator"
                },
                {
                    "@people": "enoc_martinez",
                    "role": "ProjectLeader"
                },

                {
                    "@organizations": "upc",
                    "role": "RightsHolder"
                }
            ]
        }  # underwater pictures dataset
        self.mc.insert_document("datasets", d)


        d = {
            "#id": "biodiversity_datasets",
            "title": "Biodiversity at OBSEA",
            "summary": "Fish detections from underwater photography by an AI model",
            "@sensors": [
                "IPC608"
            ],
            "@stations": "OBSEA",
            "dataSourceOptions": {
                "host": "localhost"
            },
            "dataType": "detections",
            "export": {
                "fileserver": {
                    "resources": [
                    {
                        "id": "darwin_core_dataset",
                        "host": "localhost",
                        "path": "/var/tmp/mmapi/volumes/files/datasets/biodiversity_datasets",
                        "period": "yearly",
                        "format": "dwca",
                        "dataType": "json"
                    }
                    ]
                },
                "ckan": {
                    "resources": [{
                        "id": "darwin_core_dataset",
                        "link": "$fileserver/darwin_core_dataset",
                        "title": "Fish detections",
                        "description": "Fish detections in CSV format"
                    }]
                }
            },
            "contacts": [
                {
                    "@people": "enoc_martinez",
                    "role": "DataCurator"
                },
                {
                    "@people": "enoc_martinez",
                    "role": "ProjectLeader"
                },

                {
                    "@organizations": "upc",
                    "role": "RightsHolder"
                }
            ],
            "constraints": {
                "@processes": "YOLOv8"
            }
        }  # underwater pictures dataset
        self.mc.insert_document("datasets", d)

    def test_20_propagate_to_sensorthings(self):
        """Propagate metadata from Metadata DB to SensorThingsAPI"""
        propagate_metadata_to_sensorthings(self.dc, [], self.conf["sensorthings"]["url"], update=True)

        # Make sure that we have defaultFeatureOfInterest
        self.dc.sta.get_datastream_id("AWAC", "OBSEA", "CDIR", "profiles", "30min")

    def test_21_launch_sta_timeseries(self):
        """launching sensorthings timeseries API"""
        mapi = Thread(target=run_sta_timeseries_api, args=["sta-timeseries.env", self.log, 8081], daemon=True)
        mapi.start()
        time.sleep(0.5)
        d = get_json(self.sta_ts_url)
        self.assertIsInstance(d, dict)

    def test_30_ingest_avg_timeseries_data(self):
        """Ingesting average timeseries data using the API"""
        # Generate sine wave values
        frequency = 3
        dates = pd.date_range(start='2024-01-01', end="2024-01-02", freq='30min')
        tvector = np.arange(0, len(dates)) / len(dates)
        # Create a pandas DataFrame
        df = pd.DataFrame({
            'timestamp': dates,
            "TEMP": np.sin(2 * np.pi * frequency * tvector),
            "CNDC": np.cos(2 * np.pi * frequency * tvector + np.pi/2)
        })
        df["timestamp"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        sta = self.dc.sta
        sta.initialize_dicts()  # update dicts
        temp_id = sta.get_datastream_id("SBE37", "OBSEA", "TEMP", "timeseries", average="30min")
        cndc_id = sta.get_datastream_id("SBE37", "OBSEA", "CNDC", "timeseries", average="30min")

        # Assert that we get an error with wrong data types
        with self.assertRaises(AssertionError):
            sta.get_datastream_id("SBE37", "OBSEA", "CNDC", "banana")

        foi_id = sta.value_from_query('select "ID" from "FEATURES" limit 1;')

        for indx, row in df.iterrows():
            obs = {
                "phenomenonTime": row["timestamp"],
                "result": row["TEMP"],
                "resultQuality": {"qc_flag": 1},
                "FeatureOfInterest": {"@iot.id": foi_id}
            }

            url = self.sta_url + f"/Datastreams({temp_id})/Observations"
            post_json(url, obs)
            obs = {
                "phenomenonTime": row["timestamp"],
                "result": row["CNDC"],
                "resultQuality": {"qc_flag": 1},
                "FeatureOfInterest": {"@iot.id": foi_id}
            }
            url = self.sta_url + f"/Datastreams({cndc_id})/Observations"
            post_json(url, obs)

        # We should not able to insert it more than once
        with self.assertRaises(ConnectionError):
            post_json(url, obs)

        self.log.info("Assert value is the same as injected via FROST")
        d = get_json(self.sta_url + f"/Datastreams({temp_id})/Observations",
                     params={"$top": "1", "$orderBy": "phenomenonTime asc"})
        self.assertAlmostEqual(d["value"][0]["result"], df["TEMP"].values[0])

        self.log.info("Get FROST averaged data using /Observations endpoint with filter...")
        data = get_json(self.sta_url + f"/Observations", params={"$filter": f"Datastream/id eq {temp_id}"})
        results = data["value"]
        self.assertEqual(len(results), len(df))

        self.log.info("Get FROST averaged data using /Datastream(xx)/Observations endpoint")
        data = get_json(self.sta_url + f"/Datastreams({temp_id})/Observations", params={"$top": 1000})
        results = data["value"]
        self.assertEqual(len(results), len(df))

        # self.log.info("Get STA-Timeseries averaged data using /Observations endpoint with filter...")
        # data = get_json(self.sta_ts_url + f"/Observations", params={"$filter": f"Datastream/id eq {temp_id}"})
        # results = data["value"]
        # self.assertEqual(len(results), len(df))
        #
        # self.log.info("Get STA-Timeseries averaged data using /Datastream(xx)/Observations endpoint")
        # data = get_json(self.sta_ts_url + f"/Datastreams({temp_id})/Observations", params={"$top": 1000})
        # results = data["value"]
        # self.assertEqual(len(results), len(df))

        self.dc.sta.check_data_integrity()

    def test_31_bulk_load_raw_timeseries_data(self):
        """Ingesting average timeseries data using the API"""
        # Generate sine wave values
        frequency = 3
        dates = pd.date_range(start='2023-01-01', end="2023-03-31", freq='100s')
        tvector = np.arange(0, len(dates)) / len(dates)
        # Create a pandas DataFrame
        df = pd.DataFrame({
            'timestamp': dates,
            "TEMP": np.sin(2 * np.pi * frequency * tvector),
            "CNDC": np.cos(2 * np.pi * frequency * tvector)
        })
        df["TEMP_QC"] = 1
        df["CNDC_QC"] = 1
        df["timestamp"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        sta = self.dc.sta

        filename = "test31.csv"
        df.to_csv(filename, index=False)
        bulk_load_data(filename, self.conf, "SBE37", "timeseries", "OBSEA", tmp_folder="./tmpdata")

        self.info("Now, let's get the data and check that it's the same")
        temp_id = sta.get_datastream_id("SBE37", "OBSEA", "TEMP", "timeseries")
        # Now, let's download all the data that we injected, see if it's available
        data = get_json(self.sta_ts_url + f"/Datastreams({temp_id})/Observations?$top=1000000")
        results = data["value"]
        self.assertEqual(len(results), len(tvector))
        self.dc.sta.check_data_integrity()

        self.info("Let's make sure that we have an exception when try to load 2 times the same data")
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            bulk_load_data(filename, self.conf, "SBE37", "timeseries", "OBSEA", tmp_folder="./tmpdata")

        self.info("Let's delete some data and try to reload the gaps with missing-data")
        sta.exec_query(f"delete from timeseries where timestamp between '2023-02-01T00:00:00Z' and "
                       f"'2023-02-28T00:00:00Z';", fetch=False)

        bulk_load_data(filename, self.conf, "SBE37", "timeseries", "OBSEA",
                       tmp_folder="./tmpdata", missing_data="direct")

        self.info("Now, let's get the data and check that it's the same")
        data = get_json(self.sta_ts_url + f"/Datastreams({temp_id})/Observations?$top=1000000")

        # after all this, the number of rows should be the same
        self.assertEqual(len(tvector), len(data["value"]))


        self.info("Now, try to fill data gaps with hourly data")
        sta.exec_query(f"delete from timeseries where timestamp between '2023-02-01T00:00:00Z' and "
                       f"'2023-02-02T00:00:00Z';", fetch=False)

        dates = pd.date_range(start='2023-01-01', end="2023-03-31", freq='30min')  # create a different frequency
        tvector = np.arange(0, len(dates)) / 1000
        # Create a pandas DataFrame
        df = pd.DataFrame({
            'timestamp': dates,
            "TEMP": np.sin(2 * np.pi * frequency * tvector),
            "CNDC": np.cos(2 * np.pi * frequency * tvector)
        })
        df["TEMP_QC"] = 1
        df["CNDC_QC"] = 1
        df["timestamp"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

        filename = "test31.csv"
        df.to_csv(filename, index=False)

        data = get_json(self.sta_ts_url + f"/Datastreams({temp_id})/Observations?$top=1000000")
        rows_before = len(data["value"])
        bulk_load_data(filename, self.conf, "SBE37", "timeseries", "OBSEA",
                       tmp_folder="./tmpdata", missing_data="hourly")
        data = get_json(self.sta_ts_url + f"/Datastreams({temp_id})/Observations?$top=1000000")
        self.assertEqual(rows_before + 48, len(data["value"]))  # we should have now 48 more rows
        os.remove(filename)

    def test_32_get_raw_timeseries_data_api(self):
        """get timeseries from the API"""
        temp_id = self.dc.sta.get_datastream_id("SBE37", "OBSEA", "TEMP", "timeseries")
        url = self.sta_ts_url + f"/Datastreams({temp_id})/Observations?$orderBy=phenomenonTime asc&$top=1"
        data = get_json(url)
        first_value = data["value"][0]["result"]
        # Temperature first value is 0
        self.assertAlmostEqual(first_value, 0.0)
        self.dc.sta.check_data_integrity()

    def test_33_bulk_load_avg_timeseries_data(self):
        """Bulk load average timeseries data"""
        # Generate sine wave values
        frequency = 1

        tstart = "2022-01-01T00:00:00Z"
        tend = "2022-03-31T00:00:00Z"

        dates = pd.date_range(start=tstart, end=tend, freq='30min')
        self.info(f"Bulk loading a LOT of averaged data ({len(dates)} points)")
        tvector = np.arange(0, len(dates)) / len(dates)
        # Create a pandas DataFrame
        df = pd.DataFrame({
            'timestamp': dates,
            "TEMP": np.sin(2 * np.pi * frequency * tvector),
            "CNDC": np.cos(2 * np.pi * frequency * tvector)
        })
        df["TEMP_QC"] = 1
        df["CNDC_QC"] = 1

        df["timestamp"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        sta = self.dc.sta
        sta.initialize_dicts()  # update dicts
        filename = "test33.csv"
        df.to_csv(filename, index=False)

        start = pd.Timestamp(df["timestamp"].min())
        end = pd.Timestamp(df["timestamp"].max())

        rich.print("==== ALL deployments")
        deployments = self.mc.get_sensor_deployments("SBE37")
        rich.print(deployments)

        rich.print("==== Interval deployments")
        deployments = self.mc.get_sensor_deployments("SBE37", interval=(start, end))

        bulk_load_data(filename, self.conf, "SBE37", "timeseries", "OBSEA",
                       tmp_folder="./tmpdata", average="30min")
        os.remove(filename)
        self.info("Now, let's get the data and check that it's the same")
        temp_id = sta.get_datastream_id("SBE37", "OBSEA", "TEMP", "timeseries", average="30min")
        # Now, let's download all the data that we injected, see if it's available
        data = get_json(self.sta_ts_url + f"/Datastreams({temp_id})/Observations",
                        params={
                            "$top": "10000000",
                            "$filter": f"phenomenonTime ge 2021-12-31T00:00:01Z and phenomenonTime le 2023-02-05T00:00:01Z"
                        })

        results = data["value"]
        rich.print(f"We should have {len(df)} data opints, got {len(results)}")
        rich.print(results[0])
        rich.print(results[-1])

        rich.print("now vector")
        rich.print(dates[0])
        rich.print(dates[-1])
        self.assertEqual(len(results), len(tvector))
        self.dc.sta.check_data_integrity()

    def test_40_ingest_avg_profile_data(self):
        """Ingesting average timeseries data using the API"""
        # Generate sine wave values
        frequency = 1
        dates = pd.date_range(start='2023-01-01', end="2023-01-02", freq='12h')
        depths = np.arange(0, 10)
        tvector = np.arange(0, len(dates)) / 1000
        data = {
            "timestamp": [],
            "depth": [],
            "CSPD": [],
            "CDIR": [],
            "UCUR": [],
            "VCUR": []
        }
        for date, t in zip(dates, tvector):
            for depth in depths:
                data["timestamp"].append(date)
                data["depth"].append(depth)
                for var in ["CSPD", "CDIR", "UCUR", "VCUR"]:
                    data[var].append(np.sin(2 * np.pi * frequency * t))

        # Create a pandas DataFrame
        df = pd.DataFrame(data)
        df["timestamp"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        sta = self.dc.sta
        foi_id = sta.value_from_query('select "ID" from "FEATURES" limit 1;')
        sta.initialize_dicts()  # update dicts
        for var in ["CSPD", "CDIR", "UCUR", "VCUR"]:
            datastream_id = sta.get_datastream_id("AWAC", "OBSEA", var, "profiles", average="30min")
            for indx, row in df.iterrows():
                obs = {
                    "phenomenonTime": row["timestamp"],
                    "result": row[var],
                    "resultQuality": {"qc_flag": 1},
                    "FeatureOfInterest": {"@iot.id": foi_id},
                    "parameters": {"depth": row["depth"]}
                }
                url = self.sta_url + f"/Datastreams({datastream_id})/Observations"
                post_json(url, obs)

        # We should not able to insert it more than once
        with self.assertRaises(ConnectionError):
            post_json(url, obs)
        self.dc.sta.check_data_integrity()

    def test_41_ingest_raw_profile_data(self):
        """Ingesting raw profiles data using the API"""
        # Generate sine wave values
        frequency = 1
        dates = pd.date_range(start='2023-01-01', end="2023-01-02", freq='30min')
        depths = np.arange(0, 10)
        tvector = np.arange(0, len(dates)) / 1000
        data = {
            "timestamp": [],
            "depth": [],
            "CSPD": [],
            "CDIR": [],
            "UCUR": [],
            "VCUR": []
        }
        variables = ["CSPD", "CDIR", "UCUR", "VCUR"]
        for date, t in zip(dates, tvector):
            for depth in depths:
                data["timestamp"].append(date)
                data["depth"].append(depth)
                for var in variables:
                    data[var].append(np.sin(2 * np.pi * frequency * t))

        # Create a pandas DataFrame
        df = pd.DataFrame(data)
        df["timestamp"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Add Quality Control
        for var in variables:
            df[var + "_QC"] = 1

        foi_id = self.dc.sta.value_from_query('select "ID" from "FEATURES" limit 1;')

        filename = "test41.csv"
        df.to_csv(filename)
        with self.assertRaises(AssertionError):
            bulk_load_data(filename, self.conf, "AWAC", "banana", "OBSEA", tmp_folder="./tmpdata")

        # Now use the correct data type
        bulk_load_data(filename, self.conf, "AWAC", "profiles", "OBSEA", tmp_folder="./tmpdata")
        os.remove(filename)

        # Now, let's download all the data that we injected, see if it's available
        self.log.info(f"Get profile data from Datastreams/Observations")
        cdir_id = self.dc.sta.get_datastream_id("AWAC", "OBSEA", "CDIR", "profiles")
        data = get_json(self.sta_ts_url + f"/Datastreams({cdir_id})/Observations?$top=100000")
        results = data["value"]
        self.assertEqual(len(results), len(df))

        # Now, let's get the same data, but to general Observations endpoint and filtering by datastream
        self.log.info(f"Get profile data from Observations with filter")
        data = get_json(self.sta_ts_url + f"/Observations?$top=100000&$filter=Datastream/id eq {cdir_id}")
        results = data["value"]
        self.assertEqual(len(results), len(df))
        self.dc.sta.check_data_integrity()

    def test_42_add_profile_to_ctd(self):
        """Add profile data to a sensor that has both timeseries and profile data"""
        frequency = 1
        dates = pd.date_range(start='2023-01-01', end="2023-01-02", freq='30min')
        depths = np.arange(0, 10)
        tvector = np.arange(0, len(dates)) / 1000
        data = {
            "timestamp": [],
            "depth": [],
            "TEMP": [],
            "CNDC": []
        }
        variables = ["TEMP", "CNDC"]
        for date, t in zip(dates, tvector):
            for depth in depths:
                data["timestamp"].append(date)
                data["depth"].append(depth)
                for var in variables:
                    data[var].append(np.sin(2 * np.pi * frequency * t))

        # Create a pandas DataFrame
        df = pd.DataFrame(data)
        df["timestamp"] = df["timestamp"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Add Quality Control
        for var in variables:
            df[var + "_QC"] = 1

        foi_id = self.dc.sta.value_from_query('select "ID" from "FEATURES" limit 1;')

        filename = "test42.csv"
        df.to_csv(filename)

        # Now use the correct data type
        bulk_load_data(filename, self.conf, "SBE37", "profiles", "OBSEA",
                       tmp_folder="./tmpdata")
        os.remove(filename)

        # Now, let's download all the data that we injected, see if it's available
        self.log.info(f"Get profile data from Datastreams/Observations")
        temp_id = self.dc.sta.get_datastream_id("SBE37", "OBSEA", "TEMP", "profiles")
        data = get_json(self.sta_ts_url + f"/Datastreams({temp_id})/Observations?$top=100000")
        results = data["value"]
        self.assertEqual(len(results), len(df))

        # Now, let's get the same data, but to general Observations endpoint and filtering by datastream
        self.log.info(f"Get profile data from Observations with filter")
        data = get_json(self.sta_ts_url + f"/Observations?$top=100000&$filter=Datastream/id eq {temp_id}")
        results = data["value"]
        self.assertEqual(len(results), len(df))
        self.dc.sta.check_data_integrity()

    def test_50_ingest_pics(self):
        """Create and ingest picture data"""

        width, height = 2000, 2000  # Define the dimensions of the image

        self.log.info("Creating fake pictures...")
        files = []
        for i in range(1, 10):
            image = Image.new('RGB', (width, height), 'white')  # Create a white background image
            draw = ImageDraw.Draw(image)
            top_left = (50 + i * 10, 50 + i * 10)  # Top-left corner of the rectangle
            bottom_right = (150 + i * 10, 150 + i * 10)  # Bottom-right corner of the rectangle
            rectangle_color = 'blue'  # Color of the rectangle
            draw.rectangle([top_left, bottom_right], fill=rectangle_color)
            f = f'rectangle_blue_{i:02d}.jpg'
            image.save(f)  # Save the image to a file
            files.append(f)

        self.log.info("Creating Observations with the pictures...")
        dates = pd.date_range(start='2023-01-01', end="2023-01-02", freq='30min')
        fileserver = self.dc.fileserver
        datastream_id = self.dc.sta.get_datastream_id("IPC608", "OBSEA", "underwater_photography", "files")
        for i in range(len(files)):
            file = files[i]
            path = fileserver.send_file(f"pictures/IPC608", file)
            foi_id = self.dc.sta.value_from_query('select "ID" from "FEATURES" limit 1;')
            d = {
                "phenomenonTime": dates[i].strftime('%Y-%m-%dT%H:%M:%SZ'),
                "result": path,
                "FeatureOfInterest": {"@iot.id": foi_id}
            }
            url = self.sta_url + f"/Datastreams({datastream_id})/Observations"
            post_json(url, d)

        with self.assertRaises(ConnectionError):
            post_json(url, d)

        # Now, let's download all the data that we injected, see if it's available
        data = get_json(self.sta_url + f"/Datastreams({datastream_id})/Observations")
        results = data["value"]
        self.assertEqual(len(results), len(files))

        # Now download all files
        for result in results:
            print(result["result"])
            retrieve_url(result["result"], output="image.png", timeout=1, attempts=1)
            os.remove("image.png")

        # Remove created files
        for file in files:
            os.remove(file)

    def test_51_ingest_pics_inference_detections(self):
        """Creating picture for bulk load pictures, inferences and detections"""
        width, height = 2000, 2000  # Define the dimensions of the image

        pictures = []
        for i in range(1, 100):
            image = Image.new('RGB', (width, height), 'white')  # Create a white background image
            draw = ImageDraw.Draw(image)
            top_left = (50 + i * 10, 50 + i * 10)  # Top-left corner of the rectangle
            bottom_right = (150 + i * 10, 150 + i * 10)  # Bottom-right corner of the rectangle
            rectangle_color = 'red'  # Color of the rectangle
            draw.rectangle([top_left, bottom_right], fill=rectangle_color)
            filename = f"rectangle_red_{i:03d}.jpg"
            image.save(filename)  # Save the image to a file
            pictures.append(filename)

        # Let's create a csv file for file bulk load
        data = {
            "timestamp": [],
            "results": [],
            "datastream_id": [],
            "foi_id": []
        }
        start = "2023-02-01T00:00:00Z"
        end = "2023-03-01T00:00:00Z"
        dates = pd.date_range(start=start, end=end, freq='30min')
        datastream_id = self.dc.sta.get_datastream_id("IPC608", "OBSEA", "underwater_photography", "files")
        foi_id = self.dc.sta.value_from_query('select "ID" from "FEATURES" limit 1;')
        for i in range(len(pictures)):
            url = self.dc.fileserver.send_file("pictures/IPC608", pictures[i])
            data["timestamp"].append(dates[i].strftime('%Y-%m-%dT%H:%M:%SZ'))
            data["results"].append(url)
            data["datastream_id"].append(datastream_id)
            data["foi_id"].append(foi_id)
            os.remove(pictures[i])
        df = pd.DataFrame(data)
        datafile = "test51-files.csv"
        df.to_csv(datafile)

        # Now, bulk load it!
        bulk_load_data(datafile, self.conf, "IPC608", "files", "OBSEA",
                       tmp_folder="./tmpdata")
        os.remove(datafile)

        # Now, let's download all the data that we injected, see if it's available
        data = get_json(self.sta_url + f"/Datastreams({datastream_id})/Observations?$filter=phenomenonTime ge {start}")
        results = data["value"]
        self.assertEqual(len(results), len(pictures))
        # Now download all files
        for result in results:
           retrieve_url(result["result"])

        # Now create inference data
        # Let's create a csv file for file bulk load
        data = {
            "timestamp": [],
            "results": [],
            "datastream_id": [],
            "foi_id": []
        }
        dates = pd.date_range(start='2023-02-01', end="2023-03-01", freq='30min')
        datastream_id = self.dc.sta.get_datastream_id("IPC608", "OBSEA", "FATX", "json")
        foi_id = self.dc.sta.value_from_query('select "ID" from "FEATURES" limit 1;')
        for i in range(len(pictures)):
            data["timestamp"].append(dates[i].strftime('%Y-%m-%dT%H:%M:%SZ'))
            data["results"].append({"someInferenceData": i})
            data["datastream_id"].append(datastream_id)
            data["foi_id"].append(foi_id)
        df = pd.DataFrame(data)
        datafile = "test51-inference.csv"
        df.to_csv(datafile)

        bulk_load_data(datafile, self.conf, "IPC608", "files", "OBSEA",
                       tmp_folder="./tmpdata")
        os.remove(datafile)

        # Now, let's download all the data that we injected, see if it's available
        data = get_json(self.sta_url + f"/Datastreams({datastream_id})/Observations?$filter=phenomenonTime ge {start}")
        results = data["value"]
        self.assertEqual(len(results), len(pictures))

        # Now, let's create some of detections
        data = {
            "timestamp": [],
            "results": [],
            "datastream_id": [],
            "foi_id": []
        }
        dates = pd.date_range(start='2023-02-01', end="2023-03-01", freq='30min')
        chromis_id = self.dc.sta.get_datastream_id("IPC608", "OBSEA", "chromis_chromis", "detections")
        diplodus_id = self.dc.sta.get_datastream_id("IPC608", "OBSEA", "diplodus_vulgaris", "detections")
        foi_id = self.dc.sta.value_from_query('select "ID" from "FEATURES" limit 1;')
        for i in range(len(pictures)):
            # chromis
            data["timestamp"].append(dates[i].strftime('%Y-%m-%dT%H:%M:%SZ'))
            data["results"].append(4)
            data["datastream_id"].append(chromis_id)
            data["foi_id"].append(foi_id)

            # diplodus
            data["timestamp"].append(dates[i].strftime('%Y-%m-%dT%H:%M:%SZ'))
            data["results"].append(2)
            data["datastream_id"].append(diplodus_id)
            data["foi_id"].append(foi_id)

        df = pd.DataFrame(data)
        datafile = "test51-detections.csv"
        df.to_csv(datafile)
        bulk_load_data(datafile, self.conf, "IPC608", "detections", "OBSEA",
                       tmp_folder="./tmpdata")
        os.remove(datafile)

        # Now, let's download all the data that we injected, see if it's available
        data = get_json(self.sta_ts_url + f"/Datastreams({chromis_id})/Observations?$filter=phenomenonTime ge {start}")
        results = data["value"]
        self.assertEqual(len(results), len(pictures))

        # Now, let's download all the data that we injected, see if it's available (only via sta-timeseries API)
        data = get_json(self.sta_ts_url + f"/Datastreams({diplodus_id})/Observations?$filter=phenomenonTime ge {start}")
        results = data["value"]
        self.assertEqual(len(results), len(pictures))

    def test_60_no_full_data_in_observations(self):
        sta = self.dc.sta
        sta.initialize_dicts()
        # Make sure that we do not have any fullData timeseries/profiles/detections in OBSERVATIONS
        self.log.info("Making sure that we don't have any timeseries, profiles or detections in the OBSERVATIONS table")
        wrong_datastreams = sta.timescale.check_data_in_observations()
        self.assertEqual(len(wrong_datastreams), 0)

        self.log.info("Force timeseries into OBSERVATIONS to get an exception")
        datastream_id = sta.get_datastream_id("SBE37", "OBSEA", "TEMP", "timeseries")
        foi_id = sta.datastream_properties[datastream_id]["defaultFeatureOfInterest"]
        d = {
            "phenomenonTime": "2000-01-01T00:00:00Z",
            "result": 3.14,
            "Datastream": {"@iot.id": datastream_id},
            "FeatureOfInterest": {"@iot.id": foi_id}
        }
        post_json(self.sta_url + "/Observations", d)

        lvl = self.log.getEffectiveLevel()
        self.log.setLevel(logging.CRITICAL)

        with self.assertRaises(ValueError):
            sta.timescale.check_data_in_observations(raise_exception=True)

        wrong_datastreams = sta.timescale.check_data_in_observations(raise_exception=False)
        self.log.setLevel(lvl)
        self.assertEqual(len(wrong_datastreams), 1)

    def test_61_correct_data_in_hypertables(self):
        """check that only the correct data is stored in the hypertables"""
        sta = self.dc.sta
        lvl = self.log.getEffectiveLevel()
        self.log.setLevel(logging.CRITICAL)

        errors = sta.timescale.check_data_in_hypertables()
        self.assertEqual(len(errors), 0)

        # Now let's force some errors and catch the exceptions

        timeseries_id = sta.value_from_query(
            'select "ID" from "DATASTREAMS" where '
            '   "PROPERTIES"->>\'dataType\' = \'timeseries\''
            '   and ("PROPERTIES"->>\'fullData\')::boolean = true'
            '   limit 1;'
        )

        profile_id = sta.value_from_query(
            'select "ID" from "DATASTREAMS" where '
            '   "PROPERTIES"->>\'dataType\' = \'profiles\''
            '   and ("PROPERTIES"->>\'fullData\')::boolean = true'
            '   limit 1;'
        )

        detections_id = sta.value_from_query(
            'select "ID" from "DATASTREAMS" where '
            '   "PROPERTIES"->>\'dataType\' = \'detections\''
            '   and ("PROPERTIES"->>\'fullData\')::boolean = true'
            '   limit 1;'
        )

        # Adding 3 wrong elements
        sta.timescale.insert_to_timeseries("2020-01-01T00:00:00z", 3.14, 1, profile_id)
        sta.timescale.insert_to_detections("2020-01-01T00:00:00z", 15, timeseries_id)
        sta.timescale.insert_to_profiles("2020-01-01T00:00:00z", 13.01, 3.14, 1, detections_id)

        with self.assertRaises(ValueError):
            sta.timescale.check_data_in_hypertables()

        wrong_ids = sta.timescale.check_data_in_hypertables(raise_exception=False)

        self.log.setLevel(lvl)

        self.assertEqual(len(wrong_ids), 3)
        self.assertIn(timeseries_id, wrong_ids)
        self.assertIn(profile_id, wrong_ids)
        self.assertIn(detections_id, wrong_ids)

        self.log.info("Deleting wrong observations...")
        self.dc.sta.exec_query(f"delete from timeseries where datastream_id = {profile_id};", fetch=False)
        self.dc.sta.exec_query(f"delete from detections where datastream_id = {timeseries_id};", fetch=False)
        self.dc.sta.exec_query(f"delete from profiles where datastream_id = {detections_id};", fetch=False)

    def test_70_propagate_to_ckan(self):
        propagate_metadata_to_ckan(self.mc, self.ckan, self.log, collections=[])

    def test_71_generate_fileserver_datasets(self):
        """Creating a dataset"""
        os.makedirs("datasets", exist_ok=True)

        # Export datasets with the default format (NetCDF)
        nc_datasets = self.dc.generate_dataset("obsea_ctd_full", "fileserver", overwrite=True)
        for nc_dataset in nc_datasets:
            self.assertTrue(check_url(nc_dataset.url))

        # delete one dataset and ensure that we get an error when accessing it
        file_path = self.dc.fileserver.url2path(nc_datasets[0].url)
        os.remove(file_path)
        self.assertFalse(check_url(nc_datasets[0].url))

        # overwrite datasets
        nc_datasets = self.dc.generate_dataset("obsea_ctd_full", "fileserver", overwrite=True)
        for nc_dataset in nc_datasets:
            self.assertTrue(check_url(nc_dataset.url))

        # Export as CSV datasets
        csv_datasets = self.dc.generate_dataset("obsea_ctd_full", "fileserver", fmt="csv", overwrite=True)
        for csv_dataset in csv_datasets:
            self.assertTrue(check_url(csv_dataset.url))

        # Export NetCDF
        nc_datasets = self.dc.generate_dataset("obsea_ctd_30min", "fileserver", overwrite=True)
        for nc_dataset in nc_datasets:
            self.assertTrue(check_url(nc_dataset.url))

        # Force error in format
        with self.assertRaises(AssertionError):
            self.dc.generate_dataset("obsea_ctd_full", "erddap", "2020-01-01", "2030-02-01", fmt="potato")

        zip_datasets = self.dc.generate_dataset("IPC608_pics", "fileserver", "2020-01-01", "2020-02-01")
        for zip_dataset in zip_datasets:
            if not zip_dataset:
                continue
            self.assertTrue(check_url(zip_dataset.url))

        dwca_dataset = self.dc.generate_dataset("biodiversity_datasets", "fileserver", "2020-01-01", "2020-02-01")

    def test_72_generate_ckan_datasets(self):
        self.dc.generate_dataset("obsea_ctd_full", "ckan", "2020-01-01", "2021-02-01") # default format
        self.dc.generate_dataset("obsea_ctd_full", "ckan", "2020-01-01", "2021-02-01", fmt="csv") # froce csv
        self.dc.generate_dataset("obsea_ctd_30min", "ckan", "2020-01-01", "2021-02-01")
        self.dc.generate_dataset("IPC608_pics", "ckan", "2020-01-01", "2020-02-01")

    def test_80_config_erddap(self):
        """creates a dataset and upload it to ERDDAP"""

        rich.print("")

        nc_datasets = self.dc.generate_dataset("obsea_ctd_full", "erddap", "2020-01-01", "2020-02-01")
        for nc_dataset in nc_datasets:
            # Convert from host path to erddap container path, otherwise ERDDAP will not see the files
            data_path = nc_dataset.exporter.path.replace("./datasets", "/datasets")
            nc_dataset.configure_erddap("conf/datasets.xml", data_path)
            nc_dataset.reload_erddap_dataset("erddapData")
            self.info("Wait 5 seconds for ERDDAP to reload...")
            time.sleep(5)
            # Now get ERDDAP data!
            erddap_dataset = "mydataset.csv"
            dataset_url = "http://localhost:8090/erddap/tabledap/" + nc_dataset.erddap_dataset_id + ".csv"
            self.info(f"Downloading dataset from erddap: {dataset_url}")
            download_file(dataset_url, erddap_dataset)
            df = pd.read_csv(erddap_dataset)
            print(df)

    def test_81_config_erddap_with_daily_data(self):
        """creates a dataset with daily files and upload it to ERDDAP"""
        nc_datasets = self.dc.generate_dataset("obsea_ctd_30min", "erddap", "2022-01-01", "2022-02-01")
        nc_dataset = nc_datasets[0]
        # Convert from host path to erddap container path, otherwise ERDDAP will not see the files
        data_path = nc_dataset.exporter.path.replace("./datasets", "/datasets")
        nc_dataset.configure_erddap("conf/datasets.xml", data_path)
        nc_dataset.reload_erddap_dataset("erddapData")

        # Now get ERDDAP data!
        erddap_dataset = "mydataset.csv"
        dataset_url = "http://localhost:8090/erddap/tabledap/" + nc_dataset.erddap_dataset_id + ".csv"
        self.info(f"Downloading dataset from erddap: {dataset_url}")
        retrieve_url(dataset_url, output=erddap_dataset)
        df = pd.read_csv(erddap_dataset)

        datasets = self.dc.generate_dataset("obsea_ctd_30min", "erddap", "2022-01-01", "2022-01-01")


    @classmethod
    def tearDownClass(cls):
        os.remove("ckan.key")
        # input("press key to remove docker volumes...")
        # cls.log.info("stopping containers")
        # run_subprocess("docker compose down")
        # rich.print("Deleting temporal docker volumes...")
        # for volume in cls.docker_volumes:
        #     if os.path.isfile(volume):
        #         continue  # ignore volume files
        #
        #     for f in file_list(volume):
        #         os.remove(f)
        #
        #     # now remove any subdirs
        #     subdir_list = dir_list(volume)
        #     subdir_list = list(reversed(sorted(subdir_list)))
        #     for subdir in subdir_list:
        #         if os.path.isfile(subdir):
        #             raise ValueError("This should not happen!")
        #         os.rmdir(subdir)
        #
        # for volume in cls.docker_volumes:
        #     if os.path.isdir(volume):
        #         os.rmdir(volume)


if __name__ == "__main__":
    init = time.time()
    unittest.main(failfast=True, verbosity=1)
    rich.print(f"Total testing time {time.time() - init:.02f} secs")
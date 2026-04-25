#!/usr/bin/env python3
"""
This package contains the python function to generate the Datastreams produced by different processes. For instance,
the "average" process will generaet Datastreams with the average data, the "json" type will create detections...

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 19/2/24
"""

from .average import average_process
from .inference import inference_process
from .aneris_aies_mac import aneris_aies_mac_process_20260414
#!/usr/bin/env python3
"""
Backup SensorThings Database and transfers it to a remote server

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 21/06/2022
"""

import datetime
import os
import time
import logging
from logging.handlers import TimedRotatingFileHandler


database_name = "sensorthings"
local_backup_folder = "/var/tmp/sensorthings_db_backup"
backup_file_prefix = "sensorthings_db"
log_path = "/home/enoc/logs"

remote_backup_folder = "/opt/backups/sensorthings"
remote_host = "egi-backups"


def setup_log(name, path, logger_name="backup"):
    level = logging.DEBUG
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
    logger.info(f"===== {logger_name} =====")
    return logger


if __name__ == "__main__":
    init = time.time()
    log = setup_log("sta-backup", log_path)
    log.info("Start SensorThings backup service")
    if not os.path.isdir(local_backup_folder):
        log.info("Creating backup folder...")
        os.makedirs(local_backup_folder, exist_ok=True, mode=0o777)

    tmp_backup = "/var/tmp/sta.bak"  # create in /var/tmp to avoid permissions issues
    log.info(f"Creating backup in {tmp_backup}")
    connection_chain = f"postgres://sensorthings:ChangeMe@localhost:5432/sensorthings"
    backup_cmd = f"pg_dump -Fc -f {tmp_backup} --lock-wait-timeout=6000  {database_name}"
    log.info(f"   backup command: {backup_cmd}")
    t = time.time()
    r = os.system(backup_cmd)
    if r != 0:
        log.error(f"pg_dump returned {r}!")
        exit()
    log.info(f"Backup finished, took {time.time() - t:.04f} seconds")
    backup_file = f"{backup_file_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    backup_file = os.path.join(local_backup_folder, backup_file)
    log.debug(f"changing owner to {os.getlogin()} (uid {os.getuid()}")
    os.system(f"sudo chown enoc:enoc {tmp_backup}")
    log.debug(f"archiving backup with name: {backup_file}")
    os.rename(tmp_backup, backup_file)
    log.info("Transfer backup using rsync...")
    t = time.time()
    r = os.system(f"rsync -az {backup_file} {remote_host}:{remote_backup_folder}")
    if r != 0:
        log.error(f"rsync returned {r}!")
        exit()

    log.info(f"Transfer finished, took {time.time() - t:.04f} seconds")
    log.info(f"Total backup time {time.time() - init:.04f} seconds")
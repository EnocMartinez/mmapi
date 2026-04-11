#!/usr/bin/env python3
"""
Generates all real-time datasets

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 4/10/23
"""

from argparse import ArgumentParser
import yaml
import rich
import pandas as pd
from pandas.tseries.offsets import Day, MonthBegin, YearBegin

from generate_dataset import generate_dataset
from mmm import setup_log
from mmm.common import str_to_timerange
from mmm.metadata_collector import init_metadata_collector
from mmm.schemas import valid_dataset_services, dataset_exporter_periods



def calculate_current_period(period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Return the current time period as (start, end) timestamps in UTC.
    """

    assert period in dataset_exporter_periods, f"period {period} not valid"
    if period == "none":
        raise ValueError("Cannot calculate current period for 'none' period")

    now = pd.Timestamp.now(tz="UTC")

    if period == "daily":
        start = now.normalize()
        end = start + Day(1)
    elif period == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, nanosecond=0)
        end = start + MonthBegin(1)
    elif period == "yearly":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0, nanosecond=0)
        end = start + YearBegin(1)
    else:
        raise ValueError(f"Unsupported period: {period}. Expected one of: daily, monthly, yearly")

    return start, end

def ensure_last_dataset(mc, dataset_id, service, resource_id, cstart, cend):
    """
    Checks if the last dataset was completed, by ensuring that modification_date > data_to in the dataset_registry
    :param mc:
    :param service:
    :param resource_id:
    :return:
    """
    q = f"""
        select data_from, data_to, modification_date 
        from dataset_registry 
        where dataset_id='{dataset_id}' and service='{service}' and resource_id='{resource_id}'
        order by modification_date desc limit 1;
        """
    results = mc.db.exec_query(q)
    assert len(results) == 1, f"Dataset not found! dataset_id='{dataset_id}' and service='{service}' and resource_id='{resource_id}'"

    data_from, data_to, modification_date = [pd.Timestamp(x) for x in results[0]]

    # If dataset was generated before the end of the per (e.g. daily period, dataset generated at 23:00)
    if modification_date < data_to:
        cstart = data_from
    return cstart, cend


def main():
    argparser = ArgumentParser()
    argparser.add_argument("-l", "--log-level", help="Log level", type=str, required=False, default="info")
    argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False, default="secrets.yaml")
    argparser.add_argument("--current", help="Current active period, e.g. last day or last month", action="store_true")
    argparser.add_argument("--time-range", help="Time range to generate dataset", type=str, default="")
    argparser.add_argument("services", help="Another argument", type=str, nargs="+")

    args = argparser.parse_args()
    log.setLevel(args.log_level.upper())
    with open(args.secrets) as f:
        secrets = yaml.safe_load(f)["secrets"]

    mc = init_metadata_collector(secrets, log)


    log.info("Getting all datasets")
    datasets = mc.get_documents("datasets")

    # Get list of all real-time datasets
    datasets = [d for d in datasets if d["dataMode"] == "real-time"]
    dataset_ids = [d["#id"] for d in datasets]
    rich.print(dataset_ids)

    sensors = []
    for d in datasets:
        dataset_id = d["#id"]
        sensor_ids = d["@sensors"]

        # Check if any sensor is active
        active = False
        for s in sensor_ids:
            _, _, is_active = mc.get_last_sensor_deployment(s)
            if is_active:
                active = True
                break

        if not active:
            log.info(f"No deployed sensors for dataset {dataset_id}, skipping")
            continue

        # Generate the dataset for the selected services
        for service in args.services:
            assert service in valid_dataset_services, f"Service {service} not valid"

            # Generate all the resources
            for resource in d["export"][service]["resources"]:
                resource_id = resource["id"]
                if args.time_range:
                    start, end = str_to_timerange(args.time_range)
                elif args.current:
                    # Generate only the last period
                    period = resource["period"]
                    start, end = calculate_current_period(period)
                    # Let's make sure that the last dataset was completed (if day/month changes last chunk my not be generated)
                    start, end = ensure_last_dataset(mc, dataset_id, service, resource_id, start, end)

                log.info(f"Generating dataset={dataset_id} service={service} resource={resource_id} start={start} end={end}")
                generate_dataset(dataset_id, service, start, end, args.secrets, log,  overwrite=True, resources=[resource_id])
                    

if __name__ == "__main__":
    log = setup_log("all_datasets")
    try:
        main()
    except Exception as e:
        log.exception("Unhandled exception while processing dataset export")
        

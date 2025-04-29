from mmm.parallelism import multiprocess
from mmm.data_manipulation import slice_dataframe_by_columns, resample_dataframe, resample_polar_dataframe, merge_dataframes_by_columns, merge_dataframes
from mmm import MetadataCollector
import time
import rich

def resample(mc: MetadataCollector, sensor_id: str,  df, period: str):
    """
    Averages data from a dataframe into another dataframe
    :param mc: MetadataCollector object
    :param sensor_id: Sensor identifier
    :param df: input dataframe
    :param period: time to average
    :returns: averaged dataset
    """
    sensor = mc.get_sensor(sensor_id)
    # Create a list of all the variables that have the average property set to false
    ignore = [var["@variables"] for var in sensor["variables"] if "average" in var.keys() and not var["average"]]

    log_variables = mc.get_log_variables(sensor_id)
    modules, angles = mc.get_polar_variables(sensor_id)

    if not modules:
        averaged_df = resample_dataframe(df, average_period=period, log_vars=log_variables, ignore=ignore)
    else:  # keep resampling for every element in module/angle pair
        arguments = []
        t = time.time()
        for i in range(len(modules)):
            print(f"Resampling columns {modules[i]} and {angles[i]}...")
            temp_df = slice_dataframe_by_columns(df, [modules[i], angles[i], modules[i] + "_QC", angles[i] + "_QC"])
            arguments.append([temp_df, modules[i], angles[i], "degrees", period, log_variables])

        dataframes = multiprocess(arguments, resample_polar_dataframe, text="resampling polar dataframe...")

        # resample the rest of the columns the usual way
        rich.print("[cyan]Resampling non-polar variables")
        for var in df.columns:
            if var in modules or var in angles:
                del df[var]

        ignore += angles + modules
        rest = resample_dataframe(df, average_period=period, log_vars=log_variables, ignore=ignore)
        dataframes.append(rest)

        rich.print("Individual resample finished, merging columns into a single dataframe...")
        averaged_df = merge_dataframes_by_columns(dataframes)
        rich.print(f"Resampling complete, took f{time.time() - t : 0.2f} seconds")
    return averaged_df
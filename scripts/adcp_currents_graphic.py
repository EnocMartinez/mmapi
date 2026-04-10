    #!/usr/bin/env python3
    """
    This script creates a graphic with the latest data of an ADCP with current speed.
    
    author: Enoc Martínez
    institution: Universitat Politècnica de Catalunya (UPC)
    email: enoc.martinez#upc.edu
    license: MIT
    created: 21/09/2023
    """
    import pandas as pd
    import rich
    from argparse import ArgumentParser
    import yaml
    import os
    import sys
    import pandas as pd
    import numpy as np
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    try:
        from mmm import init_data_collector
    except ModuleNotFoundError:
        current_dir = os.path.dirname(os.path.abspath(__file__)) # Get the directory of the current script
        parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir)) # Get the parent directory (project root)
        sys.path.insert(0, parent_dir) # Add the parent directory to the sys.path

    from mmm import init_data_collector, setup_log


    if __name__ == "__main__":
        argparser = ArgumentParser()
        argparser.add_argument("-s", "--secrets", help="Another argument", type=str, required=False, default="secrets.yaml")
        argparser.add_argument("-t", "--time-range", help="Time range to generate the graphic (<start>/<end>)", type=str, required=False, default="")
        argparser.add_argument("sensor", help="Sensor Name", type=str)
        argparser.add_argument("output", help="output filename", type=str)
        argparser.add_argument("--show", help="Show the plot", action="store_true")
        argparser.add_argument("-m", "--min-depth", help="Minimum depth", type=int)
        args = argparser.parse_args()

        speed_std_name = "sea_water_speed"

        log = setup_log("adcp_graphics", log_level="info")
        if not args.time_range:
            log.info("Using last 24h of data")
            now = pd.Timestamp.now()
            end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            start = now - pd.Timedelta("24h")
            start = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            a, b = args.time_range.split("/")
            start = pd.Timestamp(a)
            end = pd.Timestamp(b)

        log.info(f"Creating {speed_std_name} data from {start} to {end}")

        with open(args.secrets) as f:
            secrets = yaml.safe_load(f)["secrets"]

        dc = init_data_collector(secrets, log)
        sensor_id = dc.sta.value_from_query(f'select "ID" from "SENSORS" where "NAME" = \'{args.sensor}\';')

        query = f"""
            select "DATASTREAMS"."ID" from "DATASTREAMS", "OBS_PROPERTIES"
            where "OBS_PROPERTIES"."ID" = "DATASTREAMS"."OBS_PROPERTY_ID" and
            "OBS_PROPERTIES"."PROPERTIES"->>'standard_name' = '{speed_std_name}'
            and "DATASTREAMS"."PROPERTIES"->'fullData' = 'true'
            and "DATASTREAMS"."SENSOR_ID" = {sensor_id}
            ;
            """

        datastream_id = dc.sta.value_from_query(query)


        query = (f"select timestamp, value, depth from profiles where datastream_id = {datastream_id} and "
                 f"timestamp between '{start}' and '{end}' "             
                 f"order by timestamp asc;")
        df = dc.sta.dataframe_from_query(query)


        # Suppose df is your DataFrame with 'timestamp', 'depth', 'speed' columns
        # Make sure 'timestamp' is datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        if args.min_depth:
            log.info(f"Keeping only data with depth > {args.min_depth}")
            df = df[df["depth"] > args.min_depth]

        # Pivot the data into a 2D matrix: rows = depth, columns = timestamp
        pivot_table = df.pivot(index='depth', columns='timestamp', values='value')


        # Sort axes
        pivot_table = pivot_table.sort_index().sort_index(axis=1)

        # Create meshgrid for plotting
        X, Y = np.meshgrid(pivot_table.columns, pivot_table.index)

        # Plot using pcolormesh
        fig, ax = plt.subplots(figsize=(12, 6))

        c = ax.pcolormesh(X, Y, pivot_table.values, shading='auto', cmap='viridis')

        ax.invert_yaxis()
        plt.title('sea water speed')

        # Format x-axis for time
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M:%S'))
        plt.xticks(rotation=45)
        ax.set_xlabel('time')
        ax.set_ylabel('depth (m)')

        # [left, bottom, width, height] in figure coordinates (0 to 1)
        cbar_ax = fig.add_axes([0.15, 0.1, 0.7, 0.02])  # Adjust height (last value) and vertical position
        cbar = fig.colorbar(c, cax=cbar_ax, orientation='horizontal')
        cbar.set_label('speed (m/s)')

        plt.tight_layout()
        fig.subplots_adjust(bottom=0.4)
        if args.show:
            plt.show()
        fig.savefig(args.output)

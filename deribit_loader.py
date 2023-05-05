import requests
import time
import pandas as pd
from datetime import datetime
import logging
import gc
import concurrent.futures
import os

logging.basicConfig(
    filename='log/deribit_loader.log',
    encoding='utf-8', 
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')

logger = logging.getLogger(__name__)

# Deribit API v2 base URL
base_url = "https://www.deribit.com/api/v2/"

# folder where will be stored the downloaded market data
data_folder = 'data'

coins = ['BTC', 'ETH']
timeframe = '5m'

timeframe_minutes = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440
}

def fetch_option_data(inst):
    """
    Fetches order book data for the specified option instrument.

    Args:
        inst (str): Name of the option instrument to fetch data for.

    Returns:
        dict: The order book data for the specified instrument.
    """
    endpoint = "public/get_order_book"
    params = {
        "instrument_name": inst,
        "depth": 1
    }

    retries = 5
    while retries > 0:
        try:
            response = requests.get(base_url + endpoint, params=params)
        except Exception as e:
            logger.error(f"Failed to fetch option data for {inst}.\nException arised during request: {e}")
            return None
        result = response.json()
        if 'error' in result and result['error']['code'] == 10028:
            wait_time = int(response.headers.get('retry-after', '2'))
            logger.info(f"Too many requests, waiting for {wait_time} seconds")
            time.sleep(wait_time)
            retries -= 1
        elif 'result' in result:
            return result['result']
    
    # if we get here, it means the request failed
    logger.error(f"Failed to fetch option data for {inst}.\nUnexpected response: {result}")
    return None



def fetch_available_instruments(currency):
    """
    Fetches the available option instruments for the specified currency.

    Args:
        currency (str): The currency to fetch option instruments for.

    Returns:
        dict: The available option instruments for the specified currency.
    """
    endpoint = "public/get_instruments"
    params = {
        "currency": currency,
        "depth": 1,
        "kind" : "option",
        "expired" : "false"
    }
    
    response = requests.get(base_url + endpoint, params=params)
    return response.json()

def save_to_parquet(df, filename):
    """
    Saves the specified Pandas DataFrame to a Parquet file.

    Args:
        df (pd.DataFrame): The DataFrame to save.
        filename (str): The name of the file to save the DataFrame to.
    """
    try:
        df.to_parquet(filename)
    except Exception as e:
        logger.error(f"Failed to save\n {df}\n with exception {e}")


def fetch_and_save_data(currencies):
    """
    Fetches and saves the options data for given currencies to Parquet files.
    """
    logger.info(f"Starting job at {datetime.now()}")

    instruments_per_request = 40 

    for currency in currencies:
        instruments = [inst['instrument_name'] for inst in fetch_available_instruments(currency)['result']]
        instruments_lists = [instruments[i:i+instruments_per_request] for i in range(0, len(instruments), instruments_per_request)]

        options_data = pd.DataFrame()
        for inst_list in instruments_lists:
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                result = list(executor.map(fetch_option_data, inst_list))
                # Filter out None values from the result list
                result = [r for r in result if r is not None]
                if len(result) > 0:
                    options_data = pd.concat([options_data, pd.DataFrame(result)], axis=0)
            time.sleep(1)

        # get the UTC time at the start of the current day
        current_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y%m%d")

        # aggregate new data with existing data
        agg_file_name = f"{data_folder}/{currency.lower()}_{timeframe}_{current_day}_options_data.parquet"
        if os.path.exists(agg_file_name):
            options_data = pd.concat([pd.read_parquet(agg_file_name),options_data], axis = 0)

        save_to_parquet(options_data, agg_file_name)

        del options_data
        gc.collect()

    logger.info(f"Job completed at {datetime.now()}")


def get_next_run_time(timeframe):
    """
    Get the time until the next multiple of `timeframe` minutes and 00 seconds
    """
    current_time = time.localtime() 
    next_run = (timeframe_minutes[timeframe] - current_time.tm_min % timeframe_minutes[timeframe]) * 60 \
                - current_time.tm_sec

    return next_run % (timeframe_minutes[timeframe] * 60)


if __name__ == '__main__':
    logger.info("Loader started")

    while True:
        # Pause until the next multiple of `timeframe` minutes and 00 seconds
        next_run = get_next_run_time(timeframe)
        logger.info(f"next run in {next_run} seconds")
        time.sleep(next_run)

        # Run the job
        fetch_and_save_data(coins)
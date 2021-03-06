import json
import requests
import logging
from itertools import count
from tqdm.auto import tqdm
from pandas.core.frame import DataFrame
from typing import Iterable
import pandas as pd

RAI_SUBGRAPH_URL = 'https://api.thegraph.com/subgraphs/name/reflexer-labs/rai-mainnet'

def yield_hourly_stats(feed_size: int=1000) -> Iterable[dict]:
    """
    Generate a feed of hourly stats with timestamp, blockNumber, 
    and price fields.
    """

    # Query template
    query_header = '''
    query {{
        hourlyStats(first: {}, skip:{}) {{'''

    query_tail = '''    
    }
    }'''

    query_body = '''
        timestamp
        blockNumber
        marketPriceUsd # price of COIN in USD (uni pool price * ETH median price)
        marketPriceEth # Price of COIN in ETH (uni pool price)
    '''

    # Iterate until there's no yielded data
    counter = count(0)
    while True:
        position = next(counter) * feed_size

        # Prepare query
        query = query_header.format(feed_size, position)
        query += query_body
        query += query_tail

        # Send a POST request and parse
        r = requests.post(RAI_SUBGRAPH_URL, json={'query': query})
        s = json.loads(r.content)['data']['hourlyStats']

        # Yield if there's data, else break
        if len(s) > 0:
            yield s
        else:
            break


def retrieve_hourly_stats() -> DataFrame:
    # Retrieve hourly stats batches and transform into a single list of dicts
    gen_expr = (iter_hourly for
                iter_hourly
                in tqdm(yield_hourly_stats(),
                        desc='Retrieving hourly stats'))
    hourly_records: list[dict] = sum(gen_expr, [])

    # Clean-up to a pandas data frame
    hourlyStats = (pd.DataFrame
                   .from_records(hourly_records)
                   .applymap(pd.to_numeric)
                   .assign(timestamp=lambda df: pd.to_datetime(df.timestamp, unit='s'))
                   .assign(eth_price=lambda df: df.marketPriceUsd / df.marketPriceEth)
                   .set_index('blockNumber')
                   )
    hourlyStats.index.name = 'block_number'
    return hourlyStats


def retrieve_system_states(block_numbers: list[int]) -> DataFrame:
    """
    Retrieve a DataFrame representing the system state for all ETH
    block heights given as a input.
    """

    QUERY_TEMPLATE = """
    {
      systemState(block: {number:%s},id:"current") { 
        coinUniswapPair {
          label
          reserve0
          reserve1
          token0Price
          token1Price
          totalSupply
        }
        currentCoinMedianizerUpdate{
          value
        }
        currentRedemptionRate {
          eightHourlyRate
          annualizedRate
          hourlyRate
          createdAt
        }
        currentRedemptionPrice {
          value
        }
        erc20CoinTotalSupply
        globalDebt
        globalDebtCeiling
        safeCount,
        totalActiveSafeCount
        coinAddress
        wethAddress
        systemSurplus
        debtAvailableToSettle
        lastPeriodicUpdate
        createdAt
        createdAtBlock
      }
    }
    """

    # Loop through system states
    state = []
    null_count = count(0)
    for block_number in tqdm(block_numbers, desc='Retrieving System States'):
        # Execute query
        query = QUERY_TEMPLATE % block_number
        r = requests.post(RAI_SUBGRAPH_URL, json={'query': query})
        s = json.loads(r.content)['data']['systemState']
        s['block_number'] = block_number

        # Append if the row is valid, drop if coinUniswapPair info is missing
        if s['coinUniswapPair'] is not None:
            state.append(s)
        else:
            next(null_count)
    
    # Warn if there are null rows
    null_rows = next(null_count) - 1
    if null_rows > 0:
        logging.warning(f"There are null coinUniswapPair rows, they were dropped. ({null_rows} null rows, {null_rows / (len(state) + null_rows): .2%} of total)")


    # Transform output into a DataFrame
    systemState = pd.DataFrame(state)
    systemState = systemState.set_index('block_number')

    # Extract nested data
    systemState['RedemptionRateAnnualizedRate'] = systemState.currentRedemptionRate.apply(
        lambda x: x['annualizedRate'])
    systemState['RedemptionRateHourlyRate'] = systemState.currentRedemptionRate.apply(
        lambda x: x['hourlyRate'])
    systemState['RedemptionRateEightHourlyRate'] = systemState.currentRedemptionRate.apply(
        lambda x: x['eightHourlyRate'])
    systemState['RedemptionPrice'] = systemState.currentRedemptionPrice.apply(
        lambda x: x['value'])
    systemState['EthInUniswap'] = systemState.coinUniswapPair.apply(
        lambda x: x['reserve1'])
    systemState['RaiInUniswap'] = systemState.coinUniswapPair.apply(
        lambda x: x['reserve0'])
    systemState['RaiDrawnFromSAFEs'] = systemState['erc20CoinTotalSupply']

    # Remove nested columns
    del systemState['currentRedemptionRate']
    del systemState['currentRedemptionPrice']

    # Filter columns
    systemState = systemState[['debtAvailableToSettle',
                               'globalDebt',
                               'globalDebtCeiling',
                               'systemSurplus',
                               'totalActiveSafeCount',
                               'RedemptionRateAnnualizedRate',
                               'RedemptionRateHourlyRate',
                               'RedemptionRateEightHourlyRate',
                               'RedemptionPrice',
                               'EthInUniswap',
                               'RaiInUniswap',
                               'RaiDrawnFromSAFEs']]
    return systemState


def retrieve_safe_history(block_numbers: list[int]) -> DataFrame:
    safehistories = []
    for i in tqdm(block_numbers, desc='Retrieving SAFEs History'):
        query = '''
        {
        safes(block: {number:%s}) {
                collateral
                debt
        }
        }
        ''' % i
        r = requests.post(RAI_SUBGRAPH_URL, json={'query': query})
        s = json.loads(r.content)['data']['safes']
        t = pd.DataFrame(s)
        t['collateral'] = t['collateral'].astype(float)
        t['debt'] = t['debt'].astype(float)
        safehistories.append(pd.DataFrame(t.sum().to_dict(), index=[0]))

    safe_history = (pd.concat(safehistories)
                    .assign(block_number=block_numbers)
                    .set_index('block_number')
                    )
    return safe_history


def download_data(limit=None,
                  date_range=None) -> DataFrame:
    """
    Retrieve all historical data required for backtesting & extrapolation
    """
    # Get hourly stats from The Graph
    hourly_stats = retrieve_hourly_stats()

    # Filter hourly date if requested, else, use everything
    if date_range is not None:
        QUERY = f'timestamp >= "{date_range[0]}" & timestamp < "{date_range[1]}"'
        hourly_stats = hourly_stats.query(QUERY)
    else:
        pass

    # Get the first hourly results if requested
    if limit is not None:
        hourly_stats = hourly_stats.head(limit)
    else:
        pass

    # Retrieve block numbers
    block_numbers = hourly_stats.index
    
    # Get associated system states & safe state for each block numbers
    dfs = (retrieve_system_states(block_numbers),
           retrieve_safe_history(block_numbers),
           hourly_stats)

    # Join everything together
    historical_df = pd.concat(dfs, join='inner', axis=1)

    # Return Data Frame
    return historical_df.reset_index(drop=False)

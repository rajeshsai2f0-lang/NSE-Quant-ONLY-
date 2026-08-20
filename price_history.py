"""
Shared daily-OHLCV fetcher.

Both liquidity_rush.py (needs the last 10/20 trading days) and
relative_strength.py (needs the last 126 trading days) used to each pull
their own copy of every ticker's price history from yfinance — two
network calls per ticker per run instead of one. This module pulls it
ONCE per unique ticker (9 months of daily bars, which comfortably covers
both windows) and hands the same DataFrame to whichever metric needs it.

Usage:
    histories = fetch_price_history(tickers, market="NSE")
    # histories: {"TCS": DataFrame_or_None, ...}
    fetch_liquidity_rush(tickers, market="NSE", price_histories=histories)
    fetch_relative_strength(tickers, market="NSE", price_histories=histories)

Both consumer functions still work standalone (price_histories=None) —
they'll just do their own fetch, same as before this change.
"""

import concurrent.futures

import pandas as pd
import yfinance as yf

MAX_WORKERS = 8
HISTORY_PERIOD = "9mo"      # covers Liquidity Rush's 20d window AND
                             # Relative Strength's 126d window
HISTORY_INTERVAL = "1d"

YF_SUFFIX_BY_MARKET = {"NSE": ".NS", "US": ""}


def _fetch_one(ticker, yf_suffix):
    symbol = ticker + yf_suffix
    try:
        data = yf.download(symbol, period=HISTORY_PERIOD, interval=HISTORY_INTERVAL, progress=False)
    except Exception as e:
        return ticker, None, str(e)

    if data is None or data.empty:
        return ticker, None, "no rows returned"

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data["Typical"] = (data["High"] + data["Low"] + data["Close"]) / 3   # used by liquidity_rush
    return ticker, data, None


def fetch_price_history(tickers, market="NSE", yf_suffix=None):
    """
    tickers: iterable of bare symbols (no exchange suffix).
    market:  "NSE" or "US" — controls the ticker suffix used for lookup.

    Returns {TICKER: DataFrame_or_None} — one yfinance download per
    unique ticker, meant to be reused by every downstream metric that
    needs daily OHLCV instead of each one fetching its own copy.
    """
    market = (market or "NSE").upper()
    if yf_suffix is None:
        yf_suffix = YF_SUFFIX_BY_MARKET.get(market, "")

    tickers = sorted(set(str(t).strip().upper() for t in tickers if t and str(t).strip()))
    histories = {}
    if not tickers:
        return histories

    ok_count = 0
    fail_examples = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, t, yf_suffix): t for t in tickers}
        for fut in concurrent.futures.as_completed(futures):
            ticker = futures[fut]
            try:
                symbol, data, err = fut.result()
                histories[symbol] = data
                if data is not None:
                    ok_count += 1
                elif len(fail_examples) < 5:
                    fail_examples.append(f"{symbol} ({err})")
            except Exception as e:
                histories[ticker] = None
                if len(fail_examples) < 5:
                    fail_examples.append(f"{ticker} (exception: {e})")

    fail_count = len(tickers) - ok_count
    print(f"   \U0001f4e1  Price history: {ok_count}/{len(tickers)} ticker(s) fetched (shared by "
          f"Liquidity Rush + Relative Strength)"
          f"{'' if fail_count == 0 else f', {fail_count} returned no/insufficient data'}")
    if fail_examples:
        print(f"       e.g. {', '.join(fail_examples)}{' ...' if fail_count > len(fail_examples) else ''}")

    return histories

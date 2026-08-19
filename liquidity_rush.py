"""
╔══════════════════════════════════════════════════════════════════════════╗
║   LIQUIDITY RUSH — VWAP × Average Volume, over trailing 10d / 20d        ║
╠══════════════════════════════════════════════════════════════════════════╣
║  LiquidityRush_Nd = avg(VWAP, last N trading days) * avg(Volume, last N  ║
║                     trading days)                                       ║
║  %ofMCAP_Nd       = LiquidityRush_Nd / MarketCap * 100                  ║
║                                                                          ║
║  yfinance only gives daily OHLCV (no intraday tick data), so "VWAP" per ║
║  day is approximated with the typical price (High + Low + Close) / 3 —  ║
║  the standard stand-in used when true intraday VWAP isn't available.    ║
║                                                                          ║
║  Units:                                                                 ║
║    NSE stocks -> LiquidityRush expressed in ₹ Crores  (raw / 1e7)       ║
║    US  stocks -> LiquidityRush expressed in $ Millions (raw / 1e6)      ║
║                                                                          ║
║  %ofMCAP assumes the Market Cap value handed in is ALREADY in that same ║
║  unit (Chartink's own "Market Cap" column is in ₹ Cr for NSE), so the   ║
║  percentage is a straight ratio of the two — no further conversion.     ║
║  If you wire in a market-cap source that's in raw currency instead,     ║
║  convert it to the same unit before calling attach_liquidity_columns(). ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import concurrent.futures

import pandas as pd
import yfinance as yf

MAX_WORKERS = 8
PERIODS = (10, 20)
HISTORY_PERIOD = "3mo"      # comfortably covers 20 trading days + holidays
HISTORY_INTERVAL = "1d"     # always DAILY bars — "last 10/20 days" means
                             # trading days, regardless of whether the
                             # caller's own pipeline is running weekly or
                             # daily scoring.

UNIT_DIVISOR = {"NSE": 1e7, "US": 1e6}     # ₹ Crores vs $ Millions
UNIT_LABEL = {"NSE": "Cr", "US": "M"}
YF_SUFFIX_BY_MARKET = {"NSE": ".NS", "US": ""}


def _fetch_one(ticker, yf_suffix, min_bars):
    symbol = ticker + yf_suffix
    try:
        data = yf.download(symbol, period=HISTORY_PERIOD, interval=HISTORY_INTERVAL, progress=False)
    except Exception:
        return ticker, None
    if data is None or data.empty or len(data) < min_bars:
        return ticker, None
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data["Typical"] = (data["High"] + data["Low"] + data["Close"]) / 3
    return ticker, data


def _compute_metrics(data, unit_divisor):
    """Returns {10: liquidity_rush_10d, 20: liquidity_rush_20d} in the
    caller's display unit (already divided by unit_divisor). A period with
    fewer available bars than needed gets None."""
    out = {}
    for period in PERIODS:
        if len(data) < period:
            out[period] = None
            continue
        recent = data.tail(period)
        avg_vwap = float(recent["Typical"].mean())
        avg_volume = float(recent["Volume"].mean())
        raw = avg_vwap * avg_volume
        out[period] = raw / unit_divisor
    return out


def fetch_liquidity_rush(tickers, market="NSE", yf_suffix=None):
    """
    tickers: iterable of bare symbols (no exchange suffix — e.g. "TCS", not
             "TCS.NS").
    market:  "NSE" or "US" — controls the ticker suffix used for the
             yfinance lookup and the unit (Cr vs M) the result is expressed
             in.
    yf_suffix: override the suffix yfinance needs (defaults per `market`).

    Returns {TICKER: {10: liquidity_rush_10d_or_None, 20: liquidity_rush_20d_or_None}}
    """
    market = (market or "NSE").upper()
    unit_divisor = UNIT_DIVISOR.get(market, 1e7)
    if yf_suffix is None:
        yf_suffix = YF_SUFFIX_BY_MARKET.get(market, "")
    min_bars = max(PERIODS)

    tickers = sorted(set(str(t).strip().upper() for t in tickers if t and str(t).strip()))
    results = {}
    if not tickers:
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, t, yf_suffix, min_bars): t for t in tickers}
        for fut in concurrent.futures.as_completed(futures):
            ticker = futures[fut]
            try:
                symbol, data = fut.result()
                if data is None:
                    results[symbol] = {p: None for p in PERIODS}
                else:
                    results[symbol] = _compute_metrics(data, unit_divisor)
            except Exception as e:
                print(f"   ⚠️  Liquidity Rush fetch failed for {ticker}: {e}")
                results[ticker] = {p: None for p in PERIODS}
    return results


def attach_liquidity_columns(df, liquidity_metrics, ticker_col="Ticker", mcap_col="Market Cap"):
    """
    Adds 4 columns to a copy of df:
      LiquidityRush10days, %ofMCAP10days, LiquidityRush20days, %ofMCAP20days
    Missing/insufficient history or missing/zero Market Cap -> blank (NaN),
    so the columns stay numeric (not mixed text) for Excel formatting.
    """
    if df.empty or ticker_col not in df.columns:
        return df

    def _mcap_val(raw):
        try:
            v = float(str(raw).replace(",", "").strip())
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    lr10, pct10, lr20, pct20 = [], [], [], []
    has_mcap = mcap_col in df.columns
    for _, row in df.iterrows():
        ticker = str(row.get(ticker_col, "")).strip().upper()
        m = liquidity_metrics.get(ticker, {})
        r10, r20 = m.get(10), m.get(20)
        mcap = _mcap_val(row.get(mcap_col)) if has_mcap else None

        lr10.append(round(r10, 2) if r10 is not None else None)
        lr20.append(round(r20, 2) if r20 is not None else None)
        pct10.append(round(r10 / mcap * 100, 2) if (r10 is not None and mcap) else None)
        pct20.append(round(r20 / mcap * 100, 2) if (r20 is not None and mcap) else None)

    df = df.copy()
    df["LiquidityRush10days"] = lr10
    df["%ofMCAP10days"] = pct10
    df["LiquidityRush20days"] = lr20
    df["%ofMCAP20days"] = pct20
    return df

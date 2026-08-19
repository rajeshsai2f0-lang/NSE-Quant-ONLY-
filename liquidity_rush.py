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
║  unit. Market Cap can come from three places, in priority order:       ║
║    1. Chartink's own "Market Cap" column (₹ Cr for NSE) if present.     ║
║    2. A "Market Cap" column supplied in a watchlist Excel file.         ║
║    3. yfinance's fast_info.market_cap, fetched here automatically for   ║
║       any ticker missing #1/#2, converted from raw currency into the   ║
║       same unit (Cr / M) before use.                                   ║
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


def _fetch_market_cap(symbol, yf_ticker=None):
    """
    Tries yfinance's fast_info first (cheap, single request). fast_info's
    market_cap is frequently missing/None for NSE tickers, so if that comes
    up empty this falls back to the slower `.info` dict (a second network
    call) which fills in market cap far more often.

    Returns (market_cap_or_None, source_str) where source_str is one of
    "fast_info", "info", or None (both attempts failed/empty).
    """
    t = yf_ticker or yf.Ticker(symbol)

    try:
        fi = t.fast_info
        raw_mcap = fi.get("market_cap") if hasattr(fi, "get") else getattr(fi, "market_cap", None)
        if raw_mcap:
            return float(raw_mcap), "fast_info"
    except Exception:
        pass

    try:
        info = t.info  # slower — triggers a full quote-summary request
        raw_mcap = info.get("marketCap") if info else None
        if raw_mcap:
            return float(raw_mcap), "info"
    except Exception:
        pass

    return None, None


def _fetch_one(ticker, yf_suffix, min_bars):
    symbol = ticker + yf_suffix

    # ── Price history (for LiquidityRush) ───────────────────────────────
    try:
        data = yf.download(symbol, period=HISTORY_PERIOD, interval=HISTORY_INTERVAL, progress=False)
    except Exception as e:
        data = None
        data_err = str(e)
    else:
        data_err = None

    # ── Market cap (for %ofMCAP fallback) ───────────────────────────────
    mcap, mcap_source = _fetch_market_cap(symbol)

    if data is None or data.empty or len(data) < min_bars:
        reason = data_err or ("no rows returned" if data is None or data.empty
                               else f"only {len(data)} of {min_bars} required bars")
        return ticker, None, mcap, mcap_source, reason

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data["Typical"] = (data["High"] + data["Low"] + data["Close"]) / 3
    return ticker, data, mcap, mcap_source, None


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

    Returns {TICKER: {10: liquidity_rush_10d_or_None,
                       20: liquidity_rush_20d_or_None,
                       "market_cap": raw_market_cap_or_None}}
    market_cap is in RAW currency units (not Cr/M) — attach_liquidity_columns
    converts it to the display unit before using it.
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

    ok_count = 0
    fail_examples = []
    mcap_ok = 0
    mcap_from_fast_info = 0
    mcap_from_info = 0
    mcap_fail_examples = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, t, yf_suffix, min_bars): t for t in tickers}
        for fut in concurrent.futures.as_completed(futures):
            ticker = futures[fut]
            try:
                symbol, data, mcap, mcap_source, fail_reason = fut.result()

                if mcap is not None:
                    mcap_ok += 1
                    if mcap_source == "fast_info":
                        mcap_from_fast_info += 1
                    elif mcap_source == "info":
                        mcap_from_info += 1
                elif len(mcap_fail_examples) < 5:
                    mcap_fail_examples.append(symbol)

                if data is None:
                    results[symbol] = {**{p: None for p in PERIODS}, "market_cap": mcap}
                    if len(fail_examples) < 5:
                        fail_examples.append(f"{symbol} ({fail_reason})")
                else:
                    metrics = _compute_metrics(data, unit_divisor)
                    metrics["market_cap"] = mcap
                    results[symbol] = metrics
                    ok_count += 1
            except Exception as e:
                print(f"   ⚠️  Liquidity Rush fetch failed for {ticker}: {e}")
                results[ticker] = {**{p: None for p in PERIODS}, "market_cap": None}
                if len(fail_examples) < 5:
                    fail_examples.append(f"{ticker} (exception: {e})")

    fail_count = len(tickers) - ok_count
    print(f"   \U0001f4a7  Liquidity Rush: {ok_count}/{len(tickers)} ticker(s) had usable yfinance history"
          f"{'' if fail_count == 0 else f', {fail_count} returned no/insufficient data'}")
    if fail_examples:
        print(f"       e.g. {', '.join(fail_examples)}"
              f"{' ...' if fail_count > len(fail_examples) else ''}")
        print("       (blank LiquidityRush/%ofMCAP cells for these tickers are expected — "
              "not a wiring bug. If EVERY ticker fails, yfinance is likely being "
              "rate-limited/blocked on this runner, not a code issue.)")

    mcap_fail_count = len(tickers) - mcap_ok
    print(f"   \U0001f3e6  Market Cap (yfinance): {mcap_ok}/{len(tickers)} ticker(s) resolved "
          f"({mcap_from_fast_info} via fast_info, {mcap_from_info} via .info fallback)"
          f"{'' if mcap_fail_count == 0 else f', {mcap_fail_count} unresolved'}")
    if mcap_fail_examples:
        print(f"       no market cap for: {', '.join(mcap_fail_examples)}"
              f"{' ...' if mcap_fail_count > len(mcap_fail_examples) else ''}")

    return results


def attach_liquidity_columns(df, liquidity_metrics, ticker_col="Ticker", mcap_col="Market Cap", market="NSE"):
    """
    Adds 4 columns to a copy of df:
      LiquidityRush10days, %ofMCAP10days, LiquidityRush20days, %ofMCAP20days
    Missing/insufficient history -> blank (NaN) for the LiquidityRush columns.

    Market Cap resolution order (used only for %ofMCAP, and also written
    back into `mcap_col` when it was blank so the sheet is self-contained):
      1. The value already present in df[mcap_col] for that row, if any.
      2. liquidity_metrics[ticker]["market_cap"] fetched from yfinance,
         converted from raw currency into the same unit (Cr for NSE,
         M for US) as LiquidityRush.
    """
    if df.empty or ticker_col not in df.columns:
        return df

    unit_divisor = UNIT_DIVISOR.get((market or "NSE").upper(), 1e7)

    def _mcap_val(raw):
        try:
            v = float(str(raw).replace(",", "").strip())
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    lr10, pct10, lr20, pct20, mcap_final = [], [], [], [], []
    has_mcap = mcap_col in df.columns
    online_mcap_used = 0

    for _, row in df.iterrows():
        ticker = str(row.get(ticker_col, "")).strip().upper()
        m = liquidity_metrics.get(ticker, {})
        r10, r20 = m.get(10), m.get(20)

        mcap = _mcap_val(row.get(mcap_col)) if has_mcap else None
        if mcap is None:
            raw_online = m.get("market_cap")
            if raw_online:
                mcap = raw_online / unit_divisor
                online_mcap_used += 1

        lr10.append(round(r10, 2) if r10 is not None else None)
        lr20.append(round(r20, 2) if r20 is not None else None)
        pct10.append(round(r10 / mcap * 100, 2) if (r10 is not None and mcap) else None)
        pct20.append(round(r20 / mcap * 100, 2) if (r20 is not None and mcap) else None)
        mcap_final.append(round(mcap, 2) if mcap is not None else None)

    df = df.copy()
    df[mcap_col] = mcap_final   # fills gaps with yfinance data; leaves existing values as-is
    df["LiquidityRush10days"] = lr10
    df["%ofMCAP10days"] = pct10
    df["LiquidityRush20days"] = lr20
    df["%ofMCAP20days"] = pct20

    if online_mcap_used:
        print(f"   \U0001f310  Market Cap: filled in from yfinance for {online_mcap_used}/{len(df)} row(s) "
              f"in this sheet (no Market Cap supplied by the source)")

    return df

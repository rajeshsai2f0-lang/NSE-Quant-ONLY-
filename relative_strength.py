"""
╔══════════════════════════════════════════════════════════════════════════╗
║   RELATIVE STRENGTH — vs NIFTY 50 / NIFTY SMALLCAP 100                  ║
╠══════════════════════════════════════════════════════════════════════════╣
║  RS_vs_<BENCHMARK>     = stock's % price return over the lookback window ║
║                          minus the benchmark's % return over the same    ║
║                          window. Positive = stock beat the benchmark.    ║
║                                                                          ║
║  RS_Rating_<BENCHMARK> = 1-99 percentile rank of RS_vs_<BENCHMARK>       ║
║                          WITHIN today's scanned universe (every ticker   ║
║                          passed in to fetch_relative_strength() for      ║
║                          this run) — same 1-99 shape as IBD's classic    ║
║                          RS Rating, but NOT ranked against the full NSE  ║
║                          market. A true market-wide percentile needs     ║
║                          full return history for ~2000+ NSE tickers      ║
║                          pulled every run — well past what free          ║
║                          yfinance polling can sustain. Read this as      ║
║                          "how today's setups rank against each other",  ║
║                          not an absolute score comparable across days.   ║
║                                                                          ║
║  Lookback: 126 trading days (~6 months) by default — long enough to      ║
║  filter out noise, short enough to stay relevant to a swing-trade        ║
║  horizon. Override via lookback_days= if you want 63d (~3mo) instead.    ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import concurrent.futures

import pandas as pd
import yfinance as yf

MAX_WORKERS = 8
LOOKBACK_DAYS = 126
HISTORY_PERIOD = "9mo"      # comfortably covers 126 trading days + holidays
HISTORY_INTERVAL = "1d"

# Yahoo Finance tickers for the two benchmarks.
BENCHMARKS = {
    "NIFTY50": "^NSEI",
    "SMALLCAP100": "^CNXSC",   # "NIFTY SMLCAP 100" — Yahoo kept the old CNX name
}

YF_SUFFIX_BY_MARKET = {"NSE": ".NS", "US": ""}


def _pct_return(data, lookback_days):
    """% price return over the trailing lookback_days trading bars, or
    None if there isn't enough history."""
    if data is None or data.empty or len(data) < lookback_days + 1:
        return None
    closes = data["Close"]
    start = float(closes.iloc[-(lookback_days + 1)])
    end = float(closes.iloc[-1])
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _download(symbol):
    try:
        data = yf.download(symbol, period=HISTORY_PERIOD, interval=HISTORY_INTERVAL, progress=False)
    except Exception:
        return None
    if data is None or data.empty:
        return None
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def _fetch_benchmark_returns(lookback_days):
    """Returns {benchmark_name: pct_return_or_None}."""
    out = {}
    for name, symbol in BENCHMARKS.items():
        data = _download(symbol)
        ret = _pct_return(data, lookback_days)
        out[name] = ret
        if ret is None:
            print(f"   ⚠️  Relative Strength: no usable history for benchmark {name} ({symbol}) — "
                  f"RS_vs_{name} / RS_Rating_{name} will be blank for every ticker this run.")
    return out


def _fetch_one(ticker, yf_suffix, lookback_days):
    symbol = ticker + yf_suffix
    data = _download(symbol)
    return ticker, _pct_return(data, lookback_days)


def _percentile_rank_1_99(value_by_ticker):
    """Ranks the non-None values 1-99 (99 = strongest relative strength),
    skipping tickers with no value. Ties share the average rank, same as
    IBD's own RS Rating convention."""
    valid = {t: v for t, v in value_by_ticker.items() if v is not None}
    if not valid:
        return {}
    s = pd.Series(valid)
    pct = s.rank(pct=True, method="average")   # 0.0 - 1.0
    scaled = (1 + pct * 98).round().astype(int)  # -> 1 - 99
    return scaled.to_dict()


def fetch_relative_strength(tickers, market="NSE", yf_suffix=None, lookback_days=LOOKBACK_DAYS):
    """
    tickers: iterable of bare symbols (no exchange suffix — e.g. "TCS", not
             "TCS.NS").
    market:  "NSE" or "US" — controls the ticker suffix used for the
             yfinance lookup.

    Returns {TICKER: {"RS_vs_NIFTY50": pct_or_None, "RS_Rating_NIFTY50": int_or_None,
                       "RS_vs_SMALLCAP100": pct_or_None, "RS_Rating_SMALLCAP100": int_or_None}}
    """
    market = (market or "NSE").upper()
    if yf_suffix is None:
        yf_suffix = YF_SUFFIX_BY_MARKET.get(market, "")

    tickers = sorted(set(str(t).strip().upper() for t in tickers if t and str(t).strip()))
    results = {}
    if not tickers:
        return results

    print("   📈  Relative Strength: fetching benchmark history (NIFTY50, SMALLCAP100)...")
    bench_returns = _fetch_benchmark_returns(lookback_days)

    stock_returns = {}
    ok_count = 0
    fail_examples = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, t, yf_suffix, lookback_days): t for t in tickers}
        for fut in concurrent.futures.as_completed(futures):
            ticker = futures[fut]
            try:
                symbol, ret = fut.result()
                stock_returns[symbol] = ret
                if ret is not None:
                    ok_count += 1
                elif len(fail_examples) < 5:
                    fail_examples.append(symbol)
            except Exception as e:
                stock_returns[ticker] = None
                if len(fail_examples) < 5:
                    fail_examples.append(f"{ticker} (exception: {e})")

    fail_count = len(tickers) - ok_count
    print(f"   📈  Relative Strength: {ok_count}/{len(tickers)} ticker(s) had usable yfinance history"
          f"{'' if fail_count == 0 else f', {fail_count} returned no/insufficient data'}")
    if fail_examples:
        print(f"       e.g. {', '.join(fail_examples)}{' ...' if fail_count > len(fail_examples) else ''}")
        print("       (blank RS_vs_*/RS_Rating_* cells for these tickers are expected — "
              "not a wiring bug.)")

    # Excess return vs each benchmark, then rank that excess 1-99 within
    # this run's ticker batch.
    excess = {name: {} for name in BENCHMARKS}
    for ticker, ret in stock_returns.items():
        for name, bench_ret in bench_returns.items():
            excess[name][ticker] = (
                (ret - bench_ret) if (ret is not None and bench_ret is not None) else None
            )
    ratings = {name: _percentile_rank_1_99(excess[name]) for name in BENCHMARKS}

    for ticker in tickers:
        entry = {}
        for name in BENCHMARKS:
            raw = excess[name].get(ticker)
            entry[f"RS_vs_{name}"] = round(raw, 2) if raw is not None else None
            entry[f"RS_Rating_{name}"] = ratings[name].get(ticker)
        results[ticker] = entry

    return results


def attach_relative_strength_columns(df, rs_metrics, ticker_col="Symbol"):
    """Adds 4 columns to a COPY of df: RS_vs_NIFTY50, RS_Rating_NIFTY50,
    RS_vs_SMALLCAP100, RS_Rating_SMALLCAP100. Tickers with no data get
    blank cells. Does not modify df in place."""
    if df.empty or ticker_col not in df.columns:
        return df
    df = df.copy()
    for name in BENCHMARKS:
        df[f"RS_vs_{name}"] = df[ticker_col].apply(
            lambda t, _n=name: rs_metrics.get(str(t).strip().upper(), {}).get(f"RS_vs_{_n}")
        )
        df[f"RS_Rating_{name}"] = df[ticker_col].apply(
            lambda t, _n=name: rs_metrics.get(str(t).strip().upper(), {}).get(f"RS_Rating_{_n}")
        )
    return df

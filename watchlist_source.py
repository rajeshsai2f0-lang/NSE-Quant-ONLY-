"""
╔══════════════════════════════════════════════════════════════════════════╗
║   WATCHLIST SOURCE — manually-curated Excel ticker lists                 ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Drop up to WATCHLIST_LIMIT .xlsx files into the watchlists/ folder and  ║
║  they become selectable alongside (or instead of) the Chartink           ║
║  screeners — see source_selector.py for the picker that wires this in.  ║
║                                                                          ║
║  Expected format (flexible):                                            ║
║    - One ticker per row, any sheet name, any sheet count.               ║
║    - Ticker column can be named Ticker / Symbol / NSE Code / NSECODE /  ║
║      Stock / Scrip (case-insensitive); if none of those exist, the      ║
║      FIRST column is used.                                              ║
║    - An optional Market Cap column (in ₹ Cr) is carried through so      ║
║      %ofMCAP10days / %ofMCAP20days can be computed without an extra     ║
║      yfinance lookup. If it's missing, those two columns are simply     ║
║      left blank for that ticker in the output Excel — liquidity_rush.py ║
║      does not currently fetch market cap on its own.                    ║
║  Every other column in the sheet is ignored.                            ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import os

import pandas as pd

WATCHLIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlists")
WATCHLIST_LIMIT = 10   # keeps the interactive picker / CI input list sane

TICKER_COL_CANDIDATES = ["ticker", "symbol", "nse code", "nsecode", "stock", "scrip"]
MCAP_COL_CANDIDATES = ["market cap", "mcap", "marketcap", "market cap (cr)"]


def list_watchlists():
    """Up to WATCHLIST_LIMIT .xlsx/.xls filenames found in watchlists/, sorted.
    Returns [] if the folder doesn't exist or is empty."""
    if not os.path.isdir(WATCHLIST_DIR):
        return []
    files = sorted(
        f for f in os.listdir(WATCHLIST_DIR)
        if f.lower().endswith((".xlsx", ".xls")) and not f.startswith("~$")
    )
    if len(files) > WATCHLIST_LIMIT:
        print(f"   ⚠️  {len(files)} watchlist files found, only using the first {WATCHLIST_LIMIT}: "
              f"{', '.join(files[:WATCHLIST_LIMIT])}")
    return files[:WATCHLIST_LIMIT]


def _detect_col(columns, candidates):
    lower_map = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    return None


def load_watchlist(filename):
    """
    Reads one watchlist Excel file (every sheet) and returns a single
    DataFrame with a "Ticker" column, plus "Market Cap" if any sheet had
    a recognizable market-cap column. Empty/unreadable files return an
    empty DataFrame rather than raising.
    """
    path = filename if os.path.isabs(filename) else os.path.join(WATCHLIST_DIR, filename)
    if not os.path.exists(path):
        print(f"   ⚠️  Watchlist not found: {path}")
        return pd.DataFrame()

    try:
        sheets = pd.read_excel(path, sheet_name=None)
    except Exception as e:
        print(f"   ❌  Could not read watchlist '{filename}': {e}")
        return pd.DataFrame()

    frames = []
    for sheet_name, sheet_df in sheets.items():
        if sheet_df.empty or len(sheet_df.columns) == 0:
            continue

        tcol = _detect_col(sheet_df.columns, TICKER_COL_CANDIDATES) or sheet_df.columns[0]
        out = pd.DataFrame()
        out["Ticker"] = (
            sheet_df[tcol].astype(str).str.strip().str.upper()
            .str.replace(r"\.NS$", "", regex=True)   # tolerate ".NS"-suffixed input
        )

        mcap_col = _detect_col(sheet_df.columns, MCAP_COL_CANDIDATES)
        if mcap_col:
            out["Market Cap"] = sheet_df[mcap_col]

        frames.append(out)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined[combined["Ticker"].str.strip().replace({"NAN": ""}) != ""]
    combined = combined.drop_duplicates(subset="Ticker").reset_index(drop=True)
    return combined


def load_watchlists(filenames):
    """
    Loads several watchlist files and returns them as a
    [(sheet_name, df), ...] list — the same shape
    chartink_screener.build_excel() expects for screener_results, so
    watchlist sheets render in the same workbook (and get the Liquidity
    Rush columns attached) alongside or instead of Chartink sheets.
    """
    results = []
    for fname in filenames:
        df = load_watchlist(fname)
        label = os.path.splitext(os.path.basename(fname))[0]
        if df.empty:
            print(f"   ⚠️  Watchlist '{fname}' had no usable tickers")
        else:
            print(f"   📄  Watchlist '{fname}': {len(df)} ticker(s)")
        results.append((f"Watchlist - {label}", df))
    return results

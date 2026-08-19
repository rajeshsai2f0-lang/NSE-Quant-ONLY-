"""
╔══════════════════════════════════════════════════════════════════════════╗
║   SOURCE SELECTOR — Chartink screeners / Excel watchlist(s) / both       ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Called once at the top of main.py / main_daily.py / main_combined.py    ║
║  so a run can pull its tickers from:                                    ║
║    1) Chartink screeners only (original behavior)                       ║
║    2) One or more Excel watchlists from watchlists/ (see                ║
║       watchlist_source.py) only                                         ║
║    3) Both, merged                                                      ║
║                                                                          ║
║  LOCAL runs (no TTY-less CI) get an interactive numbered menu.          ║
║  GITHUB ACTIONS runs (GITHUB_ACTIONS env var set, no stdin to prompt     ║
║  against) instead read:                                                 ║
║    SOURCE_MODE      = "chartink" | "watchlist" | "both"  (default        ║
║                       "chartink" — existing workflows keep working       ║
║                       unchanged unless you add the new workflow_dispatch ║
║                       input)                                             ║
║    WATCHLIST_FILES  = comma-separated filenames from watchlists/, e.g.  ║
║                       "momentum.xlsx,smallcap_ideas.xlsx" — leave blank  ║
║                       to use every watchlist file found (up to 10).      ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import os

from watchlist_source import list_watchlists

SOURCE_CHARTINK = "chartink"
SOURCE_WATCHLIST = "watchlist"
SOURCE_BOTH = "both"
_VALID_MODES = {SOURCE_CHARTINK, SOURCE_WATCHLIST, SOURCE_BOTH}


def _is_ci():
    return bool(os.environ.get("GITHUB_ACTIONS"))


def choose_source():
    """
    Returns (mode, selected_watchlist_filenames).
      mode: one of SOURCE_CHARTINK / SOURCE_WATCHLIST / SOURCE_BOTH
      selected_watchlist_filenames: [] when mode == SOURCE_CHARTINK,
        otherwise the list of watchlists/ filenames to load.
    """
    available = list_watchlists()

    if _is_ci():
        mode = os.environ.get("SOURCE_MODE", SOURCE_CHARTINK).strip().lower()
        if mode not in _VALID_MODES:
            print(f"⚠️  Unknown SOURCE_MODE '{mode}' — defaulting to 'chartink'")
            mode = SOURCE_CHARTINK

        if mode == SOURCE_CHARTINK:
            return mode, []

        raw_files = os.environ.get("WATCHLIST_FILES", "").strip()
        if raw_files:
            selected = [f.strip() for f in raw_files.split(",") if f.strip()]
        else:
            selected = available   # nothing specified -> use every watchlist found

        if not selected:
            print("⚠️  SOURCE_MODE requested watchlists but none were found/selected — "
                  "falling back to 'chartink'")
            return SOURCE_CHARTINK, []
        return mode, selected

    # ── Interactive local run ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  TICKER SOURCE")
    print("=" * 60)
    print("  1) Chartink screeners only")
    print("  2) Excel watchlist(s) only")
    print("  3) Both (Chartink + watchlist(s))")
    choice = input("Select source [1/2/3, default 1]: ").strip() or "1"

    if choice not in ("2", "3"):
        return SOURCE_CHARTINK, []

    if not available:
        print("   ⚠️  No .xlsx files found in watchlists/ — falling back to Chartink only.")
        return SOURCE_CHARTINK, []

    print(f"\n  Found {len(available)} watchlist file(s) in watchlists/:")
    for i, fname in enumerate(available, 1):
        print(f"    {i}) {fname}")
    print(f"    A) All of the above")
    raw = input("Select file number(s), comma-separated (or 'A' for all): ").strip()

    if not raw or raw.upper() == "A":
        selected = available
    else:
        selected = []
        for tok in raw.split(","):
            tok = tok.strip()
            if tok.isdigit() and 1 <= int(tok) <= len(available):
                selected.append(available[int(tok) - 1])
        if not selected:
            print("   ⚠️  No valid selection — using all watchlists.")
            selected = available

    mode = SOURCE_WATCHLIST if choice == "2" else SOURCE_BOTH
    return mode, selected

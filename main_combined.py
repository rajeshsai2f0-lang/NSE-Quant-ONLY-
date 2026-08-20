"""
NSE Weekly + Daily Combined Scanner — Quant Edition (No LLM, No Chart Images)

Runs the weekly scan (main.py's pipeline) and the daily scan (main_daily.py's
pipeline) back to back, then cross-references the two result sets on Symbol
to surface CONFLUENCE: names that are validating on BOTH timeframes at once.

Why this is worth more than reading the two CSVs side by side yourself: the
classic way to combine timeframes in swing trading is "weekly sets the
trend context, daily times the entry" — a stock that's Stage 2 / breaking
out on the WEEKLY chart *and* showing a fresh daily trigger is a materially
stronger signal than either read alone, because the daily setup is
confirmed to be happening inside a real weekly uptrend rather than a
counter-trend bounce. This file computes that confluence flag directly
instead of leaving it to eyeballing two spreadsheets.

Pipeline:
  1. Run the weekly Chartink screeners -> score weekly tickers (writes/reuses
     nse_setups_weekly_results_<date>.csv, same file main.py produces).
  2. Run the daily Chartink screeners -> score daily tickers (writes/reuses
     nse_setups_daily_results_<date>.csv, same file main_daily.py produces).
  3. Outer-join the two result sets on Symbol, compute a CombinedScore and a
     Confluence flag, and write nse_setups_combined_results_<date>.csv.
  4. Email the combined CSV, with the confluence names called out first.

Does not duplicate any scoring logic — steps 1-2 are exactly what main.py /
main_daily.py already do, just called as functions instead of separately
via `python main.py` / `python main_daily.py`.
"""
import os
import time
import smtplib
from email.message import EmailMessage
import datetime

import pandas as pd
import requests

from chartink_screener import WEEKLY_SCREENERS, DAILY_SCREENERS, PAUSE_BETWEEN, excel_output_path, fetch_chartink, build_excel
from quant_scorer import run_quant_analysis
from source_selector import choose_source, SOURCE_CHARTINK, SOURCE_WATCHLIST, SOURCE_BOTH
from watchlist_source import load_watchlists
from liquidity_merge import build_liquidity_lookup, merge_liquidity, write_merged_excel, merged_output_path
from relative_strength import fetch_relative_strength, attach_relative_strength_columns

# A name counts as "confluence" when both timeframes clear this score bar
# AND neither is flagged as untradeable on its own timeframe. Tune to taste.
CONFLUENCE_SCORE_MIN = 60

# Weekly keeps its original Stage/EMA_Alignment schema (score_ticker() in
# quant_scorer.py). Daily now uses the vision-prompt-aligned schema
# (score_ticker_daily_vision()) — different column names (id column is
# "File" not "Symbol", "Score" is a percentage string like "65%" not a
# bare number), so each side needs its own keep-list and its own "don't
# count this as confluence" set. DAILY_KEEP_COLS references "Symbol"
# because build_combined_report() renames Daily's "File" -> "Symbol"
# right after reading the CSV, before this keep-list is applied.
#
# DAILY_KEEP_COLS carries every daily criteria column (all of
# DAILY_FIELDNAMES except Timeframe/RawResponse, which are redundant here)
# so the combined output shows the full daily read, not a trimmed subset —
# same fields you'd see in the standalone daily CSV.
WEEKLY_KEEP_COLS = ["Symbol", "Stage", "BreakoutStatus", "BaseStructure", "PivotPrice", "StopLevel", "Target1", "Score"]
DAILY_KEEP_COLS = [
    "Symbol", "Linearity", "MA_Status", "Pattern", "BaseDepth", "DistributionCheck",
    "InstitutionalFootprint", "Readiness", "DaysToReady", "PivotPrice", "StopLevel",
    "StoplossPercent", "Score", "Reason",
]


def _pct_to_num(val):
    """'65%' -> 65.0, 'N/A'/blank/NaN -> NaN. Daily's Score column is a
    percentage string to match the reference daily CSV format; this
    converts it back to a number for confluence math."""
    if pd.isna(val):
        return float("nan")
    s = str(val).strip()
    if s in ("", "N/A", "nan"):
        return float("nan")
    try:
        return float(s.replace("%", ""))
    except (ValueError, TypeError):
        return float("nan")

WEEKLY_NO_SETUP_STATUSES = {"No Setup / Downtrend"}
# Daily has no single "no setup" status — Broken means the pattern failed,
# Extended means there's no low-risk entry left to chase. Neither is a
# valid confluence leg even if its Score happens to clear the bar.
DAILY_NO_SETUP_STATUSES = {"Broken", "Extended"}


def _run_screeners(session, screeners, label):
    print(f"\n{'='*60}\n  {label} SCREENERS\n{'='*60}")
    results = []
    for name, mode, value in screeners:
        if mode.lower() != "clause":
            print(f"⚠️ Skipping '{name}' — only 'clause' mode supported")
            continue
        print(f"\n📡 Running: {name}")
        df = fetch_chartink(session, value)
        results.append((name, df))
        time.sleep(PAUSE_BETWEEN)
    return results


def _scan_timeframe(session, screeners, timeframe, excel_label, run_chartink=True, watchlist_results=None):
    results = _run_screeners(session, screeners, excel_label.upper()) if run_chartink else []
    if watchlist_results:
        results = results + watchlist_results

    total_rows = sum(len(df) for _, df in results)
    if not results or total_rows == 0:
        print(f"❌ No {excel_label} results retrieved today.")
        return None, [], None

    print(f"\n📊 Building {excel_label} Excel report...")
    excel_path = excel_output_path(excel_label)
    unique_tickers = build_excel(results, excel_path)

    print(f"\n📐 Scoring {len(unique_tickers)} {excel_label.lower()} tickers (no images, no LLM)...")
    csv_path = run_quant_analysis(unique_tickers, timeframe=timeframe)
    return csv_path, unique_tickers, excel_path


def build_combined_report(weekly_csv, daily_csv):
    """Outer-join weekly + daily results on Symbol, add CombinedScore/Confluence."""
    df_w = pd.read_csv(weekly_csv) if weekly_csv and os.path.exists(weekly_csv) else pd.DataFrame()
    df_d = pd.read_csv(daily_csv) if daily_csv and os.path.exists(daily_csv) else pd.DataFrame()

    if df_w.empty and df_d.empty:
        return None

    # Daily's id column is "File" (matches the reference daily CSV format);
    # rename to "Symbol" so it merges with weekly on the same key.
    if not df_d.empty and "File" in df_d.columns:
        df_d = df_d.rename(columns={"File": "Symbol"})

    df_w = df_w[[c for c in WEEKLY_KEEP_COLS if c in df_w.columns]].add_prefix("Weekly_")
    df_d = df_d[[c for c in DAILY_KEEP_COLS if c in df_d.columns]].add_prefix("Daily_")
    df_w = df_w.rename(columns={"Weekly_Symbol": "Symbol"})
    df_d = df_d.rename(columns={"Daily_Symbol": "Symbol"})

    # Daily_Score arrives as a percentage string ("65%") — convert to a
    # number so the confluence math below (and CombinedScore) works.
    if "Daily_Score" in df_d.columns:
        df_d["Daily_Score"] = df_d["Daily_Score"].apply(_pct_to_num)

    combined = pd.merge(df_w, df_d, on="Symbol", how="outer")

    def combined_score(row):
        scores = [s for s in (row.get("Weekly_Score"), row.get("Daily_Score")) if pd.notna(s)]
        return round(sum(scores) / len(scores), 1) if scores else 0

    def is_confluence(row):
        ws, ds = row.get("Weekly_Score"), row.get("Daily_Score")
        w_status, d_status = row.get("Weekly_BreakoutStatus"), row.get("Daily_Readiness")
        if pd.isna(ws) or pd.isna(ds):
            return False
        if w_status in WEEKLY_NO_SETUP_STATUSES or d_status in DAILY_NO_SETUP_STATUSES:
            return False
        return ws >= CONFLUENCE_SCORE_MIN and ds >= CONFLUENCE_SCORE_MIN

    combined["CombinedScore"] = combined.apply(combined_score, axis=1)
    combined["Confluence"] = combined.apply(is_confluence, axis=1)
    combined = combined.sort_values(["Confluence", "CombinedScore"], ascending=[False, False])

    # Display Daily_Score back as a percentage string ("65%") to match the
    # daily CSV's own convention — it was numeric above only so the
    # confluence/CombinedScore math could run on it.
    if "Daily_Score" in combined.columns:
        combined["Daily_Score"] = combined["Daily_Score"].apply(
            lambda v: f"{v:.0f}%" if pd.notna(v) else "N/A"
        )
    return combined


def send_email_report(combined_csv_path, confluence_count, total_count, excel_paths=None, merged_excel_path=None):
    print("📧 Preparing to send email report...")

    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")
    receiver_email = os.getenv("SMTP_EMAIL")

    if not sender_email or not sender_password:
        print("⚠️ Email credentials not found in environment. Skipping email.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"📐 NSE Weekly + Daily Combined Scan: {datetime.date.today()}"
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg.set_content(
        "Your automated NSE Weekly + Daily Combined Quant pipeline has finished running.\n\n"
        f"{confluence_count} of {total_count} names are showing CONFLUENCE — a qualifying "
        f"setup (Score >= {CONFLUENCE_SCORE_MIN}, no 'No Setup/Downtrend' flag) on BOTH the "
        "weekly and daily timeframe at once. These are sorted first in the attached CSV.\n\n"
        "Why that matters: the weekly read gives you the trend context (is this stock in a "
        "real Stage 2 advance right now), and the daily read gives you entry timing (is there "
        "an actual trigger today) — confluence means the daily setup is happening inside a "
        "confirmed weekly uptrend, not a counter-trend bounce.\n\n"
        "Column guide:\n"
        "  - Weekly_* : Stage, BreakoutStatus, BaseStructure, PivotPrice, StopLevel, Target1, Score "
        "(Weinstein-stage scorer, Score as a plain number)\n"
        "  - Daily_* : Linearity, MA_Status, Pattern, BaseDepth, DistributionCheck, "
        "InstitutionalFootprint, Readiness, DaysToReady, PivotPrice, StopLevel, StoplossPercent, "
        "Score, Reason (vision-PROMPT-aligned scorer, same fields as the standalone daily CSV, "
        "matched to the same ticker/id column, 'Score' written as a percentage e.g. '65%'):\n"
        "      • Linearity: Linear / Choppy price action in the run-up into the current base\n"
        "      • MA_Status: price vs. the 9/20/50 EMA trio - Rising (Price > 9,20,50) / "
        "Price > 9 & 20, but < 50 / Coiling / Downtrending\n"
        "      • Pattern: base/pattern type at the right edge of the chart (VCP, Flag, Bull Flag, "
        "Cup with Handle, Flat Base, Long Base, Wedge, Ascending Triangle, Double Bottom, "
        "Rounding Base, No Clear Base, ...)\n"
        "      • BaseDepth: Shallow (< 20%) / Normal (20-35%) / Deep (> 35%)\n"
        "      • DistributionCheck: Clean / Heavy Distribution (down-days on above-average volume "
        "in the base)\n"
        "      • InstitutionalFootprint: Strong / Moderate / Weak / Unclear - how many of the "
        "run-up-into-base criteria are visible\n"
        "      • Readiness: Ready Now / Forming / Extended / Broken\n"
        "      • DaysToReady: only set when Readiness = Forming\n"
        "  - CombinedScore: average of Weekly_Score and Daily_Score (whichever are present)\n"
        "  - Confluence: True if the name clears the bar on both timeframes at once\n\n"
        "A blank Weekly_* or Daily_* set of columns means that ticker only showed up in one "
        "of the two screeners today, not both — still worth a look, just not (yet) a "
        "confluence name.\n\n"
        "Happy Trading!"
    )

    if combined_csv_path and os.path.exists(combined_csv_path):
        with open(combined_csv_path, 'rb') as f:
            csv_data = f.read()
            msg.add_attachment(csv_data, maintype='text', subtype='csv',
                                filename=os.path.basename(combined_csv_path))
    else:
        msg.set_content("⚠️ Pipeline ran, but no combined CSV output was found.")

    # The combined CSV has no Liquidity Rush / Market Cap columns — those
    # only exist in the per-timeframe Excel workbooks build_excel() makes.
    # Attach both, or those columns never leave the (ephemeral) runner.
    for excel_path in (excel_paths or []):
        if excel_path and os.path.exists(excel_path):
            with open(excel_path, 'rb') as f:
                excel_data = f.read()
                msg.add_attachment(
                    excel_data,
                    maintype='application',
                    subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    filename=os.path.basename(excel_path),
                )
        else:
            print(f"⚠️ Excel workbook not found: {excel_path} — its Liquidity Rush/Market Cap columns won't be in this email.")

    # The combined-scores + Liquidity Rush/%ofMCAP workbook — same data as
    # combined_csv_path + the two Chartink workbooks above, just joined
    # into one sheet so you don't have to cross-reference three files by
    # hand.
    if merged_excel_path and os.path.exists(merged_excel_path):
        with open(merged_excel_path, 'rb') as f:
            merged_data = f.read()
            msg.add_attachment(
                merged_data,
                maintype='application',
                subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                filename=os.path.basename(merged_excel_path),
            )

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


if __name__ == "__main__":
    print("🤖 STARTING NSE WEEKLY + DAILY COMBINED QUANT SCAN...")

    mode, selected_watchlists = choose_source()
    run_chartink = mode in (SOURCE_CHARTINK, SOURCE_BOTH)
    watchlist_results = []
    if mode in (SOURCE_WATCHLIST, SOURCE_BOTH):
        print(f"\n📄 Loading {len(selected_watchlists)} watchlist file(s)...")
        watchlist_results = load_watchlists(selected_watchlists)

    session = requests.Session()

    weekly_csv, weekly_tickers, weekly_excel = _scan_timeframe(
        session, WEEKLY_SCREENERS, "weekly", "Weekly",
        run_chartink=run_chartink, watchlist_results=watchlist_results,
    )
    daily_csv, daily_tickers, daily_excel = _scan_timeframe(
        session, DAILY_SCREENERS, "daily", "Daily",
        run_chartink=run_chartink, watchlist_results=watchlist_results,
    )

    if not weekly_csv and not daily_csv:
        print("❌ No results on either timeframe today. Pipeline halted.")
    else:
        print(f"\n🔀 Cross-referencing weekly + daily results...")
        combined = build_combined_report(weekly_csv, daily_csv)

        if combined is None or combined.empty:
            print("❌ Nothing to combine — both result sets were empty.")
        else:
            timestamp = datetime.date.today().strftime("%Y-%m-%d")
            combined_csv = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                f"nse_setups_combined_results_{timestamp}.csv"
            )
            combined.to_csv(combined_csv, index=False)

            confluence_count = int(combined["Confluence"].sum())
            total_count = len(combined)
            print(f"🎯 {confluence_count}/{total_count} tickers show weekly+daily confluence")

            # Combine the outputs above into a new file — Liquidity Rush
            # and %ofMCAP (10d + 20d) copied over from BOTH the weekly and
            # daily Chartink Excels and joined onto the combined results by
            # Symbol. combined_csv, weekly_excel, and daily_excel are only
            # read here, never modified.
            print("\n🔗 Merging Liquidity Rush / %ofMCAP into combined results...")
            liquidity_lookup = build_liquidity_lookup([weekly_excel, daily_excel])
            merged_df = merge_liquidity(combined, liquidity_lookup, id_col="Symbol")

            print("📈 Computing Relative Strength vs NIFTY50 / SMALLCAP100...")
            all_tickers = sorted(set(weekly_tickers) | set(daily_tickers))
            rs_metrics = fetch_relative_strength(all_tickers, market="NSE")
            merged_df = attach_relative_strength_columns(merged_df, rs_metrics, ticker_col="Symbol")

            merged_excel = merged_output_path("combined", os.path.dirname(os.path.abspath(__file__)))
            write_merged_excel(merged_df, merged_excel, "NSE Combined Quant + Liquidity Rush")
            print(f"✅ Combined file saved: {merged_excel}")

            send_email_report(combined_csv, confluence_count, total_count,
                               excel_paths=[weekly_excel, daily_excel],
                               merged_excel_path=merged_excel)

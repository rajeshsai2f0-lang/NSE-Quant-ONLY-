"""
NSE Daily Breakout Scanner — Quant Edition (No LLM, No Chart Images)

Screens NSE tickers via Chartink, then scores each one against a daily
10/30-EMA breakout framework (Weinstein stage, base/contraction detection,
trigger-candle quality, measured-move target) computed directly from the
daily OHLCV numbers — no chart rendering, no vision model, no API keys
beyond your own email credentials.

Pipeline:
  1. chartink_screener.py — pull today's screener matches from Chartink,
     build a formatted Excel workbook of the raw results.
  2. quant_scorer.py       — pull daily OHLCV per ticker via yfinance and
     compute the breakout verdict.
  3. This file             — orchestrate both steps and email the results.
"""
import os
import time
import smtplib
from email.message import EmailMessage
import datetime

import pandas as pd
import requests

from chartink_screener import DAILY_SCREENERS, PAUSE_BETWEEN, excel_output_path, fetch_chartink, build_excel
from quant_scorer import run_quant_analysis
from source_selector import choose_source, SOURCE_CHARTINK, SOURCE_WATCHLIST, SOURCE_BOTH
from watchlist_source import load_watchlists
from liquidity_merge import build_liquidity_lookup, merge_liquidity, write_merged_excel, merged_output_path
from relative_strength import fetch_relative_strength, attach_relative_strength_columns


def send_email_report(csv_file_path, excel_file_path=None, merged_excel_path=None):
    print("📧 Preparing to send email report...")

    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")
    receiver_email = os.getenv("SMTP_EMAIL")

    if not sender_email or not sender_password:
        print("⚠️ Email credentials not found in environment. Skipping email.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"📐 NSE Daily Quant Scan: {datetime.date.today()}"
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg.set_content(
        "Your automated NSE Daily Quant pipeline has finished running.\n\n"
        "Every value in the attached CSV is computed straight from daily "
        "OHLCV data — no chart images, no LLM calls. This is the quant "
        "equivalent of running the swing-trader vision PROMPT against "
        "today's daily chart: same fields (Linearity, MA_Status, Pattern, "
        "BaseDepth, DistributionCheck, InstitutionalFootprint, Readiness, "
        "DaysToReady), just read directly off the numbers instead of a "
        "rendered image.\n\n"
        "Quick guide to the key columns:\n"
        "  - Linearity: Linear / Choppy price action in the run-up into the current base\n"
        "  - MA_Status: price vs. the 9/20/50 EMA trio - Rising (Price > 9,20,50) / "
        "Price > 9 & 20, but < 50 / Coiling / Downtrending\n"
        "  - Pattern: base/pattern type at the right edge of the chart (VCP, Flag, Bull Flag, "
        "Cup with Handle, Flat Base, Long Base, Wedge, Ascending Triangle, Double Bottom, "
        "Rounding Base, No Clear Base, ...)\n"
        "  - BaseDepth: Shallow (< 20%) / Normal (20-35%) / Deep (> 35%)\n"
        "  - DistributionCheck: Clean / Heavy Distribution (down-days on above-average volume in the base)\n"
        "  - InstitutionalFootprint: Strong / Moderate / Weak / Unclear - how many of the run-up-into-base "
        "criteria (days, % advance, single-day spike, volume spike, shallow follow-through) are visible\n"
        "  - Readiness: Ready Now / Forming / Extended / Broken\n"
        "  - DaysToReady: only set when Readiness = Forming\n"
        "  - PivotPrice / StopLevel / StoplossPercent: entry trigger, stop-loss reference, and risk % "
        "(all in INR) — only populated when Readiness is Ready Now or Forming\n"
        "  - Score: composite conviction score, written as a percentage (e.g. '65%')\n"
        "  - RawResponse: all of the above verdicts pipe-delimited into one string — the quant "
        "equivalent of the raw text a vision model would return, kept for parity with any "
        "downstream tooling built against that format\n\n"
        "Note: the ticker/id column in this CSV is called 'File' (not 'Symbol') to match that "
        "same downstream format.\n\n"
        "Sort by 'Score' (highest first) for the top setups.\n\n"
        "Happy Trading!"
    )

    if csv_file_path and os.path.exists(csv_file_path):
        with open(csv_file_path, 'rb') as f:
            csv_data = f.read()
            msg.add_attachment(csv_data, maintype='text', subtype='csv', filename=os.path.basename(csv_file_path))
    else:
        msg.set_content("⚠️ Pipeline ran, but no scoring CSV output was found.")

    # The scored CSV has no Liquidity Rush / Market Cap columns — those only
    # exist in the Excel workbook build_excel() produces. Attach it too, or
    # those columns never leave the (ephemeral) runner.
    if excel_file_path and os.path.exists(excel_file_path):
        with open(excel_file_path, 'rb') as f:
            excel_data = f.read()
            msg.add_attachment(
                excel_data,
                maintype='application',
                subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                filename=os.path.basename(excel_file_path),
            )
    else:
        print("⚠️ Excel workbook not found — Liquidity Rush/Market Cap columns won't be in this email.")

    # The combined daily-scores + Liquidity Rush/%ofMCAP workbook — same
    # data as csv_file_path + excel_file_path, just joined into one sheet
    # so you don't have to cross-reference two files by hand.
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
    print("🤖 STARTING NSE DAILY QUANT SCAN...")

    mode, selected_watchlists = choose_source()
    results = []

    if mode in (SOURCE_CHARTINK, SOURCE_BOTH):
        session = requests.Session()
        for name, scmode, value in DAILY_SCREENERS:
            if scmode.lower() != "clause":
                print(f"⚠️ Skipping '{name}' — only 'clause' mode supported")
                continue
            print(f"\n📡 Running: {name}")
            df = fetch_chartink(session, value)
            results.append((name, df))
            time.sleep(PAUSE_BETWEEN)

    if mode in (SOURCE_WATCHLIST, SOURCE_BOTH):
        print(f"\n📄 Loading {len(selected_watchlists)} watchlist file(s)...")
        results.extend(load_watchlists(selected_watchlists))

    total_rows = sum(len(df) for _, df in results)

    if results and total_rows > 0:
        print(f"\n📊 Building Excel report...")
        output_excel = excel_output_path("Daily")
        unique_tickers = build_excel(results, output_excel)

        print(f"\n📐 Scoring {len(unique_tickers)} tickers (no images, no LLM)...")
        output_csv = run_quant_analysis(unique_tickers, timeframe="daily")

        # Combine the two outputs above into a new file — Liquidity Rush
        # and %ofMCAP (10d + 20d) copied over from the Chartink Excel and
        # joined onto the scored results by ticker (the "File" column).
        # output_csv and output_excel themselves are only read here, never
        # modified.
        print("\n🔗 Merging Liquidity Rush / %ofMCAP into daily results...")
        liquidity_lookup = build_liquidity_lookup([output_excel])
        daily_df = pd.read_csv(output_csv)
        merged_df = merge_liquidity(daily_df, liquidity_lookup, id_col="File")

        print("📈 Computing Relative Strength vs NIFTY50 / SMALLCAP100...")
        rs_metrics = fetch_relative_strength(unique_tickers, market="NSE")
        merged_df = attach_relative_strength_columns(merged_df, rs_metrics, ticker_col="File")

        merged_excel = merged_output_path("daily", os.path.dirname(os.path.abspath(__file__)))
        write_merged_excel(merged_df, merged_excel, "NSE Daily Quant + Liquidity Rush")
        print(f"✅ Combined file saved: {merged_excel}")

        send_email_report(output_csv, output_excel, merged_excel)
    else:
        print("❌ No Chartink results retrieved today (0 tickers across all screeners). Pipeline halted.")

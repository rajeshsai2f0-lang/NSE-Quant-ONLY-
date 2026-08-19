"""
NSE Weekly Breakout Scanner — Quant Edition (No LLM, No Chart Images)

Screens NSE tickers via Chartink, then scores each one against a weekly
10/30-EMA breakout framework (Weinstein stage, base/contraction detection,
trigger-candle quality, measured-move target) computed directly from the
weekly OHLCV numbers — no chart rendering, no vision model, no API keys
beyond your own email credentials.

Pipeline:
  1. chartink_screener.py — pull today's screener matches from Chartink,
     build a formatted Excel workbook of the raw results.
  2. quant_scorer.py       — pull weekly OHLCV per ticker via yfinance and
     compute the breakout verdict.
  3. This file             — orchestrate both steps and email the results.
"""
import os
import time
import smtplib
from email.message import EmailMessage
import datetime

import requests

from chartink_screener import WEEKLY_SCREENERS, PAUSE_BETWEEN, excel_output_path, fetch_chartink, build_excel
from quant_scorer import run_quant_analysis
from source_selector import choose_source, SOURCE_CHARTINK, SOURCE_WATCHLIST, SOURCE_BOTH
from watchlist_source import load_watchlists


def send_email_report(csv_file_path):
    print("📧 Preparing to send email report...")

    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")
    receiver_email = os.getenv("SMTP_EMAIL")

    if not sender_email or not sender_password:
        print("⚠️ Email credentials not found in environment. Skipping email.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"📐 NSE Weekly Quant Scan: {datetime.date.today()}"
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg.set_content(
        "Your automated NSE Weekly Quant pipeline has finished running.\n\n"
        "Every value in the attached CSV is computed straight from weekly "
        "OHLCV data — no chart images, no LLM calls. Same 10/30-week EMA "
        "breakout framework a vision-based scanner would use, just read "
        "directly off the numbers.\n\n"
        "Quick guide to the key columns:\n"
        "  - Stage: Weinstein Stage 1 (Basing) / 2 (Advancing) / 3 (Topping) / 4 (Declining)\n"
        "  - BreakoutStatus: Pre-Breakout (Basing) / Breaking Out This Week / Already Extended / No Setup\n"
        "  - BaseStructure / BaseTightness / Contractions: shape and quality of the base "
        "(detected via swing-high/low pullback legs)\n"
        "  - TriggerCandleQuality: close position within this week's range if price is at/through the pivot\n"
        "  - PivotPrice / StopLevel / StoplossPercent / Target1: base high, base/recent low, "
        "risk %, and measured-move target (all in INR)\n\n"
        "Sort by 'Score' (highest first) for the top setups.\n\n"
        "Happy Trading!"
    )

    if csv_file_path and os.path.exists(csv_file_path):
        with open(csv_file_path, 'rb') as f:
            csv_data = f.read()
            msg.add_attachment(csv_data, maintype='text', subtype='csv', filename=os.path.basename(csv_file_path))
    else:
        msg.set_content("⚠️ Pipeline ran, but no scoring CSV output was found.")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


if __name__ == "__main__":
    print("🤖 STARTING NSE WEEKLY QUANT SCAN...")

    mode, selected_watchlists = choose_source()
    results = []

    if mode in (SOURCE_CHARTINK, SOURCE_BOTH):
        session = requests.Session()
        for name, scmode, value in WEEKLY_SCREENERS:
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
        unique_tickers = build_excel(results, excel_output_path("Weekly"))

        print(f"\n📐 Scoring {len(unique_tickers)} tickers (no images, no LLM)...")
        output_csv = run_quant_analysis(unique_tickers, timeframe="weekly")

        send_email_report(output_csv)
    else:
        print("❌ No Chartink results retrieved today (0 tickers across all screeners). Pipeline halted.")

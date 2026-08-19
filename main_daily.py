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

import requests

from chartink_screener import DAILY_SCREENERS, PAUSE_BETWEEN, excel_output_path, fetch_chartink, build_excel
from quant_scorer import run_quant_analysis


def send_email_report(csv_file_path):
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
        "(all in INR) — only populated when Readiness is Ready Now or Forming\n\n"
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
    print("🤖 STARTING NSE DAILY QUANT SCAN...")

    session = requests.Session()
    results = []

    for name, mode, value in DAILY_SCREENERS:
        if mode.lower() != "clause":
            print(f"⚠️ Skipping '{name}' — only 'clause' mode supported")
            continue
        print(f"\n📡 Running: {name}")
        df = fetch_chartink(session, value)
        results.append((name, df))
        time.sleep(PAUSE_BETWEEN)

    total_rows = sum(len(df) for _, df in results)

    if results and total_rows > 0:
        print(f"\n📊 Building Excel report...")
        unique_tickers = build_excel(results, excel_output_path("Daily"))

        print(f"\n📐 Scoring {len(unique_tickers)} tickers (no images, no LLM)...")
        output_csv = run_quant_analysis(unique_tickers, timeframe="daily")

        send_email_report(output_csv)
    else:
        print("❌ No Chartink results retrieved today (0 tickers across all screeners). Pipeline halted.")

"""
╔══════════════════════════════════════════════════════════════════════════╗
║   QUANT SCORER — No-LLM 10/30 EMA Breakout Scanner (Weekly + Daily)      ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Computes the same verdict fields the Gemini-vision pipeline in the      ║
║  parent NSEWEEKLY project would (Stage, EMA_Alignment, BaseStructure,   ║
║  BaseTightness, Contractions, VolumeSignature, TriggerCandleQuality,     ║
║  BreakoutStatus, PeriodsBasing, PivotPrice, StopLevel, StoplossPercent,  ║
║  Target1, Score, Reason) but reads them straight off the OHLCV numbers   ║
║  instead of asking a vision model to read them off a rendered chart.    ║
║                                                                          ║
║  Parameterized by TIMEFRAME so the exact same rule set runs against     ║
║  either weekly bars or daily bars — see TIMEFRAMES below. main.py runs  ║
║  the weekly config, main_daily.py runs the daily config, and            ║
║  main_combined.py runs both and cross-references the two results.      ║
╚══════════════════════════════════════════════════════════════════════════╝

Design notes / where the thresholds come from:
  - EMA_fast/EMA_slow (10/30 by default for both timeframes) exactly mirror
    the parent project's own EMA calculation (span=10 / span=30 EWM), so
    "Stage"/"EMA_Alignment" here describe the same lines that project draws
    blue/orange on its weekly charts — just also computed on daily bars.
  - "Base window" = the stretch of bars since the most recent swing high
    that saw at least a `pullback_min_pct` drop (a normal, non-noise
    correction). Everything inside that window is what
    Contractions/BaseTightness/VolumeSignature are computed over. The
    daily config uses a smaller pullback threshold and a longer max-base
    window (in bars) than the weekly config, since daily corrections are
    smaller in % terms but bases run longer in bar-count.
  - Swing highs/lows use a simple fractal rule (a bar whose High/Low is
    the most extreme within `SWING_ORDER` bars on each side) rather than
    scipy, so this file adds no new dependency to requirements.txt.
  - Score is an explicit weighted sum (see SCORE_WEIGHTS) rather than a
    learned/subjective number -- every point is traceable to a rule, which
    is the whole point of having a deterministic scorer to compare against
    a vision model's judgment.
"""

import concurrent.futures
import csv
import datetime
import os

import pandas as pd
import yfinance as yf

YF_SUFFIX = ".NS"          # NSE tickers on yfinance need ".NS" (BSE = ".BO")
SWING_ORDER = 2            # bars on each side to qualify as a local high/low
MAX_WORKERS = 8            # parallel yfinance downloads (network-bound, not quota-bound)

# ─────────────────────────────────────────────────────────────────────────
#  TIMEFRAME CONFIGS
#  Same rule set, different bar interval and window sizes. "slope_lookback"
#  and "trend_high_lookback" are scaled up for daily so trend/stage reads
#  aren't whipsawed by single-bar noise the way a raw 4-day lookback would
#  be (4 weeks is a meaningful trend window; 4 days is not).
# ─────────────────────────────────────────────────────────────────────────
TIMEFRAMES = {
    "weekly": dict(
        label="Weekly", interval="1wk", period="2y", unit="w",
        ema_fast=10, ema_slow=30,
        pullback_min_pct=8.0, max_base=26, min_bars=40,
        slope_lookback=4, trend_high_lookback=26,
    ),
    "daily": dict(
        label="Daily", interval="1d", period="1y", unit="d",
        ema_fast=10, ema_slow=30,
        pullback_min_pct=6.0, max_base=40, min_bars=60,
        slope_lookback=10, trend_high_lookback=126,
    ),
}

FIELDNAMES = [
    "Symbol", "Timeframe", "Stage", "EMA_Alignment", "BaseStructure", "BaseTightness",
    "Contractions", "VolumeSignature", "TriggerCandleQuality", "BreakoutStatus",
    "PeriodsBasing", "PivotPrice", "StopLevel", "StoplossPercent", "Target1",
    "Score", "Reason",
]

# Composite score weights — each bucket contributes independently, capped at 100.
SCORE_WEIGHTS = {
    "stage":       {"Stage 2 (Advancing)": 30, "Stage 1 (Basing)": 10,
                     "Stage 3 (Topping)": 5, "Stage 4 (Declining)": 0},
    "ema":         {"10>30 Rising (Aligned)": 15, "10 Crossing Above 30": 8,
                     "Flat/Coiling": 3, "10<30 (Downtrend)": 0},
    "tightness":   {"Tight (< 15%)": 15, "Normal (15% - 30%)": 8, "Loose (> 30%)": 0},
    "contractions": {2: 15, 1: 7, 0: 0},   # 2 used as the "2+" bucket, see _score()
    "volume":      {"Drying Up in Base": 15, "Expanding on Breakout": 15,
                     "Average/No Signal": 5, "Climactic/Blow-off": 0},
    "trigger":     {"Strong Close (Upper Third)": 10, "Mid-Range Close": 5,
                     "Weak Close (Lower Third/Long Wick)": 0, "N/A": 5},
}


# ─────────────────────────────────────────────────────────────────────────
#  DATA FETCH
# ─────────────────────────────────────────────────────────────────────────
def _download(ticker, cfg):
    base_symbol = ticker.split(".")[0].strip().upper()
    yf_symbol = base_symbol + YF_SUFFIX
    data = yf.download(yf_symbol, period=cfg["period"], interval=cfg["interval"], progress=False)
    if data.empty or len(data) < cfg["min_bars"]:
        return base_symbol, None
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    data["EMA_FAST"] = data["Close"].ewm(span=cfg["ema_fast"], adjust=False).mean()
    data["EMA_SLOW"] = data["Close"].ewm(span=cfg["ema_slow"], adjust=False).mean()
    return base_symbol, data


# ─────────────────────────────────────────────────────────────────────────
#  SWING / BASE DETECTION
# ─────────────────────────────────────────────────────────────────────────
def _find_swing_highs_lows(high, low, order=SWING_ORDER):
    """Simple fractal swing detector — no scipy dependency.
    Returns two lists of (integer index, price)."""
    n = len(high)
    swing_highs, swing_lows = [], []
    for i in range(order, n - order):
        window_h = high.iloc[i - order:i + order + 1]
        window_l = low.iloc[i - order:i + order + 1]
        if high.iloc[i] == window_h.max() and high.iloc[i] != high.iloc[i - order:i].max():
            swing_highs.append((i, high.iloc[i]))
        if low.iloc[i] == window_l.min() and low.iloc[i] != low.iloc[i - order:i].min():
            swing_lows.append((i, low.iloc[i]))
    return swing_highs, swing_lows


def _find_base_window(data, cfg):
    """
    Walk back from the most recent bar to find where the current base
    started: the most recent swing high that was followed by at least a
    cfg['pullback_min_pct']% drop. Returns (start_idx, base_high, base_low)
    or None if no qualifying base is found within cfg['max_base'] bars.
    """
    n = len(data)
    high, low = data["High"], data["Low"]
    lookback_start = max(0, n - cfg["max_base"])
    swing_highs, _ = _find_swing_highs_lows(
        high.iloc[lookback_start:].reset_index(drop=True),
        low.iloc[lookback_start:].reset_index(drop=True),
    )
    if not swing_highs:
        return None

    # Walk swing highs from most recent backwards; take the first one where
    # the subsequent low drops at least pullback_min_pct% off that high.
    for local_i, price in reversed(swing_highs):
        start_idx = lookback_start + local_i
        window_low = low.iloc[start_idx:].min()
        drop_pct = (price - window_low) / price * 100
        if drop_pct >= cfg["pullback_min_pct"]:
            base_high = high.iloc[start_idx:].max()
            base_low = low.iloc[start_idx:].min()
            return start_idx, base_high, base_low
    return None


def _count_contractions(data, start_idx):
    """
    Count distinct pullback legs (swing-high -> swing-low -> recovery)
    inside the base window, and note whether depths are shrinking (VCP).
    """
    window = data.iloc[start_idx:].reset_index(drop=True)
    if len(window) < 2 * SWING_ORDER + 3:
        return 1, False   # too short a window to resolve multiple legs

    swing_highs, swing_lows = _find_swing_highs_lows(window["High"], window["Low"])
    if not swing_highs or not swing_lows:
        return 1, False

    legs = []
    for h_idx, h_price in swing_highs:
        later_lows = [l for l in swing_lows if l[0] > h_idx]
        if not later_lows:
            continue
        l_idx, l_price = min(later_lows, key=lambda x: x[0])
        depth_pct = (h_price - l_price) / h_price * 100
        legs.append(depth_pct)

    if not legs:
        return 1, False

    contractions = max(1, len(legs))
    tightening = len(legs) >= 2 and all(
        legs[i] <= legs[i - 1] * 1.05 for i in range(1, len(legs))
    )
    return contractions, tightening


# ─────────────────────────────────────────────────────────────────────────
#  CLASSIFICATION RULES
# ─────────────────────────────────────────────────────────────────────────
def _classify_stage(close, ema_fast, ema_slow, cfg):
    look = cfg["slope_lookback"]
    trend_look = cfg["trend_high_lookback"]
    c, ef, es = close.iloc[-1], ema_fast.iloc[-1], ema_slow.iloc[-1]
    ema_slow_slope = ema_slow.iloc[-1] - ema_slow.iloc[-(look + 1)]
    ema_fast_slope = ema_fast.iloc[-1] - ema_fast.iloc[-(look + 1)]
    near_trend_high = c >= close.iloc[-trend_look:].max() * 0.85 if len(close) >= trend_look else False

    if c < ef < es and ema_slow_slope < 0:
        return "Stage 4 (Declining)"
    if c > ef > es and ema_slow_slope > 0 and ema_fast_slope > 0:
        return "Stage 2 (Advancing)"
    if ema_fast_slope <= 0 and ema_slow_slope >= 0 and near_trend_high:
        return "Stage 3 (Topping)"
    return "Stage 1 (Basing)"


def _classify_ema_alignment(ema_fast, ema_slow, cfg):
    look = cfg["slope_lookback"]
    ef, es = ema_fast.iloc[-1], ema_slow.iloc[-1]
    ef_prev, es_prev = ema_fast.iloc[-look], ema_slow.iloc[-look]
    crossed_recently = ef_prev <= es_prev and ef > es
    if crossed_recently:
        return "10 Crossing Above 30"
    if ef > es and (ef - ema_fast.iloc[-(look + 1)]) > 0 and (es - ema_slow.iloc[-(look + 1)]) > 0:
        return "10>30 Rising (Aligned)"
    if ef < es:
        return "10<30 (Downtrend)"
    return "Flat/Coiling"


def _classify_tightness(base_high, base_low):
    pct = (base_high - base_low) / base_high * 100
    if pct < 15:
        return "Tight (< 15%)", pct
    if pct <= 30:
        return "Normal (15% - 30%)", pct
    return "Loose (> 30%)", pct


def _classify_base_structure(contractions, tightening, tightness_label):
    if contractions >= 2 and tightening:
        return "VCP"
    if contractions >= 2:
        return "Flag"
    if tightness_label == "Tight (< 15%)":
        return "Shelf/Flat Base"
    if tightness_label == "Normal (15% - 30%)":
        return "Flag"
    return "No Clear Base"


def _classify_volume(data, start_idx):
    vol = data["Volume"]
    base_avg = vol.iloc[start_idx:].mean()
    prior_avg = vol.iloc[max(0, start_idx - 12):start_idx].mean() if start_idx > 0 else base_avg
    last_vol, avg20 = vol.iloc[-1], vol.rolling(20).mean().iloc[-1]
    close_pos = _close_position(data.iloc[-1])

    if avg20 and last_vol > 3 * avg20 and close_pos < 0.4:
        return "Climactic/Blow-off"
    if avg20 and last_vol > 1.5 * avg20 and close_pos >= 0.5:
        return "Expanding on Breakout"
    if prior_avg and base_avg < 0.8 * prior_avg:
        return "Drying Up in Base"
    return "Average/No Signal"


def _close_position(bar):
    rng = bar["High"] - bar["Low"]
    if rng <= 0:
        return 0.5
    return float((bar["Close"] - bar["Low"]) / rng)


def _classify_trigger(data, base_high):
    last = data.iloc[-1]
    if last["Close"] < base_high * 0.98:
        return "N/A"
    pos = _close_position(last)
    if pos >= 0.67:
        return "Strong Close (Upper Third)"
    if pos >= 0.33:
        return "Mid-Range Close"
    return "Weak Close (Lower Third/Long Wick)"


def _classify_breakout_status(stage, close, base_high):
    if stage in ("Stage 3 (Topping)", "Stage 4 (Declining)"):
        return "No Setup / Downtrend"
    if close >= base_high * 1.15:
        return "Already Extended"
    if close >= base_high * 0.98:
        return "Breaking Out This Week"
    return "Pre-Breakout (Basing)"


def _detect_anomaly(data, start_idx):
    """Flag likely corporate-action / data-artifact candles and illiquidity."""
    notes = []
    window = data.iloc[start_idx:]
    period_ret = window["Close"].pct_change().abs()
    if (period_ret > 0.35).any():
        notes.append("possible corporate action or data anomaly in base window — verify manually")
    if window["Volume"].median() > 0 and data["Volume"].iloc[-6:].median() < window["Volume"].median() * 0.1:
        notes.append("thin/illiquid — most volume concentrated in a few recent bars")
    return notes


# ─────────────────────────────────────────────────────────────────────────
#  SCORING
# ─────────────────────────────────────────────────────────────────────────
def _score(stage, ema_align, tightness_label, contractions, vol_signature, trigger_quality):
    s = 0
    s += SCORE_WEIGHTS["stage"].get(stage, 0)
    s += SCORE_WEIGHTS["ema"].get(ema_align, 0)
    s += SCORE_WEIGHTS["tightness"].get(tightness_label, 0)
    s += SCORE_WEIGHTS["contractions"].get(min(contractions, 2), 0)
    s += SCORE_WEIGHTS["volume"].get(vol_signature, 0)
    s += SCORE_WEIGHTS["trigger"].get(trigger_quality, 0)
    return max(0, min(100, s))


# ─────────────────────────────────────────────────────────────────────────
#  PER-TICKER SCORER
# ─────────────────────────────────────────────────────────────────────────
def score_ticker(symbol, data, timeframe="weekly"):
    cfg = TIMEFRAMES[timeframe]
    close, ema_fast, ema_slow = data["Close"], data["EMA_FAST"], data["EMA_SLOW"]

    stage = _classify_stage(close, ema_fast, ema_slow, cfg)
    ema_align = _classify_ema_alignment(ema_fast, ema_slow, cfg)

    base = _find_base_window(data, cfg)
    if base is None:
        # No qualifying pullback found within the window. This is NOT the
        # same thing as "no setup" — a smooth Stage 2 uptrend with no
        # correction yet is a real (if unmeasurable-here) stock, just one
        # with no low-risk pivot to buy against right now.
        no_base_status = {
            "Stage 2 (Advancing)": "Already Extended",
            "Stage 1 (Basing)": "Pre-Breakout (Basing)",
            "Stage 3 (Topping)": "No Setup / Downtrend",
            "Stage 4 (Declining)": "No Setup / Downtrend",
        }.get(stage, "No Setup / Downtrend")
        return {
            "Symbol": symbol, "Timeframe": cfg["label"], "Stage": stage, "EMA_Alignment": ema_align,
            "BaseStructure": "No Clear Base", "BaseTightness": "N/A",
            "Contractions": "N/A", "VolumeSignature": "N/A",
            "TriggerCandleQuality": "N/A", "BreakoutStatus": no_base_status,
            "PeriodsBasing": "N/A", "PivotPrice": "N/A", "StopLevel": "N/A",
            "StoplossPercent": "N/A", "Target1": "N/A",
            "Score": _score(stage, ema_align, "N/A", 0, "N/A", "N/A"),
            "Reason": f"{stage}, {ema_align}; no qualifying base/pullback found in the last "
                      f"{cfg['max_base']}{cfg['unit']}.",
        }

    start_idx, base_high, base_low = base
    periods_basing = len(data) - start_idx
    tightness_label, tightness_pct = _classify_tightness(base_high, base_low)
    contractions, tightening = _count_contractions(data, start_idx)
    base_structure = _classify_base_structure(contractions, tightening, tightness_label)
    vol_signature = _classify_volume(data, start_idx)
    trigger_quality = _classify_trigger(data, base_high)
    breakout_status = _classify_breakout_status(stage, close.iloc[-1], base_high)
    anomaly_notes = _detect_anomaly(data, start_idx)

    pivot = round(float(base_high), 2)
    recent_low = float(data["Low"].iloc[-2:].min())
    stop_level = round(min(recent_low, float(base_low)), 2)
    stoploss_pct = round((pivot - stop_level) / pivot * 100, 1) if pivot else 0
    target1 = round(pivot + (base_high - base_low), 2)

    score = _score(stage, ema_align, tightness_label, contractions, vol_signature, trigger_quality)

    reason = (
        f"{stage}, EMA {ema_align.lower()}; {base_structure} over {periods_basing}{cfg['unit']} "
        f"({tightness_pct:.1f}% range, {contractions} contraction(s)"
        f"{', tightening' if tightening else ''}); volume {vol_signature.lower()}; "
        f"trigger candle: {trigger_quality.lower() if trigger_quality != 'N/A' else 'not yet triggered'}."
    )
    if anomaly_notes:
        reason += " ⚠ " + "; ".join(anomaly_notes)

    return {
        "Symbol": symbol, "Timeframe": cfg["label"], "Stage": stage, "EMA_Alignment": ema_align,
        "BaseStructure": base_structure, "BaseTightness": tightness_label,
        "Contractions": contractions, "VolumeSignature": vol_signature,
        "TriggerCandleQuality": trigger_quality, "BreakoutStatus": breakout_status,
        "PeriodsBasing": periods_basing, "PivotPrice": pivot, "StopLevel": stop_level,
        "StoplossPercent": f"{stoploss_pct}%", "Target1": target1,
        "Score": score, "Reason": reason,
    }


# ─────────────────────────────────────────────────────────────────────────
#  BATCH RUNNER
# ─────────────────────────────────────────────────────────────────────────
def run_quant_analysis(tickers, timeframe="weekly", csv_filename=None):
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe '{timeframe}' — expected one of {list(TIMEFRAMES)}")
    cfg = TIMEFRAMES[timeframe]

    if not tickers:
        print(f"❌ No tickers supplied to {cfg['label'].lower()} quant scorer.")
        return None

    if not csv_filename:
        timestamp = datetime.date.today().strftime("%Y-%m-%d")
        csv_filename = f"nse_setups_{timeframe}_results_{timestamp}.csv"

    tickers = sorted(set(t.strip().upper() for t in tickers if t and str(t).strip()))

    processed = set()
    file_exists = os.path.exists(csv_filename)
    if file_exists:
        with open(csv_filename, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                processed.add(row["Symbol"])

    remaining = [t for t in tickers if t.split(".")[0].strip().upper() not in processed]
    print(f"[{cfg['label']}] Total tickers: {len(tickers)} | Left to score: {len(remaining)}")
    if not remaining:
        print(f"[{cfg['label']}] All tickers already scored!")
        return csv_filename

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_download, t, cfg): t for t in remaining}
        for n, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            ticker = futures[fut]
            try:
                symbol, data = fut.result()
                if data is None:
                    print(f"[{cfg['label']} {n}/{len(remaining)}] {ticker}: ⚠️  insufficient data, skipped")
                    continue
                row = score_ticker(symbol, data, timeframe)
                rows.append(row)
                print(f"[{cfg['label']} {n}/{len(remaining)}] {row['Symbol']}: {row['Stage']} | "
                      f"{row['BreakoutStatus']} | Base: {row['BaseStructure']} | Score: {row['Score']}")
            except Exception as e:
                print(f"[{cfg['label']} {n}/{len(remaining)}] {ticker}: ❌ {e}")

    with open(csv_filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ [{cfg['label']}] Quant scoring complete! Scored {len(rows)}/{len(remaining)} tickers -> {csv_filename}")
    return csv_filename

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

  - DAILY IS SPECIAL-CASED: run_quant_analysis(timeframe="daily") no longer
    calls the generic score_ticker() above — it calls
    score_ticker_daily_vision(), a separate scorer whose output schema
    (DAILY_FIELDNAMES) and step-by-step logic mirror the Gemini-vision
    swing-trader PROMPT (Linearity / MA_Status / Pattern / BaseDepth /
    DistributionCheck / InstitutionalFootprint / Readiness / DaysToReady)
    field-for-field, just computed from OHLCV instead of a chart image.
    Weekly is untouched and still uses score_ticker()/FIELDNAMES.
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

        # ─── vision-prompt-aligned daily scoring config ───────────────────
        # The daily timeframe is scored by score_ticker_daily_vision(),
        # which mirrors the Gemini-vision swing-trader PROMPT step-by-step
        # (Linearity / MA_Status / Pattern / BaseDepth / DistributionCheck /
        # InstitutionalFootprint / Readiness / DaysToReady) instead of the
        # generic Stage/EMA_Alignment scorer used for weekly. These knobs
        # are the numeric thresholds that stand in for that prompt's
        # judgment calls — tune to taste, they're not from Chartink.
        ema9=9, ema20=20, ema50=50,                    # Step 4 MA_STATUS trio
        base_depth_shallow=20.0, base_depth_deep=35.0,  # Step 6 BASE DEPTH
        distribution_vol_mult=1.2, distribution_min_days=3,  # Step 7
        advance_lookback_max=60,       # how far back to search for the run-up leg
        footprint_min_days=4, footprint_max_days=20,     # Step 8 institutional footprint
        footprint_min_advance_pct=20.0,
        footprint_single_day_pct=8.0,
        footprint_vol_spike_mult=2.5,
        footprint_base_depth_max=20.0,
        linearity_min_efficiency=0.40,   # Step 3 LINEARITY CHECK
        readiness_near_pivot_pct=2.0,    # Step 9 READINESS
        readiness_dryup_ratio=0.85,
        min_basing_days_target=10, max_basing_days_target=25,  # Step 10 DAYS TO READY
    ),
}

FIELDNAMES = [
    "Symbol", "Timeframe", "Stage", "EMA_Alignment", "BaseStructure", "BaseTightness",
    "Contractions", "VolumeSignature", "TriggerCandleQuality", "BreakoutStatus",
    "PeriodsBasing", "PivotPrice", "StopLevel", "StoplossPercent", "Target1",
    "Score", "Reason",
]

# Daily output schema — matches the Gemini-vision PROMPT's own 15-field
# pipe-delimited response format (see the parent project's vision_engine.py)
# minus Symbol/Timeframe bookkeeping duplicates, so a daily CSV row here
# reads like a row from that pipeline's output CSV.
DAILY_FIELDNAMES = [
    "Symbol", "Timeframe", "Linearity", "MA_Status", "Pattern", "BaseDepth",
    "DistributionCheck", "InstitutionalFootprint", "Readiness", "DaysToReady",
    "PivotPrice", "StopLevel", "StoplossPercent", "Score", "Reason",
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
    # Only the daily config carries ema9/ema20/ema50 — feeds
    # score_ticker_daily_vision()'s MA_Status step (Step 4 of the PROMPT).
    if "ema9" in cfg:
        data["EMA9"] = data["Close"].ewm(span=cfg["ema9"], adjust=False).mean()
        data["EMA20"] = data["Close"].ewm(span=cfg["ema20"], adjust=False).mean()
        data["EMA50"] = data["Close"].ewm(span=cfg["ema50"], adjust=False).mean()
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


# ═══════════════════════════════════════════════════════════════════════════
#  DAILY — VISION-PROMPT-ALIGNED SCORER
#  Mirrors the Gemini-vision swing-trader PROMPT step-by-step (Steps 3-15),
#  reading the exact same verdicts straight off daily OHLCV instead of a
#  rendered chart image. Only used for timeframe == "daily"; weekly keeps
#  the original Stage/EMA_Alignment scorer above untouched.
# ═══════════════════════════════════════════════════════════════════════════
def _classify_linearity(data, adv_start, start_idx, cfg):
    """PROMPT Step 3. Efficiency ratio = net move / total path length over
    the advance leg into the base. High ratio = few wasted back-and-forth
    bars = Linear; low ratio = Choppy."""
    closes = data["Close"].iloc[adv_start:start_idx + 1]
    if len(closes) < 3:
        return "Unclear"
    net_move = abs(closes.iloc[-1] - closes.iloc[0])
    path_len = closes.diff().abs().sum()
    if not path_len:
        return "Unclear"
    efficiency = net_move / path_len
    return "Linear" if efficiency >= cfg.get("linearity_min_efficiency", 0.40) else "Choppy"


def _classify_ma_status_vision(close, ema9, ema20, ema50, cfg):
    """PROMPT Step 4 — one of the same 4 labels the vision prompt uses."""
    c = close.iloc[-1]
    e9, e20, e50 = ema9.iloc[-1], ema20.iloc[-1], ema50.iloc[-1]
    look = min(cfg.get("slope_lookback", 10), len(ema50) - 1)
    e50_slope = e50 - ema50.iloc[-(look + 1)] if look > 0 else 0.0

    if c > e9 and c > e20 and c > e50 and e50_slope > 0:
        return "Rising (Price > 9, 20, 50)"
    if c > e9 and c > e20 and c < e50:
        return "Price > 9 & 20, but < 50"

    spread_pct = (max(e9, e20, e50) - min(e9, e20, e50)) / c * 100 if c else 0
    if spread_pct <= 3.0:
        return "Coiling"
    return "Downtrending"


def _classify_base_depth_vision(tightness_pct, cfg):
    """PROMPT Step 6."""
    if tightness_pct < cfg.get("base_depth_shallow", 20.0):
        return "Shallow (< 20%)"
    if tightness_pct <= cfg.get("base_depth_deep", 35.0):
        return "Normal (20% - 35%)"
    return "Deep (> 35%)"


def _classify_distribution(data, start_idx, cfg):
    """PROMPT Step 7 — count down-days on above-average volume inside the
    base window; several of those is textbook heavy distribution."""
    window = data.iloc[start_idx:]
    if len(window) < 2:
        return "Clean"
    avg_vol20 = data["Volume"].rolling(20).mean().reindex(window.index)
    down_days = 0
    for i in range(1, len(window)):
        prev_close = window["Close"].iloc[i - 1]
        cur = window.iloc[i]
        avg = avg_vol20.iloc[i]
        if pd.notna(avg) and cur["Close"] < prev_close and cur["Volume"] > avg * cfg.get("distribution_vol_mult", 1.2):
            down_days += 1
    return "Heavy Distribution" if down_days >= cfg.get("distribution_min_days", 3) else "Clean"


def _classify_pattern_vision(data, start_idx, contractions, tightening, tightness_pct, periods_basing):
    """PROMPT Step 5 — maps the base's measured shape onto the fixed
    pattern list the prompt requires. Heuristic, not exhaustive; anything
    that doesn't match a clean rule falls through to 'No Clear Base'."""
    window = data.iloc[start_idx:]
    n = len(window)
    highs, lows = window["High"].reset_index(drop=True), window["Low"].reset_index(drop=True)
    swing_highs, swing_lows = _find_swing_highs_lows(highs, lows)

    if len(swing_lows) >= 2:
        (i1, p1), (i2, p2) = swing_lows[-2], swing_lows[-1]
        if abs(p1 - p2) / max(p1, p2) <= 0.03 and (i2 - i1) >= 3:
            return "Double Bottom"

    if contractions >= 2 and tightening:
        return "VCP"

    if periods_basing <= 15 and tightness_pct <= 25:
        return "Bull Flag" if contractions <= 1 else "Flag"

    if tightness_pct < 15 and periods_basing <= 25:
        return "Flat Base"

    if n >= 15:
        third = n // 3
        first_low, mid_low, last_low = lows.iloc[:third].min(), lows.iloc[third:2 * third].min(), lows.iloc[2 * third:].min()
        if mid_low < first_low and mid_low < last_low:
            if periods_basing >= 30 and lows.iloc[-5:].min() > mid_low * 1.05:
                return "Cup with Handle"
            return "Rounding Base"

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        high_slope = swing_highs[-1][1] - swing_highs[0][1]
        low_slope = swing_lows[-1][1] - swing_lows[0][1]
        avg_high = window["High"].mean()
        if avg_high and abs(high_slope) / avg_high < 0.02 and low_slope > 0:
            return "Ascending Triangle"
        if high_slope < 0 and low_slope > 0:
            return "Wedge"

    if periods_basing >= 25:
        return "Long Base"

    return "No Clear Base"


def _find_advance_leg(data, start_idx, cfg):
    """Locate the run-up leg that led INTO the base beginning at start_idx:
    the most recent swing low before the base's opening swing high. This
    is the move PROMPT Step 8 (institutional footprint) evaluates. Returns
    (adv_start_idx, low_price) or None if it can't be resolved."""
    lookback_start = max(0, start_idx - cfg.get("advance_lookback_max", 60))
    if start_idx - lookback_start < 3:
        return None
    window_high = data["High"].iloc[lookback_start:start_idx + 1].reset_index(drop=True)
    window_low = data["Low"].iloc[lookback_start:start_idx + 1].reset_index(drop=True)
    _, swing_lows = _find_swing_highs_lows(window_high, window_low)
    if not swing_lows:
        return None
    local_low_idx, low_price = swing_lows[-1]
    adv_start = lookback_start + local_low_idx
    if adv_start >= start_idx:
        return None
    return adv_start, low_price


def _classify_institutional_footprint(data, adv_start, start_idx, low_price, base_high, tightness_pct, cfg):
    """PROMPT Step 8 — checks the run-up into the base against the same 5
    criteria the prompt lists (days, % advance, single-day spike, volume
    spike, shallow follow-through base) and buckets Strong/Moderate/Weak."""
    days = start_idx - adv_start
    advance_pct = (base_high - low_price) / low_price * 100 if low_price else 0
    window = data.iloc[adv_start:start_idx + 1]
    daily_ret_pct = window["Close"].pct_change().abs() * 100
    max_single_day = float(daily_ret_pct.max()) if len(daily_ret_pct) else 0.0
    vol = data["Volume"]
    avg_vol_before = vol.iloc[max(0, adv_start - 20):adv_start].mean() if adv_start > 0 else window["Volume"].mean()
    vol_spike_ratio = float(window["Volume"].max() / avg_vol_before) if avg_vol_before else 0.0

    criteria = [
        cfg.get("footprint_min_days", 4) <= days <= cfg.get("footprint_max_days", 20),
        advance_pct >= cfg.get("footprint_min_advance_pct", 20.0),
        max_single_day >= cfg.get("footprint_single_day_pct", 8.0),
        vol_spike_ratio >= cfg.get("footprint_vol_spike_mult", 2.5),
        tightness_pct <= cfg.get("footprint_base_depth_max", 20.0),
    ]
    hits = sum(criteria)
    label = "Strong" if hits >= 4 else "Moderate" if hits >= 2 else "Weak"
    return label, days, advance_pct, max_single_day, vol_spike_ratio


def _classify_readiness_vision(data, base_high, base_low, cfg):
    """PROMPT Step 9 — Ready Now / Forming / Extended / Broken."""
    last_close = float(data["Close"].iloc[-1])

    if last_close < base_low:
        return "Broken"
    if last_close >= base_high * 1.15:
        return "Extended"

    near_pct = cfg.get("readiness_near_pivot_pct", 2.0)
    recent_vol_avg = data["Volume"].iloc[-5:].mean()
    prior_vol_avg = data["Volume"].iloc[-20:-5].mean() if len(data) >= 20 else recent_vol_avg
    dried_up = prior_vol_avg > 0 and recent_vol_avg <= prior_vol_avg * cfg.get("readiness_dryup_ratio", 0.85)

    if last_close >= base_high * (1 - near_pct / 100):
        return "Ready Now" if dried_up else "Forming"
    return "Forming"


def _estimate_days_to_ready(readiness, periods_basing, tightness_pct, cfg):
    """PROMPT Step 10 — only meaningful when Readiness == 'Forming'."""
    if readiness != "Forming":
        return "N/A"
    min_days = cfg.get("min_basing_days_target", 10)
    max_days = cfg.get("max_basing_days_target", 25)
    remaining = max(min_days - periods_basing, 2 if tightness_pct > 25 else 1)
    remaining = min(remaining, max_days)
    return f"{remaining}-{remaining + 2} days"


# Composite score weights for the daily vision scorer — mirrors PROMPT
# Step 14's conviction scale (90%+ textbook, <40% skip) as an explicit,
# traceable weighted sum instead of a subjective vision-model number.
VISION_SCORE_WEIGHTS = {
    "linearity": {"Linear": 20, "Choppy": 5, "Unclear": 10},
    "ma_status": {"Rising (Price > 9, 20, 50)": 25, "Price > 9 & 20, but < 50": 12,
                  "Coiling": 8, "Downtrending": 0},
    "footprint": {"Strong": 25, "Moderate": 14, "Weak": 4, "Unclear": 8},
    "readiness": {"Ready Now": 20, "Forming": 10, "Extended": 5, "Broken": 0},
    "base_depth": {"Shallow (< 20%)": 10, "Normal (20% - 35%)": 5, "Deep (> 35%)": 0, "N/A": 5},
}


def _score_vision(linearity, ma_status, footprint, readiness, base_depth, distribution):
    s = 0
    s += VISION_SCORE_WEIGHTS["linearity"].get(linearity, 0)
    s += VISION_SCORE_WEIGHTS["ma_status"].get(ma_status, 0)
    s += VISION_SCORE_WEIGHTS["footprint"].get(footprint, 0)
    s += VISION_SCORE_WEIGHTS["readiness"].get(readiness, 0)
    s += VISION_SCORE_WEIGHTS["base_depth"].get(base_depth, 0)
    if distribution == "Heavy Distribution":
        s -= 15
    return max(0, min(100, s))


def score_ticker_daily_vision(symbol, data, cfg):
    """Daily-only scorer producing the DAILY_FIELDNAMES schema — the
    quant equivalent of running the Gemini-vision PROMPT against today's
    daily chart, computed straight from OHLCV instead of an image."""
    close = data["Close"]
    base = _find_base_window(data, cfg)

    if base is None:
        ma_status = _classify_ma_status_vision(close, data["EMA9"], data["EMA20"], data["EMA50"], cfg)
        readiness = "Extended" if ma_status == "Rising (Price > 9, 20, 50)" else "Forming"
        days_to_ready = "5-10 days" if readiness == "Forming" else "N/A"
        return {
            "Symbol": symbol, "Timeframe": cfg["label"], "Linearity": "Unclear",
            "MA_Status": ma_status, "Pattern": "No Clear Base", "BaseDepth": "N/A",
            "DistributionCheck": "N/A", "InstitutionalFootprint": "Unclear",
            "Readiness": readiness, "DaysToReady": days_to_ready,
            "PivotPrice": "N/A", "StopLevel": "N/A", "StoplossPercent": "N/A",
            "Score": _score_vision("Unclear", ma_status, "Unclear", readiness, "N/A", "N/A"),
            "Reason": f"MA: {ma_status.lower()}; no qualifying base/pullback found in the last "
                      f"{cfg['max_base']}{cfg['unit']}.",
        }

    start_idx, base_high, base_low = base
    periods_basing = len(data) - start_idx
    _, tightness_pct = _classify_tightness(base_high, base_low)
    contractions, tightening = _count_contractions(data, start_idx)

    base_depth = _classify_base_depth_vision(tightness_pct, cfg)
    distribution = _classify_distribution(data, start_idx, cfg)
    pattern = _classify_pattern_vision(data, start_idx, contractions, tightening, tightness_pct, periods_basing)
    ma_status = _classify_ma_status_vision(close, data["EMA9"], data["EMA20"], data["EMA50"], cfg)
    readiness = _classify_readiness_vision(data, base_high, base_low, cfg)

    adv = _find_advance_leg(data, start_idx, cfg)
    if adv is None:
        linearity, footprint = "Unclear", "Unclear"
        footprint_detail = "prior advance not resolvable in the available window"
    else:
        adv_start, low_price = adv
        linearity = _classify_linearity(data, adv_start, start_idx, cfg)
        footprint, adv_days, adv_pct, max_single_day, vol_spike = _classify_institutional_footprint(
            data, adv_start, start_idx, low_price, base_high, tightness_pct, cfg)
        footprint_detail = (f"advance {adv_days}{cfg['unit']}/{adv_pct:.0f}%, "
                             f"max single-bar move {max_single_day:.1f}%, vol spike {vol_spike:.1f}x")

    days_to_ready = _estimate_days_to_ready(readiness, periods_basing, tightness_pct, cfg)

    pivot = round(float(base_high), 2)
    recent_low = float(data["Low"].iloc[-2:].min())
    stop_level = round(min(recent_low, float(base_low)), 2)
    stoploss_pct = round((pivot - stop_level) / pivot * 100, 1) if pivot else 0

    if readiness in ("Ready Now", "Forming"):
        pivot_out, stop_out, stoploss_out = pivot, stop_level, f"{stoploss_pct}%"
    else:
        # PROMPT Steps 11-13: pivot/stop only apply "if Ready Now (or close
        # to it)" — Extended/Broken names have no valid entry trigger.
        pivot_out, stop_out, stoploss_out = "N/A", "N/A", "N/A"

    score = _score_vision(linearity, ma_status, footprint, readiness, base_depth, distribution)

    reason = (
        f"{linearity} run into base; MA: {ma_status.lower()}; {pattern} over {periods_basing}{cfg['unit']} "
        f"({base_depth.lower()}, {distribution.lower()}); institutional footprint {footprint.lower()} "
        f"({footprint_detail}); readiness: {readiness.lower()}."
    )

    return {
        "Symbol": symbol, "Timeframe": cfg["label"], "Linearity": linearity, "MA_Status": ma_status,
        "Pattern": pattern, "BaseDepth": base_depth, "DistributionCheck": distribution,
        "InstitutionalFootprint": footprint, "Readiness": readiness, "DaysToReady": days_to_ready,
        "PivotPrice": pivot_out, "StopLevel": stop_out, "StoplossPercent": stoploss_out,
        "Score": score, "Reason": reason,
    }


# ─────────────────────────────────────────────────────────────────────────
#  PER-TICKER SCORER (weekly)
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
    fieldnames = DAILY_FIELDNAMES if timeframe == "daily" else FIELDNAMES

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
                if timeframe == "daily":
                    row = score_ticker_daily_vision(symbol, data, cfg)
                    rows.append(row)
                    print(f"[{cfg['label']} {n}/{len(remaining)}] {row['Symbol']}: {row['MA_Status']} | "
                          f"{row['Readiness']} | Pattern: {row['Pattern']} | Score: {row['Score']}")
                else:
                    row = score_ticker(symbol, data, timeframe)
                    rows.append(row)
                    print(f"[{cfg['label']} {n}/{len(remaining)}] {row['Symbol']}: {row['Stage']} | "
                          f"{row['BreakoutStatus']} | Base: {row['BaseStructure']} | Score: {row['Score']}")
            except Exception as e:
                print(f"[{cfg['label']} {n}/{len(remaining)}] {ticker}: ❌ {e}")

    with open(csv_filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ [{cfg['label']}] Quant scoring complete! Scored {len(rows)}/{len(remaining)} tickers -> {csv_filename}")
    return csv_filename

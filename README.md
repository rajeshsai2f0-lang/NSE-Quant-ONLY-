# NSE 10/30 EMA Breakout Scanner — Quant Edition (No LLM, No Chart Images)

Daily automation that finds NSE stocks setting up against a 10/30 EMA
breakout framework — on the **weekly** timeframe, the **daily** timeframe,
or **both at once** — and scores them entirely from OHLCV numbers. No
chart images are generated, no LLM/vision model is called, and the only
external credential you need is your own email account.

## Three pipelines, three workflows

| Entry point | Workflow | What it does |
|---|---|---|
| `main.py` | `Pipeline.yaml` | Weekly-only scan and score, emailed alone |
| `main_daily.py` | `PipelineDaily.yaml` | Daily-only scan and score, emailed alone |
| `main_combined.py` | `PipelineCombined.yaml` | Runs **both**, then cross-references them into one combined report |

All three share the same two building blocks:

1. **`chartink_screener.py`** — runs the Chartink clause screeners for
   whichever timeframe(s) are needed:
   - `WEEKLY_SCREENERS`: **10-30 EMA Breakout - Ready Now** / **Basing -
     Forming**, on weekly bars — price above a rising 10-week EMA which is
     itself above a rising 30-week EMA, tagging a fresh ~10-week high, on
     volume expansion, not yet extended too far to chase (or, for
     "Basing", approaching but not yet through that ceiling).
   - `DAILY_SCREENERS`: the same breakout/basing logic, on daily bars
     instead (adapted from the weekly clauses by symmetry — see the
     caveat below).
   - Also builds a formatted Excel workbook of the raw screener matches,
     one file per timeframe (`Chartink_Screener_Weekly_*.xlsx` /
     `Chartink_Screener_Daily_*.xlsx`) so a same-day weekly + daily +
     combined run never collides on filenames.
2. **`quant_scorer.py`** — pulls OHLCV per ticker via `yfinance` at
   whichever `interval` the timeframe needs, and computes, directly from
   the numbers:
   - **Stage** (Weinstein 1-4) and **EMA_Alignment**, from the EMA10/EMA30
     series and their slopes.
   - **Base window / BaseStructure / BaseTightness / Contractions** — the
     current base is found via swing-high/low pullback-leg detection (a
     simple fractal rule, no `scipy` dependency), checked for whether legs
     are progressively shrinking (VCP).
   - **VolumeSignature** — dry-up in the base vs. expansion on the trigger
     bar vs. climactic/blow-off, from rolling volume averages.
   - **TriggerCandleQuality** — where the latest bar's close sits within
     its own high-low range, once price is at/through the pivot.
   - **BreakoutStatus**, **PivotPrice / StopLevel / StoplossPercent /
     Target1** — pivot = base high, stop = base/recent low, target = pivot
     + base height (measured move). All exact numbers read off the data.
   - **Score** — an explicit, traceable weighted sum (see `SCORE_WEIGHTS`
     at the top of the file).

   The weekly and daily configs (`TIMEFRAMES` dict at the top of the file)
   use the same 10/30 EMA rule set, just scaled for the timeframe: daily
   uses a smaller pullback threshold (6% vs 8%), a longer max base window
   in bar-count (40 bars vs 26), and longer slope/trend-high lookbacks (to
   avoid single-bar noise whipsawing the Stage read the way a raw 4-day
   lookback would).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your SMTP credentials
python main.py             # weekly only
python main_daily.py       # daily only
python main_combined.py    # both, cross-referenced
```

For unattended runs, each `.github/workflows/*.yaml` file runs its
corresponding pipeline via GitHub Actions (manual trigger), reading
`SMTP_EMAIL` / `SMTP_PASSWORD` from repo Secrets instead of `.env`. No
other secrets are needed for any of the three.

## The combined report: what "confluence" means

`main_combined.py` runs the weekly scan and the daily scan, then outer-joins
the two result sets on `Symbol` into one CSV with `Weekly_*` and `Daily_*`
columns side by side, plus:

- **CombinedScore** — the average of `Weekly_Score` and `Daily_Score`
  (whichever are present; a ticker that only showed up on one timeframe's
  screener just uses that one score).
- **Confluence** — `True` only when a ticker clears `CONFLUENCE_SCORE_MIN`
  (default 60) on **both** timeframes at once, and neither is flagged
  `No Setup / Downtrend`.

The reasoning: weekly gives you trend context (is this a real Stage 2
advance), daily gives you entry timing (is there an actual trigger today).
A name with confluence is a daily setup confirmed to be happening inside a
real weekly uptrend — not a counter-trend bounce. Confluence rows are
sorted to the top of the CSV and called out in the email.

## What this trades off vs. a vision-model scorer

This scanner is fast, free, and perfectly reproducible — same chart, same
score, every time, and it can be backtested against historical data since
there's no API cost per call. What it won't do as well as a human (or a
vision model standing in for one):

- **Fuzzy pattern tolerance.** Real-world VCPs rarely look textbook-clean.
  The fixed swing-detection rule can miss or miscount contraction legs on
  messy-but-real bases that a trained eye would still call a valid setup.
- **Corporate-action / data-artifact judgment.** Only a simple >35%
  single-bar move flag is used to catch likely splits/bonuses/data
  glitches — no contextual "does this look like a real move" read.
- **General "something's off here" pattern recognition** — heavy
  distribution, climactic extension, anything that doesn't reduce cleanly
  to a threshold.

Treat high scorers (and especially confluence names) as a strong
first-pass filter, but spot-check them against the actual chart before
acting — especially anything scoring in the middle of the range.

## Notes / caveats

- The Chartink clause syntax (in particular the `1 week ago` / `1 day ago`
  slope checks) is modeled on working patterns from other Chartink clauses
  but hasn't been independently verified against the live Chartink engine
  — paste-test in Chartink's scanner console before changing thresholds.
  The `DAILY_SCREENERS` clauses were adapted from the weekly ones by
  direct symmetry (dropping the `weekly`/`1 week ago` prefixes so the same
  indicators read off daily bars); verify them the same way before relying
  on them.
- `yfinance` tickers are built as `{SYMBOL}.NS` (NSE). Swap the `YF_SUFFIX`
  constant in `quant_scorer.py` to `.BO` if you want BSE listings instead.
- Weekly scoring needs at least 40 bars (~40 weeks) of history per ticker;
  daily scoring needs at least 60 bars (~3 months). Tickers with less are
  skipped (printed as a warning, not an error).
- `main_combined.py` doesn't duplicate any scoring logic — its weekly and
  daily steps call the exact same functions `main.py` and `main_daily.py`
  use, and read/write the exact same CSV filenames, so running all three
  workflows on the same day reuses each other's cached results instead of
  re-scoring from scratch.

# NSE Quant --- Easy User Documentation

## 1. What is this project?

**NSE Quant** is an automated technical-analysis and stock-screening
tool for the Indian stock market.

It is designed to find NSE stocks that may be developing **10/30 EMA
breakout-style setups** and then score those setups using only market
numbers such as:

-   Price
-   Volume
-   Moving averages
-   Highs and lows
-   Trend direction
-   Base structure
-   Breakout levels

### The important difference

This version is **Quant-only**.

It does **not**:

-   Generate chart images
-   Ask an AI/LLM to judge charts
-   Require a Gemini API key
-   Use computer vision

Instead, the calculations are based directly on OHLCV market data.

> **OHLCV** means Open, High, Low, Close, and Volume.

The project can analyze stocks on the:

-   **Weekly timeframe**
-   **Daily timeframe**
-   **Both together**

------------------------------------------------------------------------

# 2. The easiest way to understand it

Think of the project as a filter.

``` text
NSE Stocks
   ↓
Chartink screeners / Your watchlist
   ↓
Download price + volume data
   ↓
Calculate technical measurements
   ↓
Check trend + EMA alignment
   ↓
Find the current base
   ↓
Check contractions + volume
   ↓
Find breakout / stop / target levels
   ↓
Calculate a score
   ↓
Create CSV / Excel reports
```

The purpose is to reduce a large number of stocks into a smaller list
that deserves closer attention.

------------------------------------------------------------------------

# 3. Three ways to run the project

The repository has three main programs.

  -----------------------------------------------------------------------
  Program                 Timeframe               Purpose
  ----------------------- ----------------------- -----------------------
  `main.py`               Weekly                  Weekly NSE scan and
                                                  score

  `main_daily.py`         Daily                   Daily NSE scan and
                                                  score

  `main_combined.py`      Weekly + Daily          Runs both and checks
                                                  for confluence
  -----------------------------------------------------------------------

You can use whichever one fits your workflow.

------------------------------------------------------------------------

# 4. Weekly scan

Run:

``` bash
python main.py
```

The weekly scan looks for stocks using weekly market data.

Weekly analysis is useful for answering questions such as:

> "Is this stock in a healthy larger trend?"

It is generally slower and less noisy than looking only at daily
candles.

------------------------------------------------------------------------

# 5. Daily scan

Run:

``` bash
python main_daily.py
```

The daily scan looks at shorter-term price behavior.

It is useful for questions such as:

> "Is there a potential entry/setup developing now?"

The daily scorer is designed to resemble the fields used by the
project's earlier vision-analysis approach, but everything is calculated
from numerical market data instead of an image/LLM.

------------------------------------------------------------------------

# 6. Combined scan --- recommended for beginners

Run:

``` bash
python main_combined.py
```

This runs:

``` text
Weekly analysis
      +
Daily analysis
      ↓
Combined report
```

The idea is simple:

### Weekly = bigger picture

It tells you whether the stock has a reasonable larger trend.

### Daily = timing

It tells you whether the shorter-term setup is developing.

### Confluence = both agree

The combined report marks **Confluence = True** when the stock meets the
configured score and validity requirements on both timeframes.

The default minimum confluence score is **60** on each timeframe.

This is not a buy signal. It is simply a stronger filter for further
research.

------------------------------------------------------------------------

# 7. Where do the stock symbols come from?

You have two choices.

## Option A --- Chartink

The project can run the configured Chartink screeners.

The screeners look for conditions related to:

-   10 EMA
-   30 EMA
-   Rising trend
-   Recent highs
-   Volume expansion
-   Breakout readiness
-   Basing/forming behavior

The exact rules are stored in:

``` text
chartink_screener.py
```

------------------------------------------------------------------------

## Option B --- Your own watchlist

You can also supply your own Excel watchlists.

Put them in:

``` text
watchlists/
```

The repository supports up to **10 `.xlsx` watchlist files** according
to the watchlist documentation.

A basic watchlist can look like:

  Ticker       Market Cap
  ---------- ------------
  TCS             1450000
  INFY             620000
  RELIANCE        1900000

`Market Cap` is optional.

If you provide it, the project can calculate the `%ofMCAP10days` and
`%ofMCAP20days` fields in the Excel output.

If it is missing, those fields remain blank for those watchlist stocks.

------------------------------------------------------------------------

# 8. Watchlist column names

The program can recognize common ticker column names such as:

``` text
Ticker
Symbol
NSE Code
NSECODE
Stock
Scrip
```

If none of these are found, it uses the first column.

Other columns are generally ignored.

A ticker can be written as:

``` text
TCS
```

or:

``` text
TCS.NS
```

Both are supported.

------------------------------------------------------------------------

# 9. Choosing the source

The project supports three source modes:

``` text
chartink
watchlist
both
```

### Chartink

Only use stocks returned by the configured Chartink screeners.

### Watchlist

Only use stocks from your watchlist files.

### Both

Use both sources.

If the same stock appears in multiple sources, the project combines the
data rather than treating it as completely separate stocks.

------------------------------------------------------------------------

# 10. Running locally

Install Python 3.10 or a compatible Python environment.

Then install the project dependencies:

``` bash
pip install -r requirements.txt
```

The repository also provides:

``` text
.env.example
```

Copy it to:

``` text
.env
```

and fill in the email settings if you want email reports.

No Gemini key is required.

------------------------------------------------------------------------

# 11. Email setup

The project can email the generated reports.

The relevant environment variables are:

``` text
SMTP_EMAIL
SMTP_PASSWORD
```

For GitHub Actions, store them as repository secrets.

For local use, place them in your environment / `.env` according to the
project's configuration.

The project uses Gmail SMTP.

**Security:** Never commit your real email password or app password into
GitHub.

------------------------------------------------------------------------

# 12. GitHub Actions

The repository includes three workflows:

``` text
.github/workflows/Pipeline.yaml
.github/workflows/PipelineDaily.yaml
.github/workflows/PipelineCombined.yaml
```

They correspond to:

``` text
Pipeline.yaml
        ↓
Weekly scan

PipelineDaily.yaml
        ↓
Daily scan

PipelineCombined.yaml
        ↓
Weekly + Daily combined scan
```

The workflows are configured with:

``` text
workflow_dispatch
```

That means they are **manually started from GitHub Actions**.

They are not currently configured as automatic cron schedules.

------------------------------------------------------------------------

# 13. How to run from GitHub

1.  Open the GitHub repository.
2.  Click **Actions**.
3.  Select the workflow you want.
4.  Click **Run workflow**.
5.  Choose the source:
    -   `chartink`
    -   `watchlist`
    -   `both`
6.  If using watchlists, optionally enter the filenames.
7.  Start the workflow.
8.  Wait for it to finish.
9.  Open the completed workflow run.
10. Download the artifacts.

------------------------------------------------------------------------

# 14. Watchlist selection in GitHub Actions

For example, if your folder contains:

``` text
watchlists/
├── momentum.xlsx
├── smallcap.xlsx
└── portfolio.xlsx
```

you can leave the watchlist filename input blank to use all available
files.

Or specify:

``` text
momentum.xlsx,smallcap.xlsx
```

to use only those two files.

------------------------------------------------------------------------

# 15. What is an EMA?

**EMA** means **Exponential Moving Average**.

It is a moving average that gives more weight to recent prices.

This project focuses heavily on:

``` text
10 EMA
30 EMA
```

The basic idea is:

-   10 EMA reacts faster
-   30 EMA reacts more slowly

A healthy bullish structure often has the faster average above the
slower average, with both moving upward.

------------------------------------------------------------------------

# 16. The 10/30 EMA breakout idea

The scanner is built around a simple framework:

``` text
Price
  ↑
10 EMA
  ↑
30 EMA
```

with the averages themselves preferably rising.

The project also looks for price approaching or making a recent high and
checks volume behavior.

The exact screening rules are defined in the code.

------------------------------------------------------------------------

# 17. Weekly vs Daily settings

The same general concept is used on both timeframes, but the numerical
settings are scaled.

### Weekly

The project uses a larger-bar perspective.

Weekly scoring requires at least approximately:

``` text
40 weekly bars
```

of usable history.

### Daily

Daily scoring requires at least approximately:

``` text
60 daily bars
```

of usable history.

A stock without enough historical data is skipped rather than being
scored from an incomplete history.

------------------------------------------------------------------------

# 18. What is a Stage?

The weekly quant scorer uses a **Weinstein-style Stage 1--4 framework**.

### Stage 1 --- Basing

The stock is building a base.

Think:

> "Not moving strongly yet."

### Stage 2 --- Advancing

The stock is in an uptrend.

This is generally the preferred stage for a bullish breakout-style
strategy.

Think:

> "The stock is moving upward."

### Stage 3 --- Topping

The previous advance may be losing strength.

Think:

> "The uptrend may be becoming mature."

### Stage 4 --- Declining

The stock is in a downtrend.

Think:

> "The stock is moving lower."

The stage is calculated from numerical trend and EMA information. It is
not a guarantee about what happens next.

------------------------------------------------------------------------

# 19. EMA Alignment

`EMA_Alignment` describes how the price and moving averages relate to
one another.

A stronger bullish alignment generally looks like:

``` text
Price
10 EMA
30 EMA
```

with upward slopes.

Poor alignment can indicate:

-   Choppy price action
-   Weak trend
-   Downtrend
-   A stock that is not ready

------------------------------------------------------------------------

# 20. What is a base?

A **base** is a period where a stock pauses or consolidates after a
move.

A simple example:

``` text
        /\       /\
       /  \_____/  \
      /             \
_____/               \____
```

The stock is not moving straight up. It is spending time in a range.

The quant scorer tries to identify the current base using swing highs,
swing lows, and pullback legs.

------------------------------------------------------------------------

# 21. Base structure

The project reports a field such as:

``` text
BaseStructure
```

This describes what the numerical pattern resembles.

It may identify structures such as:

-   VCP
-   Flag
-   Bull Flag
-   Cup with Handle
-   Flat Base
-   Long Base
-   Wedge
-   Ascending Triangle
-   Double Bottom
-   Rounding Base
-   No Clear Base

The system uses rules rather than an image-recognition model.

Because real stock charts can be messy, a numerical rule can
occasionally classify a pattern differently from a human chart reader.

------------------------------------------------------------------------

# 22. What is VCP?

**VCP** means **Volatility Contraction Pattern**.

The basic idea is that price swings become progressively smaller as the
stock moves through a base.

Conceptually:

``` text
Large swing
     ↓
  smaller swing
       ↓
    smaller swing
          ↓
        tight
```

The project checks whether the detected pullback legs are shrinking.

If they are, the setup may be classified as a VCP-style contraction.

------------------------------------------------------------------------

# 23. Base tightness

The project measures how deep the current base is.

A smaller pullback is generally considered tighter.

The daily vision-style scoring uses:

  Base depth   Meaning
  ------------ ---------
  Below 20%    Shallow
  20--35%      Normal
  Above 35%    Deep

For weekly quant scoring, base detection uses its own configured
thresholds.

Do not assume that every timeframe uses exactly the same internal
calculation.

------------------------------------------------------------------------

# 24. Volume signature

Volume helps answer:

> "Are people trading much more or much less during this setup?"

The system checks for things such as:

-   Volume drying up inside a base
-   Volume expansion during a trigger
-   Extremely large/climactic volume

A healthy consolidation may show quieter volume before a potential
breakout.

But volume alone is not enough to make a trading decision.

------------------------------------------------------------------------

# 25. Trigger candle quality

The project checks the latest bar when the price is near or above the
pivot.

One important measurement is where the closing price sits inside the
day's/bar's high-low range.

A stronger bullish trigger generally has a close nearer the upper part
of its range.

Again, this is a numerical quality check, not a guarantee of
follow-through.

------------------------------------------------------------------------

# 26. Breakout Status

The weekly report includes:

``` text
BreakoutStatus
```

This describes the current setup.

A stock can be classified as things such as:

-   Ready Now
-   Breaking Out
-   Already Extended
-   No Setup / Downtrend

The exact labels come from the scorer configuration.

### Already Extended

This generally means the stock has already moved too far beyond the
logical trigger area to be treated as a fresh breakout setup.

### No Setup / Downtrend

The numerical conditions do not support the desired setup.

------------------------------------------------------------------------

# 27. Readiness in the daily report

The daily report uses:

``` text
Readiness
```

with categories such as:

-   `Ready Now`
-   `Forming`
-   `Extended`
-   `Broken`

### Ready Now

The setup appears close to a trigger.

### Forming

The structure is developing but needs more time.

### Extended

The move has already happened too far.

### Broken

The expected structure has failed.

------------------------------------------------------------------------

# 28. Pivot Price

The project calculates:

``` text
PivotPrice
```

The pivot is generally based on the high of the detected base.

Think of it as:

> "The price level the stock needs to overcome for the breakout
> structure to become more interesting."

Example:

``` text
PivotPrice = ₹1,250
```

This is a calculated reference level, not an instruction to buy at
₹1,250.

------------------------------------------------------------------------

# 29. Stop Level

The project calculates:

``` text
StopLevel
```

This is generally based on a relevant base/recent low.

Conceptually:

``` text
Pivot
  ↑
  |
  | potential setup area
  |
Stop
```

It is intended to show where the setup may become invalid.

------------------------------------------------------------------------

# 30. Stoploss Percent

`StoplossPercent` represents the approximate percentage distance between
the relevant entry/pivot and stop level.

Example:

``` text
Pivot = ₹1,000
Stop  = ₹950
```

The approximate distance is:

``` text
5%
```

This helps you understand how much price movement would separate the
pivot from the calculated stop.

------------------------------------------------------------------------

# 31. Target 1

The weekly quant scorer calculates:

``` text
Target1
```

using a measured-move style calculation based on the pivot and base
height.

In simple terms:

``` text
Base height
     ↓
Pivot + base height
     ↓
Target 1
```

It is a theoretical chart-derived target, not a prediction.

------------------------------------------------------------------------

# 32. The Score

The project calculates a numerical setup score.

The important point is:

> **The score is a rule-based measure of how well the stock matches the
> project's technical criteria.**

It is **not**:

-   Probability of profit
-   Guaranteed return
-   Prediction accuracy
-   A recommendation to buy

The weights are explicitly defined in the scorer configuration, so the
calculation is traceable.

------------------------------------------------------------------------

# 33. Daily score vs Weekly score

Do not assume the two scores mean exactly the same thing.

### Weekly Score

Uses the weekly quant scoring framework, including:

-   Stage
-   EMA alignment
-   Base structure
-   Base tightness
-   Contractions
-   Volume signature
-   Trigger quality
-   Breakout status
-   Risk/reward-related factors

### Daily Score

Uses the daily vision-style numerical scoring framework.

It evaluates fields such as:

-   Linearity
-   MA status
-   Pattern
-   Base depth
-   Distribution
-   Institutional-footprint-style criteria
-   Readiness
-   Pivot/stop information

The combined system knows that these are different schemas.

------------------------------------------------------------------------

# 34. What does "Linearity" mean?

This is mainly used in the daily scoring model.

It measures how efficiently price moved during the advance into the
base.

A simplified idea is:

``` text
Net movement
--------------
Total path traveled
```

A cleaner upward move has a higher efficiency ratio.

A stock that moves:

``` text
↑ ↓ ↑ ↓ ↑ ↓
```

may be less linear than one that moves:

``` text
↑ ↑ ↑ ↑ ↑
```

This is a mathematical approximation of trend cleanliness.

------------------------------------------------------------------------

# 35. Distribution Check

The daily scoring checks for heavy selling behavior inside the base.

For example:

-   Down days
-   Above-average volume
-   Repeated selling pressure

The result can distinguish a cleaner base from one showing heavier
distribution.

------------------------------------------------------------------------

# 36. Relative Strength

The combined/weekly processing also calculates relative-strength-style
metrics against:

-   NIFTY50
-   NIFTY SMALLCAP100

This helps answer:

> "Is this stock performing better or worse than a market benchmark?"

A stock can have a good-looking pattern but still be weak compared with
the broader market.

Relative strength should therefore be treated as additional context.

------------------------------------------------------------------------

# 37. Liquidity Rush

The project also incorporates liquidity-related information from the
Chartink Excel results.

The combined output can include fields related to:

``` text
Liquidity Rush
%ofMCAP10days
%ofMCAP20days
```

These help provide context about trading activity relative to the
company's market capitalization.

The values are supplemental metrics, not standalone buy signals.

------------------------------------------------------------------------

# 38. The output files

Depending on which pipeline you run, you may see files such as:

### Weekly

``` text
nse_setups_weekly_results_<date>.csv
nse_setups_weekly_with_liquidity_<date>.xlsx
```

### Daily

``` text
nse_setups_daily_results_<date>.csv
nse_setups_daily_with_liquidity_<date>.xlsx
```

### Combined

``` text
nse_setups_combined_results_<date>.csv
nse_setups_combined_with_liquidity_<date>.xlsx
```

You may also see:

``` text
Chartink_Screener_*.xlsx
```

which contains the raw screener results.

------------------------------------------------------------------------

# 39. How to read the Excel report

The Excel report is designed to make the data easier to review.

You can look at:

-   Symbol
-   Score
-   Stage
-   EMA alignment
-   Base structure
-   Base tightness
-   Volume signature
-   Breakout status
-   Pivot
-   Stop
-   Target
-   Liquidity metrics
-   Relative strength

If you are new, start by sorting the score from highest to lowest and
then inspect the underlying fields.

------------------------------------------------------------------------

# 40. How to read the combined report

The combined report places weekly and daily information side by side.

You may see columns like:

``` text
Weekly_Score
Daily_Score
CombinedScore
Confluence
```

### CombinedScore

The project averages the available weekly and daily scores.

If both are available:

``` text
CombinedScore =
(Weekly Score + Daily Score) / 2
```

If only one timeframe is available, that available score is used.

### Confluence

The default logic requires:

-   Both timeframe scores meet the configured minimum
-   Weekly is not `No Setup / Downtrend`
-   Daily is not `Broken`
-   Daily is not `Extended`

This is intended to highlight stocks where the larger trend and
shorter-term setup agree.

------------------------------------------------------------------------

# 41. A beginner-friendly way to review results

Do not start by looking only at `Score`.

Use this order:

### 1. Check Confluence

If using the combined report, see whether:

``` text
Confluence = True
```

### 2. Check weekly trend

Look at:

``` text
Stage
EMA_Alignment
```

You generally want to understand whether the stock is actually trending
rather than just bouncing.

### 3. Check the base

Look at:

``` text
BaseStructure
BaseTightness
Contractions
```

### 4. Check volume

Look at:

``` text
VolumeSignature
```

### 5. Check daily timing

Look at:

``` text
Readiness
BreakoutStatus
```

### 6. Check risk levels

Look at:

``` text
PivotPrice
StopLevel
StoplossPercent
Target1
```

### 7. Check relative strength

Compare performance with the relevant benchmark.

### 8. Do your own verification

Only after all of this should a stock become a candidate for deeper
research.

------------------------------------------------------------------------

# 42. Example interpretation

Suppose a stock has:

``` text
Weekly Stage: 2
Weekly EMA Alignment: Bullish
Base Structure: VCP
Base Tightness: Tight
Volume Signature: Dry-up + expansion
Daily Readiness: Ready Now
Daily Score: High
Weekly Score: High
Confluence: True
```

This means:

> The stock matches many of the project's preferred technical
> characteristics across both timeframes.

It does **not** mean:

> "The stock will definitely go up."

That distinction is extremely important.

------------------------------------------------------------------------

# 43. Why this version is different from an AI chart scanner

This project deliberately avoids vision/LLM analysis.

### Advantages

-   No Gemini API required
-   No image generation
-   Faster processing
-   Same inputs produce reproducible calculations
-   Easier to backtest
-   No AI API cost
-   Scores are based on explicit rules

### Limitations

A pure numerical system may struggle with things a human sees visually.

For example:

-   A messy VCP that looks valid to an experienced trader
-   Unusual chart shapes
-   Corporate actions/data anomalies
-   Context that is difficult to express as fixed thresholds
-   "Something looks wrong" intuition

Therefore, high scores should still be checked against the actual chart.

------------------------------------------------------------------------

# 44. Market data source

The scorer gets market data through:

``` text
yfinance
```

for NSE symbols.

The default Yahoo Finance suffix is:

``` text
.NS
```

So:

``` text
TCS
```

becomes:

``` text
TCS.NS
```

For BSE experiments, the code can be adapted by changing the suffix to:

``` text
.BO
```

This is a code/configuration change and should be tested before relying
on it.

------------------------------------------------------------------------

# 45. Data limitations

External market-data services can have:

-   Missing data
-   Delays
-   Symbol changes
-   Temporary outages
-   Corporate-action adjustments
-   Unexpected values

The project has a basic protection against extreme single-bar moves that
may indicate data artifacts, but it cannot perfectly identify every data
problem.

Always verify important prices against a trusted market source.

------------------------------------------------------------------------

# 46. Chartink limitations

The Chartink clauses are stored in:

``` text
chartink_screener.py
```

The project documentation notes that some clause syntax should be
paste-tested against the live Chartink scanner before changing
thresholds or relying on a modified clause.

If you modify a screener, test it independently first.

------------------------------------------------------------------------

# 47. What happens when a stock has too little data?

The project intentionally skips stocks that do not have enough history.

Approximate minimums are:

``` text
Weekly: 40 bars
Daily: 60 bars
```

This is important because moving averages and base calculations need
enough historical data to be meaningful.

------------------------------------------------------------------------

# 48. Common problems

## "No stocks found"

Possible causes:

-   Chartink returned no matches
-   Screener rules are too restrictive
-   Watchlist is empty
-   Watchlist filename is wrong
-   Tickers are invalid

### What to check

First confirm that your source contains valid NSE symbols.

------------------------------------------------------------------------

## "Stock was skipped"

Usually this means:

-   Not enough historical data
-   Invalid/unavailable Yahoo Finance symbol
-   Empty data returned
-   Data could not be processed

This is not necessarily a program failure.

------------------------------------------------------------------------

## Email was not sent

Check:

``` text
SMTP_EMAIL
SMTP_PASSWORD
```

Also check that the credentials are correctly stored in GitHub Secrets.

------------------------------------------------------------------------

## GitHub workflow did not run automatically

The workflows currently use manual triggering.

Open:

``` text
Actions
```

and select:

``` text
Run workflow
```

------------------------------------------------------------------------

# 49. Keeping your credentials safe

Never put credentials directly into Python source code.

Bad:

``` python
SMTP_PASSWORD = "my-real-password"
```

Better:

``` text
GitHub Secrets
```

or a local `.env` file that is not committed.

Before pushing the repository to GitHub:

-   Check for passwords
-   Check for API keys
-   Check for SMTP credentials
-   Check for private files
-   Check `.gitignore`

If a secret is accidentally published, rotate/revoke it immediately.

------------------------------------------------------------------------

# 50. Recommended beginner setup

If you are completely new, start with a small watchlist.

For example:

``` text
watchlists/my_watchlist.xlsx
```

with:

  Ticker
  -----------
  TCS
  INFY
  RELIANCE
  HDFCBANK
  ICICIBANK

Then run the weekly scan.

After understanding the weekly report, run:

``` bash
python main_daily.py
```

Finally, try:

``` bash
python main_combined.py
```

This makes it easier to understand what each timeframe contributes.

------------------------------------------------------------------------

# 51. Recommended workflow for everyday use

A simple process is:

``` text
1. Run Combined Scan
        ↓
2. Look for Confluence = True
        ↓
3. Review Weekly Stage
        ↓
4. Review EMA Alignment
        ↓
5. Review Base Structure
        ↓
6. Review Volume
        ↓
7. Review Daily Readiness
        ↓
8. Check Pivot / Stop / Target
        ↓
9. Check Relative Strength
        ↓
10. Verify the actual chart
        ↓
11. Make your own decision
```

The system is a **research assistant**, not an automatic trading
decision-maker.

------------------------------------------------------------------------

# 52. What the project does NOT do

This project does not:

-   Guarantee profits
-   Predict the future with certainty
-   Automatically place broker orders
-   Guarantee a breakout
-   Guarantee that a score will work in every market
-   Replace your own research

It is designed to help you **find, organize, measure, and rank technical
setups**.

------------------------------------------------------------------------

# 53. Financial disclaimer

This software is for educational and research purposes only.

The scores, technical classifications, pivot levels, stop levels,
targets, relative-strength measurements, liquidity metrics, and other
outputs are not financial advice and are not guarantees of future
performance.

Markets can move unexpectedly, and technical setups can fail.

Always verify important information, understand the risks, and make your
own investment decisions. Consider consulting a qualified financial
professional when appropriate.

**Never risk money you cannot afford to lose.**

------------------------------------------------------------------------

# 54. Quick reference

### Weekly

``` bash
python main.py
```

### Daily

``` bash
python main_daily.py
```

### Both

``` bash
python main_combined.py
```

### Main input folder

``` text
watchlists/
```

### Main source file

``` text
chartink_screener.py
```

### Main scoring engine

``` text
quant_scorer.py
```

### GitHub workflows

``` text
.github/workflows/Pipeline.yaml
.github/workflows/PipelineDaily.yaml
.github/workflows/PipelineCombined.yaml
```

### Credentials

``` text
SMTP_EMAIL
SMTP_PASSWORD
```

### No Gemini required

``` text
✓ Quant calculations
✓ No LLM
✓ No vision model
✓ No chart images required
```

------------------------------------------------------------------------

# 55. In one sentence

> **NSE Quant finds potentially interesting NSE stocks, measures their
> weekly and/or daily technical structure using explicit numerical
> rules, ranks the setups, and gives you reports so you can decide which
> stocks deserve further research.**

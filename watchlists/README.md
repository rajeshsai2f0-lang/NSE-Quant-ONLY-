# Watchlists

Drop up to **10** `.xlsx` files in this folder to use as manually-curated
ticker sources — an alternative (or supplement) to the Chartink screeners.

## Format

- One ticker per row. Any sheet name, any number of sheets — every sheet
  in the file is read and combined.
- Ticker column: any of `Ticker`, `Symbol`, `NSE Code`, `NSECODE`, `Stock`,
  `Scrip` (case-insensitive). If none of those column names are found, the
  **first column** is used.
- Optional `Market Cap` column (in ₹ Cr) — if present, it's carried
  through so the `%ofMCAP10days` / `%ofMCAP20days` columns in the output
  Excel get computed. If it's missing, those two columns are left blank
  for tickers from that file (Market Cap isn't auto-fetched today).
- Everything else in the sheet is ignored — no need to strip other columns
  before dropping a file in here.
- A bare ticker like `TCS` or a `.NS`-suffixed one like `TCS.NS` both work.

## Example

See `examples/sample_template.xlsx` for a ready-made sample in this exact
format (not picked up automatically — it lives in a subfolder so it never
counts toward your 10 real watchlist files).

| Ticker | Market Cap |
|--------|-----------:|
| TCS    | 1,450,000  |
| INFY   | 620,000    |
| RELIANCE | 1,900,000 |

## Selecting which files to use at runtime

See `source_selector.py`:

- **Local runs**: you'll get an interactive menu to pick Chartink only,
  watchlist(s) only, or both — and, if watchlists, which specific file(s).
- **GitHub Actions runs**: set the `SOURCE_MODE` (`chartink` / `watchlist`
  / `both`) and optional `WATCHLIST_FILES` (comma-separated filenames,
  e.g. `momentum.xlsx,smallcap_ideas.xlsx` — leave blank to use every file
  found here) inputs on the workflow's manual trigger.

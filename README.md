# StockScreener 📊

**Open-source Indian stock screener** — scrapes [screener.in](https://www.screener.in) for 50+ fundamental metrics, scores stocks A+ to D, and serves an interactive dashboard you can run locally or deploy as a static site.

**[🔗 Live Demo — Nifty 50 Dashboard](https://kustonaut.github.io/StockScreener/)**

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Stars](https://img.shields.io/github/stars/kustonaut/StockScreener?style=social)

---

## Why This Exists

Most free stock screeners give you a table of numbers. This one gives you a **full analyst dashboard** for every stock — quality grades, Sankey income flows, candlestick charts, red/green flags, shareholding patterns, and peer comparisons — all from a single command. No API keys. No paid subscriptions. Just `pip install` and go.

## Features

| Feature | Description |
|---------|-------------|
| **Deep Fundamentals** | Revenue, profit, margins, ROE, ROCE, debt, promoter holding, institutional ownership, and 50+ metrics |
| **Quality Scoring** | A+ to D grading across growth, profitability, financial health, valuation, and momentum |
| **Interactive Dashboard** | Vertical sidebar with tabbed stocks, search/add/delete |
| **Accordion Sections** | Group stocks into collapsible sections (Nifty 50, Watchlist, Portfolio) |
| **Live Server** (`--serve`) | Add & analyse stocks in real-time from the browser |
| **Static Demo** (`--demo`) | Generate a split-file SPA deployable to GitHub Pages |
| **Smart Autocomplete** | Instant suggestions from screener.in (server) or pre-built index (static) |
| **Price Charts** | 1Y / 3Y / 5Y / All-time candlestick charts via yfinance + Plotly |
| **Income Sankey** | Visualise revenue → expenses → profit flow |
| **Keyboard Navigation** | ↑↓ arrows to switch stocks, Enter to select |
| **Watchlist Files** | Load tickers from `.txt` files, paste comma-separated lists |

## Quick Start

```bash
# Clone & install
git clone https://github.com/kustonaut/StockScreener.git
cd StockScreener
pip install -r requirements.txt

# Terminal report for a single stock
python company_screener.py RELIANCE

# HTML dashboard from a watchlist
python company_screener.py -w watchlist_sample.txt --html

# 🚀 Live interactive server (recommended)
python company_screener.py -w watchlist_sample.txt --serve

# Generate deployable demo site (GitHub Pages ready)
python company_screener.py --sections "Nifty 50:watchlist_nifty50.txt" --demo docs/
```

### Live Server Mode

The `--serve` mode starts a local web server at `http://localhost:9000` where you can:
- **Search & add** any stock with autocomplete
- **Delete** stocks with ✕ on each tab
- **Paste** comma/newline-separated ticker lists
- **Navigate** with ↑↓ arrow keys

### Static Demo Mode

The `--demo` flag generates a **split-file SPA** — a lightweight shell (~40 KB) plus individual stock pane files (~1.5 MB each), loaded on demand. Deploy to GitHub Pages, Netlify, or any static host:

```bash
python company_screener.py --sections "Nifty 50:watchlist_nifty50.txt" --demo docs/
# Creates: docs/index.html + docs/panes/RELIANCE.html, docs/panes/TCS.html, ...
```

## Usage

```
python company_screener.py [TICKERS] [OPTIONS]

positional arguments:
  tickers               Ticker symbols (e.g., RELIANCE TCS INFY)

options:
  --watchlist, -w FILE  Load tickers from watchlist file
  --sections NAME:FILE  Accordion sections (e.g., "Nifty 50:nifty50.txt")
  --serve               Start live interactive server
  --port PORT           Server port (default: 9000)
  --html                Generate static HTML dashboard
  --demo DIR            Generate split-file demo site in DIR
  --brief, -b           Quick scorecard only (terminal)
  --json                Export raw data as JSON
  --standalone, -s      Use standalone financials (default: consolidated)
  --no-open             Don't auto-open in browser
  --output, -o FILE     Custom output file path
```

## Watchlist File Format

```
# My Portfolio
RELIANCE
TCS
HDFCBANK
INFY

# Watchlist
MARUTI
TITAN
```

## What You Get

### Terminal Report
Colour-coded terminal output with scorecard, key metrics, flags, and grade.

### HTML Dashboard
Multi-stock tabbed dashboard with:
- Stock header with price, market cap, grade badge
- Scorecard (growth, profitability, health, valuation, momentum)
- Candlestick price charts with period switcher (1Y / 3Y / 5Y / All-time)
- Income flow Sankey diagram
- Annual P&L, quarterly results, margin trends
- Return ratios, shareholding patterns, cash flow analysis
- Detailed metrics tables with conditional formatting
- Red/green flags and risk indicators

### Live Server
Everything above, plus:
- Real-time stock addition via search bar
- Autocomplete from screener.in company database
- One-click stock deletion
- Bulk paste multiple tickers
- localStorage cache to persist your watchlist across reloads

## Architecture

```
company_screener.py     # Main script (~3800 lines)
├── fetch_full_company_data()   # Scrapes screener.in
├── analyze()                   # Scoring engine (50+ metrics → A+ to D)
├── _generate_charts()          # yfinance + Plotly candlestick charts
├── _build_stock_pane()         # HTML fragment for one stock
├── generate_dashboard()        # Monolithic HTML (--html)
├── generate_demo_site()        # Split-file SPA (--demo)
├── run_server()                # Live HTTP server (--serve)
└── print_report()              # Terminal output

income_sankey.py        # Sankey diagram generator (imported by above)
```

## Data Sources

| Source | Data |
|--------|------|
| [screener.in](https://www.screener.in) | Fundamentals, financials, shareholding, peers |
| [Yahoo Finance](https://finance.yahoo.com) (via `yfinance`) | Historical price data |

> **Note**: This tool is for educational and personal use. Please respect screener.in's terms of service and rate limits. The tool includes a 1.5-second delay between requests.

## Contributing

Contributions are welcome! Some ideas:

- **More scoring dimensions** — add technical indicators, sector-relative scoring
- **Export formats** — PDF reports, CSV export
- **Global markets** — adapt the scraper for other data sources
- **UI improvements** — dark mode, responsive charts, comparison view
- **Tests** — unit tests for the scoring engine

Fork, branch, PR. Keep it simple.

## License

MIT — see [LICENSE](LICENSE).

---

Built with curiosity and [GitHub Copilot](https://github.com/features/copilot). Data from [screener.in](https://www.screener.in).

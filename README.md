# StockScreener 📊

**Full-stack Indian stock screener** that scrapes [screener.in](https://www.screener.in) for comprehensive fundamental data, generates quality scores, and serves an interactive dashboard with live stock analysis.

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

## Features

- **Deep Fundamentals**: Revenue, profit, margins, ROE, ROCE, debt, promoter holding, institutional ownership, and 50+ metrics
- **Quality Scoring**: A+ to D grading based on growth, profitability, financial health, valuation, and momentum
- **Interactive Dashboard**: Vertical sidebar with tabbed stocks, delete/add/search functionality
- **Live Server Mode** (`--serve`): Add and analyse stocks in real-time from the browser — no re-running scripts
- **Smart Autocomplete**: Type a ticker and get instant suggestions from screener.in
- **Price Charts**: 1Y / 3Y / 5Y / All-time candlestick charts via yfinance + Plotly
- **Income Sankey**: Visualise revenue → expenses → profit flow
- **Watchlist Support**: Load tickers from a file, paste comma-separated lists, or type them one by one
- **Keyboard Navigation**: ↑↓ arrows to browse suggestions, Enter to select, Escape to close

## Quick Start

```bash
# Clone
git clone https://github.com/kustonaut/StockScreener.git
cd StockScreener

# Install dependencies
pip install -r requirements.txt

# Terminal report
python company_screener.py RELIANCE

# Static HTML dashboard
python company_screener.py -w watchlist_sample.txt --html

# 🚀 Live interactive server (recommended)
python company_screener.py -w watchlist_sample.txt --serve
```

The `--serve` mode starts a local web server at `http://localhost:8765` where you can:
- **Search & add** any stock via the autocomplete search bar
- **Delete** stocks with the ✕ button on each tab
- **Paste** comma/newline-separated ticker lists
- **Navigate** with arrow keys

## Usage

```
python company_screener.py [TICKERS] [OPTIONS]

positional arguments:
  tickers               Ticker symbols (e.g., RELIANCE TCS INFY)

options:
  --watchlist, -w FILE  Load tickers from watchlist file
  --serve               Start live interactive server
  --port PORT           Server port (default: 8765)
  --html                Generate static HTML dashboard
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
- Candlestick price charts (4 time periods)
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

## Data Source

All fundamental data is scraped from [screener.in](https://www.screener.in).
Price data comes from [Yahoo Finance](https://finance.yahoo.com) via `yfinance`.

**Note**: This tool is for educational and personal use. Please respect screener.in's terms of service and rate limits. The tool includes a 1.5-second delay between requests.

## License

MIT — see [LICENSE](LICENSE).

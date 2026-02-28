# Copilot Instructions for StockScreener

## Project Overview

StockScreener is an open-source Python tool that scrapes [screener.in](https://www.screener.in) for Indian stock fundamentals, scores stocks A+ to D across 50+ metrics, and generates interactive HTML dashboards with candlestick charts and income flow Sankey diagrams.

## Architecture

```
company_screener.py     # Main script (~3800 lines)
├── fetch_full_company_data()   # Scrapes screener.in via requests + BeautifulSoup
├── analyze()                   # Scoring engine: 50+ metrics → A+ to D grade
├── _generate_charts()          # yfinance + Plotly candlestick charts
├── _build_stock_pane()         # HTML fragment for one stock tab
├── generate_dashboard()        # Monolithic HTML output (--html)
├── generate_demo_site()        # Split-file SPA for GitHub Pages (--demo)
├── run_server()                # Live HTTP server with autocomplete (--serve)
└── print_report()              # Colour-coded terminal output

income_sankey.py        # Sankey diagram generator (imported by company_screener.py)
```

## Tech Stack

- **Python 3.10+**
- **requests** + **BeautifulSoup4** — web scraping from screener.in
- **yfinance** — historical price data from Yahoo Finance
- **Plotly** — candlestick charts and Sankey diagrams
- **kaleido** — static image export for Plotly figures
- No database, no API keys required

## Development Setup

```bash
pip install -r requirements.txt

# Run against a single stock (terminal output)
python company_screener.py RELIANCE

# Generate an HTML dashboard
python company_screener.py -w watchlist_sample.txt --html

# Start the live server
python company_screener.py -w watchlist_sample.txt --serve
```

## Coding Conventions

- **Style**: Follow PEP 8. Use type hints on all new functions and methods.
- **Docstrings**: Every public function must have at least a one-line summary docstring. Match the existing `"""Short description."""` style for simple functions. For complex functions use a multi-line Google-style docstring with `Args:` and `Returns:` sections.
- **Helper functions**: Prefix private helpers with a leading underscore (e.g., `_build_stock_pane`).
- **HTML generation**: All HTML is built via Python f-strings — no external templating engine. Keep inline CSS scoped to the component being rendered.
- **Scraping**: Always include a short `time.sleep()` delay (≥ 1.5 s) between network requests to respect screener.in rate limits.
- **Error handling**: Wrap scraper calls in `try/except` blocks and return sensible defaults (`{}`, `[]`, `0`) rather than propagating exceptions to the user.
- **Figures in Crores**: All monetary values are in Indian Rupees (₹) and displayed in Crores. Use the existing `fmt_cr()` and `fmt_indian()` helpers from `income_sankey.py`.
- **No external state**: The script is intentionally stateless — all data flows through plain dicts returned by `fetch_full_company_data()` and `analyze()`.

## Key Data Structures

- `data` dict — raw scraped data returned by `fetch_full_company_data(ticker)`
- `analysis` dict — computed scores and signals returned by `analyze(data)`
- `charts` dict — Plotly HTML fragments returned by `_generate_charts(ticker)`

## Testing

There is no automated test suite yet. When adding tests, use `pytest` and place them in a `tests/` directory. Focus on unit-testing the scoring logic in `analyze()` with synthetic `data` dicts.

## Data Sources & Constraints

- **screener.in** — fundamentals, financials, shareholding, peer comparison. The scraper relies on the current DOM structure of screener.in company pages (e.g., `https://www.screener.in/company/RELIANCE/`).
- **Yahoo Finance** (via yfinance) — historical OHLCV price data, appending `.NS` suffix for NSE-listed stocks.
- Rate-limit courtesy delay: 1.5 s between screener.in requests.
- This tool is for **educational and personal use**; respect screener.in's terms of service.

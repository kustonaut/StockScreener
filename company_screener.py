#!/usr/bin/env python3
"""
Company Screener — Full Fundamental & Technical Analysis
=========================================================
Scrapes screener.in for comprehensive company data and generates a
rich analysis report with scoring, flags, and actionable insights.

Usage:
    python company_screener.py RELIANCE               # Single stock
    python company_screener.py TCS --brief             # Quick scorecard
    python company_screener.py INFY --html             # HTML dashboard
    python company_screener.py -w watchlist.txt --html  # From watchlist file
    python company_screener.py -w watchlist.txt --serve  # Live server mode
    python company_screener.py HDFCBANK --json          # Export raw JSON

Watchlist file format (one ticker per line, # for comments):
    # My Portfolio
    RELIANCE
    TCS
    HDFCBANK
"""

import argparse
import json
import os
import re
import sys
import time
import webbrowser
import threading
from datetime import datetime
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests
from bs4 import BeautifulSoup


# ─── Constants ────────────────────────────────────────────────────────────────

SCREENER_BASE = "https://www.screener.in/company"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ─── Color codes for terminal ────────────────────────────────────────────────

class C:
    """ANSI terminal colours."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GREY    = "\033[90m"
    BG_RED  = "\033[41m"
    BG_GRN  = "\033[42m"
    BG_YLW  = "\033[43m"
    BG_BLU  = "\033[44m"


# ─── Number Formatting ──────────────────────────────────────────────────────

def parse_number(text: str) -> float:
    """Parse Indian number format: '1,23,456' -> 123456.0"""
    if not text or text.strip() in ("", "-", "—", "N/A"):
        return 0.0
    text = text.strip().replace(",", "").replace("%", "").replace("₹", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def fmt_indian(value: float) -> str:
    """Format number with Indian comma system."""
    s = f"{int(abs(value)):,}"
    parts = s.split(",")
    if len(parts) <= 2:
        return s
    last = parts[-1]
    mid_num = "".join(parts[:-1])
    indian_parts = []
    while len(mid_num) > 2:
        indian_parts.insert(0, mid_num[-2:])
        mid_num = mid_num[:-2]
    if mid_num:
        indian_parts.insert(0, mid_num)
    return ",".join(indian_parts) + "," + last


def fmt_cr(value: float) -> str:
    """Format value in Crores with Indian notation."""
    abs_val = abs(value)
    sign = "-" if value < 0 else ""
    if abs_val >= 100000:
        lakh_cr = abs_val / 100000
        if lakh_cr >= 10:
            return f"{sign}₹{lakh_cr:.0f} Lakh Cr"
        return f"{sign}₹{lakh_cr:.1f} Lakh Cr"
    elif abs_val >= 1:
        return f"{sign}₹{fmt_indian(abs_val)} Cr"
    elif abs_val > 0:
        return f"{sign}₹{abs_val:.1f} Cr"
    return "₹0"


def fmt_pct(value: float, with_sign: bool = True) -> str:
    """Format percentage."""
    if value == 0:
        return "0%"
    sign = "+" if value > 0 and with_sign else ""
    return f"{sign}{value:.1f}%"


def fmt_rupee(value: float) -> str:
    """Format rupee amount."""
    if value >= 100000:
        return f"₹{value / 100000:.1f}L Cr"
    elif value >= 100:
        return f"₹{fmt_indian(value)} Cr"
    elif value >= 1:
        return f"₹{value:.1f} Cr"
    else:
        return f"₹{value:.2f}"


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

# Map portfolio ticker names that differ on screener.in
TICKER_MAP = {
    "INFOSYS": "INFY",
    "LTIM": "MINDTREE",
}

def fetch_full_company_data(ticker: str, consolidated: bool = True) -> dict:
    """
    Master fetch: scrape ALL available data from screener.in for a company.
    Returns a comprehensive dict with every section.
    """
    screener_ticker = TICKER_MAP.get(ticker.upper(), ticker.upper())
    suffix = "consolidated/" if consolidated else ""
    url = f"{SCREENER_BASE}/{screener_ticker}/{suffix}"

    import time as _time
    for attempt in range(3):
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 429:
            wait = 3 * (attempt + 1)
            print(f"   ⏳ Rate-limited, retrying in {wait}s...")
            _time.sleep(wait)
            continue
        break

    if resp.status_code == 404:
        url = f"{SCREENER_BASE}/{ticker.upper()}/"
        resp = requests.get(url, headers=HEADERS, timeout=30)

    if resp.status_code != 200:
        raise ValueError(f"Failed to fetch data for {ticker}: HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")

    # If consolidated has no data, fall back
    if consolidated and suffix:
        test_pl = _parse_table_section(soup, "profit-loss")
        if not test_pl.get("periods"):
            url = f"{SCREENER_BASE}/{ticker.upper()}/"
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

    company_name = ""
    h1 = soup.find("h1")
    if h1:
        company_name = h1.get_text(strip=True)

    is_consolidated = "consolidated" in resp.url
    company_id = _extract_company_id(resp.text)

    return {
        "ticker": ticker.upper(),
        "company_name": company_name,
        "is_consolidated": is_consolidated,
        "company_id": company_id,
        "url": resp.url,
        "fetched_at": datetime.now().isoformat(),
        "about": _parse_about(soup),
        "top_ratios": _parse_top_ratios(soup),
        "pros_cons": _parse_pros_cons(soup),
        "compounded_growth": _parse_compounded_growth(soup),
        "profit_loss": _parse_table_section(soup, "profit-loss"),
        "quarterly": _parse_table_section(soup, "quarters"),
        "balance_sheet": _parse_table_section(soup, "balance-sheet"),
        "cash_flow": _parse_table_section(soup, "cash-flow"),
        "ratios": _parse_table_section(soup, "ratios"),
        "shareholding": _parse_shareholding(soup),
        "peers": _parse_peers(soup),
        "documents": _parse_documents(soup),
        "segments": _fetch_segments(company_id, is_consolidated) if company_id else [],
        "expense_breakdown": _fetch_expense_breakdown(company_id, is_consolidated) if company_id else {},
    }


def _extract_company_id(html: str) -> str:
    m = re.search(r'/api/company/(\d+)/', html)
    return m.group(1) if m else ""


def _parse_about(soup: BeautifulSoup) -> str:
    """Extract company description."""
    about = soup.find("div", class_="about")
    if about:
        # Find the main text paragraph (before the Key Points section)
        for p in about.find_all(["p", "div"]):
            text = p.get_text(strip=True)
            if len(text) > 40 and "Key Points" not in text and "Market Cap" not in text:
                return text
    return ""


def _parse_top_ratios(soup: BeautifulSoup) -> dict:
    """Parse the key ratios from the top card."""
    ratios = {}
    # Method 1: company-ratios list
    for li in soup.select(".company-ratios li"):
        name_span = li.find("span", class_="name")
        if not name_span:
            spans = li.find_all("span")
            name = spans[0].get_text(strip=True) if spans else ""
        else:
            name = name_span.get_text(strip=True)
        if not name:
            continue
        # Get ALL number spans (e.g., High/Low has two)
        number_spans = li.find_all("span", class_="number")
        if number_spans:
            val = "/".join(s.get_text(strip=True) for s in number_spans)
        else:
            spans = li.find_all("span")
            val = spans[1].get_text(strip=True) if len(spans) >= 2 else ""
        ratios[name] = val

    # Method 2 fallback: #top-ratios
    if not ratios:
        for li in soup.select("#top-ratios li"):
            name_el = li.find("span", class_="name")
            number_spans = li.find_all("span", class_="number")
            if name_el and number_spans:
                val = "/".join(s.get_text(strip=True) for s in number_spans)
                ratios[name_el.get_text(strip=True)] = val

    return ratios


def _parse_pros_cons(soup: BeautifulSoup) -> dict:
    """Parse machine-generated pros and cons."""
    pros = []
    cons = []
    pros_div = soup.find("div", class_="pros")
    if pros_div:
        for li in pros_div.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                pros.append(text)
    cons_div = soup.find("div", class_="cons")
    if cons_div:
        for li in cons_div.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                cons.append(text)
    return {"pros": pros, "cons": cons}


def _parse_compounded_growth(soup: BeautifulSoup) -> dict:
    """Parse compounded growth tables (Sales, Profit, Stock Price CAGR, ROE)."""
    result = {}
    for tbl in soup.find_all("table", class_="ranges-table"):
        rows = tbl.find_all("tr")
        if not rows:
            continue
        # First row is the category header
        header_cell = rows[0].find(["th", "td"])
        if not header_cell:
            continue
        category = header_cell.get_text(strip=True)
        values = {}
        for row in rows[1:]:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if len(cells) >= 2:
                key = cells[0].rstrip(":")
                values[key] = cells[1]
        if values:
            result[category] = values
    return result


def _parse_table_section(soup: BeautifulSoup, section_id: str) -> dict:
    """Parse any tabular section (P&L, BS, CF, Ratios)."""
    section = soup.find("section", id=section_id)
    if not section:
        return {}
    table = section.find("table")
    if not table:
        return {}
    rows = table.find_all("tr")
    if not rows:
        return {}

    header_cells = rows[0].find_all(["th", "td"])
    periods = []
    for cell in header_cells[1:]:
        text = cell.get_text(strip=True)
        if text:
            periods.append(text)

    result = {"periods": periods, "rows": {}}
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        label = cells[0].get_text(strip=True).rstrip("+").strip()
        if not label:
            continue
        values = [cell.get_text(strip=True) for cell in cells[1:]]
        result["rows"][label] = values
    return result


def _parse_shareholding(soup: BeautifulSoup) -> dict:
    """Parse shareholding pattern."""
    section = soup.find("section", id="shareholding")
    if not section:
        return {}
    table = section.find("table")
    if not table:
        return {}
    rows = table.find_all("tr")
    if not rows:
        return {}

    header = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    data = {}
    for row in rows[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if cells:
            label = cells[0].rstrip("+").strip()
            data[label] = cells[1:]

    return {"periods": header[1:], "data": data}


def _parse_peers(soup: BeautifulSoup) -> list:
    """Parse peer comparison table."""
    section = soup.find("section", id="peers")
    if not section:
        return []
    table = section.find("table")
    if not table:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []

    headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    peers = []
    for row in rows[1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if cells and len(cells) >= 2:
            peer = {}
            for i, h in enumerate(headers):
                if i < len(cells):
                    peer[h] = cells[i]
            # Also capture the link to get ticker
            link = row.find("a", href=True)
            if link:
                href = link["href"]
                m = re.search(r'/company/([^/]+)/', href)
                if m:
                    peer["_ticker"] = m.group(1)
            peers.append(peer)
    return peers


def _parse_documents(soup: BeautifulSoup) -> dict:
    """Parse documents section — annual reports, concall links, BSE filings."""
    section = soup.find("section", id="documents")
    if not section:
        return {}

    annual_reports = []
    concalls = []
    filings = []

    for li in section.find_all("li"):
        text = li.get_text(strip=True)
        link = li.find("a", href=True)
        href = link["href"] if link else ""

        entry = {"text": text[:200], "url": href}

        if "financial year" in text.lower() or "annual report" in text.lower():
            annual_reports.append(entry)
        elif "concall" in text.lower() or "conference call" in text.lower() or "transcript" in text.lower():
            concalls.append(entry)
        elif "investor" in text.lower() or "analyst" in text.lower():
            concalls.append(entry)
        else:
            filings.append(entry)

    return {
        "annual_reports": annual_reports,
        "concalls": concalls,
        "filings": filings[:10],  # Limit
    }


def _fetch_segments(company_id: str, consolidated: bool = True) -> list:
    """Fetch product/business segment names."""
    params = "?consolidated=true" if consolidated else ""
    url = f"https://www.screener.in/api/segments/{company_id}/profit-loss/1/{params}"
    try:
        resp = requests.get(url, headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"}, timeout=10)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        sales_body = soup.find("tbody", attrs={"data-segment-line": "Sales"})
        if not sales_body:
            return []
        inner_table = sales_body.find("table")
        if not inner_table:
            return []
        segments = []
        skip = {"Sales", "Less: Intersegment", "Unallocated",
                "Reconciling Items", "Reconciline Items"}
        for tr in inner_table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if cells and cells[0] and cells[0] not in skip:
                segments.append(cells[0])
        return segments
    except Exception:
        return []


def _fetch_expense_breakdown(company_id: str, consolidated: bool = True) -> dict:
    """Fetch expense breakdown percentages."""
    params = f"?parent=Expenses&section=profit-loss"
    if consolidated:
        params += "&consolidated"
    url = f"https://www.screener.in/api/company/{company_id}/schedules/{params}"
    try:
        resp = requests.get(url, headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"}, timeout=10)
        if resp.status_code != 200:
            return {}
        data = json.loads(resp.text)
        result = {}
        for key, val in data.items():
            if not isinstance(val, dict):
                continue
            items = [(k, v) for k, v in val.items() if k != "isExpandable"]
            if items:
                pct = parse_number(str(items[-1][1]))
                clean_name = key.replace(" %", "").strip()
                if pct > 0:
                    result[clean_name] = pct
        return result
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def analyze(data: dict) -> dict:
    """
    Run comprehensive analysis on fetched data.
    Returns structured analysis with scores, flags, and insights.
    """
    ratios = data["top_ratios"]
    pl = data["profit_loss"]
    bs = data["balance_sheet"]
    cf = data["cash_flow"]
    ratio_tbl = data["ratios"]
    sh = data["shareholding"]
    growth = data["compounded_growth"]
    quarters = data["quarterly"]

    # ── Parse key metrics ──
    market_cap = parse_number(ratios.get("Market Cap", "0"))
    current_price = parse_number(ratios.get("Current Price", "0"))
    pe = parse_number(ratios.get("Stock P/E", "0"))
    book_value = parse_number(ratios.get("Book Value", "0"))
    div_yield = parse_number(ratios.get("Dividend Yield", "0"))
    roce = parse_number(ratios.get("ROCE", "0"))
    roe = parse_number(ratios.get("ROE", "0"))
    face_value = parse_number(ratios.get("Face Value", "0"))

    # High/Low parsing
    hl_text = ratios.get("High / Low", "0/0")
    hl_parts = hl_text.replace("₹", "").split("/")
    high_52w = parse_number(hl_parts[0]) if len(hl_parts) >= 1 else 0
    low_52w = parse_number(hl_parts[1]) if len(hl_parts) >= 2 else 0

    pb = current_price / book_value if book_value > 0 else 0

    # ── P&L trend analysis ──
    pl_analysis = _analyze_pl_trend(pl)
    qtr_analysis = _analyze_quarterly_trend(quarters)
    bs_analysis = _analyze_balance_sheet(bs)
    cf_analysis = _analyze_cash_flow(cf)
    sh_analysis = _analyze_shareholding(sh)

    # ── Compounded growth parsing ──
    growth_parsed = {}
    for category, vals in growth.items():
        growth_parsed[category] = {k: parse_number(v) for k, v in vals.items()}

    # ── Valuation assessment ──
    valuation = _assess_valuation(pe, pb, div_yield, roe, roce, growth_parsed)

    # ── Quality score ──
    quality = _score_quality(roe, roce, pl_analysis, bs_analysis, cf_analysis, growth_parsed)

    # ── Technical signals ──
    technical = _assess_technical(current_price, high_52w, low_52w, sh_analysis)

    # ── Red/green flags ──
    flags = _generate_flags(pe, pb, roe, roce, div_yield, pl_analysis, bs_analysis,
                            cf_analysis, sh_analysis, growth_parsed, qtr_analysis)

    return {
        "market_cap": market_cap,
        "current_price": current_price,
        "pe": pe,
        "pb": pb,
        "book_value": book_value,
        "div_yield": div_yield,
        "roce": roce,
        "roe": roe,
        "face_value": face_value,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pl_analysis": pl_analysis,
        "qtr_analysis": qtr_analysis,
        "bs_analysis": bs_analysis,
        "cf_analysis": cf_analysis,
        "sh_analysis": sh_analysis,
        "growth": growth_parsed,
        "valuation": valuation,
        "quality": quality,
        "technical": technical,
        "flags": flags,
    }


def _get_row_values(section: dict, label: str, n: int = 5) -> list:
    """Get last N numeric values for a row label from a table section."""
    rows = section.get("rows", {})
    for key, vals in rows.items():
        if label.lower() in key.lower():
            # Take last N values
            recent = vals[-n:] if len(vals) >= n else vals
            return [parse_number(v) for v in recent]
    return []


def _get_exact_row_values(section: dict, label: str, n: int = 5) -> list:
    """Exact match version."""
    rows = section.get("rows", {})
    for key, vals in rows.items():
        if key.strip().lower() == label.lower():
            recent = vals[-n:] if len(vals) >= n else vals
            return [parse_number(v) for v in recent]
    return []


def _cagr(start: float, end: float, years: int) -> float:
    """Compute CAGR."""
    if start <= 0 or end <= 0 or years <= 0:
        return 0
    return ((end / start) ** (1 / years) - 1) * 100


def _yoy_change(values: list) -> list:
    """Compute YoY % changes from a list of values."""
    changes = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            changes.append((values[i] / values[i - 1] - 1) * 100)
        else:
            changes.append(0)
    return changes


def _analyze_pl_trend(pl: dict) -> dict:
    """Analyze P&L trends over available years."""
    if not pl.get("periods"):
        return {}

    # Banks use "Revenue" not "Sales"; try both
    sales = _get_row_values(pl, "Sales", 12)
    is_bank = False
    if not sales or all(v == 0 for v in sales):
        sales = _get_exact_row_values(pl, "Revenue", 12)
        is_bank = True
    if not sales or all(v == 0 for v in sales):
        sales = _get_row_values(pl, "Total Income", 12)

    net_profit = _get_row_values(pl, "Net Profit", 12)

    # Margin: OPM for standard, Financing Margin for banks
    opm_vals = _get_row_values(pl, "OPM", 12)
    if (not opm_vals or all(v == 0 for v in opm_vals)):
        opm_vals = _get_row_values(pl, "Financing Margin", 12)

    eps_vals = _get_row_values(pl, "EPS", 12)
    op_profit = _get_row_values(pl, "Operating Profit", 12)
    if not op_profit or all(v == 0 for v in op_profit):
        op_profit = _get_row_values(pl, "Financing Profit", 12)
    expenses = _get_row_values(pl, "Expenses", 12)
    interest = _get_exact_row_values(pl, "Interest", 12)
    depreciation = _get_row_values(pl, "Depreciation", 12)
    dividend_payout = _get_row_values(pl, "Dividend Payout", 12)

    # Revenue trend
    sales_yoy = _yoy_change(sales)
    profit_yoy = _yoy_change(net_profit)

    # Margins
    opm_latest = opm_vals[-1] if opm_vals else 0
    npm_latest = (net_profit[-1] / sales[-1] * 100) if sales and net_profit and sales[-1] > 0 else 0

    # CAGR
    n_years = len(sales)
    sales_cagr_3y = _cagr(sales[-4], sales[-1], 3) if len(sales) >= 4 else 0
    sales_cagr_5y = _cagr(sales[-6], sales[-1], 5) if len(sales) >= 6 else 0
    profit_cagr_3y = _cagr(net_profit[-4], net_profit[-1], 3) if len(net_profit) >= 4 else 0
    profit_cagr_5y = _cagr(net_profit[-6], net_profit[-1], 5) if len(net_profit) >= 6 else 0

    # Margin trend (expanding or contracting?)
    margin_trend = "stable"
    if len(opm_vals) >= 3:
        recent_avg = sum(opm_vals[-2:]) / 2
        older_avg = sum(opm_vals[-4:-2]) / 2 if len(opm_vals) >= 4 else opm_vals[0]
        if recent_avg > older_avg + 2:
            margin_trend = "expanding"
        elif recent_avg < older_avg - 2:
            margin_trend = "contracting"

    # Consistency: how many years had positive profit growth?
    positive_growth_years = sum(1 for c in profit_yoy if c > 0)
    consistency = positive_growth_years / len(profit_yoy) if profit_yoy else 0

    return {
        "is_bank": is_bank,
        "sales_latest": sales[-1] if sales else 0,
        "net_profit_latest": net_profit[-1] if net_profit else 0,
        "eps_latest": eps_vals[-1] if eps_vals else 0,
        "opm_latest": opm_latest,
        "npm_latest": npm_latest,
        "sales_yoy": sales_yoy,
        "profit_yoy": profit_yoy,
        "sales_cagr_3y": sales_cagr_3y,
        "sales_cagr_5y": sales_cagr_5y,
        "profit_cagr_3y": profit_cagr_3y,
        "profit_cagr_5y": profit_cagr_5y,
        "margin_trend": margin_trend,
        "consistency": consistency,
        "sales_history": sales,
        "profit_history": net_profit,
        "opm_history": opm_vals,
        "eps_history": eps_vals,
        "dividend_payout": dividend_payout,
        "interest_latest": interest[-1] if interest else 0,
        "depreciation_latest": depreciation[-1] if depreciation else 0,
    }


def _analyze_quarterly_trend(qtr: dict) -> dict:
    """Analyze quarterly P&L trends."""
    if not qtr.get("periods"):
        return {}

    sales = _get_row_values(qtr, "Sales", 8)
    if not sales or all(v == 0 for v in sales):
        sales = _get_exact_row_values(qtr, "Revenue", 8)
    if not sales or all(v == 0 for v in sales):
        sales = _get_row_values(qtr, "Total Income", 8)
    net_profit = _get_row_values(qtr, "Net Profit", 8)
    opm_vals = _get_row_values(qtr, "OPM", 8)
    if not opm_vals or all(v == 0 for v in opm_vals):
        opm_vals = _get_row_values(qtr, "Financing Margin", 8)
    periods = qtr["periods"][-8:]

    # Quarter-over-quarter
    sales_qoq = _yoy_change(sales)
    profit_qoq = _yoy_change(net_profit)

    # YoY from quarterly (compare Q vs same Q last year)
    sales_yoy_q = 0
    profit_yoy_q = 0
    if len(sales) >= 5:
        sales_yoy_q = ((sales[-1] / sales[-5] - 1) * 100) if sales[-5] > 0 else 0
    if len(net_profit) >= 5:
        profit_yoy_q = ((net_profit[-1] / net_profit[-5] - 1) * 100) if net_profit[-5] > 0 else 0

    # Beat/miss trend (are recent quarters improving?)
    improving = False
    if len(opm_vals) >= 4:
        recent_avg = sum(opm_vals[-2:]) / 2
        old_avg = sum(opm_vals[-4:-2]) / 2
        improving = recent_avg > old_avg

    return {
        "sales_latest_q": sales[-1] if sales else 0,
        "profit_latest_q": net_profit[-1] if net_profit else 0,
        "opm_latest_q": opm_vals[-1] if opm_vals else 0,
        "sales_yoy_q": sales_yoy_q,
        "profit_yoy_q": profit_yoy_q,
        "sales_qoq": sales_qoq,
        "profit_qoq": profit_qoq,
        "periods": periods,
        "sales_history": sales,
        "profit_history": net_profit,
        "improving_margins": improving,
    }


def _analyze_balance_sheet(bs: dict) -> dict:
    """Analyze balance sheet strength."""
    if not bs.get("periods"):
        return {}

    equity = _get_row_values(bs, "Equity Capital", 5)
    reserves = _get_row_values(bs, "Reserves", 5)
    borrowings = _get_row_values(bs, "Borrowings", 5)
    total_liabilities = _get_row_values(bs, "Total Liabilities", 5)
    fixed_assets = _get_row_values(bs, "Fixed Assets", 5)
    cwip = _get_row_values(bs, "CWIP", 5)
    investments = _get_row_values(bs, "Investments", 5)
    other_assets = _get_row_values(bs, "Other Assets", 5)
    total_assets = _get_row_values(bs, "Total Assets", 5)

    # Latest values
    equity_latest = equity[-1] if equity else 0
    reserves_latest = reserves[-1] if reserves else 0
    borrowings_latest = borrowings[-1] if borrowings else 0
    total_assets_latest = total_assets[-1] if total_assets else 0

    # Shareholder equity
    shareholder_equity = equity_latest + reserves_latest

    # Debt-to-equity
    de_ratio = borrowings_latest / shareholder_equity if shareholder_equity > 0 else 0

    # Debt trend
    debt_trend = "stable"
    if len(borrowings) >= 3:
        if borrowings[-1] > borrowings[-3] * 1.2:
            debt_trend = "increasing"
        elif borrowings[-1] < borrowings[-3] * 0.8:
            debt_trend = "decreasing"

    return {
        "shareholder_equity": shareholder_equity,
        "borrowings": borrowings_latest,
        "total_assets": total_assets_latest,
        "de_ratio": de_ratio,
        "debt_trend": debt_trend,
        "borrowings_history": borrowings,
        "equity_history": [e + r for e, r in zip(equity, reserves)] if equity and reserves and len(equity) == len(reserves) else [],
        "cwip_latest": cwip[-1] if cwip else 0,
        "investments_latest": investments[-1] if investments else 0,
    }


def _analyze_cash_flow(cf: dict) -> dict:
    """Analyze cash flows."""
    if not cf.get("periods"):
        return {}

    cfo = _get_row_values(cf, "Operating Activity", 5)
    cfi = _get_row_values(cf, "Investing Activity", 5)
    cff = _get_row_values(cf, "Financing Activity", 5)
    net_cf = _get_row_values(cf, "Net Cash Flow", 5)

    cfo_latest = cfo[-1] if cfo else 0
    cfi_latest = cfi[-1] if cfi else 0
    cff_latest = cff[-1] if cff else 0

    # Cumulative free cash flow (CFO + CFI)
    fcf_values = [o + i for o, i in zip(cfo, cfi)] if cfo and cfi and len(cfo) == len(cfi) else []
    fcf_latest = fcf_values[-1] if fcf_values else 0

    # CFO consistency (all positive?)
    cfo_positive_years = sum(1 for v in cfo if v > 0)
    cfo_consistency = cfo_positive_years / len(cfo) if cfo else 0

    # Is company generating more cash than it spends on capex?
    fcf_positive = sum(1 for v in fcf_values if v > 0) if fcf_values else 0

    return {
        "cfo_latest": cfo_latest,
        "cfi_latest": cfi_latest,
        "cff_latest": cff_latest,
        "fcf_latest": fcf_latest,
        "cfo_history": cfo,
        "fcf_history": fcf_values,
        "cfo_consistency": cfo_consistency,
        "fcf_positive_years": fcf_positive,
        "total_years": len(cfo),
    }


def _analyze_shareholding(sh: dict) -> dict:
    """Analyze shareholding trends."""
    if not sh:
        return {}

    data = sh.get("data", {})
    periods = sh.get("periods", [])

    def parse_row(label):
        for key, vals in data.items():
            if label.lower() in key.lower():
                return [parse_number(v) for v in vals]
        return []

    promoter = parse_row("Promoter")
    fii = parse_row("FII")
    dii = parse_row("DII")
    public = parse_row("Public")
    n_shareholders = parse_row("No. of Shareholders")

    # Trends (last 4 quarters)
    def trend(values, n=4):
        if len(values) < n:
            return "insufficient_data"
        recent = values[-n:]
        if recent[-1] > recent[0] + 0.5:
            return "increasing"
        elif recent[-1] < recent[0] - 0.5:
            return "decreasing"
        return "stable"

    return {
        "promoter_latest": promoter[-1] if promoter else 0,
        "fii_latest": fii[-1] if fii else 0,
        "dii_latest": dii[-1] if dii else 0,
        "public_latest": public[-1] if public else 0,
        "promoter_trend": trend(promoter),
        "fii_trend": trend(fii),
        "dii_trend": trend(dii),
        "n_shareholders_latest": n_shareholders[-1] if n_shareholders else 0,
        "promoter_history": promoter[-8:] if promoter else [],
        "fii_history": fii[-8:] if fii else [],
        "dii_history": dii[-8:] if dii else [],
        "periods": periods[-8:] if periods else [],
    }


def _assess_valuation(pe, pb, div_yield, roe, roce, growth) -> dict:
    """Assess if stock is fairly valued, overvalued, or undervalued."""
    signals = []
    score = 50  # neutral start

    # PE assessment
    if pe > 0:
        if pe < 10:
            signals.append(("PE < 10 — Deep value", "bullish"))
            score += 15
        elif pe < 20:
            signals.append(("PE 10-20 — Reasonably valued", "bullish"))
            score += 8
        elif pe < 35:
            signals.append(("PE 20-35 — Growth premium", "neutral"))
        elif pe < 60:
            signals.append(("PE 35-60 — Expensive", "bearish"))
            score -= 10
        else:
            signals.append(("PE > 60 — Very expensive", "bearish"))
            score -= 15

    # PB assessment
    if pb > 0:
        if pb < 1:
            signals.append(("P/B < 1 — Below book value", "bullish"))
            score += 10
        elif pb < 3:
            signals.append(("P/B 1-3 — Fair", "neutral"))
        elif pb > 6:
            signals.append(("P/B > 6 — Premium valuation", "bearish"))
            score -= 5

    # Dividend yield
    if div_yield > 3:
        signals.append((f"Div Yield {div_yield:.1f}% — Income stock", "bullish"))
        score += 5
    elif div_yield > 1:
        signals.append((f"Div Yield {div_yield:.1f}%", "neutral"))

    # PEG-like check (PE vs growth)
    profit_growth = growth.get("Compounded Profit Growth", {})
    growth_3y = profit_growth.get("3 Years", 0)
    if pe > 0 and growth_3y > 0:
        peg = pe / growth_3y
        if peg < 1:
            signals.append((f"PEG {peg:.1f} — Growth at reasonable price", "bullish"))
            score += 10
        elif peg < 2:
            signals.append((f"PEG {peg:.1f} — Fairly priced for growth", "neutral"))
        else:
            signals.append((f"PEG {peg:.1f} — Overpriced for growth", "bearish"))
            score -= 5

    # ROE vs cost of equity (~12%)
    if roe > 20:
        signals.append((f"ROE {roe:.1f}% — Excellent capital efficiency", "bullish"))
        score += 8
    elif roe > 15:
        signals.append((f"ROE {roe:.1f}% — Good", "bullish"))
        score += 4
    elif roe < 8:
        signals.append((f"ROE {roe:.1f}% — Below cost of equity", "bearish"))
        score -= 8

    verdict = "Fairly Valued"
    if score >= 70:
        verdict = "Undervalued"
    elif score >= 60:
        verdict = "Attractively Valued"
    elif score <= 30:
        verdict = "Overvalued"
    elif score <= 40:
        verdict = "Expensive"

    return {"score": min(100, max(0, score)), "verdict": verdict, "signals": signals}


def _score_quality(roe, roce, pl_analysis, bs_analysis, cf_analysis, growth) -> dict:
    """Score overall business quality 0-100."""
    score = 0
    details = []

    # ROE
    if roe > 20:
        score += 15
        details.append("Excellent ROE (>20%)")
    elif roe > 15:
        score += 10
        details.append("Good ROE (>15%)")
    elif roe > 10:
        score += 5
        details.append("Moderate ROE")
    else:
        details.append("Low ROE (<10%)")

    # ROCE
    if roce > 20:
        score += 15
        details.append("Excellent ROCE (>20%)")
    elif roce > 15:
        score += 10
        details.append("Good ROCE (>15%)")
    elif roce > 10:
        score += 5
    else:
        details.append("Low ROCE (<10%)")

    # Revenue growth consistency
    if pl_analysis.get("sales_cagr_5y", 0) > 15:
        score += 10
        details.append("Strong 5Y revenue CAGR >15%")
    elif pl_analysis.get("sales_cagr_5y", 0) > 10:
        score += 7

    # Profit growth
    if pl_analysis.get("profit_cagr_5y", 0) > 15:
        score += 10
        details.append("Strong 5Y profit CAGR >15%")
    elif pl_analysis.get("profit_cagr_5y", 0) > 10:
        score += 7

    # Margin trend
    if pl_analysis.get("margin_trend") == "expanding":
        score += 8
        details.append("Margins expanding")
    elif pl_analysis.get("margin_trend") == "contracting":
        score -= 5
        details.append("Margins contracting")

    # Profit consistency
    consistency = pl_analysis.get("consistency", 0)
    if consistency > 0.8:
        score += 10
        details.append("Highly consistent profits")
    elif consistency > 0.6:
        score += 5

    # Debt
    de = bs_analysis.get("de_ratio", 0)
    if de < 0.3:
        score += 10
        details.append("Very low debt")
    elif de < 1:
        score += 5
    elif de > 2:
        score -= 10
        details.append("High debt (D/E > 2)")

    # Cash flow
    cfo_consistency = cf_analysis.get("cfo_consistency", 0)
    if cfo_consistency >= 1.0:
        score += 10
        details.append("100% CFO positive years")
    elif cfo_consistency >= 0.8:
        score += 5

    # Free cash flow
    fcf_positive = cf_analysis.get("fcf_positive_years", 0)
    total = cf_analysis.get("total_years", 1)
    if total > 0 and fcf_positive / total >= 0.8:
        score += 5
        details.append("Strong free cash flow generator")

    # Grade
    if score >= 80:
        grade = "A+"
    elif score >= 65:
        grade = "A"
    elif score >= 50:
        grade = "B+"
    elif score >= 40:
        grade = "B"
    elif score >= 25:
        grade = "C"
    else:
        grade = "D"

    return {"score": min(100, max(0, score)), "grade": grade, "details": details}


def _assess_technical(price, high_52w, low_52w, sh_analysis) -> dict:
    """Basic technical/price signals."""
    signals = []

    if high_52w > 0 and low_52w > 0:
        range_52w = high_52w - low_52w
        pos_in_range = (price - low_52w) / range_52w if range_52w > 0 else 0.5
        pct_from_high = ((price - high_52w) / high_52w * 100) if high_52w > 0 else 0
        pct_from_low = ((price - low_52w) / low_52w * 100) if low_52w > 0 else 0

        if pos_in_range > 0.9:
            signals.append(("Near 52W High — Momentum strong", "bullish"))
        elif pos_in_range > 0.7:
            signals.append(("Upper 52W range — Positive trend", "bullish"))
        elif pos_in_range < 0.2:
            signals.append(("Near 52W Low — Potential value or distress", "caution"))
        elif pos_in_range < 0.35:
            signals.append(("Lower 52W range — Watch for reversal", "neutral"))

        signals.append((f"{pct_from_high:+.1f}% from 52W High, {pct_from_low:+.1f}% from 52W Low", "info"))
    else:
        pos_in_range = 0.5
        pct_from_high = 0
        pct_from_low = 0

    # Shareholding signals
    if sh_analysis:
        fii_trend = sh_analysis.get("fii_trend", "")
        dii_trend = sh_analysis.get("dii_trend", "")
        promoter_trend = sh_analysis.get("promoter_trend", "")

        if fii_trend == "increasing":
            signals.append(("FII increasing — Institutional confidence", "bullish"))
        elif fii_trend == "decreasing":
            signals.append(("FII decreasing — Institutional selling", "bearish"))

        if dii_trend == "increasing":
            signals.append(("DII increasing — Domestic institutional buying", "bullish"))

        if promoter_trend == "decreasing":
            signals.append(("Promoter stake declining — Watch", "caution"))
        elif promoter_trend == "increasing":
            signals.append(("Promoter stake increasing — Confidence signal", "bullish"))

    return {
        "pos_in_52w_range": pos_in_range,
        "pct_from_high": pct_from_high,
        "pct_from_low": pct_from_low,
        "signals": signals,
    }


def _generate_flags(pe, pb, roe, roce, div_yield, pl, bs, cf, sh, growth, qtr) -> dict:
    """Generate green flags (bullish) and red flags (bearish)."""
    green = []
    red = []
    amber = []

    # Valuation
    if pe > 0 and pe < 15:
        green.append(f"Low PE ({pe:.1f})")
    elif pe > 50:
        red.append(f"Very high PE ({pe:.1f})")

    if pb > 0 and pb < 1.5:
        green.append(f"Low P/B ({pb:.1f})")
    elif pb > 8:
        red.append(f"Very high P/B ({pb:.1f})")

    # Returns
    if roe > 20:
        green.append(f"High ROE ({roe:.1f}%)")
    elif roe < 8 and roe > 0:
        red.append(f"Low ROE ({roe:.1f}%)")

    if roce > 20:
        green.append(f"High ROCE ({roce:.1f}%)")
    elif roce < 8 and roce > 0:
        red.append(f"Low ROCE ({roce:.1f}%)")

    # Growth
    sales_growth = growth.get("Compounded Sales Growth", {})
    profit_growth = growth.get("Compounded Profit Growth", {})

    sg_3y = sales_growth.get("3 Years", 0)
    pg_3y = profit_growth.get("3 Years", 0)

    if sg_3y > 20:
        green.append(f"Strong 3Y sales CAGR ({sg_3y:.0f}%)")
    elif sg_3y < 5 and sg_3y >= 0:
        amber.append(f"Slow sales growth ({sg_3y:.0f}%)")
    elif sg_3y < 0:
        red.append(f"Sales declining ({sg_3y:.0f}%)")

    if pg_3y > 20:
        green.append(f"Strong 3Y profit CAGR ({pg_3y:.0f}%)")
    elif pg_3y < 0:
        red.append(f"Profits declining ({pg_3y:.0f}%)")

    # Margins
    if pl.get("margin_trend") == "expanding":
        green.append("Operating margins expanding")
    elif pl.get("margin_trend") == "contracting":
        red.append("Operating margins contracting")

    opm = pl.get("opm_latest", 0)
    if opm > 25:
        green.append(f"High operating margin ({opm:.0f}%)")
    elif opm < 8 and opm > 0:
        amber.append(f"Thin operating margin ({opm:.0f}%)")

    # Debt
    de = bs.get("de_ratio", 0)
    if de < 0.1:
        green.append("Virtually debt-free")
    elif de < 0.5:
        green.append(f"Low debt (D/E {de:.2f})")
    elif de > 2:
        red.append(f"High debt (D/E {de:.2f})")

    if bs.get("debt_trend") == "increasing":
        amber.append("Debt increasing over time")
    elif bs.get("debt_trend") == "decreasing":
        green.append("Debt reducing")

    # Cash flow
    cfo_consistency = cf.get("cfo_consistency", 0)
    if cfo_consistency >= 1.0:
        green.append("Positive operating cash flow every year")
    elif cfo_consistency < 0.6:
        red.append("Inconsistent operating cash flow")

    if cf.get("fcf_latest", 0) > 0:
        green.append("Positive free cash flow")
    elif cf.get("fcf_latest", 0) < 0:
        amber.append("Negative free cash flow")

    # Dividends
    if div_yield > 3:
        green.append(f"Attractive dividend yield ({div_yield:.1f}%)")

    # Shareholding
    if sh:
        if sh.get("promoter_latest", 0) > 60:
            green.append(f"High promoter holding ({sh['promoter_latest']:.1f}%)")
        elif sh.get("promoter_latest", 0) < 25:
            amber.append(f"Low promoter holding ({sh['promoter_latest']:.1f}%)")

        if sh.get("fii_trend") == "increasing":
            green.append("FIIs increasing stake")
        elif sh.get("fii_trend") == "decreasing":
            amber.append("FIIs reducing stake")

        if sh.get("promoter_trend") == "decreasing":
            red.append("Promoter stake declining")

    # Quarterly
    if qtr.get("improving_margins"):
        green.append("Quarterly margins improving")
    if qtr.get("profit_yoy_q", 0) > 20:
        green.append(f"Latest quarter profit up {qtr['profit_yoy_q']:.0f}% YoY")
    elif qtr.get("profit_yoy_q", 0) < -15:
        red.append(f"Latest quarter profit down {qtr['profit_yoy_q']:.0f}% YoY")

    return {"green": green, "red": red, "amber": amber}


# ═══════════════════════════════════════════════════════════════════════════════
# TERMINAL OUTPUT — Beautiful CLI Report
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(data: dict, analysis: dict, brief: bool = False):
    """Print a rich terminal report."""
    ticker = data["ticker"]
    company = data["company_name"]
    cons = "Consolidated" if data["is_consolidated"] else "Standalone"
    ratios = data["top_ratios"]
    a = analysis

    W = 74  # report width

    def hr(char="─"):
        return C.GREY + char * W + C.RESET

    def header(text, bg=C.BG_BLU):
        padded = f" {text} ".center(W)
        return f"\n{bg}{C.WHITE}{C.BOLD}{padded}{C.RESET}"

    def kv(key, val, color=C.WHITE, width=22):
        return f"  {C.GREY}{key:<{width}}{C.RESET} {color}{val}{C.RESET}"

    def spark(values, width=20):
        """Tiny in-line sparkline."""
        if not values or len(values) < 2:
            return ""
        mn, mx = min(values), max(values)
        rng = mx - mn if mx != mn else 1
        blocks = " ▁▂▃▄▅▆▇█"
        return "".join(blocks[min(8, int((v - mn) / rng * 8))] for v in values)

    # ═══════ HEADER ═══════
    print("\n" + "═" * W)
    title = f"  {company}  ({ticker})"
    pe_str = f"PE {a['pe']:.1f}" if a["pe"] > 0 else "PE N/A"
    grade = a["quality"]["grade"]
    grade_color = {
        "A+": C.GREEN, "A": C.GREEN, "B+": C.YELLOW,
        "B": C.YELLOW, "C": C.RED, "D": C.RED,
    }.get(grade, C.WHITE)
    print(f"{C.BOLD}{C.CYAN}{title}{C.RESET}")
    print(f"  {C.GREY}{cons} · {data.get('fetched_at', '')[:10]}{C.RESET}")
    if data.get("about"):
        # Truncate about to 2 lines
        about = data["about"]
        if len(about) > 140:
            about = about[:137] + "..."
        print(f"  {C.DIM}{about}{C.RESET}")
    print("═" * W)

    # ═══════ SCORECARD ═══════
    print(header("SCORECARD", C.BG_BLU))
    val_score = a["valuation"]["score"]
    val_verdict = a["valuation"]["verdict"]
    qual_score = a["quality"]["score"]

    val_color = C.GREEN if val_score >= 60 else C.YELLOW if val_score >= 40 else C.RED
    qual_color = C.GREEN if qual_score >= 60 else C.YELLOW if qual_score >= 40 else C.RED

    print(f"  {C.BOLD}Quality Grade:{C.RESET}  {grade_color}{C.BOLD} {grade} {C.RESET}  "
          f"({qual_color}{qual_score}/100{C.RESET})    "
          f"{C.BOLD}Valuation:{C.RESET}  {val_color}{val_verdict}{C.RESET}  "
          f"({val_color}{val_score}/100{C.RESET})")

    # ═══════ KEY METRICS ═══════
    print(header("KEY METRICS"))
    col1 = [
        ("Market Cap", fmt_cr(a["market_cap"]), C.WHITE),
        ("Current Price", f"₹{a['current_price']:,.0f}", C.WHITE),
        ("52W High / Low", f"₹{a['high_52w']:,.0f} / ₹{a['low_52w']:,.0f}", C.WHITE),
        ("Stock P/E", f"{a['pe']:.1f}" if a["pe"] > 0 else "N/A", C.CYAN),
        ("P/B Ratio", f"{a['pb']:.2f}" if a["pb"] > 0 else "N/A", C.CYAN),
    ]
    col2 = [
        ("Book Value", f"₹{a['book_value']:,.0f}" if a["book_value"] > 0 else "N/A", C.WHITE),
        ("Dividend Yield", f"{a['div_yield']:.2f}%", C.GREEN if a["div_yield"] > 1 else C.WHITE),
        ("Face Value", f"₹{a['face_value']:.0f}", C.GREY),
        ("ROCE", f"{a['roce']:.1f}%", C.GREEN if a["roce"] > 15 else C.YELLOW if a["roce"] > 10 else C.RED),
        ("ROE", f"{a['roe']:.1f}%", C.GREEN if a["roe"] > 15 else C.YELLOW if a["roe"] > 10 else C.RED),
    ]

    for (k1, v1, c1), (k2, v2, c2) in zip(col1, col2):
        left = f"  {C.GREY}{k1:<20}{C.RESET} {c1}{v1:<18}{C.RESET}"
        right = f" {C.GREY}{k2:<18}{C.RESET} {c2}{v2}{C.RESET}"
        print(left + right)

    # 52W position bar
    tech = a["technical"]
    pos = tech["pos_in_52w_range"]
    bar_len = 30
    filled = int(pos * bar_len)
    bar = f"{'█' * filled}{'░' * (bar_len - filled)}"
    bar_color = C.GREEN if pos > 0.6 else C.RED if pos < 0.3 else C.YELLOW
    print(f"\n  {C.GREY}52W Range:{C.RESET}  {C.RED}Low{C.RESET} {bar_color}{bar}{C.RESET} {C.GREEN}High{C.RESET}  ({pos * 100:.0f}%)")

    if brief:
        # Brief mode — just show flags and exit
        _print_flags(a["flags"])
        print()
        return

    # ═══════ P&L SUMMARY ═══════
    pl = a["pl_analysis"]
    if pl:
        is_bank = pl.get("is_bank", False)
        margin_label = "Financing Margin" if is_bank else "OPM"
        print(header("PROFIT & LOSS"))
        print(kv("Revenue (Latest FY)", fmt_cr(pl["sales_latest"]), C.WHITE))
        print(kv("Net Profit", fmt_cr(pl["net_profit_latest"]),
                  C.GREEN if pl["net_profit_latest"] > 0 else C.RED))
        print(kv("EPS", f"₹{pl['eps_latest']:.1f}", C.WHITE))
        print(kv(margin_label, f"{pl['opm_latest']:.0f}%",
                  C.GREEN if pl["opm_latest"] > 20 else C.YELLOW if pl["opm_latest"] > 10 else C.RED))
        print(kv("NPM", f"{pl['npm_latest']:.1f}%",
                  C.GREEN if pl["npm_latest"] > 15 else C.YELLOW if pl["npm_latest"] > 8 else C.RED))
        print(kv("Margin Trend", pl["margin_trend"].title(),
                  C.GREEN if pl["margin_trend"] == "expanding" else
                  C.RED if pl["margin_trend"] == "contracting" else C.YELLOW))

        # Revenue sparkline
        if pl.get("sales_history"):
            sp = spark(pl["sales_history"])
            print(f"\n  {C.GREY}Revenue Trend:{C.RESET}  {C.CYAN}{sp}{C.RESET}  "
                  f"({len(pl['sales_history'])}Y)")
        if pl.get("profit_history"):
            sp = spark(pl["profit_history"])
            color = C.GREEN if pl["profit_history"][-1] > pl["profit_history"][0] else C.RED
            print(f"  {C.GREY}Profit Trend: {C.RESET}  {color}{sp}{C.RESET}  "
                  f"({len(pl['profit_history'])}Y)")

    # ═══════ GROWTH ═══════
    growth = a["growth"]
    if growth:
        print(header("GROWTH"))
        for category, vals in growth.items():
            short_cat = (category.replace("Compounded ", "")
                                 .replace("Stock Price CAGR", "Stock CAGR")
                                 .replace("Return on Equity", "ROE Trend"))
            parts = []
            for period, val in vals.items():
                color = C.GREEN if val > 10 else C.YELLOW if val > 0 else C.RED
                parts.append(f"{period}: {color}{val:+.0f}%{C.RESET}")
            print(f"  {C.GREY}{short_cat:<20}{C.RESET} {' · '.join(parts)}")

    # ═══════ QUARTERLY TREND ═══════
    qtr = a["qtr_analysis"]
    if qtr and qtr.get("periods"):
        print(header("QUARTERLY TREND"))
        print(kv("Latest Q Revenue", fmt_cr(qtr["sales_latest_q"]), C.WHITE))
        print(kv("Latest Q Profit", fmt_cr(qtr["profit_latest_q"]),
                  C.GREEN if qtr["profit_latest_q"] > 0 else C.RED))
        print(kv("Revenue YoY", fmt_pct(qtr["sales_yoy_q"]),
                  C.GREEN if qtr["sales_yoy_q"] > 0 else C.RED))
        print(kv("Profit YoY", fmt_pct(qtr["profit_yoy_q"]),
                  C.GREEN if qtr["profit_yoy_q"] > 0 else C.RED))
        print(kv("OPM Latest Q", f"{qtr['opm_latest_q']:.0f}%",
                  C.GREEN if qtr["opm_latest_q"] > 20 else C.YELLOW))
        print(kv("Margin Improving?",
                  "Yes ✓" if qtr["improving_margins"] else "No ✗",
                  C.GREEN if qtr["improving_margins"] else C.RED))

        # Mini quarterly table
        if qtr.get("sales_history") and qtr.get("profit_history"):
            periods = qtr["periods"]
            sales_q = qtr["sales_history"]
            profit_q = qtr["profit_history"]
            n = min(len(periods), len(sales_q), len(profit_q), 6)
            if n >= 2:
                print(f"\n  {C.GREY}{'Quarter':<12}", end="")
                for p in periods[-n:]:
                    print(f"{p:>12}", end="")
                print(C.RESET)
                print(f"  {'Revenue':<12}", end="")
                for v in sales_q[-n:]:
                    print(f"{C.CYAN}{fmt_indian(v):>12}{C.RESET}", end="")
                print()
                print(f"  {'Net Profit':<12}", end="")
                for v in profit_q[-n:]:
                    color = C.GREEN if v > 0 else C.RED
                    print(f"{color}{fmt_indian(v):>12}{C.RESET}", end="")
                print()

    # ═══════ BALANCE SHEET ═══════
    bsa = a["bs_analysis"]
    if bsa:
        print(header("BALANCE SHEET"))
        print(kv("Shareholder Equity", fmt_cr(bsa["shareholder_equity"]), C.WHITE))
        print(kv("Total Borrowings", fmt_cr(bsa["borrowings"]),
                  C.GREEN if bsa["de_ratio"] < 0.5 else C.RED))
        print(kv("Total Assets", fmt_cr(bsa["total_assets"]), C.WHITE))
        de_color = C.GREEN if bsa["de_ratio"] < 0.5 else C.YELLOW if bsa["de_ratio"] < 1 else C.RED
        print(kv("Debt/Equity Ratio", f"{bsa['de_ratio']:.2f}", de_color))
        print(kv("Debt Trend", bsa["debt_trend"].title(),
                  C.GREEN if bsa["debt_trend"] == "decreasing" else
                  C.RED if bsa["debt_trend"] == "increasing" else C.YELLOW))
        if bsa.get("cwip_latest", 0) > 0:
            print(kv("CWIP", fmt_cr(bsa["cwip_latest"]), C.GREY))

    # ═══════ CASH FLOW ═══════
    cfa = a["cf_analysis"]
    if cfa:
        print(header("CASH FLOW"))
        print(kv("CFO (Latest)", fmt_cr(cfa["cfo_latest"]),
                  C.GREEN if cfa["cfo_latest"] > 0 else C.RED))
        print(kv("CFI (Latest)", fmt_cr(cfa["cfi_latest"]),
                  C.RED if cfa["cfi_latest"] < 0 else C.GREEN))
        print(kv("CFF (Latest)", fmt_cr(cfa["cff_latest"]), C.WHITE))
        print(kv("Free Cash Flow", fmt_cr(cfa["fcf_latest"]),
                  C.GREEN if cfa["fcf_latest"] > 0 else C.RED))
        print(kv("CFO Positive Years",
                  f"{int(cfa['cfo_consistency'] * cfa['total_years'])}/{cfa['total_years']}",
                  C.GREEN if cfa["cfo_consistency"] >= 1.0 else C.YELLOW))
        if cfa.get("cfo_history"):
            sp = spark(cfa["cfo_history"])
            print(f"\n  {C.GREY}CFO Trend:    {C.RESET}  {C.GREEN}{sp}{C.RESET}")
        if cfa.get("fcf_history"):
            sp = spark(cfa["fcf_history"])
            color = C.GREEN if cfa["fcf_latest"] > 0 else C.RED
            print(f"  {C.GREY}FCF Trend:    {C.RESET}  {color}{sp}{C.RESET}")

    # ═══════ SHAREHOLDING ═══════
    sha = a["sh_analysis"]
    if sha and sha.get("promoter_latest", 0) > 0:
        print(header("SHAREHOLDING"))
        print(kv("Promoters", f"{sha['promoter_latest']:.1f}%  ({sha['promoter_trend']})",
                  C.GREEN if sha["promoter_trend"] != "decreasing" else C.RED))
        print(kv("FIIs", f"{sha['fii_latest']:.1f}%  ({sha['fii_trend']})",
                  C.GREEN if sha["fii_trend"] == "increasing" else
                  C.RED if sha["fii_trend"] == "decreasing" else C.WHITE))
        print(kv("DIIs", f"{sha['dii_latest']:.1f}%  ({sha['dii_trend']})",
                  C.GREEN if sha["dii_trend"] == "increasing" else C.WHITE))
        print(kv("Public", f"{sha['public_latest']:.1f}%", C.GREY))
        if sha.get("n_shareholders_latest", 0) > 0:
            print(kv("Shareholders", f"{sha['n_shareholders_latest']:,.0f}", C.GREY))

        # Shareholding bar
        p = sha["promoter_latest"]
        f_ = sha["fii_latest"]
        d = sha["dii_latest"]
        pub = sha["public_latest"]
        total = p + f_ + d + pub
        bar_w = 50
        if total > 0:
            bp = int(p / total * bar_w)
            bf = int(f_ / total * bar_w)
            bd = int(d / total * bar_w)
            bpub = bar_w - bp - bf - bd
            print(f"\n  {C.GREEN}{'█' * bp}{C.BLUE}{'█' * bf}{C.CYAN}{'█' * bd}{C.GREY}{'█' * bpub}{C.RESET}")
            print(f"  {C.GREEN}Promoter{C.RESET}  {C.BLUE}FII{C.RESET}  {C.CYAN}DII{C.RESET}  {C.GREY}Public{C.RESET}")

    # ═══════ SEGMENTS ═══════
    segments = data.get("segments", [])
    if segments:
        print(header("BUSINESS SEGMENTS"))
        for i, seg in enumerate(segments):
            print(f"  {C.CYAN}●{C.RESET} {seg}")

    # ═══════ KEY RATIOS ═══════
    ratios_sec = data.get("ratios", {})
    if ratios_sec.get("rows"):
        print(header("KEY RATIOS"))
        ratio_rows = ratios_sec["rows"]
        periods_r = ratios_sec.get("periods", [])
        for label in ["Debtor Days", "Inventory Days", "Days Payable",
                       "Cash Conversion Cycle", "Working Capital Days", "ROCE %"]:
            if label in ratio_rows:
                vals = ratio_rows[label][-4:]
                periods_show = periods_r[-4:]
                parts = []
                for p, v in zip(periods_show, vals):
                    parts.append(f"{p[-4:]}: {v}")
                print(f"  {C.GREY}{label:<28}{C.RESET} {' · '.join(parts)}")

    # ═══════ VALUATION SIGNALS ═══════
    print(header("VALUATION SIGNALS"))
    for signal, sentiment in a["valuation"]["signals"]:
        icon = {"bullish": f"{C.GREEN}▲", "bearish": f"{C.RED}▼",
                "neutral": f"{C.YELLOW}●", "info": f"{C.GREY}ℹ"}.get(sentiment, "●")
        print(f"  {icon} {signal}{C.RESET}")

    # ═══════ TECHNICAL SIGNALS ═══════
    print(header("TECHNICAL SIGNALS"))
    for signal, sentiment in a["technical"]["signals"]:
        icon = {"bullish": f"{C.GREEN}▲", "bearish": f"{C.RED}▼",
                "caution": f"{C.YELLOW}⚠", "neutral": f"{C.YELLOW}●",
                "info": f"{C.GREY}ℹ"}.get(sentiment, "●")
        print(f"  {icon} {signal}{C.RESET}")

    # ═══════ FLAGS ═══════
    _print_flags(a["flags"])

    # ═══════ PROS / CONS ═══════
    pc = data.get("pros_cons", {})
    if pc.get("pros") or pc.get("cons"):
        print(header("SCREENER PROS & CONS"))
        for p in pc.get("pros", []):
            print(f"  {C.GREEN}✓{C.RESET} {p}")
        for c_item in pc.get("cons", []):
            print(f"  {C.RED}✗{C.RESET} {c_item}")

    # ═══════ PEERS ═══════
    peers = data.get("peers", [])
    if peers and len(peers) >= 2:
        print(header("PEER COMPARISON"))
        # Get headers
        if peers:
            headers = [k for k in peers[0].keys() if not k.startswith("_")]
            # Limit columns
            show_cols = headers[:8]
            # Header row
            row_str = f"  {C.GREY}"
            for h in show_cols:
                row_str += f"{h[:12]:>13}"
            print(row_str + C.RESET)
            print(f"  {C.GREY}{'─' * (13 * len(show_cols))}{C.RESET}")
            for peer in peers[:8]:
                is_self = peer.get("_ticker", "").upper() == data["ticker"]
                color = C.CYAN + C.BOLD if is_self else C.WHITE
                row_str = f"  {color}"
                for h in show_cols:
                    val = peer.get(h, "")
                    val_str = val[:12] if isinstance(val, str) else str(val)[:12]
                    row_str += f"{val_str:>13}"
                print(row_str + C.RESET)

    # ═══════ DOCUMENTS ═══════
    docs = data.get("documents", {})
    if docs.get("concalls") or docs.get("annual_reports"):
        print(header("RECENT DOCUMENTS"))
        for doc in docs.get("concalls", [])[:3]:
            print(f"  {C.BLUE}📞{C.RESET} {doc['text'][:65]}")
        for doc in docs.get("annual_reports", [])[:3]:
            print(f"  {C.CYAN}📄{C.RESET} {doc['text'][:65]}")

    # ═══════ FOOTER ═══════
    print(f"\n{'═' * W}")
    print(f"  {C.GREY}Source: screener.in · {data['url']}{C.RESET}")
    print(f"  {C.GREY}Generated: {datetime.now().strftime('%d %b %Y %H:%M')}{C.RESET}")
    print(f"{'═' * W}\n")


def _print_flags(flags: dict):
    """Print green/amber/red flags section."""
    green = flags.get("green", [])
    red = flags.get("red", [])
    amber = flags.get("amber", [])

    if green or red or amber:
        W = 74
        print(f"\n{C.BG_GRN}{C.WHITE}{C.BOLD}{' GREEN FLAGS '.center(W)}{C.RESET}")
        for f in green:
            print(f"  {C.GREEN}✓{C.RESET} {f}")
        if not green:
            print(f"  {C.GREY}None{C.RESET}")

        if amber:
            print(f"\n{C.BG_YLW}{C.WHITE}{C.BOLD}{' AMBER FLAGS '.center(W)}{C.RESET}")
            for f in amber:
                print(f"  {C.YELLOW}⚠{C.RESET} {f}")

        print(f"\n{C.BG_RED}{C.WHITE}{C.BOLD}{' RED FLAGS '.center(W)}{C.RESET}")
        for f in red:
            print(f"  {C.RED}✗{C.RESET} {f}")
        if not red:
            print(f"  {C.GREY}None{C.RESET}")


# ═══════════════════════════════════════════════════════════════════════════════
# HTML REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def generate_html_report(data: dict, analysis: dict, sankey_html: str = "") -> str:
    """Generate a rich HTML screener report."""
    ticker = data["ticker"]
    company = data["company_name"]
    cons = "Consolidated" if data["is_consolidated"] else "Standalone"
    a = analysis

    # Utility CSS classes
    def sentiment_class(val, thresholds=(10, 0)):
        """Return CSS class based on value."""
        if val > thresholds[0]:
            return "positive"
        elif val < thresholds[1]:
            return "negative"
        return "neutral"

    def flag_html(flags):
        html = ""
        for f in flags.get("green", []):
            html += f'<div class="flag green">✓ {f}</div>\n'
        for f in flags.get("amber", []):
            html += f'<div class="flag amber">⚠ {f}</div>\n'
        for f in flags.get("red", []):
            html += f'<div class="flag red">✗ {f}</div>\n'
        return html

    grade = a["quality"]["grade"]
    grade_color = {"A+": "#16A34A", "A": "#22C55E", "B+": "#EAB308",
                   "B": "#F59E0B", "C": "#EF4444", "D": "#DC2626"}.get(grade, "#666")

    val_score = a["valuation"]["score"]
    val_color = "#16A34A" if val_score >= 60 else "#EAB308" if val_score >= 40 else "#EF4444"

    # Growth table rows
    growth_rows = ""
    for category, vals in a.get("growth", {}).items():
        short_cat = (category.replace("Compounded ", "")
                             .replace("Stock Price CAGR", "Stock CAGR")
                             .replace("Return on Equity", "ROE Trend"))
        cells = ""
        for period, val in vals.items():
            color = "#16A34A" if val > 10 else "#EAB308" if val > 0 else "#EF4444"
            cells += f'<td style="color:{color}; font-weight:600">{val:+.0f}%</td>'
        growth_rows += f'<tr><td style="color:#6B7280">{short_cat}</td>{cells}</tr>\n'

    # Growth headers
    growth_headers = ""
    first_cat = next(iter(a.get("growth", {}).values()), {})
    for period in first_cat:
        growth_headers += f'<th>{period}</th>'

    # Shareholding data for chart
    sha = a.get("sh_analysis", {})
    sh_data = json.dumps({
        "promoter": sha.get("promoter_latest", 0),
        "fii": sha.get("fii_latest", 0),
        "dii": sha.get("dii_latest", 0),
        "public": sha.get("public_latest", 0),
    })

    # Quarterly mini-table
    qtr = a.get("qtr_analysis", {})
    qtr_rows = ""
    if qtr.get("periods") and qtr.get("sales_history") and qtr.get("profit_history"):
        n = min(len(qtr["periods"]), len(qtr["sales_history"]), len(qtr["profit_history"]), 6)
        periods = qtr["periods"][-n:]
        sales_q = qtr["sales_history"][-n:]
        profit_q = qtr["profit_history"][-n:]

        qtr_header = "<tr><th>Quarter</th>" + "".join(f"<th>{p}</th>" for p in periods) + "</tr>"
        qtr_rev = "<tr><td>Revenue</td>" + "".join(f"<td>{fmt_indian(v)}</td>" for v in sales_q) + "</tr>"
        qtr_pat = "<tr><td>Net Profit</td>" + "".join(
            f'<td style="color:{"#16A34A" if v > 0 else "#EF4444"}">{fmt_indian(v)}</td>'
            for v in profit_q) + "</tr>"
        qtr_rows = f"{qtr_header}\n{qtr_rev}\n{qtr_pat}"

    # Valuation signals
    val_signals = ""
    for signal, sentiment in a["valuation"]["signals"]:
        icon = {"bullish": "▲", "bearish": "▼", "neutral": "●", "info": "ℹ"}.get(sentiment, "●")
        color = {"bullish": "#16A34A", "bearish": "#EF4444", "neutral": "#EAB308", "info": "#6B7280"}.get(sentiment)
        val_signals += f'<div style="color:{color}; margin:4px 0">{icon} {signal}</div>\n'

    # Technical signals
    tech_signals = ""
    for signal, sentiment in a["technical"]["signals"]:
        icon = {"bullish": "▲", "bearish": "▼", "caution": "⚠", "neutral": "●", "info": "ℹ"}.get(sentiment, "●")
        color = {"bullish": "#16A34A", "bearish": "#EF4444", "caution": "#EAB308",
                 "neutral": "#EAB308", "info": "#6B7280"}.get(sentiment)
        tech_signals += f'<div style="color:{color}; margin:4px 0">{icon} {signal}</div>\n'

    # Peers table
    peers_html = ""
    peers = data.get("peers", [])
    if peers and len(peers) >= 2:
        headers = [k for k in peers[0].keys() if not k.startswith("_")][:8]
        peers_header = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
        peers_body = ""
        for peer in peers[:10]:
            is_self = peer.get("_ticker", "").upper() == ticker
            style = 'style="background:#EBF5FF; font-weight:600"' if is_self else ""
            cells = "".join(f"<td>{peer.get(h, '')}</td>" for h in headers)
            peers_body += f"<tr {style}>{cells}</tr>\n"
        peers_html = f"""
        <div class="section">
            <h2>Peer Comparison</h2>
            <table class="data-table">{peers_header}{peers_body}</table>
        </div>"""

    # Segments
    segments_html = ""
    segments = data.get("segments", [])
    if segments:
        seg_items = "".join(f'<span class="segment-tag">{s}</span>' for s in segments)
        segments_html = f'<div class="section"><h2>Business Segments</h2><div class="segments">{seg_items}</div></div>'

    # Documents
    docs_html = ""
    docs = data.get("documents", {})
    if docs.get("concalls") or docs.get("annual_reports"):
        items = ""
        for doc in docs.get("concalls", [])[:3]:
            items += f'<div class="doc-item">📞 <a href="{doc["url"]}" target="_blank">{doc["text"][:80]}</a></div>\n'
        for doc in docs.get("annual_reports", [])[:3]:
            items += f'<div class="doc-item">📄 <a href="{doc["url"]}" target="_blank">{doc["text"][:80]}</a></div>\n'
        docs_html = f'<div class="section"><h2>Recent Documents</h2>{items}</div>'

    # Pros/Cons
    pc = data.get("pros_cons", {})
    pc_html = ""
    if pc.get("pros") or pc.get("cons"):
        pros = "".join(f'<div class="pro">✓ {p}</div>' for p in pc.get("pros", []))
        cons = "".join(f'<div class="con">✗ {c}</div>' for c in pc.get("cons", []))
        pc_html = f'<div class="section"><h2>Screener Pros & Cons</h2>{pros}{cons}</div>'

    # 52W bar
    pos_52w = a["technical"]["pos_in_52w_range"]

    # About
    about_html = ""
    if data.get("about"):
        about_html = f'<p class="about">{data["about"]}</p>'

    pl = a.get("pl_analysis", {})
    bsa = a.get("bs_analysis", {})
    cfa = a.get("cf_analysis", {})

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{company} ({ticker}) — Company Screener</title>
<style>
:root {{
    --green: #16A34A; --red: #EF4444; --amber: #EAB308;
    --blue: #2563EB; --grey: #6B7280; --light: #F9FAFB;
    --border: #E5E7EB;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Segoe UI', -apple-system, sans-serif; background:#F3F4F6; color:#1F2937; }}
.container {{ max-width:1100px; margin:0 auto; padding:20px; }}
.header {{ background:linear-gradient(135deg, #1E3A5F, #2563EB); color:white; padding:32px; border-radius:16px; margin-bottom:20px; }}
.header h1 {{ font-size:28px; margin-bottom:4px; }}
.header .sub {{ font-size:14px; opacity:0.8; }}
.about {{ font-size:13px; color:#9CA3AF; margin-top:10px; line-height:1.5; }}
.scorecard {{ display:flex; gap:16px; margin-bottom:20px; }}
.score-card {{ flex:1; background:white; border-radius:12px; padding:20px; text-align:center; box-shadow:0 1px 3px rgba(0,0,0,0.1); }}
.score-card .label {{ font-size:12px; color:var(--grey); text-transform:uppercase; letter-spacing:1px; }}
.score-card .value {{ font-size:36px; font-weight:800; margin:8px 0; }}
.score-card .desc {{ font-size:13px; color:var(--grey); }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:20px; }}
@media (max-width:768px) {{ .grid {{ grid-template-columns:1fr; }} }}
.section {{ background:white; border-radius:12px; padding:20px; box-shadow:0 1px 3px rgba(0,0,0,0.1); margin-bottom:16px; }}
.section h2 {{ font-size:16px; color:#1E3A5F; margin-bottom:12px; border-bottom:2px solid var(--border); padding-bottom:8px; }}
.metric {{ display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid #F3F4F6; }}
.metric .key {{ color:var(--grey); font-size:13px; }}
.metric .val {{ font-weight:600; font-size:14px; }}
.positive {{ color:var(--green); }}
.negative {{ color:var(--red); }}
.neutral {{ color:var(--amber); }}
.flag {{ padding:6px 12px; margin:4px 0; border-radius:6px; font-size:13px; }}
.flag.green {{ background:#F0FDF4; color:#166534; border-left:3px solid var(--green); }}
.flag.red {{ background:#FEF2F2; color:#991B1B; border-left:3px solid var(--red); }}
.flag.amber {{ background:#FFFBEB; color:#92400E; border-left:3px solid var(--amber); }}
.data-table {{ width:100%; border-collapse:collapse; font-size:13px; }}
.data-table th {{ text-align:right; padding:6px 8px; background:#F9FAFB; color:var(--grey); font-size:12px; border-bottom:2px solid var(--border); }}
.data-table td {{ text-align:right; padding:6px 8px; border-bottom:1px solid #F3F4F6; }}
.data-table td:first-child, .data-table th:first-child {{ text-align:left; }}
.bar-52w {{ height:8px; background:#E5E7EB; border-radius:4px; position:relative; margin:10px 0; }}
.bar-52w .fill {{ height:100%; border-radius:4px; }}
.bar-52w .labels {{ display:flex; justify-content:space-between; font-size:11px; color:var(--grey); }}
.segment-tag {{ display:inline-block; background:#EBF5FF; color:#2563EB; padding:4px 12px; border-radius:20px; margin:4px; font-size:13px; }}
.segments {{ display:flex; flex-wrap:wrap; gap:4px; }}
.doc-item {{ padding:4px 0; font-size:13px; }}
.doc-item a {{ color:var(--blue); text-decoration:none; }}
.doc-item a:hover {{ text-decoration:underline; }}
.pro {{ color:#166534; padding:3px 0; font-size:13px; }}
.con {{ color:#991B1B; padding:3px 0; font-size:13px; }}
.footer {{ text-align:center; color:var(--grey); font-size:12px; padding:20px; }}
.footer a {{ color:var(--blue); text-decoration:none; }}
.sh-bar {{ display:flex; height:24px; border-radius:6px; overflow:hidden; margin:10px 0; }}
.sh-bar > div {{ display:flex; align-items:center; justify-content:center; font-size:11px; color:white; font-weight:600; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>{company}</h1>
    <div class="sub">{ticker} · {cons} · {data.get('fetched_at', '')[:10]}</div>
    {about_html}
</div>

<div class="scorecard">
    <div class="score-card">
        <div class="label">Quality Grade</div>
        <div class="value" style="color:{grade_color}">{grade}</div>
        <div class="desc">Score: {a['quality']['score']}/100</div>
    </div>
    <div class="score-card">
        <div class="label">Valuation</div>
        <div class="value" style="color:{val_color}; font-size:24px">{a['valuation']['verdict']}</div>
        <div class="desc">Score: {val_score}/100</div>
    </div>
    <div class="score-card">
        <div class="label">Market Cap</div>
        <div class="value" style="font-size:22px; color:#1E3A5F">{fmt_cr(a['market_cap'])}</div>
        <div class="desc">Price: ₹{a['current_price']:,.0f}</div>
    </div>
    <div class="score-card">
        <div class="label">P/E Ratio</div>
        <div class="value" style="font-size:28px">{a['pe']:.1f}</div>
        <div class="desc">P/B: {a['pb']:.2f} · Div: {a['div_yield']:.1f}%</div>
    </div>
</div>

<div class="grid">
<div class="section">
    <h2>Key Metrics</h2>
    <div class="metric"><span class="key">Current Price</span><span class="val">₹{a['current_price']:,.0f}</span></div>
    <div class="metric"><span class="key">52W High / Low</span><span class="val">₹{a['high_52w']:,.0f} / ₹{a['low_52w']:,.0f}</span></div>
    <div class="metric"><span class="key">Book Value</span><span class="val">₹{a['book_value']:,.0f}</span></div>
    <div class="metric"><span class="key">ROCE</span><span class="val {'positive' if a['roce']>15 else 'negative' if a['roce']<10 else ''}">{a['roce']:.1f}%</span></div>
    <div class="metric"><span class="key">ROE</span><span class="val {'positive' if a['roe']>15 else 'negative' if a['roe']<10 else ''}">{a['roe']:.1f}%</span></div>
    <div class="metric"><span class="key">Dividend Yield</span><span class="val">{a['div_yield']:.2f}%</span></div>
    <div class="bar-52w">
        <div class="fill" style="width:{pos_52w*100:.0f}%; background:{'var(--green)' if pos_52w > 0.6 else 'var(--red)' if pos_52w < 0.3 else 'var(--amber)'}"></div>
    </div>
    <div class="bar-52w labels"><span>₹{a['low_52w']:,.0f}</span><span>₹{a['current_price']:,.0f}</span><span>₹{a['high_52w']:,.0f}</span></div>
</div>

<div class="section">
    <h2>Profit & Loss</h2>
    <div class="metric"><span class="key">Revenue</span><span class="val">{fmt_cr(pl.get('sales_latest', 0))}</span></div>
    <div class="metric"><span class="key">Net Profit</span><span class="val {'positive' if pl.get('net_profit_latest',0) > 0 else 'negative'}">{fmt_cr(pl.get('net_profit_latest', 0))}</span></div>
    <div class="metric"><span class="key">EPS</span><span class="val">₹{pl.get('eps_latest', 0):.1f}</span></div>
    <div class="metric"><span class="key">Operating Margin</span><span class="val">{pl.get('opm_latest', 0):.0f}%</span></div>
    <div class="metric"><span class="key">Net Margin</span><span class="val">{pl.get('npm_latest', 0):.1f}%</span></div>
    <div class="metric"><span class="key">Margin Trend</span><span class="val {'positive' if pl.get('margin_trend')=='expanding' else 'negative' if pl.get('margin_trend')=='contracting' else ''}">{pl.get('margin_trend', 'N/A').title()}</span></div>
    <div class="metric"><span class="key">3Y Rev CAGR</span><span class="val">{pl.get('sales_cagr_3y', 0):+.1f}%</span></div>
    <div class="metric"><span class="key">3Y Profit CAGR</span><span class="val">{pl.get('profit_cagr_3y', 0):+.1f}%</span></div>
</div>

<div class="section">
    <h2>Balance Sheet</h2>
    <div class="metric"><span class="key">Shareholder Equity</span><span class="val">{fmt_cr(bsa.get('shareholder_equity', 0))}</span></div>
    <div class="metric"><span class="key">Total Borrowings</span><span class="val">{fmt_cr(bsa.get('borrowings', 0))}</span></div>
    <div class="metric"><span class="key">Debt / Equity</span><span class="val {'positive' if bsa.get('de_ratio',0)<0.5 else 'negative' if bsa.get('de_ratio',0)>1.5 else ''}">{bsa.get('de_ratio', 0):.2f}</span></div>
    <div class="metric"><span class="key">Total Assets</span><span class="val">{fmt_cr(bsa.get('total_assets', 0))}</span></div>
    <div class="metric"><span class="key">Debt Trend</span><span class="val">{bsa.get('debt_trend', 'N/A').title()}</span></div>
</div>

<div class="section">
    <h2>Cash Flow</h2>
    <div class="metric"><span class="key">Cash from Operations</span><span class="val {'positive' if cfa.get('cfo_latest',0)>0 else 'negative'}">{fmt_cr(cfa.get('cfo_latest', 0))}</span></div>
    <div class="metric"><span class="key">Free Cash Flow</span><span class="val {'positive' if cfa.get('fcf_latest',0)>0 else 'negative'}">{fmt_cr(cfa.get('fcf_latest', 0))}</span></div>
    <div class="metric"><span class="key">CFO Consistency</span><span class="val">{int(cfa.get('cfo_consistency',0)*cfa.get('total_years',0))}/{cfa.get('total_years', 0)} years positive</span></div>
    <div class="metric"><span class="key">FCF Positive Years</span><span class="val">{cfa.get('fcf_positive_years', 0)}/{cfa.get('total_years', 0)}</span></div>
</div>
</div>

<!-- Shareholding -->
{"" if not sha or sha.get("promoter_latest", 0) == 0 else f'''
<div class="section">
    <h2>Shareholding Pattern</h2>
    <div class="sh-bar">
        <div style="width:{sha.get("promoter_latest",0)}%; background:#16A34A">P {sha.get("promoter_latest",0):.0f}%</div>
        <div style="width:{sha.get("fii_latest",0)}%; background:#2563EB">FII {sha.get("fii_latest",0):.0f}%</div>
        <div style="width:{sha.get("dii_latest",0)}%; background:#0891B2">DII {sha.get("dii_latest",0):.0f}%</div>
        <div style="width:{sha.get("public_latest",0)}%; background:#9CA3AF">Pub {sha.get("public_latest",0):.0f}%</div>
    </div>
    <div class="metric"><span class="key">Promoter Trend</span><span class="val">{sha.get("promoter_trend", "N/A")}</span></div>
    <div class="metric"><span class="key">FII Trend</span><span class="val {'positive' if sha.get("fii_trend")=="increasing" else 'negative' if sha.get("fii_trend")=="decreasing" else ""}">{sha.get("fii_trend", "N/A")}</span></div>
    <div class="metric"><span class="key">DII Trend</span><span class="val">{sha.get("dii_trend", "N/A")}</span></div>
    <div class="metric"><span class="key">Shareholders</span><span class="val">{sha.get("n_shareholders_latest", 0):,.0f}</span></div>
</div>
'''}

<!-- Growth Table -->
{"" if not growth_rows else f'''
<div class="section">
    <h2>Compounded Growth</h2>
    <table class="data-table">
    <tr><th>Metric</th>{growth_headers}</tr>
    {growth_rows}
    </table>
</div>
'''}

<!-- Quarterly -->
{"" if not qtr_rows else f'''
<div class="section">
    <h2>Quarterly Trend</h2>
    <table class="data-table">
    {qtr_rows}
    </table>
</div>
'''}

<!-- Flags -->
<div class="grid">
<div class="section">
    <h2>Valuation Signals</h2>
    {val_signals}
</div>
<div class="section">
    <h2>Technical Signals</h2>
    {tech_signals}
</div>
</div>

<div class="section">
    <h2>Flags</h2>
    {flag_html(a['flags'])}
</div>

{f'''
<div class="section" style="margin-bottom:16px">
    <h2>Income Flow (Sankey)</h2>
    <div style="width:100%; overflow-x:auto">{sankey_html}</div>
</div>
''' if sankey_html else ''}

{pc_html}
{segments_html}
{peers_html}
{docs_html}

<div class="footer">
    <p>Source: <a href="{data['url']}">screener.in/{ticker}</a> · Generated {datetime.now().strftime('%d %b %Y %H:%M')}</p>
    <p>Company Screener v1.0 · For informational purposes only. Not investment advice.</p>
</div>
</div>
</body>
</html>"""

    return html


# ═══════════════════════════════════════════════════════════════════════════════
# CHARTS & DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_charts(data: dict, ticker: str = "") -> dict:
    """Generate Plotly chart HTML divs for dashboard embedding."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return {}

    charts = {}
    L = dict(
        template="plotly_white", height=300,
        margin=dict(l=50, r=20, t=35, b=50),
        font=dict(family="Segoe UI, sans-serif", size=11),
        legend=dict(orientation="h", y=-0.2, x=0.5, xanchor="center"),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )

    def rv(section, label):
        for key, vals in section.get("rows", {}).items():
            if label.lower() in key.lower():
                return [parse_number(v) for v in vals]
        return []

    # 0. Price charts via yfinance — multiple periods (1y, 3y, 5y, max)
    if ticker:
        try:
            import yfinance as yf
            from plotly.subplots import make_subplots
            # Try NSE first, then BSE
            yf_obj = None
            for suffix in [".NS", ".BO"]:
                yf_obj = yf.Ticker(f"{ticker}{suffix}")
                test = yf_obj.history(period="5d")
                if test is not None and len(test) >= 1:
                    break
            period_map = [
                ("1y", "1 Year"),
                ("3y", "3 Years"),
                ("5y", "5 Years"),
                ("max", "All Time"),
            ]
            for period_key, period_label in period_map:
                try:
                    hist = yf_obj.history(period=period_key)
                except Exception:
                    continue
                if hist is None or len(hist) < 10:
                    continue
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                    vertical_spacing=0.03,
                                    row_heights=[0.75, 0.25])
                fig.add_trace(go.Candlestick(
                    x=hist.index, open=hist["Open"], high=hist["High"],
                    low=hist["Low"], close=hist["Close"], name="Price",
                    increasing_line_color="#10B981", decreasing_line_color="#EF4444",
                ), row=1, col=1)
                if len(hist) >= 20:
                    fig.add_trace(go.Scatter(
                        x=hist.index, y=hist["Close"].rolling(20).mean(),
                        name="MA20", line=dict(color="#3B82F6", width=1.2),
                        mode="lines"), row=1, col=1)
                if len(hist) >= 50:
                    fig.add_trace(go.Scatter(
                        x=hist.index, y=hist["Close"].rolling(50).mean(),
                        name="MA50", line=dict(color="#F59E0B", width=1.2),
                        mode="lines"), row=1, col=1)
                vol_colors = ["#10B981" if c >= o else "#EF4444"
                              for c, o in zip(hist["Close"], hist["Open"])]
                fig.add_trace(go.Bar(
                    x=hist.index, y=hist["Volume"], name="Volume",
                    marker_color=vol_colors, opacity=0.5,
                ), row=2, col=1)
                fig.update_layout(
                    template="plotly_white", height=420,
                    margin=dict(l=50, r=20, t=35, b=30),
                    font=dict(family="Segoe UI, sans-serif", size=11),
                    legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    title_text=f"{ticker} — {period_label}",
                    xaxis_rangeslider_visible=False,
                    yaxis_title="Price (₹)", yaxis2_title="Volume",
                    showlegend=True,
                )
                charts[f"price_{period_key}"] = fig.to_html(full_html=False, include_plotlyjs=False)
        except Exception:
            pass  # yfinance not available or ticker not found

    # 1. Annual Revenue & Net Profit
    pl = data.get("profit_loss", {})
    periods = pl.get("periods", [])
    sales = rv(pl, "sales") or rv(pl, "revenue")
    profit = rv(pl, "net profit")
    if periods and sales:
        n = min(len(periods), len(sales))
        fig = go.Figure()
        fig.add_trace(go.Bar(x=periods[:n], y=sales[:n], name="Revenue", marker_color="#3B82F6"))
        if profit:
            m = min(n, len(profit))
            fig.add_trace(go.Bar(x=periods[:m], y=profit[:m], name="Net Profit", marker_color="#10B981"))
        fig.update_layout(**L, barmode="group", title_text="Revenue & Net Profit (₹ Cr)")
        charts["annual_pl"] = fig.to_html(full_html=False, include_plotlyjs=False)

    # 2. Quarterly Revenue & Profit
    q = data.get("quarterly", {})
    qp = q.get("periods", [])
    qs = rv(q, "sales") or rv(q, "revenue")
    qn = rv(q, "net profit")
    if qp and qs:
        n = min(len(qp), len(qs), 8)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=qp[-n:], y=qs[-n:], name="Revenue", marker_color="#60A5FA"))
        if qn:
            qn8 = qn[-n:]
            fig.add_trace(go.Bar(x=qp[-n:], y=qn8, name="Net Profit",
                                 marker_color=["#10B981" if v >= 0 else "#EF4444" for v in qn8]))
        fig.update_layout(**L, barmode="group", title_text="Quarterly Revenue & Net Profit (₹ Cr)")
        charts["quarterly"] = fig.to_html(full_html=False, include_plotlyjs=False)

    # 3. Margins
    opm = rv(pl, "OPM") or rv(pl, "Financing Margin")
    if periods and opm and sales and profit:
        n = min(len(periods), len(opm), len(sales), len(profit))
        npm = [(profit[i] / sales[i] * 100 if sales[i] > 0 else 0) for i in range(n)]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=periods[:n], y=opm[:n], name="OPM %", mode="lines+markers",
                                 line=dict(color="#3B82F6", width=2.5)))
        fig.add_trace(go.Scatter(x=periods[:n], y=npm, name="NPM %", mode="lines+markers",
                                 line=dict(color="#10B981", width=2.5)))
        fig.update_layout(**L, title_text="Margin Trends (%)", yaxis_title="%")
        charts["margins"] = fig.to_html(full_html=False, include_plotlyjs=False)

    # 4. ROCE / ROE
    ratios = data.get("ratios", {})
    rp = ratios.get("periods", [])
    roce = rv(ratios, "ROCE %")
    roe = rv(ratios, "ROE %")
    if rp and (roce or roe):
        fig = go.Figure()
        if roce:
            nr = min(len(rp), len(roce))
            fig.add_trace(go.Scatter(x=rp[:nr], y=roce[:nr], name="ROCE %", mode="lines+markers",
                                     line=dict(color="#3B82F6", width=2.5)))
        if roe:
            nr = min(len(rp), len(roe))
            fig.add_trace(go.Scatter(x=rp[:nr], y=roe[:nr], name="ROE %", mode="lines+markers",
                                     line=dict(color="#10B981", width=2.5)))
        fig.add_hline(y=15, line_dash="dash", line_color="#9CA3AF", annotation_text="15%")
        fig.update_layout(**L, title_text="Return Ratios (%)", yaxis_title="%")
        charts["returns"] = fig.to_html(full_html=False, include_plotlyjs=False)

    # 5. Shareholding
    sh = data.get("shareholding", {})
    shp = sh.get("periods", [])
    shd = sh.get("data", {})
    if shp and shd:
        fig = go.Figure()
        for color, cat in [("#16A34A", "Promoters"), ("#3B82F6", "FIIs"), ("#0891B2", "DIIs"),
                           ("#9CA3AF", "Public"), ("#8B5CF6", "Government")]:
            vals = [parse_number(v) for v in shd.get(cat, [])]
            if vals:
                ns = min(len(shp), len(vals))
                fig.add_trace(go.Bar(x=shp[:ns], y=vals[:ns], name=cat, marker_color=color))
        fig.update_layout(**L, barmode="stack", title_text="Shareholding Pattern (%)")
        charts["shareholding"] = fig.to_html(full_html=False, include_plotlyjs=False)

    # 6. Cash Flow
    cf = data.get("cash_flow", {})
    cfp = cf.get("periods", [])
    cfo = rv(cf, "cash from operating")
    cfi = rv(cf, "cash from investing")
    cff = rv(cf, "cash from financing")
    if cfp and cfo:
        n = min(len(cfp), len(cfo))
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cfp[:n], y=cfo[:n], name="Operating", marker_color="#10B981"))
        if cfi:
            fig.add_trace(go.Bar(x=cfp[:min(n, len(cfi))], y=cfi[:min(n, len(cfi))],
                                 name="Investing", marker_color="#EF4444"))
        if cff:
            fig.add_trace(go.Bar(x=cfp[:min(n, len(cff))], y=cff[:min(n, len(cff))],
                                 name="Financing", marker_color="#3B82F6"))
        fig.update_layout(**L, barmode="group", title_text="Cash Flow Trend (₹ Cr)")
        charts["cashflow"] = fig.to_html(full_html=False, include_plotlyjs=False)

    return charts


def _defer_plotly(html: str) -> str:
    """Convert inline <script> tags to deferred so they don't auto-execute on page load."""
    return re.sub(r'<script\b[^>]*>', '<script type="text/plotly-deferred">', html)


def _build_stock_pane(data: dict, analysis: dict, charts: dict,
                      sankey_html: str, active: bool = False) -> str:
    """Build the HTML content for one stock's dashboard tab pane."""
    d = data
    a = analysis
    ticker = d["ticker"]
    company = d["company_name"]
    cons_label = "Consolidated" if d["is_consolidated"] else "Standalone"
    display = "block" if active else "none"

    grade = a["quality"]["grade"]
    grade_color = {"A+": "#10B981", "A": "#22C55E", "B+": "#F59E0B",
                   "B": "#F59E0B", "C": "#EF4444", "D": "#DC2626"}.get(grade, "#64748B")
    val_score = a["valuation"]["score"]
    val_color = "#10B981" if val_score >= 60 else "#F59E0B" if val_score >= 40 else "#EF4444"
    pos_52w = a["technical"]["pos_in_52w_range"]
    fill_color = "var(--green)" if pos_52w > 0.6 else "var(--red)" if pos_52w < 0.3 else "var(--amber)"

    # Pre-compute CSS classes
    roce_cls = " positive" if a["roce"] > 15 else (" negative" if a["roce"] < 10 else "")
    roe_cls = " positive" if a["roe"] > 15 else (" negative" if a["roe"] < 10 else "")

    pl = a.get("pl_analysis", {})
    bsa = a.get("bs_analysis", {})
    cfa = a.get("cf_analysis", {})
    np_cls = " positive" if pl.get("net_profit_latest", 0) > 0 else " negative"
    de_cls = " positive" if bsa.get("de_ratio", 0) < 0.5 else (" negative" if bsa.get("de_ratio", 0) > 1.5 else "")
    cfo_cls = " positive" if cfa.get("cfo_latest", 0) > 0 else " negative"
    fcf_cls = " positive" if cfa.get("fcf_latest", 0) > 0 else " negative"

    # Flags
    flags_html = ""
    for f_ in a["flags"].get("green", []):
        flags_html += f'<div class="flag green">✓ {f_}</div>\n'
    for f_ in a["flags"].get("amber", []):
        flags_html += f'<div class="flag amber">⚠ {f_}</div>\n'
    for f_ in a["flags"].get("red", []):
        flags_html += f'<div class="flag red">✗ {f_}</div>\n'

    # Signals
    val_signals = ""
    for signal, sentiment in a["valuation"]["signals"]:
        icon = {"bullish": "▲", "bearish": "▼", "neutral": "●", "info": "ℹ"}.get(sentiment, "●")
        clr = {"bullish": "#10B981", "bearish": "#EF4444", "neutral": "#F59E0B", "info": "#64748B"}.get(sentiment)
        val_signals += f'<div style="color:{clr};margin:3px 0;font-size:13px">{icon} {signal}</div>\n'

    tech_signals = ""
    for signal, sentiment in a["technical"]["signals"]:
        icon = {"bullish": "▲", "bearish": "▼", "caution": "⚠", "neutral": "●", "info": "ℹ"}.get(sentiment, "●")
        clr = {"bullish": "#10B981", "bearish": "#EF4444", "caution": "#F59E0B",
               "neutral": "#F59E0B", "info": "#64748B"}.get(sentiment)
        tech_signals += f'<div style="color:{clr};margin:3px 0;font-size:13px">{icon} {signal}</div>\n'

    # Growth table
    growth_rows = ""
    growth_headers = ""
    for category, vals in a.get("growth", {}).items():
        short = (category.replace("Compounded ", "").replace("Stock Price CAGR", "Stock CAGR")
                 .replace("Return on Equity", "ROE"))
        cells = ""
        for period, val in vals.items():
            c = "#10B981" if val > 10 else "#F59E0B" if val > 0 else "#EF4444"
            cells += f'<td style="color:{c};font-weight:600">{val:+.0f}%</td>'
        growth_rows += f'<tr><td style="color:#64748B">{short}</td>{cells}</tr>\n'
    first_cat = next(iter(a.get("growth", {}).values()), {})
    growth_headers = "".join(f'<th>{p}</th>' for p in first_cat)

    # Peers
    peers_html = ""
    peers = d.get("peers", [])
    if peers and len(peers) >= 2:
        hdrs = [k for k in peers[0].keys() if not k.startswith("_")][:8]
        p_h = "<tr>" + "".join(f"<th>{h}</th>" for h in hdrs) + "</tr>"
        p_b = ""
        for peer in peers[:10]:
            is_self = peer.get("_ticker", "").upper() == ticker
            st = 'style="background:#EBF5FF;font-weight:600"' if is_self else ""
            cells = "".join(f"<td>{peer.get(h, '')}</td>" for h in hdrs)
            p_b += f"<tr {st}>{cells}</tr>\n"
        peers_html = f'<div class="section"><h2>Peer Comparison</h2><table class="data-table">{p_h}{p_b}</table></div>'

    # Segments
    segments_html = ""
    if d.get("segments"):
        seg_items = "".join(f'<span class="segment-tag">{s}</span>' for s in d["segments"])
        segments_html = f'<div class="section"><h2>Business Segments</h2><div style="display:flex;flex-wrap:wrap;gap:4px">{seg_items}</div></div>'

    # Documents
    docs_html = ""
    docs = d.get("documents", {})
    if docs.get("concalls") or docs.get("annual_reports"):
        items = ""
        for doc in docs.get("concalls", [])[:3]:
            items += f'<div class="doc-item">📞 <a href="{doc["url"]}" target="_blank">{doc["text"][:80]}</a></div>\n'
        for doc in docs.get("annual_reports", [])[:2]:
            items += f'<div class="doc-item">📄 <a href="{doc["url"]}" target="_blank">{doc["text"][:80]}</a></div>\n'
        docs_html = f'<div class="section"><h2>Recent Documents</h2>{items}</div>'

    # Shareholding
    sha = a.get("sh_analysis", {})
    sh_html = ""
    if sha and sha.get("promoter_latest", 0) > 0:
        fii_cls = " positive" if sha.get("fii_trend") == "increasing" else (
            " negative" if sha.get("fii_trend") == "decreasing" else "")
        sh_html = f'''<div class="section">
    <h2>Shareholding Pattern</h2>
    <div class="sh-bar">
        <div style="width:{sha.get("promoter_latest",0)}%;background:#16A34A">P {sha.get("promoter_latest",0):.0f}%</div>
        <div style="width:{sha.get("fii_latest",0)}%;background:#3B82F6">FII {sha.get("fii_latest",0):.0f}%</div>
        <div style="width:{sha.get("dii_latest",0)}%;background:#0891B2">DII {sha.get("dii_latest",0):.0f}%</div>
        <div style="width:{sha.get("public_latest",0)}%;background:#9CA3AF">Pub {sha.get("public_latest",0):.0f}%</div>
    </div>
    <div class="metric"><span class="key">Promoter Trend</span><span class="val">{sha.get("promoter_trend","N/A")}</span></div>
    <div class="metric"><span class="key">FII Trend</span><span class="val{fii_cls}">{sha.get("fii_trend","N/A")}</span></div>
</div>'''

    # Price chart (full-width, with period switcher)
    price_chart_html = ""
    price_periods = [(k, charts[k]) for k in ["price_1y", "price_3y", "price_5y", "price_max"] if k in charts]
    if price_periods:
        period_labels = {"price_1y": "1Y", "price_3y": "3Y", "price_5y": "5Y", "price_max": "All"}
        # Default to All-time (price_max) if available, else first available
        default_pk = "price_max" if any(pk == "price_max" for pk, _ in price_periods) else price_periods[0][0]
        btns = ""
        divs = ""
        for idx, (pk, phtml) in enumerate(price_periods):
            active_cls = " active" if pk == default_pk else ""
            disp = "block" if pk == default_pk else "none"
            btns += f'<button class="period-btn{active_cls}" data-period="{pk}" onclick="switchPeriod(\'{ticker}\', \'{pk}\')">{period_labels[pk]}</button>'
            divs += f'<div class="price-period" id="{ticker}-{pk}" style="display:{disp}">{_defer_plotly(phtml)}</div>'
        price_chart_html = f'''<div class="section" style="padding:12px">
    <div class="period-bar" id="pbar-{ticker}">{btns}</div>
    {divs}
</div>'''

    # Charts grid
    chart_cards = ""
    for key in ["annual_pl", "quarterly", "margins", "returns", "shareholding", "cashflow"]:
        if key in charts:
            chart_cards += f'<div class="chart-card">{_defer_plotly(charts[key])}</div>\n'

    opm_val = pl.get("opm_latest", 0)
    npm_val = pl.get("npm_latest", 0)

    return f'''<div class="tab-pane" id="pane-{ticker}" style="display:{display}">
<div class="stock-header">
    <div>
        <h1>{company}</h1>
        <div class="sub">{ticker} · {cons_label} · {d.get("fetched_at","")[:10]}</div>
    </div>
    <div class="price-block">
        <div class="price">₹{a["current_price"]:,.0f}</div>
        <div class="meta">52W: ₹{a["low_52w"]:,.0f} – ₹{a["high_52w"]:,.0f}</div>
    </div>
</div>

<div class="scorecard">
    <div class="score-card"><div class="label">Quality</div>
        <div class="value" style="color:{grade_color}">{grade}</div>
        <div class="desc">{a["quality"]["score"]}/100</div></div>
    <div class="score-card"><div class="label">Valuation</div>
        <div class="value" style="color:{val_color};font-size:20px">{a["valuation"]["verdict"]}</div>
        <div class="desc">{val_score}/100</div></div>
    <div class="score-card"><div class="label">Market Cap</div>
        <div class="value" style="font-size:20px;color:var(--dark)">{fmt_cr(a["market_cap"])}</div>
        <div class="desc">P/E: {a["pe"]:.1f}</div></div>
    <div class="score-card"><div class="label">ROCE / ROE</div>
        <div class="value" style="font-size:20px">{a["roce"]:.1f}%</div>
        <div class="desc">ROE: {a["roe"]:.1f}%</div></div>
    <div class="score-card"><div class="label">P/B · Div</div>
        <div class="value" style="font-size:20px">{a["pb"]:.2f}</div>
        <div class="desc">Div: {a["div_yield"]:.1f}%</div></div>
</div>

{price_chart_html}

{"" if not sankey_html else f'<div class="section"><h2>Income Flow (Sankey)</h2><div style="width:100%;overflow-x:auto">{_defer_plotly(sankey_html)}</div></div>'}

{"" if not chart_cards else f'<div class="charts-grid">{chart_cards}</div>'}

<div class="grid-4">
<div class="section">
    <h2>Key Metrics</h2>
    <div class="metric"><span class="key">Price</span><span class="val">₹{a["current_price"]:,.0f}</span></div>
    <div class="metric"><span class="key">52W H/L</span><span class="val">₹{a["high_52w"]:,.0f} / ₹{a["low_52w"]:,.0f}</span></div>
    <div class="metric"><span class="key">Book Value</span><span class="val">₹{a["book_value"]:,.0f}</span></div>
    <div class="metric"><span class="key">ROCE</span><span class="val{roce_cls}">{a["roce"]:.1f}%</span></div>
    <div class="metric"><span class="key">ROE</span><span class="val{roe_cls}">{a["roe"]:.1f}%</span></div>
    <div class="bar-52w"><div class="fill" style="width:{pos_52w*100:.0f}%;background:{fill_color}"></div></div>
</div>
<div class="section">
    <h2>Profit & Loss</h2>
    <div class="metric"><span class="key">Revenue</span><span class="val">{fmt_cr(pl.get("sales_latest",0))}</span></div>
    <div class="metric"><span class="key">Net Profit</span><span class="val{np_cls}">{fmt_cr(pl.get("net_profit_latest",0))}</span></div>
    <div class="metric"><span class="key">OPM / NPM</span><span class="val">{opm_val:.0f}% / {npm_val:.1f}%</span></div>
    <div class="metric"><span class="key">3Y Rev CAGR</span><span class="val">{pl.get("sales_cagr_3y",0):+.1f}%</span></div>
    <div class="metric"><span class="key">3Y Profit CAGR</span><span class="val">{pl.get("profit_cagr_3y",0):+.1f}%</span></div>
</div>
<div class="section">
    <h2>Balance Sheet</h2>
    <div class="metric"><span class="key">Equity</span><span class="val">{fmt_cr(bsa.get("shareholder_equity",0))}</span></div>
    <div class="metric"><span class="key">Borrowings</span><span class="val">{fmt_cr(bsa.get("borrowings",0))}</span></div>
    <div class="metric"><span class="key">D/E</span><span class="val{de_cls}">{bsa.get("de_ratio",0):.2f}</span></div>
    <div class="metric"><span class="key">Assets</span><span class="val">{fmt_cr(bsa.get("total_assets",0))}</span></div>
</div>
<div class="section">
    <h2>Cash Flow</h2>
    <div class="metric"><span class="key">CFO</span><span class="val{cfo_cls}">{fmt_cr(cfa.get("cfo_latest",0))}</span></div>
    <div class="metric"><span class="key">FCF</span><span class="val{fcf_cls}">{fmt_cr(cfa.get("fcf_latest",0))}</span></div>
    <div class="metric"><span class="key">CFO +ve</span><span class="val">{int(cfa.get("cfo_consistency",0)*cfa.get("total_years",0))}/{cfa.get("total_years",0)} yrs</span></div>
</div>
</div>

{sh_html}

{"" if not growth_rows else f'<div class="section"><h2>Compounded Growth</h2><table class="data-table"><tr><th>Metric</th>{growth_headers}</tr>{growth_rows}</table></div>'}

<div class="grid">
<div class="section"><h2>Valuation Signals</h2>{val_signals}</div>
<div class="section"><h2>Technical Signals</h2>{tech_signals}</div>
</div>

<div class="section"><h2>Flags</h2>{flags_html}</div>

{segments_html}
{peers_html}
{docs_html}

<div class="footer">
    <p>Source: <a href="{d["url"]}">screener.in/{ticker}</a> · Generated {datetime.now().strftime("%d %b %Y %H:%M")}</p>
</div>
</div>'''


def generate_dashboard(stocks: list, sections: list = None) -> str:
    """
    Generate multi-stock tabbed dashboard HTML.
    stocks: list of dicts with keys: data, analysis, charts, sankey_html
    sections: optional list of dicts with keys: name, tickers (for accordion grouping)
              e.g. [{"name": "Nifty 50", "tickers": ["RELIANCE", ...]}, ...]
    """
    # ── CSS (plain string — no f-string brace escaping) ──
    css = """
:root { --green:#10B981; --red:#EF4444; --amber:#F59E0B;
    --blue:#3B82F6; --grey:#64748B; --dark:#0F172A;
    --card:#FFFFFF; --bg:#F1F5F9; --border:#E2E8F0;
    --sidebar-w:220px; }
* { margin:0; padding:0; box-sizing:border-box; }
html,body { height:100%; }
body { font-family:'Segoe UI',-apple-system,sans-serif; background:var(--bg);
    color:#1E293B; display:flex; }
/* ── Vertical Sidebar ── */
.sidebar { position:fixed; top:0; left:0; width:var(--sidebar-w); height:100vh;
    background:var(--dark); overflow-y:auto; z-index:100;
    box-shadow:2px 0 10px rgba(0,0,0,0.3); display:flex; flex-direction:column; }
.sidebar-header { padding:18px 16px 12px; border-bottom:1px solid rgba(255,255,255,0.1); }
.sidebar-header h3 { color:white; font-size:14px; font-weight:700;
    letter-spacing:0.5px; }
.sidebar-header .subtitle { color:#64748B; font-size:10px; margin-top:2px; }
.tab-list { flex:1; padding:8px; }
.tab-btn { display:flex; align-items:center; justify-content:space-between;
    width:100%; background:transparent; border:none; color:#94A3B8;
    padding:10px 12px; font-size:13px; font-weight:600; cursor:pointer;
    border-radius:8px; border-left:3px solid transparent; transition:all 0.15s;
    font-family:inherit; margin-bottom:2px; text-align:left; }
.tab-btn:hover { color:#E2E8F0; background:rgba(255,255,255,0.05); }
.tab-btn.active { color:white; background:rgba(59,130,246,0.15);
    border-left-color:var(--blue); }
.tab-btn .ticker-name { flex:1; }
.tab-btn .price-sm { font-size:10px; color:#94A3B8; margin-left:4px; }
.tab-btn.active .price-sm { color:#CBD5E1; }
.grade-badge { font-size:9px; padding:1px 5px; border-radius:4px;
    font-weight:700; color:white; flex-shrink:0; }
/* ── Main Content ── */
.main-container { margin-left:var(--sidebar-w); flex:1; padding:20px 24px;
    width:calc(100% - var(--sidebar-w)); min-height:100vh;
    max-width:calc(100% - var(--sidebar-w)); }
.stock-header { display:flex; justify-content:space-between; align-items:center;
    background:linear-gradient(135deg,#0F172A,#1E3A5F); padding:20px 28px;
    border-radius:14px; margin-bottom:16px; color:white; }
.stock-header h1 { font-size:22px; margin-bottom:2px; }
.stock-header .sub { font-size:12px; opacity:0.7; }
.price-block { text-align:right; }
.price-block .price { font-size:28px; font-weight:800; }
.price-block .meta { font-size:11px; opacity:0.7; }
.scorecard { display:flex; gap:10px; margin-bottom:16px; flex-wrap:wrap; }
.score-card { flex:1; min-width:120px; background:var(--card); border-radius:10px;
    padding:14px 10px; text-align:center; box-shadow:0 1px 3px rgba(0,0,0,0.08); }
.score-card .label { font-size:9px; color:var(--grey); text-transform:uppercase;
    letter-spacing:1px; }
.score-card .value { font-size:24px; font-weight:800; margin:3px 0; }
.score-card .desc { font-size:10px; color:var(--grey); }
.section { background:var(--card); border-radius:12px; padding:16px;
    box-shadow:0 1px 3px rgba(0,0,0,0.08); margin-bottom:16px; }
.section h2 { font-size:14px; color:var(--dark); margin-bottom:10px;
    border-bottom:2px solid var(--border); padding-bottom:6px; }
.charts-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(380px, 1fr)); gap:14px;
    margin-bottom:16px; }
.chart-card { background:var(--card); border-radius:12px; padding:10px;
    box-shadow:0 1px 3px rgba(0,0,0,0.08); overflow:hidden; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:16px; }
.grid-4 { display:grid; grid-template-columns:repeat(auto-fit, minmax(250px, 1fr)); gap:14px;
    margin-bottom:16px; }
/* Sidebar collapse toggle */
.sidebar-toggle { position:fixed; top:8px; left:var(--sidebar-w); z-index:101;
    width:24px; height:24px; border-radius:50%; border:1px solid var(--border);
    background:var(--card); cursor:pointer; display:flex; align-items:center;
    justify-content:center; font-size:12px; color:var(--grey);
    box-shadow:0 1px 4px rgba(0,0,0,0.15); transition:left 0.2s; }
body.sidebar-collapsed .sidebar { width:0; overflow:hidden; }
body.sidebar-collapsed .main-container { margin-left:0; width:100%; max-width:100%; }
body.sidebar-collapsed .sidebar-toggle { left:0; }
@media(max-width:768px) { .grid,.grid-4 { grid-template-columns:1fr; }
    :root { --sidebar-w:56px; }
    .sidebar-header h3,.tab-btn .ticker-name,.tab-btn .price-sm { display:none; }
    .sidebar-header .subtitle { display:none; }
    .tab-btn { justify-content:center; padding:10px 6px; }
}
.metric { display:flex; justify-content:space-between; padding:4px 0;
    border-bottom:1px solid #F1F5F9; font-size:13px; }
.metric .key { color:var(--grey); }
.metric .val { font-weight:600; }
.positive { color:var(--green); }
.negative { color:var(--red); }
.flag { padding:4px 10px; margin:3px 0; border-radius:6px; font-size:12px; }
.flag.green { background:#F0FDF4; color:#166534; border-left:3px solid var(--green); }
.flag.red { background:#FEF2F2; color:#991B1B; border-left:3px solid var(--red); }
.flag.amber { background:#FFFBEB; color:#92400E; border-left:3px solid var(--amber); }
.data-table { width:100%; border-collapse:collapse; font-size:12px; }
.data-table th { text-align:right; padding:4px 6px; background:#F8FAFC;
    color:var(--grey); font-size:11px; border-bottom:2px solid var(--border); }
.data-table td { text-align:right; padding:4px 6px; border-bottom:1px solid #F1F5F9; }
.data-table td:first-child,.data-table th:first-child { text-align:left; }
.bar-52w { height:6px; background:#E2E8F0; border-radius:3px; margin:6px 0; }
.bar-52w .fill { height:100%; border-radius:3px; }
.segment-tag { display:inline-block; background:#EBF5FF; color:var(--blue);
    padding:3px 10px; border-radius:16px; margin:3px; font-size:12px; }
.sh-bar { display:flex; height:20px; border-radius:6px; overflow:hidden; margin:8px 0; }
.sh-bar > div { display:flex; align-items:center; justify-content:center;
    font-size:10px; color:white; font-weight:600; }
.doc-item { padding:3px 0; font-size:12px; }
.doc-item a { color:var(--blue); text-decoration:none; }
.footer { text-align:center; color:var(--grey); font-size:11px; padding:12px; }
.footer a { color:var(--blue); text-decoration:none; }
/* Period switcher */
.period-bar { display:flex; gap:4px; margin-bottom:8px; }
.period-btn { padding:5px 14px; border:1px solid var(--border); border-radius:6px;
    background:var(--card); cursor:pointer; font-size:12px; font-weight:600;
    color:var(--grey); font-family:inherit; transition:all 0.15s; }
.period-btn:hover { color:var(--dark); border-color:#94A3B8; }
.period-btn.active { background:var(--blue); color:white; border-color:var(--blue); }
/* Accordion sections */
.section-header { display:flex; align-items:center; padding:8px 12px;
    color:#94A3B8; font-size:10px; font-weight:700; text-transform:uppercase;
    letter-spacing:1.2px; cursor:pointer; user-select:none;
    border-bottom:1px solid rgba(255,255,255,0.05);
    transition:background 0.15s; }
.section-header:hover { background:rgba(255,255,255,0.03); }
.section-header .chevron { margin-right:6px; transition:transform 0.2s;
    font-size:8px; display:inline-block; }
.section-header.collapsed .chevron { transform:rotate(-90deg); }
.section-header .sec-count { margin-left:auto; color:#475569;
    font-size:9px; font-weight:400; }
.section-body { overflow:hidden; transition:max-height 0.3s ease; }
.section-body.collapsed { max-height:0 !important; overflow:hidden; }
/* Add symbol input */
.add-symbol { padding:8px; border-top:1px solid rgba(255,255,255,0.1); position:relative; }
.add-symbol input { width:100%; padding:7px 10px; border:1px solid rgba(255,255,255,0.15);
    border-radius:6px; background:rgba(255,255,255,0.07); color:white;
    font-size:12px; font-family:inherit; outline:none; }
.add-symbol input::placeholder { color:#64748B; }
.add-symbol input:focus { border-color:var(--blue); background:rgba(59,130,246,0.1); }
.add-symbol .hint { font-size:9px; color:#475569; margin-top:4px; padding:0 2px; }
/* Autocomplete dropdown */
.ac-dropdown { position:absolute; left:8px; right:8px; background:#1E293B;
    border:1px solid rgba(255,255,255,0.15); border-radius:6px; max-height:240px;
    overflow-y:auto; z-index:200; display:none; box-shadow:0 4px 12px rgba(0,0,0,0.4); }
.ac-item { padding:7px 10px; cursor:pointer; font-size:12px; color:#CBD5E1;
    display:flex; justify-content:space-between; align-items:center;
    border-bottom:1px solid rgba(255,255,255,0.05); }
.ac-item:last-child { border-bottom:none; }
.ac-item:hover,.ac-item.highlighted { background:rgba(59,130,246,0.2); color:white; }
.ac-item .ac-ticker { font-weight:700; color:white; }
.ac-item .ac-name { font-size:11px; color:#94A3B8; margin-left:8px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1; }
/* Delete button on tabs */
.tab-btn .del-btn { display:none; margin-left:4px; width:16px; height:16px;
    border-radius:50%; border:none; background:rgba(255,255,255,0.1); color:#94A3B8;
    font-size:10px; cursor:pointer; line-height:16px; text-align:center;
    flex-shrink:0; }
.tab-btn:hover .del-btn { display:inline-flex; align-items:center; justify-content:center; }
.tab-btn .del-btn:hover { background:#EF4444; color:white; }
/* Loading spinner */
.tab-btn.loading .ticker-name::after { content:' ⏳'; }
.loading-pane { text-align:center; padding:60px 20px; color:var(--grey); font-size:16px; }
.loading-pane .spinner { display:inline-block; width:32px; height:32px;
    border:3px solid var(--border); border-top-color:var(--blue);
    border-radius:50%; animation:spin 0.8s linear infinite; margin-bottom:12px; }
@keyframes spin { to { transform:rotate(360deg); } }
"""

    # ── JavaScript (plain string — no brace escaping) ──
    js = """
var _acTimer = null, _acIdx = -1, _acItems = [];
var _serverMode = !!(location.protocol === 'http:' || location.protocol === 'https:');
// Try to detect if we're served by our local API
if (_serverMode) {
    fetch('/api/ping').then(function(r) {
        if (!r.ok) _serverMode = false;
        else saveWatchlist(); // seed cache with server-loaded stocks
    }).catch(function() { _serverMode = false; });
}

// ── Lazy chart rendering ──
function renderCharts(pane) {
    if (!pane) return;
    var deferred = pane.querySelectorAll('script[type="text/plotly-deferred"]');
    deferred.forEach(function(s) {
        var ns = document.createElement('script');
        ns.textContent = s.textContent;
        s.parentNode.replaceChild(ns, s);
    });
}

function switchTab(ticker) {
    document.querySelectorAll('.tab-pane').forEach(function(p) { p.style.display = 'none'; });
    document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
    var pane = document.getElementById('pane-' + ticker);
    if (pane) pane.style.display = 'block';
    document.querySelectorAll('.tab-btn').forEach(function(b) {
        if (b.dataset.ticker === ticker) b.classList.add('active');
    });
    // Render deferred charts on first view
    if (pane) renderCharts(pane);
    setTimeout(function() {
        var plots = pane ? pane.querySelectorAll('.js-plotly-plot') : [];
        plots.forEach(function(p) { if (window.Plotly) Plotly.Plots.resize(p); });
    }, 200);
    pane && pane.scrollIntoView({behavior:'smooth', block:'start'});
}

function switchPeriod(ticker, period) {
    var pane = document.getElementById('pane-' + ticker);
    if (!pane) return;
    pane.querySelectorAll('.price-period').forEach(function(d) { d.style.display = 'none'; });
    var target = document.getElementById(ticker + '-' + period);
    if (target) target.style.display = 'block';
    var bar = document.getElementById('pbar-' + ticker);
    if (bar) bar.querySelectorAll('.period-btn').forEach(function(b) {
        b.classList.toggle('active', b.dataset.period === period);
    });
    // Force Plotly to recalculate layout for charts that were hidden at render time
    setTimeout(function() {
        if (!target) return;
        target.querySelectorAll('.js-plotly-plot').forEach(function(p) {
            if (window.Plotly) {
                Plotly.Plots.resize(p);
                Plotly.relayout(p, { autosize: true });
            }
        });
    }, 50);
}

function toggleSidebar() {
    document.body.classList.toggle('sidebar-collapsed');
    setTimeout(function() {
        document.querySelectorAll('.js-plotly-plot').forEach(function(p) {
            if (window.Plotly && p.offsetParent !== null) Plotly.Plots.resize(p);
        });
    }, 300);
}

function updateCount() {
    var sub = document.querySelector('.sidebar-header .subtitle');
    var n = document.querySelectorAll('.tab-pane').length;
    sub.textContent = n + ' stock' + (n !== 1 ? 's' : '');
}

// ── localStorage watchlist cache ──
var _LS_KEY = 'screener_watchlist';
function saveWatchlist() {
    var tickers = [];
    document.querySelectorAll('.tab-btn').forEach(function(b) {
        if (b.dataset.ticker) tickers.push(b.dataset.ticker);
    });
    try { localStorage.setItem(_LS_KEY, JSON.stringify(tickers)); } catch(e) {}
}
function getCachedWatchlist() {
    try { return JSON.parse(localStorage.getItem(_LS_KEY) || '[]'); } catch(e) { return []; }
}
function clearCache() {
    try { localStorage.removeItem(_LS_KEY); } catch(e) {}
    console.log('Cache cleared');
}

function deleteStock(ticker, evt) {
    evt.stopPropagation();
    var pane = document.getElementById('pane-' + ticker);
    var btn = document.querySelector('.tab-btn[data-ticker="' + ticker + '"]');
    var wasActive = btn && btn.classList.contains('active');
    if (pane) pane.remove();
    if (btn) btn.remove();
    updateCount();
    saveWatchlist();
    // Switch to first remaining tab if we deleted the active one
    if (wasActive) {
        var first = document.querySelector('.tab-btn');
        if (first) switchTab(first.dataset.ticker);
    }
}

function addTabButton(ticker, price, grade, gradeColor) {
    var list = document.querySelector('.tab-list');
    var btn = document.createElement('button');
    btn.className = 'tab-btn';
    btn.dataset.ticker = ticker;
    btn.onclick = function() { switchTab(ticker); };
    btn.innerHTML = '<span class="ticker-name">' + ticker + '</span>' +
        (price ? '<span class="price-sm">' + price + '</span> ' : '') +
        '<span class="grade-badge" style="background:' + (gradeColor||'#64748B') + '">' + (grade||'?') + '</span>' +
        '<span class="del-btn" onclick="deleteStock(\\'' + ticker + '\\', event)">&times;</span>';
    list.appendChild(btn);
    return btn;
}

function addSymbol(singleTicker) {
    var input = document.getElementById('add-symbol-input');
    var raw = singleTicker || input.value.trim().toUpperCase();
    if (!raw) return;
    hideAC();
    // Support pasting multiple symbols (comma, space, newline, semicolon separated)
    var tickers = raw.split(/[,;\\s\\n]+/).filter(function(t) { return t.length > 0; });
    tickers.forEach(function(ticker) {
        ticker = ticker.replace(/[^A-Z0-9&]/g, '');
        if (!ticker) return;
        // Already in dashboard?
        if (document.getElementById('pane-' + ticker)) {
            switchTab(ticker);
            return;
        }
        // Add loading tab
        var btn = addTabButton(ticker, '', '?', '#64748B');
        btn.classList.add('loading');
        // Add loading pane
        var pane = document.createElement('div');
        pane.className = 'tab-pane';
        pane.id = 'pane-' + ticker;
        pane.style.display = 'none';
        pane.innerHTML = '<div class="loading-pane"><div class="spinner"></div><br>Analysing ' + ticker + '...</div>';
        document.querySelector('.main-container').appendChild(pane);
        updateCount();
        // Try live fetch if in server mode
        if (_serverMode) {
            fetch('/api/analyze/' + ticker)
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    btn.classList.remove('loading');
                    if (data.error) {
                        pane.innerHTML = '<div class="stock-header"><div><h1>' + ticker + '</h1>' +
                            '<div class="sub">Error: ' + data.error + '</div></div></div>';
                        return;
                    }
                    // Update tab button with real data
                    var tn = btn.querySelector('.ticker-name');
                    if (tn) tn.textContent = ticker;
                    var ps = btn.querySelector('.price-sm');
                    if (ps) ps.textContent = data.price || '';
                    var gb = btn.querySelector('.grade-badge');
                    if (gb) { gb.textContent = data.grade || '?'; gb.style.background = data.grade_color || '#64748B'; }
                    // Replace pane content
                    pane.outerHTML = data.pane_html;
                    var np = document.getElementById('pane-' + ticker);
                    if (np) renderCharts(np);
                    switchTab(ticker);
                })
                .catch(function(err) {
                    btn.classList.remove('loading');
                    pane.innerHTML = '<div class="stock-header"><div><h1>' + ticker + '</h1>' +
                        '<div class="sub">Fetch failed: ' + err.message + '</div></div></div>';
                });
        } else {
            // Static mode: show instructions
            btn.classList.remove('loading');
            pane.innerHTML = '<div class="stock-header"><div>' +
                '<h1>' + ticker + '</h1>' +
                '<div class="sub">Not yet analysed</div></div></div>' +
                '<div class="section" style="text-align:center;padding:40px">' +
                '<p style="font-size:16px;margin-bottom:12px">To add <b>' + ticker + '</b>, run in <b>--serve</b> mode:</p>' +
                '<code style="background:#1E293B;color:#E2E8F0;padding:10px 20px;border-radius:8px;font-size:14px;display:inline-block">' +
                'python company_screener.py -w watchlist.txt --serve</code>' +
                '<p style="margin-top:12px;color:#64748B;font-size:13px">Stocks added in serve mode are fetched live.</p>' +
                '<p style="margin-top:8px"><a href="https://www.screener.in/company/' + ticker + '/" target="_blank" style="color:#3B82F6">View on screener.in \u2192</a></p></div>';
        }
    });
    if (tickers.length === 1) switchTab(tickers[0]);
    input.value = '';
    saveWatchlist();
}

// ── Autocomplete ──
function showAC(items) {
    var dd = document.getElementById('ac-dropdown');
    if (!items || !items.length) { hideAC(); return; }
    _acItems = items;
    _acIdx = -1;
    dd.innerHTML = '';
    items.forEach(function(item, idx) {
        var div = document.createElement('div');
        div.className = 'ac-item';
        div.innerHTML = '<span class="ac-ticker">' + item.ticker + '</span><span class="ac-name">' + (item.name||'') + '</span>';
        div.onmousedown = function(e) { e.preventDefault(); addSymbol(item.ticker); };
        div.onmouseover = function() { highlightAC(idx); };
        dd.appendChild(div);
    });
    dd.style.display = 'block';
}
function hideAC() {
    var dd = document.getElementById('ac-dropdown');
    if (dd) dd.style.display = 'none';
    _acIdx = -1;
    _acItems = [];
}
function highlightAC(idx) {
    var dd = document.getElementById('ac-dropdown');
    var items = dd.querySelectorAll('.ac-item');
    items.forEach(function(el, i) { el.classList.toggle('highlighted', i === idx); });
    _acIdx = idx;
}

function handleSymbolInput(e) {
    var input = e.target;
    var val = input.value.trim();

    // Arrow navigation
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (_acItems.length) highlightAC(Math.min(_acIdx + 1, _acItems.length - 1));
        return;
    }
    if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (_acItems.length) highlightAC(Math.max(_acIdx - 1, 0));
        return;
    }
    if (e.key === 'Enter') {
        e.preventDefault();
        if (_acIdx >= 0 && _acItems[_acIdx]) {
            addSymbol(_acItems[_acIdx].ticker);
        } else {
            addSymbol();
        }
        hideAC();
        return;
    }
    if (e.key === 'Escape') { hideAC(); return; }

    // Debounced search
    clearTimeout(_acTimer);
    if (val.length < 2) { hideAC(); return; }
    _acTimer = setTimeout(function() {
        if (_serverMode) {
            fetch('/api/search?q=' + encodeURIComponent(val))
                .then(function(r) { return r.json(); })
                .then(function(data) { showAC(data.results || []); })
                .catch(function() { hideAC(); });
        } else {
            // Static mode: no autocomplete
            hideAC();
        }
    }, 250);
}

// ── Paste handler ──
function handlePaste(e) {
    setTimeout(function() {
        var val = e.target.value.trim();
        // If pasted text has commas, newlines, or multiple spaces, treat as list
        if (/[,;\\n]/.test(val) || val.split(/\\s+/).length > 1) {
            addSymbol();
        }
    }, 50);
}

window.addEventListener('resize', function() {
    document.querySelectorAll('.js-plotly-plot').forEach(function(p) {
        if (window.Plotly && p.offsetParent !== null) Plotly.Plots.resize(p);
    });
});
// Close autocomplete when clicking outside
document.addEventListener('click', function(e) {
    if (!e.target.closest('.add-symbol')) hideAC();
});

// ── Keyboard navigation for stock tabs ──
document.addEventListener('keydown', function(e) {
    // Only handle if not typing in an input
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    var btns = Array.from(document.querySelectorAll('.tab-btn'));
    if (!btns.length) return;
    var activeIdx = btns.findIndex(function(b) { return b.classList.contains('active'); });
    if (e.key === 'ArrowDown') {
        activeIdx = (activeIdx + 1) % btns.length;
    } else {
        activeIdx = (activeIdx - 1 + btns.length) % btns.length;
    }
    var ticker = btns[activeIdx].dataset.ticker;
    switchTab(ticker);
    btns[activeIdx].scrollIntoView({ block: 'nearest' });
});

// ── Accordion toggle ──
function toggleSection(id) {
    var hdr = document.getElementById('sec-hdr-' + id);
    var body = document.getElementById('sec-body-' + id);
    if (!hdr || !body) return;
    hdr.classList.toggle('collapsed');
    body.classList.toggle('collapsed');
}

// ── On load: restore cached watchlist ──
(function restoreFromCache() {
    if (!_serverMode) return; // only in serve mode
    var cached = getCachedWatchlist();
    if (!cached.length) return;
    // Find tickers in cache that aren't already loaded
    var loaded = {};
    document.querySelectorAll('.tab-btn').forEach(function(b) {
        loaded[b.dataset.ticker] = true;
    });
    var toLoad = cached.filter(function(t) { return !loaded[t]; });
    if (!toLoad.length) {
        // Cache matches dashboard — save to sync (handles removals from server restart)
        saveWatchlist();
        return;
    }
    console.log('Restoring ' + toLoad.length + ' cached stocks...');
    // Sequentially load missing stocks with small delay to avoid hammering
    var idx = 0;
    function loadNext() {
        if (idx >= toLoad.length) return;
        var t = toLoad[idx++];
        addSymbol(t);
        setTimeout(loadNext, 500);
    }
    // Wait a moment for serverMode detection to settle
    setTimeout(loadNext, 800);
})();

// ── Render first visible pane's charts on page load ──
(function initFirstPane() {
    var first = document.querySelector('.tab-pane[style*=\"display:block\"]') ||
                document.querySelector('.tab-pane');
    if (first) renderCharts(first);
})();
"""

    # ── Build tab buttons (grouped by sections if provided) ──
    def _make_tab_btn(s, is_first=False):
        t = s["data"]["ticker"]
        a = s["analysis"]
        g = a["quality"]["grade"]
        gc = {"A+": "#10B981", "A": "#22C55E", "B+": "#F59E0B",
              "B": "#F59E0B", "C": "#EF4444", "D": "#DC2626"}.get(g, "#64748B")
        price = a.get("current_price", 0)
        active = " active" if is_first else ""
        return (f'<button class="tab-btn{active}" data-ticker="{t}" '
                f"onclick=\"switchTab('{t}')\">"
                f'<span class="ticker-name">{t}</span>'
                f'<span class="price-sm">₹{price:,.0f}</span> '
                f'<span class="grade-badge" style="background:{gc}">{g}</span>'
                f'<span class="del-btn" onclick="deleteStock(\'{t}\', event)">&times;</span></button>\n')

    first_ticker = stocks[0]["data"]["ticker"] if stocks else ""
    # Index stocks by ticker for section lookup
    stock_by_ticker = {s["data"]["ticker"]: s for s in stocks}

    tab_btns = ""
    if sections:
        assigned = set()
        for sec_idx, sec in enumerate(sections):
            sec_id = re.sub(r'[^a-z0-9]', '', sec["name"].lower())
            sec_tickers = [t for t in sec["tickers"] if t in stock_by_ticker]
            assigned.update(sec_tickers)
            tab_btns += (f'<div class="section-header" id="sec-hdr-{sec_id}" '
                         f'onclick="toggleSection(\'{sec_id}\')">' 
                         f'<span class="chevron">▼</span>{sec["name"]}'
                         f'<span class="sec-count">{len(sec_tickers)}</span></div>\n')
            tab_btns += f'<div class="section-body" id="sec-body-{sec_id}" style="max-height:9999px">\n'
            for t in sec_tickers:
                s = stock_by_ticker[t]
                is_first = (t == first_ticker)
                tab_btns += _make_tab_btn(s, is_first)
            tab_btns += '</div>\n'
        # Any unassigned stocks go into an "Other" section
        remaining = [s for s in stocks if s["data"]["ticker"] not in assigned]
        if remaining:
            tab_btns += ('<div class="section-header" id="sec-hdr-other" '
                         'onclick="toggleSection(\'other\')">' 
                         '<span class="chevron">▼</span>Other'
                         f'<span class="sec-count">{len(remaining)}</span></div>\n')
            tab_btns += '<div class="section-body" id="sec-body-other" style="max-height:9999px">\n'
            for s in remaining:
                tab_btns += _make_tab_btn(s, s["data"]["ticker"] == first_ticker)
            tab_btns += '</div>\n'
    else:
        for i, s in enumerate(stocks):
            tab_btns += _make_tab_btn(s, i == 0)

    # ── Build panes ──
    panes = ""
    for i, s in enumerate(stocks):
        panes += _build_stock_pane(s["data"], s["analysis"],
                                   s.get("charts", {}), s.get("sankey_html", ""),
                                   active=(i == 0))

    # ── Title ──
    if len(stocks) == 1:
        title = f'{stocks[0]["data"]["company_name"]} ({first_ticker})'
    else:
        title = " | ".join(s["data"]["ticker"] for s in stocks)
    title += " — Screener Dashboard"

    n_stocks = len(stocks)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>{css}</style>
</head>
<body>
<nav class="sidebar" id="sidebar">
    <div class="sidebar-header">
        <h3>Stock Screener</h3>
        <div class="subtitle">{n_stocks} stock{'s' if n_stocks != 1 else ''}</div>
    </div>
    <div class="tab-list">{tab_btns}</div>
    <div class="add-symbol">
        <input type="text" id="add-symbol-input" placeholder="Add symbol..."
               onkeydown="handleSymbolInput(event)" onpaste="handlePaste(event)">
        <div id="ac-dropdown" class="ac-dropdown"></div>
        <div class="hint">Type ticker, paste list, or ↑↓ to browse</div>
    </div>
</nav>
<button class="sidebar-toggle" onclick="toggleSidebar()" title="Toggle sidebar">&#9776;</button>
<div class="main-container">
{panes}
</div>
<script>{js}</script>
</body>
</html>"""
    return html


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _load_watchlist(path: str) -> list:
    """Load tickers from a watchlist file (one per line, # comments, blank lines OK)."""
    tickers = []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Watchlist file not found: {path}")
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.split("#")[0].strip()  # strip comments
            if line:
                tickers.append(line.upper())
    return tickers


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE SERVER MODE
# ═══════════════════════════════════════════════════════════════════════════════

class ScreenerHandler(BaseHTTPRequestHandler):
    """Request handler for --serve mode.  Serves the dashboard + REST API."""

    def log_message(self, fmt, *args):
        """Quieter logging."""
        print(f"  {C.GREY}{fmt % args}{C.RESET}")

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        # ── Serve dashboard ──
        if path == "/":
            self._html(self.server.dashboard_html)
            return

        # ── Ping (for detecting server mode) ──
        if path == "/api/ping":
            self._json({"ok": True})
            return

        # ── Search / autocomplete ──
        if path == "/api/search":
            q = qs.get("q", [""])[0].strip()
            if len(q) < 2:
                self._json({"results": []})
                return
            try:
                r = requests.get(
                    f"https://www.screener.in/api/company/search/?q={q}",
                    headers=HEADERS, timeout=5
                )
                items = r.json() if r.status_code == 200 else []
                results = []
                for item in items[:10]:
                    # screener search returns objects with url & name
                    name = item.get("name", "")
                    url = item.get("url", "")
                    # Extract ticker from URL like /company/RELIANCE/consolidated/
                    ticker_match = re.search(r"/company/([^/]+)/", url)
                    ticker = ticker_match.group(1) if ticker_match else name
                    results.append({"ticker": ticker.upper(), "name": name})
                self._json({"results": results})
            except Exception as e:
                self._json({"results": [], "error": str(e)})
            return

        # ── Analyze a ticker ──
        if path.startswith("/api/analyze/"):
            ticker = path.split("/api/analyze/")[1].strip().upper()
            if not ticker or not re.match(r"^[A-Z0-9&]+$", ticker):
                self._json({"error": "Invalid ticker"}, 400)
                return
            try:
                consolidated = not getattr(self.server, "standalone", False)
                data = fetch_full_company_data(ticker, consolidated=consolidated)
                analysis = analyze(data)
                # Generate charts
                try:
                    charts = _generate_charts(data, ticker=ticker)
                except Exception:
                    charts = {}
                # Generate Sankey
                sankey_html = ""
                try:
                    sys.path.insert(0, str(Path(__file__).parent))
                    from income_sankey import get_period_data, build_sankey
                    sankey_data = dict(data)
                    sankey_data["annual"] = sankey_data.get("profit_loss", {})
                    pl_sankey = get_period_data(sankey_data, "annual")
                    fig = build_sankey(
                        pl_sankey, data["company_name"], ticker,
                        data["is_consolidated"], "",
                        data.get("segments"), data.get("expense_breakdown"),
                    )
                    sankey_html = fig.to_html(full_html=False, include_plotlyjs=False)
                except Exception:
                    pass
                # Build the pane HTML
                pane_html = _build_stock_pane(data, analysis, charts, sankey_html, active=False)
                # Get price and grade for tab button
                g = analysis["quality"]["grade"]
                gc = {"A+": "#10B981", "A": "#22C55E", "B+": "#F59E0B",
                      "B": "#F59E0B", "C": "#EF4444", "D": "#DC2626"}.get(g, "#64748B")
                price = analysis.get("current_price", 0)
                self._json({
                    "ticker": ticker,
                    "name": data.get("company_name", ticker),
                    "price": f"₹{price:,.0f}" if price else "",
                    "grade": g,
                    "grade_color": gc,
                    "pane_html": pane_html,
                })
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        # ── 404 ──
        self.send_error(404, "Not found")


def run_server(stocks, port=8765, standalone=False, no_open=False, sections=None):
    """Start the live server with pre-loaded dashboard."""
    print(f"\n{C.CYAN}{C.BOLD}🚀 Generating dashboard...{C.RESET}")
    html = generate_dashboard(stocks, sections=sections)

    server = ThreadingHTTPServer(("127.0.0.1", port), ScreenerHandler)
    server.dashboard_html = html
    server.standalone = standalone

    print(f"\n{C.GREEN}{C.BOLD}✅ Live server running at: http://localhost:{port}{C.RESET}")
    print(f"{C.GREY}   • Add stocks live via the search bar")
    print(f"   • Autocomplete from screener.in")
    print(f"   • Delete stocks with ✕ button on tabs")
    print(f"   • Paste comma-separated tickers")
    print(f"   • Press Ctrl+C to stop{C.RESET}\n")

    if not no_open:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}🛑 Server stopped.{C.RESET}")
        server.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="Stock Screener Dashboard — Fundamental & Technical Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python company_screener.py RELIANCE                Terminal report
  python company_screener.py TCS --brief             Quick scorecard only
  python company_screener.py INFY --html             HTML dashboard
  python company_screener.py RELIANCE TCS INFY --html  Multi-stock dashboard
  python company_screener.py -w watchlist.txt --html  Dashboard from file
  python company_screener.py -w watchlist.txt --serve  Live interactive mode
  python company_screener.py MARUTI --json           Export raw JSON
        """
    )
    parser.add_argument("tickers", nargs="*",
                        help="Ticker symbols (e.g., RELIANCE TCS INFY)")
    parser.add_argument("--watchlist", "-w", type=str, default=None,
                        help="Path to watchlist file (one ticker per line, # comments)")
    parser.add_argument("--brief", "-b", action="store_true",
                        help="Quick summary only (scorecard + flags)")
    parser.add_argument("--html", action="store_true",
                        help="Generate HTML dashboard")
    parser.add_argument("--serve", action="store_true",
                        help="Start live server with interactive add/delete/search")
    parser.add_argument("--port", type=int, default=9000,
                        help="Port for --serve mode (default: 9000)")
    parser.add_argument("--json", action="store_true",
                        help="Export raw data as JSON")
    parser.add_argument("--standalone", "-s", action="store_true",
                        help="Use standalone figures (default: consolidated)")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open HTML report in browser")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output file path")
    parser.add_argument("--sections", type=str, nargs="+", metavar="NAME:FILE",
                        help="Accordion sections as NAME:FILE pairs, e.g. 'Nifty 50:nifty50.txt' 'My Stocks:watchlist.txt'")

    args = parser.parse_args()

    # Build ticker list: CLI args + watchlist file (both can be combined)
    tickers = [t.upper() for t in (args.tickers or [])]
    if args.watchlist:
        try:
            wl = _load_watchlist(args.watchlist)
            print(f"{C.CYAN}📋 Loaded {len(wl)} tickers from {args.watchlist}{C.RESET}")
            tickers.extend(wl)
        except FileNotFoundError as e:
            print(f"{C.RED}❌ {e}{C.RESET}")
            sys.exit(1)

    # Parse --sections (NAME:FILE pairs for accordion grouping)
    sections = None
    if args.sections:
        sections = []
        for spec in args.sections:
            if ":" not in spec:
                print(f"{C.RED}❌ Invalid section format '{spec}'. Use NAME:FILE{C.RESET}")
                sys.exit(1)
            name, filepath = spec.split(":", 1)
            try:
                sec_tickers = _load_watchlist(filepath)
                sections.append({"name": name.strip(), "tickers": sec_tickers})
                tickers.extend(sec_tickers)
                print(f"{C.CYAN}📂 Section '{name.strip()}': {len(sec_tickers)} tickers from {filepath}{C.RESET}")
            except FileNotFoundError as e:
                print(f"{C.RED}❌ {e}{C.RESET}")
                sys.exit(1)

    # Deduplicate while preserving order
    seen = set()
    unique_tickers = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)
    tickers = unique_tickers

    if not tickers:
        parser.error("No tickers provided. Use positional args or --watchlist/-w file.")

    # Fetch and analyze all stocks
    stocks = []
    for idx, ticker in enumerate(tickers):
        if idx > 0:
            time.sleep(1.5)  # polite delay to avoid rate-limiting
        print(f"\n{C.CYAN}{C.BOLD}🔍 Screening {ticker} [{idx+1}/{len(tickers)}]...{C.RESET}")
        print(f"{C.GREY}   Fetching data from screener.in{C.RESET}")
        try:
            data = fetch_full_company_data(ticker, consolidated=not args.standalone)
        except Exception as e:
            print(f"{C.RED}❌ Error fetching {ticker}: {e}{C.RESET}")
            continue
        print(f"{C.GREEN}✅ {data['company_name']}{C.RESET} "
              f"({'Consolidated' if data['is_consolidated'] else 'Standalone'})")
        analysis = analyze(data)
        stocks.append({"data": data, "analysis": analysis, "ticker": ticker})

    if not stocks:
        print(f"\n{C.RED}❌ No stocks could be fetched.{C.RESET}")
        sys.exit(1)

    # Generate charts and Sankey for HTML/serve modes
    if args.html or args.serve:
        for s in stocks:
            t = s["ticker"]
            try:
                s["charts"] = _generate_charts(s["data"], ticker=t)
                print(f"{C.GREEN}📊 Charts: {t}{C.RESET}")
            except Exception as e:
                print(f"{C.GREY}   ⚠ Charts skipped for {t}: {e}{C.RESET}")
                s["charts"] = {}
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from income_sankey import get_period_data, build_sankey
                sankey_data = dict(s["data"])
                sankey_data["annual"] = sankey_data.get("profit_loss", {})
                pl_sankey = get_period_data(sankey_data, "annual")
                fig = build_sankey(
                    pl_sankey, s["data"]["company_name"], t,
                    s["data"]["is_consolidated"], "",
                    s["data"].get("segments"), s["data"].get("expense_breakdown"),
                )
                s["sankey_html"] = fig.to_html(full_html=False, include_plotlyjs=False)
                print(f"{C.GREEN}📈 Sankey: {t}{C.RESET}")
            except Exception as e:
                print(f"{C.GREY}   ⚠ Sankey skipped for {t}: {e}{C.RESET}")
                s["sankey_html"] = ""

    # ── SERVE MODE ──
    if args.serve:
        run_server(stocks, port=args.port, standalone=args.standalone,
                   no_open=args.no_open, sections=sections)
        return

    # JSON export
    if args.json:
        out_dir = Path(__file__).parent / "reports"
        out_dir.mkdir(exist_ok=True)
        for s in stocks:
            t = s["ticker"]
            out_path = str(out_dir / f"screener_{t}.json")
            export = {"data": s["data"], "analysis": s["analysis"]}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(export, f, indent=2, ensure_ascii=False, default=str)
            print(f"{C.GREEN}📁 JSON: {out_path}{C.RESET}")
        return

    # Terminal reports
    for s in stocks:
        print_report(s["data"], s["analysis"], brief=args.brief)

    # HTML dashboard
    if args.html:
        out_dir = Path(__file__).parent / "reports"
        out_dir.mkdir(exist_ok=True)
        if len(stocks) == 1:
            out_path = args.output or str(out_dir / f"screener_{stocks[0]['ticker']}.html")
        else:
            out_path = args.output or str(out_dir / "screener_dashboard.html")

        html = generate_dashboard(stocks, sections=sections)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n{C.GREEN}✅ Dashboard saved: {out_path}{C.RESET}")
        if not args.no_open:
            webbrowser.open(f"file:///{os.path.abspath(out_path)}")
            print(f"{C.BLUE}🌐 Opened in browser{C.RESET}")


if __name__ == "__main__":
    main()

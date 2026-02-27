#!/usr/bin/env python3
"""
Income Statement Sankey Diagram Generator for Indian Companies
=============================================================
Fetches P&L data from screener.in and generates beautiful Sankey flow diagrams
similar to "How They Make Money" / App Economy Insights style.

Usage:
    python income_sankey.py RELIANCE                    # Latest annual, consolidated
    python income_sankey.py TCS --year "Mar 2025"       # Specific year
    python income_sankey.py INFY --quarterly             # Latest quarter
    python income_sankey.py HDFCBANK --standalone        # Standalone (not consolidated)
    python income_sankey.py RELIANCE --compare           # Compare last 2 years side by side
"""

import argparse
import os
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    print("ERROR: plotly not installed. Run: pip install plotly kaleido")
    sys.exit(1)


# ─── Constants ───────────────────────────────────────────────────────────────

SCREENER_BASE = "https://www.screener.in/company"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Color palette inspired by "How They Make Money" / App Economy Insights
COLORS = {
    "revenue":      "#2563EB",   # Blue
    "expenses":     "#DC2626",   # Red
    "gross_profit": "#16A34A",   # Green
    "op_profit":    "#16A34A",   # Green
    "depreciation": "#EA580C",   # Orange
    "interest":     "#B91C1C",   # Dark red
    "other_income": "#0891B2",   # Teal
    "pbt":          "#16A34A",   # Green
    "tax":          "#DC2626",   # Red
    "net_profit":   "#15803D",   # Dark green
    "minority":     "#9333EA",   # Purple
}

LINK_OPACITY = 0.35


# ─── Data Fetching ───────────────────────────────────────────────────────────

def parse_number(text: str) -> float:
    """Parse Indian number format: '1,23,456' → 123456.0"""
    if not text or text.strip() in ("", "-", "—"):
        return 0.0
    text = text.strip().replace(",", "").replace("%", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def fetch_company_data(ticker: str, consolidated: bool = True) -> dict:
    """
    Fetch company financial data from screener.in.
    Returns dict with company info and P&L data for all available periods.
    """
    suffix = "consolidated/" if consolidated else ""
    url = f"{SCREENER_BASE}/{ticker.upper()}/{suffix}"

    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 404:
        # Try without consolidated
        url = f"{SCREENER_BASE}/{ticker.upper()}/"
        resp = requests.get(url, headers=HEADERS, timeout=30)
    
    if resp.status_code != 200:
        raise ValueError(f"Failed to fetch data for {ticker}: HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── If consolidated page has no P&L data, fall back to standalone ──
    if consolidated and suffix:
        test_pl = _parse_pl_section(soup, "profit-loss")
        if not test_pl.get("periods"):
            url = f"{SCREENER_BASE}/{ticker.upper()}/"
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

    # ── Company name ──
    h1 = soup.find("h1")
    company_name = h1.get_text(strip=True) if h1 else ticker.upper()

    # ── Check if consolidated data exists ──
    is_consolidated = "consolidated" in resp.url

    # ── Parse Annual P&L ──
    annual_data = _parse_pl_section(soup, "profit-loss")

    # ── Parse Quarterly P&L ──
    quarterly_data = _parse_pl_section(soup, "quarters")

    # ── Try to get sector info ──
    sector = ""
    sector_links = soup.select("a[href*='/market/']")
    if sector_links:
        sector = sector_links[-1].get_text(strip=True)

    # ── Get key ratios from top card ──
    ratios = {}
    top_ul = soup.select("#top-ratios li")
    for li in top_ul:
        name_el = li.find("span", class_="name")
        val_el = li.find("span", class_="number")
        if name_el and val_el:
            ratios[name_el.get_text(strip=True)] = val_el.get_text(strip=True)

    # ── Get company ID for segment/schedule API calls ──
    company_id = extract_company_id(resp.text)

    # ── Fetch segment names and expense breakdown ──
    segments = []
    expense_breakdown = {}
    if company_id:
        segments = fetch_segments(company_id, "profit-loss", is_consolidated)
        expense_breakdown = fetch_expense_breakdown(
            company_id, "profit-loss", is_consolidated)

    return {
        "ticker": ticker.upper(),
        "company_name": company_name,
        "is_consolidated": is_consolidated,
        "sector": sector,
        "ratios": ratios,
        "annual": annual_data,
        "quarterly": quarterly_data,
        "url": resp.url,
        "company_id": company_id,
        "segments": segments,
        "expense_breakdown": expense_breakdown,
    }


def _parse_pl_section(soup: BeautifulSoup, section_id: str) -> dict:
    """Parse a P&L table section (annual or quarterly)."""
    section = soup.find("section", id=section_id)
    if not section:
        return {}

    table = section.find("table")
    if not table:
        return {}

    rows = table.find_all("tr")
    if not rows:
        return {}

    # ── Header row → period labels ──
    header_cells = rows[0].find_all(["th", "td"])
    periods = []
    for cell in header_cells[1:]:  # Skip first (label) column
        text = cell.get_text(strip=True)
        if text:
            periods.append(text)

    # ── Data rows ──
    result = {"periods": periods, "rows": {}}
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        label = cells[0].get_text(strip=True).rstrip("+").strip()
        if not label:
            continue

        values = []
        for cell in cells[1:]:
            values.append(cell.get_text(strip=True))

        result["rows"][label] = values

    return result


def fetch_segments(company_id: str, section: str = "profit-loss",
                   consolidated: bool = True) -> list[str]:
    """
    Fetch product segment names from screener.in segment API.
    Returns list of segment names (values require premium, names are free).
    """
    params = f"?consolidated=true" if consolidated else ""
    url = (f"https://www.screener.in/api/segments/{company_id}"
           f"/{section}/1/{params}")
    try:
        resp = requests.get(url, headers={**HEADERS,
                            "X-Requested-With": "XMLHttpRequest"}, timeout=10)
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


def fetch_expense_breakdown(company_id: str, section: str = "profit-loss",
                            consolidated: bool = True) -> dict:
    """
    Fetch expense breakdown percentages from screener.in schedules API.
    Returns dict like {'Material Cost': 65, 'Employee Cost': 3, ...}
    for the latest available period.
    """
    params = f"?parent=Expenses&section={section}"
    if consolidated:
        params += "&consolidated"
    url = f"https://www.screener.in/api/company/{company_id}/schedules/{params}"
    try:
        resp = requests.get(url, headers={**HEADERS,
                            "X-Requested-With": "XMLHttpRequest"}, timeout=10)
        if resp.status_code != 200:
            return {}
        import json
        data = json.loads(resp.text)
        result = {}
        for key, val in data.items():
            if not isinstance(val, dict):
                continue
            # Get the latest value
            items = [(k, v) for k, v in val.items() if k != "isExpandable"]
            if items:
                latest_val = items[-1][1]
                # Parse percentage string like "65%"
                pct = parse_number(str(latest_val))
                clean_name = key.replace(" %", "").strip()
                if pct > 0:
                    result[clean_name] = pct
        return result
    except Exception:
        return {}


def extract_company_id(html_text: str) -> str:
    """Extract company ID from screener.in page HTML."""
    m = re.search(r'/api/company/(\d+)/', html_text)
    return m.group(1) if m else ""


def get_period_data(data: dict, period_type: str = "annual",
                    period_label: str = None) -> dict:
    """
    Extract P&L data for a specific period.
    Returns a clean dict with all line items as numbers.
    """
    section = data.get(period_type, {})
    if not section:
        raise ValueError(f"No {period_type} data found for {data['ticker']}")

    periods = section.get("periods", [])
    rows = section.get("rows", {})

    if not periods:
        raise ValueError("No period columns found")

    # ── Find the right column index ──
    if period_label:
        # Fuzzy match
        idx = None
        for i, p in enumerate(periods):
            if period_label.lower() in p.lower():
                idx = i
                break
        if idx is None:
            raise ValueError(
                f"Period '{period_label}' not found. Available: {periods}"
            )
    else:
        # Default to latest (last column, but skip TTM if it's unreliable)
        idx = len(periods) - 1

    chosen_period = periods[idx]

    # ── Extract values ──
    def get_val(label: str) -> float:
        for key, vals in rows.items():
            if label.lower() in key.lower():
                if idx < len(vals):
                    return parse_number(vals[idx])
        return 0.0

    def get_exact(label: str) -> float:
        """Exact match (for 'Interest' vs 'Other Interest')."""
        for key, vals in rows.items():
            if key.strip().lower() == label.lower():
                if idx < len(vals):
                    return parse_number(vals[idx])
        return 0.0

    # ── Detect if this is a bank/NBFC (has "Revenue" but no "Sales") ──
    has_sales = any("sales" in k.lower() for k in rows.keys())
    has_revenue = any("revenue" in k.lower() and "sales" not in k.lower() for k in rows.keys())
    has_financing = any("financing" in k.lower() for k in rows.keys())
    is_bank = (has_revenue and not has_sales) or has_financing

    if is_bank:
        # ── Bank/NBFC P&L Structure ──
        # Revenue = total income (interest + fee income)
        # Interest = interest paid to depositors
        # Expenses = operating expenses
        # Financing Profit = Revenue - Interest - Expenses (can be negative)
        # Other Income = fee/trading income
        # PBT = Financing Profit + Other Income - Depreciation
        revenue = get_val("Revenue")
        interest_paid = get_exact("Interest")
        expenses = get_val("Expenses")
        financing_profit = get_val("Financing Profit") or get_val("Financing Margin")
        other_income = get_val("Other Income")
        depreciation = get_val("Depreciation")
        pbt = get_val("Profit before tax")
        tax_pct = get_exact("Tax %")
        net_profit = get_val("Net Profit")
        eps = get_val("EPS")
        
        # For banks: total income = revenue + other income
        total_income = revenue + other_income
        # NII (Net Interest Income) = Revenue - Interest Paid
        nii = revenue - interest_paid
        # Operating profit proxy = NII + Other Income - Expenses
        op_profit = nii + other_income - expenses
        
        sales = total_income  # Use total income as "revenue" for display
        operating_profit = op_profit
        interest = interest_paid
        opm = (op_profit / total_income * 100) if total_income > 0 else 0

    else:
        # ── Standard Company P&L ──
        sales = get_val("Sales")
        expenses = get_val("Expenses")
        operating_profit = get_val("Operating Profit")
        other_income = get_val("Other Income")
        interest = get_exact("Interest")
        depreciation = get_val("Depreciation")
        pbt = get_val("Profit before tax")
        tax_pct = get_exact("Tax %")
        net_profit = get_val("Net Profit")
        eps = get_val("EPS")
        opm = (operating_profit / sales * 100) if sales > 0 else 0

    # ── Compute derived values ──
    if not is_bank:
        if operating_profit == 0 and sales > 0 and expenses > 0:
            operating_profit = sales - expenses
        if expenses == 0 and sales > 0 and operating_profit > 0:
            expenses = sales - operating_profit
        opm = (operating_profit / sales * 100) if sales > 0 else 0

    # Tax amount
    if pbt > 0 and tax_pct > 0:
        tax_amount = pbt * (tax_pct / 100.0)
    elif pbt > 0 and net_profit > 0:
        tax_amount = pbt - net_profit
    else:
        tax_amount = 0

    # EBIT = Operating Profit - Depreciation
    ebit = operating_profit - depreciation

    # Verify PBT ≈ EBIT - Interest + Other Income
    computed_pbt = ebit - interest + other_income
    
    # Net profit check (may include minority interest)
    minority_interest = 0
    if pbt > 0 and net_profit > 0:
        implied_net = pbt - tax_amount
        if abs(implied_net - net_profit) > 1:
            minority_interest = implied_net - net_profit

    opm = (operating_profit / sales * 100) if sales > 0 else 0
    npm = (net_profit / sales * 100) if sales > 0 else 0
    ebitda_margin = opm
    
    # Try to get previous period for YoY comparison
    prev_sales = 0
    prev_net_profit = 0
    if idx > 0:
        search_key = "revenue" if is_bank else "sales"
        for key, vals in rows.items():
            if search_key in key.lower():
                if idx - 1 < len(vals):
                    prev_sales = parse_number(vals[idx - 1])
                break
        for key, vals in rows.items():
            if "net profit" in key.lower():
                if idx - 1 < len(vals):
                    prev_net_profit = parse_number(vals[idx - 1])
                break

    # For banks, use total_income for YoY if available
    if is_bank and prev_sales > 0:
        # prev_sales is just revenue; add prev other_income for fair comparison
        prev_oi = 0
        for key, vals in rows.items():
            if "other income" in key.lower():
                if idx - 1 < len(vals):
                    prev_oi = parse_number(vals[idx - 1])
                break
        prev_total = prev_sales + prev_oi
        yoy_sales = ((sales / prev_total - 1) * 100) if prev_total > 0 else 0
    else:
        yoy_sales = ((sales / prev_sales - 1) * 100) if prev_sales > 0 else 0
    yoy_profit = ((net_profit / prev_net_profit - 1) * 100) if prev_net_profit > 0 else 0

    return {
        "period": chosen_period,
        "period_type": period_type,
        "is_bank": is_bank,
        "sales": sales,
        "expenses": expenses,
        "operating_profit": operating_profit,
        "opm": opm,
        "other_income": other_income,
        "interest": interest,
        "depreciation": depreciation,
        "ebit": ebit,
        "pbt": pbt,
        "tax_amount": tax_amount,
        "tax_pct": tax_pct,
        "net_profit": net_profit,
        "minority_interest": minority_interest,
        "eps": eps,
        "ebitda_margin": ebitda_margin,
        "npm": npm,
        "yoy_sales": yoy_sales,
        "yoy_profit": yoy_profit,
        # Bank-specific
        "nii": nii if is_bank else 0,
        "interest_income": revenue if is_bank else 0,
        "fee_income": other_income if is_bank else 0,
    }


# ─── Formatting Helpers ─────────────────────────────────────────────────────

def fmt_indian(value: float) -> str:
    """Format number with Indian comma system (12,34,567)."""
    s = f"{int(abs(value)):,}"
    # Convert Western commas to Indian: 1,234,567 → 12,34,567
    parts = s.split(",")
    if len(parts) <= 2:
        return s
    # Last group stays 3 digits, rest become 2-digit groups
    last = parts[-1]  # "567"
    middle = ",".join(parts[:-1])  # "1,234"
    # Re-split middle into 2-digit groups from right
    mid_num = middle.replace(",", "")
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
        # Lakh Crores (spelled out, no ambiguity)
        lakh_cr = abs_val / 100000
        if lakh_cr >= 10:
            return f"{sign}₹{lakh_cr:.0f} Lakh Cr"
        return f"{sign}₹{lakh_cr:.1f} Lakh Cr"
    elif abs_val >= 1:
        # Plain Crores with Indian commas
        return f"{sign}₹{fmt_indian(abs_val)} Cr"
    else:
        return f"{sign}₹{abs_val:.1f} Cr"


def fmt_pct(value: float) -> str:
    """Format percentage."""
    if value == 0:
        return ""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.0f}%"


def fmt_yoy(value: float) -> str:
    """Format YoY change."""
    if value == 0:
        return ""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.0f}% Y/Y"


# ─── Sankey Diagram Builder ─────────────────────────────────────────────────

def build_sankey(pl: dict, company_name: str, ticker: str, 
                 is_consolidated: bool, sector: str = "",
                 segments: list = None, expense_breakdown: dict = None) -> go.Figure:
    """
    Build a Sankey diagram from P&L data.
    Handles both standard companies and banks/NBFCs.
    """
    is_bank = pl.get("is_bank", False)
    
    if is_bank:
        return _build_bank_sankey(pl, company_name, ticker, is_consolidated)
    else:
        return _build_standard_sankey(pl, company_name, ticker, is_consolidated,
                                     segments=segments or [],
                                     expense_breakdown=expense_breakdown or {})


def _build_standard_sankey(pl: dict, company_name: str, ticker: str,
                           is_consolidated: bool,
                           segments: list = None,
                           expense_breakdown: dict = None) -> go.Figure:
    """
    Standard company Sankey — strict vertical zoning to prevent overlap.

    Y-zones (top=0, bottom=1):
      PROFIT ZONE   y 0.00-0.18  : EBITDA, OtherIncome, PBT, Net Profit
      LEAK ZONE     y 0.20-0.38  : Dep, Interest, Tax
      REVENUE       y 0.35       : Revenue node (bridge)
      EXPENSE ZONE  y 0.44-0.97  : OpEx, expense breakdown
    """
    segments = segments or []
    expense_breakdown = expense_breakdown or {}

    sales = pl["sales"]
    expenses = pl["expenses"]
    op_profit = pl["operating_profit"]
    depreciation = pl["depreciation"]
    interest = pl["interest"]
    other_income = pl["other_income"]
    pbt = pl["pbt"]
    tax = pl["tax_amount"]
    net_profit = pl["net_profit"]
    minority = pl["minority_interest"]

    # ── Expense sub-categories ──
    exp_cats = {}
    for cat, pct in expense_breakdown.items():
        exp_cats[cat] = sales * pct / 100.0

    has_exp_breakdown = len(exp_cats) >= 2
    if has_exp_breakdown:
        computed_total = sum(exp_cats.values())
        if computed_total > 0 and abs(computed_total - expenses) > 1:
            scale = expenses / computed_total
            exp_cats = {k: v * scale for k, v in exp_cats.items()}

    has_segments = len(segments) >= 2
    n_seg = len(segments) if has_segments else 0

    SEG_COLORS = [
        "#2563EB", "#7C3AED", "#059669", "#D97706", "#DC2626",
        "#0891B2", "#4F46E5", "#BE185D", "#15803D", "#92400E",
    ]
    EXP_COLORS = {
        "Material Cost": "#B91C1C",
        "Employee Cost": "#9333EA",
        "Manufacturing Cost": "#EA580C",
        "Other Cost": "#6B7280",
    }

    # ── X columns ──
    if has_segments:
        X = {
            "seg": 0.001, "rev": 0.14, "split": 0.28,
            "exp_detail": 0.44, "dep": 0.54, "oi": 0.62,
            "pbt": 0.72, "tax": 0.84, "pat": 0.95,
        }
    else:
        X = {
            "rev": 0.001, "split": 0.20,
            "exp_detail": 0.40, "dep": 0.52, "oi": 0.60,
            "pbt": 0.72, "tax": 0.84, "pat": 0.95,
        }

    # ── Fixed Y positions — strict zones, never overlap ──
    Y_PROFIT  = 0.001   # EBITDA, Net Profit (top)
    Y_OI      = 0.001   # Other Income
    Y_PBT     = 0.06    # PBT
    Y_DEP     = 0.18    # Depreciation (leak zone top)
    Y_INT     = 0.28    # Interest (leak zone bottom)
    Y_TAX     = 0.22    # Tax (leak zone)
    Y_REV     = 0.35    # Revenue (bridge)
    Y_OPEX    = 0.50    # OpEx
    Y_EXP_TOP = 0.44    # Expense breakdown starts
    Y_EXP_BOT = 0.97    # Expense breakdown ends

    # ── Node helpers ──
    nodes = []
    node_idx = {}

    def add_node(name, color, x, y, label=None):
        idx = len(nodes)
        node_idx[name] = idx
        nodes.append({
            "label": label or name, "color": color,
            "x": max(0.001, min(0.999, x)),
            "y": max(0.001, min(0.999, y)),
        })
        return idx

    has_other_income = other_income > 0
    has_interest = interest > 0
    has_depreciation = depreciation > 0
    has_minority = abs(minority) > 1

    # ═══════ NODES ═══════

    # Segments
    if has_segments:
        y_spacing = min(0.85 / n_seg, 0.12)
        y_start = 0.5 - (n_seg - 1) * y_spacing / 2
        for i, seg_name in enumerate(segments):
            add_node(f"seg_{i}", SEG_COLORS[i % len(SEG_COLORS)],
                     X["seg"], y_start + i * y_spacing, seg_name)

    # Revenue
    yoy_str = f"\n{fmt_yoy(pl['yoy_sales'])}" if pl['yoy_sales'] else ""
    add_node("Revenue", COLORS["revenue"], X["rev"], Y_REV,
             f"Revenue\n{fmt_cr(sales)}{yoy_str}")

    # EBITDA (profit zone — TOP)
    add_node("EBITDA", COLORS["op_profit"], X["split"], Y_PROFIT,
             f"Operating Profit\n{fmt_cr(op_profit)}\n{pl['opm']:.0f}% margin")

    # OpEx (expense zone — BOTTOM)
    exp_pct = (expenses / sales * 100) if sales > 0 else 0
    add_node("OpEx", COLORS["expenses"], X["split"], Y_OPEX,
             f"Operating\nExpenses\n({fmt_cr(expenses)})\n{exp_pct:.0f}% of rev")

    # Expense breakdown (expense zone — BOTTOM, terminal nodes)
    if has_exp_breakdown:
        sorted_cats = sorted(exp_cats.items(), key=lambda x: -x[1])
        total_exp = sum(v for _, v in sorted_cats)
        n_cats = len(sorted_cats)
        cat_y_range = Y_EXP_BOT - Y_EXP_TOP

        cumulative = 0
        for i, (cat_name, cat_amount) in enumerate(sorted_cats):
            band_frac = cat_amount / total_exp if total_exp > 0 else 1 / n_cats
            cat_y = Y_EXP_TOP + (cumulative + band_frac / 2) * cat_y_range
            cumulative += band_frac
            cat_pct = (cat_amount / sales * 100) if sales > 0 else 0
            cat_color = EXP_COLORS.get(cat_name, "#6B7280")
            display_name = (cat_name.replace("Manufacturing Cost", "Manufacturing")
                                    .replace("Material Cost", "Materials")
                                    .replace("Employee Cost", "Employee")
                                    .replace("Other Cost", "Other Costs"))
            add_node(f"exp_{i}", cat_color, X["exp_detail"], cat_y,
                     f"{display_name}\n({fmt_cr(cat_amount)})\n{cat_pct:.0f}% of rev")

    # Dep + Interest (leak zone — MIDDLE, strictly above expense zone)
    if has_depreciation:
        dep_pct = (depreciation / sales * 100) if sales > 0 else 0
        add_node("Dep", COLORS["depreciation"], X["dep"], Y_DEP,
                 f"Depreciation\n({fmt_cr(depreciation)})\n{dep_pct:.0f}% of rev")
    if has_interest:
        int_pct = (interest / sales * 100) if sales > 0 else 0
        add_node("Interest", COLORS["interest"], X["dep"], Y_INT,
                 f"Interest\n({fmt_cr(interest)})\n{int_pct:.0f}% of rev")

    # Other Income (profit zone — TOP)
    if has_other_income:
        oi_pct = (other_income / sales * 100) if sales > 0 else 0
        add_node("OtherInc", COLORS["other_income"], X["oi"], Y_OI,
                 f"Other Income\n{fmt_cr(other_income)}\n{oi_pct:.0f}% of rev")

    # PBT (profit zone)
    pbt_margin = (pbt / sales * 100) if sales > 0 else 0
    add_node("PBT", COLORS["pbt"], X["pbt"], Y_PBT,
             f"Profit Before Tax\n{fmt_cr(pbt)}\n{pbt_margin:.0f}% margin")

    # Tax (leak zone)
    add_node("Tax", COLORS["tax"], X["tax"], Y_TAX,
             f"Tax\n({fmt_cr(tax)})\n{pl['tax_pct']:.0f}%")

    # Net Profit (profit zone — TOP)
    npm_label = f"{pl['npm']:.0f}% margin" if pl['npm'] != 0 else ""
    yoy_label = f"\n{fmt_yoy(pl['yoy_profit'])}" if pl['yoy_profit'] else ""
    add_node("PAT", COLORS["net_profit"], X["pat"], Y_PROFIT,
             f"Net Profit\n{fmt_cr(net_profit)}\n{npm_label}{yoy_label}")

    if has_minority and minority > 0:
        add_node("Minority", COLORS["minority"], X["tax"],
                 min(0.92, Y_TAX + 0.18),
                 f"Minority\nInterest\n({fmt_cr(minority)})")

    # ═══════ LINKS ═══════
    links = []

    def add_link(src, tgt, value, color_key=None, color_hex=None):
        if value <= 0 or src not in node_idx or tgt not in node_idx:
            return
        hex_color = color_hex or COLORS.get(color_key, "#888888")
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        links.append({
            "source": node_idx[src], "target": node_idx[tgt],
            "value": value,
            "color": f"rgba({r},{g},{b},{LINK_OPACITY})",
        })

    # Segments → Revenue
    if has_segments:
        seg_share = sales / n_seg
        for i in range(n_seg):
            add_link(f"seg_{i}", "Revenue", seg_share,
                     color_hex=SEG_COLORS[i % len(SEG_COLORS)])

    # Revenue → EBITDA (top path) then OpEx (bottom path)
    add_link("Revenue", "EBITDA", op_profit, "op_profit")
    add_link("Revenue", "OpEx", expenses, "expenses")

    # OpEx → Expense breakdown (stays in bottom zone)
    if has_exp_breakdown:
        sorted_cats = sorted(exp_cats.items(), key=lambda x: -x[1])
        for i, (cat_name, cat_amount) in enumerate(sorted_cats):
            add_link("OpEx", f"exp_{i}", cat_amount,
                     color_hex=EXP_COLORS.get(cat_name, "#6B7280"))

    # EBITDA → Dep + Interest (leak zone) + PBT (profit zone)
    if has_depreciation:
        add_link("EBITDA", "Dep", depreciation, "depreciation")
    if has_interest:
        add_link("EBITDA", "Interest", interest, "interest")
    ebit_to_pbt = op_profit - depreciation - interest
    if ebit_to_pbt > 0:
        add_link("EBITDA", "PBT", ebit_to_pbt, "op_profit")

    # Other Income → PBT
    if has_other_income:
        add_link("OtherInc", "PBT", other_income, "other_income")

    # PBT → Tax + Net Profit + Minority
    if tax > 0:
        add_link("PBT", "Tax", tax, "tax")
    add_link("PBT", "PAT", net_profit, "net_profit")
    if has_minority and minority > 0:
        add_link("PBT", "Minority", minority, "minority")

    # ═══════ FIGURE ═══════
    fig = go.Figure(data=[go.Sankey(
        arrangement="snap",
        node=dict(
            pad=28, thickness=28,
            line=dict(color="#E5E7EB", width=1),
            label=[n["label"] for n in nodes],
            color=[n["color"] for n in nodes],
            x=[n["x"] for n in nodes],
            y=[n["y"] for n in nodes],
            hovertemplate="%{label}<extra></extra>",
        ),
        link=dict(
            source=[l["source"] for l in links],
            target=[l["target"] for l in links],
            value=[l["value"] for l in links],
            color=[l["color"] for l in links],
            hovertemplate=(
                "%{source.label} → %{target.label}<br>"
                "₹%{value:,.0f} Cr<extra></extra>"
            ),
        ),
        textfont=dict(size=12, color="#1F2937", family="Segoe UI, Arial"),
    )])

    return _apply_layout(fig, pl, company_name, ticker, is_consolidated, sales)


def _apply_layout(fig: go.Figure, pl: dict, company_name: str, ticker: str,
                  is_consolidated: bool, sales: float) -> go.Figure:
    """Apply shared layout to the Sankey figure."""
    period_label = pl["period"]
    cons_label = "Consolidated" if is_consolidated else "Standalone"
    
    fy_info = ""
    if "Mar" in period_label:
        year = period_label.split()[-1]
        fy_info = f"FY{year}"
    elif period_label == "TTM":
        fy_info = "Trailing Twelve Months"
    else:
        fy_info = f"Q ending {period_label}"

    rev_label = "Total Income" if pl.get("is_bank") else "Revenue"
    margin_label = "Op. Margin" if pl.get("is_bank") else "EBITDA Margin"

    fig.update_layout(
        title=dict(
            text=(
                f"<b style='font-size:30px'>{company_name}</b> "
                f"<b style='font-size:30px; color:#6B7280'>Income Statement</b><br>"
                f"<span style='font-size:15px; color:#9CA3AF'>"
                f"{cons_label} · {fy_info} · Figures in ₹ Crores</span>"
            ),
            x=0.5,
            xanchor="center",
            font=dict(size=20, family="Segoe UI, Arial, sans-serif"),
        ),
        font=dict(size=12, family="Segoe UI, Arial, sans-serif", color="#1F2937"),
        paper_bgcolor="white",
        plot_bgcolor="white",
        height=680,
        width=1350,
        margin=dict(l=20, r=80, t=100, b=60),
        annotations=[
            dict(
                text=(
                    f"<b>{rev_label}:</b> {fmt_cr(sales)} ({fmt_yoy(pl['yoy_sales'])}) · "
                    f"<b>{margin_label}:</b> {pl['opm']:.1f}% · "
                    f"<b>Net Margin:</b> {pl['npm']:.1f}% · "
                    f"<b>EPS:</b> ₹{pl['eps']:.1f}"
                ),
                x=0.5, y=-0.04,
                xref="paper", yref="paper",
                xanchor="center",
                font=dict(size=13, color="#4B5563"),
                showarrow=False,
            ),
            dict(
                text=(
                    f"Source: screener.in/{ticker} · "
                    f"Generated {datetime.now().strftime('%d %b %Y')}"
                ),
                x=0.99, y=-0.07,
                xref="paper", yref="paper",
                xanchor="right",
                font=dict(size=10, color="#D1D5DB"),
                showarrow=False,
            ),
        ],
    )
    return fig


def _build_bank_sankey(pl: dict, company_name: str, ticker: str,
                       is_consolidated: bool) -> go.Figure:
    """
    Bank/NBFC Sankey diagram.
    
    Interest Income ──┬── Interest Paid ──── (cost)
                      └── NII ──┐
                                ├── ► Total Income ──┬── OpEx
    Other Income ───────────────┘                    └── PBT ──┬── Tax
                                                               └── Net Profit
    """
    total_income = pl["sales"]  # = revenue + other_income for banks
    interest_income = pl.get("interest_income", 0)
    interest_paid = pl["interest"]
    other_income = pl["other_income"]
    expenses = pl["expenses"]
    depreciation = pl["depreciation"]
    pbt = pl["pbt"]
    tax = pl["tax_amount"]
    net_profit = pl["net_profit"]
    minority = pl["minority_interest"]
    nii = pl.get("nii", interest_income - interest_paid)
    
    # NII + Other Income - Expenses = Pre-provision profit
    pre_provision = nii + other_income - expenses
    
    nodes = []
    node_idx = {}
    links = []
    
    def add_node(name, color, x, y, label=None):
        idx = len(nodes)
        node_idx[name] = idx
        nodes.append({
            "label": label or name,
            "color": color,
            "x": max(0.001, min(0.999, x)),
            "y": max(0.001, min(0.999, y)),
        })
        return idx

    def add_link(src, tgt, value, color_key):
        if value <= 0 or src not in node_idx or tgt not in node_idx:
            return
        hex_color = COLORS.get(color_key, "#888888")
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        rgba = f"rgba({r},{g},{b},{LINK_OPACITY})"
        links.append({
            "source": node_idx[src],
            "target": node_idx[tgt],
            "value": value,
            "color": rgba,
        })

    # ── Nodes ──
    yoy_str = f"\n{fmt_yoy(pl['yoy_sales'])}" if pl['yoy_sales'] else ""
    add_node("IntIncome", COLORS["revenue"], 0.001, 0.35,
             f"Interest\nIncome\n{fmt_cr(interest_income)}")
    
    add_node("IntPaid", COLORS["interest"], 0.22, 0.7,
             f"Interest\nPaid\n({fmt_cr(interest_paid)})")
    nii_pct = (nii / interest_income * 100) if interest_income > 0 else 0
    add_node("NII", "#0D9488", 0.22, 0.2,
             f"Net Interest\nIncome\n{fmt_cr(nii)}\n{nii_pct:.0f}% NIM")
    
    if other_income > 0:
        add_node("OtherInc", COLORS["other_income"], 0.35, 0.05,
                 f"Other Income\n{fmt_cr(other_income)}")

    add_node("OpEx", COLORS["expenses"], 0.52, 0.7,
             f"Operating\nExpenses\n({fmt_cr(expenses)})")
    if depreciation > 0:
        add_node("Dep", COLORS["depreciation"], 0.52, 0.9,
                 f"Depreciation\n({fmt_cr(depreciation)})")

    pbt_margin = (pbt / total_income * 100) if total_income > 0 else 0
    add_node("PBT", COLORS["pbt"], 0.72, 0.2,
             f"Profit Before\nTax\n{fmt_cr(pbt)}\n{pbt_margin:.0f}% margin")

    add_node("Tax", COLORS["tax"], 0.92, 0.55,
             f"Tax\n({fmt_cr(tax)})\n{pl['tax_pct']:.0f}%")
    
    npm_label = f"{pl['npm']:.0f}% margin" if pl['npm'] != 0 else ""
    add_node("PAT", COLORS["net_profit"], 0.999, 0.12,
             f"Net Profit\n{fmt_cr(net_profit)}\n{npm_label}")

    if abs(minority) > 1 and minority > 0:
        add_node("Minority", COLORS["minority"], 0.92, 0.85,
                 f"Minority\n({fmt_cr(minority)})")

    # ── Links ──
    # Interest Income → Interest Paid + NII
    add_link("IntIncome", "IntPaid", interest_paid, "interest")
    add_link("IntIncome", "NII", nii, "op_profit")
    
    # NII → OpEx + remainder to PBT area
    # Other Income also flows to PBT area
    # Combined: NII + OI → OpEx + Dep + PBT
    total_op_income = nii + other_income
    
    add_link("NII", "OpEx", min(nii, expenses), "expenses")
    if other_income > 0:
        opex_from_oi = max(0, expenses - nii)
        pbt_from_oi = other_income - opex_from_oi
        if opex_from_oi > 0:
            add_link("OtherInc", "OpEx", opex_from_oi, "expenses")
        if pbt_from_oi > 0:
            add_link("OtherInc", "PBT", pbt_from_oi, "other_income")
    
    nii_to_pbt = max(0, nii - expenses)
    if nii_to_pbt > 0:
        add_link("NII", "PBT", nii_to_pbt, "op_profit")
    
    if depreciation > 0:
        add_link("NII", "Dep", min(depreciation, max(0, nii - expenses)), "depreciation")

    # PBT → Tax + Net Profit
    if tax > 0:
        add_link("PBT", "Tax", tax, "tax")
    add_link("PBT", "PAT", net_profit, "net_profit")
    if abs(minority) > 1 and minority > 0:
        add_link("PBT", "Minority", minority, "minority")

    # ── Create figure ──
    fig = go.Figure(data=[go.Sankey(
        arrangement="snap",
        node=dict(
            pad=30, thickness=30,
            line=dict(color="#E5E7EB", width=1),
            label=[n["label"] for n in nodes],
            color=[n["color"] for n in nodes],
            x=[n["x"] for n in nodes],
            y=[n["y"] for n in nodes],
            hovertemplate="%{label}<extra></extra>",
        ),
        link=dict(
            source=[l["source"] for l in links],
            target=[l["target"] for l in links],
            value=[l["value"] for l in links],
            color=[l["color"] for l in links],
            hovertemplate="%{source.label} → %{target.label}<br>₹%{value:,.0f} Cr<extra></extra>",
        ),
        textfont=dict(size=12, color="#1F2937", family="Segoe UI, Arial"),
    )])

    return _apply_layout(fig, pl, company_name, ticker, is_consolidated, total_income)


# ─── HTML Wrapper (for richer output) ────────────────────────────────────────

def create_html_report(fig: go.Figure, pl: dict, company_name: str,
                       ticker: str, output_path: str) -> str:
    """Create a standalone HTML with the Sankey chart + summary table."""
    
    sankey_html = fig.to_html(full_html=False, include_plotlyjs="cdn")
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{company_name} - Income Statement Sankey</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: #F9FAFB;
            color: #1F2937;
            padding: 20px;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .chart-container {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.08);
            padding: 20px;
            margin-bottom: 24px;
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .summary-card {{
            background: white;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.06);
            text-align: center;
        }}
        .summary-card .label {{
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #6B7280;
            margin-bottom: 8px;
        }}
        .summary-card .value {{
            font-size: 24px;
            font-weight: 700;
        }}
        .summary-card .sub {{
            font-size: 12px;
            margin-top: 4px;
        }}
        .green {{ color: #16A34A; }}
        .red {{ color: #DC2626; }}
        .blue {{ color: #2563EB; }}
        .footer {{
            text-align: center;
            color: #9CA3AF;
            font-size: 12px;
            padding: 20px 0;
        }}
        .waterfall {{
            background: white;
            border-radius: 12px;
            padding: 20px 28px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.06);
            margin-bottom: 24px;
        }}
        .waterfall h3 {{
            font-size: 16px;
            color: #374151;
            margin-bottom: 16px;
        }}
        .waterfall-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid #F3F4F6;
        }}
        .waterfall-row:last-child {{ border-bottom: none; }}
        .waterfall-row.total {{
            font-weight: 700;
            border-top: 2px solid #E5E7EB;
            border-bottom: none;
            padding-top: 12px;
        }}
        .waterfall-bar {{
            height: 8px;
            border-radius: 4px;
            min-width: 4px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Summary Cards -->
        <div class="summary-grid">
            <div class="summary-card">
                <div class="label">Revenue</div>
                <div class="value blue">{fmt_cr(pl['sales'])}</div>
                <div class="sub {'green' if pl['yoy_sales'] > 0 else 'red'}">{fmt_yoy(pl['yoy_sales'])}</div>
            </div>
            <div class="summary-card">
                <div class="label">Operating Profit</div>
                <div class="value green">{fmt_cr(pl['operating_profit'])}</div>
                <div class="sub">{pl['opm']:.1f}% margin</div>
            </div>
            <div class="summary-card">
                <div class="label">Net Profit</div>
                <div class="value green">{fmt_cr(pl['net_profit'])}</div>
                <div class="sub {'green' if pl['yoy_profit'] > 0 else 'red'}">{fmt_yoy(pl['yoy_profit'])}</div>
            </div>
            <div class="summary-card">
                <div class="label">Net Margin</div>
                <div class="value {'green' if pl['npm'] > 15 else 'blue'}">{pl['npm']:.1f}%</div>
                <div class="sub">EPS: ₹{pl['eps']:.1f}</div>
            </div>
        </div>

        <!-- Sankey Chart -->
        <div class="chart-container">
            {sankey_html}
        </div>

        <!-- P&L Waterfall -->
        <div class="waterfall">
            <h3>P&L Waterfall — {pl['period']}</h3>
            {_build_waterfall_html(pl)}
        </div>

        <div class="footer">
            Data source: screener.in/{ticker.upper()} | 
            Generated on {datetime.now().strftime('%d %b %Y, %I:%M %p')} |
            Figures in ₹ Crores
        </div>
    </div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path


def _build_waterfall_html(pl: dict) -> str:
    """Build an HTML waterfall breakdown."""
    sales = pl["sales"]
    
    rows = [
        ("Revenue", pl["sales"], "blue", False),
        ("Operating Expenses", -pl["expenses"], "red", False),
        ("Operating Profit (EBITDA)", pl["operating_profit"], "green", True),
        ("Depreciation", -pl["depreciation"], "red", False),
        ("Interest", -pl["interest"], "red", False),
        ("Other Income", pl["other_income"], "blue", False),
        ("Profit Before Tax", pl["pbt"], "green", True),
        ("Tax", -pl["tax_amount"], "red", False),
    ]
    
    if abs(pl["minority_interest"]) > 1:
        rows.append(("Minority Interest", -pl["minority_interest"], "red", False))
    
    rows.append(("Net Profit (PAT)", pl["net_profit"], "green", True))

    html_parts = []
    for label, value, color, is_total in rows:
        abs_val = abs(value)
        pct_of_rev = (abs_val / sales * 100) if sales > 0 else 0
        bar_width = min(pct_of_rev * 2, 100)  # Scale for visual
        sign = "" if value >= 0 else "-"
        css_class = "waterfall-row total" if is_total else "waterfall-row"
        bar_color = "#16A34A" if value >= 0 else "#DC2626"
        
        html_parts.append(f"""
            <div class="{css_class}">
                <span style="flex:1">{label}</span>
                <span style="width:100px; text-align:right; color:{bar_color}">
                    {sign}₹{abs_val:,.0f}
                </span>
                <span style="width:70px; text-align:right; color:#6B7280; font-size:12px">
                    {pct_of_rev:.1f}%
                </span>
                <span style="width:120px; padding-left:12px">
                    <div class="waterfall-bar" 
                         style="width:{bar_width}%; background:{bar_color}"></div>
                </span>
            </div>""")

    return "\n".join(html_parts)


# ─── Main CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate Income Statement Sankey diagrams for Indian companies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python income_sankey.py RELIANCE                   Latest annual, consolidated
  python income_sankey.py TCS --year "Mar 2025"      Specific fiscal year
  python income_sankey.py INFY --quarterly            Latest quarter
  python income_sankey.py HDFCBANK --standalone       Standalone figures
  python income_sankey.py RELIANCE --list             List available periods
  python income_sankey.py TCS --png                   Also save as PNG image
        """
    )
    parser.add_argument("ticker", help="BSE/NSE ticker symbol (e.g., RELIANCE, TCS, INFY)")
    parser.add_argument("--quarterly", "-q", action="store_true",
                        help="Use quarterly data instead of annual")
    parser.add_argument("--year", "-y", type=str, default=None,
                        help="Specific period label (e.g., 'Mar 2025', 'Dec 2024')")
    parser.add_argument("--standalone", "-s", action="store_true",
                        help="Use standalone figures (default: consolidated)")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available periods and exit")
    parser.add_argument("--png", action="store_true",
                        help="Also export as PNG image")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output file path (default: auto-generated)")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open in browser")

    args = parser.parse_args()
    ticker = args.ticker.upper()

    print(f"📊 Fetching data for {ticker} from screener.in...")
    try:
        data = fetch_company_data(ticker, consolidated=not args.standalone)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

    company = data["company_name"]
    print(f"✅ Found: {company}")

    period_type = "quarterly" if args.quarterly else "annual"

    # ── List mode ──
    if args.list:
        section = data.get(period_type, {})
        periods = section.get("periods", [])
        print(f"\nAvailable {period_type} periods:")
        for p in periods:
            print(f"  • {p}")
        return

    # ── Get P&L for selected period ──
    try:
        pl = get_period_data(data, period_type, args.year)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(f"📅 Period: {pl['period']}")
    print(f"   Revenue: {fmt_cr(pl['sales'])} | Net Profit: {fmt_cr(pl['net_profit'])} | Margin: {pl['npm']:.1f}%")

    # ── Build Sankey ──
    fig = build_sankey(pl, company, ticker, data["is_consolidated"], data["sector"],
                       segments=data.get("segments"),
                       expense_breakdown=data.get("expense_breakdown"))

    # ── Output ──
    if args.output:
        out_path = args.output
    else:
        safe_name = re.sub(r'[^\w]', '_', ticker)
        period_safe = re.sub(r'[^\w]', '_', pl['period'])
        out_dir = Path(__file__).parent / "reports"
        out_dir.mkdir(exist_ok=True)
        out_path = str(out_dir / f"sankey_{safe_name}_{period_safe}.html")

    # Create rich HTML report
    create_html_report(fig, pl, company, ticker, out_path)
    print(f"✅ Report saved: {out_path}")

    # PNG export
    if args.png:
        png_path = out_path.replace(".html", ".png")
        try:
            fig.write_image(png_path, width=1400, height=700, scale=2)
            print(f"📸 PNG saved: {png_path}")
        except Exception as e:
            print(f"⚠️  PNG export failed: {e}")
            print("   Install kaleido: pip install kaleido")

    # Open in browser
    if not args.no_open:
        webbrowser.open(f"file:///{os.path.abspath(out_path)}")
        print("🌐 Opened in browser")


if __name__ == "__main__":
    main()

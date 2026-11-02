# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Valuation multiples calculated deterministically from VIPER's own prices and fundamentals.

No estimate, no analyst figure and no external valuation API: every value is arithmetic on rows already in the database, and a row is marked with the data
source ``VIPER_CALCULATED``.  The multiples are kept in ``valuation_metrics`` (``Migrate-TradingValuationMetrics.py``), one row per security, price date and
source, written by ``Refresh-TradingValuationMetrics.py``; ``analysis_engine`` reads the latest row on or before the evaluation date.

Inputs (all point-in-time for the price date, with the same visibility rule as the fundamentals module of ``analysis_engine``):

* price: the latest ``market_data`` close on or before the date, in the security's trading currency; a provider quote unit such as ``GBp`` (pence) is turned into
  its ISO currency with ``fx_resolver.normalize_quote_unit``.  Market cap = price x shares outstanding, in the price currency;
* TTM flows (revenue, net income, EBITDA, free cash flow): the newest of a stored ``ttm`` row, the sum of the four newest consecutive discrete quarters of one
  source (``4q``), the latest annual row bridged to the latest interim period of the next fiscal year, ``TTM = FY + YTD(current) - YTD(prior year)``, from rows of
  the same source and currency, and the latest annual row itself (``trailing``).  A YTD is a ``semiannual``/``nine_month`` row or the sum of the discrete quarters
  1..n; a missing quarter leaves that flow empty, it is never filled in.  When the bridge lacks a period, the annual row stays the TTM and the period is a gap;
* balance sheet (equity, cash, debt): the latest visible fundamentals row of that source that carries balance-sheet values;
* shares outstanding: the newest count of that source not older than the latest annual row, else the newest such count of another source of the same company
  (for example an IR row when the TTM comes from SEC).  The count must pass ``shares_conflict`` and ``shares_jump``; a count that fails is skipped with its reason
  and the next one is tried.  A depositary receipt (ADR/ADS) turns the filed
  count, which counts underlying shares, into depositary shares with the reviewed ratio of ``universe/adr_ratios_v1.json`` valid on the count's date
  (``depositary_shares``); without such a ratio it keeps no count.

Fundamentals in another currency than the price are converted into the price currency with the stored ECB rate of ``fx_resolver.resolve_fx_rate`` (direct EUR
pairs, or both EUR rates of one ECB day): balance-sheet values at the balance-sheet period end, TTM flows at the TTM period end.  Without a usable rate on that date the values of
that group stay empty, and so do the multiples that need them; nothing is estimated.

A multiple is left empty, with its reason, when an input is missing or the denominator is not positive (no P/E from a loss, no EV multiple from a negative EV).
An empty multiple never drops the others.  Every missing input is also returned as data (``ValuationRow.gaps``: the field or ``period`` / ``fx`` / ``adr_ratio`` /
``implausible``, the periods it is missing in and why), which ``fundamentals_completion.py`` turns into its gap analysis.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import fx_resolver

SOURCE_NAME = "VIPER_CALCULATED"
SOURCE_TYPE = "calculated"
TABLE = "valuation_metrics"

# A TTM older than this (from its period end to the price date) is not a trailing figure any more: the flows are left empty.
TTM_MAX_AGE_DAYS = 550

# Below this EPS the rounding of the reported EPS makes net income / EPS too coarse for the share-count check.
SHARES_CHECK_MIN_EPS = 0.10

# A share count more than this factor away from the company's previous count (either way) is a jump of the order of magnitude, not a buyback or an issue.
SHARES_JUMP_FACTOR = 2.0

# Reviewed depositary ratios (versioned, next to the other universe files).
ADR_RATIOS_FILE = Path(__file__).resolve().parent / "universe" / "adr_ratios_v1.json"

MULTIPLES = ("pe_ttm", "ps_ttm", "pb", "ev_revenue", "ev_ebitda", "p_fcf", "fcf_yield_pct")
FLOWS = ("revenue", "net_income", "ebitda", "free_cash_flow", "operating_cash_flow", "capex")
BALANCE = ("total_equity", "cash", "total_debt")
YTD_MONTHS = {"semiannual": 6, "nine_month": 9}

# Same visibility as analysis_engine._FUNDAMENTAL_VISIBLE_SQL: filed on or before the date, or (without a filing date) fetched on or before it.  Parameters: as_of x3.
_VISIBLE_SQL = (
    " AND period_end <= ?"
    " AND ((filing_date IS NOT NULL AND filing_date <= ?)"
    " OR (filing_date IS NULL AND fetched_at IS NOT NULL AND substr(fetched_at, 1, 10) <= ?))"
)


@dataclass
class ValuationRow:
    security_id: int
    as_of_date: Optional[str] = None
    price: Optional[float] = None
    price_currency: Optional[str] = None
    currency: Optional[str] = None
    ttm_period_end: Optional[str] = None
    ttm_method: Optional[str] = None
    balance_sheet_period_end: Optional[str] = None
    shares_outstanding: Optional[float] = None
    revenue_ttm: Optional[float] = None
    net_income_ttm: Optional[float] = None
    ebitda_ttm: Optional[float] = None
    free_cash_flow_ttm: Optional[float] = None
    total_equity: Optional[float] = None
    cash: Optional[float] = None
    total_debt: Optional[float] = None
    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None
    pe_ttm: Optional[float] = None
    ps_ttm: Optional[float] = None
    pb: Optional[float] = None
    ev_revenue: Optional[float] = None
    ev_ebitda: Optional[float] = None
    p_fcf: Optional[float] = None
    fcf_yield_pct: Optional[float] = None
    missing: list[str] = field(default_factory=list)
    # The missing inputs as data for the completion of the fundamentals (fundamentals_completion.py); not stored with the row.
    gaps: list[dict] = field(default_factory=list)


def _gap(gap: str, periods: list[str], detail: str, **extra) -> dict:
    """One missing valuation input: ``gap`` is a field of ``GAP_FIELDS`` or ``period`` / ``fx`` / ``adr_ratio`` / ``implausible``."""
    return {"gap": gap, "periods": sorted(set(periods)), "detail": detail, **extra}


def _label(row: sqlite3.Row) -> str:
    return f"{row['period_end']} {row['period_type']}"


# -- formulas ---------------------------------------------------------------------------------------------------------------------------
def ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """numerator / denominator, or None without both inputs or with a denominator that is not positive."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def market_cap(price: Optional[float], shares: Optional[float]) -> Optional[float]:
    if price is None or shares is None or price <= 0 or shares <= 0:
        return None
    return float(price) * float(shares)


def enterprise_value(cap: Optional[float], total_debt: Optional[float], cash: Optional[float]) -> Optional[float]:
    """Market cap + total debt - cash; None when one of them is missing."""
    if cap is None or total_debt is None or cash is None:
        return None
    return float(cap) + float(total_debt) - float(cash)


def apply_multiples(row: ValuationRow) -> ValuationRow:
    """Fill market cap, EV and every multiple whose inputs are present; record why the others are empty."""
    reasons: list[str] = []
    row.market_cap = market_cap(row.price, row.shares_outstanding)
    if row.market_cap is None:
        reasons.append("market_cap: " + ("price unavailable" if row.price is None else "shares_outstanding unavailable"))
    row.enterprise_value = enterprise_value(row.market_cap, row.total_debt, row.cash)
    if row.market_cap is not None and row.enterprise_value is None:
        reasons.append("enterprise_value: " + ("total_debt unavailable" if row.total_debt is None else "cash unavailable"))

    def multiple(name: str, numerator: Optional[float], denominator: Optional[float], label: str, base: str) -> Optional[float]:
        value = ratio(numerator, denominator)
        if value is None and numerator is not None:
            reasons.append(f"{name}: {label} unavailable" if denominator is None else f"{name}: {label} not positive")
        elif value is None:
            reasons.append(f"{name}: {base} unavailable")
        return value

    row.pe_ttm = multiple("pe_ttm", row.market_cap, row.net_income_ttm, "net_income TTM", "market_cap")
    row.ps_ttm = multiple("ps_ttm", row.market_cap, row.revenue_ttm, "revenue TTM", "market_cap")
    row.pb = multiple("pb", row.market_cap, row.total_equity, "total_equity", "market_cap")
    row.ev_revenue = multiple("ev_revenue", row.enterprise_value, row.revenue_ttm, "revenue TTM", "enterprise_value")
    if row.enterprise_value is not None and row.enterprise_value <= 0:
        row.ev_revenue = row.ev_ebitda = None
        reasons = [r for r in reasons if not r.startswith("ev_revenue:")]
        reasons += ["ev_revenue: enterprise_value not positive", "ev_ebitda: enterprise_value not positive"]
    else:
        row.ev_ebitda = multiple("ev_ebitda", row.enterprise_value, row.ebitda_ttm, "ebitda TTM", "enterprise_value")
    row.p_fcf = multiple("p_fcf", row.market_cap, row.free_cash_flow_ttm, "free_cash_flow TTM", "market_cap")
    # A negative FCF yield is a real figure (cash burn); only the inputs must be present.
    if row.market_cap is not None and row.free_cash_flow_ttm is not None:
        row.fcf_yield_pct = float(row.free_cash_flow_ttm) / row.market_cap * 100.0
    else:
        row.fcf_yield_pct = None
        reasons.append("fcf_yield_pct: " + ("market_cap unavailable" if row.market_cap is None else "free_cash_flow TTM unavailable"))
    row.missing = reasons
    return row


# -- TTM ----------------------------------------------------------------------------------------------------------------------------------
def _visible_rows(conn: sqlite3.Connection, security_id: int, as_of: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM fundamentals WHERE security_id = ?" + _VISIBLE_SQL + " ORDER BY period_end DESC, filing_date DESC, id DESC",
        (security_id, as_of, as_of, as_of),
    ).fetchall()


def _ytd_rows(rows: list[sqlite3.Row], fiscal_year: int, months: int) -> tuple[list[sqlite3.Row], list[str]]:
    """(rows, missing period labels) that make up the first ``months`` of ``fiscal_year``: one YTD row, or its discrete quarters 1..n."""
    fy = [r for r in rows if r["fiscal_year"] == fiscal_year]
    for r in fy:
        if (YTD_MONTHS.get(r["period_type"]) == months) or (months == 3 and r["period_type"] == "quarterly" and r["fiscal_quarter"] == 1):
            return [r], []
    quarters = {r["fiscal_quarter"]: r for r in fy if r["period_type"] == "quarterly"}
    needed = range(1, months // 3 + 1)
    missing = [f"FY{fiscal_year} Q{q}" for q in needed if q not in quarters]
    if missing:
        missing = [f"FY{fiscal_year} {'H1' if months == 6 else '9M' if months == 9 else 'Q1'} (YTD row or quarters {', '.join(f'Q{q}' for q in needed)})"]
    return [quarters[q] for q in needed if q in quarters], missing


def _ytd(rows: list[sqlite3.Row], fiscal_year: int, months: int, metric: str) -> Optional[float]:
    """The YTD value of ``metric`` over the first ``months`` of ``fiscal_year``: one YTD row, or the sum of its discrete quarters."""
    parts, missing = _ytd_rows(rows, fiscal_year, months)
    if missing or not parts or any(r[metric] is None for r in parts):
        return None
    return sum(float(r[metric]) for r in parts)


def _interim_months(row: sqlite3.Row) -> Optional[int]:
    if row["period_type"] in YTD_MONTHS:
        return YTD_MONTHS[row["period_type"]]
    if row["period_type"] == "quarterly" and row["fiscal_quarter"] in (1, 2, 3):
        return int(row["fiscal_quarter"]) * 3
    return None


def _values(row: sqlite3.Row) -> dict[str, Optional[float]]:
    return {m: (None if row[m] is None else float(row[m])) for m in FLOWS}


def _four_quarters(rows: list[sqlite3.Row]) -> Optional[list[sqlite3.Row]]:
    """The four newest discrete quarters of one source and currency (that of the newest quarter), newest first, when they follow each other
    without a hole (period ends about three months apart); None otherwise."""
    quarters = [r for r in rows if r["period_type"] == "quarterly"]
    if not quarters:
        return None
    newest = quarters[0]
    four: list[sqlite3.Row] = []
    for r in quarters:
        if r["source_id"] == newest["source_id"] and r["currency"] == newest["currency"] and all(r["period_end"] != q["period_end"] for q in four):
            four.append(r)
    four = four[:4]
    if len(four) < 4:
        return None
    for later, earlier in zip(four, four[1:]):
        if not 80 <= (date.fromisoformat(later["period_end"][:10]) - date.fromisoformat(earlier["period_end"][:10])).days <= 100:
            return None
    return four


@dataclass
class Ttm:
    """The trailing twelve months of one security: the flows, their period end and method, the anchor row (its source decides the balance sheet
    and the shares), the rows the flows were taken from, and the periods a better (newer) TTM still lacks."""

    flows: dict[str, Optional[float]]
    period_end: Optional[str] = None
    method: Optional[str] = None
    anchor: Optional[sqlite3.Row] = None
    used: list = field(default_factory=list)
    missing_periods: list[str] = field(default_factory=list)


def trailing(rows: list[sqlite3.Row]) -> Ttm:
    """The TTM of the visible rows (newest first): the newest of

    * a stored ``ttm`` row;
    * the sum of the four newest consecutive discrete quarters of one source (``4q``);
    * the latest annual row bridged to the latest interim period of the next fiscal year, ``FY + YTD(current) - YTD(prior year)`` (``annual+ytdNm``);
    * the latest annual row on its own (``annual``).

    A bridge that cannot be built (an interim period of the prior year is not stored) or gives no flow at all leaves the latest annual row as the
    TTM; the missing periods are recorded.  Flows of different methods or periods are never mixed.
    """
    candidates: list[Ttm] = []
    ttm = next((r for r in rows if r["period_type"] == "ttm"), None)
    if ttm is not None:
        candidates.append(Ttm(_values(ttm), ttm["period_end"], "stored_ttm", ttm, [ttm]))
    four = _four_quarters(rows)
    if four is not None:
        flows = {m: (None if any(q[m] is None for q in four) else sum(float(q[m]) for q in four)) for m in FLOWS}
        candidates.append(Ttm(flows, four[0]["period_end"], "4q", four[-1], four))
    annual = next((r for r in rows if r["period_type"] == "annual" and r["fiscal_year"] is not None), None)
    missing_periods: list[str] = []
    if annual is not None:
        same = [r for r in rows if r["source_id"] == annual["source_id"] and r["currency"] == annual["currency"]]
        later = [r for r in same if r["period_end"] > annual["period_end"] and r["period_type"] != "annual"]
        latest = later[0] if later else None
        months = _interim_months(latest) if latest is not None else None
        if latest is not None and (months is None or latest["fiscal_year"] != annual["fiscal_year"] + 1):
            missing_periods.append(f"FY{annual['fiscal_year'] + 1} annual (latest interim {latest['period_end']} is not in the fiscal year after "
                                   f"the annual {annual['period_end']})")
        elif latest is not None:
            current, missing_current = _ytd_rows(same, annual["fiscal_year"] + 1, months)
            prior, missing_prior = _ytd_rows(same, annual["fiscal_year"], months)
            missing_periods += missing_current + missing_prior
            if not missing_periods:
                flows = {}
                for metric in FLOWS:
                    cur, pri = _ytd(same, annual["fiscal_year"] + 1, months, metric), _ytd(same, annual["fiscal_year"], months, metric)
                    flows[metric] = None if annual[metric] is None or cur is None or pri is None else float(annual[metric]) + cur - pri
                if any(v is not None for v in flows.values()):
                    candidates.append(Ttm(flows, latest["period_end"], f"annual+ytd{months}m", annual, [annual, *current, *prior]))
        candidates.append(Ttm(_values(annual), annual["period_end"], "annual", annual, [annual]))
    if not candidates:
        return Ttm({m: None for m in FLOWS})
    order = {"stored_ttm": 0, "4q": 1, "annual": 3}
    chosen = max(candidates, key=lambda c: (c.period_end, -order.get(c.method, 2)))
    if chosen.method == "annual" and missing_periods:
        chosen.missing_periods = missing_periods
    return chosen


def ttm_flows(rows: list[sqlite3.Row]) -> tuple[dict[str, Optional[float]], Optional[str], Optional[str], Optional[sqlite3.Row]]:
    """(TTM flows, TTM period end, method, anchor row) from the visible rows, newest first (see ``trailing``)."""
    t = trailing(rows)
    return t.flows, t.period_end, t.method, t.anchor


def shares_conflict(row: Optional[sqlite3.Row]) -> Optional[str]:
    """Why the stored share count of ``row`` is not usable, or None.

    A cover-page count can be one share class only (multi-class 20-F filers).  The row's own net income / EPS gives the share count its earnings
    were divided by; a stored count off by more than a factor of 2 is not this company's share count.  The implied count only rejects, it is never used.
    Diluted EPS is preferred; basic EPS takes over when the diluted one implies no positive count (a diluted loss from adding back a convertible's
    fair-value gain next to a basic profit).
    """
    if row is None or row["net_income"] is None:
        return None
    implied = None
    for eps in (row["eps_diluted"], row["eps_basic"]):
        if eps is not None and abs(float(eps)) >= SHARES_CHECK_MIN_EPS and float(row["net_income"]) / float(eps) > 0:
            implied = float(row["net_income"]) / float(eps)
            break
    if implied is None:
        return None
    factor = float(row["shares_outstanding"]) / implied
    if 0.5 <= factor <= 2.0:
        return None
    return f"{float(row['shares_outstanding']):,.0f} on {row['period_end']} is inconsistent with net_income/eps ({implied:,.0f})"


def depositary_receipt(security: Optional[sqlite3.Row]) -> bool:
    """True for an ADR/ADS/GDR listing: named so, or a US ISIN of an issuer from another country."""
    if security is None:
        return False
    if re.search(r"\b(ADR|ADS|GDR)\b|DEPOSITARY", (security["name"] or "").upper()):
        return True
    country = (security["country"] or "").upper()
    return (security["isin"] or "").upper().startswith("US") and country not in ("", "US")


@lru_cache(maxsize=4)
def _adr_ratios(path: str) -> dict[str, dict]:
    """The entries of the ratio file by ISIN; an unreadable file gives none (every depositary receipt then keeps no count)."""
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f).get("entries") or []
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(e.get("isin") or "").upper(): e for e in entries if isinstance(e, dict) and e.get("isin")}


def adr_entry(security: Optional[sqlite3.Row], path: Path = ADR_RATIOS_FILE) -> Optional[dict]:
    """The reviewed ratio entry of a listing (by ISIN), or None."""
    if security is None:
        return None
    return _adr_ratios(str(path)).get((security["isin"] or "").upper())


def depositary_shares(entry: Optional[dict], underlying: float, counted_on: str) -> tuple[Optional[float], str]:
    """(depositary-share equivalents, note) of an underlying share count dated ``counted_on``; None with the reason without a valid ratio for that date.

    depositary shares = underlying shares / ordinary_shares_per_depositary_share.  The ratio must be a positive number and ``valid_from`` a date on or
    before the count's date: a ratio is never assumed for a period its source does not cover.
    """
    if entry is None:
        return None, "the listing is a depositary receipt and the filed count is in underlying shares; no reviewed ratio in adr_ratios_v1.json"
    ratio = entry.get("ordinary_shares_per_depositary_share")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or ratio <= 0:
        return None, f"invalid depositary ratio {ratio!r} in adr_ratios_v1.json"
    valid_from = str(entry.get("valid_from") or "")
    try:
        date.fromisoformat(valid_from)
    except ValueError:
        return None, f"depositary ratio without a valid 'valid_from' date ({valid_from!r}) in adr_ratios_v1.json"
    if counted_on[:10] < valid_from:
        return None, f"depositary ratio is reviewed from {valid_from}, the share count is dated {counted_on[:10]}"
    return float(underlying) / float(ratio), f"{float(underlying):,.0f} underlying shares on {counted_on[:10]} / {ratio:g} per depositary share (adr_ratios_v1)"


def shares_jump(rows: list[sqlite3.Row], chosen: Optional[sqlite3.Row]) -> Optional[str]:
    """Why ``chosen`` jumps by an order of magnitude against the company's previous count, or None.

    The previous count is the newest count of any source with an earlier period end.  A factor above ``SHARES_JUMP_FACTOR`` either way has no ordinary
    explanation (issue, buyback, conversion) and points at a unit, class or ADR mix-up; such a count is not used.
    """
    if chosen is None:
        return None
    previous = next((r for r in rows if r["shares_outstanding"] is not None and float(r["shares_outstanding"]) > 0
                     and r["period_end"] < chosen["period_end"]), None)
    if previous is None:
        return None
    factor = float(chosen["shares_outstanding"]) / float(previous["shares_outstanding"])
    if 1 / SHARES_JUMP_FACTOR <= factor <= SHARES_JUMP_FACTOR:
        return None
    return (f"{float(chosen['shares_outstanding']):,.0f} on {chosen['period_end']} jumps x{factor:.3g} against "
            f"{float(previous['shares_outstanding']):,.0f} on {previous['period_end']}")


def _converted(conn: sqlite3.Connection, values: dict[str, Optional[float]], from_currency: str, to_currency: str, on: Optional[str],
               label: str, notes: list[str]) -> dict[str, Optional[float]]:
    """``values`` in ``to_currency`` at the stored ECB rate on ``on``; all None, with the reason in ``notes``, without a usable rate."""
    if all(v is None for v in values.values()):
        return values
    if not on:
        notes.append(f"fx: {label} in {from_currency} has no date for a {from_currency}->{to_currency} rate; not converted")
        return {k: None for k in values}
    fx = fx_resolver.resolve_fx_rate(conn, from_currency, to_currency, on[:10])
    if fx.conversion_factor is None:
        why = "; ".join(fx.quality.details) or fx.quality.status.value
        notes.append(f"fx: {label} in {from_currency}, no usable {from_currency}->{to_currency} rate on {on[:10]} ({why}); not converted")
        return {k: None for k in values}
    notes.append(f"fx_converted: {label} {from_currency}->{to_currency} x{fx.conversion_factor:.6g} ({fx.source} {fx.rate_date})")
    return {k: (None if v is None else float(v) * fx.conversion_factor) for k, v in values.items()}


def _latest_price(conn: sqlite3.Connection, security_id: int, as_of: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT md.trade_date, md.close, COALESCE(md.currency, s.currency) AS currency FROM market_data md JOIN security s ON s.id = md.security_id "
        "WHERE md.security_id = ? AND md.trade_date <= ? AND md.close IS NOT NULL ORDER BY md.trade_date DESC, md.id DESC LIMIT 1",
        (security_id, as_of),
    ).fetchone()


def calculate(conn: sqlite3.Connection, security_id: int, as_of: Optional[str] = None) -> Optional[ValuationRow]:
    """The valuation row of one security for the latest price on or before ``as_of`` (default today); None without a price or fundamentals."""
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        price = _latest_price(conn, int(security_id), as_of or date.today().isoformat())
        if price is None:
            return None
        price_date = str(price["trade_date"])[:10]
        rows = _visible_rows(conn, int(security_id), price_date)
        if not rows:
            return None
        ttm = trailing(rows)
        flows, ttm_end, method, anchor = ttm.flows, ttm.period_end, ttm.method, ttm.anchor
        # GBp (pence) -> GBP x0.01: a quote unit, not an FX conversion.
        price_currency, price_factor = fx_resolver.normalize_quote_unit(price["currency"])
        row = ValuationRow(security_id=int(security_id), as_of_date=price_date, price=float(price["close"]) * price_factor,
                           price_currency=price_currency or (price["currency"] or "").upper() or None)
        notes: list[str] = []
        gaps = row.gaps
        if anchor is None:
            notes.append("ttm: no annual fundamentals row and no four consecutive quarters")
            gaps.append(_gap("period", ["latest fiscal year (annual)"], "no annual fundamentals row and no four consecutive quarters"))
        elif ttm.missing_periods:
            notes.append(f"ttm: latest interim period cannot be bridged to the annual {anchor['period_end']} ({'; '.join(ttm.missing_periods)} missing); "
                         "the annual figures are used")
            gaps.append(_gap("period", ttm.missing_periods, f"TTM bridge to the annual {anchor['period_end']} needs these periods"))
        if ttm_end and (date.fromisoformat(price_date) - date.fromisoformat(ttm_end[:10])).days > TTM_MAX_AGE_DAYS:
            notes.append(f"ttm: period end {ttm_end} is older than {TTM_MAX_AGE_DAYS} days")
            gaps.append(_gap("period", [f"periods after {ttm_end}"], f"TTM period end {ttm_end} is older than {TTM_MAX_AGE_DAYS} days"))
            flows = {m: None for m in FLOWS}
        source_rows = [r for r in rows if anchor is None or r["source_id"] == anchor["source_id"]] or rows
        # Balance sheet: the newest row of that source that carries one (an interim income statement can come without it).
        balance = next((r for r in source_rows if any(r[k] is not None for k in BALANCE)), source_rows[0])
        row.currency = (balance["currency"] or (anchor["currency"] if anchor is not None else None) or "").upper() or None
        row.ttm_period_end, row.ttm_method = (ttm_end if method else None), method
        row.balance_sheet_period_end = balance["period_end"]
        row.revenue_ttm, row.net_income_ttm, row.ebitda_ttm = flows["revenue"], flows["net_income"], flows["ebitda"]
        fcf = flows["free_cash_flow"]
        if fcf is None and flows["operating_cash_flow"] is not None and flows["capex"] is not None:
            fcf = flows["operating_cash_flow"] - abs(flows["capex"])
        row.free_cash_flow_ttm = fcf
        if method and not any(g["gap"] == "period" and "older than" in g["detail"] for g in gaps):
            for metric in ("revenue", "net_income", "ebitda", "operating_cash_flow"):
                if flows[metric] is None:
                    gaps.append(_gap(metric, [_label(r) for r in ttm.used if r[metric] is None], f"{metric} TTM ({method}) unavailable"))
            if fcf is None:
                lacking = [_label(r) for r in ttm.used if r["free_cash_flow"] is None and (r["operating_cash_flow"] is None or r["capex"] is None)]
                gaps.append(_gap("free_cash_flow", lacking, f"free_cash_flow TTM ({method}) unavailable: capex or operating_cash_flow missing"))
        row.total_equity, row.cash, row.total_debt = balance["total_equity"], balance["cash"], balance["total_debt"]
        for name in BALANCE:
            if balance[name] is None:
                gaps.append(_gap(name, [_label(balance)], f"{name} missing in the balance sheet of {balance['period_end']}"))
        # Shares: the newest count of the same source not older than the latest annual row (else the TTM anchor), then the newest such count of
        # another source.  A count that fails the checks is skipped with its reason; the next candidate is tried.
        floor = next((r["period_end"] for r in rows if r["period_type"] == "annual"), anchor["period_end"] if anchor is not None else None)
        candidates = [r for r in source_rows + [r for r in rows if r not in source_rows]
                      if r["shares_outstanding"] is not None and (floor is None or r["period_end"] >= floor)]
        shares_row, rejected = None, []
        for candidate in candidates:
            if float(candidate["shares_outstanding"]) <= 0:
                rejected.append(f"{float(candidate['shares_outstanding']):,.0f} on {candidate['period_end']} is not positive")
                continue
            conflict = shares_conflict(candidate) or shares_jump(rows, candidate)
            if conflict:
                rejected.append(conflict)
                continue
            shares_row = candidate
            break
        notes += [f"shares_outstanding: {reason}" for reason in rejected]
        row.shares_outstanding = None if shares_row is None else float(shares_row["shares_outstanding"])
        if shares_row is None:
            wanted = [_label(balance)] if balance["shares_outstanding"] is None else [f"{balance['period_end']} or later"]
            gaps.append(_gap("implausible" if rejected else "shares_outstanding", wanted,
                             "shares_outstanding: " + ("; ".join(rejected) if rejected else f"no count on or after {floor}")))
        security = conn.execute("SELECT name, isin, country FROM security WHERE id = ?", (int(security_id),)).fetchone()
        entry = adr_entry(security)
        if row.shares_outstanding is not None and (entry is not None or depositary_receipt(security)):
            # The checks above ran on the underlying count (net income / EPS are per ordinary share); the ratio converts it afterwards.
            row.shares_outstanding, note = depositary_shares(entry, row.shares_outstanding, shares_row["period_end"])
            notes.append(f"shares_outstanding: {note}")
            if row.shares_outstanding is None:
                gaps.append(_gap("adr_ratio", [], note))
        if row.currency and row.price_currency and row.currency != row.price_currency:
            # Market cap stays price x shares in the price currency; the fundamentals follow it, each group at the rate of its own date.
            before = len(notes)
            balance_values = _converted(conn, {"total_equity": row.total_equity, "cash": row.cash, "total_debt": row.total_debt}, row.currency,
                                        row.price_currency, row.balance_sheet_period_end, "balance sheet", notes)
            flow_values = _converted(conn, {"revenue_ttm": row.revenue_ttm, "net_income_ttm": row.net_income_ttm, "ebitda_ttm": row.ebitda_ttm,
                                            "free_cash_flow_ttm": row.free_cash_flow_ttm}, row.currency, row.price_currency, row.ttm_period_end, "TTM", notes)
            failed = [n for n in notes[before:] if n.startswith("fx:")]
            if failed:
                gaps.append(_gap("fx", [], "; ".join(failed), currency=row.currency, target=row.price_currency))
            for name, value in {**balance_values, **flow_values}.items():
                setattr(row, name, value)
            row.currency = row.price_currency
        apply_multiples(row)
        row.missing = notes + row.missing
        return row
    finally:
        conn.row_factory = previous


# -- storage ------------------------------------------------------------------------------------------------------------------------------
def table_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone() is not None


def source_id(conn: sqlite3.Connection) -> Optional[int]:
    found = conn.execute("SELECT id FROM data_sources WHERE name = ?", (SOURCE_NAME,)).fetchone()
    return None if found is None else int(found[0])


COLUMNS = ("security_id", "as_of_date", "price", "price_currency", "currency", "ttm_period_end", "ttm_method", "balance_sheet_period_end",
           "shares_outstanding", "revenue_ttm", "net_income_ttm", "ebitda_ttm", "free_cash_flow_ttm", "total_equity", "cash", "total_debt",
           "market_cap", "enterprise_value", *MULTIPLES)


def upsert(conn: sqlite3.Connection, row: ValuationRow, source: int, calculated_at: Optional[str] = None) -> None:
    """Insert or replace the row of (security, price date, source): a second run with the same inputs writes the same values."""
    values = asdict(row)
    data = [values[c] for c in COLUMNS] + [json.dumps(row.missing, ensure_ascii=False), source,
                                           calculated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")]
    updates = ", ".join(f"{c} = excluded.{c}" for c in (*COLUMNS[2:], "missing", "calculated_at"))
    conn.execute(
        f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}, missing, source_id, calculated_at) VALUES ({', '.join('?' * (len(COLUMNS) + 3))}) "
        f"ON CONFLICT(security_id, as_of_date, source_id) DO UPDATE SET {updates}",
        data,
    )


def latest(conn: sqlite3.Connection, security_id: int, as_of: str) -> Optional[sqlite3.Row]:
    """The newest calculated row of a security on or before ``as_of``; None without the table or a row."""
    if not table_exists(conn):
        return None
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            f"SELECT v.* FROM {TABLE} v JOIN data_sources d ON d.id = v.source_id "
            "WHERE v.security_id = ? AND v.as_of_date <= ? AND d.name = ? ORDER BY v.as_of_date DESC, v.id DESC LIMIT 1",
            (int(security_id), as_of, SOURCE_NAME),
        ).fetchone()
    finally:
        conn.row_factory = previous

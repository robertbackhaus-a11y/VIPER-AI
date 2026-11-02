# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Valuation multiples calculated deterministically from VIPER's own prices and fundamentals.

No estimate, no analyst figure and no external valuation API: every value is arithmetic on rows already in the database, and a row is marked with the data
source ``VIPER_CALCULATED``.  The multiples are kept in ``valuation_metrics`` (``Migrate-TradingValuationMetrics.py``), one row per security, price date and
source, written by ``Refresh-TradingValuationMetrics.py``; ``analysis_engine`` reads the latest row on or before the evaluation date.

Inputs (all point-in-time for the price date, with the same visibility rule as the fundamentals module of ``analysis_engine``):

* price: the latest ``market_data`` close on or before the date, in the security's trading currency; a provider quote unit such as ``GBp`` (pence) is turned into
  its ISO currency with ``fx_resolver.normalize_quote_unit``.  Market cap = price x shares outstanding, in the price currency;
* TTM flows (revenue, net income, EBITDA, free cash flow): a stored ``ttm`` row if there is one, else the latest annual row bridged to the latest interim period of
  the next fiscal year, ``TTM = FY + YTD(current) - YTD(prior year)``, from rows of the same source and currency.  A YTD is a ``semiannual``/``nine_month`` row or the
  sum of the discrete quarters 1..n; a missing quarter leaves that flow empty, it is never filled in;
* balance sheet (equity, cash, debt): the latest visible fundamentals row of that source;
* shares outstanding: the newest count of that source not older than the TTM anchor, else the newest such count of another source of the same company (for
  example an IR row when the TTM comes from SEC).  The count must pass ``shares_conflict``, and a depositary receipt (ADR/ADS) never takes a filed count: the
  filing counts underlying shares and the depositary ratio is not in the data.

Fundamentals in another currency than the price are converted into the price currency with the stored ECB rate of ``fx_resolver.resolve_fx_rate`` (direct EUR
pairs, no cross rates): balance-sheet values at the balance-sheet period end, TTM flows at the TTM period end.  Without a usable rate on that date the values of
that group stay empty, and so do the multiples that need them; nothing is estimated.

A multiple is left empty, with its reason, when an input is missing or the denominator is not positive (no P/E from a loss, no EV multiple from a negative EV).
An empty multiple never drops the others.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import fx_resolver

SOURCE_NAME = "VIPER_CALCULATED"
SOURCE_TYPE = "calculated"
TABLE = "valuation_metrics"

# A TTM older than this (from its period end to the price date) is not a trailing figure any more: the flows are left empty.
TTM_MAX_AGE_DAYS = 550

# Below this EPS the rounding of the reported EPS makes net income / EPS too coarse for the share-count check.
SHARES_CHECK_MIN_EPS = 0.10

MULTIPLES = ("pe_ttm", "ps_ttm", "pb", "ev_revenue", "ev_ebitda", "p_fcf", "fcf_yield_pct")
FLOWS = ("revenue", "net_income", "ebitda", "free_cash_flow", "operating_cash_flow", "capex")
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


def _ytd(rows: list[sqlite3.Row], fiscal_year: int, months: int, metric: str) -> Optional[float]:
    """The YTD value of ``metric`` over the first ``months`` of ``fiscal_year``: one YTD row, or the sum of its discrete quarters."""
    fy = [r for r in rows if r["fiscal_year"] == fiscal_year]
    for r in fy:
        if (YTD_MONTHS.get(r["period_type"]) == months) or (months == 3 and r["period_type"] == "quarterly" and r["fiscal_quarter"] == 1):
            return None if r[metric] is None else float(r[metric])
    quarters = {r["fiscal_quarter"]: r for r in fy if r["period_type"] == "quarterly"}
    needed = range(1, months // 3 + 1)
    if any(q not in quarters or quarters[q][metric] is None for q in needed):
        return None
    return sum(float(quarters[q][metric]) for q in needed)


def _interim_months(row: sqlite3.Row) -> Optional[int]:
    if row["period_type"] in YTD_MONTHS:
        return YTD_MONTHS[row["period_type"]]
    if row["period_type"] == "quarterly" and row["fiscal_quarter"] in (1, 2, 3):
        return int(row["fiscal_quarter"]) * 3
    return None


def ttm_flows(rows: list[sqlite3.Row]) -> tuple[dict[str, Optional[float]], Optional[str], Optional[str], Optional[sqlite3.Row]]:
    """(TTM flows, TTM period end, method, anchor row) from the visible rows, newest first."""
    empty = {m: None for m in FLOWS}
    ttm = next((r for r in rows if r["period_type"] == "ttm"), None)
    annual = next((r for r in rows if r["period_type"] == "annual"), None)
    if ttm is not None and (annual is None or ttm["period_end"] >= annual["period_end"]):
        return {m: (None if ttm[m] is None else float(ttm[m])) for m in FLOWS}, ttm["period_end"], "stored_ttm", ttm
    if annual is None or annual["fiscal_year"] is None:
        return empty, None, None, None
    same = [r for r in rows if r["source_id"] == annual["source_id"] and r["currency"] == annual["currency"]]
    later = [r for r in same if r["period_end"] > annual["period_end"] and r["period_type"] != "annual"]
    if not later:
        return {m: (None if annual[m] is None else float(annual[m])) for m in FLOWS}, annual["period_end"], "annual", annual
    latest = later[0]
    months = _interim_months(latest)
    if months is None or latest["fiscal_year"] != annual["fiscal_year"] + 1:
        return empty, latest["period_end"], None, annual
    flows: dict[str, Optional[float]] = {}
    for metric in FLOWS:
        current = _ytd(same, annual["fiscal_year"] + 1, months, metric)
        prior = _ytd(same, annual["fiscal_year"], months, metric)
        flows[metric] = None if annual[metric] is None or current is None or prior is None else float(annual[metric]) + current - prior
    return flows, latest["period_end"], f"annual+ytd{months}m", annual


def shares_conflict(row: Optional[sqlite3.Row]) -> Optional[str]:
    """Why the stored share count of ``row`` is not usable, or None.

    A cover-page count can be one share class only (multi-class 20-F filers).  The row's own net income / EPS gives the share count its earnings
    were divided by; a stored count off by more than a factor of 2 is not this company's share count.  The implied count only rejects, it is never used.
    """
    if row is None:
        return None
    eps = row["eps_diluted"] if row["eps_diluted"] is not None else row["eps_basic"]
    if eps is None or row["net_income"] is None or abs(float(eps)) < SHARES_CHECK_MIN_EPS:
        return None
    implied = float(row["net_income"]) / float(eps)
    if implied <= 0:
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
        flows, ttm_end, method, anchor = ttm_flows(rows)
        # GBp (pence) -> GBP x0.01: a quote unit, not an FX conversion.
        price_currency, price_factor = fx_resolver.normalize_quote_unit(price["currency"])
        row = ValuationRow(security_id=int(security_id), as_of_date=price_date, price=float(price["close"]) * price_factor,
                           price_currency=price_currency or (price["currency"] or "").upper() or None)
        notes: list[str] = []
        if anchor is None:
            notes.append("ttm: no annual fundamentals row")
        elif method is None:
            notes.append(f"ttm: latest interim period {ttm_end} cannot be bridged to the annual {anchor['period_end']}")
        elif ttm_end and (date.fromisoformat(price_date) - date.fromisoformat(ttm_end[:10])).days > TTM_MAX_AGE_DAYS:
            notes.append(f"ttm: period end {ttm_end} is older than {TTM_MAX_AGE_DAYS} days")
            flows = {m: None for m in FLOWS}
        source_rows = [r for r in rows if anchor is None or r["source_id"] == anchor["source_id"]] or rows
        balance = source_rows[0]
        row.currency = (balance["currency"] or (anchor["currency"] if anchor is not None else None) or "").upper() or None
        row.ttm_period_end, row.ttm_method = (ttm_end if method else None), method
        row.balance_sheet_period_end = balance["period_end"]
        row.revenue_ttm, row.net_income_ttm, row.ebitda_ttm = flows["revenue"], flows["net_income"], flows["ebitda"]
        fcf = flows["free_cash_flow"]
        if fcf is None and flows["operating_cash_flow"] is not None and flows["capex"] is not None:
            fcf = flows["operating_cash_flow"] - abs(flows["capex"])
        row.free_cash_flow_ttm = fcf
        row.total_equity, row.cash, row.total_debt = balance["total_equity"], balance["cash"], balance["total_debt"]
        # Shares: the newest count of the same source not older than the TTM anchor, else the newest such count of another source.
        def newest_count(candidates: list[sqlite3.Row]) -> Optional[sqlite3.Row]:
            return next((r for r in candidates if r["shares_outstanding"] is not None
                         and (anchor is None or r["period_end"] >= anchor["period_end"])), None)
        shares_row = newest_count(source_rows) or newest_count(rows)
        row.shares_outstanding = None if shares_row is None else float(shares_row["shares_outstanding"])
        conflict = shares_conflict(shares_row)
        if conflict:
            notes.append(f"shares_outstanding: {conflict}")
            row.shares_outstanding = None
        security = conn.execute("SELECT name, isin, country FROM security WHERE id = ?", (int(security_id),)).fetchone()
        if row.shares_outstanding is not None and depositary_receipt(security):
            notes.append("shares_outstanding: the listing is a depositary receipt and the filed count is in underlying shares (ratio not in the data)")
            row.shares_outstanding = None
        if row.currency and row.price_currency and row.currency != row.price_currency:
            # Market cap stays price x shares in the price currency; the fundamentals follow it, each group at the rate of its own date.
            balance_values = _converted(conn, {"total_equity": row.total_equity, "cash": row.cash, "total_debt": row.total_debt}, row.currency,
                                        row.price_currency, row.balance_sheet_period_end, "balance sheet", notes)
            flow_values = _converted(conn, {"revenue_ttm": row.revenue_ttm, "net_income_ttm": row.net_income_ttm, "ebitda_ttm": row.ebitda_ttm,
                                            "free_cash_flow_ttm": row.free_cash_flow_ttm}, row.currency, row.price_currency, row.ttm_period_end, "TTM", notes)
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

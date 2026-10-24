# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Read-only portfolio views that no other module offers: holdings with cost basis, the transaction list, the daily bars of one security and
the daily history of the portfolio rebuilt from the transactions (the web front end; ``ARCHITECTURE.md`` 2.3).

Nothing is computed a second way:

* the market value of a holding is the one of ``analysis_engine.build_portfolio_context`` (shares x EUR-normalised snapshot price); the daily
  change is ``sector_overview.day_changes`` / ``group_change_pct`` (the last two daily closes, native currency);
* the cost basis of a holding is ``positions.remaining_cost_basis`` (the average-cost model of ``transaction_import``); the history replays the
  transactions with the same step ``transaction_import.apply_cost_basis`` and the same split factors (``corporate_actions``);
* EUR values of the history use the daily ``close`` of ``market_data`` (Yahoo closes are split-adjusted, so quantities are taken on today's split
  basis) and ``fx_resolver.resolve_fx_rate`` at that day, with the age limits of ``DataQualityConfig`` / ``FXConfig``.  A day on which one held
  position has no price or no FX rate gets no market value (``None``) -- never a partial sum.  Its invested capital is still known, because every
  transaction amount is stored in EUR.

Only SELECTs; no write, no network.
"""

from __future__ import annotations

import bisect
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

import corporate_actions
import fx_resolver
import sector_overview
from strategy_config import DataQualityConfig, FXConfig
from transaction_import import apply_cost_basis

BASE = "EUR"
TRANSACTION_LIMIT = 2000


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _security_rows(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {int(r["id"]): r for r in conn.execute("SELECT id, symbol, name, isin, wkn, currency, asset_type FROM security")}


def holdings(conn: sqlite3.Connection, as_of: Optional[str] = None) -> dict[str, Any]:
    """The open positions with market value, cost basis, unrealised result and daily change, and the portfolio totals."""
    import analysis_engine

    context = analysis_engine.build_portfolio_context(as_of=as_of, connection=conn)
    evaluation = str(context.evaluation_as_of)[:10]
    securities = _security_rows(conn)
    costs = {int(r["security_id"]): r for r in conn.execute("SELECT security_id, remaining_cost_basis, currency, realized_gain, first_transaction_at FROM positions")}
    exposures = [e for e in context.exposures if e.quantity is not None and float(e.quantity) > 0]
    changes = sector_overview.day_changes(conn, [int(e.security_id) for e in exposures], evaluation)
    total = _num(context.total_market_value)
    rows: list[dict[str, Any]] = []
    for e in exposures:
        sid = int(e.security_id)
        sec, cost_row = securities.get(sid), costs.get(sid)
        value = _num(e.market_value_eur)
        cost = _num(cost_row["remaining_cost_basis"]) if cost_row is not None and (cost_row["currency"] or BASE) == BASE else None
        unrealized = value - cost if value is not None and cost is not None else None
        rows.append({
            "security_id": sid, "symbol": e.symbol, "name": sec["name"] if sec else None, "isin": sec["isin"] if sec else None,
            "asset_type": sec["asset_type"] if sec else None, "strategy": getattr(e.strategy, "value", e.strategy), "quantity": float(e.quantity),
            "price_native": _num(e.market_price_native), "price_currency": e.market_price_currency, "price_eur": _num(e.market_price_eur),
            "market_value_eur": value, "cost_basis_eur": cost, "unrealized_eur": unrealized,
            "unrealized_pct": unrealized / cost * 100.0 if unrealized is not None and cost else None,
            "weight_pct": value / total * 100.0 if value is not None and total else None,
            "day_change_pct": changes.get(sid, {}).get("pct"), "day_change_date": changes.get(sid, {}).get("date"),
            "valuation_status": e.valuation_quality.status.value, "valuation_details": list(e.valuation_quality.details),
            "first_transaction_at": cost_row["first_transaction_at"] if cost_row is not None else None,
        })
    rows.sort(key=lambda r: -(r["market_value_eur"] or 0.0))
    valued = [r for r in rows if r["market_value_eur"] is not None]
    cost_total = sum(r["cost_basis_eur"] for r in rows if r["cost_basis_eur"] is not None) if all(r["cost_basis_eur"] is not None for r in rows) else None
    day_pct, day_coverage = sector_overview.group_change_pct([r for r in valued]) if valued else (None, None)
    day_eur = (sum(r["market_value_eur"] - r["market_value_eur"] / (1.0 + r["day_change_pct"] / 100.0) for r in valued if r["day_change_pct"] is not None)
               if day_pct is not None else None)
    realized = conn.execute("SELECT SUM(realized_gain) FROM positions WHERE currency = ? OR currency IS NULL", (BASE,)).fetchone()[0]
    dividends = conn.execute("SELECT SUM(amount - COALESCE(taxes, 0) - COALESCE(fees, 0)) FROM transactions WHERE transaction_type = 'DIVIDEND' AND currency = ?",
                             (BASE,)).fetchone()[0]
    unrealized_total = total - cost_total if total is not None and cost_total is not None else None
    return {
        "as_of": evaluation, "base_currency": BASE, "valuation_status": context.valuation_quality.status.value,
        "valuation_details": list(context.valuation_quality.details),
        "totals": {
            "market_value_eur": total, "cost_basis_eur": cost_total, "unrealized_eur": unrealized_total,
            "unrealized_pct": unrealized_total / cost_total * 100.0 if unrealized_total is not None and cost_total else None,
            "realized_eur": _num(realized), "dividends_net_eur": _num(dividends),
            "day_change_pct": day_pct, "day_change_eur": day_eur, "day_coverage_pct": day_coverage,
            "cash_eur": _num(context.cash_available), "cash_as_of": context.capital_state_as_of,
            "swing_weight_pct": _num(context.swing_weight_pct), "long_term_weight_pct": _num(context.long_term_weight_pct),
        },
        "positions": rows,
    }


def transactions(conn: sqlite3.Connection, security_id: Optional[int] = None, limit: int = TRANSACTION_LIMIT) -> list[dict[str, Any]]:
    """The stored transactions, newest first (optionally of one security)."""
    sql = ("SELECT t.id, t.security_id, s.symbol, s.name, t.transaction_type, t.transaction_date, t.shares, t.price, t.amount, t.fees, t.taxes, t.currency, t.broker "
           "FROM transactions t LEFT JOIN security s ON s.id = t.security_id")
    params: list[Any] = []
    if security_id is not None:
        sql += " WHERE t.security_id = ?"
        params.append(int(security_id))
    sql += " ORDER BY t.transaction_date DESC, t.id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    return [dict(r) for r in conn.execute(sql, params)]


def price_history(conn: sqlite3.Connection, security_id: int, start: Optional[str] = None) -> dict[str, Any]:
    """The stored daily bars of one security (trading currency, as stored)."""
    sec = conn.execute("SELECT id, symbol, name, isin, currency, asset_type FROM security WHERE id = ?", (int(security_id),)).fetchone()
    if sec is None:
        raise ValueError(f"security_id {security_id} not found")
    sql = "SELECT trade_date, open, high, low, close, volume, currency FROM market_data WHERE security_id = ? AND close IS NOT NULL"
    params: list[Any] = [int(security_id)]
    if start:
        sql += " AND trade_date >= ?"
        params.append(str(start)[:10])
    bars = [dict(r) for r in conn.execute(sql + " ORDER BY trade_date", params)]
    currencies = sorted({b["currency"] for b in bars if b["currency"]})
    return {"security": dict(sec), "currency": currencies[-1] if len(currencies) == 1 else (currencies or [None])[-1],
            "currencies": currencies, "bars": [{k: b[k] for k in ("trade_date", "open", "high", "low", "close", "volume")} for b in bars]}


class _Prices:
    """The daily closes of one security for lookups "latest close on or before a day"."""

    def __init__(self, rows: list[sqlite3.Row]):
        self.dates = [str(r["trade_date"])[:10] for r in rows]
        self.rows = rows

    def at(self, day: str, max_age: int) -> Optional[tuple[float, str, str]]:
        i = bisect.bisect_right(self.dates, day) - 1
        if i < 0 or (date.fromisoformat(day) - date.fromisoformat(self.dates[i])).days > max_age:
            return None
        return float(self.rows[i]["close"]), self.rows[i]["currency"], self.dates[i]


def portfolio_history(conn: sqlite3.Connection, end: Optional[str] = None, *, data_quality: Optional[DataQualityConfig] = None,
                      fx_config: Optional[FXConfig] = None) -> dict[str, Any]:
    """Daily invested capital (cost basis of the holdings) and EUR market value since the first transaction."""
    dq, fx_cfg = data_quality or DataQualityConfig(), fx_config or FXConfig()
    rows = conn.execute("SELECT security_id, transaction_type, transaction_date, shares, amount, fees, taxes, currency FROM transactions "
                        "ORDER BY transaction_date, id").fetchall()
    last_day = date.fromisoformat(str(end)[:10]) if end else date.today()
    if not rows:
        return {"base_currency": BASE, "points": [], "first_valued_date": None, "end": last_day.isoformat()}
    securities = _security_rows(conn)
    actions = {sid: corporate_actions.load_corporate_actions(conn, sid) for sid in {int(r["security_id"]) for r in rows}}
    prices: dict[int, _Prices] = {}
    fx_cache: dict[tuple[str, str], Optional[float]] = {}
    table_cache: dict[str, bool] = {}
    state: dict[int, list[float]] = {}  # security_id -> [shares, remaining cost basis, invested]
    non_eur_cash = False

    def price_eur(sid: int, day: str) -> Optional[float]:
        if sid not in prices:
            prices[sid] = _Prices(conn.execute("SELECT trade_date, close, currency FROM market_data WHERE security_id = ? AND close IS NOT NULL AND close > 0 "
                                               "ORDER BY trade_date", (sid,)).fetchall())
        hit = prices[sid].at(day, dq.market_data_max_age_days)
        if hit is None:
            return None
        close, currency, _ = hit
        key = (str(currency), day)
        if key not in fx_cache:
            fx_cache[key] = fx_resolver.resolve_fx_rate(conn, currency, BASE, day, fx_cfg, table_exists_cache=table_cache).conversion_factor
        factor = fx_cache[key]
        return None if factor is None else close * factor

    points: list[dict[str, Any]] = []
    index, day = 0, date.fromisoformat(str(rows[0]["transaction_date"])[:10])
    while day <= last_day:
        text = day.isoformat()
        flow = dividends = 0.0
        while index < len(rows) and str(rows[index]["transaction_date"])[:10] <= text:
            r = rows[index]
            index += 1
            if (r["currency"] or BASE) != BASE:
                non_eur_cash = True
            flow, dividends = _cash(r, flow, dividends)
            sid = int(r["security_id"])
            quantity = float(r["shares"] or 0.0) * corporate_actions.cumulative_split_factor(actions[sid], str(r["transaction_date"]), None)
            s = state.setdefault(sid, [0.0, 0.0, 0.0])
            s[0], s[1], s[2] = apply_cost_basis(s[0], s[1], s[2], r["transaction_type"], quantity, float(r["amount"] or 0.0), float(r["fees"] or 0.0),
                                                float(r["taxes"] or 0.0))
            if abs(s[0]) < 1e-9:
                s[0] = s[1] = 0.0
        held = {sid: s for sid, s in state.items() if s[0] > 0}
        value: Optional[float] = 0.0
        missing: list[str] = []
        for sid, s in held.items():
            p = price_eur(sid, text)
            if p is None:
                missing.append(str(securities[sid]["symbol"] if sid in securities and securities[sid]["symbol"] else sid))
            elif value is not None:
                value += s[0] * p
        if missing:
            value = None
        invested = sum(s[1] for s in held.values())
        point = {"date": text, "invested_eur": round(invested, 2), "market_value_eur": None if value is None else round(value, 2),
                 "unrealized_eur": None if value is None else round(value - invested, 2), "positions": len(held),
                 "flow_eur": round(flow, 2), "dividends_eur": round(dividends, 2)}
        if missing:
            point["missing"] = missing
        points.append(point)
        day += timedelta(days=1)
    first_valued = next((p["date"] for p in points if p["market_value_eur"] is not None and p["positions"]), None)
    unvalued: dict[str, int] = {}
    for p in points:
        for symbol in p.get("missing", ()):
            unvalued[symbol] = unvalued.get(symbol, 0) + 1
    return {"base_currency": BASE, "points": points, "first_valued_date": first_valued, "end": last_day.isoformat(),
            "non_eur_transactions": non_eur_cash, "periods": periods(points, last_day),
            "unvalued_days": dict(sorted(unvalued.items(), key=lambda kv: -kv[1])),
            "fx_from": {r[0]: r[1] for r in conn.execute("SELECT quote_currency, MIN(rate_date) FROM fx_rates WHERE base_currency = ? GROUP BY quote_currency", (BASE,))},
            "basis": "invested = cost basis of the holdings (average cost); market value = daily close x ECB rate of that day; "
                     "a day with one unvalued holding has no market value; flow = buys (amount + fees + taxes) minus sells (amount - fees - taxes); "
                     "performance = time-weighted return of the valued days, chained only over days without a gap"}


def _cash(row: Any, flow: float, dividends: float) -> tuple[float, float]:
    """The external cash flow of one transaction into the securities (+) or out of them (-), and the dividends paid out (income)."""
    amount, fees, taxes = float(row["amount"] or 0.0), float(row["fees"] or 0.0), float(row["taxes"] or 0.0)
    kind = row["transaction_type"]
    if kind in ("BUY", "TRANSFERIN"):
        flow += amount + fees + taxes
    elif kind in ("SELL", "TRANSFEROUT"):
        flow -= amount - fees - taxes
    elif kind == "DIVIDEND":
        dividends += amount - fees - taxes
    return flow, dividends


PERIODS: tuple[tuple[str, Optional[int]], ...] = (("1M", 1), ("3M", 3), ("6M", 6), ("1J", 12), ("3J", 36), ("5J", 60), ("MAX", None))


def _months_back(day: date, months: int) -> date:
    month = day.month - months
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    return date(year, month, 28)


def performance(points: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    """Value change and time-weighted return between two days.

    Daily return ``(V_t - flow_t + dividends_t) / V_t-1 - 1``: buys and sells are external flows, not performance; dividends are income.  The
    chain needs a market value on every day of the span (a gap makes the return unknown, never estimated)."""
    span = [p for p in points if start <= p["date"] <= end]
    if not span:
        return {"start": start, "end": end, "available": False, "reason": "keine Tage im Zeitraum"}
    first, last = span[0], span[-1]
    flows = sum(p["flow_eur"] for p in span[1:])
    dividends = sum(p["dividends_eur"] for p in span[1:])
    out: dict[str, Any] = {"start": first["date"], "end": last["date"], "value_start_eur": first["market_value_eur"], "value_end_eur": last["market_value_eur"],
                           "net_flows_eur": round(flows, 2), "dividends_eur": round(dividends, 2)}
    gaps = [p["date"] for p in span if p["market_value_eur"] is None]
    if gaps:
        return {**out, "available": False, "reason": f"{len(gaps)} Tage ohne Depotwert (erster {gaps[0]})"}
    index = 1.0
    for previous, current in zip(span, span[1:]):
        if previous["market_value_eur"] and previous["market_value_eur"] > 0:
            index *= (current["market_value_eur"] - current["flow_eur"] + current["dividends_eur"]) / previous["market_value_eur"]
    gain = last["market_value_eur"] - first["market_value_eur"] - flows + dividends
    return {**out, "available": True, "value_change_eur": round(last["market_value_eur"] - first["market_value_eur"], 2), "result_eur": round(gain, 2),
            "twr_pct": round((index - 1.0) * 100.0, 4)}


def periods(points: list[dict[str, Any]], end: date) -> list[dict[str, Any]]:
    """The fixed periods of the chart; MAX starts with the first fully valued day (the history before has no market value)."""
    first_valued = next((p["date"] for p in points if p["market_value_eur"] is not None and p["positions"]), None)
    out = []
    for label, months in PERIODS:
        if months is None:
            start = first_valued
        else:
            start = _months_back(end, months).isoformat()
            if first_valued and start < first_valued:
                out.append({"period": label, "start": start, "end": end.isoformat(), "available": False,
                            "reason": f"Depotwert erst ab {first_valued} rekonstruierbar"})
                continue
        out.append({"period": label, **(performance(points, start, end.isoformat()) if start else {"available": False, "reason": "kein bewertbarer Tag"})})
    return out

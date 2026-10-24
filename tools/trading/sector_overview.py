# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Read-only sector overview of the current portfolio (the ``get_sector_overview`` MCP tool, the "Sektoren" page of the desktop GUI).

``open positions (EUR market value of the portfolio context) + effective sector (public + local sector map) + ETF look-through + last daily close change
-> exposure grouped by sector``.

Nothing is invented here:

* the market value of a position is the one of ``analysis_engine.build_portfolio_context`` (``shares x EUR-normalised price``), the figure every other
  portfolio report uses; a position without a valuation is listed separately (``unvalued``) and never counted as 0;
* the sector of a single stock is the effective sector of ``sector_assignment`` (local entry over public entry; the key is the listing key the risk guards use);
  a position without an entry is "not assigned" and stays visible as its own group -- it is never dropped and never put into another sector;
* **ETF look-through** (``lookthrough=True``, the default; ``etf_lookthrough``): an ETF with an official provider sector allocation is split into its
  categories (``market value x weight``); a category that maps to exactly one sector group (``etf_sector_mapping_v1.json``) adds to that group as an
  ``etf_lookthrough`` contribution, a category that does not (spans several groups, no group, not a sector) forms its own *unmapped* category group and is
  never put into a group by name or size.  An ETF without a usable allocation stays one unresolved position (its own sector entry or "not assigned"),
  visible with its status.  The split replaces the ETF's single position, so nothing is counted twice (``checks`` compares the sum with the portfolio context).
* **Company-based assignment** (``etf_company``): where the ETF's holdings are usable, every holding is resolved to a security of the database by the one matching of
  ``effective_exposure`` (ISIN, reviewed alias, ticker + exchange; no fuzzy matching).  A company whose security has a sector in ``sector_assignment`` contributes its ETF value
  to *that* project sector -- the same sector its direct position has -- and this value is taken out of the provider category it belongs to (the holding's own GICS sector;
  a provider that publishes none: the category group that fits, else the unmapped categories, pro rata, reported as ``estimated_value_eur``).  A company without a sector, or not
  in the database, is not forced anywhere: its value stays in the provider category (mapped one-to-one or unmapped / ``NOT_ASSESSED``).  The total is unchanged.
* **ISIN fallback** (``etf_company_isin_map``): a company that resolves to no security of the database at all, but has a valid ISIN (an ISIN holding, or every ISIN of a
  reviewed issuer group), is looked up with that ISIN as the key of the same effective sector map (local over public).  An entry puts it into that group exactly like
  ``etf_company``; no entry, no ISIN or a conflict between the ISINs of a group keeps it in its provider category.  A company with a security never takes this path, so the
  security-based assignment always wins and no company is counted twice.  Neither the effective exposure nor the company grouping is changed.
* **Listing fallback** (``etf_company_listing_map``): a company without a security and without a sector-map entry for its ISIN is looked up with the Yahoo symbol of its
  listing in the same effective sector map -- ticker + the suffix of the exchange (``etf_holdings.listing_symbol``, versioned table ``etf_listing_symbols_v1.json``), or for a
  holding published with an ISIN only the reviewed symbol of that ISIN (``isin_symbols`` of the private ``etf_listing_symbols_local.json``).  An exchange without a suffix, an ISIN without a symbol, a symbol
  without an entry, or the symbols of an issuer group that disagree keep it in its provider category; the name is never used.
  Every contribution says whether it is ``direct``, ``etf_company``, ``etf_company_isin_map``, ``etf_company_listing_map`` or ``etf_lookthrough``;
* the weight is the share of the invested value (the sum of the valued positions), the basis of the sector guard; ``limit_pct`` is the guard limit
  (``RiskGuardConfig.max_sector_weight``) and ``guard_status`` says ``OVER_LIMIT`` / ``OK`` / ``NOT_ASSESSED`` for the *effective* exposure (direct + look-through).
  The risk guard itself is NOT changed: it still reads direct sectors only; this view reads no guard result;
* the daily change of a position is ``close / previous close - 1`` of the two latest daily bars of the Yahoo source (native currency, so the currency effect
  is not included); a missing or stale bar means ``None``.  A contribution of an ETF carries the change of the ETF.  The change of a group is the exact
  value-weighted change of its entries that have one (``sum(v - v/(1+p)) / sum(v/(1+p))``) and says how much of the group it covers (``day_coverage_pct``).

Only SELECTs; no write, no network, no order.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

import effective_exposure
import etf_holdings as eh
import etf_lookthrough
import sector_assignment
from strategy_config import RiskGuardConfig

OPERATION = "get_sector_overview"
GUARD_OK = "OK"
GUARD_OVER = "OVER_LIMIT"
GUARD_NOT_ASSESSED = "NOT_ASSESSED"
KIND_SECTOR, KIND_ETF_CATEGORY, KIND_UNASSIGNED = "sector", "etf_category", "unassigned"
ORIGIN_DIRECT, ORIGIN_ETF, ORIGIN_COMPANY, ORIGIN_COMPANY_ISIN = "direct", "etf_lookthrough", "etf_company", "etf_company_isin_map"
ORIGIN_COMPANY_LISTING = "etf_company_listing_map"
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
COMPANIES_SHOWN = 3  # companies named in an etf_company entry
_NOT_A_SECTOR = "not a sector"
STALE_BAR_DAYS = 5  # the latest daily bar may be at most this many calendar days older than the evaluation date
BAR_GAP_DAYS = 7  # the previous bar must lie at most this many days before the latest one
SOURCE_NAME = "Yahoo Finance"
_EPS = 1e-9


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def day_changes(conn: sqlite3.Connection, security_ids, as_of: str) -> dict[int, dict[str, Any]]:
    """``{security_id: {"pct": float|None, "date": str|None}}`` from the two latest daily closes up to ``as_of`` (read-only)."""
    out: dict[int, dict[str, Any]] = {int(i): {"pct": None, "date": None} for i in security_ids}
    try:
        row = conn.execute("SELECT id FROM data_sources WHERE name = ?", (SOURCE_NAME,)).fetchone()
        source_id = int(row[0]) if row else None
        evaluation = date.fromisoformat(as_of[:10])
    except (sqlite3.Error, ValueError):
        return out
    clause = " AND source_id = ?" if source_id is not None else ""
    for sid in out:
        try:
            params: list[Any] = [sid, evaluation.isoformat()] + ([source_id] if source_id is not None else [])
            bars = conn.execute(
                f"SELECT trade_date, close FROM market_data WHERE security_id = ? AND trade_date <= ? AND close IS NOT NULL AND close > 0{clause} "
                "ORDER BY trade_date DESC LIMIT 2", params).fetchall()
            if len(bars) < 2:
                continue
            latest, previous = date.fromisoformat(str(bars[0][0])[:10]), date.fromisoformat(str(bars[1][0])[:10])
            if (evaluation - latest).days > STALE_BAR_DAYS or (latest - previous).days > BAR_GAP_DAYS:
                continue
            out[sid] = {"pct": (float(bars[0][1]) / float(bars[1][1]) - 1.0) * 100.0, "date": latest.isoformat()}
        except (sqlite3.Error, ValueError, TypeError):
            continue
    return out


def group_change_pct(positions: list[Mapping[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    """``(value-weighted daily change in %, share of the group value that has a change in %)`` -- ``None`` when no entry has one."""
    total = sum(p["market_value_eur"] for p in positions)
    covered = [p for p in positions if p.get("day_change_pct") is not None]
    if not covered:
        return None, None
    now = sum(p["market_value_eur"] for p in covered)
    before = sum(p["market_value_eur"] / (1.0 + p["day_change_pct"] / 100.0) for p in covered)
    change = (now / before - 1.0) * 100.0 if before > _EPS else None
    return change, (now / total * 100.0 if total > _EPS else None)


def _bucket(entry: Mapping[str, Any]) -> tuple[str, Optional[str]]:
    if entry["sector"] is not None:
        return KIND_SECTOR, entry["sector"]
    if entry["origin"] == ORIGIN_ETF:
        return KIND_ETF_CATEGORY, entry["category"]
    return KIND_UNASSIGNED, None


def group_positions(entries: list[dict[str, Any]], total: float, limit_pct: float) -> list[dict[str, Any]]:
    """The groups of valued entries: sector groups by value (largest first), then the unmapped ETF categories, "not assigned" last."""
    buckets: dict[tuple[str, Optional[str]], list[dict[str, Any]]] = {}
    for entry in entries:
        buckets.setdefault(_bucket(entry), []).append(entry)
    groups = []
    for (kind, name), members in buckets.items():
        members.sort(key=lambda p: (-p["market_value_eur"], p["symbol"], p.get("category") or ""))
        value = sum(p["market_value_eur"] for p in members)
        direct = sum(p["market_value_eur"] for p in members if p["origin"] == ORIGIN_DIRECT)
        weight = value / total * 100.0 if total > _EPS else 0.0
        change, coverage = group_change_pct(members)
        assessed = kind == KIND_SECTOR
        status = GUARD_NOT_ASSESSED if not assessed else GUARD_OVER if weight > limit_pct + _EPS else GUARD_OK
        securities = {p["security_id"] for p in members}
        groups.append({
            "sector": name if kind == KIND_SECTOR else None, "kind": kind, "assigned": assessed, "label": name or "",
            "market_value_eur": value, "weight_pct": weight, "direct_value_eur": direct, "etf_value_eur": value - direct,
            "direct_weight_pct": direct / total * 100.0 if total > _EPS else 0.0, "etf_weight_pct": (value - direct) / total * 100.0 if total > _EPS else 0.0,
            "position_count": len(securities), "direct_count": len({p["security_id"] for p in members if p["origin"] == ORIGIN_DIRECT}),
            "etf_count": len({p["security_id"] for p in members if p["is_etf"]}),
            "day_change_pct": change, "day_coverage_pct": coverage, "guard_status": status,
            "limit_pct": limit_pct if assessed else None, "headroom_pct": limit_pct - weight if assessed else None, "positions": members,
        })
    order = {KIND_SECTOR: 0, KIND_ETF_CATEGORY: 1, KIND_UNASSIGNED: 2}
    groups.sort(key=lambda g: (order[g["kind"]], -g["market_value_eur"], g["label"]))
    return groups


def _security_info(conn: sqlite3.Connection, ids: list[int]) -> dict[int, tuple[Any, Any, Any]]:
    """``{id: (name, asset_type, isin)}``; a database without an ``isin`` column (an old schema) just has none."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    try:
        return {int(r[0]): (r[1], r[2], r[3]) for r in conn.execute(f"SELECT id, name, asset_type, isin FROM security WHERE id IN ({marks})", ids)}
    except sqlite3.OperationalError:
        return {int(r[0]): (r[1], r[2], None) for r in conn.execute(f"SELECT id, name, asset_type FROM security WHERE id IN ({marks})", ids)}


def _lookthrough_layers(universe_dir: Optional[Path], valid_groups: Optional[set[str]], warnings: list[str]):
    try:
        return etf_lookthrough.read_layers(universe_dir, valid_groups)
    except (OSError, ValueError) as exc:  # LookthroughError is a ValueError: no mapping file means no look-through, never a failed tool
        warnings.append(f"LOOKTHROUGH_UNAVAILABLE: {exc}; ETFs stay single positions")
        return None


def _company_group(conn: sqlite3.Connection, layers, row: Mapping[str, Any]) -> Optional[str]:
    """The project sector of a company (all its securities agree; a conflict or no entry means none)."""
    found = set()
    for sid in row.get("security_ids") or []:
        try:
            key = sector_assignment.listing_key(conn, int(sid))
        except sqlite3.Error:
            continue
        sector = sector_assignment.resolve(layers, key)["sector"]
        if sector:
            found.add(sector)
    return next(iter(found)) if len(found) == 1 else None


def company_isins(row: Mapping[str, Any], issuer_groups: Mapping[str, str]) -> tuple[str, ...]:
    """The ISINs of a company that resolves to no security: its own ISIN, or every ISIN of its reviewed issuer group; empty for a company with a security or without an ISIN."""
    if row.get("security_ids"):
        return ()
    key = str(row.get("key") or "")
    if key.startswith("isin:"):
        isins = [key[5:]]
    elif key.startswith("grp:"):
        isins = [isin for isin, name in issuer_groups.items() if "grp:" + eh.norm(name) == key]
    else:
        return ()
    isins = sorted({str(i).strip().upper() for i in isins})
    return tuple(isins) if isins and all(_ISIN.match(i) for i in isins) else ()


def company_symbols(row: Mapping[str, Any], issuer_groups: Mapping[str, str], table: eh.ListingTable) -> tuple[str, ...]:
    """The Yahoo symbols of a company that resolves to no security: of its listing (key ticker | exchange | name; the name plays no part), else the reviewed symbols of its
    ISINs; empty for a company with a security or without a known symbol."""
    if row.get("security_ids"):
        return ()
    key = str(row.get("key") or "")
    if key.startswith("tk:") and key.count("|") >= 2:
        ticker, exchange, _name = key[3:].split("|", 2)
        symbol = eh.listing_symbol(ticker, exchange, table.suffixes)
        return (symbol,) if symbol else ()
    return tuple(sorted({table.isin_symbols[i] for i in company_isins(row, issuer_groups) if i in table.isin_symbols}))


def _isin_group(layers, isins: tuple[str, ...]) -> Optional[str]:
    """The project sector the effective sector map gives these keys -- ISINs or symbols (all that have an entry agree; a conflict or no entry means none)."""
    found = {sector for sector in (sector_assignment.resolve(layers, isin)["sector"] for isin in isins) if sector}
    return next(iter(found)) if len(found) == 1 else None


def company_items(conn: sqlite3.Connection, layers, exposure: Mapping[str, Any], issuer_groups: Optional[Mapping[str, str]] = None,
                  table: Optional[eh.ListingTable] = None) -> dict[int, list[dict[str, Any]]]:
    """``{etf security_id: [{"name", "group", "gics", "value", "origin"}]}``: the ETF part of every company that has a project sector (rows of ``effective_exposure``).

    The sector of the company's security comes first (``etf_company``); a company without any security is looked up by its ISIN (``etf_company_isin_map``), one without an ISIN
    -- or one whose ISIN has no entry -- by the Yahoo symbol of its listing (``etf_company_listing_map``)."""
    out: dict[int, list[dict[str, Any]]] = {}
    for row in exposure.get("companies") or []:
        if not row.get("etf_value_eur"):
            continue
        group, origin = _company_group(conn, layers, row), ORIGIN_COMPANY
        if group is None:
            isins = company_isins(row, issuer_groups or {})
            group, origin = (_isin_group(layers, isins) if isins else None), ORIGIN_COMPANY_ISIN
        if group is None:
            symbols = company_symbols(row, issuer_groups or {}, table or eh.ListingTable())
            group, origin = (_isin_group(layers, symbols) if symbols else None), ORIGIN_COMPANY_LISTING
        if group is None:
            continue
        for c in row["contributors"]:
            out.setdefault(int(c["etf_security_id"]), []).append({"name": row["name"], "group": group, "gics": str(c.get("gics_sector") or ""), "value": float(c["value_eur"]),
                                                                   "origin": origin})
    return out


def _take(pools: list[dict[str, Any]], amount: float) -> float:
    """Takes up to ``amount`` EUR out of the categories, pro rata to their value; returns what could not be taken."""
    available = sum(p["value_eur"] for p in pools)
    if available <= _EPS:
        return amount
    used = min(amount, available)
    for p in pools:
        p["value_eur"] -= used * p["value_eur"] / available
    return amount - used


def deduct_companies(parts: list[dict[str, Any]], items: list[dict[str, Any]]) -> float:
    """Takes the value of the company-assigned holdings out of the ETF's provider categories (``parts``, changed in place) and sets ``accepted`` on every item.

    Known category (the holding's own GICS sector is a category of the allocation): taken from it.  Otherwise the value is *estimated* out of the categories mapped to the
    company's own group, else out of the unmapped ones that are sectors, pro rata.  Returns the estimated EUR."""
    estimated = 0.0
    for item in items:
        pools = [p for p in parts if item["gics"] and p["category"].strip().lower() == item["gics"].strip().lower()]
        guess = not pools
        if guess:
            pools = [p for p in parts if p["group"] == item["group"]]
            if not pools:
                pools = [p for p in parts if p["group"] is None and not str(p.get("reason") or "").startswith(_NOT_A_SECTOR)]
        left = _take(pools, item["value"])
        if left > _EPS:  # not enough in the likely categories: the rest from all of them
            guess = True
            left = _take(parts, left)
        item["accepted"] = item["value"] - left
        if guess:
            estimated += item["accepted"]
    return estimated


def _rounded(value: Any, key: str = "") -> Any:
    if isinstance(value, float):
        return round(value, 2 if key.endswith("_eur") else 4)
    if isinstance(value, dict):
        return {k: _rounded(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v, key) for v in value]
    return value


# contribution field -> the field of its ``etfs`` row (``is_etf`` is always true for a contribution, ``weight_pct`` is market_value_eur / total_market_value_eur)
ETF_ROW_FIELDS = {"symbol": "symbol", "day_change_pct": "day_change_pct", "etf_value_eur": "market_value_eur", "etf_as_of": "as_of", "is_etf": None, "weight_pct": None}


def public_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """The answer of the MCP tool: every EUR figure to the cent, every other number to 4 decimals (the server refuses answers above 40,000 characters;
    full float precision was about an eighth of the answer), an ETF contribution without what its ``etfs`` row carries once or what follows from its value (``ETF_ROW_FIELDS``:
    symbol, daily change, value and as-of date of the ETF, the share of the invested value; the contribution names its ETF by ``security_id``), and the companies named in a
    contribution with their value in whole EUR.  ``checks`` and the group figures were computed before, on the exact and complete entries."""
    out = dict(result)
    def slim(p: Mapping[str, Any]) -> Mapping[str, Any]:
        if p.get("origin") == ORIGIN_DIRECT:
            return p
        out = {k: v for k, v in p.items() if k not in ETF_ROW_FIELDS}
        if "companies" in out:
            out["companies"] = [{**c, "value_eur": round(float(c["value_eur"]))} for c in out["companies"]]
        return out

    out["sectors"] = [{**g, "positions": [slim(p) for p in g.get("positions") or []]} for g in result.get("sectors") or []]
    return _rounded(out)


def build_sector_overview(
    conn: sqlite3.Connection,
    *,
    as_of: Optional[str] = None,
    universe_dir: Optional[Path] = None,
    max_sector_weight: Optional[float] = None,
    lookthrough: bool = True,
) -> dict[str, Any]:
    """The sector overview of the open positions (see the module docstring).  Read-only."""
    import analysis_engine

    limit_pct = (RiskGuardConfig().max_sector_weight if max_sector_weight is None else float(max_sector_weight)) * 100.0
    context = analysis_engine.build_portfolio_context(as_of=as_of, connection=conn)
    evaluation = str(context.evaluation_as_of)[:10]
    warnings: list[str] = []
    try:
        layers = sector_assignment.read_layers(None if universe_dir is None else Path(universe_dir) / sector_assignment.PUBLIC_NAME)
    except (OSError, ValueError, sector_assignment.SectorMapError):
        layers = None
        warnings.append("SECTOR_MAP_UNAVAILABLE: no sector could be resolved; every position is shown as not assigned")
    lt = _lookthrough_layers(universe_dir, set(layers.groups) if layers is not None else None, warnings) if lookthrough else None
    if lt is not None:
        warnings.extend(lt.warnings)
    exposures = [e for e in context.exposures if e.quantity is not None and float(e.quantity) > 0]
    ids = [int(e.security_id) for e in exposures]
    info = _security_info(conn, ids)
    changes = day_changes(conn, ids, evaluation)
    positions: list[dict[str, Any]] = []
    unvalued: list[dict[str, Any]] = []
    for exposure in exposures:
        sid = int(exposure.security_id)
        name, asset_type, isin = info.get(sid, (None, None, None))
        try:
            key = sector_assignment.listing_key(conn, sid)
        except sqlite3.Error:  # no alias tables: the plain symbol is the key
            key = exposure.symbol
        resolved = sector_assignment.resolve(layers, key) if layers is not None else {"sector": None, "source": sector_assignment.SOURCE_NONE}
        row = {
            "security_id": sid, "symbol": exposure.symbol, "name": name, "asset_type": asset_type, "is_etf": str(asset_type or "").lower() == "etf", "isin": isin,
            "strategy": exposure.strategy, "quantity": _num(exposure.quantity), "sector": resolved["sector"], "sector_source": resolved["source"],
        }
        value = _num(exposure.market_value_eur)
        if value is None or value <= 0:
            row["reasons"] = list(exposure.valuation_quality.details)
            unvalued.append(row)
            continue
        change = changes.get(sid, {})
        row.update(market_value_eur=value, day_change_pct=change.get("pct"), day_change_date=change.get("date"))
        positions.append(row)
    total = sum(p["market_value_eur"] for p in positions)

    entries: list[dict[str, Any]] = []
    etfs: list[dict[str, Any]] = []
    by_etf: dict[int, list[dict[str, Any]]] = {}
    if lookthrough and layers is not None and lt is not None and any(p["is_etf"] for p in positions):
        try:
            exposure = effective_exposure.build_effective_exposure(conn, as_of=as_of, universe_dir=universe_dir, top=1, context=context, with_companies=True)
            try:
                issuer_groups = eh.read_layers(universe_dir).issuer_groups
            except eh.HoldingsError:
                issuer_groups = {}
            try:
                table = eh.read_listing_table(universe_dir)
            except eh.HoldingsError as exc:
                table = eh.ListingTable()
                warnings.append(f"LISTING_TABLE_UNAVAILABLE: {exc}; companies without a security are assigned by their ISIN only")
            by_etf = company_items(conn, layers, exposure, issuer_groups, table)
            for held in exposure["etfs"]:
                if held["status"] not in (eh.APPLIED, eh.STALE):
                    warnings.append(f"COMPANY_SPLIT_UNAVAILABLE_{held['status']}: {held['symbol']} is split by provider category only ({held['detail']})")
        except (OSError, ValueError, sqlite3.Error) as exc:
            warnings.append(f"COMPANY_ASSIGNMENT_UNAVAILABLE: {exc}; ETFs are split by provider category only")
    for position in positions:
        position["origin"], position["category"], position["etf_weight_pct"] = ORIGIN_DIRECT, None, None
        if not (lookthrough and position["is_etf"]):
            entries.append(position)
            continue
        if lt is None:
            status, allocation, detail = etf_lookthrough.NO_DATA, None, "look-through data unavailable"
        else:
            status, allocation, detail = etf_lookthrough.status_of(lt, position["isin"], evaluation)
        item = {"security_id": position["security_id"], "symbol": position["symbol"], "name": position["name"], "isin": position["isin"], "strategy": position["strategy"],
                "market_value_eur": position["market_value_eur"], "day_change_pct": position["day_change_pct"],
                "status": status, "detail": detail, "as_of": allocation.as_of if allocation else None, "provider": allocation.provider if allocation else None,
                "source": allocation.source if allocation else None, "mapped_value_eur": 0.0, "mapped_pct": None, "categories": 0,
                "company_value_eur": 0.0, "company_count": 0, "company_isin_value_eur": 0.0, "company_isin_count": 0,
                "company_listing_value_eur": 0.0, "company_listing_count": 0, "estimated_value_eur": 0.0}
        etfs.append(item)
        if allocation is None:
            entries.append(position)  # stays one unresolved position (its own sector entry, or not assigned)
            continue
        parts = [dict(part) for part in etf_lookthrough.contributions(allocation, position["market_value_eur"], lt.mapping)]
        resolved = by_etf.get(position["security_id"], [])
        item["estimated_value_eur"] = deduct_companies(parts, resolved) if resolved else 0.0
        parts = [part for part in parts if part["value_eur"] > _EPS]
        item["categories"] = len(parts)
        by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for company in resolved:
            if company["accepted"] > _EPS:
                by_group.setdefault((company["group"], company["origin"]), []).append(company)
        for (group, origin), members in sorted(by_group.items()):
            value = sum(m["accepted"] for m in members)
            members.sort(key=lambda m: (-m["accepted"], m["name"]))
            entries.append({  # slim: what the ETF itself says (name, strategy, ISIN, quantity, asset type) is in ``etfs``, the sector is the group the entry sits in; none of it is repeated in every contribution
                "security_id": position["security_id"], "symbol": position["symbol"], "is_etf": True, "sector": group,
                "market_value_eur": value, "day_change_pct": position["day_change_pct"],
                "origin": origin, "etf_weight_pct": value / position["market_value_eur"] * 100.0,
                "etf_value_eur": position["market_value_eur"], "etf_as_of": allocation.as_of,
                "company_count": len(members), "companies": [{"name": m["name"], "value_eur": m["accepted"]} for m in members[:COMPANIES_SHOWN]],
            })
            item["company_value_eur"] += value
            item["company_count"] += len(members)
            if origin == ORIGIN_COMPANY_ISIN:
                item["company_isin_value_eur"] += value
                item["company_isin_count"] += len(members)
            elif origin == ORIGIN_COMPANY_LISTING:
                item["company_listing_value_eur"] += value
                item["company_listing_count"] += len(members)
            item["mapped_value_eur"] += value
        if item["estimated_value_eur"] > _EPS:
            warnings.append(f"COMPANY_SPLIT_ESTIMATED: {position['symbol']}: {item['estimated_value_eur']:.0f} EUR of company holdings had no provider sector and were taken from the fitting categories pro rata")
        for part in parts:
            entries.append({
                "security_id": position["security_id"], "symbol": position["symbol"], "is_etf": True, "sector": part["group"],
                "market_value_eur": part["value_eur"], "day_change_pct": position["day_change_pct"],
                "origin": ORIGIN_ETF, "category": part["category"], **({"category_reason": part["reason"]} if part["reason"] else {}), "etf_weight_pct": part["provider_weight_pct"],
                "etf_value_eur": position["market_value_eur"], "etf_as_of": allocation.as_of,
            })
            if part["group"] is not None:
                item["mapped_value_eur"] += part["value_eur"]
        item["mapped_pct"] = item["mapped_value_eur"] / position["market_value_eur"] * 100.0
    groups = group_positions(entries, total, limit_pct)
    for group in groups:
        for entry in group["positions"]:
            entry["weight_pct"] = entry["market_value_eur"] / total * 100.0 if total > _EPS else 0.0
            if entry["origin"] != ORIGIN_DIRECT:
                entry.pop("sector", None)  # the group the entry sits in says it; only the grouping above needed the key

    etf_total = sum(e["market_value_eur"] for e in etfs)
    covered = sum(e["market_value_eur"] for e in etfs if e["status"] in (etf_lookthrough.APPLIED, etf_lookthrough.STALE))
    for item in etfs:
        if item["status"] == etf_lookthrough.STALE:
            warnings.append(f"LOOKTHROUGH_STALE: allocation of {item['symbol']} is from {item['as_of']}")
        elif item["status"] != etf_lookthrough.APPLIED:
            warnings.append(f"LOOKTHROUGH_{item['status']}: {item['symbol']} stays one unresolved position ({item['detail']})")
    context_total = _num(context.total_market_value)
    if unvalued:
        warnings.append(f"UNVALUED_POSITIONS: {len(unvalued)} open position(s) without a EUR valuation are not part of the values")
    if context_total is not None and abs(context_total - total) > 0.01:
        warnings.append("TOTAL_MISMATCH: the sum of the valued positions differs from the portfolio context")
    change, coverage = group_change_pct(positions)
    sector_sum = sum(g["market_value_eur"] for g in groups)
    assigned = sum(g["market_value_eur"] for g in groups if g["kind"] == KIND_SECTOR)
    return {
        "ok": True, "operation": OPERATION, "as_of": evaluation, "base_currency": "EUR",
        "total_market_value_eur": total, "position_count": len(positions), "limit_pct": limit_pct,
        "day_change_pct": change, "day_coverage_pct": coverage,
        "lookthrough_enabled": bool(lookthrough),
        "assigned_value_eur": assigned,
        "direct_value_eur": sum(g["direct_value_eur"] for g in groups), "etf_value_eur": sum(g["etf_value_eur"] for g in groups),
        "etf_category_value_eur": sum(g["market_value_eur"] for g in groups if g["kind"] == KIND_ETF_CATEGORY),
        "etf_company_value_eur": sum(e["company_value_eur"] for e in etfs),
        "etf_company_isin_value_eur": sum(e["company_isin_value_eur"] for e in etfs),
        "etf_company_listing_value_eur": sum(e["company_listing_value_eur"] for e in etfs),
        "not_assessed_value_eur": sum(g["market_value_eur"] for g in groups if g["kind"] != KIND_SECTOR),
        "unassigned_value_eur": sum(g["market_value_eur"] for g in groups if g["kind"] == KIND_UNASSIGNED),
        "lookthrough": {"etf_count": len(etfs), "etf_value_eur": etf_total, "covered_value_eur": covered, "coverage_pct": covered / etf_total * 100.0 if etf_total > _EPS else None,
                        "mapped_value_eur": sum(e["mapped_value_eur"] for e in etfs), "mapped_pct": sum(e["mapped_value_eur"] for e in etfs) / etf_total * 100.0 if etf_total > _EPS else None},
        "etfs": etfs, "sectors": groups, "unvalued": unvalued,
        "checks": {"sector_sum_eur": sector_sum, "context_total_eur": context_total,
                   "reconciles": None if context_total is None else abs(context_total - total) <= 0.01 and abs(sector_sum - total) <= 0.01,  # None while the context has no total (an unvalued position)
                   "groups_sum_matches_total": abs(sector_sum - total) <= 0.01},
        "etf_note": "Companies of an ETF with a project sector go to that sector (etf_company; a company without a security by its ISIN in the sector map: etf_company_isin_map, otherwise by the Yahoo symbol of its listing: etf_company_listing_map); the rest of an ETF with an official sector allocation is split into its categories (etf_lookthrough); categories that fit no single sector group stay unmapped (NOT_ASSESSED).",
        "warnings": warnings,
    }

# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Calculate the valuation multiples of the active stocks from VIPER's prices and fundamentals (``valuation_metrics``, source ``VIPER_CALCULATED``).

Dry-run by default: prints what would be written.  ``--write`` upserts one row per security and price date, so a second run on the same data changes
nothing but ``calculated_at``.  A security without a price or fundamentals gets no row.  See ``valuation_metrics.py`` for the formulas.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths
import valuation_metrics


DB_PATH = runtime_paths.default_db_path()


def _fmt(value) -> str:
    return "-" if value is None else (f"{value:,.2f}" if abs(value) < 1e6 else f"{value / 1e9:,.2f}B")


def refresh(conn: sqlite3.Connection, security_ids: list[int] | None, as_of: str | None, write: bool) -> list[valuation_metrics.ValuationRow]:
    if write and not valuation_metrics.table_exists(conn):
        raise RuntimeError("valuation_metrics is missing: run Migrate-TradingValuationMetrics.py --write first")
    source = valuation_metrics.source_id(conn) if write else None
    if write and source is None:
        raise RuntimeError("data source VIPER_CALCULATED is missing: run Migrate-TradingValuationMetrics.py --write first")
    query = "SELECT id FROM security WHERE active = 1 AND lower(asset_type) NOT IN ('etf', 'fund')"
    params: list[object] = []
    if security_ids:
        query = f"SELECT id FROM security WHERE id IN ({', '.join('?' * len(security_ids))})"
        params = list(security_ids)
    rows = []
    for (security_id,) in conn.execute(query + " ORDER BY id", params).fetchall():
        row = valuation_metrics.calculate(conn, security_id, as_of)
        if row is None:
            continue
        rows.append(row)
        if write:
            valuation_metrics.upsert(conn, row, source)
    if write:
        conn.commit()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate valuation multiples from VIPER prices and fundamentals (dry-run by default)")
    parser.add_argument("--db-path", "--db", dest="db_path", type=Path, default=DB_PATH)
    parser.add_argument("--security-id", type=int, action="append", dest="security_ids", help="only this security_id (repeatable)")
    parser.add_argument("--as-of", help="price date YYYY-MM-DD (default: latest price)")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not args.db_path.is_file():
        raise FileNotFoundError(f"Trading DB not found: {args.db_path}")
    if args.write:
        conn = sqlite3.connect(args.db_path)
        conn.execute("PRAGMA foreign_keys=ON")
    else:
        conn = sqlite3.connect(f"file:///{args.db_path.as_posix()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
    try:
        rows = refresh(conn, args.security_ids, args.as_of, args.write)
        symbols = dict(conn.execute("SELECT id, symbol FROM security").fetchall())
        for r in rows:
            print(f"{symbols.get(r.security_id) or r.security_id:<8} {r.as_of_date} {r.price_currency or '':<3} cap={_fmt(r.market_cap)} ev={_fmt(r.enterprise_value)} "
                  + " ".join(f"{m}={_fmt(getattr(r, m))}" for m in valuation_metrics.MULTIPLES)
                  + (f" | missing: {'; '.join(r.missing)}" if r.missing else ""))
        print(f"{'written' if args.write else 'dry-run'}: {len(rows)} securities")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

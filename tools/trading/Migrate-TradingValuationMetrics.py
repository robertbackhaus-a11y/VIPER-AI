# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Add the relation of the valuation multiples calculated from VIPER's own data (``valuation_metrics``) and its data source ``VIPER_CALCULATED``."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths


DB_PATH = runtime_paths.default_db_path()
FEATURE_VERSION_KEY = "valuation_metrics_schema_version"
FEATURE_VERSION = "1"

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS valuation_metrics (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id                 INTEGER NOT NULL,
    as_of_date                  TEXT NOT NULL,
    price                       REAL,
    price_currency              TEXT,
    currency                    TEXT,
    ttm_period_end              TEXT,
    ttm_method                  TEXT,
    balance_sheet_period_end    TEXT,
    shares_outstanding          REAL,
    revenue_ttm                 REAL,
    net_income_ttm              REAL,
    ebitda_ttm                  REAL,
    free_cash_flow_ttm          REAL,
    total_equity                REAL,
    cash                        REAL,
    total_debt                  REAL,
    market_cap                  REAL,
    enterprise_value            REAL,
    pe_ttm                      REAL,
    ps_ttm                      REAL,
    pb                          REAL,
    ev_revenue                  REAL,
    ev_ebitda                   REAL,
    p_fcf                       REAL,
    fcf_yield_pct               REAL,
    missing                     TEXT,
    source_id                   INTEGER NOT NULL,
    calculated_at               TEXT NOT NULL,
    FOREIGN KEY (security_id) REFERENCES security(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES data_sources(id),
    UNIQUE (security_id, as_of_date, source_id)
);
CREATE INDEX IF NOT EXISTS idx_valuation_metrics_security_date
ON valuation_metrics(security_id, as_of_date DESC);
INSERT OR IGNORE INTO data_sources(name, source_type, url) VALUES ('VIPER_CALCULATED', 'calculated', NULL);
"""


def inspect_migration(conn: sqlite3.Connection) -> dict:
    schema = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    if schema is None or schema[0] != "2.0":
        raise RuntimeError("valuation metrics migration requires schema_version 2.0")
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='valuation_metrics'"
    ).fetchone()
    source = conn.execute("SELECT id FROM data_sources WHERE name = 'VIPER_CALCULATED'").fetchone()
    version = conn.execute("SELECT value FROM metadata WHERE key=?", (FEATURE_VERSION_KEY,)).fetchone()
    row_count = None
    if table is not None:
        row_count = conn.execute("SELECT COUNT(*) FROM valuation_metrics").fetchone()[0]
    return {
        "table_exists": table is not None,
        "row_count": row_count,
        "source_id": source[0] if source else None,
        "feature_version": version[0] if version else None,
        "target_feature_version": FEATURE_VERSION,
    }


def apply_migration(conn: sqlite3.Connection) -> dict:
    before = inspect_migration(conn)
    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + MIGRATION_SQL
            + f"""
        INSERT INTO metadata(key, value, updated_at)
        VALUES ('{FEATURE_VERSION_KEY}', '{FEATURE_VERSION}', CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
        COMMIT;
        """
        )
    except Exception:
        conn.rollback()
        raise
    return {"before": before, "after": inspect_migration(conn)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate the calculated valuation multiples relation (dry-run by default)"
    )
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not args.db_path.is_file():
        raise FileNotFoundError(f"Trading DB not found: {args.db_path}")
    if args.write:
        conn = sqlite3.connect(args.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
    else:
        conn = sqlite3.connect(f"file:///{args.db_path.as_posix()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
    try:
        print(apply_migration(conn) if args.write else {"dry_run": True, **inspect_migration(conn)})
    finally:
        conn.close()


if __name__ == "__main__":
    main()

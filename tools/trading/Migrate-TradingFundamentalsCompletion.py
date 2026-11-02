# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Add the two relations of the automatic fundamentals completion (``fundamentals_completion.py``): the gap status per security and input
(``fundamentals_gap``) and the research jobs (``fundamentals_research_job``)."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths


DB_PATH = runtime_paths.default_db_path()
FEATURE_VERSION_KEY = "fundamentals_completion_schema_version"
FEATURE_VERSION = "1"

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS fundamentals_research_job (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id         INTEGER NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('open', 'done', 'not_available', 'failed')),
    request_key         TEXT NOT NULL,
    request_json        TEXT NOT NULL,
    prompt_path         TEXT,
    answer_path         TEXT,
    result_json         TEXT,
    detail              TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    finished_at         TEXT,
    FOREIGN KEY (security_id) REFERENCES security(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fundamentals_research_job_open
ON fundamentals_research_job(security_id) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_fundamentals_research_job_security
ON fundamentals_research_job(security_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fundamentals_gap (
    security_id         INTEGER NOT NULL,
    gap                 TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('research', 'closed', 'unavailable')),
    periods             TEXT,
    detail              TEXT,
    trail_json          TEXT NOT NULL,
    research_job_id     INTEGER,
    first_seen_at       TEXT NOT NULL,
    checked_at          TEXT NOT NULL,
    closed_at           TEXT,
    PRIMARY KEY (security_id, gap),
    FOREIGN KEY (security_id) REFERENCES security(id) ON DELETE CASCADE,
    FOREIGN KEY (research_job_id) REFERENCES fundamentals_research_job(id) ON DELETE SET NULL
);
"""


def inspect_migration(conn: sqlite3.Connection) -> dict:
    schema = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    if schema is None or schema[0] != "2.0":
        raise RuntimeError("fundamentals completion migration requires schema_version 2.0")
    tables = {name for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('fundamentals_gap', 'fundamentals_research_job')")}
    version = conn.execute("SELECT value FROM metadata WHERE key=?", (FEATURE_VERSION_KEY,)).fetchone()
    return {
        "tables": sorted(tables),
        "gap_rows": conn.execute("SELECT COUNT(*) FROM fundamentals_gap").fetchone()[0] if "fundamentals_gap" in tables else None,
        "job_rows": conn.execute("SELECT COUNT(*) FROM fundamentals_research_job").fetchone()[0] if "fundamentals_research_job" in tables else None,
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
    parser = argparse.ArgumentParser(description="Migrate the fundamentals completion relations (dry-run by default)")
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

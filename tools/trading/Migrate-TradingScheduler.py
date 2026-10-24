# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Add the tables of VIPER's integrated task scheduler (``scheduled_job``, ``job_run``; see ``job_store.py``) and its seven jobs - the former Windows
scheduled tasks, created disabled (the takeover enables a job and disables its Windows task). Dry run by default."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import job_store
import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths

DB_PATH = runtime_paths.default_db_path()
FEATURE_VERSION_KEY = job_store.FEATURE_VERSION_KEY
FEATURE_VERSION = job_store.FEATURE_VERSION


def inspect_migration(conn: sqlite3.Connection) -> dict:
    schema = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    if schema is None or schema[0] != "2.0":
        raise RuntimeError("scheduler migration requires schema_version 2.0")
    exists = job_store.available(conn)
    version = conn.execute("SELECT value FROM metadata WHERE key=?", (FEATURE_VERSION_KEY,)).fetchone()
    return {
        "table_exists": exists,
        "row_count": conn.execute("SELECT COUNT(*) FROM scheduled_job").fetchone()[0] if exists else None,
        "feature_version": version[0] if version else None,
        "target_feature_version": FEATURE_VERSION,
    }


def apply_migration(conn: sqlite3.Connection) -> dict:
    before = inspect_migration(conn)
    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + job_store.SCHEMA_SQL
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
    conn.execute("BEGIN IMMEDIATE")
    added = job_store.seed(conn)
    conn.execute("COMMIT")
    return {"before": before, "after": {**inspect_migration(conn), "jobs_added": added}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the integrated task scheduler tables and jobs (dry-run by default)")
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

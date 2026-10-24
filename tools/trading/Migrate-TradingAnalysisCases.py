# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Add the tables of the reasoning analyses (``analysis_case``, ``analysis_step``; see ``analysis_store.py``) and, when LangGraph is installed (extra
``web``), the checkpoint tables of its ``SqliteSaver`` (``checkpoints``, ``writes``), so the workflow needs no database of its own. Dry run by default."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import analysis_store
import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths

DB_PATH = runtime_paths.default_db_path()
FEATURE_VERSION_KEY = analysis_store.FEATURE_VERSION_KEY
FEATURE_VERSION = analysis_store.FEATURE_VERSION


def inspect_migration(conn: sqlite3.Connection) -> dict:
    schema = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    if schema is None or schema[0] != "2.0":
        raise RuntimeError("analysis case migration requires schema_version 2.0")
    exists = analysis_store.available(conn)
    version = conn.execute("SELECT value FROM metadata WHERE key=?", (FEATURE_VERSION_KEY,)).fetchone()
    return {
        "table_exists": exists,
        "row_count": conn.execute("SELECT COUNT(*) FROM analysis_case").fetchone()[0] if exists else None,
        "feature_version": version[0] if version else None,
        "target_feature_version": FEATURE_VERSION,
    }


def apply_migration(conn: sqlite3.Connection) -> dict:
    before = inspect_migration(conn)
    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + analysis_store.SCHEMA_SQL
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
    after = inspect_migration(conn)
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        after["checkpoint_tables"] = "skipped: langgraph not installed (created on the first analysis)"
    else:
        SqliteSaver(conn).setup()
        after["checkpoint_tables"] = "created"
    return {"before": before, "after": after}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the reasoning analysis tables (dry-run by default)")
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

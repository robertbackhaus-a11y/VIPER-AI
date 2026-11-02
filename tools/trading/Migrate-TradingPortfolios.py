# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Additive migration: several portfolios (depots) in the one trading database (see ``portfolio_scope.py``).  Dry run unless ``--write``.

In ONE transaction:

* creates ``portfolio`` and the default portfolio 1 ("Main", ``--default-name``);
* adds ``transactions.portfolio_id`` (``NOT NULL DEFAULT 1``): every existing transaction belongs to the default portfolio, nothing else of a transaction changes;
  the unique index of ``external_id`` becomes unique per portfolio (the same export may be imported into two portfolios);
* moves the rows of the table ``positions`` unchanged into ``portfolio_position`` (portfolio 1) and replaces the table by the VIEW ``positions``, which adds up
  all portfolios per security -- with one portfolio it returns exactly the former rows;
* adds ``analysis_case.portfolio_id`` (NULL = the total) when the analysis tables exist;
* records ``metadata.portfolio_schema_version = 1``.

Before committing it compares every row of the ``positions`` view with the former table (all columns, exact), the transactions (count and every former column)
and runs ``integrity_check`` / ``foreign_key_check``; any difference rolls everything back.  A backup copy is made first through the SQLite backup API
(``--no-backup`` to skip, ``--backup-path`` to choose it).
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import portfolio_scope
import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths

DB_PATH = runtime_paths.default_db_path()
FEATURE_VERSION_KEY = portfolio_scope.FEATURE_VERSION_KEY
FEATURE_VERSION = portfolio_scope.FEATURE_VERSION

PORTFOLIO_SQL = """
CREATE TABLE portfolio (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    description     TEXT,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

POSITION_SQL = """
CREATE TABLE portfolio_position (
    portfolio_id            INTEGER NOT NULL,
    security_id             INTEGER NOT NULL,

    shares                  REAL NOT NULL DEFAULT 0,

    avg_cost                REAL,
    remaining_cost_basis    REAL,

    currency                TEXT,

    invested_amount         REAL,
    realized_gain           REAL DEFAULT 0,

    first_transaction_at    TEXT,
    last_transaction_at     TEXT,

    transaction_count       INTEGER NOT NULL DEFAULT 0,

    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (portfolio_id, security_id),

    FOREIGN KEY (portfolio_id)
        REFERENCES portfolio(id)
        ON DELETE CASCADE,
    FOREIGN KEY (security_id)
        REFERENCES security(id)
        ON DELETE CASCADE
)"""

_COLUMNS = ", ".join(portfolio_scope.POSITION_COLUMNS)


class MigrationError(RuntimeError):
    """The migration cannot be applied safely."""


def _exists(conn: sqlite3.Connection, kind: str, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def plan(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read-only: what the migration would do, or why it cannot."""
    schema = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    if schema is None or schema[0] != "2.0":
        return {"status": "BLOCKED", "blockers": ["portfolio migration requires schema_version 2.0"]}
    if portfolio_scope.available(conn):
        return {"status": "ALREADY_MIGRATED", "portfolios": portfolio_scope.list_portfolios(conn)}
    blockers = []
    if not _exists(conn, "table", "positions"):
        blockers.append("table positions not found")
    if _exists(conn, "table", "portfolio"):
        blockers.append("a table portfolio exists already")
    if "portfolio_id" in _columns(conn, "transactions"):
        blockers.append("transactions.portfolio_id exists already")
    analysis = _exists(conn, "table", "analysis_case")
    return {
        "status": "BLOCKED" if blockers else "READY", "blockers": blockers,
        "transactions": int(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]),
        "positions": int(conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]) if not blockers else None,
        "positions_held": int(conn.execute("SELECT COUNT(*) FROM positions WHERE shares > 0").fetchone()[0]) if not blockers else None,
        "analysis_case_column": analysis and "portfolio_id" not in _columns(conn, "analysis_case"),
    }


def _backup(conn: sqlite3.Connection, target: Path) -> Path:
    destination = sqlite3.connect(str(target))
    try:
        conn.backup(destination)
    finally:
        destination.close()
    return target


def apply(conn: sqlite3.Connection, backup_path: Optional[Path] = None, default_name: str = portfolio_scope.DEFAULT_PORTFOLIO_NAME) -> dict[str, Any]:
    """Apply the migration (connection with ``isolation_level=None``).  Raises ``MigrationError`` and changes nothing if it cannot."""
    found = plan(conn)
    if found["status"] == "ALREADY_MIGRATED":
        return found
    if found["status"] != "READY":
        raise MigrationError("; ".join(found["blockers"]))
    if backup_path is not None:
        _backup(conn, backup_path)
    transaction_columns = ", ".join(f'"{c}"' for c in _columns(conn, "transactions"))
    transactions_before = conn.execute(f"SELECT {transaction_columns} FROM transactions ORDER BY id").fetchall()
    positions_before = conn.execute(f"SELECT {_COLUMNS} FROM positions ORDER BY security_id").fetchall()
    # ADD COLUMN with a REFERENCES clause and a non-NULL default is refused while foreign keys are enforced; the foreign_key_check below verifies the result
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(PORTFOLIO_SQL)
        conn.execute("INSERT INTO portfolio(id, name, description) VALUES (?, ?, ?)",
                     (portfolio_scope.DEFAULT_PORTFOLIO_ID, " ".join(default_name.split()) or portfolio_scope.DEFAULT_PORTFOLIO_NAME,
                      "Standard-Depot: alle Daten vor der Einführung mehrerer Depots"))
        conn.execute(f"ALTER TABLE transactions ADD COLUMN portfolio_id INTEGER NOT NULL DEFAULT {portfolio_scope.DEFAULT_PORTFOLIO_ID} REFERENCES portfolio(id)")
        conn.execute("DROP INDEX IF EXISTS idx_transactions_external_id")
        conn.execute("CREATE UNIQUE INDEX idx_transactions_external_id ON transactions(portfolio_id, external_id) WHERE external_id IS NOT NULL")
        conn.execute("CREATE INDEX idx_transactions_portfolio_security ON transactions(portfolio_id, security_id, transaction_date)")
        conn.execute(POSITION_SQL)
        conn.execute(f"INSERT INTO portfolio_position(portfolio_id, {_COLUMNS}) SELECT {portfolio_scope.DEFAULT_PORTFOLIO_ID}, {_COLUMNS} FROM positions")
        conn.execute("DROP TABLE positions")
        conn.execute("CREATE INDEX idx_portfolio_position_security ON portfolio_position(security_id, shares)")
        conn.execute(f"CREATE VIEW positions AS {portfolio_scope.aggregate_positions_sql()}")
        if found["analysis_case_column"]:
            conn.execute("ALTER TABLE analysis_case ADD COLUMN portfolio_id INTEGER")
        conn.execute("INSERT INTO metadata(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (FEATURE_VERSION_KEY, FEATURE_VERSION))

        if conn.execute(f"SELECT {transaction_columns} FROM transactions ORDER BY id").fetchall() != transactions_before:
            raise MigrationError("a transaction changed")
        if conn.execute("SELECT COUNT(*) FROM transactions WHERE portfolio_id <> ?", (portfolio_scope.DEFAULT_PORTFOLIO_ID,)).fetchone()[0]:
            raise MigrationError("a transaction is not in the default portfolio")
        if conn.execute(f"SELECT {_COLUMNS} FROM positions ORDER BY security_id").fetchall() != positions_before:
            raise MigrationError("the positions view differs from the former positions table")
        for view in ("v_active_positions", "v_portfolio_market"):  # the views over positions still compile
            if _exists(conn, "view", view):
                conn.execute(f"SELECT COUNT(*) FROM {view}").fetchone()
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("integrity_check or foreign_key_check failed")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return {"status": "MIGRATED", "transactions": len(transactions_before), "positions": len(positions_before),
            "portfolios": portfolio_scope.list_portfolios(conn), "analysis_case_column": found["analysis_case_column"],
            "backup": str(backup_path) if backup_path else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Add several portfolios (depots) to the trading database (dry run by default)")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--write", action="store_true", help="apply the migration")
    parser.add_argument("--default-name", default=portfolio_scope.DEFAULT_PORTFOLIO_NAME, help="name of the default portfolio (default: Main)")
    parser.add_argument("--no-backup", action="store_true", help="skip the backup copy of the database (not recommended)")
    parser.add_argument("--backup-path", type=Path, default=None, help="where to put the backup copy (default: next to the database)")
    args = parser.parse_args()
    args.db_path = args.db_path.resolve()
    if args.backup_path is not None:
        args.backup_path = args.backup_path.resolve()
    if not args.db_path.exists():
        raise FileNotFoundError(f"Trading DB not found: {args.db_path}")
    if not args.write:
        conn = sqlite3.connect(f"file:///{args.db_path.as_posix()}?mode=ro", uri=True, timeout=10.0)
        try:
            print({"dry_run": True, **plan(conn)})
        finally:
            conn.close()
        return
    conn = sqlite3.connect(str(args.db_path), timeout=10.0, isolation_level=None)
    try:
        backup = None if args.no_backup else (args.backup_path or args.db_path.with_name(f"{args.db_path.name}.bak-pre-portfolios-{datetime.now():%Y%m%d-%H%M%S}"))
        print(apply(conn, backup, args.default_name))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

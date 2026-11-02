# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Additive migration: the cash (``portfolio_capital_state``) per portfolio (see ``portfolio_scope.py`` and ``capital_state.py``).  Dry run unless ``--write``.

Needs ``Migrate-TradingPortfolios.py`` and ``Migrate-TradingCapitalState.py``.  In ONE transaction:

* adds ``portfolio_capital_state.portfolio_id`` (``NOT NULL DEFAULT 1``): every recorded state belongs to the default portfolio 1 ("Main"), nothing else of a
  state changes, so Main's cash = the former global cash = the total;
* records ``metadata.portfolio_cash_schema_version = 1``.

Before committing it compares every former column of every state, checks that the resolved cash of the total and of Main equal the former global cash
(today and at every recorded ``as_of``) and runs ``integrity_check`` / ``foreign_key_check``; any difference rolls everything back.  Portfolios created
before this migration start without a state (their cash adds nothing to the total) until one is recorded (``Manage-TradingCapitalState.py set
--portfolio-id``).  A backup copy is made first through the SQLite backup API (``--no-backup`` to skip, ``--backup-path`` to choose it).
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import capital_state
import portfolio_scope
import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths

DB_PATH = runtime_paths.default_db_path()
FEATURE_VERSION_KEY = portfolio_scope.CASH_FEATURE_VERSION_KEY
FEATURE_VERSION = portfolio_scope.CASH_FEATURE_VERSION
TABLE = capital_state.TABLE_NAME


class MigrationError(RuntimeError):
    """The migration cannot be applied safely."""


def _columns(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})")]


def _resolved(conn: sqlite3.Connection, as_of: str, portfolio_id: Optional[int]) -> tuple:
    state = capital_state.resolve_capital_state(conn, as_of, portfolio_id=portfolio_id)
    return state.cash_available, state.buying_power, state.as_of


def _check_dates(conn: sqlite3.Connection) -> list[str]:
    """Today and every recorded ``as_of``: where the resolved cash must not change."""
    days = {r[0][:10] for r in conn.execute(f"SELECT as_of FROM {TABLE}")}
    return sorted(days | {date.today().isoformat()})


def plan(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read-only: what the migration would do, or why it cannot."""
    if portfolio_scope.cash_available(conn):
        return {"status": "ALREADY_MIGRATED"}
    blockers = []
    if not portfolio_scope.available(conn):
        blockers.append("the portfolio schema is missing (Migrate-TradingPortfolios.py --write first)")
    if not capital_state.capital_state_schema_available(conn):
        blockers.append("the capital-state schema is missing (Migrate-TradingCapitalState.py --write first)")
    elif "portfolio_id" in _columns(conn):
        blockers.append(f"{TABLE}.portfolio_id exists already")
    if blockers:
        return {"status": "BLOCKED", "blockers": blockers}
    today = date.today().isoformat()
    return {"status": "READY", "blockers": [], "states": int(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]),
            "cash_global_today": _resolved(conn, today, None)[0], "target_portfolio": portfolio_scope.label(conn, portfolio_scope.DEFAULT_PORTFOLIO_ID)}


def _backup(conn: sqlite3.Connection, target: Path) -> Path:
    destination = sqlite3.connect(str(target))
    try:
        conn.backup(destination)
    finally:
        destination.close()
    return target


def apply(conn: sqlite3.Connection, backup_path: Optional[Path] = None) -> dict[str, Any]:
    """Apply the migration (connection with ``isolation_level=None``).  Raises ``MigrationError`` and changes nothing if it cannot."""
    found = plan(conn)
    if found["status"] == "ALREADY_MIGRATED":
        return found
    if found["status"] != "READY":
        raise MigrationError("; ".join(found["blockers"]))
    if backup_path is not None:
        _backup(conn, backup_path)
    columns = ", ".join(f'"{c}"' for c in _columns(conn))
    states_before = conn.execute(f"SELECT {columns} FROM {TABLE} ORDER BY id").fetchall()
    days = _check_dates(conn)
    cash_before = {day: _resolved(conn, day, None) for day in days}
    main = portfolio_scope.DEFAULT_PORTFOLIO_ID
    # ADD COLUMN with a REFERENCES clause and a non-NULL default is refused while foreign keys are enforced; the foreign_key_check below verifies the result
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN portfolio_id INTEGER NOT NULL DEFAULT {main} REFERENCES portfolio(id)")
        conn.execute(f"CREATE INDEX idx_portfolio_capital_state_portfolio ON {TABLE}(portfolio_id, as_of DESC, id DESC)")
        conn.execute("INSERT INTO metadata(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (FEATURE_VERSION_KEY, FEATURE_VERSION))

        if conn.execute(f"SELECT {columns} FROM {TABLE} ORDER BY id").fetchall() != states_before:
            raise MigrationError("a capital state changed")
        if conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE portfolio_id <> ?", (main,)).fetchone()[0]:
            raise MigrationError("a capital state is not in the default portfolio")
        for day, before in cash_before.items():
            # Main must be active for the total to include it; a deactivated Main is checked on its own only
            total, of_main = _resolved(conn, day, None), _resolved(conn, day, main)
            main_active = conn.execute("SELECT active FROM portfolio WHERE id = ?", (main,)).fetchone()[0]
            if of_main != before or (main_active and total != before):
                raise MigrationError(f"the resolved cash of {day} differs: before {before}, total {total}, Main {of_main}")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("integrity_check or foreign_key_check failed")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    today = date.today().isoformat()
    return {"status": "MIGRATED", "states": len(states_before), "checked_dates": len(days), "cash_before_today": cash_before[today][0],
            "cash_main_today": _resolved(conn, today, main)[0], "cash_total_today": _resolved(conn, today, None)[0],
            "backup": str(backup_path) if backup_path else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep the cash (capital state) per portfolio (dry run by default)")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--write", action="store_true", help="apply the migration")
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
        conn.row_factory = sqlite3.Row
        try:
            print({"dry_run": True, **plan(conn)})
        finally:
            conn.close()
        return
    backup = None if args.no_backup else (args.backup_path or args.db_path.with_name(
        f"{args.db_path.name}.bak-pre-portfolio-cash-{datetime.now().strftime('%Y%m%d-%H%M%S')}"))
    conn = sqlite3.connect(str(args.db_path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        print(apply(conn, backup))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Additive migration: strategy assignments and Swing campaigns per portfolio (see ``portfolio_scope.py``).  Dry run unless ``--write``.

Needs ``Migrate-TradingPortfolios.py``, ``Migrate-TradingStrategyAssignments.py`` and (for the campaigns) ``Migrate-TradingSwingLifecycle.py``.  In ONE
transaction:

* ``strategy_assignment`` gets ``portfolio_id`` (``NOT NULL DEFAULT 1``).  Its unique key and the no-overlap triggers become per portfolio, so the same
  security can be Long-Term in one portfolio and Swing in another; SQLite cannot change a unique key in place, so the table is rebuilt under the same
  name with the same ids and values (the ``AUTOINCREMENT`` counter is kept);
* ``swing_campaign`` gets ``portfolio_id`` (``NOT NULL DEFAULT 1``); "one open campaign per security" becomes "per portfolio and security"; a campaign needs
  a Swing assignment of its own portfolio, and a transaction linked to a campaign event must belong to the campaign's portfolio;
* records ``metadata.portfolio_strategy_schema_version = 1``.

Every existing assignment and campaign belongs to the default portfolio 1 ("Main").  Before committing it compares every former column of every row and
checks that the active strategy and the open campaign of every security are unchanged for Main and for the total (today and at every recorded start
date), then runs ``integrity_check`` / ``foreign_key_check``; any difference rolls everything back.  A backup copy is made first through the SQLite
backup API (``--no-backup`` to skip, ``--backup-path`` to choose it).
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import portfolio_scope
import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths

DB_PATH = runtime_paths.default_db_path()
FEATURE_VERSION_KEY = portfolio_scope.STRATEGY_FEATURE_VERSION_KEY
FEATURE_VERSION = portfolio_scope.STRATEGY_FEATURE_VERSION
MAIN = portfolio_scope.DEFAULT_PORTFOLIO_ID

STRATEGY_TABLE = f"""CREATE TABLE strategy_assignment__new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id     INTEGER NOT NULL,
    strategy_type   TEXT NOT NULL
        CHECK (strategy_type IN ('long_term', 'swing', 'tactical', 'unknown')),
    effective_from  TEXT NOT NULL,
    effective_to    TEXT,
    source          TEXT,
    rationale       TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    portfolio_id    INTEGER NOT NULL DEFAULT {MAIN} REFERENCES portfolio(id),

    CHECK (effective_to IS NULL OR effective_to >= effective_from),

    FOREIGN KEY (security_id)
        REFERENCES security(id)
        ON DELETE CASCADE,

    UNIQUE (portfolio_id, security_id, effective_from)
)"""

STRATEGY_FORMER_COLUMNS = ("id", "security_id", "strategy_type", "effective_from", "effective_to", "source", "rationale", "created_at", "updated_at")

STRATEGY_OBJECTS = (
    "CREATE INDEX idx_strategy_assignment_security_dates ON strategy_assignment(security_id, effective_from, effective_to)",
    "CREATE INDEX idx_strategy_assignment_portfolio ON strategy_assignment(portfolio_id, security_id, effective_from, effective_to)",
    """CREATE TRIGGER trg_strategy_assignment_no_overlap_insert
BEFORE INSERT ON strategy_assignment
WHEN EXISTS (
    SELECT 1
    FROM strategy_assignment existing
    WHERE existing.portfolio_id = NEW.portfolio_id
      AND existing.security_id = NEW.security_id
      AND COALESCE(existing.effective_to, '9999-12-31') >= NEW.effective_from
      AND COALESCE(NEW.effective_to, '9999-12-31') >= existing.effective_from
)
BEGIN
    SELECT RAISE(ABORT, 'strategy assignment overlaps an existing interval');
END""",
    """CREATE TRIGGER trg_strategy_assignment_no_overlap_update
BEFORE UPDATE OF portfolio_id, security_id, effective_from, effective_to ON strategy_assignment
WHEN EXISTS (
    SELECT 1
    FROM strategy_assignment existing
    WHERE existing.portfolio_id = NEW.portfolio_id
      AND existing.security_id = NEW.security_id
      AND existing.id <> OLD.id
      AND COALESCE(existing.effective_to, '9999-12-31') >= NEW.effective_from
      AND COALESCE(NEW.effective_to, '9999-12-31') >= existing.effective_from
)
BEGIN
    SELECT RAISE(ABORT, 'strategy assignment overlaps an existing interval');
END""",
)

CAMPAIGN_TRIGGERS = ("trg_swing_campaign_assignment_insert", "trg_swing_campaign_assignment_update",
                     "trg_swing_campaign_event_transaction_insert", "trg_swing_campaign_event_transaction_update")

CAMPAIGN_OBJECTS = (
    "DROP INDEX idx_swing_campaign_one_open_per_security",
    "CREATE UNIQUE INDEX idx_swing_campaign_one_open_per_portfolio_security ON swing_campaign(portfolio_id, security_id) WHERE status = 'open'",
    "CREATE INDEX idx_swing_campaign_portfolio_security ON swing_campaign(portfolio_id, security_id, status, opened_at DESC)",
    """CREATE TRIGGER trg_swing_campaign_assignment_insert
BEFORE INSERT ON swing_campaign
WHEN NOT EXISTS (
    SELECT 1 FROM strategy_assignment sa
    WHERE sa.id = NEW.strategy_assignment_id
      AND sa.security_id = NEW.security_id
      AND sa.portfolio_id = NEW.portfolio_id
      AND sa.strategy_type = 'swing'
)
BEGIN
    SELECT RAISE(ABORT, 'campaign requires a matching swing strategy assignment');
END""",
    """CREATE TRIGGER trg_swing_campaign_assignment_update
BEFORE UPDATE OF security_id, strategy_assignment_id, portfolio_id ON swing_campaign
WHEN NOT EXISTS (
    SELECT 1 FROM strategy_assignment sa
    WHERE sa.id = NEW.strategy_assignment_id
      AND sa.security_id = NEW.security_id
      AND sa.portfolio_id = NEW.portfolio_id
      AND sa.strategy_type = 'swing'
)
BEGIN
    SELECT RAISE(ABORT, 'campaign requires a matching swing strategy assignment');
END""",
    """CREATE TRIGGER trg_swing_campaign_event_transaction_insert
BEFORE INSERT ON swing_campaign_event
WHEN NEW.transaction_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM swing_campaign campaign
    JOIN transactions transaction_row ON transaction_row.id = NEW.transaction_id
    WHERE campaign.id = NEW.campaign_id
      AND campaign.security_id = transaction_row.security_id
      AND campaign.portfolio_id = transaction_row.portfolio_id
)
BEGIN
    SELECT RAISE(ABORT, 'linked transaction must belong to the campaign security and portfolio');
END""",
    """CREATE TRIGGER trg_swing_campaign_event_transaction_update
BEFORE UPDATE OF campaign_id, transaction_id ON swing_campaign_event
WHEN NEW.transaction_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM swing_campaign campaign
    JOIN transactions transaction_row ON transaction_row.id = NEW.transaction_id
    WHERE campaign.id = NEW.campaign_id
      AND campaign.security_id = transaction_row.security_id
      AND campaign.portfolio_id = transaction_row.portfolio_id
)
BEGIN
    SELECT RAISE(ABORT, 'linked transaction must belong to the campaign security and portfolio');
END""",
)


class MigrationError(RuntimeError):
    """The migration cannot be applied safely."""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]


def _rows(conn: sqlite3.Connection, table: str, columns: list[str] | tuple[str, ...]) -> list[tuple]:
    names = ", ".join(f'"{c}"' for c in columns)
    return [tuple(r) for r in conn.execute(f"SELECT {names} FROM main.{table} ORDER BY id")]


def _check_dates(conn: sqlite3.Connection) -> list[str]:
    """Today and every recorded start and end of an assignment: where the active strategy must not change."""
    days = {str(r[0])[:10] for r in conn.execute("SELECT effective_from FROM main.strategy_assignment UNION SELECT effective_to FROM main.strategy_assignment "
                                                 "WHERE effective_to IS NOT NULL")}
    return sorted(days | {date.today().isoformat()})


def _state(conn: sqlite3.Connection, days: list[str], campaigns: bool) -> dict[str, Any]:
    """What a reader of ``conn`` (in its portfolio context) sees: the active strategies per security and day, the open campaigns per security."""
    strategies = {day: [tuple(r) for r in conn.execute(
        "SELECT security_id, strategy_type, id FROM strategy_assignment WHERE effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?) "
        "ORDER BY security_id, id", (day, day))] for day in days}
    open_campaigns = [tuple(r) for r in conn.execute("SELECT security_id, id, strategy_assignment_id FROM swing_campaign WHERE status = 'open' "
                                                     "ORDER BY security_id, id")] if campaigns else []
    return {"strategies": strategies, "open_campaigns": open_campaigns}


def plan(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read-only: what the migration would do, or why it cannot."""
    if portfolio_scope.strategies_available(conn):
        return {"status": "ALREADY_MIGRATED"}
    blockers = []
    if not portfolio_scope.available(conn):
        blockers.append("the portfolio schema is missing (Migrate-TradingPortfolios.py --write first)")
    if not _table_exists(conn, "strategy_assignment"):
        blockers.append("the strategy-assignment schema is missing (Migrate-TradingStrategyAssignments.py --write first)")
    elif "portfolio_id" in _columns(conn, "strategy_assignment"):
        blockers.append("strategy_assignment.portfolio_id exists already")
    elif _table_exists(conn, "strategy_assignment__new"):
        blockers.append("a table strategy_assignment__new exists (left over?)")
    if not (_table_exists(conn, "swing_campaign") and _table_exists(conn, "swing_campaign_event")):
        blockers.append("the Swing campaign schema is missing (Migrate-TradingSwingLifecycle.py --write first)")
    elif "portfolio_id" in _columns(conn, "swing_campaign"):
        blockers.append("swing_campaign.portfolio_id exists already")
    if blockers:
        return {"status": "BLOCKED", "blockers": blockers}
    return {"status": "READY", "blockers": [], "target_portfolio": portfolio_scope.label(conn, MAIN),
            "strategy_assignments": int(conn.execute("SELECT COUNT(*) FROM main.strategy_assignment").fetchone()[0]),
            "swing_campaigns": int(conn.execute("SELECT COUNT(*) FROM main.swing_campaign").fetchone()[0]),
            "swing_campaign_events": int(conn.execute("SELECT COUNT(*) FROM main.swing_campaign_event").fetchone()[0])}


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
    portfolio_scope.unscope(conn)
    strategy_columns = list(STRATEGY_FORMER_COLUMNS)
    if _columns(conn, "strategy_assignment") != strategy_columns:
        raise MigrationError(f"unexpected strategy_assignment columns: {_columns(conn, 'strategy_assignment')}")
    campaign_columns = _columns(conn, "swing_campaign")
    days = _check_dates(conn)
    assignments_before = _rows(conn, "strategy_assignment", strategy_columns)
    campaigns_before = _rows(conn, "swing_campaign", campaign_columns)
    events_before = _rows(conn, "swing_campaign_event", _columns(conn, "swing_campaign_event"))
    state_before = _state(conn, days, True)
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'strategy_assignment'").fetchone()
    # the rebuild drops a table other objects refer to, and ADD COLUMN with a REFERENCES clause and a non-NULL default is refused while foreign keys are
    # enforced; the foreign_key_check below verifies the result
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for trigger in CAMPAIGN_TRIGGERS:  # they name strategy_assignment / the campaign columns; recreated below
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        columns = ", ".join(strategy_columns)
        conn.execute(STRATEGY_TABLE)
        conn.execute(f"INSERT INTO strategy_assignment__new({columns}, portfolio_id) SELECT {columns}, {MAIN} FROM strategy_assignment ORDER BY id")
        conn.execute("DROP TABLE strategy_assignment")
        conn.execute("ALTER TABLE strategy_assignment__new RENAME TO strategy_assignment")
        if sequence is not None:
            conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'strategy_assignment'", (sequence[0],))
        for statement in STRATEGY_OBJECTS:
            conn.execute(statement)
        conn.execute(f"ALTER TABLE swing_campaign ADD COLUMN portfolio_id INTEGER NOT NULL DEFAULT {MAIN} REFERENCES portfolio(id)")
        for statement in CAMPAIGN_OBJECTS:
            conn.execute(statement)
        conn.execute("INSERT INTO metadata(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (FEATURE_VERSION_KEY, FEATURE_VERSION))

        if _rows(conn, "strategy_assignment", strategy_columns) != assignments_before:
            raise MigrationError("a strategy assignment changed")
        if _rows(conn, "swing_campaign", campaign_columns) != campaigns_before:
            raise MigrationError("a Swing campaign changed")
        if _rows(conn, "swing_campaign_event", _columns(conn, "swing_campaign_event")) != events_before:
            raise MigrationError("a Swing campaign event changed")
        for table in ("strategy_assignment", "swing_campaign"):
            if conn.execute(f"SELECT COUNT(*) FROM main.{table} WHERE portfolio_id <> ?", (MAIN,)).fetchone()[0]:
                raise MigrationError(f"a row of {table} is not in the default portfolio")
        main_active = conn.execute("SELECT active FROM portfolio WHERE id = ?", (MAIN,)).fetchone()[0]
        for context in ([MAIN, None] if main_active else [MAIN]):  # a deactivated Main is not part of the total
            portfolio_scope.scope(conn, context)
            try:
                if _state(conn, days, True) != state_before:
                    raise MigrationError(f"the strategies or open campaigns of {portfolio_scope.label(conn, context)} differ")
            finally:
                portfolio_scope.unscope(conn)
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("integrity_check or foreign_key_check failed")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return {"status": "MIGRATED", "strategy_assignments": len(assignments_before), "swing_campaigns": len(campaigns_before),
            "swing_campaign_events": len(events_before), "checked_dates": len(days), "portfolio": portfolio_scope.label(conn, MAIN),
            "backup": str(backup_path) if backup_path else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep strategy assignments and Swing campaigns per portfolio (dry run by default)")
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
        f"{args.db_path.name}.bak-pre-portfolio-strategies-{datetime.now().strftime('%Y%m%d-%H%M%S')}"))
    conn = sqlite3.connect(str(args.db_path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        print(apply(conn, backup))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

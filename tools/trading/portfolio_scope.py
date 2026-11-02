# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Several portfolios (depots) in the one trading database: the ``portfolio`` table, the per-portfolio positions and the ONE convention every reader uses.

Data model (``Migrate-TradingPortfolios.py``):

* ``portfolio``: id, name, description, active, created_at.  Portfolio 1 is the default portfolio ("Main"): the migration assigns everything that existed
  before to it, and a writer that does not know portfolios (``DEFAULT 1``) still writes there.  It can be renamed and deactivated, not deleted.
* ``transactions.portfolio_id``: every transaction belongs to exactly one portfolio.
* ``portfolio_position``: the positions rebuilt from the transactions, one row per ``(portfolio_id, security_id)``.
* ``positions`` is now a VIEW: the positions of all portfolios added up per security (shares, cost basis, invested amount and realized gain summed; the
  average cost is the summed cost basis per share).  Every existing reader of ``positions`` (engine, collectors, MCP tools) keeps working unchanged.

Security master data, prices, FX rates, fundamentals, news, the watchlist, strategy assignments and Swing campaigns stay global.

The convention (:func:`scope`): a read connection is given a portfolio context ``portfolio_id`` -- an id = that portfolio only, ``None`` = the total
(``Gesamt``: all ACTIVE portfolios consolidated).  It is applied by connection-local TEMP views named ``transactions``, ``positions``,
``v_active_positions`` and ``v_portfolio_market``; SQLite resolves a TEMP object before the main one, so every existing query of that connection reads
the scoped rows without being changed.  Writers never use a scoped connection (the TEMP views are read-only).  A database without the migration has no
``portfolio`` table: :func:`scope` then changes nothing (one implicit portfolio, as before).
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Optional

FEATURE_VERSION_KEY = "portfolio_schema_version"
FEATURE_VERSION = "1"
DEFAULT_PORTFOLIO_ID = 1
DEFAULT_PORTFOLIO_NAME = "Main"
TOTAL_LABEL = "Gesamt"

POSITION_COLUMNS = ("security_id", "shares", "avg_cost", "remaining_cost_basis", "currency", "invested_amount", "realized_gain", "first_transaction_at",
                    "last_transaction_at", "transaction_count", "updated_at")


def aggregate_positions_sql(where: str = "") -> str:
    """The positions of the portfolios matching ``where`` (a condition on ``portfolio_position``) added up per security.  A security held in one portfolio
    only keeps that row's stored values exactly (in particular ``avg_cost``), so a single-portfolio database reads exactly as before."""
    return f"""SELECT
    security_id,
    SUM(shares) AS shares,
    CASE WHEN COUNT(*) = 1 THEN MAX(avg_cost)
         WHEN SUM(shares) > 0 THEN SUM(remaining_cost_basis) / SUM(shares) END AS avg_cost,
    SUM(remaining_cost_basis) AS remaining_cost_basis,
    CASE WHEN COUNT(DISTINCT currency) <= 1 THEN MAX(currency) END AS currency,
    SUM(invested_amount) AS invested_amount,
    SUM(realized_gain) AS realized_gain,
    MIN(first_transaction_at) AS first_transaction_at,
    MAX(last_transaction_at) AS last_transaction_at,
    SUM(transaction_count) AS transaction_count,
    MAX(updated_at) AS updated_at
FROM main.portfolio_position{(' WHERE ' + where) if where else ''}
GROUP BY security_id"""


# The two views over ``positions`` (Initialize-TradingDatabase.py / Migrate-TradingFXRates.py); a scoped connection needs TEMP copies of them, because a
# view of the main schema always reads the main ``positions``.
ACTIVE_POSITIONS_VIEW = """SELECT
    s.id AS security_id, s.symbol, s.isin, s.wkn, s.name, s.exchange, s.currency AS security_currency, p.currency AS cost_basis_currency,
    p.shares, p.avg_cost, p.remaining_cost_basis, p.realized_gain, p.last_transaction_at
FROM positions p
JOIN main.security s ON s.id = p.security_id
WHERE p.shares > 0"""

PORTFOLIO_MARKET_VIEW = """SELECT
    s.id AS security_id, s.symbol, s.name, s.isin, p.shares, p.avg_cost, p.remaining_cost_basis, p.currency AS cost_basis_currency, p.realized_gain,
    ms.price AS market_price_native, ms.currency AS market_price_currency, ms.previous_close, ms.market_cap, ms.as_of_at,
    CASE WHEN p.shares > 0 AND ms.price IS NOT NULL AND upper(p.currency) = upper(ms.currency) THEN p.shares * ms.price ELSE NULL END AS market_value,
    CASE WHEN p.shares > 0 AND ms.price IS NOT NULL AND upper(p.currency) = upper(ms.currency) THEN p.currency ELSE NULL END AS market_value_currency,
    CASE
        WHEN ms.price IS NULL THEN 'MARKET_PRICE_UNAVAILABLE'
        WHEN p.currency IS NULL OR ms.currency IS NULL THEN 'CURRENCY_UNAVAILABLE'
        WHEN upper(p.currency) <> upper(ms.currency) THEN 'CURRENCY_MISMATCH'
        ELSE 'AVAILABLE'
    END AS valuation_status
FROM positions p
JOIN main.security s ON s.id = p.security_id
LEFT JOIN main.market_snapshot ms ON ms.security_id = s.id
WHERE p.shares > 0"""

_SCOPED_VIEWS = ("v_portfolio_market", "v_active_positions", "positions", "transactions")


def available(conn: sqlite3.Connection) -> bool:
    """True when the database has the portfolio schema (``Migrate-TradingPortfolios.py --write``)."""
    return conn.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = 'portfolio_position'").fetchone() is not None


def parse_portfolio_id(value: Any) -> Optional[int]:
    """The portfolio context of a request: an id, or ``None`` for the total (``None``, ``""``, ``"all"``, ``"total"``, ``"gesamt"``, ``0``)."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().lower()
    if text in ("", "all", "total", "gesamt", "none", "null", "0"):
        return None
    number = int(text)
    if number < 0:
        raise ValueError(f"invalid portfolio_id {value!r}")
    return number


def list_portfolios(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All portfolios with their transaction and open-position counts (empty list without the migration)."""
    if not available(conn):
        return []
    rows = conn.execute(
        """SELECT p.id, p.name, p.description, p.active, p.created_at,
                  (SELECT COUNT(*) FROM main.transactions t WHERE t.portfolio_id = p.id) AS transactions,
                  (SELECT COUNT(*) FROM main.portfolio_position pp WHERE pp.portfolio_id = p.id AND pp.shares > 0) AS positions
             FROM main.portfolio p ORDER BY p.id""").fetchall()
    return [{"id": int(r[0]), "name": r[1], "description": r[2], "active": bool(r[3]), "created_at": r[4], "transactions": int(r[5]), "positions": int(r[6]),
             "default": int(r[0]) == DEFAULT_PORTFOLIO_ID} for r in rows]


def get_portfolio(conn: sqlite3.Connection, portfolio_id: int) -> Optional[dict[str, Any]]:
    return next((p for p in list_portfolios(conn) if p["id"] == int(portfolio_id)), None)


def require_portfolio(conn: sqlite3.Connection, portfolio_id: Optional[int], *, active: bool = False) -> Optional[int]:
    """``portfolio_id`` checked against the database (``None`` stays the total).  Raises ``ValueError`` for an unknown (or, with ``active``, deactivated) one."""
    if portfolio_id is None:
        return None
    if not available(conn):
        raise ValueError("the database has no portfolios yet (Migrate-TradingPortfolios.py --write)")
    row = conn.execute("SELECT active FROM main.portfolio WHERE id = ?", (int(portfolio_id),)).fetchone()
    if row is None:
        raise ValueError(f"portfolio {portfolio_id} not found")
    if active and not row[0]:
        raise ValueError(f"portfolio {portfolio_id} is deactivated")
    return int(portfolio_id)


def label(conn: sqlite3.Connection, portfolio_id: Optional[int]) -> str:
    if portfolio_id is None or not available(conn):
        return TOTAL_LABEL
    row = conn.execute("SELECT name FROM main.portfolio WHERE id = ?", (int(portfolio_id),)).fetchone()
    return str(row[0]) if row else f"Depot {portfolio_id}"


def scope(conn: sqlite3.Connection, portfolio_id: Optional[int]) -> Optional[int]:
    """Give a READ connection the portfolio context ``portfolio_id`` (see the module docstring); answers the context applied.

    Can be called again on the same connection to change the context.  ``None`` with every portfolio active needs no TEMP view: the main ``positions``
    view already adds up all portfolios.  A ``query_only`` connection stays ``query_only``."""
    unscope(conn)
    if not available(conn):
        if portfolio_id is not None:
            raise ValueError("the database has no portfolios yet (Migrate-TradingPortfolios.py --write)")
        return None
    portfolio_id = require_portfolio(conn, portfolio_id)
    if portfolio_id is None:
        inactive = conn.execute("SELECT COUNT(*) FROM main.portfolio WHERE active = 0").fetchone()[0]
        if not inactive:
            return None
        where = "portfolio_id IN (SELECT id FROM main.portfolio WHERE active = 1)"
    else:
        where = f"portfolio_id = {int(portfolio_id)}"
    _with_writes(conn,
                 f"CREATE TEMP VIEW transactions AS SELECT * FROM main.transactions WHERE {where}",
                 f"CREATE TEMP VIEW positions AS {aggregate_positions_sql(where)}",
                 f"CREATE TEMP VIEW v_active_positions AS {ACTIVE_POSITIONS_VIEW}",
                 f"CREATE TEMP VIEW v_portfolio_market AS {PORTFOLIO_MARKET_VIEW}")
    return portfolio_id


def current(conn: sqlite3.Connection) -> Optional[int]:
    """The portfolio context :func:`scope` gave this connection: the id of one portfolio, ``None`` for the total (or an unscoped connection)."""
    row = conn.execute("SELECT sql FROM temp.sqlite_master WHERE type = 'view' AND name = 'transactions'").fetchone()
    match = re.search(r"WHERE portfolio_id = (\d+)$", row[0]) if row else None
    return int(match.group(1)) if match else None


def unscope(conn: sqlite3.Connection) -> None:
    """Remove the TEMP views of :func:`scope`: the connection reads the main tables again (all portfolios)."""
    for name in _SCOPED_VIEWS:
        if conn.execute("SELECT 1 FROM temp.sqlite_master WHERE type = 'view' AND name = ?", (name,)).fetchone() is not None:
            _with_writes(conn, f"DROP VIEW temp.{name}")


def _with_writes(conn: sqlite3.Connection, *statements: str) -> None:
    """TEMP schema statements on a connection that may be ``query_only`` (the main database stays read-only: ``mode=ro`` still applies)."""
    query_only = conn.execute("PRAGMA query_only").fetchone()[0]
    if query_only:
        conn.execute("PRAGMA query_only = OFF")
    try:
        for statement in statements:
            conn.execute(statement)
    finally:
        if query_only:
            conn.execute("PRAGMA query_only = ON")


# -- the portfolio management (Settings page): small writes, each in its own transaction ------------------------------------------------------------------
def _clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise ValueError("Name fehlt")
    if len(text) > 60:
        raise ValueError("Name zu lang (höchstens 60 Zeichen)")
    if text.lower() in (TOTAL_LABEL.lower(), "all", "total"):
        raise ValueError(f"{text!r} ist für die Gesamtansicht reserviert")
    return text


def _commit(conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Cursor:
    try:
        cursor = conn.execute(sql, params)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError("Ein Depot mit diesem Namen gibt es schon" if "UNIQUE" in str(exc) else str(exc)) from exc
    conn.commit()
    return cursor


def create_portfolio(conn: sqlite3.Connection, name: Any, description: Any = None) -> dict[str, Any]:
    require_schema(conn)
    cursor = _commit(conn, "INSERT INTO portfolio(name, description) VALUES (?, ?)", (_clean_name(name), (str(description).strip() or None) if description else None))
    return get_portfolio(conn, int(cursor.lastrowid))


def update_portfolio(conn: sqlite3.Connection, portfolio_id: int, *, name: Any = None, description: Any = None, active: Optional[bool] = None) -> dict[str, Any]:
    require_schema(conn)
    require_portfolio(conn, portfolio_id)
    if name is not None:
        _commit(conn, "UPDATE portfolio SET name = ? WHERE id = ?", (_clean_name(name), int(portfolio_id)))
    if description is not None:
        _commit(conn, "UPDATE portfolio SET description = ? WHERE id = ?", (str(description).strip() or None, int(portfolio_id)))
    if active is not None:
        if not active and conn.execute("SELECT COUNT(*) FROM portfolio WHERE active = 1 AND id <> ?", (int(portfolio_id),)).fetchone()[0] == 0:
            raise ValueError("Das letzte aktive Depot kann nicht deaktiviert werden")
        _commit(conn, "UPDATE portfolio SET active = ? WHERE id = ?", (1 if active else 0, int(portfolio_id)))
    return get_portfolio(conn, int(portfolio_id))


def delete_portfolio(conn: sqlite3.Connection, portfolio_id: int) -> None:
    """Only a portfolio without transactions; never the default portfolio."""
    require_schema(conn)
    require_portfolio(conn, portfolio_id)
    if int(portfolio_id) == DEFAULT_PORTFOLIO_ID:
        raise ValueError("Das Standard-Depot kann nicht gelöscht werden (umbenennen oder deaktivieren geht)")
    if conn.execute("SELECT COUNT(*) FROM transactions WHERE portfolio_id = ?", (int(portfolio_id),)).fetchone()[0]:
        raise ValueError("Das Depot hat Transaktionen und kann nicht gelöscht werden (deaktivieren geht)")
    conn.execute("DELETE FROM portfolio_position WHERE portfolio_id = ?", (int(portfolio_id),))
    _commit(conn, "DELETE FROM portfolio WHERE id = ?", (int(portfolio_id),))


def require_schema(conn: sqlite3.Connection) -> None:
    if not available(conn):
        raise ValueError("Die Depot-Tabellen fehlen. Einmalig ausführen: viper-ai run Migrate-TradingPortfolios.py --write")

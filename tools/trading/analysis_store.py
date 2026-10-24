# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Storage of the reasoning analyses (``viper_reasoning``) in the trading database: two additive tables, separate from portfolio and
transaction data.

* ``analysis_case``: one analysis of one security - question, data stand, model, status, the proposal and its validity, the input
  fingerprint (to tell when new prices, news or portfolio changes call for a re-evaluation) and the complete result as JSON.
* ``analysis_step``: one row per finished step of the workflow (observe, assess, quantitative, qualitative, hypothesis, evidence check,
  risk context, decide, review) with its kind (``deterministic`` / ``llm``) and output - the protocol shown in the Intelligence page.

The tables are created only by ``Migrate-TradingAnalysisCases.py --write`` (feature key ``analysis_case_schema_version``); without them the
readers answer ``available: false`` and nothing is written.  This module writes nothing else.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

FEATURE_VERSION_KEY = "analysis_case_schema_version"
FEATURE_VERSION = "1"
STATUSES = ("RUNNING", "DONE", "INSUFFICIENT_DATA", "FAILED")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analysis_case (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id         INTEGER NOT NULL,
    question            TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('RUNNING', 'DONE', 'INSUFFICIENT_DATA', 'FAILED')),
    trigger             TEXT NOT NULL DEFAULT 'manual',
    thread_id           TEXT NOT NULL UNIQUE,
    supersedes_id       INTEGER,
    as_of               TEXT NOT NULL,
    data_as_of          TEXT,
    model               TEXT,
    action              TEXT,
    engine_action       TEXT,
    confidence          TEXT,
    valid_until         TEXT,
    fingerprint_json    TEXT,
    result_json         TEXT,
    error               TEXT,
    created_at          TEXT NOT NULL,
    finished_at         TEXT,
    FOREIGN KEY (security_id) REFERENCES security(id) ON DELETE CASCADE,
    FOREIGN KEY (supersedes_id) REFERENCES analysis_case(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_case_security ON analysis_case(security_id, created_at);
CREATE TABLE IF NOT EXISTS analysis_step (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id             INTEGER NOT NULL,
    node                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('deterministic', 'llm')),
    started_at          TEXT NOT NULL,
    finished_at         TEXT NOT NULL,
    output_json         TEXT,
    error               TEXT,
    FOREIGN KEY (case_id) REFERENCES analysis_case(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_analysis_step_case ON analysis_step(case_id, id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _load(text: Optional[str]) -> Any:
    return None if not text else json.loads(text)


def available(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'analysis_case'").fetchone() is not None


def create_case(conn: sqlite3.Connection, *, security_id: int, question: str, thread_id: str, as_of: str, model: str, trigger: str = "manual",
                supersedes_id: Optional[int] = None) -> int:
    cur = conn.execute("INSERT INTO analysis_case(security_id, question, status, trigger, thread_id, supersedes_id, as_of, model, created_at) "
                       "VALUES (?, ?, 'RUNNING', ?, ?, ?, ?, ?, ?)", (security_id, question, trigger, thread_id, supersedes_id, as_of, model, now()))
    conn.commit()
    return int(cur.lastrowid)


def add_step(conn: sqlite3.Connection, case_id: int, node: str, kind: str, started_at: str, output: Any, error: Optional[str] = None) -> None:
    conn.execute("INSERT INTO analysis_step(case_id, node, kind, started_at, finished_at, output_json, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (case_id, node, kind, started_at, now(), _dump(output), error))
    conn.commit()


def finish_case(conn: sqlite3.Connection, case_id: int, *, status: str, result: Any, data_as_of: Optional[str] = None, action: Optional[str] = None,
                engine_action: Optional[str] = None, confidence: Optional[str] = None, valid_until: Optional[str] = None, fingerprint: Any = None,
                error: Optional[str] = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status}")
    conn.execute("UPDATE analysis_case SET status = ?, result_json = ?, data_as_of = ?, action = ?, engine_action = ?, confidence = ?, valid_until = ?, "
                 "fingerprint_json = ?, error = ?, finished_at = ? WHERE id = ?",
                 (status, _dump(result), data_as_of, action, engine_action, confidence, valid_until, _dump(fingerprint), error, now(), case_id))
    conn.commit()


def fail_case(conn: sqlite3.Connection, case_id: int, error: str) -> None:
    conn.execute("UPDATE analysis_case SET status = 'FAILED', error = ?, finished_at = ? WHERE id = ?", (error[:2000], now(), case_id))
    conn.commit()


def reopen_case(conn: sqlite3.Connection, case_id: int) -> None:
    """A FAILED or interrupted case is resumed from its last checkpoint: back to RUNNING."""
    conn.execute("UPDATE analysis_case SET status = 'RUNNING', error = NULL, finished_at = NULL WHERE id = ?", (case_id,))
    conn.commit()


def _case(row: sqlite3.Row, with_result: bool) -> dict[str, Any]:
    out = {k: row[k] for k in row.keys() if k not in ("result_json", "fingerprint_json")}
    out["fingerprint"] = _load(row["fingerprint_json"])
    if with_result:
        out["result"] = _load(row["result_json"])
    return out


def get_case(conn: sqlite3.Connection, case_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT c.*, s.symbol, s.name FROM analysis_case c LEFT JOIN security s ON s.id = c.security_id WHERE c.id = ?", (case_id,)).fetchone()
    if row is None:
        return None
    case = _case(row, True)
    case["steps"] = [{**{k: r[k] for k in r.keys() if k != "output_json"}, "output": _load(r["output_json"])}
                     for r in conn.execute("SELECT * FROM analysis_step WHERE case_id = ? ORDER BY id", (case_id,))]
    return case


def list_cases(conn: sqlite3.Connection, security_id: Optional[int] = None, limit: int = 200) -> list[dict[str, Any]]:
    sql = "SELECT c.*, s.symbol, s.name FROM analysis_case c LEFT JOIN security s ON s.id = c.security_id"
    params: list[Any] = []
    if security_id is not None:
        sql += " WHERE c.security_id = ?"
        params.append(int(security_id))
    sql += " ORDER BY c.id DESC LIMIT ?"
    params.append(int(limit))
    return [_case(r, False) for r in conn.execute(sql, params)]


def latest_per_security(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The current thesis of every analysed security: its newest finished case (DONE or INSUFFICIENT_DATA)."""
    rows = conn.execute("SELECT c.*, s.symbol, s.name FROM analysis_case c LEFT JOIN security s ON s.id = c.security_id WHERE c.id IN ("
                        "SELECT MAX(id) FROM analysis_case WHERE status IN ('DONE', 'INSUFFICIENT_DATA') GROUP BY security_id) ORDER BY s.symbol")
    return [_case(r, True) for r in rows]

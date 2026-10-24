# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Job definitions and run results of VIPER's integrated task scheduler (M3a), in the trading database.

* ``scheduled_job``: one row per job - the script and its arguments, the log file, the schedule (cron times and "at VIPER start"), whether missed
  runs are caught up, the time limit, whether it is enabled, and the Windows task it replaces.
* ``job_run``: one row per run - trigger, start, end, exit code, status and the end of the log on an error.

The seven jobs are the former Windows scheduled tasks with the same scripts, arguments, log files and times (``SEED``); they are created
**disabled** so they never run twice next to the Windows tasks: a job is enabled by the takeover (after a successful run in VIPER), which
disables - never deletes - its Windows task.  The scheduler itself is APScheduler in the web process (``web/scheduler.py``).  Tables only from
``Migrate-TradingScheduler.py --write`` (feature key ``scheduler_schema_version``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

FEATURE_VERSION_KEY = "scheduler_schema_version"
FEATURE_VERSION = "1"
RUN_STATUSES = ("RUNNING", "OK", "FAILED", "TIMEOUT", "INTERRUPTED", "SKIPPED")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_job (
    name                TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    script              TEXT NOT NULL,
    args_json           TEXT NOT NULL DEFAULT '[]',
    log_file            TEXT NOT NULL,
    schedule_json       TEXT NOT NULL,
    run_at_start        INTEGER NOT NULL DEFAULT 0 CHECK (run_at_start IN (0, 1)),
    catch_up            INTEGER NOT NULL DEFAULT 1 CHECK (catch_up IN (0, 1)),
    time_limit_s        INTEGER NOT NULL CHECK (time_limit_s > 0),
    working_dir         TEXT,
    enabled             INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    windows_task        TEXT,
    taken_over_at       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_run (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name            TEXT NOT NULL,
    trigger             TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('RUNNING', 'OK', 'FAILED', 'TIMEOUT', 'INTERRUPTED', 'SKIPPED')),
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    exit_code           INTEGER,
    pid                 INTEGER,
    owner_pid           INTEGER,
    error               TEXT,
    FOREIGN KEY (job_name) REFERENCES scheduled_job(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_job_run_job ON job_run(job_name, id);
"""

ROOT_DIR = "<root>"  # stands for the runtime root in working_dir


def _daily(hour: int, minute: int) -> dict[str, Any]:
    return {"day_of_week": "*", "hour": hour, "minute": minute}


def _sunday(hour: int, minute: int) -> dict[str, Any]:
    return {"day_of_week": "sun", "hour": hour, "minute": minute}


# The Windows tasks of 24.10.2026 (Get-ScheduledTask): script, arguments, log, triggers ("at logon" = run_at_start), StartWhenAvailable (= catch_up),
# ExecutionTimeLimit, working directory; IgnoreNew (= never two runs of one job) holds for all of them.
SEED: list[dict[str, Any]] = [
    {"name": "Trading-MarketData-Backfill", "title": "Marktdaten", "script": "Backfill-TradingMarketData.py", "args": ["--write"], "log_file": "market-data-backfill.log",
     "schedule": [_daily(17, 15)], "run_at_start": True, "time_limit_s": 72 * 3600, "working_dir": None},
    {"name": "Trading-FXRates-Backfill", "title": "FX-Kurse", "script": "Backfill-TradingFXRatesECB.py", "args": ["--write", "--quote-currency", "USD,GBP,AUD,KRW"],
     "log_file": "fx-rates-backfill.log", "schedule": [_daily(17, 20)], "run_at_start": True, "time_limit_s": 72 * 3600, "working_dir": None},
    {"name": "Trading-EventsNews-Backfill", "title": "Events / News", "script": "Backfill-TradingEventsNews.py", "args": ["--write"], "log_file": "events-news-backfill.log",
     "schedule": [_daily(17, 25)], "run_at_start": True, "time_limit_s": 72 * 3600, "working_dir": None},
    {"name": "Trading-Fundamentals-SEC", "title": "Fundamentals (SEC)", "script": "Backfill-TradingFundamentalsSEC.py", "args": [], "log_file": "fundamentals-sec-backfill.log",
     "schedule": [_sunday(10, 0)], "run_at_start": True, "time_limit_s": 72 * 3600, "working_dir": None},
    {"name": "Trading-Candidate-Discovery", "title": "Candidate Discovery", "script": "Discover-TradingCandidates.py", "args": [], "log_file": "candidate-discovery.log",
     "schedule": [_daily(17, 35)], "run_at_start": False, "time_limit_s": 2 * 3600, "working_dir": ROOT_DIR},
    {"name": "Trading-Market-Intelligence", "title": "Market Intelligence", "script": "Collect-TradingMarketIntelligence.py", "args": [], "log_file": "market-intelligence.log",
     "schedule": [_daily(8, 15), _daily(17, 45)], "run_at_start": False, "time_limit_s": 2 * 3600, "working_dir": ROOT_DIR},
    {"name": "Trading-ETF-Refresh", "title": "ETF-Daten", "script": "Refresh-TradingEtfData.py", "args": ["--write"], "log_file": "etf-refresh.log",
     "schedule": [_sunday(11, 0)], "run_at_start": False, "time_limit_s": 3600, "working_dir": ROOT_DIR},
]

FIELDS_DOW = ("mon", "tue", "wed", "thu", "fri", "sat", "sun", "*")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def available(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scheduled_job'").fetchone() is not None


def seed(conn: sqlite3.Connection) -> int:
    """Insert the jobs that do not exist yet (disabled); an existing job keeps its settings.  Returns the number inserted."""
    added = 0
    for job in SEED:
        cur = conn.execute(
            "INSERT OR IGNORE INTO scheduled_job(name, title, script, args_json, log_file, schedule_json, run_at_start, catch_up, time_limit_s, working_dir, enabled, "
            "windows_task, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?, ?)",
            (job["name"], job["title"], job["script"], json.dumps(job["args"]), job["log_file"], json.dumps(job["schedule"]), int(job["run_at_start"]),
             job["time_limit_s"], job["working_dir"], job["name"], now(), now()))
        added += cur.rowcount
    return added


def validate_schedule(schedule: Any) -> list[dict[str, Any]]:
    """A schedule is a non-empty list of cron times ``{"day_of_week": "*"|"mon".."sun"|"mon,wed", "hour": 0-23, "minute": 0-59}``."""
    if not isinstance(schedule, list) or not schedule or len(schedule) > 12:
        raise ValueError("Zeitplan: mindestens eine und höchstens zwölf Zeiten")
    out = []
    for item in schedule:
        if not isinstance(item, dict):
            raise ValueError("Zeitplan: jede Zeit ist ein Objekt")
        dow = str(item.get("day_of_week", "*")).strip().lower() or "*"
        if any(part not in FIELDS_DOW for part in dow.split(",")):
            raise ValueError(f"Zeitplan: ungültiger Wochentag {dow!r}")
        hour, minute = item.get("hour"), item.get("minute")
        if not (isinstance(hour, int) and 0 <= hour <= 23 and isinstance(minute, int) and 0 <= minute <= 59):
            raise ValueError("Zeitplan: Stunde 0-23 und Minute 0-59")
        out.append({"day_of_week": dow, "hour": hour, "minute": minute})
    return out


def _job(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in row.keys() if k not in ("args_json", "schedule_json")}
    out["args"], out["schedule"] = json.loads(row["args_json"]), json.loads(row["schedule_json"])
    out["enabled"], out["run_at_start"], out["catch_up"] = bool(row["enabled"]), bool(row["run_at_start"]), bool(row["catch_up"])
    return out


def jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_job(r) for r in conn.execute("SELECT * FROM scheduled_job ORDER BY name")]


def job(conn: sqlite3.Connection, name: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM scheduled_job WHERE name = ?", (name,)).fetchone()
    return _job(row) if row else None


def update_job(conn: sqlite3.Connection, name: str, *, enabled: Optional[bool] = None, schedule: Any = None, taken_over: bool = False) -> None:
    sets, params = ["updated_at = ?"], [now()]
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(int(bool(enabled)))
    if schedule is not None:
        sets.append("schedule_json = ?")
        params.append(json.dumps(validate_schedule(schedule)))
    if taken_over:
        sets.append("taken_over_at = ?")
        params.append(now())
    conn.execute(f"UPDATE scheduled_job SET {', '.join(sets)} WHERE name = ?", (*params, name))
    conn.commit()


def start_run(conn: sqlite3.Connection, name: str, trigger: str, owner_pid: int) -> Optional[int]:
    """The id of a new RUNNING row, or ``None`` when the job already runs (never two runs of one job)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM job_run WHERE job_name = ? AND status = 'RUNNING'", (name,)).fetchone():
            conn.execute("ROLLBACK")
            return None
        cur = conn.execute("INSERT INTO job_run(job_name, trigger, status, started_at, owner_pid) VALUES (?, ?, 'RUNNING', ?, ?)", (name, trigger, now(), owner_pid))
        conn.execute("COMMIT")
        return int(cur.lastrowid)
    except Exception:
        conn.execute("ROLLBACK")
        raise


def set_pid(conn: sqlite3.Connection, run_id: int, pid: int) -> None:
    conn.execute("UPDATE job_run SET pid = ? WHERE id = ?", (pid, run_id))
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, exit_code: Optional[int], error: Optional[str] = None) -> None:
    if status not in RUN_STATUSES:
        raise ValueError(status)
    conn.execute("UPDATE job_run SET status = ?, exit_code = ?, error = ?, finished_at = ? WHERE id = ?", (status, exit_code, error, now(), run_id))
    conn.commit()


def runs(conn: sqlite3.Connection, name: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM job_run", []
    if name:
        sql += " WHERE job_name = ?"
        params.append(name)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))]


def last_runs(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Per job its newest run and its newest successful run."""
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM job_run WHERE id IN (SELECT MAX(id) FROM job_run GROUP BY job_name)"):
        out.setdefault(row["job_name"], {})["last"] = dict(row)
    for row in conn.execute("SELECT * FROM job_run WHERE id IN (SELECT MAX(id) FROM job_run WHERE status = 'OK' GROUP BY job_name)"):
        out.setdefault(row["job_name"], {})["last_ok"] = dict(row)
    return out


def running_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM job_run WHERE status = 'RUNNING'")]

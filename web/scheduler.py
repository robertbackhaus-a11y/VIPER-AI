# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""VIPER's integrated task scheduler (M3a): APScheduler in the web process runs the jobs of ``job_store`` (the former Windows scheduled tasks).

* Scheduling is APScheduler's (``BackgroundScheduler``, cron triggers, ``max_instances=1``, ``coalesce``); there is no scheduling code of our own.
  The definitions, the enabled state, the schedules and every run result live in the trading database (``job_store``), so after a restart the
  enabled jobs are scheduled again from there.
* A run is exactly the run of the Windows task: ``pythonw.exe Run-TradingTask.py <log> <script> [args]`` of this installation, no console
  window, the same log file, the exit code of the script.  A job never runs twice at the same time (a ``RUNNING`` row in the database blocks the
  second start, also across two processes); the time limit ends a run that takes too long.
* "Catch up" (``StartWhenAvailable``): a scheduled time that passed while VIPER was not running is made up once at the start; "at VIPER start"
  replaces the Windows trigger "at logon".
* Takeover: a job is enabled only after a successful run in VIPER, and then its Windows task is disabled (``Disable-ScheduledTask``) - never deleted.
"""

from __future__ import annotations

import ntpath
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request

from .deps import task_model, task_service

CREATE_NO_WINDOW = 0x08000000
TIMEZONE = "Europe/Berlin"  # schedules are wall-clock times in Berlin, with summer and winter time; UTC is only the internal base of APScheduler
START_DELAY_S = 30  # jobs "at VIPER start" and catch-up runs start a little after the server, not during its start
LOG_TAIL_BYTES = 4000


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


class JobRunner:
    def __init__(self, state) -> None:
        self.state = state
        self.scheduler = None
        self._threads: dict[str, threading.Thread] = {}

    # -- environment ----------------------------------------------------------------------------------------------------------------
    @property
    def db_path(self) -> str:
        return self.state.engine.db_path

    def store(self):
        return self.state.engine.module("job_store")

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def available(self) -> bool:
        conn = self.connect()
        try:
            return self.store().available(conn)
        finally:
            conn.close()

    def app_dir(self) -> Path:
        return Path(self.state.engine.tools._runtime_tools_directory())

    def command(self, job: dict) -> tuple[list[str], Optional[str], str]:
        """``(command, working directory, log path)`` of one run - the action of the former Windows task."""
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        python = str(pythonw if pythonw.is_file() else exe)
        app = self.app_dir()
        log = ntpath.join(self.state.settings.trading_logs_dir, job["log_file"])
        root = self.state.engine.module("runtime_paths").trading_root()
        cwd = str(root) if job.get("working_dir") == self.store().ROOT_DIR else None
        return [python, str(app / "Run-TradingTask.py"), log, str(app / job["script"]), *job["args"]], cwd, log

    # -- running ---------------------------------------------------------------------------------------------------------------------
    def execute(self, name: str, trigger: str) -> Optional[int]:
        """Run one job now in this thread; ``None`` when it already runs or does not exist."""
        store = self.store()
        conn = self.connect()
        try:
            job = store.job(conn, name)
            if job is None:
                return None
            run_id = store.start_run(conn, name, trigger, os.getpid())
            if run_id is None:
                return None
            try:
                command, cwd, log = self.command(job)
                Path(log).parent.mkdir(parents=True, exist_ok=True)
                process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
                store.set_pid(conn, run_id, process.pid)
                try:
                    code = process.wait(timeout=job["time_limit_s"])
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    store.finish_run(conn, run_id, "TIMEOUT", None, f"Zeitlimit von {job['time_limit_s']} s überschritten; der Lauf wurde beendet")
                    return run_id
                error = None if code == 0 else self._log_tail(log)
                store.finish_run(conn, run_id, "OK" if code == 0 else "FAILED", code, error)
            except Exception as exc:  # noqa: BLE001 - the run is recorded as failed, the scheduler keeps going
                store.finish_run(conn, run_id, "FAILED", None, f"{type(exc).__name__}: {exc}"[:2000])
            return run_id
        finally:
            conn.close()

    @staticmethod
    def _log_tail(log: str) -> str:
        try:
            with open(log, "rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - LOG_TAIL_BYTES))
                return handle.read().decode("utf-8", errors="replace")
        except OSError as exc:
            return f"Log nicht lesbar: {exc}"

    def run_in_background(self, name: str, trigger: str) -> bool:
        if self.is_running(name):
            return False
        thread = threading.Thread(target=self.execute, args=(name, trigger), name=f"viper-job-{name}", daemon=True)
        self._threads[name] = thread
        thread.start()
        return True

    def is_running(self, name: str) -> bool:
        thread = self._threads.get(name)
        if thread is not None and thread.is_alive():
            return True
        conn = self.connect()
        try:
            return any(r["job_name"] == name for r in self.store().running_rows(conn))
        finally:
            conn.close()

    # -- scheduling ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def trigger(job: dict):
        from apscheduler.triggers.combining import OrTrigger
        from apscheduler.triggers.cron import CronTrigger

        crons = [CronTrigger(day_of_week=t["day_of_week"], hour=t["hour"], minute=t["minute"], timezone=TIMEZONE) for t in job["schedule"]]
        return crons[0] if len(crons) == 1 else OrTrigger(crons)

    def plan(self, job: dict) -> None:
        if self.scheduler is None:
            return
        if self.scheduler.get_job(job["name"]):
            self.scheduler.remove_job(job["name"])
        if job["enabled"]:
            self.scheduler.add_job(self.execute, self.trigger(job), args=[job["name"], "schedule"], id=job["name"], name=job["title"], max_instances=1,
                                   coalesce=True, misfire_grace_time=3600, replace_existing=True)

    def next_run(self, name: str) -> Optional[str]:
        job = self.scheduler.get_job(name) if self.scheduler else None
        return job.next_run_time.isoformat() if job and job.next_run_time else None

    def missed(self, job: dict, last_start: Optional[str], now: Optional[datetime] = None) -> bool:
        """Whether a scheduled time passed since the last run (or since the takeover) - the "StartWhenAvailable" catch-up."""
        reference = last_start or job.get("taken_over_at") or job.get("updated_at")
        if not reference:
            return False
        now = now or datetime.now(ZoneInfo(TIMEZONE))
        since = datetime.fromisoformat(reference)
        if since.tzinfo is None:
            since = since.replace(tzinfo=ZoneInfo(TIMEZONE))
        due = self.trigger(job).get_next_fire_time(None, since + timedelta(seconds=1))
        return due is not None and due <= now

    def start(self) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        if not self.available():
            return
        store = self.store()
        conn = self.connect()
        try:
            for row in store.running_rows(conn):  # a run of a previous VIPER process that ended with it
                if row["owner_pid"] != os.getpid() and not _alive(row["pid"]):
                    store.finish_run(conn, row["id"], "INTERRUPTED", None, "VIPER wurde während des Laufs beendet")
            jobs = [j for j in store.jobs(conn) if j["enabled"]]
            last = store.last_runs(conn)
        finally:
            conn.close()
        self.scheduler = BackgroundScheduler(timezone=TIMEZONE, job_defaults={"max_instances": 1, "coalesce": True})
        self.scheduler.start()
        at = datetime.now(ZoneInfo(TIMEZONE)) + timedelta(seconds=START_DELAY_S)
        for job in jobs:
            self.plan(job)
            started = ((last.get(job["name"]) or {}).get("last") or {}).get("started_at")
            reason = "start" if job["run_at_start"] else "catch_up" if job["catch_up"] and self.missed(job, started) else None
            if reason:
                self.scheduler.add_job(self.execute, "date", run_date=at, args=[job["name"], reason], id=f"{job['name']}#{reason}", max_instances=1)

    def stop(self) -> None:
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None


def disable_windows_task(name: str) -> tuple[bool, str]:
    """``Disable-ScheduledTask`` for one of the seven known tasks (never ``Unregister``)."""
    if name not in task_model.SPECS:
        return False, f"unbekannter Windows-Task: {name}"
    done = subprocess.run(task_service.powershell_args(f"Disable-ScheduledTask -TaskName '{name}' -ErrorAction Stop | Out-Null"), capture_output=True, text=True,
                          timeout=60, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    if done.returncode != 0:
        return False, (done.stderr.strip().splitlines() or ["unbekannter Fehler"])[0][:300]
    return True, f"Windows-Task {name} deaktiviert (nicht gelöscht)"


def register(app: FastAPI, state) -> None:
    runner: JobRunner = state.jobs

    def unavailable() -> dict:
        return {"available": False, "message": "Die Tabellen der Aufgabenverwaltung fehlen. Einmalig ausführen: viper-ai run Migrate-TradingScheduler.py --write"}

    @app.get("/api/scheduler")
    async def overview():
        import asyncio

        def load():
            if not runner.available():
                return unavailable()
            conn = runner.connect()
            try:
                store = runner.store()
                jobs, last = store.jobs(conn), store.last_runs(conn)
                running = {r["job_name"] for r in store.running_rows(conn)}
            finally:
                conn.close()
            return jobs, last, running

        loaded = await asyncio.to_thread(load)
        if isinstance(loaded, dict):
            return loaded
        jobs, last, running = loaded
        try:
            windows = {t.name: t for t in await asyncio.to_thread(task_service.TaskBackend(state.settings.trading_logs_dir).query)}
        except Exception:  # noqa: BLE001 - the Windows view is informational
            windows = {}
        for job in jobs:
            job.update(last.get(job["name"], {}))
            job["running"] = job["name"] in running
            job["next_run"] = runner.next_run(job["name"])
            w = windows.get(job.get("windows_task") or "")
            job["windows"] = None if w is None else {"found": w.found, "state": getattr(w, "state", None), "disabled": getattr(w, "disabled", None)}
            job["command"] = " ".join(runner.command(job)[0][1:])
        return {"available": True, "scheduler_running": runner.scheduler is not None, "jobs": jobs}

    @app.post("/api/scheduler/jobs/{name}")
    async def update(name: str, request: Request):
        body = await request.json()
        conn = runner.connect()
        try:
            store = runner.store()
            if store.job(conn, name) is None:
                raise HTTPException(404, f"unbekannter Job: {name}")
            try:
                store.update_job(conn, name, enabled=body.get("enabled"), schedule=body.get("schedule"))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            job = store.job(conn, name)
        finally:
            conn.close()
        runner.plan(job)
        return {"ok": True, "job": job, "next_run": runner.next_run(name)}

    @app.post("/api/scheduler/jobs/{name}/run")
    async def run(name: str):
        conn = runner.connect()
        try:
            if runner.store().job(conn, name) is None:
                raise HTTPException(404, f"unbekannter Job: {name}")
        finally:
            conn.close()
        started = runner.run_in_background(name, "manual")
        return {"ok": started, "message": "gestartet" if started else "Der Job läuft bereits – kein zweiter Start."}

    @app.get("/api/scheduler/jobs/{name}/runs")
    async def runs(name: str):
        conn = runner.connect()
        try:
            return runner.store().runs(conn, name)
        finally:
            conn.close()

    @app.post("/api/scheduler/jobs/{name}/takeover")
    async def takeover(name: str):
        """Enable the job in VIPER and disable its Windows task - only after a successful run in VIPER."""
        import asyncio

        conn = runner.connect()
        try:
            store = runner.store()
            job = store.job(conn, name)
            if job is None:
                raise HTTPException(404, f"unbekannter Job: {name}")
            ok_run = (store.last_runs(conn).get(name) or {}).get("last_ok")
            if ok_run is None:
                return {"ok": False, "message": "Noch kein erfolgreicher Lauf in VIPER: zuerst „Jetzt ausführen“, dann übernehmen."}
            store.update_job(conn, name, enabled=True, taken_over=True)
            job = store.job(conn, name)
        finally:
            conn.close()
        runner.plan(job)
        disabled, message = (True, "kein Windows-Task hinterlegt") if not job.get("windows_task") else await asyncio.to_thread(disable_windows_task, job["windows_task"])
        return {"ok": disabled, "message": f"In VIPER aktiviert. {message}", "next_run": runner.next_run(name)}

# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""What a scheduler backend reports about a deployed task, independent of the operating system, and the log of a task (read-only).

A backend answers with one :class:`TaskInfo` per definition: whether the task is deployed, its state, last and next run, the result of the last run
(the exit code of the script, or a Windows Task Scheduler code), the triggers and settings as the scheduler has them.  The log file next to it adds
when it was last written and the last error-looking line of its end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

DAY_NAMES = ((1, "So"), (2, "Mo"), (4, "Di"), (8, "Mi"), (16, "Do"), (32, "Fr"), (64, "Sa"))  # the DaysOfWeek bit mask of the Windows Task Scheduler
DAY_BITS = {"sunday": 1, "monday": 2, "tuesday": 4, "wednesday": 8, "thursday": 16, "friday": 32, "saturday": 64}
RESULT_TEXT = {0: "OK", 267009: "läuft", 267010: "nicht geplant", 267011: "noch nie gelaufen", 267014: "beendet (Zeitlimit/Abbruch)", 1: "Fehler (Exitcode 1)"}


@dataclass(frozen=True)
class TriggerInfo:
    """One trigger of a task: ``kind`` is ``Daily``, ``Weekly``, ``Logon`` or ``Other``; ``time`` is the local ``HH:MM`` of the start."""

    kind: str
    time: Optional[str] = None
    days_interval: int = 1
    weeks_interval: int = 1
    days_of_week: int = 0
    enabled: bool = True


@dataclass
class TaskInfo:
    name: str
    found: bool = False
    state: str = ""  # ready, running, disabled, ... (lower case does not matter)
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    last_result: Optional[int] = None
    log_file: str = ""
    triggers: list[TriggerInfo] = field(default_factory=list)
    start_when_available: Optional[bool] = None
    multiple_instances: str = ""  # IgnoreNew, Parallel, Queue, StopExisting
    time_limit: str = ""  # ISO 8601 duration, e.g. PT2H
    missed_runs: Optional[int] = None
    log_modified: Optional[datetime] = None  # when the log file was last written (about the end of the last run)
    log_size: Optional[int] = None
    log_hint: str = ""  # the last error-looking line at the end of the log
    action_execute: str = ""  # the program the task starts (Windows: pythonw.exe = no window)
    drift: list[str] = field(default_factory=list)  # where the deployed task differs from the VIPER definition (the next deploy corrects it)

    @property
    def silent(self) -> Optional[bool]:
        """Windows only: True if the task starts a program without a console (pythonw), False for a console program, None if not known or not relevant."""
        if not self.action_execute:
            return None
        return self.action_execute.replace("\\", "/").rsplit("/", 1)[-1].lower() == "pythonw.exe"

    @property
    def running(self) -> bool:
        return self.state.lower() == "running"

    @property
    def disabled(self) -> bool:
        return self.state.lower() == "disabled"


@dataclass
class LogTail:
    available: bool
    text: str = ""
    truncated: bool = False
    size: int = 0
    modified: Optional[datetime] = None
    note: str = ""


def _days_text(mask: int) -> str:
    names = [name for bit, name in DAY_NAMES if mask & bit]
    return ", ".join(names) if names else "?"


def trigger_line(trigger: TriggerInfo) -> str:
    """One trigger in words, for the details: ``täglich 17:15``, ``alle 2 Tage 10:00``, ``wöchentlich So 10:00``, ``bei Anmeldung``."""
    suffix = "" if trigger.enabled else " (deaktiviert)"
    at = f" {trigger.time}" if trigger.time else ""
    if trigger.kind == "Daily":
        return (("täglich" if trigger.days_interval <= 1 else f"alle {trigger.days_interval} Tage") + at) + suffix
    if trigger.kind == "Weekly":
        return (("wöchentlich" if trigger.weeks_interval <= 1 else f"alle {trigger.weeks_interval} Wochen") + f" {_days_text(trigger.days_of_week)}" + at) + suffix
    if trigger.kind == "Logon":
        return "bei Anmeldung" + suffix
    return "anderer Auslöser" + suffix


def rhythm_text(triggers: list[TriggerInfo]) -> str:
    """The rhythm of a task from its (enabled) triggers: ``täglich 17:15 + bei Anmeldung``, ``2× täglich 08:15, 17:45``, ``wöchentlich So 10:00 + bei Anmeldung``."""
    active = [t for t in triggers if t.enabled]
    if not active:
        return "kein Auslöser" if not triggers else "Auslöser deaktiviert"
    parts: list[str] = []
    daily = sorted((t for t in active if t.kind == "Daily"), key=lambda t: t.time or "")
    if len(daily) == 1:
        parts.append(trigger_line(daily[0]))
    elif len(daily) > 1:
        parts.append(f"{len(daily)}× täglich " + ", ".join(t.time or "?" for t in daily))
    parts += [trigger_line(t) for t in active if t.kind == "Weekly"]
    if any(t.kind == "Logon" for t in active):
        parts.append("bei Anmeldung")
    parts += [trigger_line(t) for t in active if t.kind == "Other"]
    return " + ".join(parts)


def definition_triggers(task) -> list[TriggerInfo]:
    """The triggers a definition asks for (a backend whose scheduler has no comparable trigger list reports these)."""
    kind = "Daily" if task.day == "daily" else "Weekly"
    out = [TriggerInfo(kind, t, days_of_week=DAY_BITS.get(task.day, 0)) for t in task.times]
    return out + ([TriggerInfo("Logon")] if task.at_login else [])


def result_text(code: Optional[int]) -> str:
    if code is None:
        return "—"
    if code in RESULT_TEXT:
        return RESULT_TEXT[code]
    if 0 < code < 256:
        return f"Fehler (Exitcode {code})"  # the exit code of the script (the launcher hands it on to the scheduler)
    return f"Fehler (0x{code & 0xFFFFFFFF:08X})"


_ERROR_LINE = re.compile(r"traceback|\berror\b|\bfailed\b|\bfehler\b|getaddrinfo|forbidden|timed? ?out", re.I)
_BENIGN_LINE = re.compile(r"failed[^:=\n]{0,24}[:=]\s*0\b|failed:\s*\[\s*\]|\"failed\":\s*0|errors\"?:\s*(\{\s*\}|0\b)|no errors|keine fehler", re.I)
HINT_LINES, HINT_BYTES = 80, 40_000


def log_hint(tail_text: str) -> str:
    """The last error-looking line of the end of a log (a line that says ``0 failed`` or ``errors: {}`` does not count), at most 200 characters; empty if none."""
    for line in reversed(tail_text.splitlines()[-HINT_LINES:]):
        if _ERROR_LINE.search(line) and not _BENIGN_LINE.search(line):
            text = line.strip()
            return text if len(text) <= 200 else text[:197] + "…"
    return ""


def tail_log(path: Optional[Path], max_lines: int = 300, max_bytes: int = 200_000) -> LogTail:
    """Last lines of a log file (reads at most ``max_bytes`` from the end; never modifies the file)."""
    if path is None:
        return LogTail(False, note="Kein gültiger Log-Pfad")
    try:
        stat = path.stat()
        with open(path, "rb") as handle:
            start = max(0, stat.st_size - max_bytes)
            handle.seek(start)
            data = handle.read()
    except FileNotFoundError:
        return LogTail(False, note=f"Log-Datei nicht vorhanden: {path.name}")
    except OSError as exc:
        return LogTail(False, note=f"Log nicht lesbar ({type(exc).__name__})")
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    cut = start > 0
    if cut and lines:
        lines = lines[1:]  # the first line is probably cut in the middle
    if len(lines) > max_lines:
        lines, cut = lines[-max_lines:], True
    return LogTail(True, "\n".join(lines), cut, stat.st_size, datetime.fromtimestamp(stat.st_mtime))


def add_log_facts(task: TaskInfo, path: Path) -> TaskInfo:
    """Write time and size of the task's log and the last error-looking line at its end (read-only; a missing log leaves the fields empty)."""
    try:
        stat = path.stat()
    except OSError:
        return task
    task.log_modified, task.log_size = datetime.fromtimestamp(stat.st_mtime), stat.st_size
    tail = tail_log(path, max_lines=HINT_LINES, max_bytes=HINT_BYTES)
    task.log_hint = log_hint(tail.text) if tail.available else ""
    return task

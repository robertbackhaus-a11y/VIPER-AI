# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The seven scheduled tasks of VIPER-AI: the one place that defines them.

Each task is the run of one script of the app folder through the launcher ``Run-TradingTask.py <log> <script> [args]`` (it appends stdout and stderr to
the log under ``<root>/logs`` and hands on the exit code).  The built-in definitions below hold name, script, arguments, schedule (local wall-clock
times, daily or on one weekday), the time limit and whether the task also runs at login.  What is changed in VIPER (``viper-ai tasks edit`` / ``delete``,
the Tasks page) is stored in ``<root>/scheduler/tasks.json`` as a change of the built-in definition; together they are the definition of the
installation.  The operating system scheduler (Windows Task Scheduler, systemd user timers) is only where the definition is deployed to: a change made
there is overwritten by the next ``viper-ai tasks deploy``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterable, Optional

DAYS = ("daily", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PREFIX = "Trading-"  # the names of the scheduled tasks (Windows) since 2026; systemd units are named viper-<key>


@dataclass(frozen=True)
class TaskDefinition:
    key: str  # MarketData-Backfill, the name without prefix
    title: str
    script: str  # file name in the app folder
    args: tuple[str, ...]
    log: str  # file name in <root>/logs
    times: tuple[str, ...]  # local wall-clock times HH:MM
    day: str = "daily"  # daily or one weekday (monday .. sunday)
    enabled: bool = True
    time_limit_h: int = 72
    at_login: bool = False  # also run when the user logs in (missed runs are caught up in any case)
    root_workdir: bool = False  # run in the runtime root (the scripts that write reports relative to it)

    @property
    def name(self) -> str:
        return PREFIX + self.key


BUILTIN: tuple[TaskDefinition, ...] = (
    TaskDefinition("MarketData-Backfill", "Marktdaten", "Backfill-TradingMarketData.py", ("--write",), "market-data-backfill.log", ("17:15",), at_login=True),
    TaskDefinition("FXRates-Backfill", "FX-Kurse", "Backfill-TradingFXRatesECB.py", ("--write", "--quote-currency", "USD,GBP,AUD,KRW"), "fx-rates-backfill.log",
                   ("17:20",), at_login=True),
    TaskDefinition("EventsNews-Backfill", "Events / News", "Backfill-TradingEventsNews.py", ("--write",), "events-news-backfill.log", ("17:25",), at_login=True),
    TaskDefinition("Fundamentals-SEC", "Fundamentals (SEC)", "Backfill-TradingFundamentalsSEC.py", (), "fundamentals-sec-backfill.log", ("10:00",), "sunday",
                   at_login=True),
    TaskDefinition("Candidate-Discovery", "Candidate Discovery", "Discover-TradingCandidates.py", (), "candidate-discovery.log", ("17:35",), time_limit_h=2,
                   root_workdir=True),
    TaskDefinition("Market-Intelligence", "Market Intelligence", "Collect-TradingMarketIntelligence.py", (), "market-intelligence.log", ("08:15", "17:45"),
                   time_limit_h=2, root_workdir=True),
    TaskDefinition("ETF-Refresh", "ETF-Daten", "Refresh-TradingEtfData.py", ("--write",), "etf-refresh.log", ("11:00",), "sunday", time_limit_h=1,
                   root_workdir=True),
)
BUILTIN_BY_KEY = {t.key: t for t in BUILTIN}
EDITABLE = ("times", "day", "enabled")


def key_of(name: str) -> str:
    """``Trading-ETF-Refresh`` / ``ETF-Refresh`` / ``etf-refresh`` -> ``ETF-Refresh``; ``KeyError`` for anything that is not a VIPER task."""
    text = name.strip()
    if text.lower().startswith(PREFIX.lower()):
        text = text[len(PREFIX):]
    for key in BUILTIN_BY_KEY:
        if key.lower() == text.lower():
            return key
    raise KeyError(f"keine VIPER-Aufgabe: {name!r} (erlaubt: {', '.join(BUILTIN_BY_KEY)})")


def check_times(times: Iterable[str]) -> tuple[str, ...]:
    out = tuple(sorted({t.strip() for t in times if t.strip()}))
    if not out:
        raise ValueError("mindestens eine Uhrzeit HH:MM")
    for t in out:
        if not _TIME.match(t):
            raise ValueError(f"Uhrzeit {t!r}: erwartet HH:MM")
    return out


def check_day(day: str) -> str:
    value = day.strip().lower()
    if value not in DAYS:
        raise ValueError(f"Tag {day!r}: erwartet {', '.join(DAYS)}")
    return value


@dataclass
class TaskStore:
    """The changes of this installation to the built-in definitions (``<root>/scheduler/tasks.json``)."""

    path: Path
    changes: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "TaskStore":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(Path(path))
        tasks = raw.get("tasks") if isinstance(raw, dict) else None
        if not isinstance(tasks, dict):
            raise ValueError(f"{path}: erwartet {{\"tasks\": {{...}}}}")
        return cls(Path(path), {k: dict(v) for k, v in tasks.items() if k in BUILTIN_BY_KEY and isinstance(v, dict)})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"tasks": self.changes}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def definition(self, key: str) -> Optional[TaskDefinition]:
        """The definition of this installation; ``None`` when the task was deleted."""
        base, change = BUILTIN_BY_KEY[key], self.changes.get(key, {})
        if change.get("deleted"):
            return None
        values = {k: change[k] for k in EDITABLE if k in change}
        if "times" in values:
            values["times"] = check_times(values["times"])
        if "day" in values:
            values["day"] = check_day(values["day"])
        if "enabled" in values:
            values["enabled"] = bool(values["enabled"])
        return replace(base, **values)

    def definitions(self) -> list[TaskDefinition]:
        return [d for d in (self.definition(t.key) for t in BUILTIN) if d is not None]

    def deleted(self) -> list[str]:
        return [t.key for t in BUILTIN if self.changes.get(t.key, {}).get("deleted")]

    def edit(self, key: str, times: Optional[Iterable[str]] = None, day: Optional[str] = None, enabled: Optional[bool] = None) -> TaskDefinition:
        if self.definition(key) is None:
            raise KeyError(f"{PREFIX}{key} ist gelöscht (viper-ai tasks restore {key})")
        change = self.changes.setdefault(key, {})
        if times is not None:
            change["times"] = list(check_times(times))
        if day is not None:
            change["day"] = check_day(day)
        if enabled is not None:
            change["enabled"] = bool(enabled)
        self._drop_defaults(key)
        return self.definition(key)

    def delete(self, key: str) -> None:
        self.changes[key] = {"deleted": True}

    def restore(self, key: str) -> TaskDefinition:
        """Back to the built-in definition (also after a delete)."""
        self.changes.pop(key, None)
        return BUILTIN_BY_KEY[key]

    def _drop_defaults(self, key: str) -> None:
        base, change = asdict(BUILTIN_BY_KEY[key]), self.changes.get(key, {})
        for k in list(change):
            if k == "times" and tuple(change[k]) == base["times"] or k != "times" and change[k] == base.get(k):
                del change[k]
        if not change:
            self.changes.pop(key, None)


def schedule_text(task: TaskDefinition) -> str:
    """``täglich 17:15``, ``So 10:00``, ``täglich 08:15, 17:45`` (+ ``bei Anmeldung``)."""
    short = {"monday": "Mo", "tuesday": "Di", "wednesday": "Mi", "thursday": "Do", "friday": "Fr", "saturday": "Sa", "sunday": "So"}
    text = ("täglich " if task.day == "daily" else short[task.day] + " ") + ", ".join(task.times)
    return text + (" + bei Anmeldung" if task.at_login else "")

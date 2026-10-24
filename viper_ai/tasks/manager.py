# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The task management of VIPER-AI: one API for the CLI (``viper-ai tasks``) and the Tasks page of the web front end.

The definitions (:mod:`.definitions`) are the truth; the backend of the operating system (:mod:`.windows`, :mod:`.linux`) only deploys them and reports
what is deployed.  Every change (edit, enable, disable, delete) is made to the definition first and then deployed, so the scheduler never holds a
state VIPER does not know.  Only the seven VIPER tasks are ever touched.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .. import runtime
from .definitions import BUILTIN_BY_KEY, PREFIX, TaskDefinition, TaskStore, key_of
from .status import LogTail, TaskInfo, add_log_facts, tail_log


@dataclass(frozen=True)
class TaskCommand:
    """How the scheduler starts a task: the program, its arguments, the working directory and the environment it needs."""

    program: str
    args: list[str]
    workdir: Optional[str]
    log: Path
    env: dict = field(default_factory=dict)


def default_backend():
    if os.name == "nt":
        from .windows import WindowsBackend

        return WindowsBackend()
    if sys.platform.startswith("linux"):
        from .linux import SystemdBackend

        return SystemdBackend()
    raise RuntimeError(f"kein Scheduler-Backend für {sys.platform} (unterstützt: Windows, Linux mit systemd)")


def python_program() -> str:
    """The interpreter of this installation; on Windows ``pythonw.exe`` (no console window on every run)."""
    exe = Path(sys.executable)
    if os.name == "nt":
        windowless = exe.with_name("pythonw.exe")
        return str(windowless if windowless.is_file() else exe)
    return str(exe)


class TaskManager:
    def __init__(self, backend=None, store_path: Optional[Path] = None, prefix: str = PREFIX, root: Optional[Path] = None,
                 app_dir: Optional[Path] = None, check_environment: bool = True) -> None:
        self.paths = runtime.runtime_paths()
        self.backend = backend or default_backend()
        self.root = Path(root) if root else self.paths.trading_root()
        self.store_path = Path(store_path) if store_path else self.root / "scheduler" / "tasks.json"
        self.prefix = prefix
        self.app_dir = Path(app_dir) if app_dir else runtime.APP_DIR
        self.check_environment = check_environment

    # -- definitions --------------------------------------------------------------------------------------------------------------
    def store(self) -> TaskStore:
        return TaskStore.load(self.store_path)

    def definitions(self) -> list[TaskDefinition]:
        return self.store().definitions()

    def scheduler_name(self, task: TaskDefinition) -> str:
        return self.prefix + task.key

    def key(self, name: str) -> str:
        text = name.strip()
        if self.prefix != PREFIX and text.lower().startswith(self.prefix.lower()):
            text = text[len(self.prefix):]
        return key_of(text)

    def log_path(self, task: TaskDefinition) -> Path:
        return self.root / "logs" / task.log

    def command(self, task: TaskDefinition) -> TaskCommand:
        log = self.log_path(task)
        args = [str(self.app_dir / "Run-TradingTask.py"), str(log), str(self.app_dir / task.script), *task.args]
        return TaskCommand(python_program(), args, str(self.root) if task.root_workdir else None, log, {self.paths.TRADING_ROOT_ENV: str(self.root)})

    def _entries(self, tasks: Iterable[TaskDefinition]) -> list[tuple[str, TaskDefinition, TaskCommand]]:
        return [(self.scheduler_name(t), t, self.command(t)) for t in tasks]

    # -- state ----------------------------------------------------------------------------------------------------------------------
    def status(self) -> list[tuple[TaskDefinition, TaskInfo]]:
        """Every defined task with what the scheduler has (``found``, state, runs, ``drift`` = differences to the definition) and its log facts."""
        tasks = self.definitions()
        infos = self.backend.query(self._entries(tasks))
        return [(t, add_log_facts(infos[self.scheduler_name(t)], self.log_path(t))) for t in tasks]

    def problems(self) -> list[str]:
        out = []
        if self.check_environment:
            problem = self.backend.environment_problem(self.root, self.paths.default_trading_root())
            if problem:
                out.append(problem)
        for name in ("Run-TradingTask.py", *sorted({t.script for t in self.definitions()})):
            if not (self.app_dir / name).is_file():
                out.append(f"Skript fehlt: {self.app_dir / name}")
        return out

    # -- changes ----------------------------------------------------------------------------------------------------------------
    def deploy(self, dry_run: bool = False, only: Optional[Iterable[str]] = None) -> list[tuple[str, str]]:
        """Bring the scheduler to the definitions: create missing tasks, update differing ones, enabled state as defined; idempotent.
        A task deleted in VIPER is removed from the scheduler too."""
        problems = self.problems()
        if problems and not dry_run:
            raise RuntimeError("; ".join(problems))
        keys = None if only is None else {self.key(n) for n in only}
        tasks = [t for t in self.definitions() if keys is None or t.key in keys]
        entries = self._entries(tasks)
        current = self.backend.query(entries)
        out = [(name, self.backend.deploy(name, task, command, current.get(name), dry_run)) for name, task, command in entries]
        for key in self.store().deleted():
            if keys is None or key in keys:
                out.append((self.prefix + key, self.backend.remove(self.prefix + key, dry_run)))
        return out

    def edit(self, name: str, times: Optional[Iterable[str]] = None, day: Optional[str] = None, enabled: Optional[bool] = None,
             dry_run: bool = False) -> tuple[TaskDefinition, str]:
        """Change the definition (time, day, enabled), save it and deploy this task."""
        key = self.key(name)
        store = self.store()
        task = store.edit(key, times, day, enabled)
        if dry_run:
            return task, "geändert (Probelauf, nichts gespeichert)"
        store.save()
        return task, dict(self.deploy(only=[key])).get(self.prefix + key, "")

    def delete(self, name: str, dry_run: bool = False) -> str:
        """Delete the VIPER task: mark it deleted in the definitions and remove it from the scheduler."""
        key = self.key(name)
        result = self.backend.remove(self.prefix + key, dry_run)
        if not dry_run:
            store = self.store()
            store.delete(key)
            store.save()
        return result

    def restore(self, name: str) -> str:
        """Back to the built-in definition (undoes edits and a delete) and deploy it."""
        key = self.key(name)
        store = self.store()
        store.restore(key)
        store.save()
        return dict(self.deploy(only=[key])).get(self.prefix + key, "")

    def run(self, name: str) -> tuple[bool, str]:
        """Start the deployed task now through the scheduler (the same run as at its time); never a running, disabled or missing one."""
        key = self.key(name)
        task = self.store().definition(key)
        if task is None:
            return False, f"{self.prefix}{key} ist gelöscht"
        sched = self.scheduler_name(task)
        info = self.backend.query(self._entries([task]))[sched]
        if not info.found:
            return False, f"{sched} ist nicht deployt (viper-ai tasks deploy)"
        if info.running:
            return False, f"{sched} läuft bereits"
        if info.disabled:
            return False, f"{sched} ist deaktiviert"
        self.backend.run(sched)
        return True, f"{sched} gestartet"

    def tail(self, name: str) -> LogTail:
        return tail_log(self.log_path(BUILTIN_BY_KEY[self.key(name)]))

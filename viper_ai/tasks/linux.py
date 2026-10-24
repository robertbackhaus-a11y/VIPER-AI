# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Linux backend: deploys the VIPER task definitions as systemd user units (``viper-<key>.service`` + ``viper-<key>.timer``).  No task data of its own.

* the service is ``Type=oneshot`` and runs the Python of this installation with ``Run-TradingTask.py <log> <script> [args]``, ``VIPER_TRADING_ROOT`` set to
  the root deployed for, ``TimeoutStartSec`` = the time limit; a oneshot service that is still running is not started a second time (like ``IgnoreNew``);
* the timer has one ``OnCalendar=`` per time (``*-*-* 17:15:00`` or ``Sun *-*-* 11:00:00``), evaluated in the local time zone of the machine, so the
  wall-clock time stays across the change between summer and winter time; ``Persistent=true`` catches up a run missed while the machine was off;
* "at login": the service is also wanted by ``default.target`` of the user manager, which starts with the user's session;
* enabled = the timer is enabled and started; disabled = stopped and disabled.

The unit files carry a marker line; only files with that marker and the names of the definitions are ever written or removed.  ``deploy`` writes a
file only when its content differs and runs ``daemon-reload`` only then, so a second deploy changes nothing and a unit changed by hand is put back.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from .definitions import PREFIX, TaskDefinition
from .status import TaskInfo, definition_triggers

MARKER = "# Managed by VIPER-AI (viper-ai tasks); changes made here are overwritten by the next deploy."
CALENDAR_DAY = {"daily": "*-*-*", "monday": "Mon *-*-*", "tuesday": "Tue *-*-*", "wednesday": "Wed *-*-*", "thursday": "Thu *-*-*", "friday": "Fri *-*-*",
                "saturday": "Sat *-*-*", "sunday": "Sun *-*-*"}


class SubprocessRunner:
    def run(self, args: list[str], timeout: float) -> tuple[int, str, str]:
        done = subprocess.run(args, capture_output=True, timeout=timeout, text=True)  # noqa: S603 - fixed argument list
        return done.returncode, done.stdout, done.stderr


def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base) if base and Path(base).is_absolute() else Path.home() / ".config") / "systemd" / "user"


def unit_name(name: str) -> str:
    """``Trading-ETF-Refresh`` -> ``viper-etf-refresh``; another prefix (tests: ``ViperTest-ETF-Refresh``) -> ``vipertest-etf-refresh``."""
    return ("viper-" + name[len(PREFIX):] if name.startswith(PREFIX) else name).lower()


def _exec_line(program: str, args: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in [program, *args])


def service_text(task: TaskDefinition, command) -> str:
    lines = [MARKER, "[Unit]", f"Description=VIPER-AI {task.title} ({task.script})", "", "[Service]", "Type=oneshot"]
    lines += [f"Environment={shlex.quote(f'{k}={v}')}" for k, v in sorted(command.env.items())]
    if command.workdir:
        lines.append(f"WorkingDirectory={command.workdir}")
    lines += [f"ExecStart={_exec_line(command.program, command.args)}", f"TimeoutStartSec={task.time_limit_h}h"]
    if task.at_login:
        lines += ["", "[Install]", "WantedBy=default.target"]
    return "\n".join(lines) + "\n"


def timer_text(task: TaskDefinition, unit: str) -> str:
    lines = [MARKER, "[Unit]", f"Description=VIPER-AI {task.title}: Zeitplan", "", "[Timer]"]
    lines += [f"OnCalendar={CALENDAR_DAY[task.day]} {t}:00" for t in task.times]
    lines += ["Persistent=true", f"Unit={unit}.service", "", "[Install]", "WantedBy=timers.target"]
    return "\n".join(lines) + "\n"


def _usec(value: str) -> Optional[datetime]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(number / 1_000_000) if number > 0 else None


class SystemdBackend:
    name = "linux"
    title = "systemd (Benutzer-Timer)"

    def __init__(self, runner=None, directory: Optional[Path] = None) -> None:
        self.runner = runner or SubprocessRunner()
        self.directory = Path(directory) if directory else unit_dir()

    def _systemctl(self, *args: str, check: bool = True) -> str:
        code, out, err = self.runner.run(["systemctl", "--user", *args], 60.0)
        if check and code != 0:
            raise RuntimeError(f"systemctl --user {' '.join(args)}: {err.strip() or code}")
        return out

    def files(self, name: str, task: TaskDefinition, command) -> dict[Path, str]:
        unit = unit_name(name)
        return {self.directory / f"{unit}.service": service_text(task, command), self.directory / f"{unit}.timer": timer_text(task, unit)}

    def _drift(self, name: str, task: TaskDefinition, command) -> tuple[bool, list[str]]:
        found, differences = False, []
        for path, text in self.files(name, task, command).items():
            try:
                have = path.read_text(encoding="utf-8")
                found = True
            except FileNotFoundError:
                differences.append(f"{path.name} fehlt")
                continue
            if have != text:
                differences.append(f"{path.name} geändert")
        return found, differences

    def query(self, tasks) -> dict[str, TaskInfo]:
        infos: dict[str, TaskInfo] = {}
        for name, task, command in tasks:
            found, differences = self._drift(name, task, command)
            info = TaskInfo(name, found, log_file=task.log, triggers=definition_triggers(task), start_when_available=True, multiple_instances="IgnoreNew",
                            time_limit=f"PT{task.time_limit_h}H")
            if found:
                unit = unit_name(name)
                props = self._props(f"{unit}.service", "ActiveState,ExecMainStartTimestampMonotonic,ExecMainStartTimestamp,ExecMainStatus,Result")
                timer = self._props(f"{unit}.timer", "UnitFileState,NextElapseUSecRealtime,LastTriggerUSec")
                enabled = timer.get("UnitFileState") == "enabled"
                running = props.get("ActiveState") in ("activating", "active")
                info.state = "running" if running else "ready" if enabled else "disabled"
                info.next_run = _usec(timer.get("NextElapseUSecRealtime", "")) if enabled else None
                info.last_run = _usec(timer.get("LastTriggerUSec", ""))
                status = props.get("ExecMainStatus", "")
                info.last_result = int(status) if status.isdigit() and info.last_run else None
                if enabled != task.enabled:
                    differences.append("Aktiviert")
            info.drift = differences
            infos[name] = info
        return infos

    def _props(self, unit: str, names: str) -> dict[str, str]:
        out = self._systemctl("show", unit, f"--property={names}", "--timestamp=unix", check=False)
        values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        for key in ("NextElapseUSecRealtime", "LastTriggerUSec"):  # --timestamp=unix gives "@<seconds>"
            if values.get(key, "").startswith("@"):
                values[key] = str(int(values[key][1:]) * 1_000_000)
        return values

    def deploy(self, name: str, task: TaskDefinition, command, current: Optional[TaskInfo], dry_run: bool = False) -> str:
        if current is not None and current.found and not current.drift:
            return "unverändert"
        action = "aktualisiert" if current is not None and current.found else "angelegt"
        if dry_run:
            return action + " (Probelauf)"
        changed = False
        self.directory.mkdir(parents=True, exist_ok=True)
        for path, text in self.files(name, task, command).items():
            try:
                have = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                have = None
            if have is not None and not have.startswith(MARKER):
                raise RuntimeError(f"{path} gehört nicht VIPER-AI (keine Markierung); nicht überschrieben")
            if have != text:
                path.write_text(text, encoding="utf-8")
                changed = True
        if changed:
            self._systemctl("daemon-reload")
        unit = unit_name(name)
        self._systemctl("enable" if task.at_login and task.enabled else "disable", f"{unit}.service", check=False)
        if task.enabled:
            self._systemctl("enable", "--now", f"{unit}.timer")
        else:
            self._systemctl("disable", "--now", f"{unit}.timer", check=False)
        return action

    def remove(self, name: str, dry_run: bool = False) -> str:
        unit = unit_name(name)
        paths = [self.directory / f"{unit}.timer", self.directory / f"{unit}.service"]
        present = [p for p in paths if p.is_file()]
        if not present:
            return "nicht vorhanden"
        for path in present:
            if not path.read_text(encoding="utf-8").startswith(MARKER):
                raise RuntimeError(f"{path} gehört nicht VIPER-AI (keine Markierung); nicht gelöscht")
        if dry_run:
            return "gelöscht (Probelauf)"
        self._systemctl("disable", "--now", f"{unit}.timer", check=False)
        self._systemctl("disable", f"{unit}.service", check=False)
        for path in present:
            path.unlink()
        self._systemctl("daemon-reload")
        return "gelöscht"

    def run(self, name: str) -> None:
        self._systemctl("start", "--no-block", f"{unit_name(name)}.service")

    def environment_problem(self, root: Path, default_root: Path) -> Optional[str]:
        return None  # the service sets VIPER_TRADING_ROOT itself

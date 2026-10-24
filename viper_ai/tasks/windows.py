# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Windows backend: deploys the VIPER task definitions to the Windows Task Scheduler and reads their state.  No task data of its own.

A task is registered from a Task Scheduler XML that is generated here from the definition (``Register-ScheduledTask -Xml ... -Force``):

* the action ``pythonw.exe`` of this installation (no console window) with ``Run-TradingTask.py <log> <script> [args]``;
* one calendar trigger per time, daily or weekly on the day of the definition, with the start time as local wall-clock time *without* an offset
  (``2026-01-01T17:15:00``), so the task keeps its local time across the change between summer and winter time; a logon trigger when the
  definition asks for one;
* ``StartWhenAvailable`` (a missed run is caught up), ``MultipleInstancesPolicy IgnoreNew`` (never two runs at once), the time limit, enabled as defined;
* the principal is the current user, interactive token (runs only while the user is logged on, no stored password).

``deploy`` compares the registered task with that XML (action, triggers, settings, enabled) and registers again only when they differ: a second
deploy changes nothing, an external change in the Task Scheduler is put back.  Only the names of the definitions are ever touched.
PowerShell is used as the transport (JSON and XML out, a generated XML in); task names come from the definitions and never contain quotes.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol
from xml.sax.saxutils import escape

from .definitions import TaskDefinition
from .status import TaskInfo, TriggerInfo

CREATE_NO_WINDOW = 0x08000000
NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
START_DATE = "2026-01-01"  # the date part of every start boundary (only the time of day matters)
DAY_XML = {"monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday", "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday",
           "sunday": "Sunday"}
MARKER = "Managed by VIPER-AI (viper-ai tasks); changes made here are overwritten by the next deploy."


class CommandRunner(Protocol):
    def run(self, args: list[str], timeout: float) -> tuple[int, str, str]: ...


class SubprocessRunner:
    """Runs ``args`` without a console window and decodes the output as UTF-8."""

    def run(self, args: list[str], timeout: float) -> tuple[int, str, str]:
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        done = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)  # noqa: S603 - fixed argument list
        return done.returncode, done.stdout.decode("utf-8-sig", "replace"), done.stderr.decode("utf-8", "replace")


def powershell_args(script: str) -> list[str]:
    prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $ErrorActionPreference = 'Stop'; "
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prefix + script]


def _quote(text: str) -> str:
    if "'" in text:
        raise ValueError(f"unzulässiges Zeichen in {text!r}")
    return f"'{text}'"


def current_user() -> str:
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def command_line(args: list[str]) -> str:
    """The argument string of the action: paths in double quotes (as the tasks always had them), plain options as they are."""
    return " ".join(f'"{a}"' if ("\\" in a or "/" in a or " " in a) else a for a in args)


def task_xml(task: TaskDefinition, program: str, args: list[str], workdir: Optional[str], user: str) -> str:
    """The Task Scheduler XML of one definition."""
    triggers = []
    if task.at_login:
        triggers.append(f"<LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>")
    for time in task.times:
        schedule = ("<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>" if task.day == "daily" else
                    f"<ScheduleByWeek><DaysOfWeek><{DAY_XML[task.day]} /></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
        triggers.append(f"<CalendarTrigger><StartBoundary>{START_DATE}T{time}:00</StartBoundary><Enabled>true</Enabled>{schedule}</CalendarTrigger>")
    workdir_xml = f"<WorkingDirectory>{escape(workdir)}</WorkingDirectory>" if workdir else ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        f"<RegistrationInfo><Description>{escape(task.title)}: {escape(task.script)}. {MARKER}</Description></RegistrationInfo>"
        f"<Triggers>{''.join(triggers)}</Triggers>"
        f'<Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>'
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><StartWhenAvailable>true</StartWhenAvailable>"
        f"<Enabled>{'true' if task.enabled else 'false'}</Enabled><ExecutionTimeLimit>PT{task.time_limit_h}H</ExecutionTimeLimit></Settings>"
        f'<Actions Context="Author"><Exec><Command>{escape(program)}</Command><Arguments>{escape(command_line(args))}</Arguments>{workdir_xml}</Exec></Actions>'
        "</Task>"
    )


def _text(node, path: str, default: str = "") -> str:
    found = node.find(path) if node is not None else None
    return (found.text or "").strip() if found is not None and found.text is not None else default


def summary(xml_text: str) -> dict:
    """What decides whether a registered task matches its definition: action, triggers (kind, local time, days, offset), settings, enabled."""
    root = ET.fromstring(xml_text.encode("utf-16") if xml_text.lstrip().startswith("<?xml") and "UTF-16" in xml_text[:60] else xml_text.encode("utf-8"))
    triggers = []
    found = root.find(f"{NS}Triggers")
    for trig in (list(found) if found is not None else []):
        tag = trig.tag.replace(NS, "")
        if tag == "LogonTrigger":
            triggers.append(("logon", "", "", _text(trig, f"{NS}Enabled", "true")))
            continue
        start = _text(trig, f"{NS}StartBoundary")
        time = start[11:16] if len(start) >= 16 else ""
        offset = start[19:] if len(start) > 19 else ""  # "+02:00", "Z": a start bound to UTC (not DST-safe)
        week = trig.find(f"{NS}ScheduleByWeek")
        if week is not None:
            day_nodes = week.find(f"{NS}DaysOfWeek")
            days = ",".join(sorted(d.tag.replace(NS, "") for d in (list(day_nodes) if day_nodes is not None else [])))
            when = f"weekly/{_text(week, f'{NS}WeeksInterval', '1')}/{days}"
        elif trig.find(f"{NS}ScheduleByDay") is not None:
            when = f"daily/{_text(trig.find(f'{NS}ScheduleByDay'), f'{NS}DaysInterval', '1')}"
        else:
            when = tag
        triggers.append((when, time, offset, _text(trig, f"{NS}Enabled", "true")))
    settings = root.find(f"{NS}Settings")
    exec_ = root.find(f"{NS}Actions/{NS}Exec")
    return {
        "command": _text(exec_, f"{NS}Command"), "arguments": _text(exec_, f"{NS}Arguments"), "workdir": _text(exec_, f"{NS}WorkingDirectory"),
        "triggers": sorted(triggers), "enabled": _text(settings, f"{NS}Enabled", "true"),
        "multiple_instances": _text(settings, f"{NS}MultipleInstancesPolicy", "IgnoreNew"), "start_when_available": _text(settings, f"{NS}StartWhenAvailable", "false"),
        "time_limit": _text(settings, f"{NS}ExecutionTimeLimit", "PT72H"),
    }


DRIFT_TEXT = {"command": "Programm", "arguments": "Argumente", "workdir": "Arbeitsverzeichnis", "triggers": "Zeitplan", "enabled": "Aktiviert",
              "multiple_instances": "Mehrfachstart", "start_when_available": "Nachholen", "time_limit": "Zeitlimit"}


def drift(registered_xml: str, wanted_xml: str) -> list[str]:
    have, want = summary(registered_xml), summary(wanted_xml)
    return [DRIFT_TEXT[k] for k in want if have.get(k) != want[k]]


def _when(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _trigger(raw) -> Optional[TriggerInfo]:
    if not isinstance(raw, dict):
        return None
    kind_raw = str(raw.get("type") or "")
    kind = "Daily" if "Daily" in kind_raw else "Weekly" if "Weekly" in kind_raw else "Logon" if "Logon" in kind_raw else "Other"
    start = str(raw.get("start") or "")
    time = start[11:16] if len(start) >= 16 else None  # the local wall-clock time as registered
    interval, weeks, mask = raw.get("days_interval"), raw.get("weeks_interval"), raw.get("days_of_week")
    return TriggerInfo(kind, time, int(interval) if isinstance(interval, (int, float)) and interval else 1,
                       int(weeks) if isinstance(weeks, (int, float)) and weeks else 1, int(mask) if isinstance(mask, (int, float)) else 0, raw.get("enabled") is not False)


def query_script(names: list[str]) -> str:
    return (
        f"$names = @({','.join(_quote(n) for n in names)}); "
        "$rows = foreach ($n in $names) { "
        "$t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; "
        "if ($null -eq $t) { [pscustomobject]@{ name = $n; found = $false } } else { "
        "$i = $t | Get-ScheduledTaskInfo; "
        "[pscustomobject]@{ name = $n; found = $true; state = [string]$t.State; xml = [string](Export-ScheduledTask -TaskName $n); "
        "last_run = $(if ($i.LastRunTime -gt [datetime]'2001-01-01') { $i.LastRunTime.ToString('o') } else { $null }); "
        "next_run = $(if ($i.NextRunTime -gt [datetime]'2001-01-01') { $i.NextRunTime.ToString('o') } else { $null }); "
        "last_result = [int64]$i.LastTaskResult; missed_runs = [int]$i.NumberOfMissedRuns; action_execute = [string]$t.Actions[0].Execute; "
        "start_when_available = [bool]$t.Settings.StartWhenAvailable; multiple_instances = [string]$t.Settings.MultipleInstances; time_limit = [string]$t.Settings.ExecutionTimeLimit; "
        "triggers = @($t.Triggers | ForEach-Object { [pscustomobject]@{ type = [string]$_.CimClass.CimClassName; start = [string]$_.StartBoundary; "
        "days_interval = [int]$_.DaysInterval; weeks_interval = [int]$_.WeeksInterval; days_of_week = [int]$_.DaysOfWeek; enabled = [bool]$_.Enabled } }) } } }; "
        "ConvertTo-Json -InputObject @($rows) -Compress -Depth 5"
    )


class WindowsBackend:
    name = "windows"
    title = "Windows-Aufgabenplanung"

    def __init__(self, runner: Optional[CommandRunner] = None, user: Optional[str] = None) -> None:
        self.runner = runner or SubprocessRunner()
        self.user = user or current_user()

    def _ps(self, script: str, timeout: float = 60.0) -> str:
        code, out, err = self.runner.run(powershell_args(script), timeout)
        if code != 0:
            raise RuntimeError(f"PowerShell-Fehler ({code}): {err.strip().splitlines()[0] if err.strip() else 'keine Meldung'}")
        return out

    def wanted_xml(self, task: TaskDefinition, name: str, command) -> str:
        return task_xml(task, command.program, command.args, command.workdir, self.user)

    def query(self, tasks: list[tuple[str, TaskDefinition, object]]) -> dict[str, TaskInfo]:
        """``[(scheduler name, definition, command)]`` -> state of each, with the differences to the definition (``drift``)."""
        if not tasks:
            return {}
        out = self._ps(query_script([name for name, _t, _c in tasks]))
        raw = json.loads(out) if out.strip() else []
        by_name = {str(r.get("name")): r for r in ([raw] if isinstance(raw, dict) else raw) if isinstance(r, dict)}
        infos: dict[str, TaskInfo] = {}
        for name, task, command in tasks:
            item = by_name.get(name)
            if not item or not item.get("found"):
                infos[name] = TaskInfo(name, log_file=task.log)
                continue
            result, missed = item.get("last_result"), item.get("missed_runs")
            info = TaskInfo(
                name, True, str(item.get("state") or ""), _when(item.get("last_run")), _when(item.get("next_run")),
                int(result) if isinstance(result, (int, float)) else None, task.log,
                [t for t in (_trigger(r) for r in (item.get("triggers") if isinstance(item.get("triggers"), list) else [])) if t is not None],
                item.get("start_when_available") if isinstance(item.get("start_when_available"), bool) else None, str(item.get("multiple_instances") or ""),
                str(item.get("time_limit") or ""), int(missed) if isinstance(missed, (int, float)) else None, action_execute=str(item.get("action_execute") or ""))
            try:
                info.drift = drift(str(item.get("xml") or ""), self.wanted_xml(task, name, command))
            except ET.ParseError:
                info.drift = ["Definition nicht lesbar"]
            infos[name] = info
        return infos

    def deploy(self, name: str, task: TaskDefinition, command, current: Optional[TaskInfo], dry_run: bool = False) -> str:
        """Register the task when it is missing or differs from the definition; returns ``angelegt`` / ``aktualisiert`` / ``unverändert``."""
        if current is not None and current.found and not current.drift:
            return "unverändert"
        action = "aktualisiert" if current is not None and current.found else "angelegt"
        if dry_run:
            return action + " (Probelauf)"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "task.xml"
            path.write_text(self.wanted_xml(task, name, command), encoding="utf-16")
            self._ps(f"Register-ScheduledTask -TaskName {_quote(name)} -Xml (Get-Content -Raw -LiteralPath {_quote(str(path))}) -Force | Out-Null")
        return action

    def remove(self, name: str, dry_run: bool = False) -> str:
        out = self._ps(f"if (Get-ScheduledTask -TaskName {_quote(name)} -ErrorAction SilentlyContinue) {{ 'found' }}")
        if "found" not in out:
            return "nicht vorhanden"
        if not dry_run:
            self._ps(f"Unregister-ScheduledTask -TaskName {_quote(name)} -Confirm:$false")
        return "gelöscht" + (" (Probelauf)" if dry_run else "")

    def run(self, name: str) -> None:
        self._ps(f"Start-ScheduledTask -TaskName {_quote(name)}")

    def environment_problem(self, root: Path, default_root: Path) -> Optional[str]:
        """The tasks run with the user's environment: their runtime root must be the one deployed for."""
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                configured = str(winreg.QueryValueEx(key, "VIPER_TRADING_ROOT")[0]).strip()
        except OSError:
            configured = ""
        effective = Path(configured) if configured else default_root
        if os.path.normcase(str(effective)) != os.path.normcase(str(root)):
            return (f"Die Tasks liefen mit dem Runtime-Root {effective} (Benutzer-Umgebungsvariable VIPER_TRADING_ROOT"
                    f"{'' if configured else ' nicht gesetzt'}), deployt wird für {root}")
        return None

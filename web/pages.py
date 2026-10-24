# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The endpoints behind the pages that replace the desktop GUI (M3): planner, watchlist with its actions, sectors and single-name exposure,
tasks, transaction import, settings and diagnostics.

Every endpoint does what the matching handler of ``gui/app.py`` does - the same tool, the same arguments, the same view model, the same order of
writes - so the wording, the checks and the safety rules stay those of the desktop GUI.  Nothing here computes trading figures.
"""

from __future__ import annotations

import asyncio
import ntpath
import re
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .deps import effective as vm_effective
from .deps import onboarding as vm_onboarding
from .deps import portfolio as vm_portfolio
from .deps import sectors as vm_sectors
from .deps import settings as settings_mod
from .deps import task_model, task_service, tool_policy
from .deps import tasks as vm_tasks
from .deps import transaction_import as vm_import
from .deps import watchlist as vm_watchlist
from .deps import watchlist_edit as vm_edit
from .views import jsonable

IMPORT_DIR_NAME = "web-import"  # uploaded export files: <settings folder>\web-import (never the trading root, never the package)
MAX_UPLOAD_BYTES = 50_000_000
TASK_POLL_HINT_S = 3


def _ok(data: Any) -> bool:
    """``McpResult.tool_ok`` of a direct call: the call returned and the tool did not report ``ok: false``."""
    return not (isinstance(data, dict) and data.get("ok") is False)


def _message(data: Any) -> str:
    return str(data.get("message") or data.get("error") or "") if isinstance(data, dict) else ""


def register(app: FastAPI, state) -> None:  # noqa: C901 - one flat list of endpoints
    engine = state.engine

    async def act(tool: str, arguments: dict) -> tuple[bool, Any, str]:
        """``(tool_ok, data, message)`` of one GUI action, like ``McpService.call_action``."""
        if not tool_policy.is_gui_action(tool):
            raise HTTPException(403, f"{tool} is not a GUI action")
        try:
            data = await engine.call(tool, **arguments)
        except TypeError as exc:
            return False, None, f"ungültige Argumente: {exc}"
        except Exception as exc:  # noqa: BLE001 - shown on the page like a failed MCP call
            return False, None, f"{type(exc).__name__}: {exc}"
        state.forget()
        return _ok(data), data, _message(data)

    # -- planner ----------------------------------------------------------------------------------------------------------------
    @app.get("/api/views/planner")
    async def planner(refresh: bool = False):
        return jsonable(vm_portfolio.build_planner(await state.orchestrator(refresh), datetime.now()))

    # -- watchlist ----------------------------------------------------------------------------------------------------------------
    @app.get("/api/views/watchlist")
    async def watchlist():
        vm = vm_watchlist.build_watchlist(await engine.read("get_watchlist", limit=200, table=True), datetime.now())
        sector_ok, sector_hint = vm_edit.sector_editable(vm.sector_groups)
        rows = []
        for row in vm.rows:
            editable, hint = vm_edit.strategy_editable(row)
            rows.append({**jsonable(row), "strategy_editable": editable, "strategy_hint": hint, "sector_editable": sector_ok,
                         "sector_tip": vm_edit.sector_tooltip(row, sector_hint), "sector_options": [[t, v] for t, v in vm_edit.sector_options(row, vm.sector_groups)]})
        out = jsonable(vm)
        out.update(rows=rows, headers=vm_watchlist.HEADERS, numeric=list(vm_watchlist.NUMERIC_COLUMNS), filters=vm_watchlist.filter_options(vm.rows),
                   strategy_options=[[t, v] for t, v in vm_edit.STRATEGY_OPTIONS])
        return out

    @app.post("/api/watchlist/add")
    async def watchlist_add(request: Request):
        body = await request.json()
        identifier, priority, dry_run = str(body.get("identifier") or "").strip(), body.get("priority"), bool(body.get("dry_run"))
        if not identifier:
            raise HTTPException(422, "identifier fehlt")
        problem = vm_watchlist.check_priority(priority)
        if problem:
            raise HTTPException(422, problem)
        arguments: dict[str, Any] = {"identifier": identifier}
        if dry_run:
            arguments["dry_run"] = True
        elif priority not in (None, ""):
            arguments["priority"] = int(priority)
        ok, data, message = await act("add_watchlist_candidate", arguments)
        return jsonable(vm_watchlist.describe_action(ok, data, message))

    @app.post("/api/watchlist/{security_id}/remove")
    async def watchlist_remove(security_id: int):
        ok, data, message = await act("remove_watchlist_candidate", {"security_id": security_id})
        return jsonable(vm_watchlist.describe_action(ok, data, message))

    @app.post("/api/watchlist/{security_id}/edit")
    async def watchlist_edit(security_id: int, request: Request):
        """"Änderung übernehmen": strategy, then priority, then sector; a failure stops the sequence (``MainWindow.apply_watchlist_edit``)."""
        changes = await request.json()
        if "priority" in changes:
            problem = vm_watchlist.check_priority(changes["priority"])
            if problem:
                raise HTTPException(422, problem)
        steps = []
        if "strategy" in changes:
            steps.append(("strategy", "set_security_strategy", {"security_id": security_id, "strategy": changes["strategy"]}))
        if "priority" in changes:
            steps.append(("priority", "set_watchlist_priority", {"security_id": security_id, "priority": None if changes["priority"] in (None, "") else int(changes["priority"])}))
        if "sector" in changes:
            steps.append(("sector", "set_security_sector", {"security_id": security_id, "sector": changes["sector"]}))
        saved, lines = [], []
        for field, tool, arguments in steps:
            ok, data, message = await act(tool, arguments)
            if field == "strategy":
                done, shown = vm_edit.strategy_saved(ok, data), vm_edit.describe_strategy(ok, data, message)
            elif field == "sector":
                done, shown = vm_edit.sector_saved(ok, data), vm_edit.describe_sector(ok, data, message)
            else:
                done, shown = vm_edit.priority_saved(ok, data), vm_watchlist.describe_action(ok, data, message)
                shown = (vm_watchlist.ActionMessage("info", "Priorität gespeichert.", True, "PRIORITY_SET") if done
                         else vm_watchlist.ActionMessage(shown.level, f"Die Priorität wurde nicht gespeichert: {shown.text}", False, shown.result, shown.tip))
            if not done:
                return {"saved": saved, "message": jsonable(vm_watchlist.ActionMessage(shown.level, "\n".join([*lines, shown.text]), False, shown.result, shown.tip))}
            saved.append(field)
            lines.append(shown.text)
        return {"saved": saved, "message": jsonable(vm_watchlist.ActionMessage("info", "\n".join(lines) or "Nichts zu speichern.", True))}

    @app.post("/api/onboarding")
    async def onboarding(request: Request):
        """"Neues Wertpapier aufnehmen": ``create=false`` checks (dry run), ``create=true`` creates after a clean check."""
        body = await request.json()
        form = body.get("form") or {}
        arguments = vm_onboarding.form_arguments(**{k: form.get(k, "") for k in ("symbol", "name", "asset_text", "isin", "wkn", "exchange", "currency", "country", "yahoo", "fmp")},
                                                 add_to_watchlist=bool(form.get("add_to_watchlist")),
                                                 priority=None if form.get("priority") in (None, "") else int(form["priority"]))
        missing = vm_onboarding.missing_fields(arguments)
        if missing:
            return {"result": "", "level": "warning", "text": "Bitte ausfüllen: " + ", ".join(missing), "lines": [], "can_create": False, "created": False, "existing": None}
        ok, data, message = await act("onboard_security", {**arguments, "dry_run": not body.get("create")})
        return jsonable(vm_onboarding.describe_onboarding(ok, data, message))

    # -- sectors ------------------------------------------------------------------------------------------------------------------
    @app.get("/api/views/sectors")
    async def sectors():
        overview, exposure = await asyncio.gather(engine.read("get_sector_overview"), engine.read("get_effective_exposure", top=40))
        now = datetime.now()
        vm = jsonable(vm_sectors.build_sectors(overview, now))
        vm["headers"] = {"sectors": vm_sectors.SECTOR_HEADERS, "positions": vm_sectors.POSITION_HEADERS, "etfs": vm_sectors.ETF_HEADERS}
        eff = jsonable(vm_effective.build_effective(exposure, now, vm_effective.direct_performance(overview)))
        eff["headers"] = {"companies": vm_effective.COMPANY_HEADERS, "etfs": vm_effective.ETF_HEADERS}
        eff["numeric"], eff["highlight"], eff["default_sort"] = list(vm_effective.NUMERIC_COLUMNS), list(vm_effective.EFFECTIVE_COLUMNS), vm_effective.DEFAULT_SORT_COLUMN
        return {"sectors": vm, "effective": eff}

    # -- tasks --------------------------------------------------------------------------------------------------------------------
    @app.get("/api/views/tasks")
    async def tasks():
        backend = task_service.TaskBackend(state.settings.trading_logs_dir)
        infos, freshness = await asyncio.gather(asyncio.to_thread(backend.query), engine.read("get_data_freshness"))
        freshness = freshness if isinstance(freshness, dict) and freshness.get("ok") is not False else None
        now = datetime.now()
        rows = vm_tasks.build_task_rows(infos, freshness, now, backend.logs_dir)
        etf_rows, etf_levels = vm_tasks.build_etf_rows(freshness, now)
        runs = []
        by_name = {t.name: t for t in infos}
        for name, watch in list(state.task_watches.items()):
            text, level, finished = vm_tasks.run_progress(watch, by_name.get(name), now)
            runs.append({"name": name, "text": text, "level": level, "finished": finished})
            if finished:
                del state.task_watches[name]
        return {"rows": jsonable(rows), "summary": vm_tasks.summarize(rows, freshness is not None), "etf": {"headers": vm_tasks.ETF_HEADERS, "rows": etf_rows, "levels": etf_levels},
                "runs": runs, "refreshed": vm_portfolio.fmt_time(now), "poll_s": TASK_POLL_HINT_S}

    @app.post("/api/tasks/{name}/run")
    async def task_run(name: str):
        """"Jetzt ausführen": starts the existing scheduled task (never the script behind it); a task already followed is not started twice."""
        if name not in task_model.SPECS:
            raise HTTPException(404, f"unbekannter Task: {name}")
        if name in state.task_watches:
            return {"ok": False, "message": "Dieser Task läuft oder startet gerade – kein zweiter Start."}
        state.task_watches[name] = vm_tasks.RunWatch(name, datetime.now())
        backend = task_service.TaskBackend(state.settings.trading_logs_dir)
        try:
            started, message = await asyncio.to_thread(backend.start, name)
        except Exception as exc:  # noqa: BLE001
            started, message = False, f"Start fehlgeschlagen: {exc}"
        if not started:
            state.task_watches.pop(name, None)
            return {"ok": False, "message": message}
        state.task_watches[name].started = datetime.now()
        return {"ok": True, "message": message}

    @app.get("/api/tasks/{name}/log")
    async def task_log(name: str):
        if name not in task_model.SPECS:
            raise HTTPException(404, f"unbekannter Task: {name}")
        tail = await asyncio.to_thread(task_service.TaskBackend(state.settings.trading_logs_dir).tail, name)
        return {"name": name, **asdict(tail)}

    # -- transaction import -------------------------------------------------------------------------------------------------------
    @app.post("/api/import/upload")
    async def import_upload(request: Request, name: str = "export.csv"):
        """Stores the chosen export file in the settings folder and answers its id; the check reads it from there (the tools take a local path)."""
        body = await request.body()
        if not body or len(body) > MAX_UPLOAD_BYTES:
            raise HTTPException(422, "leere oder zu große Datei")
        folder = settings_mod.app_dir() / IMPORT_DIR_NAME
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", ntpath.basename(name))[-80:] or "export.csv"
        file_id = f"{uuid.uuid4().hex[:12]}-{safe if safe.lower().endswith('.csv') else safe + '.csv'}"
        (folder / file_id).write_bytes(body)
        return {"file_id": file_id, "size": len(body)}

    def upload_path(file_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{12}-[A-Za-z0-9._-]+", file_id or ""):
            raise HTTPException(422, "ungültige Datei-Kennung")
        path = settings_mod.app_dir() / IMPORT_DIR_NAME / file_id
        if not path.is_file():
            raise HTTPException(404, "Datei nicht mehr vorhanden; bitte erneut wählen")
        return str(path)

    def import_arguments(body: dict) -> dict:
        return vm_import.request_arguments(upload_path(str(body.get("file_id"))), str(body.get("format_text") or "Parqet-Export"), bool(body.get("create_securities")),
                                           bool(body.get("include_historical")), body.get("strategies") or {}, body.get("aliases") or {})

    @app.post("/api/import/check")
    async def import_check(request: Request):
        ok, data, message = await act("check_transaction_import", import_arguments(await request.json()))
        return jsonable(vm_import.build(ok, data, message))

    @app.post("/api/import/run")
    async def import_run(request: Request):
        body = await request.json()
        token = str(body.get("plan_token") or "")
        if not token:
            raise HTTPException(422, "Kein Prüfergebnis: zuerst prüfen")
        ok, data, message = await act("import_transactions", {**import_arguments(body), "plan_token": token})
        return jsonable(vm_import.build(ok, data, message))

    @app.get("/api/import/options")
    async def import_options():
        return {"formats": list(vm_import.FORMATS), "strategies": list(vm_import.STRATEGY_CHOICES), "aliases": list(vm_import.ALIAS_CHOICES),
                "headers": {"securities": vm_import.SECURITY_HEADERS, "masterdata": vm_import.MASTERDATA_HEADERS, "positions": vm_import.POSITION_HEADERS,
                            "problems": vm_import.PROBLEM_HEADERS, "open_items": vm_import.OPEN_HEADERS, "differences": vm_import.DIFFERENCE_HEADERS},
                "strategy_values": {k: v for k, v in vm_import.STRATEGY_CHOICES.items()}, "alias_values": {k: v for k, v in vm_import.ALIAS_CHOICES.items()}}

    # -- settings and diagnostics -------------------------------------------------------------------------------------------------
    @app.get("/api/settings")
    async def get_settings():
        return {"settings": asdict(state.settings), "path": str(state.settings_path or settings_mod.settings_path()), "warnings": state.warnings}

    @app.post("/api/settings")
    async def save_settings(request: Request):
        """Only the fields the web uses; the GUI fields (MCP paths, window) stay as they are."""
        body = await request.json()
        editable = ("ollama_base_url", "ollama_model", "trading_db_path", "trading_logs_dir", "think_enabled", "refresh_interval_s")
        values = {k: body[k] for k in editable if k in body}
        problems = {k: p for k, v in values.items() if (p := settings_mod.validate_edit(k, v))}
        if problems:
            raise HTTPException(422, "; ".join(f"{k}: {p}" for k, p in problems.items()))
        new = replace(state.settings, **values)
        settings_mod.save_settings(new, state.settings_path)
        db_changed = new.trading_db_path != state.settings.trading_db_path
        state.settings = new
        return {"saved": True, "restart_needed": db_changed}

    @app.get("/api/diagnostics")
    async def diagnostics():
        from .deps import ollama_client

        s = state.settings
        db = await engine.read("database_status")
        ollama = await asyncio.to_thread(ollama_client.check_ollama, s.ollama_base_url)
        names = sorted(n for n in dir(engine.tools) if not n.startswith("_") and callable(getattr(engine.tools, n)) and n != "Valves")
        return {
            "rows": [
                ["Datenbank", engine.db_path], ["Datenbank-Status", "OK" if _ok(db) else f"Fehler: {_message(db)}"],
                ["Log-Ordner der Tasks", s.trading_logs_dir], ["Einstellungen", str(state.settings_path or settings_mod.settings_path())],
                ["Engine-Ordner", str(engine.tools._runtime_tools_directory())], ["Ollama-URL", s.ollama_base_url], ["Ollama-Modell", s.ollama_model],
                ["Ollama erreichbar", "Ja" if ollama.reachable else f"Nein ({ollama.error})"], ["Ollama-Version", ollama.version or "—"],
                ["Gewähltes Modell vorhanden", ("Ja" if ollama.has_model(s.ollama_model) else "Nein") if ollama.reachable else "—"],
            ],
            "models": list(ollama.models) if ollama.reachable else [],
            "tools": [[n, tool_policy.classify(n), "erlaubt" if engine.allowed(n) else "gesperrt"] for n in names],
            "warnings": state.warnings,
        }


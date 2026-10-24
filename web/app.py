# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""FastAPI application of the web front end: JSON under ``/api``, the static page under ``/``.

Every figure comes from the engine (``engine.Engine``); the endpoints only pick the tool, pass the arguments and serialise.  The server listens on
127.0.0.1 only.  A write (every method but ``GET``/``HEAD``/``OPTIONS``) is accepted only with the ``Host`` ``127.0.0.1:<port>`` or ``localhost:<port>`` of this server
and, when the browser sends one, the matching ``Origin`` ``http://127.0.0.1:<port>`` / ``http://localhost:<port>``; anything else gets 403.  So neither a foreign web
site in the browser nor a DNS-rebinding name can trigger an action.  There is no login: the protection is the local binding plus this check.

The web starts no job on its own: the scheduled jobs are Windows scheduled tasks, the Tasks page only shows them and starts one on an explicit click.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .deps import portfolio as vm_portfolio
from .deps import settings as settings_mod
from . import pages, reasoning
from .chat import ChatSession
from .engine import Engine, ToolNotAllowed
from .views import jsonable

STATIC = Path(__file__).resolve().parent / "static"
ORCHESTRATOR_TTL_S = 60  # the orchestrator takes seconds; pages opened within this time share one result (refresh=true forces a new run)
OLLAMA_STATUS_TIMEOUT_S = 1.5  # the header check of Ollama must not hold back the status line; details stay on the settings page


def version() -> str:
    try:
        from viper_ai import __version__
    except ImportError:  # repository checkout without the package
        return "dev"
    return str(__version__)


def private_files_warning(state) -> list[str]:
    """A repository checkout reads the private files (``*_local.json``) next to its code, a pip installation those of the runtime root.  When a checkout
    serves the database of the runtime root but lacks private files the root has, its ETF and sector figures are not those of the installation (the ETF
    data stand then reads as missing): say so instead of showing it silently."""
    paths = state.engine.module("runtime_paths")
    if paths.is_installed_package():
        return []
    try:
        root_universe = paths.trading_root() / "app" / "universe"
    except ValueError:
        return []
    missing = sorted(p.name for p in root_universe.glob("*_local.json") if not (paths.private_universe_dir() / p.name).is_file())
    if not missing:
        return []
    return [f"Repository-Checkout: private Dateien werden aus {paths.private_universe_dir()} gelesen; dort fehlen {', '.join(missing)} "
            f"(vorhanden in {root_universe}). ETF- und Sektorwerte entsprechen nicht der Installation."]


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def as_json(obj: Any, status: int = 200) -> Response:
    return Response(dumps(obj), status_code=status, media_type="application/json")


class State:
    def __init__(self, db_path: Optional[str] = None, settings_path: Optional[Path] = None):
        self.settings_path = settings_path
        loaded = settings_mod.load_settings(settings_path)
        self.settings, self.warnings = loaded.settings, list(loaded.warnings)
        self.engine = Engine(db_path or self.settings.trading_db_path)
        self._orchestrator: tuple[float, Any] | None = None
        self.task_watches: dict = {}  # scheduled tasks started from the Tasks page, followed until the scheduler reports their end
        self.chat = ChatSession(self)
        self._tasks = None

    def tasks(self):
        """The task management of this installation (``viper_ai.tasks``); created on first use."""
        if self._tasks is None:
            from .deps import task_manager

            self._tasks = task_manager.TaskManager()
        return self._tasks

    async def orchestrator(self, refresh: bool = False) -> Any:
        if refresh or self._orchestrator is None or time.monotonic() - self._orchestrator[0] > ORCHESTRATOR_TTL_S:
            self._orchestrator = (time.monotonic(), await self.engine.read("run_trading_orchestrator"))
        return self._orchestrator[1]

    def forget(self) -> None:
        """After a write the cached orchestrator result is stale."""
        self._orchestrator = None


async def ollama_status(settings) -> dict:
    """A real check of the configured Ollama (``check_ollama``, the check of the diagnostics) with a short timeout.  Only chat and reasoning need
    Ollama, so a failure is reported here and nothing else waits for it."""
    from .deps import ollama_client

    try:
        result = await asyncio.wait_for(asyncio.to_thread(ollama_client.check_ollama, settings.ollama_base_url, OLLAMA_STATUS_TIMEOUT_S),
                                        OLLAMA_STATUS_TIMEOUT_S * 2)
    except asyncio.TimeoutError:
        result = ollama_client.OllamaStatus(False, error="keine Antwort")
    model = result.reachable and result.has_model(settings.ollama_model)
    error = result.error if not result.reachable else "" if model else f"Modell {settings.ollama_model} nicht vorhanden"
    return {"ok": bool(model), "reachable": result.reachable, "version": result.version, "model": settings.ollama_model, "error": error}


LOCAL_HOSTS = ("127.0.0.1", "localhost")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def local_write_allowed(method: str, host: Optional[str], origin: Optional[str], port: Optional[int]) -> bool:
    """Whether a request may pass: reads always; a write only with ``Host`` = ``127.0.0.1:<port>`` / ``localhost:<port>`` of this server and an ``Origin``
    (when sent) of ``http://`` with one of these two hosts and the same port.  ``port`` is the port the server listens on (``None``: taken from ``Host``)."""
    if method.upper() in SAFE_METHODS:
        return True
    name, _, given = (host or "").strip().lower().rpartition(":")
    if name not in LOCAL_HOSTS or not given.isdigit() or (port is not None and int(given) != port):
        return False
    return origin is None or origin.strip().lower() in {f"http://{h}:{given}" for h in LOCAL_HOSTS}


def create_app(db_path: Optional[str] = None, settings_path: Optional[Path] = None) -> FastAPI:
    state = State(db_path, settings_path)

    app = FastAPI(title="VIPER-AI", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.viper = state

    @app.middleware("http")
    async def local_writes_only(request: Request, call_next):
        server = request.scope.get("server")
        port = server[1] if server and len(server) > 1 and server[1] else None
        if not local_write_allowed(request.method, request.headers.get("host"), request.headers.get("origin"), port):
            return JSONResponse({"ok": False, "error": "FOREIGN_ORIGIN"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"  # an updated installation is seen at the next page load
        return response

    @app.exception_handler(ToolNotAllowed)
    async def not_allowed(_request: Request, exc: ToolNotAllowed):
        return JSONResponse({"ok": False, "error": "TOOL_NOT_ALLOWED", "tool": str(exc)}, status_code=403)

    @app.exception_handler(FileNotFoundError)
    async def missing(_request: Request, exc: FileNotFoundError):
        return JSONResponse({"ok": False, "error": "NOT_FOUND", "message": str(exc)}, status_code=503)

    # -- status ---------------------------------------------------------------------------------------------------------------------
    @app.get("/api/status")
    async def status():
        db, ollama = await asyncio.gather(state.engine.read("database_status"), ollama_status(state.settings))
        return as_json({"version": version(), "database": db, "ollama": ollama, "db_path": state.engine.db_path,
                        "warnings": state.warnings + [w for w in settings_mod.runtime_warnings(state.settings) if "MCP-Server" not in w] + private_files_warning(state)})

    # -- portfolio (engine module portfolio_history) -----------------------------------------------------------------------------------
    @app.get("/api/portfolio")
    async def portfolio():
        return as_json(await state.engine.history("holdings"))

    @app.get("/api/portfolio/history")
    async def portfolio_history():
        return as_json(await state.engine.history("portfolio_history"))

    @app.get("/api/transactions")
    async def transactions(security_id: Optional[int] = None, limit: int = 2000):
        return as_json(await state.engine.history("transactions", security_id, limit))

    @app.get("/api/securities/{security_id}/prices")
    async def prices(security_id: int, start: Optional[str] = None):
        try:
            return as_json(await state.engine.history("price_history", security_id, start))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    # -- facade tools -----------------------------------------------------------------------------------------------------------------
    @app.get("/api/orchestrator")
    async def orchestrator(refresh: bool = False):
        return as_json(await state.orchestrator(refresh))

    @app.post("/api/read/{tool}")
    async def read_tool(tool: str, request: Request):
        """Any READ tool of the facade with its JSON arguments (watchlist, sectors, exposure, freshness, market intelligence, ...)."""
        arguments = await _arguments(request)
        try:
            return as_json(await state.engine.read(tool, **arguments))
        except TypeError as exc:
            raise HTTPException(422, f"invalid arguments for {tool}: {exc}") from exc

    @app.post("/api/action/{tool}")
    async def action(tool: str, request: Request):
        """A GUI action of the facade (explicit button of the page); never a READ tool, never sql_execute."""
        from .deps import tool_policy

        if not tool_policy.is_gui_action(tool):
            raise ToolNotAllowed(tool)
        arguments = await _arguments(request)
        try:
            result = await state.engine.call(tool, **arguments)
        except TypeError as exc:
            raise HTTPException(422, f"invalid arguments for {tool}: {exc}") from exc
        state.forget()
        return as_json(result)

    # -- view models of the desktop GUI (same wording and tables) -------------------------------------------------------------------
    @app.get("/api/views/dashboard")
    async def view_dashboard(refresh: bool = False):
        envelope = await state.orchestrator(refresh)
        db = await state.engine.read("database_status")
        return as_json(jsonable(vm_portfolio.build_dashboard(envelope, db, datetime.now())))

    pages.register(app, state)
    reasoning.register(app, state)

    # -- chat (SSE) ---------------------------------------------------------------------------------------------------------------
    @app.post("/api/chat")
    async def chat(request: Request):
        body = await _arguments(request)
        return StreamingResponse(state.chat.stream(str(body.get("text") or "")), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/chat/stop")
    async def chat_stop():
        state.chat.cancel()
        return {"ok": True}

    @app.post("/api/chat/reset")
    async def chat_reset():
        return {"ok": state.chat.reset()}

    @app.get("/api/chat/status")
    async def chat_status():
        s = state.settings
        return {"busy": state.chat.busy, "model": s.ollama_model, "think": s.think_enabled, "turns": sum(1 for m in state.chat.history if m.get("role") == "user"),
                "last_error": state.chat.last_error}

    # -- page -------------------------------------------------------------------------------------------------------------------------
    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


async def _arguments(request: Request) -> dict:
    body = await request.body()
    if not body:
        return {}
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise HTTPException(400, "body is not JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "body must be a JSON object")
    return data

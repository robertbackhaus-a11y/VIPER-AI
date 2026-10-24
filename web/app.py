# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""FastAPI application of the web front end: JSON under ``/api``, the static page under ``/``.

Every figure comes from the engine (``engine.Engine``); the endpoints only pick the tool, pass the arguments and serialise.  The server listens on
127.0.0.1 only.  A write (``POST``) from a page of another origin is refused, so a foreign web site cannot trigger an action in the browser.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .deps import portfolio as vm_portfolio
from .deps import settings as settings_mod
from . import pages, reasoning, scheduler as scheduler_routes
from .chat import ChatSession
from .engine import Engine, ToolNotAllowed
from .scheduler import JobRunner
from .views import jsonable

STATIC = Path(__file__).resolve().parent / "static"
ORCHESTRATOR_TTL_S = 60  # the orchestrator takes seconds; pages opened within this time share one result (refresh=true forces a new run)


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
        self.jobs = JobRunner(self)

    async def orchestrator(self, refresh: bool = False) -> Any:
        if refresh or self._orchestrator is None or time.monotonic() - self._orchestrator[0] > ORCHESTRATOR_TTL_S:
            self._orchestrator = (time.monotonic(), await self.engine.read("run_trading_orchestrator"))
        return self._orchestrator[1]

    def forget(self) -> None:
        """After a write the cached orchestrator result is stale."""
        self._orchestrator = None


def create_app(db_path: Optional[str] = None, settings_path: Optional[Path] = None, scheduler: bool = False) -> FastAPI:
    """``scheduler=True`` (``viper-ai-web``) starts the integrated task scheduler with the server; tests and tools leave it off."""
    state = State(db_path, settings_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if scheduler:
            state.jobs.start()
        try:
            yield
        finally:
            state.jobs.stop()

    app = FastAPI(title="VIPER-AI", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.viper = state

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
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
        db = await state.engine.read("database_status")
        return as_json({"version": version(), "database": db, "db_path": state.engine.db_path,
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
    scheduler_routes.register(app, state)

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

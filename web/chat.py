# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The chat of the web front end: the tool loop of the desktop GUI (``gui/services/tool_loop.py``) against the local Ollama, streamed to the page
as Server-Sent Events.

* The tools are those of the MCP server in the profile ``read`` (built in this process with ``server.build_server``), so the model sees exactly the
  names, descriptions, schemas, the compact orchestrator answer and the 40,000-character limit that every MCP client sees.  Actions are not even
  registered there; the loop itself also refuses anything that is not READ.
* One request at a time; the history lives in memory only (like the desktop GUI).  Stop closes the HTTP request to Ollama.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from .deps import ollama_client, tool_loop
from .engine import server_dir

TOOL_TIMEOUT_S = tool_loop.TOOL_TIMEOUT_S


@dataclass
class ToolResult:
    """The attributes of ``McpResult`` the tool loop reads."""

    name: str
    ok: bool
    data: Any = None
    error: Optional[str] = None
    kind: str = "OK"

    @property
    def tool_ok(self) -> bool:
        return self.ok and not (isinstance(self.data, dict) and self.data.get("ok") is False)

    @property
    def message(self) -> str:
        if self.error:
            return self.error
        if isinstance(self.data, dict) and self.data.get("ok") is False:
            return str(self.data.get("message") or self.data.get("error") or "Tool meldet einen Fehler")
        return ""


def build_read_server(db_path: str):
    """The MCP server of this installation in the profile ``read`` (not started; its tools are called in this process)."""
    name = "viper_ai_web_mcp_server"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, server_dir() / "server.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    saved = {k: os.environ.get(k) for k in ("TRADING_DB_PATH", "TRADING_TOOL_PROFILE", "TRADING_EXCLUDE")}
    os.environ.update(TRADING_DB_PATH=db_path, TRADING_TOOL_PROFILE="read", TRADING_EXCLUDE="")
    try:
        return sys.modules[name].build_server()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ChatSession:
    def __init__(self, state) -> None:
        self.state = state
        self.history: list[dict[str, Any]] = []
        self.busy = False
        self.last_error = ""
        self._token: Optional[ollama_client.CancelToken] = None
        self._server = None
        self._catalog: Optional[dict[str, dict[str, Any]]] = None
        self._lock = threading.Lock()

    async def catalog(self) -> dict[str, dict[str, Any]]:
        if self._catalog is None:
            self._server = build_read_server(self.state.engine.db_path)
            tools = await self._server.list_tools()
            self._catalog = {t.name: {"description": t.description or t.name, "input_schema": t.input_schema} for t in tools}
        return self._catalog

    def _call(self, loop: asyncio.AbstractEventLoop, name: str, arguments: dict[str, Any], token) -> ToolResult:
        if token.cancelled:
            return ToolResult(name, False, error="abgebrochen", kind="CANCELLED")
        future = asyncio.run_coroutine_threadsafe(self._server.call_tool(name, arguments), loop)
        try:
            result = future.result(TOOL_TIMEOUT_S)
        except TimeoutError:
            future.cancel()
            return ToolResult(name, False, error=f"Zeitüberschreitung nach {TOOL_TIMEOUT_S:.0f} s", kind="TIMEOUT")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(name, False, error=f"{type(exc).__name__}: {exc}"[:300], kind="TOOL_ERROR")
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            return ToolResult(name, False, error=" ".join(getattr(c, "text", "") for c in result.content)[:300], kind="TOOL_ERROR")
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            return ToolResult(name, True, json.loads(text))
        except ValueError:
            return ToolResult(name, False, error="Antwort ist kein JSON", kind="INVALID_RESULT")

    def cancel(self) -> None:
        if self._token is not None:
            self._token.cancel()

    def reset(self) -> bool:
        if self.busy:
            return False
        self.history, self.last_error = [], ""
        return True

    async def stream(self, text: str) -> AsyncIterator[str]:
        """The events of one request as SSE lines (``event: <kind>``, ``data: {"text", "data"}``)."""

        def sse(kind: str, text: str = "", data: Optional[dict] = None) -> str:
            return f"event: {kind}\ndata: {json.dumps({'text': text, 'data': data or {}}, ensure_ascii=False, default=str)}\n\n"

        text = (text or "").strip()
        if not text:
            yield sse("error", "Leere Frage", {"kind": "EMPTY"})
            yield sse("done", data={"outcome": "error"})
            return
        with self._lock:
            if self.busy:
                yield sse("warning", "Es läuft bereits eine Anfrage. Warte auf das Ende oder stoppe sie.")
                yield sse("done", data={"outcome": "busy"})
                return
            self.busy = True
        try:
            settings = self.state.settings
            try:
                client = ollama_client.OllamaClient(settings.ollama_base_url)
                catalog = await self.catalog()
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"SETTINGS: {exc}"
                yield sse("error", str(exc), {"kind": "SETTINGS"})
                yield sse("done", data={"outcome": "error"})
                return
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()
            token = self._token = ollama_client.CancelToken()
            runner = tool_loop.ToolLoop(client, catalog, lambda name, args, tok: self._call(loop, name, args, tok), settings.ollama_model, settings.think_enabled)

            def emit(event) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, event)

            def work():
                try:
                    return runner.run(list(self.history), text, emit, token)
                except Exception as exc:  # noqa: BLE001 - the page must survive anything the loop does
                    emit(tool_loop.LoopEvent("error", f"Interner Fehler: {type(exc).__name__}", {"kind": "INTERNAL"}))
                    emit(tool_loop.LoopEvent("done", data={"outcome": "error"}))
                    return tool_loop.LoopResult("error", self.history, error=f"INTERNAL: {type(exc).__name__}")

            task = asyncio.ensure_future(asyncio.to_thread(work))
            while True:
                event = await queue.get()
                if event.kind == "error":
                    self.last_error = f"{event.data.get('kind', 'ERROR')}: {event.text}"[:300]
                yield sse(event.kind, event.text, event.data)
                if event.kind == "done":
                    break
            result = await task
            if result.outcome == "done":
                self.history = result.history
        finally:
            if self._token is not None and self.busy:
                self._token.cancel()  # the page went away before the end: stop the request to Ollama
            self._token = None
            self.busy = False

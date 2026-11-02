# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""In-process access to the trading engine for the web API.

* The tools are the methods of the facade ``trading_sqlite.Tools`` (the same class the MCP server offers), loaded from the server folder of this
  installation and called in this process.  Only what the desktop GUI may call is allowed: the READ tools and ``tool_policy.GUI_ACTIONS``;
  ``sql_execute``, ``approve_swing_promotion`` and every unknown name are refused.
* The views the facade does not offer (holdings, transactions, daily bars, portfolio history) come from the read-only engine module
  ``portfolio_history`` over a read-only connection (``mode=ro`` + ``query_only``).
* The portfolio (depot) chosen in the header of the page is the context of every read of a request (:data:`PORTFOLIO`, set by the app from the
  request header ``X-Viper-Portfolio``): read connections get it through ``portfolio_scope.scope``, and a facade tool with a ``portfolio_id``
  argument gets it when the caller passes none.  ``None`` = the total of all active portfolios.
"""

from __future__ import annotations

import asyncio
import contextvars
import importlib.util
import inspect
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

from .deps import tool_policy

HERE = Path(__file__).resolve().parent
# the portfolio context of the current request (None = the total); see the module docstring
PORTFOLIO: contextvars.ContextVar[int | None] = contextvars.ContextVar("viper_portfolio", default=None)


class ToolNotAllowed(Exception):
    pass


def server_dir() -> Path:
    """``mcp`` next to this package (pip installation) or ``mcp-tools`` in the repository."""
    for candidate in (HERE.parent / "mcp", HERE.parent / "mcp-tools"):
        if (candidate / "trading_sqlite.py").is_file():
            return candidate
    raise FileNotFoundError(f"trading_sqlite.py not found next to {HERE.parent}")


def _load_tools_class():
    name = "viper_ai_web_trading_sqlite"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, server_dir() / "trading_sqlite.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].Tools


class Engine:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self.tools = _load_tools_class()()
        self.tools.valves.database_path = self.db_path

    # -- facade tools -------------------------------------------------------------------------------------------------------------
    @staticmethod
    def allowed(name: str) -> bool:
        return tool_policy.is_read(name) or tool_policy.is_gui_action(name)

    async def call(self, tool: str, /, **arguments: Any) -> Any:
        """A READ tool or a GUI action of the facade; anything else raises :class:`ToolNotAllowed`.  (``tool`` is positional only: tools have an argument ``name``.)"""
        if not self.allowed(tool) or tool.startswith("_") or not callable(getattr(self.tools, tool, None)):
            raise ToolNotAllowed(tool)
        method = getattr(self.tools, tool)
        if "portfolio_id" not in arguments and "portfolio_id" in inspect.signature(method).parameters:
            arguments = {**arguments, "portfolio_id": PORTFOLIO.get()}
        return await method(**arguments)

    async def read(self, tool: str, /, **arguments: Any) -> Any:
        if not tool_policy.is_read(tool):
            raise ToolNotAllowed(tool)
        return await self.call(tool, **arguments)

    # -- engine modules -----------------------------------------------------------------------------------------------------------
    def module(self, name: str):
        """An engine module of this installation, loaded the way the facade loads its helpers."""
        return self.tools._load_runtime_trading_module(name)

    def connect_readonly(self, portfolio_id: Any = "request") -> sqlite3.Connection:
        """A read-only connection in the portfolio context of the request (or the given ``portfolio_id``; ``None`` = the total)."""
        path = Path(self.db_path)
        if not path.is_file():
            raise FileNotFoundError(f"Trading database not found: {path}")
        conn = sqlite3.connect(f"file:///{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            self.module("portfolio_scope").scope(conn, PORTFOLIO.get() if portfolio_id == "request" else portfolio_id)
        except BaseException:
            conn.close()
            raise
        return conn

    def connect_write(self) -> sqlite3.Connection:
        """A short write connection (the portfolio management of the settings page); never scoped."""
        path = Path(self.db_path)
        if not path.is_file():
            raise FileNotFoundError(f"Trading database not found: {path}")
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def with_readonly(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        portfolio_id = PORTFOLIO.get()

        def run() -> Any:
            conn = self.connect_readonly(portfolio_id)
            try:
                return fn(conn)
            finally:
                conn.close()

        return await asyncio.to_thread(run)

    async def history(self, function: str, *args: Any, **kwargs: Any) -> Any:
        """``portfolio_history.<function>(conn, *args, **kwargs)`` on a read-only connection."""
        module = self.module("portfolio_history")
        return await self.with_readonly(lambda conn: getattr(module, function)(conn, *args, **kwargs))

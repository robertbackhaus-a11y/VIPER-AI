# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The web side of the MCP tool classification: READ may be called by the web front end and offered to the chat, everything else is an ACTION.

The classification itself is not kept here: it is the server's ``tool_policy.py`` (``viper_ai/mcp`` in the pip installation, ``mcp-tools`` in the repository),
so the server and the web front end can never disagree.  A tool that is not listed there is treated as an ACTION and denied (deny by default).

The web front end allows READ plus ``GUI_ACTIONS`` (the name of the server's tool profile ``gui``, which stays for MCP clients): those ACTIONs may be triggered by
an explicit button of the Watchlist or Import page; they are never offered to the chat.  ``sql_execute``, ``approve_swing_promotion`` and every unknown tool
stay denied.
"""

from __future__ import annotations

import importlib.util
import sys

from ... import runtime

_MODULE_NAME = "viper_ai_server_tool_policy"


def _load_server_policy():
    candidate = runtime.MCP_DIR / "tool_policy.py"  # viper_ai/mcp (pip installation) or mcp-tools (repository)
    if not candidate.is_file():
        raise ImportError(f"tool_policy.py of the MCP server not found: {candidate}")
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, candidate)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_server = _load_server_policy()

READ = _server.READ
ACTION = _server.ACTION
POLICY = _server.POLICY
GUI_ACTIONS = _server.GUI_ACTIONS
PROFILE_ENV = _server.PROFILE_ENV
PROFILE_GUI = _server.PROFILE_GUI
classify = _server.classify
is_read = _server.is_read
is_gui_action = _server.is_gui_action
is_known = _server.is_known
unclassified = _server.unclassified


class ToolDenied(Exception):
    """A tool outside the READ class was requested."""


def require_read(name: str) -> None:
    if not is_read(name):
        raise ToolDenied(f"{name}: {'Aktion' if is_known(name) else 'unbekanntes Tool'} – in VIPER nicht erlaubt")

# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Where the code of this installation lives, in both layouts, and its ``runtime_paths``.

pip installation: ``viper_ai/app`` (trading modules and scripts) and ``viper_ai/mcp`` (MCP server); repository checkout: ``tools/trading`` and ``mcp-tools``
next to the ``viper_ai`` folder.  Every runtime path (root, database, logs, task state, settings) comes from ``runtime_paths`` of the app folder.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
_INSTALLED = (PACKAGE_DIR / "app" / "runtime_paths.py").is_file()
APP_DIR = PACKAGE_DIR / "app" if _INSTALLED else PACKAGE_DIR.parent / "tools" / "trading"
MCP_DIR = PACKAGE_DIR / "mcp" if _INSTALLED else PACKAGE_DIR.parent / "mcp-tools"
_MODULE = "viper_ai_runtime_paths"


def is_installed() -> bool:
    """True for the pip installation, False for a repository checkout."""
    return _INSTALLED


def runtime_paths():
    """``runtime_paths`` of the app folder, loaded once under its own name (the scripts import the same file as ``runtime_paths``)."""
    module = sys.modules.get(_MODULE)
    if module is None:
        spec = importlib.util.spec_from_file_location(_MODULE, APP_DIR / "runtime_paths.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE] = module
        spec.loader.exec_module(module)
    return module

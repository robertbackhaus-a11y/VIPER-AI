# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""VIPER-AI: local, MCP-based trading agent (portfolio analysis and simulation only, no order execution).

The pip installation maps the existing source folders into this package without moving them in the repository::

    viper_ai.app   <- tools/trading   domain modules and the CLI scripts (flat modules, run with their own folder on sys.path)
    viper_ai.mcp   <- mcp-tools       MCP server adapter (27 tools)
    viper_ai.web   <- web             local web front end (extra ``web``)

and has its own subpackages: ``viper_ai.shared`` (view models and services of the web front end) and ``viper_ai.tasks`` (the task definitions and
their deployment to the Windows Task Scheduler or systemd user timers).

Runtime data (database, logs, task state, private universe files) never lives in the package; see ``viper_ai.app.runtime_paths``.
"""

__version__ = "0.6.0"

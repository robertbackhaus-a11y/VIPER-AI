# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The shared modules the web uses: view models and services (``viper_ai.shared``) and the task management (``viper_ai.tasks``).

Imported absolutely in both layouts: pip installation (``viper_ai.web``) and repository checkout (``web/`` next to the ``viper_ai`` folder, repository root on ``sys.path``).
"""

from __future__ import annotations

from viper_ai.shared.services import ollama_client, settings, task_model, tool_loop, tool_policy
from viper_ai.shared.viewmodels import effective, onboarding, portfolio, reason_codes, sectors, tasks, transaction_import, watchlist, watchlist_edit
from viper_ai.tasks import definitions as task_definitions
from viper_ai.tasks import manager as task_manager

__all__ = ["settings", "task_model", "task_manager", "task_definitions", "tool_policy", "ollama_client", "tool_loop", "effective", "onboarding", "portfolio",
           "reason_codes", "sectors", "tasks", "transaction_import", "watchlist", "watchlist_edit"]

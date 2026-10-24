# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The Qt-free modules of ``gui`` the web reuses, imported the same way in both layouts.

pip installation: ``viper_ai.web`` next to ``viper_ai.gui``; repository checkout: ``web/`` next to ``gui/`` (repository root on ``sys.path``).
"""

from __future__ import annotations

try:  # pip installation
    from ..gui.services import settings, task_model, task_service, tool_policy
    from ..gui.services import ollama_client, tool_loop
    from ..gui.viewmodels import effective, onboarding, portfolio, reason_codes, sectors, tasks, transaction_import, watchlist, watchlist_edit
except ImportError:  # repository checkout
    from gui.services import settings, task_model, task_service, tool_policy  # type: ignore[no-redef]
    from gui.services import ollama_client, tool_loop  # type: ignore[no-redef]
    from gui.viewmodels import effective, onboarding, portfolio, reason_codes, sectors, tasks, transaction_import, watchlist, watchlist_edit  # type: ignore[no-redef]

__all__ = ["settings", "task_model", "task_service", "tool_policy", "ollama_client", "tool_loop", "effective", "onboarding", "portfolio", "reason_codes", "sectors",
           "tasks", "transaction_import", "watchlist", "watchlist_edit"]

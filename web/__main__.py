# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Start the local web front end: ``viper-ai-web`` (with a console), ``viper-ai-start`` / ``viper-ai start`` (the standard start, no console window),
``python -m viper_ai.web`` or ``python -m web`` (repository root).

Listens on 127.0.0.1 only and opens the browser unless ``--no-browser`` is given.  When VIPER already runs on the port, no second server is started:
the browser is opened on the running one.  The database is the one of the settings (``trading_db_path``, default ``<root>\\data\\trading.db`` of
``runtime_paths``) unless ``--db`` names another.  Without a console (``pythonw``) the output goes to ``web.log`` in the settings folder.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.request
import webbrowser
from typing import Optional, Sequence

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def running(port: int) -> bool:
    """Whether a VIPER web server already answers on ``port``."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/status", timeout=2) as response:
            return response.status == 200 and "version" in json.loads(response.read() or b"{}")
    except (OSError, ValueError):
        return False


def _log_to_file() -> None:
    """``pythonw`` has no stdout/stderr; uvicorn logs there, so it goes to the settings folder instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from .deps import settings
    except ImportError:
        from web.deps import settings  # type: ignore[no-redef]
    folder = settings.app_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stream = open(folder / "web.log", "a", encoding="utf-8", errors="backslashreplace", buffering=1)
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def main(argv: Optional[Sequence[str]] = None, prog: str = "viper-ai-web") -> int:
    parser = argparse.ArgumentParser(prog=prog, description="VIPER-AI web front end on http://127.0.0.1:<port>/")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port on 127.0.0.1 (default {DEFAULT_PORT})")
    parser.add_argument("--db", default=None, help="absolute path of the trading database (default: trading_db_path of the settings)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser (for example for the autostart at logon)")
    parser.add_argument("--no-scheduler", action="store_true", help=argparse.SUPPRESS)  # without effect: the jobs are Windows scheduled tasks, the web never runs them on its own
    _log_to_file()
    args = parser.parse_args(argv)
    url = f"http://{HOST}:{args.port}/"
    if running(args.port):
        if not args.no_browser:
            webbrowser.open(url)
        print(f"VIPER-AI already runs on {url}")
        return 0

    import uvicorn

    try:
        from .app import create_app
    except ImportError:  # python -m web from the repository root
        from web.app import create_app  # type: ignore[no-redef]

    app = create_app(args.db)
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning", log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

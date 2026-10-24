# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Console entry points of the VIPER-AI pip installation.

Each entry point runs one existing script of the package exactly like ``python <script> <args>`` did: as ``__main__``, with the script's own folder first on
``sys.path`` and the script path in ``sys.argv[0]``, so its arguments, output, exit code and defaults (database path, ``--db``) are unchanged.
"""

from __future__ import annotations

import argparse
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__

PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR / "app"
MCP_DIR = PACKAGE_DIR / "mcp"

# console script -> (folder, script); the folder of every script stays its import root
COMMANDS: dict[str, tuple[Path, str]] = {
    "viper-ai-mcp": (MCP_DIR, "server.py"),
    "viper-ai-orchestrator": (APP_DIR, "Run-TradingOrchestrator.py"),
    "viper-ai-import-parqet": (APP_DIR, "Import-ParqetTransactions.py"),
    "viper-ai-import-transactions": (APP_DIR, "Import-TradingTransactions.py"),
    "viper-ai-refresh-etf": (APP_DIR, "Refresh-TradingEtfData.py"),
    "viper-ai-backfill-market-data": (APP_DIR, "Backfill-TradingMarketData.py"),
    "viper-ai-backfill-fx-rates": (APP_DIR, "Backfill-TradingFXRatesECB.py"),
    "viper-ai-backfill-events-news": (APP_DIR, "Backfill-TradingEventsNews.py"),
    "viper-ai-backfill-fundamentals-sec": (APP_DIR, "Backfill-TradingFundamentalsSEC.py"),
    "viper-ai-discover-candidates": (APP_DIR, "Discover-TradingCandidates.py"),
    "viper-ai-market-intelligence": (APP_DIR, "Collect-TradingMarketIntelligence.py"),
}


# Scripts without a command line of their own: they ignore every argument and start their job, so ``-h`` / ``--help`` is answered here and the
# script is not run (a call without help arguments is passed through unchanged).
NO_ARGPARSE: dict[str, str] = {
    "server.py": "MCP server over stdio. Configuration only through the environment: TRADING_DB_PATH (viper-ai-mcp: default <root>/data/trading.db of VIPER_TRADING_ROOT; the database must exist), TRADING_TOOL_PROFILE (read = default, 17 READ tools; gui = plus the 8 GUI actions; admin = all 27 incl. sql_execute), TRADING_EXCLUDE, TRADING_USER_ID, TRADING_MAX_RESULT_CHARS.",
    "Run-TradingTask.py": "Run-TradingTask.py <log> <script> [args...]: runs one script without a console window, appends stdout/stderr to <log>, returns the script's exit code.",
    "Backfill-TradingFundamentalsIR.py": "Fundamentals from the company IR reference parsers; takes no arguments and writes on every run.",
    "Discover-TradingSECIdentifiers.py": "SEC CIK identifier discovery; takes no arguments and writes on every run.",
    "Resolve-TradingSecurities.py": "Security master data resolution (OpenFIGI); takes no arguments and writes on every run.",
}
HELP_FLAGS = ("-h", "--help")


def run_script(script: Path, args: Sequence[str]) -> int:
    """Run ``script`` as ``python script args`` would; returns its exit code (a script that ends without ``SystemExit`` gives 0)."""
    script = Path(script).resolve()
    if not script.is_file():
        print(f"viper-ai: script not found: {script}", file=sys.stderr)
        return 2
    own_args = args[:1] if script.name == "Run-TradingTask.py" else args  # the launcher passes the arguments after <log> <script> to the script
    if script.name in NO_ARGPARSE and any(arg in HELP_FLAGS for arg in own_args):
        print(f"usage: {script.name}", "", NO_ARGPARSE[script.name], "", "This script has no options of its own; nothing was started.", sep="\n")
        return 0
    sys.argv = [str(script), *args]
    sys.path.insert(0, str(script.parent))  # the script's folder first, like "python script.py"
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def app_scripts() -> list[str]:
    """The runnable scripts of ``viper_ai.app`` (``Verb-Trading*.py``)."""
    return sorted(p.name for p in APP_DIR.glob("*-*.py"))


def _command(name: str) -> int:
    folder, script = COMMANDS[name]
    return run_script(folder / script, sys.argv[1:])


def _runtime_paths():
    """``viper_ai.app.runtime_paths`` loaded the way the scripts load it (the app folder is their import root); in a repository checkout ``tools/trading``."""
    folder = APP_DIR if (APP_DIR / "runtime_paths.py").is_file() else PACKAGE_DIR.parent / "tools" / "trading"
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    import runtime_paths

    return runtime_paths


def mcp_server() -> int:
    """The MCP server of this installation.  Without ``TRADING_DB_PATH`` it uses the database of the runtime root (``VIPER_TRADING_ROOT``, default
    ``C:\\tools\\trading``); an explicit ``TRADING_DB_PATH`` always wins.  A database that does not exist stops the start (the server would only answer errors)."""
    if any(arg in HELP_FLAGS for arg in sys.argv[1:]):
        return _command("viper-ai-mcp")
    try:
        db = os.environ.get("TRADING_DB_PATH", "").strip() or str(_runtime_paths().default_db_path())
    except ValueError as exc:  # a relative VIPER_TRADING_ROOT
        print(f"viper-ai-mcp: {exc}", file=sys.stderr)
        return 2
    if not Path(db).is_absolute() or not Path(db).is_file():
        print(f"viper-ai-mcp: database not found (TRADING_DB_PATH must name an existing absolute path): {db}", file=sys.stderr)
        return 2
    os.environ["TRADING_DB_PATH"] = db
    return _command("viper-ai-mcp")


def orchestrator() -> int:
    return _command("viper-ai-orchestrator")


def import_parqet() -> int:
    return _command("viper-ai-import-parqet")


def import_transactions() -> int:
    return _command("viper-ai-import-transactions")


def refresh_etf() -> int:
    return _command("viper-ai-refresh-etf")


def backfill_market_data() -> int:
    return _command("viper-ai-backfill-market-data")


def backfill_fx_rates() -> int:
    return _command("viper-ai-backfill-fx-rates")


def backfill_events_news() -> int:
    return _command("viper-ai-backfill-events-news")


def backfill_fundamentals_sec() -> int:
    return _command("viper-ai-backfill-fundamentals-sec")


def discover_candidates() -> int:
    return _command("viper-ai-discover-candidates")


def market_intelligence() -> int:
    return _command("viper-ai-market-intelligence")


def task() -> int:
    """``Run-TradingTask.py <log> <script> [args]`` for the Task Scheduler (installed as a GUI script: no console window).  A bare script name is taken from ``viper_ai.app``."""
    args = sys.argv[1:]
    if len(args) >= 2 and not Path(args[1]).is_file() and (APP_DIR / args[1]).is_file():
        args = [args[0], str(APP_DIR / args[1]), *args[2:]]
    return run_script(APP_DIR / "Run-TradingTask.py", args)


def gui() -> int:
    """The standard start (installed as a GUI script, no console window): the web front end in the browser; a running VIPER is reused.  The former
    Qt desktop GUI was replaced by it (M6), so the desktop shortcut keeps its target.  ``-h`` / ``--help`` only prints the usage."""
    from .web.__main__ import main as web_main

    return web_main(sys.argv[1:], prog="viper-ai-gui")


def web() -> int:
    """The local web front end on http://127.0.0.1:8765/ (needs the extra 'web'); ``-h`` / ``--help`` only prints the usage."""
    from .web.__main__ import main as web_main

    return web_main(sys.argv[1:])


def tasks_script() -> Path:
    """``Manage-TradingTasks.ps1`` of this installation (``viper_ai/scheduler``) or of the repository (``scheduler``)."""
    for candidate in (PACKAGE_DIR / "scheduler" / "Manage-TradingTasks.ps1", PACKAGE_DIR.parent / "scheduler" / "Manage-TradingTasks.ps1"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Manage-TradingTasks.ps1 not found")


def tasks_arguments(args: argparse.Namespace, root: Path) -> list[str]:
    """The ``Manage-TradingTasks.ps1`` arguments of one ``viper-ai tasks`` call (the script does the work, this only translates the options)."""
    out = ["-Action", args.tasks_action.capitalize(), "-Root", str(root)]
    if args.tasks_action == "deploy" and args.reset_schedule:
        out.append("-ResetSchedule")
    if args.tasks_action in ("edit", "delete") and args.task:
        out += ["-Task", args.task]
    if args.tasks_action == "edit":
        if args.at:
            out += ["-At", args.at]
        if args.day:
            out += ["-Day", args.day.capitalize()]
        out += ["-Enable"] if args.enable else ["-Disable"] if args.disable else []
    if args.tasks_action == "delete" and args.all:
        out.append("-All")
    if getattr(args, "dry_run", False):
        out.append("-DryRun")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    commands = "\n".join(f"  {name:<36} {folder.name}/{script}" for name, (folder, script) in COMMANDS.items())
    parser = argparse.ArgumentParser(
        prog="viper-ai",
        description="VIPER-AI: local MCP-based trading agent (analysis and simulation only, no order execution).",
        epilog=f"console commands:\n{commands}\n  {'viper-ai-task':<36} app/Run-TradingTask.py (no console window)\n  {'viper-ai-gui':<36} standard start: web front end in the browser (no console window)\n"
               f"  {'viper-ai-web':<36} web front end on 127.0.0.1 (extra 'web')\n\n"
               "Every command accepts -h / --help without starting a job. Runtime data (database, logs, scheduler, *_local.json) is never part of the package.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"VIPER-AI {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="list the console commands and the runnable scripts of the package")
    sub.add_parser("paths", help="show the package folders and the runtime folders the installation uses")
    run = sub.add_parser("run", help="run one script of the package with its own arguments, e.g. viper-ai run Audit-TradingDataQuality.py --help")
    run.add_argument("script", help="file name of the script in viper_ai.app (e.g. Manage-TradingCapitalState.py)")
    run.add_argument("args", nargs=argparse.REMAINDER, help="arguments passed to the script unchanged")
    tasks = sub.add_parser("tasks", help="manage the seven VIPER Windows scheduled tasks (Trading-*) of the trading root")
    tasks_sub = tasks.add_subparsers(dest="tasks_action", required=True)
    tasks_sub.add_parser("list", help="name, enabled, schedule, next run and action of each task")
    deploy = tasks_sub.add_parser("deploy", help="create missing tasks and update existing ones (action, settings, local DST-safe times); idempotent")
    deploy.add_argument("--reset-schedule", action="store_true", help="also set the default times again (otherwise an edited schedule stays)")
    edit = tasks_sub.add_parser("edit", help="change the schedule and/or enable or disable one task")
    edit.add_argument("task", help="task name, e.g. ETF-Refresh or Trading-ETF-Refresh")
    edit.add_argument("--at", help="time(s) in local time, e.g. 17:15 or 08:15,17:45")
    edit.add_argument("--day", choices=["daily", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
                      help="daily or one weekday (default: the task's current kind)")
    toggle = edit.add_mutually_exclusive_group()
    toggle.add_argument("--enable", action="store_true")
    toggle.add_argument("--disable", action="store_true")
    delete = tasks_sub.add_parser("delete", help="delete one VIPER task, or all seven with --all (never another task)")
    delete.add_argument("task", nargs="?", help="task name")
    delete.add_argument("--all", action="store_true", help="delete all seven VIPER tasks")
    for action_parser in (deploy, edit, delete):
        action_parser.add_argument("--dry-run", action="store_true", help="only show what would happen")
    args = parser.parse_args(argv)
    if args.command == "list":
        print("Console commands:")
        for name, (folder, script) in COMMANDS.items():
            print(f"  {name:<36} {folder.name}/{script}")
        print(f"  {'viper-ai-task':<36} app/Run-TradingTask.py (no console window)")
        print(f"  {'viper-ai-gui':<36} standard start: web front end in the browser (no console window)")
        print(f"  {'viper-ai-web':<36} web front end on 127.0.0.1 (extra 'web')")
        print("\nScripts for 'viper-ai run':")
        for name in app_scripts():
            print(f"  {name}")
        return 0
    if args.command == "paths":
        runtime_paths = _runtime_paths()
        try:
            root = runtime_paths.trading_root()
        except ValueError as exc:
            print(f"viper-ai: {exc}", file=sys.stderr)
            return 2
        db = runtime_paths.default_db_path()
        explicit = os.environ.get("TRADING_DB_PATH", "").strip()
        print(f"package:          {PACKAGE_DIR}")
        print(f"app scripts:      {APP_DIR}")
        print(f"mcp server:       {MCP_DIR / 'server.py'}")
        print(f"trading root:     {root}  (environment variable {runtime_paths.TRADING_ROOT_ENV})")
        print(f"database:         {db}  ({'exists' if db.is_file() else 'NOT FOUND'}; scripts: unless --db / --db-path is given)")
        if explicit:
            print(f"TRADING_DB_PATH:  {explicit}  (used by viper-ai-mcp instead of the database above)")
        print(f"data:             {runtime_paths.data_dir()}")
        print(f"logs:             {runtime_paths.logs_dir()}")
        print(f"private universe: {runtime_paths.private_universe_dir()}")
        print(f"private data:     {runtime_paths.private_data_dir()}")
        return 0
    if args.command == "tasks":
        if args.tasks_action == "delete" and not (args.task or args.all):
            print("viper-ai: tasks delete needs a task name or --all", file=sys.stderr)
            return 2
        try:
            root = _runtime_paths().trading_root()
        except ValueError as exc:
            print(f"viper-ai: {exc}", file=sys.stderr)
            return 2
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tasks_script()), *tasks_arguments(args, root)]
        return subprocess.run(command).returncode
    if args.command == "run":
        name = Path(args.script).name
        if name not in app_scripts():
            print(f"viper-ai: unknown script {args.script!r}; see 'viper-ai list'", file=sys.stderr)
            return 2
        return run_script(APP_DIR / name, args.args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

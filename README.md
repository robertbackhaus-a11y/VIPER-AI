# VIPER-AI

![VIPER-AI, local trading assistant](web/static/logo.png)

Version: v0.6.0 — see [CHANGELOG.md](CHANGELOG.md).

VIPER-AI is a local, single-user trading platform for Windows and Linux. It keeps a portfolio, its transactions, market and fundamental data in one
SQLite database, evaluates positions and watchlist candidates deterministically, shows everything in a local web front end and offers the
results through an MCP server to any MCP-capable LLM client. Its own reasoning workflow lets a local language model weigh theses and
counter-theses on top of the engine's figures. Python does every calculation, every decision and every write; the model only interprets.
VIPER-AI analyses and simulates only: **it never places an order.**

## Functions

- **Web front end** (`viper-ai-start` / `viper-ai-web`, in the browser on `http://127.0.0.1:8765/`): dashboard with market value, cost basis, results,
  portfolio history and allocation, portfolio and position detail with price charts, transactions, planner, watchlist with its actions,
  sectors and single-name exposure, Intelligence, task management, transaction import, chat with a local Ollama model, settings.
- **Data collection:** market data, ECB FX rates, SEC events and fundamentals, news and ETF holdings, kept current by seven scheduled
  tasks. VIPER holds their definitions and deploys them to the Windows Task Scheduler or to systemd user timers (`viper-ai tasks`).
- **Read-only analysis:** SELL / TRIM / HOLD / ADD for held positions, watchlist candidate status, strategy suggestion, Swing promotion,
  the portfolio action planner, candidate discovery, market intelligence and the opportunity view.
- **Reasoning:** per security observe, data check, quantitative and qualitative analysis, thesis and counter-thesis with cited evidence,
  risk context and a reviewed proposal, stored with its protocol and re-evaluated when prices, news or the position change (LangGraph).
- **Transaction import:** canonical CSV or Parqet export, always checked as a dry run first.
- **MCP server:** the server decides which tools a client sees; by default only the read-only tools.

## Release and development state

- **Latest release: v0.6.0** (package version `0.6.0`). Since 24.10.2026 production runs the web version (commit `045f5f2`, package
  version still `0.6.0`) with the integrated task scheduler of M3a, which is removed again on `main`. The next production update switches
  over as described in [web.en.md, section 6](docs/web.en.md#6-start-settings-and-production) (`viper-ai tasks deploy` enables the seven
  tasks again; autostart and desktop shortcut start `viper-ai-start.exe` instead of `viper-ai-gui.exe`).
- The repository history was started fresh on 24.10.2026 with the web version; the tags and releases up to v0.6.0 are no longer on
  GitHub. The v0.6.0 wheel is kept locally as the way back.
- Changes after the release are listed under `[Unreleased]` in the [changelog](CHANGELOG.md); they become part of a release only with the
  next tag. Install release tags only, never the moving `main` branch.

## Architecture and roadmap

The binding plan is [ROADMAP.md](ROADMAP.md), the architecture and the current state of the code are in [ARCHITECTURE.md](ARCHITECTURE.md);
both are written in German. State of the development branch:

- Done: M0 inventory; M1 web API over the unchanged engine; M2 portfolio dashboard; M3 every desktop GUI workflow in the browser; M4 the
  reasoning workflow on LangGraph; M5 the Intelligence page; M3a the integrated task scheduler (discarded: VIPER holds the task definitions
  and deploys them to the operating system's scheduler, Windows or Linux); M3b the portfolio history with periods up to MAX and a
  time-weighted return; M3c the time zone of the schedules.
- Done: M6, the cutover in the code: the web front end is the only user interface; Qt, PySide and the former `gui/` package are removed
  completely (the view models and services the web uses are in `viper_ai/shared`).
- Production switched on 24.10.2026: web version installed, both additive migrations run, VIPER starts at logon, all seven jobs taken
  over by the integrated scheduler (the Windows tasks are disabled, not deleted). Until the next update production keeps running them that way.

## Installation

Requirements: Windows, Python 3.12 or newer. Production uses one environment under `C:\tools\Viper-AI\.venv`.

```powershell
py -3.12 -m venv C:\tools\Viper-AI\.venv
C:\tools\Viper-AI\.venv\Scripts\python.exe -m pip install "VIPER-AI[web] @ git+https://github.com/robertbackhaus-a11y/VIPER-AI.git@v<version>"
```

The extra `web` brings the web front end and the reasoning workflow (`fastapi`, `langgraph`, `langgraph-checkpoint-sqlite`); there is no
`[gui]` extra any more. On Linux create the environment with `python3 -m venv` and use `bin/` instead of `Scripts\`. A wheel
on the [release page](https://github.com/robertbackhaus-a11y/VIPER-AI/releases), once a release is published, works the same way. Do not move the environment after it is
created: the command starters contain its path.

## Update

```powershell
C:\tools\Viper-AI\.venv\Scripts\python.exe -m pip install --upgrade "VIPER-AI[web] @ git+https://github.com/robertbackhaus-a11y/VIPER-AI.git@v<new version>"
```

Then restart VIPER (close `viper-ai-web` / the server started by `viper-ai-start` and start it again), restart running MCP processes and run
`viper-ai tasks deploy`, so the scheduled tasks match the installation.

## Runtime data

The database, logs, scheduler data, backups and the private `*_local.json` files are not part of the package. `pip install`, `--upgrade`
and `uninstall` never change them. They live under the runtime root `C:\tools\Viper-AI` on Windows and `~/.local/share/viper-ai` on Linux (another one through the
environment variable `VIPER_TRADING_ROOT`);
the database is `<root>\data\trading.db`. The default comes from one place (`tools/trading/runtime_paths.py`), so the web front end,
the MCP server, the scheduled tasks and the scripts use the same root and database; an explicit `--db`, `TRADING_DB_PATH` or a database
saved in the settings wins. `viper-ai paths` shows the folders in use.

## Entry points

| Command | Purpose |
|---|---|
| `viper-ai` | `--version`, `list` (all commands), `paths`, `run <Script.py> [args]` |
| `viper-ai-start` / `viper-ai start` | standard start: VIPER in the browser, no console window; a running VIPER is reused |
| `viper-ai-web` | the same with a console (`--port`, `--db`, `--no-browser`) |
| `viper-ai-mcp` | MCP server over stdio |
| `viper-ai-orchestrator` | portfolio-wide read-only report |
| `viper-ai-import-transactions` / `viper-ai-import-parqet` | transaction import (dry run by default, `--write` imports) |
| `viper-ai-refresh-etf` | ETF holdings and sector data (dry run by default) |
| `viper-ai tasks list` / `status` / `deploy` / `edit <task>` / `delete <task>` / `restore <task>` / `run <task>` | the seven VIPER tasks: VIPER's definitions, deployed to the Windows Task Scheduler or systemd user timers ([web.en.md, section 7](docs/web.en.md#7-tasks-scheduled-tasks)) |
| `viper-ai-task` | task launcher without a console window |

`viper-ai list` lists the others. `-h` / `--help` of a command only prints its help and never starts a job.

## Start

```powershell
viper-ai --version
viper-ai paths
viper-ai tasks deploy
viper-ai start
```

The scheduled tasks run through the operating system's scheduler; VIPER itself never starts a job on its own. `viper-ai-start --no-browser`
in the Windows startup folder starts the user interface at logon.

![Dashboard with fictional demo data](docs/images/web-dashboard.png)

More views (fictional demo data): [web.en.md, section 8](docs/web.en.md#8-views).
An MCP client starts the server like this; the profile `read` offers the read-only tools only (profiles:
[SECURITY.md](SECURITY.md#mcp-tool-profiles)):

```json
"trading": { "command": "C:\\tools\\Viper-AI\\.venv\\Scripts\\viper-ai-mcp.exe", "args": [],
             "env": { "VIPER_TRADING_ROOT": "C:\\tools\\Viper-AI", "TRADING_TOOL_PROFILE": "read" } }
```

## Documentation

The technical documentation exists in German and English with the same content.

| Topic | English | Deutsch |
|---|---|---|
| Web front end (pages, API, start, calculation basis) | [web.en.md](docs/web.en.md) | [web.de.md](docs/web.de.md) |
| Architecture reference (modules, values, MCP tools, paths, installation, production setup) | [trading-agent-architecture.en.md](docs/trading-agent-architecture.en.md) | [trading-agent-architecture.de.md](docs/trading-agent-architecture.de.md) |
| Queries and examples for the MCP tools | [trading-agent-query-examples.en.md](docs/trading-agent-query-examples.en.md) | [trading-agent-query-examples.de.md](docs/trading-agent-query-examples.de.md) |
| Transaction import (canonical CSV, Parqet) | [trading-import.en.md](docs/trading-import.en.md) | [trading-import.de.md](docs/trading-import.de.md) |
| Swing entry recommendations | [trading-entry-recommendations.en.md](docs/trading-entry-recommendations.en.md) | [trading-entry-recommendations.de.md](docs/trading-entry-recommendations.de.md) |
| Watchlist candidate decision status | [trading-candidate-decision.en.md](docs/trading-candidate-decision.en.md) | [trading-candidate-decision.de.md](docs/trading-candidate-decision.de.md) |
| Watchlist strategy suggestion | [trading-strategy-suggestion.en.md](docs/trading-strategy-suggestion.en.md) | [trading-strategy-suggestion.de.md](docs/trading-strategy-suggestion.de.md) |
| Watchlist-to-Swing promotion | [trading-swing-promotion.en.md](docs/trading-swing-promotion.en.md) | [trading-swing-promotion.de.md](docs/trading-swing-promotion.de.md) |

Configuration and runtime paths: architecture reference, section 6. Production setup, scheduled tasks, the former installation and the
rollback: [section 6.1](docs/trading-agent-architecture.en.md#61-installation-and-update-with-pip). Development rules:
[CONTRIBUTING.md](CONTRIBUTING.md). Security: [SECURITY.md](SECURITY.md).

## License

Apache License 2.0, see [LICENSE](LICENSE).

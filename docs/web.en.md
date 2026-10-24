# Web front end

Deutsche Version: [web.de.md](web.de.md)

The web front end is the interface of VIPER-AI in the local browser. It replaced the former Qt desktop GUI (M6); the plan and its state are in the roadmap of the repository. It holds no trading logic: every figure comes from the existing engine.

## 1. Architecture

```
Browser (http://127.0.0.1:8765/)
  |-- static page (HTML, CSS, JavaScript)
  `-- /api ----- FastAPI (web/app.py)
                   |-- facade trading_sqlite.Tools in its own process (READ tools + GUI actions)
                   `-- engine module portfolio_history (read-only)  ----- SQLite
```

- The tools are the same methods of the class `Tools` that the MCP server offers; they run in the process of the web server, without an MCP child process.
- Allowed is exactly what the former desktop GUI was allowed to do: the READ tools and the actions of `GUI_ACTIONS` in `tool_policy.py`. `sql_execute`, `approve_swing_promotion` and every unknown name are refused (HTTP 403, `TOOL_NOT_ALLOWED`).
- Values that no tool provides (holdings with cost basis, transactions, daily bars, portfolio history) come from `portfolio_history.py` over a read-only connection (`mode=ro`, `query_only`).
- The page uses Apache ECharts 5.6.0 (dashboard charts) and TradingView Lightweight Charts 4.2.3 (price charts), both shipped under `web/static/vendor`; it loads nothing from the internet.
- The server listens on 127.0.0.1 only. A write (`POST`) carrying the `Origin` of another site is refused (HTTP 403, `FOREIGN_ORIGIN`).

## 2. Installation and start

The web front end needs the extra `web` (package `fastapi`; `uvicorn` comes with `mcp`):

```
<venv>\Scripts\python.exe -m pip install "VIPER-AI[web] @ <source>"
viper-ai-web
viper-ai-web --port 8766 --no-browser
```

- `viper-ai-web` starts the integrated task scheduler with it (enabled jobs run only while VIPER runs); `--no-scheduler` leaves it off.
- Without `--db` the `trading_db_path` of the settings file (section 6) applies, otherwise `<root>\data\trading.db` of `VIPER_TRADING_ROOT`.
- In the repository `python -m web` starts the same.

## 3. API

| Path | Content | Source |
|---|---|---|
| `GET /api/status` | version, database status, path, hints | `database_status` |
| `GET /api/portfolio` | open positions with market value, cost basis, unrealised result, daily change; totals | `portfolio_history.holdings` |
| `GET /api/portfolio/history` | daily values (invested capital, market value, net flow, dividends in EUR), figures per period, missing prices, ECB stand | `portfolio_history.portfolio_history` |
| `GET /api/transactions` | stored transactions, newest first (`security_id`, `limit`) | `portfolio_history.transactions` |
| `GET /api/securities/{id}/prices` | daily bars of one security in its trading currency | `portfolio_history.price_history` |
| `GET /api/views/dashboard` | planner and engine block of the desktop dashboard (the same texts and tables) | `run_trading_orchestrator`, `database_status` |
| `GET /api/views/planner`, `/api/views/watchlist`, `/api/views/sectors`, `/api/views/tasks` | tables and texts of the desktop pages planner, watchlist, sectors (with single-name exposure) and tasks | view models of `gui/viewmodels` |
| `POST /api/watchlist/add`, `/api/watchlist/{id}/remove`, `/api/watchlist/{id}/edit`, `/api/onboarding` | watchlist actions and adding a new security (check, then create) | `add_watchlist_candidate`, `remove_watchlist_candidate`, `set_security_strategy`, `set_watchlist_priority`, `set_security_sector`, `onboard_security` |
| `POST /api/tasks/{name}/run`, `GET /api/tasks/{name}/log` | start a scheduled task now, read the end of its log | Windows Task Scheduler |
| `POST /api/import/upload`, `/api/import/check`, `/api/import/run` | store an export file, check it, import with the token of that check | `check_transaction_import`, `import_transactions` |
| `POST /api/chat` | question to the local model, answer as Server-Sent Events; `/api/chat/stop`, `/api/chat/reset` | Ollama, READ tools |
| `GET /api/diagnostics`, `GET` and `POST /api/settings` | paths, status, tool classification; settings | settings file |
| `GET /api/intelligence`, `GET /api/intelligence/cases/{id}`, `GET /api/intelligence/securities` | current theses with reasons for a re-evaluation, analysis cases with their step protocol, securities to choose | `analysis_store` |
| `POST /api/intelligence/analyze`, `POST /api/intelligence/cases/{id}/resume` | start an analysis (`security_id`, `question`, `supersedes_id`), continue an interrupted analysis | `viper_reasoning`, Ollama |
| `GET /api/scheduler`, `POST /api/scheduler/jobs/{name}`, `.../run`, `.../takeover`, `GET .../runs` | task scheduler: overview, enable and change the schedule, run now, takeover of the Windows task, runs | `job_store`, APScheduler |
| `GET /api/orchestrator` | the complete orchestrator result (cached for 60 seconds, `refresh=true` computes it again) | `run_trading_orchestrator` |
| `POST /api/read/{tool}` | any READ tool with its arguments as JSON | READ tools |
| `POST /api/action/{tool}` | one GUI action with its arguments as JSON | `GUI_ACTIONS` |

The OpenAPI description is at `/api/docs`.

## 4. Calculation basis

- Market value of a position: the value of `build_portfolio_context` (shares x latest snapshot price x ECB rate). If one valuation is missing, there is no total.
- Cost basis: `remaining_cost_basis` of the table `positions` (average-cost model of the import); unrealised result = market value - cost basis.
- Daily change: `close / previous_close - 1` of the two latest daily bars (trading currency, without currency effect), as on the sector page.
- Realised result: sum of `realized_gain` of `positions`; dividends: DIVIDEND amounts minus taxes and fees.
- Portfolio history: the transactions are replayed day by day with the same cost model (`apply_cost_basis`) and the same split factors. The market value of a day is shares x close x ECB rate of that day; if a held position has no price or no ECB rate (older than 5 days) on a day, the day has no market value (`missing` names the symbols). The invested capital is always known, because the amounts are stored in EUR.
- Performance per period (1M, 3M, 6M, 1J, 3J, 5J, MAX): the net flow of a day = buys (amount + fees + taxes) minus sells (amount - fees - taxes); dividends are income. Daily return = (market value - net flow + dividends) / market value of the day before - 1, chained to the time-weighted return; a day without a market value in the period makes the return "not computable". MAX starts with the first fully valued day. Market value and return are separate figures.

## 5. Pages

| Page | Content |
|---|---|
| Dashboard | market value with daily change, cost basis, unrealised and stored realised result, dividends, cash; portfolio and capital history with periods (1M, 3M, 6M, 1J, 3J, 5J, MAX) and the figures of the period; allocation by position, strategy and sector; top/flop today and by result; positions; planner and hints of the engine |
| Portfolio | every open position with price, market value, cost basis, result, weight, daily change and valuation status; sortable |
| Position | price history as a candlestick chart with buys and sells, figures of the position, transactions of the security |
| Transactions | every stored transaction with a filter by symbol, name and type |
| Planner / Aktionen | engine actions per position, planned and deferred entries, entry candidates with sector and correlation risk |
| Watchlist | table with filters and sorting; strategy, priority and sector are chosen in the table, the apply button saves (strategy, then priority, then sector; a failure stops the sequence); add with a check, remove after a confirmation; "Neues Wertpapier aufnehmen" (check, create only after a clean check of the unchanged form) |
| Sektoren | treemap and tables of the sectors, ETF look-through; tab "Einzelwertrisiko" with its own treemap, company and ETF table |
| Aufgaben | integrated task scheduler: enable, schedule (for example "So 10:00" for Sunday 10:00, several separated by ";"), run now, last run, last success, error details, state of the Windows task, takeover after a successful run |
| Tasks | the seven scheduled tasks with data stand, run now after a confirmation (the existing scheduler task, followed until its end), end of the log, ETF data stand |
| Import | choose the file, check (writes nothing), decide open points (every decision needs a new check), import with the token of the check after a confirmation |
| Chat | questions to the local model with the READ tools of the MCP profile `read`; answer, thinking, tool calls and errors kept apart; stop, new chat |
| Intelligence | start an analysis; active theses with proposal, engine action, confidence, data stand, validity and reasons for a re-evaluation; analyses and processes (continue after an interruption); latest events |
| Analyse | one analysis case: proposal next to the engine action (no order), thesis and counter-thesis with evidence, assumptions, uncertainty, data gaps, risk and portfolio context, evidence catalogue, actual transactions since the analysis, step protocol |
| Einstellungen | diagnostics (paths, database, Ollama, tool classification) and the settings the web uses |

- Data stand, currency and calculation basis are named in the head of every page; the tooltip "Berechnungsbasis" gives the rules of section 4.
- The periods and their figures are computed by the engine (section 4); the page only cuts the daily values for the chart and shows gaps as a break in the line.
- Every page calls the same tool with the same arguments and the same view model as the former desktop GUI; wording, reason code texts, checks and the order of the write steps are the same.
- An import file is stored in `%LOCALAPPDATA%\TradingAgentGui\web-import` (the tools only read a local path); it is never part of the package or of the runtime root.
- The chat builds the MCP server in the profile `read` in its own process (without starting it) and gives the model its tools: the same names, descriptions, schemas, compact answers and the same limit of 40,000 characters as any MCP client.
- "Neu berechnen" makes the orchestrator compute again at once instead of showing the result of up to 60 seconds ago.

## 6. Start, settings and production

- `viper-ai-gui` is the standard start (no console window): it starts the server if it does not run yet and opens the browser; if VIPER already runs, it only opens the browser. The desktop shortcut that used to start `viper-ai-gui.exe` therefore keeps its target. `viper-ai-web` does the same with a console.
- Without a console the server writes its output to `%LOCALAPPDATA%\TradingAgentGui\web.log`. The settings stay in `%LOCALAPPDATA%\TradingAgentGui\settings.json` (Ollama URL and model, database, log folder of the tasks); the settings page changes them.
- The integrated task scheduler runs only while VIPER runs. So `viper-ai-gui --no-browser` belongs in the autostart at logon (a shortcut in the folder `shell:startup`); it replaces the logon triggers of the Windows tasks.

Switching production over (each step can be undone, runtime data stays untouched):

1. Install the new version with the extra `web` (`[gui]` is an alias and installs the same).
2. `viper-ai run Migrate-TradingAnalysisCases.py --write` and `viper-ai run Migrate-TradingScheduler.py --write` (additive tables; check without `--write` first).
3. Set up the autostart as above and start VIPER.
4. On the page Aufgaben, per job run it now and, after the successful run, use the takeover button: the job then runs in VIPER, the Windows task is disabled, not deleted.
5. Way back: install the locally kept v0.6.0 wheel again (`pip install "<path>\viper_ai-0.6.0-py3-none-any.whl[gui]"`; the tag is no longer on GitHub) and enable the Windows tasks again with `viper-ai tasks edit <task> --enable`.

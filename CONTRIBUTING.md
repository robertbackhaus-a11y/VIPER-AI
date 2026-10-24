# Contributing

This repository holds the VIPER-AI code (`tools/trading/`, `mcp-tools/`, `web/`, the
pip packaging `pyproject.toml` / `viper_ai/` with `viper_ai/shared` and `viper_ai/tasks`, `docs/`). It does not hold the trading database, backups, audit/candidate JSONs, PDFs,
logs, or any other production data — see `.gitignore`.

## Before you start

For anything beyond a small, self-contained fix, open an issue first and agree on the
approach before writing code. This avoids wasted work on changes that conflict with the
existing architecture or module boundaries.

## Workflow

- Fork the repository, work on a feature branch, open a pull request.
- No direct pushes to `main` from outside the maintainer.
- Keep changes small and reviewable. One logical change per pull request.

## Architecture and code conventions

- Respect the existing architecture and module boundaries — see
  [docs/trading-agent-architecture.en.md](docs/trading-agent-architecture.en.md) for the
  current component flow and module inventory before adding a new one.
- Reuse existing modules/functions instead of duplicating logic. If you need something
  a module already provides (analytics scoring, portfolio valuation, FX resolution,
  strategy/campaign state, etc.), import and call it — don't recompute it.
- Read-only components stay read-only. If a module or function is documented as
  read-only/no-write (e.g. `decision_engine.py`, `candidate_decision.py`,
  `strategy_suggestion.py`, `analysis_engine.py`), a change must not give it a database
  write path.
- No fabricated financial data and no hidden fallbacks. Missing or unverifiable data
  must surface as an explicit unavailable/insufficient/unknown status — never a guessed
  or interpolated value presented as real.
- Handle external data sources traceably: name the source, note format/version
  assumptions, and keep parsing/verification logic auditable (see the
  `verified_direct`/`verified_derived`/`verified_secondary`/`not_verified` pattern in
  `Research-TradingFundamentals.py` for the established convention).

## Documentation (mandatory)

A task is only done when its effect on the README and the relevant documentation has been checked and every needed change is made.
Checking the documentation is part of the same task and the same commit, not a follow-up.

Update the documentation when a change touches:

- installation, update, deployment or rollback (`pyproject.toml`, `viper_ai/`, extras, `deploy/`);
- CLI commands and start parameters, including entry points (`[project.scripts]` / `[project.gui-scripts]`);
- environment variables and runtime paths (`runtime_paths.py`, `VIPER_TRADING_ROOT`, `TRADING_DB_PATH`, ...);
- MCP tools, tool profiles and permissions (tool names, parameters, result fields, `TRADING_TOOL_PROFILE`, READ/ACTION);
- web front end pages and workflows;
- the scheduled tasks: their definitions (`viper_ai/tasks/definitions.py`) and the Windows / Linux deployment backends (`viper_ai/tasks/`);
- the database schema or relevant data formats (`*_local.json`, reports, status and reason codes);
- trading decision rules and business limits (thresholds, guardrails, constants: with their exact value, as in the constants tables of
  `docs/trading-agent-architecture.*.md`);
- security behaviour and compatibility, including breaking changes and migrations.

Where it goes (each fact in one place, the others link to it):

| Document | Holds |
|---|---|
| `README.md` | what VIPER-AI is, functions, installation, start and links |
| `ROADMAP.md` | the binding milestones and their state (German) |
| `ARCHITECTURE.md` | the overall architecture, the current state of the code and the target (German) |
| `CHANGELOG.md` | the changes of each version (under `[Unreleased]` until the release) |
| `SECURITY.md` | security behaviour and security-relevant configuration |
| `CONTRIBUTING.md` | the binding development rules |
| `docs/*.de.md` / `docs/*.en.md` | technical details and operating instructions |

German and English documents must say the same; change both together.

Definition of done, before a task is reported as finished:

1. Identify the affected documents.
2. Check their statements against the actual implementation.
3. Make the needed changes in the same task.
4. Remove or correct outdated information.
5. Name the updated documents in the final report (pull request or commit description); if none needed a change, say why in one sentence.

No change is forced where nothing is affected.

## Verification

Work feature by feature: requirement → implementation → targeted checks for the affected
functions and paths → acceptance → next feature. Do not run the complete test suite for
every development task and do not use a test count as a quality goal.

Keep a new test only when it has lasting value for that concrete function. Do not add
tests for trivial text, wrappers, or implementation details. After acceptance, the
feature block is complete.

Run a complete test suite only before a release or milestone, after a larger shared
architecture change, or when explicitly requested. Record the checks actually run in
the pull request description.

## Database / schema changes

- Mark any pull request that adds or changes a table, column, constraint, or
  feature-version marker explicitly in its title and description ("DB/schema change:
  ...").
- Follow the existing additive-migration pattern (`Migrate-Trading*.py`): dry-run by
  default, `--write` to apply, a dedicated `metadata` feature-version key, never a
  destructive rewrite of existing rows.

## Breaking changes

Mark any pull request that changes an existing function's signature/return shape, a
CLI flag, a status vocabulary, or a documented constant's value explicitly in its title
and description ("BREAKING: ..."), and say what downstream code (MCP tool `mcp-tools/trading_sqlite.py`,
other modules, scripts) is affected.

## What never gets committed

- Secrets, API keys, tokens, credentials, or personal data.
- Production trading data: the production database file, backups, caches, audit/report
  output, or any other file under the paths listed in `.gitignore`.

If you're unsure whether a file belongs in the repository, don't add it — ask first.

## Security checks

- CodeQL, Dependency Review, Dependabot and GitHub secret scanning (with push protection) run
  automatically; see [SECURITY.md](SECURITY.md). Do not silence or work around a finding —
  fix it or explain it in the pull request.
- Never commit secrets, not even test ones that look real. Use obviously fake values.
- Dependencies and GitHub Actions versions are kept current by Dependabot pull requests.
  GitHub Actions are pinned to a full commit SHA with the version in a trailing comment;
  workflows use the minimal `permissions:` they need and never `pull_request_target`.

## Commit messages

- Write clear, descriptive commit messages that explain the change.
- No automatic `Co-Authored-By` lines.
- No tool/AI attribution of any kind in commit messages.
- No automatic contributor lines.

## Pull requests

Describe, in the PR description:

- The purpose of the change and what it does.
- The targeted checks you ran, and why a complete suite ran if it did.
- The documentation files you updated (or why none needed a change).
- Whether it includes a DB/schema change (see above).
- Whether it includes a breaking change (see above).

# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""The regular fundamentals and valuation run (scheduled task ``Fundamentals``): SEC/XBRL import, the IR parsers, gap analysis, research jobs for the
remaining gaps (answers are taken over automatically), valuation refresh and the gap status of Intelligence.  See ``fundamentals_completion.py``.

Dry-run by default: only the gap analysis of the stored data is printed, nothing is fetched or written.  ``--write`` runs everything.  Without the
relations of ``Migrate-TradingFundamentalsCompletion.py`` the run still imports and refreshes the valuation, but keeps no research jobs and no gap status.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
from pathlib import Path

import fundamentals_completion
import runtime_paths  # sibling module: the runtime root (VIPER_TRADING_ROOT) and its default paths

HERE = Path(__file__).resolve().parent


def load(name: str, file_name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def steps(conn: sqlite3.Connection, use_cache: bool) -> fundamentals_completion.Steps:
    sec = load("fundamentals_sec_backfill", "Backfill-TradingFundamentalsSEC.py")
    ir = load("fundamentals_ir_backfill", "Backfill-TradingFundamentalsIR.py")
    refresh = load("valuation_refresh", "Refresh-TradingValuationMetrics.py")
    research = load("fundamentals_research", "Research-TradingFundamentals.py")
    sec_mapped = {r["id"] for r in sec.get_relevant_sec_securities(conn, sec.get_sec_source_id(conn))}

    def refresh_valuation(ids: list[int]) -> None:
        if refresh.valuation_metrics.table_exists(conn):
            refresh.refresh(conn, ids, None, True)
        else:
            print("valuation_metrics is missing (Migrate-TradingValuationMetrics.py --write): valuation not stored")

    return fundamentals_completion.Steps(
        sec=lambda ids: sec.run(conn, ids, use_cache) if ids else {},
        sec_mapped=sec_mapped,
        ir=lambda ids: ir.run(conn, ids) if ids else {},
        ir_parsers={security_id for security_id, _name, _parser in ir.PARSERS},
        refresh_valuation=refresh_valuation,
        research=research,
    )


def print_report(conn: sqlite3.Connection, report: fundamentals_completion.Report) -> None:
    symbols = {r[0]: r[1] or str(r[0]) for r in conn.execute("SELECT id, symbol FROM security")}
    n = len(report.security_ids)
    print()
    print("===============================================")
    print(" Fundamentals / valuation completion")
    print("===============================================")
    print(f"Securities in scope              : {n}")
    print(f"Market cap stored before the run : {sum(report.stored_before.values())}/{n}")
    print(f"Market cap calculated before     : {report.coverage_before}/{n}")
    print(f"Market cap calculated after      : {report.coverage_after}/{n}")
    if report.sec or report.ir:
        print(f"SEC import                       : " + ", ".join(f"{s}={sum(1 for r in report.sec.values() if r['status'] == s)}" for s in ("ok", "skipped", "failed")))
        print(f"IR parsers                       : " + ", ".join(f"{symbols.get(i)}={r['status']}" for i, r in report.ir.items()))
        print(f"Research values written back     : {report.refilled}")
    for action in ("created", "updated", "closed_obsolete"):
        items = [f"{symbols.get(i)}#{a['job_id']}" for i, a in report.jobs.items() if a["action"] == action]
        if items:
            print(f"Research jobs {action:<19}: {', '.join(items)}")
    for security_id, a in report.answers.items():
        print(f"Research answer {symbols.get(security_id)} #{a['job_id']}: {a['status']} ({a['detail']})")
    if report.counts:
        for key in ("closed_by_source", "closed_by_research", "research", "unavailable"):
            items = report.counts.get(key, [])
            print(f"Gaps {key:<28}: {len(items)}" + (f"  {', '.join(f'{symbols.get(i)}:{g}' for i, g in items)}" if items else ""))
    print()
    print("Gaps after the run:")
    for security_id in report.security_ids:
        for code, gap in report.after.get(security_id, {}).items():
            print(f"  {symbols.get(security_id):<8} {code:<20} {', '.join(gap.get('periods') or [])} | {gap['detail'][:140]}")
    if not report.persisted:
        print()
        print("Gap status and research jobs not stored" + ("" if not report.sec and not report.ir else
              " (run Migrate-TradingFundamentalsCompletion.py --write)") + ".")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fundamentals + valuation run with automatic gap completion (dry-run by default)")
    parser.add_argument("--db-path", "--db", dest="db_path", type=Path, default=runtime_paths.default_db_path())
    parser.add_argument("--security-id", type=int, action="append", dest="security_ids", help="only this security_id (repeatable)")
    parser.add_argument("--no-cache", action="store_true", help="download the SEC CompanyFacts again instead of the local cache")
    parser.add_argument("--json", type=Path, help="also write the report as JSON to this file")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not args.db_path.is_file():
        raise FileNotFoundError(f"Trading DB not found: {args.db_path}")
    if args.write:
        conn = sqlite3.connect(str(args.db_path), timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
    else:
        conn = sqlite3.connect(f"file:///{args.db_path.as_posix()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        config = fundamentals_completion.ResearchConfig.from_env(args.db_path.parent)
        run_steps = steps(conn, not args.no_cache) if args.write else None
        report = fundamentals_completion.run(conn, run_steps, config, write=args.write, security_ids=args.security_ids)
        print_report(conn, report)
        if args.json:
            args.json.write_text(json.dumps(report.__dict__, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Automatic completion of the fundamentals the valuation needs: gap analysis, source cascade, research jobs and the gap status of Intelligence.

One regular run (``Refresh-TradingFundamentals.py --write``, the scheduled Fundamentals task) does, for the active stocks with a position or a watchlist
entry (the SEC scope):

1. gap analysis before the run: every valuation input a security lacks, from ``valuation_metrics.calculate`` (``ValuationRow.gaps``): the field
   (``shares_outstanding``, ``revenue``, ``net_income``, ``total_equity``, ``cash``, ``total_debt``, ``ebitda``, ``operating_cash_flow``,
   ``free_cash_flow`` = capex/FCF), a needed ``period``, ``fx``, ``adr_ratio`` or ``implausible`` data, with the periods and the reason;
2. sources in a fixed order: SEC/XBRL (``Backfill-TradingFundamentalsSEC.run``), the existing IR parsers (``Backfill-TradingFundamentalsIR.run``), then
   the research path (``Research-TradingFundamentals.py``).  Research values recorded by earlier jobs are written back when an SEC re-import replaced
   their row;
3. a research job per security with gaps left that research can close (not FX, not a depositary ratio): the fields, periods, known values, allowed
   sources and the JSON format, as a prompt and a request file under ``<data>/fundamentals-research/jobs``; the answer is expected as
   ``<data>/fundamentals-research/answers/<job id>.json``.  A job is not repeated: an open job is updated in place, and a job that ended without the
   value is not asked again for ``RESEARCH_RETRY_DAYS``.  With ``VIPER_FUNDAMENTALS_RESEARCH_COMMAND`` set, the command (tokens ``{prompt}``,
   ``{answer}``, ``{job_id}``, ``{security_id}``, ``{symbol}``) is run for each open job without an answer, so the answer can come in the same run;
4. an answer is validated (``Research-TradingFundamentals.validate_response``) and taken over with ``apply_answer``: only empty fields are filled and
   missing periods added, a stored value is never changed; field, value, document, URL and retrieval time are kept in the job's ``result_json``;
5. valuation refresh (``Refresh-TradingValuationMetrics.refresh``) and the gap analysis again; each gap is stored in ``fundamentals_gap`` as
   ``research`` (job open), ``closed`` (with the step that closed it) or ``unavailable`` (with the reason), with the trail of every step.

Job status: ``open`` / ``done`` (a value was taken over) / ``not_available`` (the answer had none) / ``failed`` (invalid answer).  Nothing in this module
names a security: every rule is general.  No value is estimated.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import valuation_metrics

GAP_FIELDS = ("shares_outstanding", "revenue", "net_income", "total_equity", "cash", "total_debt", "ebitda", "operating_cash_flow", "free_cash_flow")
GAPS = GAP_FIELDS + ("period", "fx", "adr_ratio", "implausible")
# Gaps research cannot close: the reason is fixed by the gap.
NO_RESEARCH = {
    "fx": "an FX rate comes from the ECB reference rates (FX task), not from research",
    "adr_ratio": "a depositary ratio is a reviewed entry of universe/adr_ratios_v1.json, not taken over automatically",
}
# Fields a research job asks for a missing period (and for an implausible share count: the count).
PERIOD_FIELDS = ("revenue", "net_income", "eps_basic", "eps_diluted", "ebitda", "operating_cash_flow", "capex", "free_cash_flow", "cash", "total_debt",
                 "total_equity", "shares_outstanding")
RESEARCH_RETRY_DAYS = 30
RESEARCH_COMMAND_ENV = "VIPER_FUNDAMENTALS_RESEARCH_COMMAND"
RESEARCH_TIMEOUT_ENV = "VIPER_FUNDAMENTALS_RESEARCH_TIMEOUT"
RESEARCH_TIMEOUT_DEFAULT = 900
ALLOWED_SOURCES = ["regulatory filings (SEC EDGAR, ESEF/iXBRL, OpenDART, Unternehmensregister)", "the company's own reports and IR pages",
                   "secondary sources only for discovery and cross-checks (see the prompt)"]
TABLES = ("fundamentals_gap", "fundamentals_research_job")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tables_exist(conn: sqlite3.Connection) -> bool:
    found = {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)", TABLES)}
    return found == set(TABLES)


def scope(conn: sqlite3.Connection) -> list[int]:
    """The securities whose fundamentals are kept: active stocks with an open position or a watchlist entry (as the SEC import)."""
    return [r[0] for r in conn.execute(
        "SELECT s.id FROM security s WHERE s.active = 1 AND lower(s.asset_type) = 'stock' AND ("
        " EXISTS (SELECT 1 FROM positions p WHERE p.security_id = s.id AND p.shares > 0)"
        " OR EXISTS (SELECT 1 FROM watchlist w WHERE w.security_id = s.id)) ORDER BY s.id")]


# -- gap analysis --------------------------------------------------------------------------------------------------------------------------------
def analyse(conn: sqlite3.Connection, security_ids: list[int], as_of: Optional[str] = None) -> dict[int, dict[str, dict]]:
    """{security_id: {gap: {gap, periods, detail, ...}}} of the valuation inputs each security lacks; a security without a price has none (no
    valuation is possible, and no fundamentals input would change that)."""
    out: dict[int, dict[str, dict]] = {}
    for security_id in security_ids:
        row = valuation_metrics.calculate(conn, security_id, as_of)
        gaps: dict[str, dict] = {}
        if row is None:
            has_rows = conn.execute("SELECT 1 FROM fundamentals WHERE security_id = ? LIMIT 1", (security_id,)).fetchone()
            has_price = conn.execute("SELECT 1 FROM market_data WHERE security_id = ? AND close IS NOT NULL LIMIT 1", (security_id,)).fetchone()
            if has_price and not has_rows:
                gaps["period"] = {"gap": "period", "periods": ["last 3 fiscal years and the interim periods since"], "detail": "no fundamentals rows"}
        else:
            for g in row.gaps:
                known = gaps.get(g["gap"])
                if known is None:
                    gaps[g["gap"]] = dict(g)
                else:  # two reasons of one kind (e.g. a bridge period and an old TTM): one gap with both
                    known["periods"] = sorted(set(known["periods"]) | set(g["periods"]))
                    known["detail"] = f"{known['detail']}; {g['detail']}"
        out[security_id] = gaps
    return out


def valuation_coverage(conn: sqlite3.Connection, security_ids: list[int]) -> dict[int, bool]:
    """{security_id: has a market cap} of the newest stored valuation row (what Intelligence reads)."""
    if not valuation_metrics.table_exists(conn):
        return {i: False for i in security_ids}
    out = {}
    for security_id in security_ids:
        row = conn.execute(
            f"SELECT v.market_cap FROM {valuation_metrics.TABLE} v JOIN data_sources d ON d.id = v.source_id WHERE v.security_id = ? AND d.name = ? "
            "ORDER BY v.as_of_date DESC, v.id DESC LIMIT 1", (security_id, valuation_metrics.SOURCE_NAME)).fetchone()
        out[security_id] = row is not None and row[0] is not None
    return out


def fx_reason(conn: sqlite3.Connection, gap: dict) -> str:
    """Why an FX gap stays: a currency without any stored ECB rate, or no rate at the needed date."""
    missing = [c for c in (gap.get("currency"), gap.get("target")) if c and c != "EUR"
               and conn.execute("SELECT 1 FROM fx_rates WHERE base_currency = 'EUR' AND quote_currency = ? LIMIT 1", (c,)).fetchone() is None]
    if missing:
        return f"no ECB reference rate for {', '.join(missing)} stored (not published by the ECB or not in the FX task)"
    return "no ECB rate at the needed date stored: " + gap.get("detail", "")


# -- research jobs ---------------------------------------------------------------------------------------------------------------------------------
def research_request(conn: sqlite3.Connection, security_id: int, gaps: dict[str, dict]) -> Optional[dict]:
    """The request of a research job for the gaps research can close, or None."""
    fields: set[str] = set()
    periods: set[str] = set()
    for code, gap in gaps.items():
        if code in NO_RESEARCH:
            continue
        if code == "period":
            fields.update(PERIOD_FIELDS)
        elif code == "implausible":
            fields.add("shares_outstanding")
        elif code == "free_cash_flow":
            fields.update(("operating_cash_flow", "capex", "free_cash_flow"))
        else:
            fields.add(code)
        periods.update(gap.get("periods") or [])
    if not fields:
        return None
    known: dict[str, str] = {}
    for label in sorted(periods):
        match = re.match(r"^(\d{4}-\d{2}-\d{2}) (\w+)$", label)
        if not match:
            continue
        stored = conn.execute("SELECT * FROM fundamentals WHERE security_id = ? AND period_end = ? AND period_type = ? ORDER BY id",
                              (security_id, match.group(1), match.group(2))).fetchall()
        for row in stored:
            values = {k: row[k] for k in row.keys() if k in PERIOD_FIELDS + ("currency",) and row[k] is not None}
            if values:
                known[label] = ", ".join(f"{k}={v}" for k, v in sorted(values.items()))
    request = {"fields": sorted(fields), "periods": sorted(periods), "gaps": sorted(c for c in gaps if c not in NO_RESEARCH), "known": known,
               "allowed_sources": ALLOWED_SOURCES}
    return request


def request_key(request: dict) -> str:
    return json.dumps({"fields": request["fields"], "periods": request["periods"]}, sort_keys=True)


@dataclass
class ResearchConfig:
    root: Path  # <data>/fundamentals-research
    command: Optional[str] = None
    timeout_s: int = RESEARCH_TIMEOUT_DEFAULT

    @classmethod
    def from_env(cls, data_dir: Path) -> "ResearchConfig":
        timeout = os.environ.get(RESEARCH_TIMEOUT_ENV, "").strip()
        return cls(Path(data_dir) / "fundamentals-research", os.environ.get(RESEARCH_COMMAND_ENV, "").strip() or None,
                   int(timeout) if timeout.isdigit() else RESEARCH_TIMEOUT_DEFAULT)

    def answer_path(self, job_id: int) -> Path:
        return self.root / "answers" / f"{job_id}.json"


def _security(conn: sqlite3.Connection, security_id: int) -> dict:
    row = conn.execute("SELECT id, name, symbol, isin, wkn, exchange, currency, country, industry FROM security WHERE id = ?", (security_id,)).fetchone()
    return dict(row)


def _write_job_files(conn: sqlite3.Connection, research, config: ResearchConfig, job_id: int, security_id: int, request: dict) -> tuple[str, str]:
    security = _security(conn, security_id)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", security["symbol"] or security["name"]).strip("_")
    jobs = config.root / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (config.root / "answers").mkdir(parents=True, exist_ok=True)
    answer = config.answer_path(job_id)
    prompt = research.build_job_prompt(security, research.get_existing_fundamentals(conn, security_id), job_id, request, answer)
    prompt_path = jobs / f"{job_id}_{safe}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    (jobs / f"{job_id}_{safe}.request.json").write_text(json.dumps({
        "job_id": job_id, "security_id": security_id, "symbol": security["symbol"], "name": security["name"], "isin": security["isin"],
        "missing_fields": request["fields"], "missing_periods": request["periods"], "gaps": request["gaps"], "known_values": request["known"],
        "allowed_sources": request["allowed_sources"], "answer_file": str(answer), "answer_format": "see the 'Output format' of the prompt file",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(prompt_path), str(answer)


def _open_job(conn: sqlite3.Connection, security_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM fundamentals_research_job WHERE security_id = ? AND status = 'open'", (security_id,)).fetchone()


def _recent_finished(conn: sqlite3.Connection, security_id: int, key: str, now: str) -> Optional[sqlite3.Row]:
    """The newest job with the same request that ended without the values within ``RESEARCH_RETRY_DAYS`` (a failed answer may be asked again)."""
    since = (datetime.fromisoformat(now) - timedelta(days=RESEARCH_RETRY_DAYS)).isoformat(timespec="seconds")
    return conn.execute(
        "SELECT * FROM fundamentals_research_job WHERE security_id = ? AND request_key = ? AND status IN ('done', 'not_available') AND finished_at >= ? "
        "ORDER BY id DESC LIMIT 1", (security_id, key, since)).fetchone()


def sync_jobs(conn: sqlite3.Connection, research, config: ResearchConfig, gaps: dict[int, dict[str, dict]], now: str,
              create: bool = True) -> dict[int, dict]:
    """Create (unless ``create`` is False), update or close the research job of each security from its gaps.  {security_id: {"action", "job_id"}}
    for every change."""
    actions: dict[int, dict] = {}
    for security_id, security_gaps in gaps.items():
        request = research_request(conn, security_id, security_gaps)
        job = _open_job(conn, security_id)
        if request is None:
            if job is not None:
                conn.execute("UPDATE fundamentals_research_job SET status = 'done', detail = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                             ("no research gap left (closed by another source)", now, now, job["id"]))
                actions[security_id] = {"action": "closed_obsolete", "job_id": job["id"]}
            continue
        key = request_key(request)
        if job is not None:
            if job["request_key"] != key:
                prompt, answer = _write_job_files(conn, research, config, job["id"], security_id, request)
                conn.execute("UPDATE fundamentals_research_job SET request_key = ?, request_json = ?, prompt_path = ?, answer_path = ?, updated_at = ? "
                             "WHERE id = ?", (key, json.dumps(request, ensure_ascii=False), prompt, answer, now, job["id"]))
                actions[security_id] = {"action": "updated", "job_id": job["id"]}
            continue
        if not create or _recent_finished(conn, security_id, key, now) is not None:
            continue
        cur = conn.execute("INSERT INTO fundamentals_research_job (security_id, status, request_key, request_json, created_at, updated_at) "
                           "VALUES (?, 'open', ?, ?, ?, ?)", (security_id, key, json.dumps(request, ensure_ascii=False), now, now))
        job_id = cur.lastrowid
        prompt, answer = _write_job_files(conn, research, config, job_id, security_id, request)
        conn.execute("UPDATE fundamentals_research_job SET prompt_path = ?, answer_path = ? WHERE id = ?", (prompt, answer, job_id))
        actions[security_id] = {"action": "created", "job_id": job_id}
    return actions


def _run_command(config: ResearchConfig, job: sqlite3.Row, symbol: str) -> Optional[str]:
    """Run the configured research command for one job; the error text, or None."""
    values = {"prompt": job["prompt_path"], "answer": job["answer_path"], "job_id": str(job["id"]), "security_id": str(job["security_id"]),
              "symbol": symbol or ""}
    try:
        # Windows: non-POSIX splitting keeps backslashes of paths but also the quotes around a token, which are removed here.
        tokens = shlex.split(config.command, posix=os.name != "nt")
        args = [(t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t).format(**values) for t in tokens]
        done = subprocess.run(args, capture_output=True, text=True, timeout=config.timeout_s, check=False)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        return f"research command failed: {exc}"
    if done.returncode != 0:
        return f"research command exit {done.returncode}: {(done.stderr or done.stdout or '').strip()[:300]}"
    return None


def process_answers(conn: sqlite3.Connection, research, config: ResearchConfig, now: str) -> dict[int, dict]:
    """Run the research command for open jobs without an answer (when configured), then take over every answer that is there.
    {security_id: {"job_id", "status", "applied", "detail"}} for every job that ended or whose command failed."""
    outcome: dict[int, dict] = {}
    for job in conn.execute("SELECT j.*, s.symbol FROM fundamentals_research_job j JOIN security s ON s.id = j.security_id "
                            "WHERE j.status = 'open' ORDER BY j.id").fetchall():
        answer = Path(job["answer_path"] or config.answer_path(job["id"]))
        if not answer.is_file() and config.command:
            error = _run_command(config, job, job["symbol"])
            if error:
                conn.execute("UPDATE fundamentals_research_job SET detail = ?, updated_at = ? WHERE id = ?", (error, now, job["id"]))
                outcome[job["security_id"]] = {"job_id": job["id"], "status": "open", "applied": [], "detail": error}
                continue
        if not answer.is_file():
            continue
        try:
            response = json.loads(answer.read_text(encoding="utf-8"))
            result = research.apply_answer(conn, job["security_id"], response, retrieved_at=now)
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            result = {"errors": [f"answer not readable: {exc}"], "applied": [], "periods": []}
        requested = set(json.loads(job["request_json"])["fields"])
        if result["errors"]:
            status, detail = "failed", "invalid answer: " + "; ".join(str(e) for e in result["errors"])[:500]
        elif any(a["field"] in requested for a in result["applied"]):
            status, detail = "done", f"{len(result['applied'])} value(s) taken over"
        else:
            status, detail = "not_available", "the answer has no verified value for the requested fields"
        conn.execute("UPDATE fundamentals_research_job SET status = ?, result_json = ?, detail = ?, updated_at = ?, finished_at = ? WHERE id = ?",
                     (status, json.dumps(result, ensure_ascii=False, default=str), detail, now, now, job["id"]))
        outcome[job["security_id"]] = {"job_id": job["id"], "status": status, "applied": result["applied"], "detail": detail}
    return outcome


def reapply_fills(conn: sqlite3.Connection, security_ids: list[int]) -> int:
    """Write back the research values of earlier jobs into a stored period that lost them (an SEC import replaces its rows).  Only an empty field
    is filled, and only in a row of the period the job filled; returns the number of values written back."""
    count = 0
    rows = conn.execute(
        f"SELECT security_id, result_json FROM fundamentals_research_job WHERE status = 'done' AND result_json IS NOT NULL "
        f"AND security_id IN ({', '.join('?' * len(security_ids))}) ORDER BY id", security_ids).fetchall() if security_ids else []
    for security_id, result_json in rows:
        for applied in json.loads(result_json).get("applied", []):
            name = applied.get("field")
            if name not in PERIOD_FIELDS + ("gross_profit", "operating_income", "ebit", "total_assets", "total_liabilities") or applied.get("new_period"):
                continue
            cur = conn.execute(
                f"UPDATE fundamentals SET {name} = ? WHERE security_id = ? AND period_end = ? AND period_type = ? AND {name} IS NULL "
                "AND id = (SELECT id FROM fundamentals WHERE security_id = ? AND period_end = ? AND period_type = ? ORDER BY id LIMIT 1)",
                (applied["value"], security_id, applied["period_end"], applied["period_type"], security_id, applied["period_end"], applied["period_type"]))
            count += cur.rowcount
    return count


# -- gap status ------------------------------------------------------------------------------------------------------------------------------------
def _source_step(name: str, available: bool, result: Optional[dict], missing: bool) -> dict:
    if not available:
        return {"step": name, "result": "not_mapped" if name == "sec" else "no_parser"}
    if result is None:
        return {"step": name, "result": "not_run"}
    if result["status"] == "failed":
        return {"step": name, "result": "failed", "detail": result.get("error")}
    if result["status"] in ("skipped", "no_rows"):  # ran, but the source had no usable data for the security
        return {"step": name, "result": "no_data"}
    return {"step": name, "result": "checked_missing" if missing else "found"}


def persist_gaps(conn: sqlite3.Connection, security_ids: list[int], before: dict, middle: dict, after: dict, *, sec: dict, sec_mapped: set,
                 ir: dict, ir_parsers: set, answers: dict, now: str) -> dict:
    """Store the gap status of each security (``fundamentals_gap``) and return the counts for the report."""
    counts = {"closed_by_source": [], "closed_by_research": [], "research": [], "unavailable": []}
    for security_id in security_ids:
        previous = {r["gap"]: r for r in conn.execute("SELECT * FROM fundamentals_gap WHERE security_id = ?", (security_id,)).fetchall()}
        job = _open_job(conn, security_id)
        answer = answers.get(security_id)
        latest_job = conn.execute("SELECT * FROM fundamentals_research_job WHERE security_id = ? ORDER BY id DESC LIMIT 1", (security_id,)).fetchone()
        current = after.get(security_id, {})
        was = set(before.get(security_id, {})) | {g for g, r in previous.items() if r["status"] != "closed"}
        for code in sorted(was | set(current)):
            gap = current.get(code)
            trail = [_source_step("sec", security_id in sec_mapped, sec.get(security_id), code in middle.get(security_id, {})),
                     _source_step("ir", security_id in ir_parsers, ir.get(security_id), code in middle.get(security_id, {}))]
            row = previous.get(code)
            if gap is None:
                if row is not None and row["status"] == "closed":
                    continue
                by_research = code in middle.get(security_id, {})
                trail.append({"step": "research", "result": "answer_applied" if by_research else "not_needed",
                              **({"job_id": answer["job_id"]} if by_research and answer else {})})
                trail.append({"step": "valuation", "result": "refreshed"})
                conn.execute(
                    "INSERT INTO fundamentals_gap (security_id, gap, status, periods, detail, trail_json, research_job_id, first_seen_at, checked_at, closed_at) "
                    "VALUES (?, ?, 'closed', ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(security_id, gap) DO UPDATE SET status = 'closed', trail_json = excluded.trail_json, "
                    "research_job_id = excluded.research_job_id, checked_at = excluded.checked_at, closed_at = excluded.closed_at",
                    (security_id, code, row["periods"] if row else json.dumps(before.get(security_id, {}).get(code, {}).get("periods", [])),
                     row["detail"] if row else before.get(security_id, {}).get(code, {}).get("detail"), json.dumps(trail),
                     answer["job_id"] if by_research and answer else None, row["first_seen_at"] if row else now, now, now))
                counts["closed_by_research" if by_research else "closed_by_source"].append((security_id, code))
                continue
            if code in NO_RESEARCH:
                status, reason, job_id = "unavailable", fx_reason(conn, gap) if code == "fx" else NO_RESEARCH[code], None
                trail.append({"step": "research", "result": "not_applicable", "detail": NO_RESEARCH[code]})
            elif job is not None:
                status, reason, job_id = "research", f"research job {job['id']} open", job["id"]
                trail.append({"step": "research", "result": "job_open", "job_id": job["id"],
                              **({"detail": job["detail"]} if job["detail"] else {})})
            else:
                job_id = latest_job["id"] if latest_job is not None else None
                if latest_job is not None and latest_job["status"] == "failed":
                    reason = f"research job {job_id}: {latest_job['detail']}"
                    trail.append({"step": "research", "result": "answer_invalid", "job_id": job_id})
                elif latest_job is not None:
                    reason = f"research job {job_id} found no verified value ({latest_job['status']}); asked again after {RESEARCH_RETRY_DAYS} days"
                    trail.append({"step": "research", "result": "not_available", "job_id": job_id})
                else:
                    reason = "no research job"
                    trail.append({"step": "research", "result": "not_run"})
                status = "unavailable"
            conn.execute(
                "INSERT INTO fundamentals_gap (security_id, gap, status, periods, detail, trail_json, research_job_id, first_seen_at, checked_at, closed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) ON CONFLICT(security_id, gap) DO UPDATE SET status = excluded.status, periods = excluded.periods, "
                "detail = excluded.detail, trail_json = excluded.trail_json, research_job_id = excluded.research_job_id, checked_at = excluded.checked_at, "
                "closed_at = NULL, first_seen_at = CASE WHEN fundamentals_gap.status = 'closed' THEN excluded.first_seen_at ELSE fundamentals_gap.first_seen_at END",
                (security_id, code, status, json.dumps(gap.get("periods", [])), f"{gap['detail']} | {reason}", json.dumps(trail), job_id, now, now))
            counts[status].append((security_id, code))
    return counts


def gap_details(conn: sqlite3.Connection, security_id: int) -> tuple[str, ...]:
    """The open gaps of a security for the data quality of Intelligence, e.g.
    ``completion shares_outstanding=research [sec=not_mapped, ir=checked_missing, research=job_open #3]``; () without the table or a gap."""
    if not tables_exist(conn):
        return ()
    out = []
    for gap, status, trail_json, job_id in conn.execute(
            "SELECT gap, status, trail_json, research_job_id FROM fundamentals_gap WHERE security_id = ? AND status != 'closed' ORDER BY gap",
            (int(security_id),)).fetchall():
        steps = ", ".join(f"{s['step']}={s['result']}" + (f" #{s['job_id']}" if s.get("job_id") else "") for s in json.loads(trail_json or "[]"))
        out.append(f"completion {gap}={status} [{steps}]")
    return tuple(out)


# -- one run -----------------------------------------------------------------------------------------------------------------------------------------
@dataclass
class Steps:
    """The source steps of a run, wired by the caller (the scripts are loaded by file name)."""

    sec: Callable[[set], dict]  # security ids -> {security_id: {"status": "ok"|"skipped"|"failed", ...}}
    sec_mapped: set
    ir: Callable[[set], dict]
    ir_parsers: set
    refresh_valuation: Callable[[list[int]], object]
    research: object  # the Research-TradingFundamentals module


@dataclass
class Report:
    security_ids: list[int] = field(default_factory=list)
    stored_before: dict = field(default_factory=dict)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    coverage_before: int = 0
    coverage_after: int = 0
    sec: dict = field(default_factory=dict)
    ir: dict = field(default_factory=dict)
    refilled: int = 0
    jobs: dict = field(default_factory=dict)
    answers: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    persisted: bool = False


def _market_caps(conn: sqlite3.Connection, security_ids: list[int]) -> int:
    count = 0
    for security_id in security_ids:
        row = valuation_metrics.calculate(conn, security_id)
        count += row is not None and row.market_cap is not None
    return count


def run(conn: sqlite3.Connection, steps: Steps, config: ResearchConfig, *, write: bool, security_ids: Optional[list[int]] = None,
        now: Optional[str] = None) -> Report:
    """One data-maintenance run (see the module text).  Without ``write`` only the gap analysis of the stored data is done."""
    conn.row_factory = sqlite3.Row
    now = now or now_utc()
    ids = [i for i in scope(conn) if security_ids is None or i in security_ids]
    report = Report(security_ids=ids)
    report.stored_before = valuation_coverage(conn, ids)
    report.before = analyse(conn, ids)
    report.coverage_before = _market_caps(conn, ids)
    if not write:
        report.after = report.before
        report.coverage_after = report.coverage_before
        return report
    report.sec = steps.sec({i for i in ids if i in steps.sec_mapped})
    report.ir = steps.ir({i for i in ids if i in steps.ir_parsers})
    tracked = tables_exist(conn)
    if tracked:
        report.refilled = reapply_fills(conn, ids)
    middle = analyse(conn, ids)
    if tracked:
        report.jobs = sync_jobs(conn, steps.research, config, middle, now)
        report.answers = process_answers(conn, steps.research, config, now)
    report.after = analyse(conn, ids)
    if report.answers:
        # A taken-over answer can close the last research gap: the job list follows the new state (a failed answer is asked again next run).
        for security_id, action in sync_jobs(conn, steps.research, config, report.after, now, create=False).items():
            report.jobs.setdefault(security_id, action)
    steps.refresh_valuation(ids)
    report.coverage_after = _market_caps(conn, ids)
    if tracked:
        report.counts = persist_gaps(conn, ids, report.before, middle, report.after, sec=report.sec, sec_mapped=steps.sec_mapped, ir=report.ir,
                                     ir_parsers=steps.ir_parsers, answers=report.answers, now=now)
        report.persisted = True
    return report

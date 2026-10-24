# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""VIPER's own reasoning workflow for one security, built as a LangGraph ``StateGraph``:

    observe -> assess_data --(insufficient)--> finalize
                          `--> quantitative  (deterministic) --.
                          `--> qualitative   (LLM + evidence) --+-> hypothesis (LLM) -> evidence_check --(gaps, once)--> hypothesis
                                                                                                    `--> risk_context -> decide (LLM) -> review -> finalize

* Deterministic: every figure (prices, technical and fundamental metrics, the engine decision, portfolio weight, sector guard) comes from the
  existing engine (``analysis_engine``, ``trading_orchestrator``, ``portfolio_history``, ``sector_overview``) on a read-only connection.
* LLM: only interpretation - the news/event reading, thesis and counter-thesis with assumptions and uncertainty, and a reasoned proposal.  Every
  claim must cite evidence ids of the catalogue built in ``observe`` (``metric:*``, ``engine:*``, ``news:<id>``, ``event:<id>``); the evidence
  check refuses unknown ids and sends the hypothesis back once.
* The proposal is never an order: ``review`` checks it against the holding (no SELL of a security that is not held), marks where it differs from
  the engine and gives it a validity date.  Nothing here writes outside the two analysis tables.
* LangGraph does the orchestration: parallel branches, conditional edges, and checkpointing with ``SqliteSaver`` in the trading database
  (one thread per case; no database of its own), so a
  case interrupted by a crash or a failed LLM call continues with :func:`resume` from its last finished step.  The step protocol is written to
  ``analysis_step`` as each node finishes; the result to ``analysis_case``.
* Re-evaluation: the case stores a fingerprint of its inputs; :func:`staleness` says what changed since (new prices, news, events, position,
  engine action, expired validity), and :func:`run_analysis` with ``supersedes_id`` starts the follow-up case.  There is no permanent loop.
"""

from __future__ import annotations

import json
import operator
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator, Optional, TypedDict

import analysis_store

LLM = Callable[[str, str], str]  # (system prompt, user prompt) -> JSON text

NEWS_DAYS, NEWS_LIMIT = 45, 15
EVENT_DAYS, EVENT_LIMIT = 180, 10
MAX_HYPOTHESIS_ATTEMPTS = 2
MAX_VALIDITY_DAYS = 90
HELD_ACTIONS = ("HOLD", "ADD", "TRIM", "SELL")
NOT_HELD_ACTIONS = ("WATCH", "BUY", "NO_ACTION")
CONFIDENCE = ("low", "medium", "high")

SYSTEM = (
    "Du bist der Analyse-Baustein von VIPER-AI, einer lokalen Portfolio-Analyse. Du interpretierst nur: alle Zahlen kommen aus der Engine und "
    "werden nicht neu berechnet. Jede Aussage nennt die IDs der Belege aus dem Belegkatalog (z. B. metric:rsi14, engine:action, news:123). "
    "Erfinde keine Daten und keine Belege. Ein Handlungsvorschlag ist keine Order. Antworte ausschließlich mit einem JSON-Objekt auf Deutsch."
)


class State(TypedDict, total=False):
    case_id: int
    security_id: int
    question: str
    as_of: Optional[str]
    db_path: str
    observation: dict
    evidence: dict
    data_quality: dict
    quantitative: dict
    qualitative: dict
    hypothesis: dict
    evidence_check: dict
    attempts: int
    risk: dict
    proposal: dict
    review: dict
    log: Annotated[list, operator.add]


# -- helpers ----------------------------------------------------------------------------------------------------------------------
def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _round(value: Any, digits: int = 2) -> Any:
    n = _num(value)
    return round(n, digits) if n is not None else value


def readonly(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:///{Path(db_path).resolve().as_posix()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


@contextmanager
def writable(db_path: str) -> Iterator[sqlite3.Connection]:
    """A short write connection for the analysis tables (closed at the end; ``analysis_store`` commits each write)."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def parse_json(text: str) -> dict:
    """The JSON object of an LLM answer (also inside a code fence or surrounded by text)."""
    text = (text or "").strip()
    try:
        value = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError("Die Modellantwort enthält kein JSON-Objekt") from None
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Die Modellantwort ist kein JSON-Objekt")
    return value


def _cited(items: Any) -> list[dict]:
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            out.append({"evidence": [str(e) for e in (item.get("evidence") if isinstance(item.get("evidence"), list) else [item.get("evidence")]) if e],
                        "note": str(item.get("note") or item.get("text") or "")})
    return out


# -- deterministic inputs -------------------------------------------------------------------------------------------------------------
def gather(conn: sqlite3.Connection, security_id: int, as_of: Optional[str] = None) -> tuple[dict, dict]:
    """``(observation, evidence catalogue)`` of one security from the existing engine; read-only."""
    import analysis_engine
    import portfolio_history
    import sector_overview
    import trading_orchestrator
    from analysis_contracts import to_primitive

    sec = conn.execute("SELECT id, symbol, name, isin, asset_type, currency FROM security WHERE id = ?", (int(security_id),)).fetchone()
    if sec is None:
        raise ValueError(f"security_id {security_id} not found")
    snapshot = to_primitive(analysis_engine.build_analysis_snapshot(int(security_id), as_of, connection=conn))
    orchestrator = trading_orchestrator.run_trading_orchestrator(conn, as_of=as_of).primitive()
    record = next((r for r in (orchestrator.get("existing_position_results") or []) + (orchestrator.get("entry_candidate_results") or [])
                   if r.get("security_id") == int(security_id)), None)
    plan = orchestrator.get("portfolio_action_plan") or {}
    plan_action = next((a for a in plan.get("existing_position_actions") or [] if a.get("security_id") == int(security_id)), None)
    holdings = portfolio_history.holdings(conn, as_of)
    position = next((p for p in holdings["positions"] if p["security_id"] == int(security_id)), None)
    sector = None
    try:
        overview = sector_overview.build_sector_overview(conn, as_of=as_of)
        for group in overview.get("sectors", []):
            for p in group.get("positions", []):
                if p.get("security_id") == int(security_id) and p.get("origin", "direct") == "direct":
                    sector = {"sector": group.get("label"), "assigned": group.get("assigned"), "weight_pct": group.get("weight_pct"),
                              "guard_status": group.get("guard_status"), "limit_pct": group.get("limit_pct")}
    except Exception as exc:  # noqa: BLE001 - the sector view is context, not required
        sector = {"unavailable": f"{type(exc).__name__}: {exc}"[:200]}
    evaluation = str(snapshot.get("as_of") or as_of or date.today().isoformat())[:10]
    news = [dict(r) for r in conn.execute(
        "SELECT id, published_at, title, summary, publisher, is_material, sentiment FROM news WHERE security_id = ? AND substr(published_at, 1, 10) BETWEEN ? AND ? "
        "ORDER BY is_material DESC, published_at DESC LIMIT ?", (int(security_id), (date.fromisoformat(evaluation) - timedelta(days=NEWS_DAYS)).isoformat(), evaluation, NEWS_LIMIT))]
    events = [dict(r) for r in conn.execute(
        "SELECT id, event_date, event_type, title FROM events WHERE security_id = ? AND substr(event_date, 1, 10) BETWEEN ? AND ? ORDER BY event_date DESC LIMIT ?",
        (int(security_id), (date.fromisoformat(evaluation) - timedelta(days=EVENT_DAYS)).isoformat(), evaluation, EVENT_LIMIT))]
    tech, fund, pos = snapshot.get("technical") or {}, snapshot.get("fundamental") or {}, snapshot.get("position") or {}
    decision = (record or {}).get("decision") or {}
    observation = {
        "security": dict(sec), "as_of": evaluation, "market_data_as_of": (tech.get("quality") or {}).get("as_of"),
        "quality": {k: (snapshot.get(k) or {}).get("quality") for k in ("technical", "fundamental", "valuation", "event_risk", "risk", "position")},
        "technical": {k: _round(tech.get(k)) for k in ("current_price", "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "dist_sma50_pct", "dist_sma200_pct", "rsi14",
                                                        "drawdown_52w_pct", "volatility_60d_annualized_pct", "momentum_score", "data_points")},
        "fundamental": {k: _round(fund.get(k)) for k in ("period_end", "period_type", "currency", "revenue_yoy_growth_pct", "net_income_yoy_growth_pct",
                                                          "operating_margin_pct", "fcf_margin_pct")},
        "engine": {"action": decision.get("action"), "confidence": decision.get("confidence"), "action_quantity": decision.get("action_quantity"),
                   "reasons": decision.get("reasons") or [], "risks": decision.get("risks") or [], "blocking_data_gaps": decision.get("blocking_data_gaps") or [],
                   "stop": _round(decision.get("stop")), "tp1": _round(decision.get("tp1")), "tp2": _round(decision.get("tp2")),
                   "strategy": (record or {}).get("strategy") or pos.get("strategy"), "priority": (record or {}).get("priority"),
                   "plan_block_reason": (plan_action or {}).get("plan_block_reason"), "simulation_only": plan.get("simulation_only")},
        "position": None if position is None else {k: _round(position.get(k)) for k in ("quantity", "market_value_eur", "cost_basis_eur", "unrealized_pct", "weight_pct",
                                                                                         "day_change_pct", "strategy")},
        "held": position is not None,
        "portfolio": {k: _round(holdings["totals"].get(k)) for k in ("market_value_eur", "cash_eur", "swing_weight_pct", "long_term_weight_pct")},
        "sector": sector,
        "news": [{"id": n["id"], "date": str(n["published_at"])[:10], "title": n["title"], "publisher": n["publisher"], "material": bool(n["is_material"])} for n in news],
        "events": [{"id": e["id"], "date": str(e["event_date"])[:10], "type": e["event_type"], "title": e["title"]} for e in events],
    }
    evidence: dict[str, str] = {}
    for key, value in observation["technical"].items():
        if value is not None:
            evidence[f"metric:{key}"] = f"{key} = {value}"
    for key, value in observation["fundamental"].items():
        if value is not None:
            evidence[f"fundamental:{key}"] = f"{key} = {value}"
    engine = observation["engine"]
    if engine["action"]:
        evidence["engine:action"] = f"Engine-Aktion {engine['action']} (Gründe: {', '.join(engine['reasons']) or '—'})"
    if observation["position"]:
        evidence["portfolio:position"] = f"Position: {observation['position']}"
    if sector and sector.get("sector"):
        evidence["portfolio:sector"] = f"Sektor {sector['sector']}: {sector.get('weight_pct')} % (Guard {sector.get('guard_status')})"
    for n in observation["news"]:
        evidence[f"news:{n['id']}"] = f"{n['date']} {n['publisher'] or ''}: {n['title']}"
    for e in observation["events"]:
        evidence[f"event:{e['id']}"] = f"{e['date']} {e['type']}: {e['title'] or ''}"
    return observation, evidence


def fingerprint(conn: sqlite3.Connection, security_id: int, observation: dict) -> dict:
    """What the case was based on, to tell later whether new data call for a re-evaluation."""
    sid = int(security_id)
    return {
        "market_data": conn.execute("SELECT MAX(trade_date) FROM market_data WHERE security_id = ?", (sid,)).fetchone()[0],
        "news": conn.execute("SELECT MAX(id) FROM news WHERE security_id = ?", (sid,)).fetchone()[0],
        "events": conn.execute("SELECT MAX(id) FROM events WHERE security_id = ?", (sid,)).fetchone()[0],
        "shares": (conn.execute("SELECT shares FROM positions WHERE security_id = ?", (sid,)).fetchone() or [0.0])[0],
        "engine_action": (observation.get("engine") or {}).get("action"),
    }


def staleness(conn: sqlite3.Connection, case: dict, today: Optional[date] = None) -> list[str]:
    """Why a finished case should be evaluated again (empty: still current)."""
    reasons: list[str] = []
    old = case.get("fingerprint") or {}
    if case.get("valid_until") and (today or date.today()).isoformat() > str(case["valid_until"]):
        reasons.append(f"Gültigkeit abgelaufen ({case['valid_until']})")
    sid = int(case["security_id"])
    current = {
        "market_data": conn.execute("SELECT MAX(trade_date) FROM market_data WHERE security_id = ?", (sid,)).fetchone()[0],
        "news": conn.execute("SELECT MAX(id) FROM news WHERE security_id = ?", (sid,)).fetchone()[0],
        "events": conn.execute("SELECT MAX(id) FROM events WHERE security_id = ?", (sid,)).fetchone()[0],
        "shares": (conn.execute("SELECT shares FROM positions WHERE security_id = ?", (sid,)).fetchone() or [0.0])[0],
    }
    if old:
        if current["market_data"] != old.get("market_data"):
            reasons.append(f"neue Kurse (bis {current['market_data']})")
        if (current["news"] or 0) > (old.get("news") or 0):
            reasons.append("neue Meldungen")
        if (current["events"] or 0) > (old.get("events") or 0):
            reasons.append("neue Unternehmensereignisse")
        if abs(float(current["shares"] or 0) - float(old.get("shares") or 0)) > 1e-9:
            reasons.append("Position geändert")
    return reasons


# -- the graph --------------------------------------------------------------------------------------------------------------------------
def build_graph(llm: LLM, checkpointer=None):
    from langgraph.graph import END, START, StateGraph

    def step(node: str, kind: str, fn: Callable[[State], dict]) -> Callable[[State], dict]:
        """Run one node and write its protocol row (output or error) for the Intelligence page."""
        def run(state: State) -> dict:
            started = analysis_store.now()
            try:
                update = fn(state)
            except Exception as exc:
                with writable(state["db_path"]) as conn:
                    analysis_store.add_step(conn, state["case_id"], node, kind, started, None, f"{type(exc).__name__}: {exc}"[:1000])
                raise
            shown = {k: v for k, v in update.items() if k not in ("log", "observation", "evidence")}
            if node == "observe":
                shown = {"evidence": len(update.get("evidence") or {}), "news": len(update["observation"]["news"]), "events": len(update["observation"]["events"]),
                         "held": update["observation"]["held"], "engine_action": update["observation"]["engine"]["action"]}
            with writable(state["db_path"]) as conn:
                analysis_store.add_step(conn, state["case_id"], node, kind, started, shown)
            return {**update, "log": [node]}
        return run

    def observe(state: State) -> dict:
        conn = readonly(state["db_path"])
        try:
            observation, evidence = gather(conn, state["security_id"], state.get("as_of"))
        finally:
            conn.close()
        return {"observation": observation, "evidence": evidence}

    def assess_data(state: State) -> dict:
        o = state["observation"]
        gaps = [f"{k}: {', '.join((q or {}).get('details') or []) or (q or {}).get('status')}" for k, q in o["quality"].items()
                if q and q.get("status") not in ("available", "not_applicable")]
        price = o["technical"].get("current_price") is not None
        status = "INSUFFICIENT" if not price else ("LIMITED" if gaps or not o["news"] else "SUFFICIENT")
        return {"data_quality": {"status": status, "gaps": gaps, "market_data_as_of": o["market_data_as_of"], "news": len(o["news"]), "events": len(o["events"])}}

    def quantitative(state: State) -> dict:
        o = state["observation"]
        return {"quantitative": {"technical": o["technical"], "fundamental": o["fundamental"], "engine": o["engine"], "position": o["position"]}}

    def qualitative(state: State) -> dict:
        o = state["observation"]
        if not o["news"] and not o["events"]:
            return {"qualitative": {"summary": "Keine Meldungen oder Ereignisse im Betrachtungszeitraum.", "signals": [], "llm": False}}
        items = {k: v for k, v in state["evidence"].items() if k.startswith(("news:", "event:"))}
        answer = parse_json(llm(SYSTEM, "Lies die Meldungen und Ereignisse zu " + f"{o['security']['symbol']} ({o['security']['name']}). Belege:\n"
                                + json.dumps(items, ensure_ascii=False) + '\nAntwort: {"summary": "...", "signals": [{"evidence": ["news:ID"], "direction": '
                                '"positiv|negativ|neutral", "note": "..."}]}'))
        return {"qualitative": {"summary": str(answer.get("summary") or ""), "signals": _cited(answer.get("signals")), "llm": True}}

    def hypothesis(state: State) -> dict:
        o = state["observation"]
        feedback = (state.get("evidence_check") or {}).get("problems") or []
        prompt = (f"Frage: {state['question']}\nWertpapier: {o['security']['symbol']} ({o['security']['name']}), gehalten: {'ja' if o['held'] else 'nein'}\n"
                  f"Datenlage: {json.dumps(state['data_quality'], ensure_ascii=False)}\nKennzahlen (deterministisch): {json.dumps(state['quantitative'], ensure_ascii=False)}\n"
                  f"Meldungslage: {json.dumps(state['qualitative'], ensure_ascii=False)}\nBelegkatalog: {json.dumps(state['evidence'], ensure_ascii=False)}\n"
                  + (f"Korrigiere diese Probleme der letzten Antwort: {feedback}\n" if feedback else "")
                  + 'Antwort: {"thesis": "...", "counter_thesis": "...", "assumptions": ["..."], "evidence_for": [{"evidence": ["ID"], "note": "..."}], '
                  '"evidence_against": [{"evidence": ["ID"], "note": "..."}], "uncertainty": "niedrig|mittel|hoch", "data_gaps": ["..."]}')
        a = parse_json(llm(SYSTEM, prompt))
        return {"hypothesis": {"thesis": str(a.get("thesis") or ""), "counter_thesis": str(a.get("counter_thesis") or ""),
                               "assumptions": [str(x) for x in a.get("assumptions") or []], "evidence_for": _cited(a.get("evidence_for")),
                               "evidence_against": _cited(a.get("evidence_against")), "uncertainty": str(a.get("uncertainty") or ""),
                               "data_gaps": [str(x) for x in a.get("data_gaps") or []]},
                "attempts": int(state.get("attempts") or 0) + 1}

    def evidence_check(state: State) -> dict:
        h, known = state["hypothesis"], set(state["evidence"])
        cited = [e for part in ("evidence_for", "evidence_against") for item in h[part] for e in item["evidence"]]
        cited += [e for item in state["qualitative"].get("signals", []) for e in item["evidence"]]
        unknown = sorted({e for e in cited if e not in known})
        problems = [f"unbekannte Belege: {', '.join(unknown)}"] if unknown else []
        if not h["thesis"] or not h["counter_thesis"]:
            problems.append("These und Gegenthese müssen beide vorhanden sein")
        if not h["evidence_for"] and not h["evidence_against"]:
            problems.append("keine Belege zitiert")
        return {"evidence_check": {"ok": not problems, "problems": problems, "cited": sorted(set(cited) & known), "attempt": state.get("attempts")}}

    def after_check(state: State) -> str:
        return "hypothesis" if not state["evidence_check"]["ok"] and int(state.get("attempts") or 0) < MAX_HYPOTHESIS_ATTEMPTS else "risk_context"

    def risk_context(state: State) -> dict:
        o = state["observation"]
        return {"risk": {"held": o["held"], "position": o["position"], "sector": o["sector"], "portfolio": o["portfolio"],
                         "engine_action": o["engine"]["action"], "engine_reasons": o["engine"]["reasons"], "plan_block_reason": o["engine"]["plan_block_reason"],
                         "volatility_60d_pct": o["technical"].get("volatility_60d_annualized_pct"), "drawdown_52w_pct": o["technical"].get("drawdown_52w_pct")}}

    def decide(state: State) -> dict:
        allowed = HELD_ACTIONS if state["observation"]["held"] else NOT_HELD_ACTIONS
        prompt = (f"These: {json.dumps(state['hypothesis'], ensure_ascii=False)}\nPrüfung der Belege: {json.dumps(state['evidence_check'], ensure_ascii=False)}\n"
                  f"Risiko und Portfoliokontext (deterministisch): {json.dumps(state['risk'], ensure_ascii=False)}\nErlaubte Aktionen: {', '.join(allowed)}.\n"
                  "Wäge These und Gegenthese ab und schlage eine Aktion vor. Weiche von der Engine-Aktion nur mit Begründung ab.\n"
                  'Antwort: {"action": "...", "rationale": "...", "confidence": "low|medium|high", "horizon_days": 30, "review_triggers": ["..."], '
                  '"evidence": ["ID"]}')
        a = parse_json(llm(SYSTEM, prompt))
        return {"proposal": {"action": str(a.get("action") or "").upper(), "rationale": str(a.get("rationale") or ""), "confidence": str(a.get("confidence") or "").lower(),
                             "horizon_days": a.get("horizon_days"), "review_triggers": [str(x) for x in a.get("review_triggers") or []],
                             "evidence": [str(x) for x in a.get("evidence") or []]}}

    def review(state: State) -> dict:
        p, o = state["proposal"], state["observation"]
        allowed = HELD_ACTIONS if o["held"] else NOT_HELD_ACTIONS
        problems = []
        if p["action"] not in allowed:
            problems.append(f"Aktion {p['action'] or '—'} ist für {'eine gehaltene' if o['held'] else 'eine nicht gehaltene'} Position nicht zulässig; ersetzt durch "
                            f"{'HOLD' if o['held'] else 'WATCH'}")
        action = p["action"] if p["action"] in allowed else ("HOLD" if o["held"] else "WATCH")
        unknown = [e for e in p["evidence"] if e not in state["evidence"]]
        if unknown:
            problems.append(f"unbekannte Belege im Vorschlag: {', '.join(unknown)}")
        confidence = p["confidence"] if p["confidence"] in CONFIDENCE else "low"
        if state["data_quality"]["status"] != "SUFFICIENT" and confidence == "high":
            confidence = "medium"
            problems.append("Konfidenz wegen eingeschränkter Datenlage auf mittel begrenzt")
        if not state["evidence_check"]["ok"]:
            confidence = "low"
            problems.append("Belegprüfung nach zwei Versuchen nicht bestanden: " + "; ".join(state["evidence_check"]["problems"]))
        horizon = p["horizon_days"] if isinstance(p["horizon_days"], int) and 1 <= p["horizon_days"] <= MAX_VALIDITY_DAYS else MAX_VALIDITY_DAYS // 3
        engine = o["engine"]["action"]
        return {"review": {"action": action, "confidence": confidence, "engine_action": engine, "agrees_with_engine": engine is None or action == engine,
                           "valid_until": (date.fromisoformat(o["as_of"]) + timedelta(days=horizon)).isoformat(), "problems": problems,
                           "is_order": False, "note": "Vorschlag der Analyse – keine Order, keine Buchung."}}

    def finalize(state: State) -> dict:
        return {}

    graph = StateGraph(State)
    for name, kind, fn in (("observe", "deterministic", observe), ("assess_data", "deterministic", assess_data), ("quantitative", "deterministic", quantitative),
                           ("qualitative", "llm", qualitative), ("hypothesis", "llm", hypothesis), ("evidence_check", "deterministic", evidence_check),
                           ("risk_context", "deterministic", risk_context), ("decide", "llm", decide), ("review", "deterministic", review)):
        graph.add_node(name, step(name, kind, fn))
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "observe")
    graph.add_edge("observe", "assess_data")
    graph.add_conditional_edges("assess_data", lambda s: "finalize" if s["data_quality"]["status"] == "INSUFFICIENT" else ["quantitative", "qualitative"],
                                ["finalize", "quantitative", "qualitative"])
    graph.add_edge(["quantitative", "qualitative"], "hypothesis")
    graph.add_edge("hypothesis", "evidence_check")
    graph.add_conditional_edges("evidence_check", after_check, ["hypothesis", "risk_context"])
    graph.add_edge("risk_context", "decide")
    graph.add_edge("decide", "review")
    graph.add_edge("review", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def _compiled(db_path: str, llm: LLM):
    """The graph with LangGraph's ``SqliteSaver`` on the trading database (its tables ``checkpoints`` / ``writes`` are created by the migration)."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return build_graph(llm, SqliteSaver(conn)), conn


def _finish(db_path: str, case_id: int, state: dict) -> dict:
    dq, review = state.get("data_quality") or {}, state.get("review")
    result = {k: state.get(k) for k in ("data_quality", "quantitative", "qualitative", "hypothesis", "evidence_check", "risk", "proposal", "review", "evidence", "log")}
    result["observation"] = {k: (state.get("observation") or {}).get(k) for k in ("security", "as_of", "market_data_as_of", "held", "engine", "position", "sector", "news", "events")}
    ro = readonly(db_path)
    try:
        fp = fingerprint(ro, state["security_id"], state.get("observation") or {})
    finally:
        ro.close()
    with writable(db_path) as conn:
        if dq.get("status") == "INSUFFICIENT":
            analysis_store.finish_case(conn, case_id, status="INSUFFICIENT_DATA", result=result, data_as_of=dq.get("market_data_as_of"), fingerprint=fp,
                                       engine_action=((state.get("observation") or {}).get("engine") or {}).get("action"))
        else:
            analysis_store.finish_case(conn, case_id, status="DONE", result=result, data_as_of=dq.get("market_data_as_of"), action=review["action"],
                                       engine_action=review["engine_action"], confidence=review["confidence"], valid_until=review["valid_until"], fingerprint=fp)
        return analysis_store.get_case(conn, case_id)


def _execute(db_path: str, case_id: int, thread_id: str, llm: LLM, initial: Optional[dict]) -> dict:
    app, conn = _compiled(db_path, llm)
    try:
        state = app.invoke(initial, {"configurable": {"thread_id": thread_id}})
    except Exception as exc:
        with writable(db_path) as w:
            analysis_store.fail_case(w, case_id, f"{type(exc).__name__}: {exc}")
            return analysis_store.get_case(w, case_id)
    finally:
        conn.close()
    return _finish(db_path, case_id, state)


def start_case(db_path: str, security_id: int, *, question: str = "", model: str = "", as_of: Optional[str] = None, trigger: str = "manual",
               supersedes_id: Optional[int] = None) -> int:
    """Create the case row (status RUNNING) and answer its id; :func:`run_case` executes it."""
    with writable(db_path) as conn:
        if not analysis_store.available(conn):
            raise RuntimeError("Die Analyse-Tabellen fehlen: Migrate-TradingAnalysisCases.py --write ausführen")
        if conn.execute("SELECT 1 FROM security WHERE id = ?", (int(security_id),)).fetchone() is None:
            raise ValueError(f"security_id {security_id} not found")
        return analysis_store.create_case(conn, security_id=int(security_id), question=question or "Wie ist die Investmentthese zu bewerten und was ist zu tun?",
                                          thread_id=f"viper-case-{uuid.uuid4().hex}", as_of=as_of or date.today().isoformat(), model=model, trigger=trigger,
                                          supersedes_id=supersedes_id)


def run_case(db_path: str, case_id: int, llm: LLM) -> dict:
    with writable(db_path) as conn:
        case = analysis_store.get_case(conn, case_id)
    return _execute(db_path, case_id, case["thread_id"], llm, {"case_id": case_id, "security_id": case["security_id"], "question": case["question"],
                                                                "as_of": None if case["as_of"] == date.today().isoformat() else case["as_of"], "db_path": str(db_path),
                                                                "attempts": 0, "log": []})


def run_analysis(db_path: str, security_id: int, llm: LLM, **kwargs: Any) -> dict:
    return run_case(db_path, start_case(db_path, security_id, **kwargs), llm)


def resume(db_path: str, case_id: int, llm: LLM) -> dict:
    """Continue an interrupted or failed case from its last LangGraph checkpoint (finished steps are not repeated)."""
    with writable(db_path) as conn:
        case = analysis_store.get_case(conn, case_id)
        if case is None:
            raise ValueError(f"analysis case {case_id} not found")
        if case["status"] in ("DONE", "INSUFFICIENT_DATA"):
            return case
        analysis_store.reopen_case(conn, case_id)
    app, cp = _compiled(db_path, llm)
    try:
        has_checkpoint = app.get_state({"configurable": {"thread_id": case["thread_id"]}}).next
    finally:
        cp.close()
    if not has_checkpoint:
        return run_case(db_path, case_id, llm)
    return _execute(db_path, case_id, case["thread_id"], llm, None)

# Copyright (C) 2026 Robert Backhaus
# SPDX-License-Identifier: Apache-2.0

"""Endpoints of the reasoning workflow (``viper_reasoning``, LangGraph) and the Intelligence page.

An analysis runs in a worker thread of this process; the page follows it by asking ``/api/intelligence`` (cases, their steps and status).
The language model is the local Ollama of the settings, asked for JSON answers.  Only the two analysis tables are written; a proposal is never
an order.  Without the migration (``Migrate-TradingAnalysisCases.py --write``) the page says so and nothing is written.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .deps import ollama_client

LLM_TIMEOUT_S = 300.0


def ollama_llm(settings) -> Any:
    """``(system, user) -> JSON text`` against the local Ollama of the settings (no tools, thinking as configured)."""
    client = ollama_client.OllamaClient(settings.ollama_base_url, timeout=LLM_TIMEOUT_S)

    def complete(system: str, user: str) -> str:
        text = ""
        for chunk in client.chat_stream(settings.ollama_model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                        think=settings.think_enabled, response_format="json"):
            if chunk.kind == "content":
                text += chunk.text
        return text

    return complete


def register(app: FastAPI, state) -> None:
    engine = state.engine
    running: dict[int, asyncio.Task] = {}

    def modules():
        return engine.module("viper_reasoning"), engine.module("analysis_store")

    async def in_thread(fn, *args):
        return await asyncio.to_thread(fn, *args)

    def launch(case_id: int, work) -> None:
        task = asyncio.create_task(asyncio.to_thread(work))
        running[case_id] = task
        task.add_done_callback(lambda _t: running.pop(case_id, None))

    def overview() -> dict:
        vr, store = modules()
        conn = vr.readonly(engine.db_path)
        try:
            if not store.available(conn):
                return {"available": False, "message": "Die Analyse-Tabellen fehlen. Einmalig ausführen: viper-ai run Migrate-TradingAnalysisCases.py --write"}
            theses = store.latest_per_security(conn)
            for case in theses:
                case["stale"] = vr.staleness(conn, case)
            cases = store.list_cases(conn, limit=50)
            for case in cases:
                case["interrupted"] = case["status"] == "RUNNING" and case["id"] not in running
            return {"available": True, "theses": theses, "cases": cases, "running": sorted(running)}
        finally:
            conn.close()

    @app.get("/api/intelligence")
    async def intelligence():
        return await in_thread(overview)

    @app.get("/api/intelligence/cases/{case_id}")
    async def case(case_id: int):
        def load():
            vr, store = modules()
            conn = vr.readonly(engine.db_path)
            try:
                found = store.get_case(conn, case_id) if store.available(conn) else None
                if found is None:
                    return None
                found["stale"] = vr.staleness(conn, found) if found["status"] in ("DONE", "INSUFFICIENT_DATA") else []
                found["interrupted"] = found["status"] == "RUNNING" and case_id not in running
                return found
            finally:
                conn.close()

        found = await in_thread(load)
        if found is None:
            raise HTTPException(404, f"Analyse {case_id} nicht gefunden")
        return found

    @app.post("/api/intelligence/analyze")
    async def analyze(request: Request):
        """A new analysis of one security (``supersedes_id``: the re-evaluation of an earlier case)."""
        body = await request.json()
        vr, _ = modules()
        try:
            case_id = await in_thread(lambda: vr.start_case(engine.db_path, int(body["security_id"]), question=str(body.get("question") or ""),
                                                             model=state.settings.ollama_model, trigger=str(body.get("trigger") or "manual"),
                                                             supersedes_id=body.get("supersedes_id")))
        except (KeyError, ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        llm = ollama_llm(state.settings)
        launch(case_id, lambda: vr.run_case(engine.db_path, case_id, llm))
        return {"case_id": case_id}

    @app.post("/api/intelligence/cases/{case_id}/resume")
    async def resume(case_id: int):
        if case_id in running:
            return {"case_id": case_id, "running": True}
        vr, _ = modules()
        llm = ollama_llm(state.settings)
        launch(case_id, lambda: vr.resume(engine.db_path, case_id, llm))
        return {"case_id": case_id, "running": True}

    @app.get("/api/intelligence/securities")
    async def securities():
        """The securities an analysis can be started for: the holdings first, then the watchlist."""
        def load():
            conn = engine.connect_readonly()
            try:
                rows = conn.execute("SELECT s.id, s.symbol, s.name, CASE WHEN p.shares > 0 THEN 1 ELSE 0 END AS held, w.status AS watch FROM security s "
                                    "LEFT JOIN positions p ON p.security_id = s.id LEFT JOIN watchlist w ON w.security_id = s.id "
                                    "WHERE (p.shares > 0) OR w.security_id IS NOT NULL ORDER BY held DESC, s.symbol").fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

        return await in_thread(load)

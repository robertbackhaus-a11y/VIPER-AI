// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Intelligence: active investment theses with their validity and data stand, analyses and running processes (/api/intelligence), the
// latest market events (get_market_intelligence), and the detail of one analysis (#/analysis/<id>) with evidence, counter-arguments,
// uncertainty, portfolio context and the step protocol. A proposal is never an order; real transactions are shown apart.

import { DASH, day, el, get, kv, panel, pct, post, read, eur } from "./core.js";

let timer = null;
const ACTION_TEXT = { HOLD: "Halten", ADD: "Aufstocken", TRIM: "Reduzieren", SELL: "Verkaufen", WATCH: "Beobachten", BUY: "Kaufen", NO_ACTION: "Keine Aktion" };
const STATUS_TEXT = { RUNNING: "läuft", DONE: "fertig", INSUFFICIENT_DATA: "Daten unzureichend", FAILED: "fehlgeschlagen" };
const CONF_TEXT = { low: "niedrig", medium: "mittel", high: "hoch" };
const KIND_TEXT = { deterministic: "deterministisch", llm: "Modell" };
const actionText = (a) => (a ? `${ACTION_TEXT[a] || a} (${a})` : DASH);

export async function render(target, arg, name) {
  clearTimeout(timer);
  if (name === "analysis") return detail(target, Number(arg));
  return overview(target);
}

function statusBadge(c) {
  if (c.interrupted) return el("span", { class: "badge warn" }, "unterbrochen");
  const cls = { DONE: "ok", RUNNING: "warn", FAILED: "bad", INSUFFICIENT_DATA: "warn" }[c.status] || "";
  return el("span", { class: `badge ${cls}`, "data-tip": c.error || null }, STATUS_TEXT[c.status] || c.status);
}

async function overview(target) {
  const [data, securities, events] = await Promise.all([get("/api/intelligence"), get("/api/intelligence/securities"), read("get_market_intelligence", { limit: 12 }).catch(() => null)]);
  const head = el("div", { class: "head" }, el("div", {}, el("h1", {}, "Intelligence"),
    el("div", { class: "muted small" }, "Investmentthesen der VIPER-Analyse (LangGraph): Zahlen aus der Engine, Abwägung durch das lokale Modell. Ein Vorschlag ist keine Order.")));
  if (!data.available) {
    target.replaceChildren(head, el("div", { class: "note warning" }, data.message));
    return;
  }
  const select = el("select", {}, securities.map((s) => el("option", { value: s.id }, `${s.symbol || s.id} – ${s.name}${s.held ? " (gehalten)" : ""}`)));
  const question = el("input", { placeholder: "Frage (optional), z. B. Ist die These nach den Quartalszahlen noch intakt?", style: "min-width:380px;flex:1" });
  const message = el("div");
  const start = async (body) => {
    try {
      const r = await post("/api/intelligence/analyze", body);
      location.hash = `#/analysis/${r.case_id}`;
    } catch (error) {
      message.replaceChildren(el("div", { class: "note error" }, error.message));
    }
  };

  const theses = data.theses.length ? el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Wertpapier", "Vorschlag", "Engine", "Konfidenz", "Datenstand", "Gültig bis", "Stand", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, data.theses.map((c) => el("tr", { class: c.stale.length ? "warning" : "" },
      el("td", {}, el("a", { href: `#/analysis/${c.id}` }, c.symbol || c.security_id), el("span", { class: "muted" }, ` ${c.name || ""}`)),
      el("td", {}, c.status === "DONE" ? actionText(c.action) : statusBadge(c)),
      el("td", { class: c.action && c.engine_action && c.action !== c.engine_action ? "down" : "" }, actionText(c.engine_action)),
      el("td", {}, CONF_TEXT[c.confidence] || DASH),
      el("td", {}, day(c.data_as_of)), el("td", {}, day(c.valid_until)),
      el("td", { class: "wrap" }, c.stale.length ? `Neubewertung: ${c.stale.join(", ")}` : "aktuell"),
      el("td", {}, el("button", { onclick: () => start({ security_id: c.security_id, question: c.question, supersedes_id: c.id, trigger: "reevaluation" }) }, "Neu bewerten"))))))) :
    el("p", { class: "muted" }, "Noch keine Analyse. Wertpapier wählen und „Analyse starten“.");

  const processes = data.cases.length ? el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Nr.", "Wertpapier", "Status", "Auslöser", "Gestartet", "Beendet", "Vorschlag", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, data.cases.map((c) => el("tr", { class: "click", onclick: () => { location.hash = `#/analysis/${c.id}`; } },
      el("td", {}, `#${c.id}`), el("td", {}, c.symbol || c.security_id), el("td", {}, statusBadge(c)), el("td", {}, c.trigger === "reevaluation" ? "Neubewertung" : "manuell"),
      el("td", {}, (c.created_at || "").replace("T", " ").slice(0, 16)), el("td", {}, (c.finished_at || "").replace("T", " ").slice(0, 16) || DASH),
      el("td", {}, actionText(c.action)),
      el("td", {}, c.interrupted || c.status === "FAILED" ? el("button", { onclick: async (e) => { e.stopPropagation(); await post(`/api/intelligence/cases/${c.id}/resume`); render(target, "", "intelligence"); } }, "Fortsetzen") : "")))))) :
    el("p", { class: "muted" }, "Keine Analysen.");

  const eventList = events && Array.isArray(events.events) ? el("div", {}, events.events.map((e) => el("div", { class: "note", style: `border-color:${e.importance === "HIGH" ? "#ff5f6d" : e.importance === "MEDIUM" ? "#f2c14e" : "#1d2a25"}` },
    el("div", { class: "small muted" }, `${(e.published_at || "").replace("T", " ")} · ${e.category} · ${e.importance}${(e.affected_symbols || []).length ? " · " + e.affected_symbols.join(", ") : ""}`), e.headline))) :
    el("p", { class: "muted" }, "Market-Intelligence-Report nicht verfügbar");

  target.replaceChildren(head, message,
    panel("Analyse starten", el("div", { class: "head" }, select, question, el("button", { class: "primary", onclick: () => start({ security_id: Number(select.value), question: question.value }) }, "Analyse starten")),
      el("p", { class: "muted small" }, "Die Analyse läuft im Hintergrund (Beobachten → Analyse → These/Gegenthese → Belegprüfung → Risiko → Vorschlag → Prüfung) und wird gespeichert.")),
    el("div", { class: "grid cols-3", style: "margin-top:16px" },
      el("div", { class: "span-2" }, panel("Aktive Investmentthesen", theses)),
      panel(`Letzte Ereignisse${events && events.freshness ? ` (Stand ${day(events.freshness.evaluation_as_of)})` : ""}`, eventList),
      el("div", { class: "span-2" }, panel("Analysen und Prozesse", processes))));
  if (data.running.length) timer = setTimeout(() => { if (location.hash.startsWith("#/intelligence")) overview(target); }, 3000);
}

function evidenceList(items, catalogue) {
  if (!items || !items.length) return el("p", { class: "muted" }, DASH);
  return el("ul", {}, items.map((i) => el("li", {}, i.note || DASH, " ", ...i.evidence.map((id) => el("span", { class: "badge", "data-tip": catalogue[id] || "unbekannter Beleg" }, id)))));
}

async function detail(target, id) {
  const c = await get(`/api/intelligence/cases/${id}`);
  const r = c.result || {};
  const o = r.observation || {};
  const h = r.hypothesis || {}, review = r.review || {}, proposal = r.proposal || {}, q = r.qualitative || {}, dq = r.data_quality || {}, risk = r.risk || {};
  const catalogue = r.evidence || {};
  const head = el("div", { class: "head" },
    el("div", {}, el("h1", {}, `Analyse #${c.id}: ${c.symbol || c.security_id} · ${c.name || ""}`),
      el("div", { class: "muted small" }, `${c.question} · Modell ${c.model || DASH} · gestartet ${(c.created_at || "").replace("T", " ").slice(0, 16)}`, c.supersedes_id ? [" · Neubewertung von ", el("a", { href: `#/analysis/${c.supersedes_id}` }, `#${c.supersedes_id}`)] : "")),
    el("div", { class: "right" }, statusBadge(c),
      c.interrupted || c.status === "FAILED" ? el("button", { onclick: async () => { await post(`/api/intelligence/cases/${c.id}/resume`); detail(target, id); } }, "Fortsetzen") : "",
      c.status === "DONE" || c.status === "INSUFFICIENT_DATA" ? el("button", { onclick: async () => { const x = await post("/api/intelligence/analyze", { security_id: c.security_id, question: c.question, supersedes_id: c.id, trigger: "reevaluation" }); location.hash = `#/analysis/${x.case_id}`; } }, "Neu bewerten") : "",
      el("a", { href: "#/intelligence" }, "← Intelligence")));
  const blocks = [head];
  if (c.error) blocks.push(el("div", { class: "note error" }, c.error));
  if ((c.stale || []).length) blocks.push(el("div", { class: "note warning" }, `Neubewertung empfohlen: ${c.stale.join(", ")}`));
  if (c.status === "DONE") {
    blocks.push(el("div", { class: "kpis" },
      box("Vorschlag der Analyse", actionText(c.action), "keine Order, keine Buchung"),
      box("Engine-Aktion", actionText(c.engine_action), review.agrees_with_engine ? "stimmt mit dem Vorschlag überein" : "weicht vom Vorschlag ab"),
      box("Konfidenz", CONF_TEXT[c.confidence] || DASH, `Unsicherheit: ${h.uncertainty || DASH}`),
      box("Datenstand", day(c.data_as_of), `Datenlage ${dq.status || DASH}`),
      box("Gültig bis", day(c.valid_until), (proposal.review_triggers || []).join(", ") || "")));
  }
  if (h.thesis) {
    blocks.push(el("div", { class: "grid cols-2" },
      panel("These", el("p", {}, h.thesis), el("h3", {}, "Belege dafür"), evidenceList(h.evidence_for, catalogue)),
      panel("Gegenthese", el("p", {}, h.counter_thesis), el("h3", {}, "Belege dagegen"), evidenceList(h.evidence_against, catalogue)),
      panel("Begründung des Vorschlags", el("p", {}, proposal.rationale || DASH), (review.problems || []).length ? el("div", {}, review.problems.map((p) => el("div", { class: "note warning" }, p))) : null,
        el("p", { class: "muted small" }, review.note || "")),
      panel("Annahmen, Unsicherheit, Datenlücken", kv([["Annahmen", (h.assumptions || []).join("; ") || DASH], ["Unsicherheit", h.uncertainty || DASH],
        ["Datenlücken (Modell)", (h.data_gaps || []).join("; ") || DASH], ["Datenlücken (Engine)", (dq.gaps || []).join("; ") || "keine"],
        ["Belegprüfung", r.evidence_check ? (r.evidence_check.ok ? `bestanden (Versuch ${r.evidence_check.attempt})` : r.evidence_check.problems.join("; ")) : DASH]]))));
  }
  if (o.security) {
    const pos = risk.position || o.position;
    blocks.push(el("div", { class: "grid cols-3", style: "margin-top:16px" },
      panel("Meldungslage", el("p", {}, q.summary || DASH), evidenceList(q.signals, catalogue)),
      panel("Risiko und Portfoliokontext", kv([
        ["Gehalten", o.held ? "ja" : "nein"], ["Marktwert", pos ? eur(pos.market_value_eur) : DASH], ["Anteil am Depot", pos ? pct(pos.weight_pct) : DASH],
        ["Ergebnis", pos ? pct(pos.unrealized_pct) : DASH], ["Sektor", risk.sector && risk.sector.sector ? `${risk.sector.sector} (${pct(risk.sector.weight_pct)}, Guard ${risk.sector.guard_status})` : DASH],
        ["Volatilität 60 T", pct(risk.volatility_60d_pct)], ["Drawdown 52 W", pct(risk.drawdown_52w_pct)], ["Engine-Gründe", (risk.engine_reasons || []).join(", ") || DASH],
        ["Plan-Block", risk.plan_block_reason || DASH]])),
      panel("Kennzahlen (Engine)", kv(Object.entries((r.quantitative || {}).technical || {}).map(([k, v]) => [k, v ?? DASH])))));
    blocks.push(el("div", { class: "grid cols-2", style: "margin-top:16px" },
      panel("Belegkatalog", el("div", { class: "table-wrap", style: "max-height:360px;overflow:auto" }, el("table", {}, el("tbody", {}, Object.entries(catalogue).map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", { class: "wrap" }, v))))))),
      panel("Tatsächliche Transaktionen", el("p", { class: "muted small" }, "Empfehlungen, bestätigte Aktionen und Buchungen sind getrennt: hier stehen nur die gespeicherten Transaktionen seit Beginn der Analyse."),
        el("div", { id: "tx" }, "lädt …"))));
  }
  blocks.push(panel("Schrittprotokoll", el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Schritt", "Art", "Beginn", "Ende", "Ergebnis"].map((x) => el("th", {}, x)))),
    el("tbody", {}, (c.steps || []).map((s) => el("tr", { class: s.error ? "blocker" : "" },
      el("td", {}, s.node), el("td", {}, KIND_TEXT[s.kind] || s.kind), el("td", {}, (s.started_at || "").slice(11, 19)), el("td", {}, (s.finished_at || "").slice(11, 19)),
      el("td", { class: "wrap" }, s.error ? s.error : el("details", {}, el("summary", {}, "anzeigen"), el("pre", { class: "small", style: "white-space:pre-wrap" }, JSON.stringify(s.output, null, 2)))))))))));
  target.replaceChildren(...blocks);
  if (o.security) {
    const txs = await get(`/api/transactions?security_id=${c.security_id}`);
    const since = txs.filter((t) => t.transaction_date.slice(0, 10) >= (c.created_at || "").slice(0, 10));
    const node = document.getElementById("tx");
    if (node) node.replaceChildren(since.length ? el("ul", {}, since.map((t) => el("li", {}, `${day(t.transaction_date)} ${t.transaction_type} ${t.shares} × ${t.price}`))) : el("p", { class: "muted" }, "Keine Transaktion seit der Analyse."));
  }
  if (c.status === "RUNNING" && !c.interrupted) timer = setTimeout(() => { if (location.hash === `#/analysis/${id}`) detail(target, id); }, 2000);
}

const box = (label, value, sub) => el("div", { class: "kpi" }, el("div", { class: "label" }, label), el("div", { class: "value", style: "font-size:1.15rem" }, value), el("div", { class: "sub" }, sub));

// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Intelligence: active investment theses with their validity and data stand, analyses and running processes (/api/intelligence), the
// latest market events (get_market_intelligence), and the detail of one analysis (#/analysis/<id>): executive summary, thesis and
// counter-thesis with collapsible evidence, news, assumptions / uncertainty / data gaps, portfolio context, and the step protocol as the
// technical audit view. A proposal is never an order; real transactions are shown apart.
// Presentation only: evidence ids and engine field names are translated into labels, the ids stay available in the details. Polling
// updates the page in place (open details, scroll position and the start form survive); running times tick in the browser.

import { DASH, cell, day, el, eur, get, kv, num, panel, pct, post, read } from "./core.js";

let poll = null;
let tick = null;
const ACTION_TEXT = { HOLD: "Halten", ADD: "Aufstocken", TRIM: "Reduzieren", SELL: "Verkaufen", WATCH: "Beobachten", BUY: "Kaufen", NO_ACTION: "Keine Aktion" };
const STATUS_TEXT = { RUNNING: "läuft", DONE: "fertig", INSUFFICIENT_DATA: "Daten unzureichend", FAILED: "fehlgeschlagen" };
const CONF_TEXT = { low: "niedrig", medium: "mittel", high: "hoch" };
const KIND_TEXT = { deterministic: "deterministisch", llm: "Modell" };
const DQ_TEXT = { SUFFICIENT: "ausreichend", LIMITED: "eingeschränkt", INSUFFICIENT: "unzureichend" };
const actionText = (a) => (a ? `${ACTION_TEXT[a] || a} (${a})` : DASH);

// Labels of the engine fields and evidence ids (the keys and values themselves are unchanged).
const METRIC = {
  current_price: "Aktueller Kurs", perf_1m_pct: "Performance 1 Monat", perf_3m_pct: "Performance 3 Monate", perf_6m_pct: "Performance 6 Monate",
  dist_sma50_pct: "Abstand SMA 50", dist_sma200_pct: "Abstand SMA 200", rsi14: "RSI 14", drawdown_52w_pct: "Drawdown 52 Wochen",
  volatility_60d_annualized_pct: "Volatilität 60 Tage", momentum_score: "Momentum Score", data_points: "Datenpunkte",
};
const FUNDAMENTAL = {
  period_end: "Periodenende", period_type: "Periodenart", currency: "Berichtswährung", revenue_yoy_growth_pct: "Umsatzwachstum YoY",
  net_income_yoy_growth_pct: "Gewinnwachstum YoY", operating_margin_pct: "Operative Marge", fcf_margin_pct: "FCF-Marge",
};
const AREA = { technical: "Kursdaten", fundamental: "Fundamentaldaten", valuation: "Bewertungsdaten", event_risk: "Ereignisrisiko", risk: "Risikodaten", position: "Positionsdaten" };

export async function render(target, arg, name) {
  clearTimeout(poll);
  if (name === "analysis") return detail(target, Number(arg));
  return overview(target);
}

// -- time ------------------------------------------------------------------------------------------------------------------------------
const ms = (iso) => (iso ? Date.parse(iso) : NaN);
export function duration(millis) {
  if (!Number.isFinite(millis)) return DASH;
  const s = Math.max(0, Math.floor(millis / 1000));
  if (s < 1) return "<1 s";
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} min`;
}
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : DASH);
const hm = (iso) => (iso ? new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : DASH);
const stamp = (iso) => (iso ? `${new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })} ${hm(iso)}` : DASH);
// A live duration: the text is refreshed every second by the browser (no request).
const since = (iso) => el("span", { "data-since": iso }, duration(Date.now() - ms(iso)));
function startTick() {
  if (tick) return;
  tick = setInterval(() => {
    const live = document.querySelectorAll("#page [data-since]");
    if (!live.length && !/^#\/(analysis|intelligence)/.test(location.hash)) { clearInterval(tick); tick = null; return; }
    for (const node of live) node.textContent = duration(Date.now() - ms(node.dataset.since));
  }, 1000);
}
const errorText = (e) => String(e || "").replace(/^OllamaError:\s*/, "");
const cancelled = (c) => c.status === "FAILED" && /Analyse abgebrochen/.test(c.error || "");

function statusBadge(c) {
  if (c.interrupted) return el("span", { class: "badge warn" }, "unterbrochen");
  if (c.status === "RUNNING") return el("span", { class: "badge warn" }, el("span", { class: "pulse" }), "läuft");
  if (cancelled(c)) return el("span", { class: "badge warn" }, "abgebrochen");
  const cls = { DONE: "ok", FAILED: "bad", INSUFFICIENT_DATA: "warn" }[c.status] || "";
  return el("span", { class: `badge ${cls}`, "data-tip": c.error ? errorText(c.error) : null }, STATUS_TEXT[c.status] || c.status);
}
const caseDuration = (c) => (c.status === "RUNNING" ? (c.interrupted ? DASH : since(c.created_at)) : duration(ms(c.finished_at) - ms(c.created_at)));

// -- overview --------------------------------------------------------------------------------------------------------------------------
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
  const thesesBox = el("div"), processesBox = el("div");
  const start = async (body) => {
    try {
      const r = await post("/api/intelligence/analyze", body);
      location.hash = `#/analysis/${r.case_id}`;
    } catch (error) {
      message.replaceChildren(el("div", { class: "note error" }, error.message));
    }
  };

  // Only the two tables are refreshed while an analysis runs; the start form keeps its selection and text.
  const fill = (d) => {
    thesesBox.replaceChildren(d.theses.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Wertpapier", "Vorschlag", "Engine", "Konfidenz", "Datenstand", "Gültig bis", "Stand", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, d.theses.map((c) => el("tr", { class: c.stale.length ? "warning" : "" },
        el("td", {}, el("a", { href: `#/analysis/${c.id}` }, c.symbol || c.security_id), el("span", { class: "muted" }, ` ${c.name || ""}`)),
        el("td", {}, c.status === "DONE" ? actionText(c.action) : statusBadge(c)),
        el("td", { class: c.action && c.engine_action && c.action !== c.engine_action ? "down" : "" }, actionText(c.engine_action)),
        el("td", {}, CONF_TEXT[c.confidence] || DASH),
        el("td", {}, day(c.data_as_of)), el("td", {}, day(c.valid_until)),
        el("td", { class: "wrap" }, c.stale.length ? `Neubewertung: ${c.stale.join(", ")}` : "aktuell"),
        el("td", {}, el("button", { onclick: () => start({ security_id: c.security_id, question: c.question, supersedes_id: c.id, trigger: "reevaluation" }) }, "Neu bewerten"))))))) :
      el("p", { class: "muted" }, "Noch keine Analyse. Wertpapier wählen und „Analyse starten“."));
    processesBox.replaceChildren(d.cases.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Nr.", "Wertpapier", "Status", "Auslöser", "Gestartet", "Beendet", "Dauer", "Vorschlag", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, d.cases.map((c) => el("tr", { class: "click", onclick: () => { location.hash = `#/analysis/${c.id}`; } },
        el("td", {}, `#${c.id}`), el("td", {}, c.symbol || c.security_id), el("td", {}, statusBadge(c)), el("td", {}, c.trigger === "reevaluation" ? "Neubewertung" : "manuell"),
        el("td", {}, stamp(c.created_at)), el("td", {}, c.finished_at ? stamp(c.finished_at) : DASH), el("td", {}, caseDuration(c)),
        el("td", {}, actionText(c.action)),
        el("td", {}, c.interrupted || c.status === "FAILED" ? el("button", { onclick: async (e) => { e.stopPropagation(); await post(`/api/intelligence/cases/${c.id}/resume`); refresh(); } }, "Fortsetzen") : "")))))) :
      el("p", { class: "muted" }, "Keine Analysen."));
  };
  const refresh = async () => {
    clearTimeout(poll);
    if (!location.hash.startsWith("#/intelligence")) return;
    let d = data;
    try { d = await get("/api/intelligence"); } catch { /* keep the last state; the next poll tries again */ }
    if (!thesesBox.isConnected) return;
    fill(d);
    if (d.running.length) poll = setTimeout(refresh, 3000);
  };

  const eventList = events && Array.isArray(events.events) ? el("div", {}, events.events.map((e) => el("div", { class: "note", style: `border-color:${e.importance === "HIGH" ? "#ff5f6d" : e.importance === "MEDIUM" ? "#f2c14e" : "#1d2a25"}` },
    el("div", { class: "small muted" }, `${(e.published_at || "").replace("T", " ")} · ${e.category} · ${e.importance}${(e.affected_symbols || []).length ? " · " + e.affected_symbols.join(", ") : ""}`), e.headline))) :
    el("p", { class: "muted" }, "Market-Intelligence-Report nicht verfügbar");

  fill(data);
  target.replaceChildren(head, message,
    panel("Analyse starten", el("div", { class: "head" }, select, question, el("button", { class: "primary", onclick: () => start({ security_id: Number(select.value), question: question.value }) }, "Analyse starten")),
      el("p", { class: "muted small" }, "Die Analyse läuft im Hintergrund (Beobachten → Analyse → These/Gegenthese → Belegprüfung → Risiko → Vorschlag → Prüfung) und wird gespeichert.")),
    el("div", { class: "grid cols-3", style: "margin-top:16px" },
      el("div", { class: "span-2" }, panel("Aktive Investmentthesen", thesesBox)),
      panel(`Letzte Ereignisse${events && events.freshness ? ` (Stand ${day(events.freshness.evaluation_as_of)})` : ""}`, eventList),
      el("div", { class: "span-2" }, panel("Analysen und Prozesse", processesBox))));
  startTick();
  if (data.running.length) poll = setTimeout(refresh, 3000);
}

// -- evidence: labels, reference chips, readable text ----------------------------------------------------------------------------------
const ID = "(?:metric|fundamental|engine|portfolio|news|event):[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)*";
const IDS = `${ID}(?:\\s*[,;/]\\s*${ID})*`;
const GROUP = new RegExp(`\\s*\\((${IDS})\\)|\\s*\\[(${IDS})\\]|(${IDS})`, "g");
const ONE = new RegExp(ID, "g");

function split(id) {
  const at = id.indexOf(":");
  return [id.slice(0, at), id.slice(at + 1)];
}
function evidenceLabel(id, catalogue) {
  const [kind, key] = split(id);
  if (kind === "metric") return METRIC[key] || key;
  if (kind === "fundamental") return FUNDAMENTAL[key] || key;
  if (kind === "engine") return "Engine-Aktion";
  if (kind === "portfolio") return { position: "Depotposition", sector: "Sektor" }[key] || "Portfolio";
  const date = /^\d{4}-\d{2}-\d{2}/.exec(catalogue[id] || "");
  const when = date ? ` ${date[0].slice(8, 10)}.${date[0].slice(5, 7)}.` : "";
  return (kind === "news" ? "Meldung" : kind === "event" ? "Ereignis" : id) + when;
}
const SOURCE = { metric: "Engine (Kennzahl)", fundamental: "Engine (Fundamentaldaten)", engine: "Engine", portfolio: "Portfolio (Engine)", news: "Meldung", event: "Unternehmensereignis" };
function evidenceValue(id, catalogue) {
  const text = catalogue[id];
  if (text === undefined) return "unbekannter Beleg";
  const [kind] = split(id);
  if (kind === "metric" || kind === "fundamental") {
    const value = text.slice(text.indexOf(" = ") + 3);
    return value !== "" && Number.isFinite(Number(value)) ? num(Number(value), 2) : value;
  }
  return text;
}
// One reference chip for a group of evidence ids; opened it shows ID, value and source of every id (nothing is lost).
function refChip(ids, catalogue) {
  const unique = [...new Set(ids)];
  const labels = [...new Set(unique.map((id) => evidenceLabel(id, catalogue)))];
  const news = unique.filter((id) => split(id)[0] === "news").length;
  const unknown = unique.some((id) => catalogue[id] === undefined);
  const title = news > 1 && news === unique.length ? `${news} Meldungen` : labels.length <= 2 ? labels.join(" · ") : `${labels[0]} · ${labels[1]} +${labels.length - 2}`;
  return el("details", { class: `ref${unknown ? " unknown" : ""}` }, el("summary", {}, title),
    el("div", { class: "ref-box" }, unique.map((id) => el("div", { class: "ref-item" }, el("div", { class: "ref-label" }, evidenceLabel(id, catalogue)),
      kv([["ID", id], ["Wert", evidenceValue(id, catalogue)], ["Quelle", SOURCE[split(id)[0]] || DASH]])))));
}
// Text of the model with its evidence ids replaced by reference chips.
function richText(text, catalogue) {
  const out = [];
  let last = 0;
  for (const m of String(text || "").matchAll(GROUP)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(" ", refChip((m[1] || m[2] || m[3]).match(ONE), catalogue));
    last = m.index + m[0].length;
  }
  if (last < String(text || "").length) out.push(String(text).slice(last));
  return out;
}
// Prose: the first sentences (or paragraphs) visible, the rest under "weiterlesen" - shortened for reading, never rewritten.
function prose(text, catalogue, limit = 4) {
  const paragraphs = String(text || "").split(/\n\s*\n|\n/).map((p) => p.trim()).filter(Boolean);
  if (!paragraphs.length) return el("p", { class: "muted" }, DASH);
  let shown = paragraphs.slice(0, 2), rest = paragraphs.slice(2);
  if (paragraphs.length === 1) {
    const sentences = paragraphs[0].split(/(?<=[.!?])\s+(?=[A-ZÄÖÜ„"(])/);
    shown = [sentences.slice(0, limit).join(" ")];
    rest = sentences.length > limit ? [sentences.slice(limit).join(" ")] : [];
  }
  const ps = (list) => list.map((p) => el("p", {}, richText(p, catalogue)));
  return el("div", { class: "prose" }, ps(shown), rest.length ? el("details", { class: "more" }, el("summary", {}, "weiterlesen"), ps(rest)) : null);
}
function evidenceToggle(title, items, catalogue) {
  if (!items || !items.length) return el("p", { class: "muted small" }, `${title}: keine`);
  return el("details", { class: "more" }, el("summary", {}, `${title} (${items.length}) · Belege anzeigen`),
    el("ul", { class: "evidence" }, items.map((i) => el("li", {}, richText(i.note || DASH, catalogue), " ", i.evidence.length ? refChip(i.evidence, catalogue) : null))));
}
// The data gaps of the engine ("fundamental: FUNDAMENTALS_PUBLICATION_DATE_UNVERIFIED: filing_date missing, ...") as readable text.
function gapText(gap) {
  const [area, ...restParts] = String(gap).split(": ");
  const rest = restParts.join(": ");
  const name = AREA[area] || area;
  if (/FUNDAMENTALS_PUBLICATION_DATE_UNVERIFIED/.test(rest)) return "Publikationsdatum der Fundamentaldaten konnte nicht verifiziert werden (Filing Date fehlt)";
  if (/no source rows|missing|unavailable/i.test(rest)) return `${name} fehlen`;
  if (/stale/i.test(rest)) return `${name} veraltet`;
  if (/partial/i.test(rest)) return `${name} unvollständig`;
  return `${name}: ${rest || "eingeschränkt"}`;
}
const technicalCodes = (codes) => (codes.length ? el("details", { class: "more small" }, el("summary", {}, "Technische Codes"), el("ul", { class: "small muted" }, codes.map((g) => el("li", {}, g)))) : null);

// -- step protocol (updated in place) --------------------------------------------------------------------------------------------------
function stepState(s, c) {
  if (s.error) return "FAILED";
  if (s.finished_at) return "DONE";
  return c.status === "RUNNING" && !c.interrupted ? "RUNNING" : "OPEN";
}
function stepCells(s, state) {
  const status = state === "RUNNING" ? el("span", { class: "badge warn" }, el("span", { class: "pulse" }), s.kind === "llm" ? "Modell läuft" : "läuft")
    : state === "DONE" ? el("span", { class: "badge ok" }, "fertig") : state === "FAILED" ? el("span", { class: "badge bad" }, "Fehler") : el("span", { class: "badge warn" }, "nicht beendet");
  const result = state === "FAILED" ? el("span", { class: "down" }, errorText(s.error))
    : state === "DONE" ? el("details", {}, el("summary", {}, "anzeigen"), el("pre", { class: "small", style: "white-space:pre-wrap" }, JSON.stringify(s.output, null, 2)))
    : el("span", { class: "muted" }, state === "RUNNING" ? "noch kein Ergebnis" : "Analyse läuft nicht mehr");
  return [el("td", {}, s.node), el("td", {}, KIND_TEXT[s.kind] || s.kind), el("td", {}, clock(s.started_at)), el("td", {}, s.finished_at ? clock(s.finished_at) : DASH),
    el("td", { class: "num" }, state === "RUNNING" ? since(s.started_at) : s.finished_at ? duration(ms(s.finished_at) - ms(s.started_at)) : DASH),
    el("td", {}, status), el("td", { class: "wrap" }, result)];
}
// A row is rebuilt only when its state changes, so an opened result stays open over every poll and new steps are appended below.
function stepsTable() {
  const body = el("tbody");
  const rows = new Map();
  const empty = el("p", { class: "muted" }, "Noch kein Schritt.");
  const node = el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Schritt", "Art", "Beginn", "Ende", "Dauer", "Status", "Ergebnis / Fehler"].map((h, i) => el("th", { class: i === 4 ? "num" : "" }, h)))), body), empty);
  const update = (c) => {
    for (const s of c.steps || []) {
      const state = stepState(s, c);
      let row = rows.get(s.id);
      if (!row) {
        row = { tr: el("tr"), key: null };
        rows.set(s.id, row);
        body.append(row.tr);
      }
      if (row.key === state) continue;
      row.key = state;
      row.tr.className = state === "FAILED" ? "blocker" : state === "RUNNING" ? "running" : "";
      row.tr.replaceChildren(...stepCells(s, state));
    }
    empty.hidden = rows.size > 0;
  };
  return { node, update };
}

// -- detail ----------------------------------------------------------------------------------------------------------------------------
async function detail(target, id) {
  let c = await get(`/api/intelligence/cases/${id}`);
  const title = el("h1"), meta = el("div", { class: "muted small" }), actions = el("div", { class: "right" });
  const notesBox = el("div"), result = el("div"), steps = stepsTable();
  const stepsPanel = panel("Schrittprotokoll", el("p", { class: "muted small" }, "Technische Auditansicht: jeder Schritt des Workflows mit Art, Beginn, Ende, Dauer und Ergebnis oder Fehler."), steps.node);
  target.replaceChildren(el("div", { class: "head" }, el("div", {}, title, meta), actions), notesBox, result, stepsPanel);
  let shown = null;

  const refresh = async () => {
    clearTimeout(poll);
    if (location.hash !== `#/analysis/${id}`) return;
    try { c = await get(`/api/intelligence/cases/${id}`); } catch { /* keep the last state; the next poll tries again */ }
    if (!title.isConnected) return;
    apply();
  };
  const resume = async () => { await post(`/api/intelligence/cases/${c.id}/resume`); shown = null; refresh(); };
  const cancel = async (button) => {
    button.disabled = true;
    try { await post(`/api/intelligence/cases/${c.id}/cancel`); } catch (error) { notesBox.append(el("div", { class: "note error" }, error.message)); }
    refresh();
  };

  function apply() {
    const live = c.status === "RUNNING" && !c.interrupted;
    title.textContent = `Analyse #${c.id} · ${c.symbol || c.security_id}${c.name ? ` · ${c.name}` : ""}`;
    const runtime = live ? ["läuft seit ", since(c.created_at)] : c.interrupted ? "unterbrochen"
      : `${cancelled(c) ? "abgebrochen" : c.status === "FAILED" ? "fehlgeschlagen" : "abgeschlossen"} nach ${duration(ms(c.finished_at) - ms(c.created_at))}`;
    meta.replaceChildren(el("span", {}, `Modell ${c.model || DASH} · gestartet ${hm(c.created_at)} · `, runtime,
      c.supersedes_id ? [" · Neubewertung von ", el("a", { href: `#/analysis/${c.supersedes_id}` }, `#${c.supersedes_id}`)] : ""),
      el("div", {}, c.question));
    const signature = `${c.status}|${c.interrupted}|${c.cancel_requested}`;
    if (signature !== shown) {
      shown = signature;
      const cancelButton = live ? el("button", { class: "danger", disabled: c.cancel_requested || null, onclick: (e) => cancel(e.currentTarget) }, c.cancel_requested ? "Abbruch angefordert …" : "Analyse abbrechen") : "";
      actions.replaceChildren(statusBadge(c), cancelButton,
        c.interrupted || c.status === "FAILED" ? el("button", { onclick: resume }, "Fortsetzen") : "",
        c.status === "DONE" || c.status === "INSUFFICIENT_DATA" ? el("button", { onclick: async () => { const x = await post("/api/intelligence/analyze", { security_id: c.security_id, question: c.question, supersedes_id: c.id, trigger: "reevaluation" }); location.hash = `#/analysis/${x.case_id}`; } }, "Neu bewerten") : "",
        el("a", { href: "#/intelligence" }, "← Intelligence"));
      const failedStep = (c.steps || []).filter((s) => s.error).pop();
      const blocks = [];
      if (cancelled(c)) blocks.push(el("div", { class: "note warning" }, el("strong", {}, "Analyse abgebrochen"), failedStep ? ` im Schritt ${failedStep.node}. ` : ". ",
        "„Fortsetzen“ setzt beim letzten abgeschlossenen Schritt fort."));
      else if (c.status === "FAILED") blocks.push(el("div", { class: "note error" }, el("strong", {}, "Analyse fehlgeschlagen"), failedStep ? ` im Schritt ${failedStep.node}` : "", `: ${errorText(c.error)}. `,
        "„Fortsetzen“ setzt beim letzten abgeschlossenen Schritt fort."));
      if (c.interrupted) blocks.push(el("div", { class: "note warning" }, "Die Analyse wurde unterbrochen (zum Beispiel durch einen Neustart von VIPER). „Fortsetzen“ setzt beim letzten abgeschlossenen Schritt fort."));
      if (live) blocks.push(el("div", { class: "note small" }, c.cancel_requested ? "Abbruch angefordert: der laufende Modellaufruf wird beendet, ein deterministischer Schritt läuft noch zu Ende."
        : `Modellschritte können einige Minuten dauern (Zeitlimit ${Math.round(c.llm_timeout_s || 300)} s je Warten auf Ollama). „Analyse abbrechen“ beendet den laufenden Modellaufruf; danach kann die Analyse fortgesetzt werden.`));
      if (live && c.live_steps === false) blocks.push(el("div", { class: "note warning" }, "Der laufende Schritt erscheint erst nach seinem Ende im Protokoll, bis einmalig „viper-ai run Migrate-TradingAnalysisCases.py --write“ ausgeführt wurde."));
      if ((c.stale || []).length) blocks.push(el("div", { class: "note warning" }, `Neubewertung empfohlen: ${c.stale.join(", ")}`));
      notesBox.replaceChildren(...blocks);
      if (c.status !== "RUNNING" && c.result) {
        const before = stepsPanel.getBoundingClientRect().top;
        result.replaceChildren(...resultBlocks(c));
        if (window.scrollY > 0) window.scrollBy(0, stepsPanel.getBoundingClientRect().top - before); // the part the reader was looking at stays in place
        transactions(c);
      }
    }
    steps.update(c);
    if (live) poll = setTimeout(refresh, 2000);
  }

  apply();
  startTick();
}

const box = (label, value, sub) => el("div", { class: "kpi" }, el("div", { class: "label" }, label), el("div", { class: "value compact" }, value), sub ? el("div", { class: "sub" }, sub) : null);

function resultBlocks(c) {
  const r = c.result || {};
  const o = r.observation || {};
  const h = r.hypothesis || {}, review = r.review || {}, proposal = r.proposal || {}, q = r.qualitative || {}, dq = r.data_quality || {}, risk = r.risk || {};
  const catalogue = r.evidence || {};
  const blocks = [];
  const engineGaps = dq.gaps || [];
  if (c.status === "DONE") {
    blocks.push(el("div", { class: "kpis" },
      box("Vorschlag", actionText(c.action), "keine Order"),
      box("Engine", actionText(c.engine_action)),
      box("Konfidenz", CONF_TEXT[c.confidence] || DASH),
      box("Datenlage", DQ_TEXT[dq.status] || dq.status || DASH, `Datenstand ${day(c.data_as_of)}`),
      box("Gültig bis", day(c.valid_until), "Neu bewerten bei wesentlichen Kurs-, Nachrichten- oder Fundamentaldatenänderungen")));
    blocks.push(c.engine_action ? el("div", { class: `agree ${review.agrees_with_engine ? "ok" : "warn"}` }, review.agrees_with_engine ? "✓ Engine und Analyse stimmen überein" : "⚠ Analyse weicht von der Engine ab")
      : el("div", { class: "agree muted" }, "Keine Engine-Aktion für dieses Wertpapier"));
  }
  if (dq.status && dq.status !== "SUFFICIENT") {
    const reasons = engineGaps.map(gapText);
    if (!dq.news) reasons.push("Keine Meldungen im Betrachtungszeitraum");
    blocks.push(el("div", { class: "note warning" }, el("strong", {}, dq.status === "INSUFFICIENT" ? "⚠ Datenlage unzureichend" : "⚠ Datenlage eingeschränkt"),
      el("div", {}, reasons.length ? `${reasons.join(". ")}. ` : "", dq.status === "INSUFFICIENT" ? "Ohne Kurs ist keine Bewertung möglich." : "Bewertung mit eingeschränkter Datenqualität."),
      technicalCodes([`Datenlage ${dq.status}`, ...engineGaps])));
  }
  if (h.thesis || h.counter_thesis) {
    blocks.push(el("div", { class: "grid cols-2" },
      panel("These", prose(h.thesis, catalogue), evidenceToggle("Belege dafür", h.evidence_for, catalogue)),
      panel("Gegenthese", prose(h.counter_thesis, catalogue), evidenceToggle("Belege dagegen", h.evidence_against, catalogue))));
  }
  if (proposal.rationale || o.security) {
    const signals = q.signals || [];
    const items = new Map([...(o.news || []).map((n) => [`news:${n.id}`, n]), ...(o.events || []).map((e) => [`event:${e.id}`, { ...e, title: `${e.type}: ${e.title || ""}` }])]);
    const relevant = signals.length ? signals : (o.news || []).map((n) => ({ evidence: [`news:${n.id}`], note: "" }));
    const count = new Set(relevant.flatMap((s) => s.evidence)).size;
    blocks.push(el("div", { class: "grid cols-2", style: "margin-top:16px" },
      panel("Begründung des Vorschlags", prose(proposal.rationale, catalogue, 3),
        (review.problems || []).map((p) => el("div", { class: "note warning" }, p)),
        (proposal.review_triggers || []).length ? el("details", { class: "more" }, el("summary", {}, `Neubewertung auslösen bei (${proposal.review_triggers.length})`),
          el("ul", { class: "evidence" }, proposal.review_triggers.map((t) => el("li", {}, richText(t, catalogue))))) : null,
        el("p", { class: "muted small" }, review.note || "")),
      panel("Meldungslage", prose(q.summary, catalogue, 4),
        relevant.length ? el("details", { class: "more" }, el("summary", {}, `Relevante Meldungen (${count}) · anzeigen`),
          el("ul", { class: "evidence" }, relevant.map((s) => el("li", {},
            s.evidence.map((eid) => items.get(eid) ? el("div", {}, el("span", { class: "muted" }, `${day(items.get(eid).date)} `), items.get(eid).title) : null),
            s.note ? el("div", { class: "small" }, "Einordnung: ", richText(s.note, catalogue)) : null,
            refChip(s.evidence, catalogue))))) : null)));
  }
  if (h.thesis) {
    const ec = r.evidence_check;
    blocks.push(el("div", { class: "grid cols-3", style: "margin-top:16px" },
      panel("Annahmen", (h.assumptions || []).length ? el("ul", { class: "evidence" }, h.assumptions.map((a) => el("li", {}, richText(a, catalogue)))) : el("p", { class: "muted" }, DASH)),
      panel("Unsicherheit", el("div", { class: "big-value" }, h.uncertainty || DASH),
        el("p", { class: "small muted" }, `Konfidenz des Vorschlags ${CONF_TEXT[c.confidence] || DASH}, Datenlage ${DQ_TEXT[dq.status] || DASH}.`),
        el("p", { class: "small muted" }, ec ? (ec.ok ? `Belegprüfung bestanden (Versuch ${ec.attempt}).` : `Belegprüfung: ${ec.problems.join("; ")}`) : "")),
      panel("Datenlücken", engineGaps.length || (h.data_gaps || []).length
        ? el("ul", { class: "evidence" }, engineGaps.map((g) => el("li", {}, gapText(g))), (h.data_gaps || []).map((g) => el("li", {}, richText(g, catalogue))))
        : el("p", { class: "muted" }, "keine"), technicalCodes(engineGaps))));
  }
  if (o.security) {
    const pos = risk.position || o.position;
    const quant = r.quantitative || {};
    const fmt = (key, v) => (v === null || v === undefined ? DASH : key === "data_points" ? num(v, 0) : typeof v !== "number" ? String(v) : key.endsWith("_pct") ? pct(v, 2) : num(v, 2));
    const labelled = (map, values) => Object.entries(values || {}).map(([k, v]) => [map[k] || k, fmt(k, v)]);
    const raw = (values) => Object.entries(values || {}).map(([k, v]) => [k, v ?? DASH]);
    const d = c.display || {};
    blocks.push(el("div", { class: "grid cols-2", style: "margin-top:16px" },
      panel("Risiko und Portfoliokontext", kv([
        ["Gehalten", o.held ? "ja" : "nein"], ["Marktwert", pos ? eur(pos.market_value_eur) : DASH], ["Anteil am Depot", pos ? pct(pos.weight_pct) : DASH],
        ["Ergebnis", pos ? pct(pos.unrealized_pct) : DASH], ["Sektor", risk.sector && risk.sector.sector ? `${risk.sector.sector} (${pct(risk.sector.weight_pct)}, Guard ${risk.sector.guard_status})` : DASH],
        ["Volatilität 60 Tage", pct(risk.volatility_60d_pct)], ["Drawdown 52 Wochen", pct(risk.drawdown_52w_pct)],
        ["Engine-Grund", d.engine_reasons ? cell(d.engine_reasons) : (risk.engine_reasons || []).join(", ") || DASH],
        ["Plan-Block", d.plan_block_reason ? cell(d.plan_block_reason) : risk.plan_block_reason || "keiner"]])),
      panel("Kennzahlen (Engine)", kv(labelled(METRIC, quant.technical)),
        Object.keys(quant.fundamental || {}).length ? [el("h3", {}, "Fundamentaldaten"), kv(labelled(FUNDAMENTAL, quant.fundamental))] : null,
        el("details", { class: "more" }, el("summary", {}, "Technische Felder anzeigen"), kv([...raw(quant.technical), ...raw(quant.fundamental)])))));
    const entries = Object.entries(catalogue);
    blocks.push(el("div", { class: "grid cols-2", style: "margin-top:16px" },
      panel("Belegkatalog", el("details", { class: "more" }, el("summary", {}, `${entries.length} Belege anzeigen`),
        el("div", { class: "table-wrap", style: "max-height:420px;overflow:auto" }, el("table", {},
          el("thead", {}, el("tr", {}, ["Beleg", "Wert", "ID"].map((x) => el("th", {}, x)))),
          el("tbody", {}, entries.map(([k]) => el("tr", {}, el("td", {}, evidenceLabel(k, catalogue)), el("td", { class: "wrap" }, evidenceValue(k, catalogue)), el("td", { class: "muted small" }, k)))))))),
      panel("Tatsächliche Transaktionen", el("p", { class: "muted small" }, "Empfehlungen, bestätigte Aktionen und Buchungen sind getrennt: hier stehen nur die gespeicherten Transaktionen seit Beginn der Analyse."),
        el("div", { id: "tx" }, "lädt …"))));
  }
  blocks.push(el("div", { style: "height:16px" }));
  return blocks;
}

async function transactions(c) {
  if (!(c.result && c.result.observation && c.result.observation.security)) return;
  const txs = await get(`/api/transactions?security_id=${c.security_id}`).catch(() => null);
  const node = document.getElementById("tx");
  if (!node) return;
  if (!txs) { node.replaceChildren(el("p", { class: "muted" }, "Transaktionen nicht verfügbar.")); return; }
  const since_ = txs.filter((t) => t.transaction_date.slice(0, 10) >= (c.created_at || "").slice(0, 10));
  node.replaceChildren(since_.length ? el("ul", {}, since_.map((t) => el("li", {}, `${day(t.transaction_date)} ${t.transaction_type} ${t.shares} × ${t.price}`))) : el("p", { class: "muted" }, "Keine Transaktion seit der Analyse."));
}

// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Watchlist: the table of the desktop page (/api/views/watchlist) with filters and sorting; strategy, priority and sector are edited in the
// table and written only by "Übernehmen" (strategy, then priority, then sector); add (with check), remove (after a confirmation) and the
// dialog "Neues Wertpapier aufnehmen" (check first, create only after a clean check of the unchanged form).

import { cell, el, get, panel, post, text } from "./core.js";

const STRATEGY_COL = 2, PRIORITY_COL = 3, SECTOR_COL = 14;
let pending = {};

export async function render(target) {
  const vm = await get("/api/views/watchlist");
  pending = {};
  if (!vm.available) {
    target.replaceChildren(el("h1", {}, "Watchlist"), el("div", { class: "note error" }, vm.error || "Keine Daten"));
    return;
  }
  const message = el("div");
  const show = (m) => message.replaceChildren(el("div", { class: `note ${m.level}`, "data-tip": m.tip || null, style: "white-space:pre-line" }, m.text));
  const reload = () => {
    if (Object.keys(pending).length && !confirm("Es gibt ungespeicherte Änderungen. Verwerfen und neu laden?")) return;
    render(target);
  };

  // filters
  const search = el("input", { type: "search", placeholder: "Symbol, Name oder ISIN" });
  const selects = Object.fromEntries(["status", "strategy", "quality"].map((key) => [key, el("select", {}, vm.filters[key].map((v) => el("option", { value: v }, v)))]));
  let sortCol = 4, sortDown = false;
  const body = el("tbody");
  const head = el("tr");
  const table = el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, head), body));

  const drawHead = () => head.replaceChildren(...vm.headers.map((h, i) => el("th", {
    class: `sortable ${vm.numeric.includes(i) ? "num" : ""}`, onclick: () => { sortDown = sortCol === i ? !sortDown : false; sortCol = i; draw(); },
  }, h + (sortCol === i ? (sortDown ? " ▾" : " ▴") : ""))), el("th", {}, "Änderung"));

  const draw = () => {
    drawHead();
    const q = search.value.trim().toLowerCase();
    const rows = vm.rows.filter((r) => (!q || r.search.includes(q)) && ["status", "strategy", "quality"].every((k) => [vm.filters[k][0], r[k]].includes(selects[k].value)));
    rows.sort((a, b) => {
      const x = a.sort_keys[sortCol], y = b.sort_keys[sortCol];
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * (sortDown ? -1 : 1);
    });
    body.replaceChildren(...rows.map(rowNode));
  };

  const rowNode = (r) => {
    const edits = pending[r.security_id] || {};
    const tr = el("tr", { class: [r.level, Object.keys(edits).length ? "edited" : ""].join(" ") });
    r.cells.forEach((value, i) => {
      let content = cell(value);
      if (i === STRATEGY_COL && r.strategy_editable) {
        const current = "strategy" in edits ? edits.strategy : r.strategy_value;
        content = el("select", { onchange: (e) => mark(r, "strategy", e.target.value === "" ? null : e.target.value, r.strategy_value) },
          vm.strategy_options.map(([label, v]) => el("option", { value: v ?? "", selected: (v ?? null) === (current ?? null) }, label)));
      } else if (i === STRATEGY_COL) {
        content = el("span", { "data-tip": r.strategy_hint || null }, text(value));
      } else if (i === PRIORITY_COL) {
        const current = "priority" in edits ? edits.priority : r.priority;
        content = el("input", { type: "number", step: "1", value: current ?? "", style: "width:70px", onchange: (e) => mark(r, "priority", e.target.value === "" ? null : Number(e.target.value), r.priority) });
      } else if (i === SECTOR_COL && r.sector_editable) {
        const original = r.sector_source === "none" ? null : r.sector_value;
        const current = "sector" in edits ? edits.sector : original;
        content = el("select", { "data-tip": r.sector_tip, onchange: (e) => mark(r, "sector", e.target.value === "" ? null : e.target.value, original) },
          (r.sector_source === "public" ? [[`${r.sector_value} (öffentlich)`, r.sector_value], ...r.sector_options.filter(([, v]) => v !== r.sector_value)] : r.sector_options)
            .map(([label, v]) => el("option", { value: v ?? "", selected: (v ?? null) === (current ?? null) }, label)));
      }
      tr.append(el("td", { class: vm.numeric.includes(i) ? "num" : "" }, content));
    });
    const actions = el("td", {});
    if (Object.keys(edits).length) {
      actions.append(el("button", { class: "primary", onclick: () => apply(r) }, "Übernehmen"), " ", el("button", { onclick: () => { delete pending[r.security_id]; draw(); } }, "Verwerfen"));
    } else {
      actions.append(el("button", { class: "danger", onclick: () => remove(r) }, "Entfernen"));
    }
    tr.append(actions);
    return tr;
  };

  const mark = (r, field, value, original) => {
    const edits = pending[r.security_id] || {};
    if ((value ?? null) === (original ?? null)) delete edits[field]; else edits[field] = value;
    if (Object.keys(edits).length) pending[r.security_id] = edits; else delete pending[r.security_id];
    draw();
  };

  const apply = async (r) => {
    const result = await post(`/api/watchlist/${r.security_id}/edit`, pending[r.security_id]);
    show(result.message);
    delete pending[r.security_id];
    const keep = message.firstChild;
    await render(target);
    if (keep) target.querySelector("#wl-message").replaceChildren(keep);
  };

  const remove = async (r) => {
    if (!confirm(`${r.symbol} – ${r.name} von der Watchlist entfernen? Eine gehaltene Position bleibt unberührt.`)) return;
    const result = await post(`/api/watchlist/${r.security_id}/remove`);
    show(result);
    if (result.done) {
      const keep = message.firstChild;
      await render(target);
      if (keep) target.querySelector("#wl-message").replaceChildren(keep);
    }
  };

  search.addEventListener("input", draw);
  Object.values(selects).forEach((s) => s.addEventListener("change", draw));
  message.id = "wl-message";

  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Watchlist"), el("div", { class: "muted small" }, `${vm.summary} · Stand ${vm.refreshed}`)),
      el("div", { class: "right" }, search, selects.status, selects.strategy, selects.quality, el("button", { onclick: reload }, "Neu laden"))),
    vm.notes.length ? el("div", {}, vm.notes.map((n) => el("div", { class: "note warning" }, n))) : "",
    message,
    el("div", { class: "grid cols-2", style: "margin-bottom:16px" }, addPanel(show, target), onboardPanel(show, target)),
    panel(null, el("p", { class: "muted small" }, "Strategie, Priorität und Sektor werden in der Tabelle gewählt und erst mit „Übernehmen“ gespeichert. Eine Strategie erzeugt keine Order und keine Campaign."), table));
  draw();
}

function addPanel(show, target) {
  const identifier = el("input", { placeholder: "Symbol, ISIN, WKN oder ID" });
  const priority = el("input", { type: "number", step: "1", placeholder: "Priorität (optional)", style: "width:150px" });
  const hint = el("div");
  const run = async (dryRun) => {
    if (!identifier.value.trim()) return;
    const result = await post("/api/watchlist/add", { identifier: identifier.value.trim(), priority: priority.value === "" ? null : Number(priority.value), dry_run: dryRun });
    show(result);
    hint.replaceChildren(result.result === "SECURITY_NOT_FOUND"
      ? el("button", { onclick: () => { document.getElementById("onboard-symbol").value = identifier.value.trim(); document.getElementById("onboard-symbol").focus(); } }, "Neues Wertpapier aufnehmen")
      : "");
    if (result.done && !dryRun) render(target);
  };
  return panel("Zur Watchlist hinzufügen", el("p", { class: "muted small" }, "Nur Wertpapiere aus dem Security Master; „Prüfen“ löst nur auf und schreibt nichts."),
    el("div", { class: "head" }, identifier, priority, el("button", { onclick: () => run(true) }, "Prüfen"), el("button", { class: "primary", onclick: () => run(false) }, "Hinzufügen")), hint);
}

function onboardPanel(show, target) {
  const fields = [["symbol", "Symbol *"], ["name", "Name *"], ["isin", "ISIN"], ["wkn", "WKN"], ["exchange", "Börse"], ["currency", "Währung"], ["country", "Land"], ["yahoo", "Yahoo-Symbol"], ["fmp", "FMP-Symbol"]];
  const inputs = Object.fromEntries(fields.map(([k, label]) => [k, el("input", { placeholder: label, id: k === "symbol" ? "onboard-symbol" : null })]));
  const asset = el("select", {}, el("option", { value: "" }, "Art *"), el("option", { value: "Aktie" }, "Aktie"), el("option", { value: "ETF" }, "ETF"));
  const addWl = el("input", { type: "checkbox" });
  const prio = el("input", { type: "number", step: "1", placeholder: "Priorität", style: "width:110px" });
  const out = el("div");
  const create = el("button", { class: "primary", disabled: true }, "Anlegen");
  let checked = null;
  const form = () => ({ ...Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value])), asset_text: asset.value, add_to_watchlist: addWl.checked, priority: prio.value });
  const changed = () => { create.disabled = true; checked = null; };
  [...Object.values(inputs), asset, addWl, prio].forEach((i) => i.addEventListener("input", changed));
  const outcome = (o) => {
    out.replaceChildren(el("div", { class: `note ${o.level}`, "data-tip": o.tooltip || null }, o.text, o.lines && o.lines.length ? el("ul", {}, o.lines.map((l) => el("li", {}, l))) : ""),
      o.existing ? el("button", { onclick: async () => { const r = await post("/api/watchlist/add", { identifier: String(o.existing.security_id) }); show(r); if (r.done) render(target); } },
        `Vorhandenes ${o.existing.symbol || ""} zur Watchlist hinzufügen`) : "");
  };
  const check = async () => {
    const o = await post("/api/onboarding", { form: form(), create: false });
    outcome(o);
    checked = o.can_create ? JSON.stringify(form()) : null;
    create.disabled = !o.can_create;
  };
  create.addEventListener("click", async () => {
    if (checked !== JSON.stringify(form())) { create.disabled = true; return; }
    const o = await post("/api/onboarding", { form: form(), create: true });
    outcome(o);
    create.disabled = true;
    if (o.created) show({ level: "info", text: o.text });
  });
  return panel("Neues Wertpapier aufnehmen",
    el("p", { class: "muted small" }, "Legt genau ein Wertpapier aus diesen Angaben an; nichts wird recherchiert oder ergänzt. Erst prüfen, dann anlegen."),
    el("div", { style: "display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px" }, Object.values(inputs), asset),
    el("div", { class: "head", style: "margin-top:8px" }, el("label", {}, addWl, " nach Anlage zur Watchlist"), prio, el("button", { onclick: check }, "Prüfen"), create),
    out);
}


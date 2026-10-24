// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Sektoren: sector treemap and tables, and the tab "Einzelwertrisiko" (effective single-name exposure) with its own treemap
// (/api/views/sectors = the view models of the desktop page). Read-only; the risk guard is not changed here.

import { disposeCharts, el, echart, get, kv, panel, vmTable } from "./core.js";

export async function render(target) {
  const data = await get("/api/views/sectors");
  const s = data.sectors, e = data.effective;
  const tabs = el("div", { class: "seg" });
  const body = el("div");
  let tab = "sectors";
  const draw = () => {
    tabs.replaceChildren(...[["sectors", "Sektoren"], ["effective", "Einzelwertrisiko"]].map(([k, label]) => el("button", { class: k === tab ? "on" : "", onclick: () => { tab = k; draw(); } }, label)));
    disposeCharts();
    if (tab === "sectors") sectorsTab(body, s); else effectiveTab(body, e);
  };
  target.replaceChildren(el("div", { class: "head" }, el("div", {}, el("h1", {}, "Sektoren"), el("div", { class: "muted small" }, `Stand ${s.refreshed} · nur lesend; der Risk-Guard wird hier nicht geändert`)),
    el("div", { class: "right" }, tabs)), body);
  draw();
}

const tooltip = { backgroundColor: "#0b1512", borderColor: "#1f4a35", textStyle: { color: "#e3efe9" }, extraCssText: "white-space:pre-line;max-width:420px" };

function sectorsTab(body, s) {
  if (!s.available) { body.replaceChildren(el("div", { class: "note error" }, s.error || "Keine Daten")); return; }
  const map = el("div", { class: "chart map" });
  const table = (rows, levels) => ({ headers: [], rows, levels });
  body.replaceChildren(
    panel("Treemap (Fläche = Marktwert, Farbe = Tagesperformance)", map),
    el("div", { class: "grid cols-3", style: "margin-top:16px" },
      panel("Überblick", kv(s.summary), s.notes.length ? s.notes.map((n) => el("div", { class: "note warning" }, n)) : null),
      el("div", { class: "span-2" }, panel("Sektoren", vmTable({ ...table(s.sector_rows, s.sector_levels), headers: s.headers.sectors }, { numeric: [1, 2, 3, 4, 6] })))),
    el("div", { class: "grid", style: "margin-top:16px" },
      panel("Positionen und Beiträge", vmTable({ ...table(s.position_rows, []), headers: s.headers.positions }, { numeric: [3, 4, 5] })),
      panel("ETF-Look-through", vmTable({ ...table(s.etf_rows, s.etf_levels), headers: s.headers.etfs }, { numeric: [1] }))));
  echart(map, {
    tooltip: { ...tooltip, formatter: (p) => (p.data && p.data.tip ? p.data.tip.replace(/\n/g, "<br>") : p.name) },
    series: [{
      type: "treemap", roam: false, nodeClick: false, breadcrumb: { show: false }, width: "100%", height: "100%",
      upperLabel: { show: true, height: 22, color: "#e3efe9", fontWeight: 600 },
      label: { show: true, formatter: "{b}", color: "#fff", fontSize: 12 },
      itemStyle: { borderColor: "#050706", borderWidth: 2, gapWidth: 2 },
      levels: [{ itemStyle: { borderColor: "#1d2a25", borderWidth: 3, gapWidth: 3 } }, { itemStyle: { borderColor: "#050706", borderWidth: 1, gapWidth: 1 } }],
      data: s.sectors.map((g) => ({
        name: g.title, value: g.value, itemStyle: { color: "#0f1714" },
        children: g.positions.map((p) => ({ name: p.table_label, value: p.value, tip: p.tip, itemStyle: { color: p.color || "#3a4a44", borderType: p.lookthrough ? "dashed" : "solid" } })),
      })),
    }],
  });
}

function effectiveTab(body, e) {
  if (!e.available) { body.replaceChildren(el("div", { class: "note error" }, e.error || "Keine Daten")); return; }
  const map = el("div", { class: "chart map" });
  let sortCol = e.default_sort, down = true;
  const holder = el("div");
  const drawCompanies = () => {
    const order = e.company_rows.map((_, i) => i).sort((a, b) => {
      const x = e.company_keys[a][sortCol], y = e.company_keys[b][sortCol];
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * (down ? -1 : 1);
    });
    holder.replaceChildren(el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, e.headers.companies.map((h, i) => el("th", { class: `sortable ${e.numeric.includes(i) ? "num" : ""}`, onclick: () => { down = sortCol === i ? !down : true; sortCol = i; drawCompanies(); } },
        h + (sortCol === i ? (down ? " ▾" : " ▴") : ""))))),
      el("tbody", {}, order.map((r) => el("tr", { class: e.company_levels[r] || "" }, e.company_rows[r].map((v, i) => el("td", {
        class: `${e.numeric.includes(i) ? "num" : ""}`, style: e.highlight.includes(i) ? "color:#2ee88a;font-weight:600" : null, "data-tip": i === 0 ? e.companies[r] && e.companies[r].tip : null,
      }, typeof v === "object" && v ? v.t : v))))))));
  };
  body.replaceChildren(
    panel(e.headline || "Einzelwertrisiko", el("p", { class: "muted small" }, e.headline_detail || ""), map),
    el("div", { class: "grid cols-3", style: "margin-top:16px" },
      panel("Überblick", el("div", { class: "head" }, e.chips.map(([label, value, level]) => el("span", { class: `badge ${level === "warning" ? "warn" : level === "blocker" ? "bad" : "ok"}` }, `${label}: ${value}`))),
        kv(e.summary), e.notes.length ? e.notes.map((n) => el("div", { class: "note warning" }, n)) : null),
      el("div", { class: "span-2" }, panel("Unternehmen (direkt + über ETFs)", holder))),
    el("div", { class: "grid", style: "margin-top:16px" }, panel("ETFs", vmTable({ headers: e.headers.etfs, rows: e.etf_rows, levels: e.etf_levels }, { numeric: [1] }))));
  drawCompanies();
  echart(map, {
    tooltip: { ...tooltip, formatter: (p) => (p.data && p.data.tip ? p.data.tip.replace(/\n/g, "<br>") : p.name) },
    series: [{
      type: "treemap", roam: false, nodeClick: false, breadcrumb: { show: false }, width: "100%", height: "100%",
      label: { show: true, formatter: "{b}", color: "#fff", fontSize: 12 }, itemStyle: { borderColor: "#050706", borderWidth: 2, gapWidth: 2 },
      data: e.tiles.map((t) => ({ name: t.label, value: t.value, tip: t.tip, itemStyle: { color: t.color || "#3a4a44", borderType: t.kind === "company" ? "solid" : "dashed" } })),
    }],
  });
}

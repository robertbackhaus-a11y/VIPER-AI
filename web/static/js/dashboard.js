// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Dashboard: portfolio figures (/api/portfolio), history (/api/portfolio/history), allocation, top/flop, and the planner block of the
// desktop dashboard (/api/views/dashboard). Every figure comes from the engine; this file only selects and formats.

import { DASH, dataTable, day, echart, el, eur, get, kv, notes, panel, pct, read, signed, tone, vmTable } from "./core.js";


export async function render(target) {
  const refresh = new URLSearchParams(location.hash.split("?")[1] || "").get("refresh") === "1";
  const [portfolio, history, vm, sectors] = await Promise.all([
    get("/api/portfolio"), get("/api/portfolio/history"), get(`/api/views/dashboard${refresh ? "?refresh=true" : ""}`), read("get_sector_overview").catch(() => null),
  ]);
  const t = portfolio.totals;
  const marketDay = portfolio.positions.map((p) => p.day_change_date).filter(Boolean).sort().pop();

  const head = el("div", { class: "head" },
    el("div", {}, el("h1", {}, "Dashboard"),
      el("div", { class: "muted small" }, `Bewertung ${day(portfolio.as_of)} · Marktdaten bis ${day(marketDay)} · alle Werte in ${portfolio.base_currency} · `,
        el("span", { "data-tip": "Depotwert: Stück × letzter Kurs-Snapshot × EZB-Kurs (build_portfolio_context). Einstandswert: Durchschnittskosten aus den Transaktionen. Tagesänderung: letzter gegen vorletzten Tagesschluss, ohne Währungseffekt." }, "Berechnungsbasis"))),
    el("div", { class: "right" }, el("button", { onclick: () => { location.hash = "#/dashboard?refresh=1"; } }, "Neu berechnen")));

  const valuationNote = portfolio.valuation_status !== "available"
    ? el("div", { class: "note warning" }, "Bewertung unvollständig: ", portfolio.valuation_details.join("; ")) : null;

  const kpis = el("div", { class: "kpis" },
    kpi("Depotwert", eur(t.market_value_eur), [el("span", { class: tone(t.day_change_eur) }, `${signed(t.day_change_eur, (v) => eur(v))} · ${signed(t.day_change_pct, (v) => pct(v, 2))}`), " heute",
      t.day_coverage_pct !== null && t.day_coverage_pct < 100 ? ` (Abdeckung ${pct(t.day_coverage_pct, 0)})` : ""], true),
    kpi("Investiert (Einstand)", eur(t.cost_basis_eur), "Kostenbasis der offenen Positionen"),
    kpi("Unrealisiert", el("span", { class: tone(t.unrealized_eur) }, signed(t.unrealized_eur, (v) => eur(v))), el("span", { class: tone(t.unrealized_pct) }, signed(t.unrealized_pct, (v) => pct(v)))),
    kpi("Realisiert (gespeichert)", eur(t.realized_eur), el("span", { "data-tip": "Summe realized_gain der Tabelle positions; ältere Werte stammen teils aus Parqet." }, "Herkunft")),
    kpi("Dividenden netto", eur(t.dividends_net_eur), "nach Steuern und Gebühren"),
    kpi("Cash", eur(t.cash_eur), t.cash_as_of ? `Stand ${day(t.cash_as_of)}` : "kein Kapitalstand"),
  );

  const chartBox = el("div", { class: "chart" });
  const seg = el("div", { class: "seg" });
  const perfBox = el("div");
  const missing = Object.entries(history.unvalued_days || {}).slice(0, 6).map(([s, n]) => `${s} (${n} Tage)`).join(", ");
  const fxFrom = Object.entries(history.fx_from || {}).map(([c, d]) => `${c} ab ${day(d)}`).join(", ");
  const historyPanel = panel(null,
    el("div", { class: "head" }, el("h2", {}, "Depot- und Kapitalentwicklung"), el("div", { class: "right" }, seg)),
    chartBox, perfBox,
    el("p", { class: "muted small" }, history.first_valued_date
      ? `Depotwert ab ${day(history.first_valued_date)} (erster Tag, an dem jede gehaltene Position einen Schlusskurs und einen EZB-Kurs hat; Schlusskurse × EZB-Kurs des Tages). `
        + `Davor zeigt der Chart nur das investierte Kapital und eine Lücke beim Depotwert – nichts wird geschätzt. Fehlende Kurse: ${missing || "—"}. EZB-Kurse: ${fxFrom || "—"}.`
      : "Kein Tag vollständig bewertbar; es wird nur das investierte Kapital gezeigt."));
  let range = "MAX";
  const drawHistory = () => {
    seg.replaceChildren(...history.periods.map((p) => el("button", { class: p.period === range ? "on" : "", onclick: () => { range = p.period; drawHistory(); } }, p.period)));
    const period = history.periods.find((p) => p.period === range);
    historyChart(chartBox, history.points.filter((p) => p.date >= (range === "MAX" ? history.points[0].date : period.start)));
    perfBox.replaceChildren(perfLine(period));
  };

  const allocation = el("div", { class: "chart small" });
  const strategy = el("div", { class: "chart small" });
  const sector = el("div", { class: "chart small" });

  const valued = portfolio.positions.filter((p) => p.day_change_pct !== null && p.day_change_pct !== undefined);
  const byDay = [...valued].sort((a, b) => b.day_change_pct - a.day_change_pct);
  const byResult = portfolio.positions.filter((p) => p.unrealized_pct !== null).sort((a, b) => b.unrealized_pct - a.unrealized_pct);
  const mover = (p, key, fmt) => el("tr", { class: "click", onclick: () => { location.hash = `#/position/${p.security_id}`; } },
    el("td", {}, p.symbol), el("td", { class: "muted" }, short(p.name)), el("td", { class: `num ${tone(p[key])}` }, signed(p[key], fmt)));
  const movers = (title, rows, key, fmt) => panel(title, rows.length ? el("table", {}, el("tbody", {}, rows.map((p) => mover(p, key, (v) => fmt(v))))) : el("p", { class: "muted" }, DASH));

  const positions = dataTable(portfolio.positions, [
    { key: "symbol", label: "Symbol" },
    { key: "name", label: "Name", fmt: (v) => short(v) },
    { key: "market_value_eur", label: "Marktwert", num: true, fmt: (v) => eur(v) },
    { key: "weight_pct", label: "Anteil", num: true, fmt: (v) => pct(v) },
    { key: "day_change_pct", label: "Heute", num: true, fmt: (v) => signed(v, (x) => pct(x, 2)), cls: tone },
    { key: "unrealized_eur", label: "G/V", num: true, fmt: (v) => signed(v, (x) => eur(x)), cls: tone },
    { key: "unrealized_pct", label: "G/V %", num: true, fmt: (v) => signed(v, (x) => pct(x)), cls: tone },
  ], { sortKey: "market_value_eur", onRow: (r) => { location.hash = `#/position/${r.security_id}`; } });

  const engine = vm.available ? [
    el("div", { class: "span-2" }, panel("Planner (Simulation, keine Orders)", kv(vm.planner), el("h3", {}, "Geplante Entries"), vmTable(vm.planned, { numeric: [2, 3, 4, 5, 6, 7] }),
      el("h3", {}, "Zurückgestellte Entries"), vmTable(vm.deferred))),
    panel("Portfolio laut Engine", kv(vm.portfolio), vm.issues.length ? [el("h3", {}, "Hinweise"), ...notes(vm.issues)] : null),
  ] : [panel("Planner", el("div", { class: "note error" }, vm.error || "Keine Daten"))];

  target.replaceChildren(head, valuationNote || "", kpis,
    el("div", { class: "grid cols-3" },
      el("div", { class: "span-2" }, historyPanel),
      panel("Allokation nach Position", allocation),
      panel("Strategie", strategy),
      panel("Sektoren", sector),
      el("div", { class: "grid" }, movers("Top heute", byDay.slice(0, 3), "day_change_pct", (v) => pct(v, 2)),
        movers("Flop heute", byDay.slice(-3).reverse(), "day_change_pct", (v) => pct(v, 2))),
      el("div", { class: "grid" }, movers("Beste Positionen (G/V %)", byResult.slice(0, 3), "unrealized_pct", (v) => pct(v)),
        movers("Schwächste Positionen (G/V %)", byResult.slice(-3).reverse(), "unrealized_pct", (v) => pct(v))),
      el("div", { class: "span-2" }, panel("Positionen", positions)),
      ...engine));

  drawHistory();
  donut(allocation, portfolio.positions.filter((p) => p.market_value_eur).map((p) => ({ name: p.symbol, value: round(p.market_value_eur) })));
  donut(strategy, [["Swing", t.swing_weight_pct], ["Long-Term", t.long_term_weight_pct]].filter(([, v]) => v !== null && v !== undefined)
    .map(([name, w]) => ({ name, value: round(w) })), (p) => `${p.name}<br>${pct(p.value)} des Depotwerts`);
  if (sectors && sectors.ok !== false && Array.isArray(sectors.sectors)) {
    donut(sector, sectors.sectors.map((s) => ({ name: s.kind === "unassigned" ? "Nicht zugeordnet" : s.kind === "etf_category" ? `${s.label} (ETF)` : s.label, value: round(s.market_value_eur) })));
  } else {
    sector.replaceChildren(el("p", { class: "muted" }, "Sektorübersicht nicht verfügbar"));
  }
}

function kpi(label, value, sub, main = false) {
  return el("div", { class: `kpi ${main ? "main" : ""}` }, el("div", { class: "label" }, label), el("div", { class: "value" }, value), el("div", { class: "sub" }, sub));
}

const short = (name) => (name && name.length > 34 ? `${name.slice(0, 32)}…` : name || DASH);
const round = (v) => Math.round(v * 100) / 100;

function perfLine(p) {
  if (!p) return "";
  if (!p.available) return el("div", { class: "note warning" }, `${p.period}: Performance nicht berechenbar – ${p.reason}.`);
  return el("div", { class: "kpis", style: "margin:10px 0 0" },
    mini("Zeitraum", `${day(p.start)} – ${day(p.end)}`),
    mini("Depotwert-Änderung", el("span", { class: tone(p.value_change_eur) }, signed(p.value_change_eur, (v) => eur(v)))),
    mini("Nettozuflüsse (Käufe − Verkäufe)", signed(p.net_flows_eur, (v) => eur(v))),
    mini("Dividenden", eur(p.dividends_eur)),
    mini("Ergebnis ohne Zuflüsse", el("span", { class: tone(p.result_eur) }, signed(p.result_eur, (v) => eur(v)))),
    mini("Rendite (zeitgewichtet)", el("span", { class: tone(p.twr_pct) }, signed(p.twr_pct, (v) => pct(v, 2)))));
}

const mini = (label, value) => el("div", { class: "kpi", style: "padding:8px 12px" }, el("div", { class: "label" }, label), el("div", { class: "value", style: "font-size:1rem" }, value));

function historyChart(node, points) {
  const chart = node._chart || (node._chart = echart(node, {}));
  chart.setOption({
    grid: { left: 70, right: 20, top: 30, bottom: 30 },
    legend: { top: 0, textStyle: { color: "#8ba198" } },
    tooltip: {
      trigger: "axis", backgroundColor: "#0b1512", borderColor: "#1f4a35", textStyle: { color: "#e3efe9" },
      valueFormatter: (v) => (v === null || v === undefined ? "nicht bewertbar" : eur(v)),
    },
    xAxis: { type: "category", data: points.map((p) => day(p.date)), axisLine: { lineStyle: { color: "#1d2a25" } }, axisLabel: { color: "#8ba198" } },
    yAxis: { type: "value", scale: true, splitLine: { lineStyle: { color: "#13201b" } }, axisLabel: { color: "#8ba198", formatter: (v) => `${(v / 1000).toLocaleString("de-DE")} k` } },
    series: [
      { name: "Depotwert", type: "line", showSymbol: false, connectNulls: false, data: points.map((p) => p.market_value_eur), lineStyle: { width: 2, color: "#2ee88a" },
        itemStyle: { color: "#2ee88a" }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: "rgba(46,232,138,.28)" }, { offset: 1, color: "rgba(46,232,138,0)" }]) } },
      { name: "Investiertes Kapital", type: "line", step: "end", showSymbol: false, data: points.map((p) => p.invested_eur), lineStyle: { width: 1.5, type: "dashed", color: "#6cb6ff" }, itemStyle: { color: "#6cb6ff" } },
    ],
  }, true);
}

function donut(node, data, formatter = (p) => `${p.name}<br>${eur(p.value)} (${pct(p.percent)})`) {
  echart(node, {
    tooltip: { trigger: "item", backgroundColor: "#0b1512", borderColor: "#1f4a35", textStyle: { color: "#e3efe9" }, formatter },
    legend: { type: "scroll", bottom: 0, left: "center", textStyle: { color: "#8ba198" }, pageTextStyle: { color: "#8ba198" } },
    series: [{ type: "pie", radius: ["45%", "70%"], center: ["50%", "44%"], avoidLabelOverlap: true, label: { show: false }, itemStyle: { borderColor: "#0c1210", borderWidth: 2 }, data }],
  });
}

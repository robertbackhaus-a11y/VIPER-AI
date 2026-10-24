// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Portfolio (#/portfolio), position detail with price chart and transactions (#/position/<security_id>) and the transaction history
// (#/transactions). Data: /api/portfolio, /api/securities/<id>/prices, /api/transactions.

import { DASH, dataTable, day, el, eur, get, kv, num, panel, pct, qty, signed, tone } from "./core.js";

export async function render(target, arg, name) {
  if (name === "position") return position(target, Number(arg));
  if (name === "transactions") return transactions(target);
  return holdings(target);
}

const TYPE_TEXT = { BUY: "Kauf", SELL: "Verkauf", DIVIDEND: "Dividende", COST: "Kosten", TRANSFERIN: "Einlieferung", TRANSFEROUT: "Auslieferung" };

async function holdings(target) {
  const p = await get("/api/portfolio");
  const t = p.totals;
  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Portfolio"), el("div", { class: "muted small" }, `${p.positions.length} offene Positionen · Bewertung ${day(p.as_of)} · EUR`))),
    el("div", { class: "kpis" },
      box("Depotwert", eur(t.market_value_eur)), box("Einstandswert", eur(t.cost_basis_eur)),
      box("Unrealisiert", el("span", { class: tone(t.unrealized_eur) }, `${signed(t.unrealized_eur, (v) => eur(v))} (${signed(t.unrealized_pct, (v) => pct(v))})`)),
      box("Swing / Long-Term", `${pct(t.swing_weight_pct)} / ${pct(t.long_term_weight_pct)}`)),
    panel(null, dataTable(p.positions, [
      { key: "symbol", label: "Symbol" },
      { key: "name", label: "Name" },
      { key: "strategy", label: "Strategie", fmt: strategyText },
      { key: "quantity", label: "Stück", num: true, fmt: qty },
      { key: "price_native", label: "Kurs", num: true, fmt: (v, r) => (v === null ? DASH : `${num(v)} ${r.price_currency || ""}`) },
      { key: "market_value_eur", label: "Marktwert", num: true, fmt: (v) => eur(v) },
      { key: "cost_basis_eur", label: "Einstand", num: true, fmt: (v) => eur(v) },
      { key: "unrealized_eur", label: "G/V", num: true, fmt: (v) => signed(v, (x) => eur(x)), cls: tone },
      { key: "unrealized_pct", label: "G/V %", num: true, fmt: (v) => signed(v, (x) => pct(x)), cls: tone },
      { key: "weight_pct", label: "Anteil", num: true, fmt: (v) => pct(v) },
      { key: "day_change_pct", label: "Heute", num: true, fmt: (v) => signed(v, (x) => pct(x, 2)), cls: tone },
      { key: "valuation_status", label: "Bewertung", fmt: (v, r) => el("span", { class: `badge ${v === "available" ? "ok" : "bad"}`, "data-tip": (r.valuation_details || []).join("\n") || null }, v === "available" ? "OK" : v) },
    ], { sortKey: "market_value_eur", onRow: (r) => { location.hash = `#/position/${r.security_id}`; } })),
  );
}

async function position(target, id) {
  const [p, prices, txs] = await Promise.all([get("/api/portfolio"), get(`/api/securities/${id}/prices`), get(`/api/transactions?security_id=${id}`)]);
  const sec = prices.security;
  const held = p.positions.find((x) => x.security_id === id);
  const chartBox = el("div", { class: "chart" });
  const info = held ? kv([
    ["Stück", qty(held.quantity)], ["Kurs", held.price_native === null ? DASH : `${num(held.price_native)} ${held.price_currency}`], ["Kurs in EUR", eur(held.price_eur, 2)],
    ["Marktwert", eur(held.market_value_eur)], ["Einstandswert", eur(held.cost_basis_eur)],
    ["Unrealisiert", el("span", { class: tone(held.unrealized_eur) }, `${signed(held.unrealized_eur, (v) => eur(v))} (${signed(held.unrealized_pct, (v) => pct(v))})`)],
    ["Anteil am Depot", pct(held.weight_pct)], ["Tagesänderung", el("span", { class: tone(held.day_change_pct) }, `${signed(held.day_change_pct, (v) => pct(v, 2))} (${day(held.day_change_date)})`)],
    ["Strategie", strategyText(held.strategy)], ["Erste Transaktion", day(held.first_transaction_at)],
  ]) : el("p", { class: "muted" }, "Keine offene Position.");
  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, `${sec.symbol || DASH} · ${sec.name}`),
      el("div", { class: "muted small" }, [sec.isin, sec.asset_type, prices.currency ? `Handelswährung ${prices.currency}` : null].filter(Boolean).join(" · "))),
      el("div", { class: "right" }, el("a", { href: "#/portfolio" }, "← Portfolio"))),
    el("div", { class: "grid cols-3" },
      el("div", { class: "span-2" }, panel(`Kursverlauf (Tagesschluss, ${prices.currency || DASH})`, chartBox,
        el("p", { class: "muted small" }, prices.bars.length ? `${prices.bars.length} Tagesbalken, ${day(prices.bars[0].trade_date)} bis ${day(prices.bars[prices.bars.length - 1].trade_date)}` : "Keine Kursdaten"))),
      panel("Position", info),
      el("div", { class: "span-2" }, panel("Transaktionen", txTable(txs, false)))),
  );
  if (prices.bars.length) priceChart(chartBox, prices.bars, txs);
}

async function transactions(target) {
  const txs = await get("/api/transactions");
  const filter = el("input", { type: "search", placeholder: "Symbol oder Name filtern" });
  const type = el("select", {}, el("option", { value: "" }, "alle Arten"), Object.entries(TYPE_TEXT).map(([k, v]) => el("option", { value: k }, v)));
  const holder = el("div");
  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    holder.replaceChildren(txTable(txs.filter((x) => (!type.value || x.transaction_type === type.value) && (!q || `${x.symbol} ${x.name}`.toLowerCase().includes(q))), true));
  };
  filter.addEventListener("input", draw);
  type.addEventListener("change", draw);
  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Transaktionen"), el("div", { class: "muted small" }, `${txs.length} gespeicherte Transaktionen (neueste zuerst)`)),
      el("div", { class: "right" }, filter, type)),
    panel(null, holder));
  draw();
}

function txTable(rows, withSecurity) {
  const columns = [
    { key: "transaction_date", label: "Datum", fmt: day },
    ...(withSecurity ? [{ key: "symbol", label: "Symbol", fmt: (v, r) => el("a", { href: `#/position/${r.security_id}` }, v || String(r.security_id)) }, { key: "name", label: "Name" }] : []),
    { key: "transaction_type", label: "Art", fmt: (v) => TYPE_TEXT[v] || v },
    { key: "shares", label: "Stück", num: true, fmt: qty },
    { key: "price", label: "Preis", num: true, fmt: (v) => num(v) },
    { key: "amount", label: "Betrag", num: true, fmt: (v) => num(v) },
    { key: "fees", label: "Gebühren", num: true, fmt: (v) => num(v) },
    { key: "taxes", label: "Steuern", num: true, fmt: (v) => num(v) },
    { key: "currency", label: "Währung" },
    { key: "broker", label: "Broker" },
  ];
  return dataTable(rows, columns, { sortKey: "transaction_date", empty: "Keine Transaktionen" });
}

function priceChart(node, bars, txs) {
  const chart = LightweightCharts.createChart(node, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#8ba198", fontFamily: "Segoe UI, system-ui, sans-serif" },
    grid: { vertLines: { color: "#13201b" }, horzLines: { color: "#13201b" } },
    rightPriceScale: { borderColor: "#1d2a25" }, timeScale: { borderColor: "#1d2a25" },
    localization: { locale: "de-DE", priceFormatter: (v) => num(v) },
  });
  const ohlc = bars.every((b) => b.open !== null && b.high !== null && b.low !== null);
  const series = ohlc
    ? chart.addCandlestickSeries({ upColor: "#2ee88a", downColor: "#ff5f6d", wickUpColor: "#2ee88a", wickDownColor: "#ff5f6d", borderVisible: false })
    : chart.addAreaSeries({ lineColor: "#2ee88a", topColor: "rgba(46,232,138,.3)", bottomColor: "rgba(46,232,138,0)", lineWidth: 2 });
  series.setData(bars.map((b) => (ohlc ? { time: b.trade_date, open: b.open, high: b.high, low: b.low, close: b.close } : { time: b.trade_date, value: b.close })));
  const first = bars[0].trade_date;
  const markers = txs.filter((x) => ["BUY", "SELL", "TRANSFERIN", "TRANSFEROUT"].includes(x.transaction_type) && x.transaction_date.slice(0, 10) >= first)
    .map((x) => {
      const buy = x.transaction_type === "BUY" || x.transaction_type === "TRANSFERIN";
      return { time: x.transaction_date.slice(0, 10), position: buy ? "belowBar" : "aboveBar", color: buy ? "#6cb6ff" : "#f2c14e", shape: buy ? "arrowUp" : "arrowDown", text: `${TYPE_TEXT[x.transaction_type]} ${qty(x.shares)}` };
    })
    .sort((a, b) => (a.time < b.time ? -1 : 1));
  if (markers.length) series.setMarkers(markers);
  chart.timeScale().fitContent();
}

const strategyText = (v) => ({ swing: "Swing", long_term: "Long-Term", unknown: "unbekannt" }[v] || v || DASH);
const box = (label, value) => el("div", { class: "kpi" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value));

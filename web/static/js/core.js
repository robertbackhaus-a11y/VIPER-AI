// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Shared helpers of the pages: API calls, number formatting (display only, never a calculation), DOM building, tables, tooltips.

export const DASH = "—";

export async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const text = await response.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = { ok: false, message: text }; }
  if (!response.ok) {
    const message = (data && (data.message || data.detail || data.error)) || `HTTP ${response.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return data;
}
export const get = (path) => api(path);
export const post = (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });
export const read = (tool, args = {}) => post(`/api/read/${tool}`, args);
export const action = (tool, args = {}) => post(`/api/action/${tool}`, args);

// -- formatting ----------------------------------------------------------------------------------------------------------------------
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
export function num(v, digits = 2) {
  return isNum(v) ? v.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : DASH;
}
export const eur = (v, digits = 0) => (isNum(v) ? `${num(v, digits)} €` : DASH);
export const pct = (v, digits = 1) => (isNum(v) ? `${num(v, digits)} %` : DASH);
export const signed = (v, fmt) => (isNum(v) ? (v > 0 ? "+" : "") + fmt(v) : DASH);
export const qty = (v) => (isNum(v) ? num(v, Number.isInteger(v) ? 0 : 2) : DASH);
export const tone = (v) => (isNum(v) ? (v > 0 ? "up" : v < 0 ? "down" : "") : "");
export function day(text) {
  if (!text) return DASH;
  const d = String(text).slice(0, 10).split("-");
  return d.length === 3 ? `${d[2]}.${d[1]}.${d[0]}` : String(text);
}

// -- DOM -------------------------------------------------------------------------------------------------------------------------------
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** A view-model cell: plain text or {t, tip}. */
export function cell(value) {
  if (value && typeof value === "object" && "t" in value) return el("span", { "data-tip": value.tip || null }, value.t);
  return document.createTextNode(value === null || value === undefined || value === "" ? DASH : String(value));
}
export const text = (value) => (value && typeof value === "object" && "t" in value ? value.t : value ?? "");

export function kv(pairs) {
  return el("dl", { class: "kv" }, pairs.map(([k, v]) => [el("dt", {}, cell(k)), el("dd", {}, v instanceof Node ? v : cell(v))]));
}

export function notes(items) {
  return items.map((item) => {
    const [level, message] = Array.isArray(item) ? item : ["info", item];
    return el("div", { class: `note ${level}` }, cell(message));
  });
}

/** A view-model Table {headers, rows, levels} (optionally numeric columns by index). */
export function vmTable(table, { numeric = [], onRow = null, empty = "Keine Einträge" } = {}) {
  if (!table || !table.rows || !table.rows.length) return el("p", { class: "muted" }, empty);
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, table.headers.map((h, i) => el("th", { class: numeric.includes(i) ? "num" : "" }, h)))),
    el("tbody", {}, table.rows.map((row, r) => el("tr", { class: [(table.levels || [])[r] || "", onRow ? "click" : ""].join(" "), onclick: onRow ? () => onRow(r) : null },
      row.map((v, i) => el("td", { class: numeric.includes(i) ? "num" : "" }, cell(v))))))));
}

/** A sortable table from column specs {key, label, fmt, num, cls, sort}. */
export function dataTable(rows, columns, { onRow = null, sortKey = null, desc = true, rowClass = null, empty = "Keine Einträge" } = {}) {
  const wrap = el("div", { class: "table-wrap" });
  let key = sortKey, down = desc;
  const render = () => {
    const sorted = [...rows];
    if (key) {
      const col = columns.find((c) => c.key === key);
      const pick = col && col.sort ? col.sort : (r) => r[key];
      sorted.sort((a, b) => {
        const x = pick(a), y = pick(b);
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (x < y ? -1 : x > y ? 1 : 0) * (down ? -1 : 1);
      });
    }
    wrap.replaceChildren(rows.length ? el("table", {},
      el("thead", {}, el("tr", {}, columns.map((c) => el("th", {
        class: `sortable ${c.num ? "num" : ""}`,
        onclick: () => { if (key === c.key) down = !down; else { key = c.key; down = true; } render(); },
      }, c.label + (key === c.key ? (down ? " ▾" : " ▴") : ""))))),
      el("tbody", {}, sorted.map((r) => el("tr", { class: [onRow ? "click" : "", rowClass ? rowClass(r) : ""].join(" "), onclick: onRow ? () => onRow(r) : null },
        columns.map((c) => {
          const v = c.fmt ? c.fmt(r[c.key], r) : r[c.key];
          return el("td", { class: [c.num ? "num" : "", c.cls ? c.cls(r[c.key], r) : ""].join(" ") }, v instanceof Node ? v : cell(v));
        }))))) : el("p", { class: "muted" }, empty));
  };
  render();
  return wrap;
}

export function panel(title, ...children) {
  return el("section", { class: "panel" }, title ? el("h2", {}, title) : null, ...children);
}

export function loading(target, label = "lädt …") {
  target.replaceChildren(el("div", { class: "loading" }, label));
}

export function failure(target, error) {
  target.replaceChildren(el("div", { class: "note error" }, `Fehler: ${error.message || error}`));
}

// -- tooltips (rich texts of the reason codes arrive as plain text with line breaks) -------------------------------------------
const tipBox = () => document.getElementById("tip");
document.addEventListener("mouseover", (event) => {
  const target = event.target.closest && event.target.closest("[data-tip]");
  const box = tipBox();
  if (!box) return;
  if (!target || !target.dataset.tip) { box.hidden = true; return; }
  box.textContent = target.dataset.tip;
  box.hidden = false;
});
document.addEventListener("mousemove", (event) => {
  const box = tipBox();
  if (!box || box.hidden) return;
  const x = Math.min(event.clientX + 14, window.innerWidth - box.offsetWidth - 8);
  const y = event.clientY + 16 + box.offsetHeight > window.innerHeight ? event.clientY - box.offsetHeight - 10 : event.clientY + 16;
  box.style.left = `${x}px`;
  box.style.top = `${y}px`;
});

// -- charts ----------------------------------------------------------------------------------------------------------------------------
export const COLORS = ["#2ee88a", "#19c9a3", "#6cb6ff", "#b48cff", "#f2c14e", "#ff8f5c", "#ff5f6d", "#4dd4e8", "#9fe870", "#e66bd1", "#8ba198", "#5c7cff"];
// Every chart follows the size of its box (window, collapsed navigation, tab switch) through a ResizeObserver.
const charts = new Map();
export function echart(node, option) {
  const chart = echarts.init(node, null, { renderer: "canvas" });
  chart.setOption({ backgroundColor: "transparent", textStyle: { color: "#e3efe9", fontFamily: "Segoe UI, system-ui, sans-serif" }, color: COLORS, ...option });
  const observer = new ResizeObserver(() => { if (node.isConnected && node.clientWidth) chart.resize(); });
  observer.observe(node);
  charts.set(chart, observer);
  return chart;
}
export function disposeCharts() {
  for (const [chart, observer] of charts) { observer.disconnect(); chart.dispose(); }
  charts.clear();
}

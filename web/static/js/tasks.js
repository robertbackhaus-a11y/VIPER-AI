// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Tasks: the seven scheduled Trading tasks with data stand (/api/views/tasks), "Jetzt ausführen" (starts the existing scheduled task after a
// confirmation and follows it until the scheduler reports its end), the end of the log, and the stand of the ETF datasets.

import { el, get, panel, post, vmTable } from "./core.js";

let timer = null;

export async function render(target) {
  clearTimeout(timer);
  const status = el("div");
  const logBox = el("pre", { class: "small", style: "max-height:420px;overflow:auto;white-space:pre-wrap;background:#08100d;padding:10px;border-radius:8px;border:1px solid #1d2a25" }, "Log: Task wählen");
  const logTitle = el("h2", {}, "Log");
  const tableBox = el("div");
  const etfBox = el("div");
  const summary = el("div", { class: "muted small" });

  const showLog = async (name) => {
    const tail = await get(`/api/tasks/${encodeURIComponent(name)}/log`);
    logTitle.textContent = `Log: ${name}`;
    logBox.textContent = tail.available ? (tail.truncated ? "… (gekürzt)\n" : "") + tail.text : tail.note || "Log nicht verfügbar";
    logBox.scrollTop = logBox.scrollHeight;
  };

  const load = async () => {
    const vm = await get("/api/views/tasks");
    if (!document.body.contains(tableBox)) return;  // the page was left
    summary.textContent = `${vm.summary} · Stand ${vm.refreshed}`;
    status.replaceChildren(...vm.runs.map((r) => el("div", { class: `note ${r.level}` }, r.text)));
    for (const r of vm.runs) if (r.finished) showLog(r.name);
    tableBox.replaceChildren(el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Task", "Rhythmus", "Status", "Datenstand", "Letzter Erfolg", "Nächster Lauf", "Dauer", "Aktion", "Log"].map((h) => el("th", {}, h)))),
      el("tbody", {}, vm.rows.map((r) => el("tr", { class: r.level },
        el("td", { "data-tip": r.details || null }, r.title || r.name),
        el("td", {}, r.rhythm),
        el("td", { class: "wrap", "data-tip": r.last_error || null }, r.status_line || r.status),
        el("td", { class: r.data_level === "blocker" ? "down" : r.data_level === "warning" ? "" : "" , style: r.data_level === "warning" ? "color:#f2c14e" : null }, r.data_stand),
        el("td", {}, r.last_success), el("td", {}, r.next_run), el("td", {}, r.duration),
        el("td", {}, el("button", { disabled: !r.can_run, "data-tip": r.run_hint || null, onclick: () => run(r) }, "Jetzt ausführen")),
        el("td", {}, el("button", { onclick: () => showLog(r.name) }, "Log"))))))));
    etfBox.replaceChildren(vmTable(vm.etf, { empty: "Kein ETF-Datenstand" }));
    if (vm.runs.some((r) => !r.finished)) timer = setTimeout(load, vm.poll_s * 1000);
  };

  const run = async (r) => {
    const lines = [`${r.title || r.name} jetzt starten?`, "", "Der geplante Task läuft wie zur geplanten Zeit (Netzwerk, Schreiben in die Datenbank, gleiches Log)."];
    if (r.run_warning) lines.push("", r.run_warning);
    if (r.dependency_lines && r.dependency_lines.length) lines.push("", ...r.dependency_lines);
    if (!confirm(lines.join("\n"))) return;
    const result = await post(`/api/tasks/${encodeURIComponent(r.name)}/run`);
    status.replaceChildren(el("div", { class: `note ${result.ok ? "info" : "blocker"}` }, result.message + (result.ok ? " – warte auf den Scheduler …" : "")));
    if (result.ok) timer = setTimeout(load, 2000);
  };

  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Tasks"), summary), el("div", { class: "right" }, el("button", { onclick: load }, "Neu laden"))),
    status,
    panel(null, tableBox),
    el("div", { class: "grid cols-2", style: "margin-top:16px" }, panel("ETF-Datenstand", etfBox), panel(null, logTitle, logBox)));
  await load();
}

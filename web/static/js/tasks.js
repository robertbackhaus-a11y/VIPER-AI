// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Aufgaben: the seven VIPER tasks (/api/views/tasks). The definition is VIPER's (schedule, enabled); the scheduler of the operating system only runs
// what "Deployen" puts there. Per task: edit time/day, enable/disable, delete or restore, "Jetzt ausführen" (after a confirmation, through the scheduler,
// followed until it reports the end), the end of the log; below, the stand of the ETF datasets.

import { el, get, panel, post, vmTable } from "./core.js";

let timer = null;
const DAY_TEXT = { daily: "täglich", monday: "Mo", tuesday: "Di", wednesday: "Mi", thursday: "Do", friday: "Fr", saturday: "Sa", sunday: "So" };

export async function render(target) {
  clearTimeout(timer);
  const status = el("div");
  const logBox = el("pre", { class: "small", style: "max-height:420px;overflow:auto;white-space:pre-wrap;background:#08100d;padding:10px;border-radius:8px;border:1px solid #1d2a25" }, "Log: Task wählen");
  const logTitle = el("h2", {}, "Log");
  const tableBox = el("div");
  const etfBox = el("div");
  const summary = el("div", { class: "muted small" });
  const info = el("div");

  const say = (result) => status.replaceChildren(el("div", { class: `note ${result.ok ? "info" : "blocker"}` }, result.message || (result.ok ? "erledigt" : "fehlgeschlagen")));

  const showLog = async (name) => {
    const tail = await get(`/api/tasks/${encodeURIComponent(name)}/log`);
    logTitle.textContent = `Log: ${name}`;
    logBox.textContent = tail.available ? (tail.truncated ? "… (gekürzt)\n" : "") + tail.text : tail.note || "Log nicht verfügbar";
    logBox.scrollTop = logBox.scrollHeight;
  };

  const change = async (path, body, question) => {
    if (question && !confirm(question)) return;
    say(await post(path, body));
    await load();
  };

  const editor = (r, days) => {
    const times = el("input", { value: r.times.join(", "), size: 12, title: "Uhrzeit(en) HH:MM, mehrere mit Komma" });
    const day = el("select", {}, days.map((d) => el("option", { value: d, selected: d === r.day ? "" : null }, DAY_TEXT[d] || d)));
    return el("span", {}, times, " ", day, " ",
      el("button", { onclick: () => change(`/api/tasks/${encodeURIComponent(r.key)}/edit`, { times: times.value, day: day.value }) }, "Speichern"));
  };

  const load = async () => {
    const vm = await get("/api/views/tasks");
    if (!document.body.contains(tableBox)) return;  // the page was left
    summary.textContent = `${vm.summary} · Scheduler: ${vm.backend} · Stand ${vm.refreshed}`;
    info.replaceChildren(...vm.problems.map((p) => el("div", { class: "note warning" }, p)),
      ...(vm.deleted.length ? [el("div", { class: "note info" }, "Gelöscht: ", ...vm.deleted.map((k) => el("button", { onclick: () => change(`/api/tasks/${encodeURIComponent(k)}/restore`) }, `${k} wiederherstellen`)))] : []));
    if (vm.runs.length) status.replaceChildren(...vm.runs.map((r) => el("div", { class: `note ${r.level}` }, r.text)));
    for (const r of vm.runs) if (r.finished) showLog(r.key || r.name);
    tableBox.replaceChildren(el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Task", "Zeitplan (VIPER)", "Aktiv", "Status", "Datenstand", "Letzter Erfolg", "Nächster Lauf", "Dauer", "Aktion", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, vm.rows.map((r) => el("tr", { class: r.level },
        el("td", { "data-tip": r.details || null }, r.title || r.name),
        el("td", { "data-tip": r.drift ? `Im Scheduler abweichend: ${r.drift} – „Deployen“ stellt die VIPER-Definition her` : null }, editor(r, vm.days), r.drift ? " ⚠" : ""),
        el("td", {}, el("input", { type: "checkbox", checked: r.enabled ? "" : null, onchange: (e) => change(`/api/tasks/${encodeURIComponent(r.key)}/edit`, { enabled: e.target.checked }) })),
        el("td", { class: "wrap", "data-tip": r.last_error || null }, r.status_line || r.status),
        el("td", { style: r.data_level === "warning" ? "color:#f2c14e" : null, class: r.data_level === "blocker" ? "down" : "" }, r.data_stand),
        el("td", {}, r.last_success), el("td", {}, r.next_run), el("td", {}, r.duration),
        el("td", {}, el("button", { disabled: !r.can_run, "data-tip": r.run_hint || null, onclick: () => run(r) }, "Jetzt ausführen"), " ",
          el("button", { onclick: () => showLog(r.key) }, "Log")),
        el("td", {}, el("button", { onclick: () => change(`/api/tasks/${encodeURIComponent(r.key)}/delete`, {}, `${r.title || r.name} löschen?\n\nDie VIPER-Definition wird als gelöscht markiert und der Task aus dem Scheduler entfernt. „Wiederherstellen“ holt ihn zurück.`) }, "Löschen"))))))));
    etfBox.replaceChildren(vmTable(vm.etf, { empty: "Kein ETF-Datenstand" }));
    if (vm.runs.some((r) => !r.finished)) timer = setTimeout(load, vm.poll_s * 1000);
  };

  const run = async (r) => {
    const lines = [`${r.title || r.name} jetzt starten?`, "", "Der Task läuft über den Scheduler wie zur geplanten Zeit (Netzwerk, Schreiben in die Datenbank, gleiches Log)."];
    if (r.run_warning) lines.push("", r.run_warning);
    if (r.dependency_lines && r.dependency_lines.length) lines.push("", ...r.dependency_lines);
    if (!confirm(lines.join("\n"))) return;
    const result = await post(`/api/tasks/${encodeURIComponent(r.key)}/run`);
    status.replaceChildren(el("div", { class: `note ${result.ok ? "info" : "blocker"}` }, result.message + (result.ok ? " – warte auf den Scheduler …" : "")));
    if (result.ok) timer = setTimeout(load, 2000);
  };

  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Aufgaben"), summary),
      el("div", { class: "right" },
        el("button", { onclick: () => change("/api/tasks/deploy", {}, "VIPER-Definitionen in den Scheduler des Betriebssystems übertragen?\n\nFehlende Tasks werden angelegt, abweichende angepasst, gelöschte entfernt.") }, "Deployen"), " ",
        el("button", { onclick: load }, "Neu laden"))),
    info,
    status,
    panel(null, tableBox),
    el("div", { class: "grid cols-2", style: "margin-top:16px" }, panel("ETF-Datenstand", etfBox), panel(null, logTitle, logBox)));
  await load();
}

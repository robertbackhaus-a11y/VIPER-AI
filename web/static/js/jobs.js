// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Aufgaben: VIPER's integrated task scheduler (/api/scheduler): overview, enable/disable, edit the schedule, run now, last result with error
// details, and the takeover from the Windows task (enables the job after a successful run in VIPER and disables - never deletes - the Windows task).

import { DASH, el, get, panel, post } from "./core.js";

let timer = null;
const DOW = { mon: "Mo", tue: "Di", wed: "Mi", thu: "Do", fri: "Fr", sat: "Sa", sun: "So" };
const DOW_BACK = Object.fromEntries(Object.entries(DOW).map(([k, v]) => [v.toLowerCase(), k]));
const STATUS = { RUNNING: ["läuft", "warn"], OK: ["OK", "ok"], FAILED: ["Fehler", "bad"], TIMEOUT: ["Zeitlimit", "bad"], INTERRUPTED: ["unterbrochen", "warn"], SKIPPED: ["übersprungen", ""] };
const TRIGGER = { schedule: "Zeitplan", manual: "manuell", start: "bei VIPER-Start", catch_up: "nachgeholt" };

const pad = (n) => String(n).padStart(2, "0");
export const scheduleText = (s) => s.map((t) => `${t.day_of_week === "*" ? "täglich" : t.day_of_week.split(",").map((d) => DOW[d] || d).join(",")} ${pad(t.hour)}:${pad(t.minute)}`).join("; ");

export function parseSchedule(text) {
  return text.split(";").map((part) => part.trim()).filter(Boolean).map((part) => {
    const m = part.match(/^(täglich|[a-zäöü,]+)\s+(\d{1,2}):(\d{2})$/i);
    if (!m) throw new Error(`„${part}“: erwartet „täglich 17:15“ oder „So 10:00“ bzw. „Mo,Mi 08:00“`);
    const days = m[1].toLowerCase() === "täglich" ? "*" : m[1].toLowerCase().split(",").map((d) => { if (!DOW_BACK[d]) throw new Error(`unbekannter Tag „${d}“`); return DOW_BACK[d]; }).join(",");
    return { day_of_week: days, hour: Number(m[2]), minute: Number(m[3]) };
  });
}

const when = (iso) => (iso ? new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : DASH);
function badge(run) {
  if (!run) return el("span", { class: "muted" }, "noch kein Lauf");
  const [label, cls] = STATUS[run.status] || [run.status, ""];
  return el("span", {}, el("span", { class: `badge ${cls}` }, label), ` ${when(run.started_at)} · ${TRIGGER[run.trigger] || run.trigger}${run.exit_code !== null && run.exit_code !== undefined ? ` · Exitcode ${run.exit_code}` : ""}`);
}

export async function render(target) {
  clearTimeout(timer);
  const data = await get("/api/scheduler");
  const head = el("div", { class: "head" }, el("div", {}, el("h1", {}, "Aufgaben"),
    el("div", { class: "muted small" }, "Integrierte Aufgabenverwaltung (APScheduler im VIPER-Prozess). Läufe, Zeitpläne und Ergebnisse stehen in der Datenbank.")),
    el("div", { class: "right" }, el("button", { onclick: () => render(target) }, "Neu laden")));
  if (!data.available) { target.replaceChildren(head, el("div", { class: "note warning" }, data.message)); return; }
  const message = el("div");
  const say = (level, text) => message.replaceChildren(el("div", { class: `note ${level}` }, text));
  const act = async (fn) => { try { const r = await fn(); if (r && r.message) say(r.ok === false ? "warning" : "ok", r.message); } catch (e) { say("error", e.message); } render(target).then(() => target.prepend(message)); };

  const rows = data.jobs.map((j) => {
    const input = el("input", { value: scheduleText(j.schedule), style: "width:190px" });
    const save = el("button", { onclick: () => act(async () => { const schedule = parseSchedule(input.value); await post(`/api/scheduler/jobs/${j.name}`, { schedule }); return { message: `Zeitplan von ${j.title} gespeichert.` }; }) }, "Speichern");
    const toggle = el("input", { type: "checkbox", checked: j.enabled, onchange: (e) => act(() => post(`/api/scheduler/jobs/${j.name}`, { enabled: e.target.checked })) });
    const windows = j.windows ? (j.windows.found ? (j.windows.disabled ? "deaktiviert" : j.windows.state || "aktiv") : "nicht registriert") : DASH;
    const error = j.last && j.last.error ? el("details", {}, el("summary", {}, "Fehlerdetails"), el("pre", { class: "small", style: "white-space:pre-wrap;max-width:520px" }, j.last.error)) : "";
    return el("tr", { class: j.last && ["FAILED", "TIMEOUT"].includes(j.last.status) ? "blocker" : "" },
      el("td", { "data-tip": `${j.name}\n${j.command}` }, j.title),
      el("td", {}, el("label", {}, toggle, j.enabled ? " aktiv" : " aus")),
      el("td", {}, input, " ", save, el("div", { class: "muted small" }, [j.run_at_start ? "auch bei VIPER-Start" : "", j.catch_up ? "verpasste Läufe nachholen" : ""].filter(Boolean).join(" · "))),
      el("td", {}, j.enabled ? when(j.next_run) : "—"),
      el("td", { class: "wrap" }, j.running ? el("span", { class: "badge warn" }, "läuft") : badge(j.last), error),
      el("td", {}, j.last_ok ? when(j.last_ok.finished_at) : DASH),
      el("td", {}, windows, j.taken_over_at ? el("div", { class: "muted small" }, `übernommen ${when(j.taken_over_at)}`) : ""),
      el("td", {}, el("button", { disabled: j.running, onclick: () => { if (confirm(`${j.title} jetzt in VIPER ausführen? Der Job läuft wie geplant (Netzwerk, Schreiben in die Datenbank, gleiches Log).`)) act(() => post(`/api/scheduler/jobs/${j.name}/run`)); } }, "Jetzt ausführen"),
        " ", j.taken_over_at ? "" : el("button", { class: "primary", disabled: !j.last_ok, "data-tip": j.last_ok ? null : "erst nach einem erfolgreichen Lauf in VIPER",
          onclick: () => { if (confirm(`${j.title} übernehmen? Der Job wird in VIPER aktiviert und der Windows-Task ${j.windows_task} deaktiviert (nicht gelöscht).`)) act(() => post(`/api/scheduler/jobs/${j.name}/takeover`)); } }, "Übernehmen"),
        " ", el("a", { href: "#/tasks" }, "Log")));
  });
  target.replaceChildren(head, message,
    data.scheduler_running ? "" : el("div", { class: "note warning" }, "Der Scheduler läuft in diesem Prozess nicht (gestartet mit --no-scheduler); Zeitpläne werden gespeichert, aber nicht ausgeführt."),
    panel(null, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Aufgabe", "Aktiv", "Zeitplan", "Nächster Lauf", "Letzter Lauf", "Letzter Erfolg", "Windows-Task", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))),
      el("p", { class: "muted small" }, "Übernahme: erst „Jetzt ausführen“; nach einem erfolgreichen Lauf aktiviert „Übernehmen“ den Job in VIPER und deaktiviert den Windows-Task. Ein Job läuft nie doppelt.")));
  if (data.jobs.some((j) => j.running)) timer = setTimeout(() => { if (location.hash.startsWith("#/jobs")) render(target); }, 3000);
}

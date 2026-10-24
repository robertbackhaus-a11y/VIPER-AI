// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Einstellungen / Diagnose: paths, database and Ollama status, the tool classification (/api/diagnostics) and the settings the web uses
// (/api/settings; the same settings file as the desktop GUI).

import { el, get, kv, panel, post } from "./core.js";

const FIELDS = [["ollama_base_url", "Ollama-URL"], ["ollama_model", "Ollama-Modell"], ["trading_db_path", "Datenbank"], ["trading_logs_dir", "Log-Ordner der Tasks"],
  ["refresh_interval_s", "Aktualisierung (Sekunden)"], ["think_enabled", "Thinking im Chat"]];

export async function render(target) {
  const [diag, current] = await Promise.all([get("/api/diagnostics"), get("/api/settings")]);
  const inputs = {};
  const message = el("div");
  const formRows = FIELDS.map(([key, label]) => {
    const value = current.settings[key];
    const input = typeof value === "boolean" ? el("input", { type: "checkbox", checked: value }) : el("input", { value: value ?? "", style: "width:100%", list: key === "ollama_model" ? "models" : null });
    inputs[key] = input;
    return [label, input];
  });
  const save = async () => {
    const body = {};
    for (const [key] of FIELDS) {
      const i = inputs[key];
      body[key] = i.type === "checkbox" ? i.checked : key === "refresh_interval_s" ? Number(i.value) : i.value;
    }
    try {
      const r = await post("/api/settings", body);
      message.replaceChildren(el("div", { class: "note ok" }, `Gespeichert in ${current.path}.${r.restart_needed ? " Die neue Datenbank gilt nach einem Neustart von viper-ai-web." : ""}`));
    } catch (error) {
      message.replaceChildren(el("div", { class: "note error" }, `Nicht gespeichert: ${error.message}`));
    }
  };
  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Einstellungen und Diagnose"))),
    diag.warnings.length ? diag.warnings.map((w) => el("div", { class: "note warning" }, w)) : "",
    el("div", { class: "grid cols-2" },
      panel("Diagnose", kv(diag.rows)),
      panel("Einstellungen", kv(formRows), el("datalist", { id: "models" }, diag.models.map((m) => el("option", { value: m }))),
        el("div", { class: "head", style: "margin-top:10px" }, el("button", { class: "primary", onclick: save }, "Speichern")), message),
      el("div", { class: "span-2" }, panel("Tool-Klassifikation (tool_policy.py)", el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ["Tool", "Klasse", "Web"].map((h) => el("th", {}, h)))),
        el("tbody", {}, diag.tools.map(([name, cls, allowed]) => el("tr", {}, el("td", {}, name), el("td", {}, cls), el("td", { class: allowed === "erlaubt" ? "up" : "down" }, allowed))))))))));
}

// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Einstellungen / Diagnose: paths, database and Ollama status, the tool classification (/api/diagnostics) and the settings the web uses
// (/api/settings; settings.json in the settings folder of the installation).

import { el, get, kv, panel, post } from "./core.js";

/** Depotverwaltung: create, rename, (de)activate; delete only a depot without transactions (the server refuses otherwise). */
async function depotPanel() {
  const box = el("div");
  const message = el("div");
  const refresh = async () => {
    const data = await get("/api/portfolios");
    if (!data.available) {
      box.replaceChildren(el("p", { class: "muted small" }, "Mehrere Depots sind noch nicht eingerichtet. Einmalig ausführen: viper-ai run Migrate-TradingPortfolios.py --write"));
      return;
    }
    const run = async (fn, ok) => {
      try {
        const r = await fn();
        if (r && r.ok === false) throw new Error(r.message || "abgelehnt");
        message.replaceChildren(el("div", { class: "note ok" }, ok));
        window.dispatchEvent(new Event("viper:depots"));
        await refresh();
      } catch (error) {
        message.replaceChildren(el("div", { class: "note error" }, error.message));
      }
    };
    const rows = data.portfolios.map((p) => {
      const name = el("input", { value: p.name, style: "width:100%" });
      return el("tr", {},
        el("td", {}, name),
        el("td", { class: "num" }, String(p.transactions)), el("td", { class: "num" }, String(p.positions)),
        el("td", {}, p.active ? "aktiv" : "deaktiviert", p.default ? " · Standard" : ""),
        el("td", {},
          el("button", { onclick: () => run(() => post(`/api/portfolios/${p.id}`, { name: name.value }), `Depot ${name.value} gespeichert.`) }, "Umbenennen"), " ",
          el("button", { onclick: () => run(() => post(`/api/portfolios/${p.id}`, { active: !p.active }), `Depot ${p.name} ${p.active ? "deaktiviert" : "aktiviert"}.`) },
            p.active ? "Deaktivieren" : "Aktivieren"), " ",
          p.default || p.transactions ? "" : el("button", { class: "danger", onclick: () => confirm(`Depot ${p.name} löschen?`) && run(() => post(`/api/portfolios/${p.id}/delete`), `Depot ${p.name} gelöscht.`) }, "Löschen")));
    });
    const newName = el("input", { placeholder: "Name des neuen Depots" });
    box.replaceChildren(
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Name", "Transaktionen", "Positionen", "Status", ""].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows))),
      el("div", { class: "head", style: "margin-top:10px" }, newName,
        el("button", { class: "primary", onclick: () => run(() => post("/api/portfolios", { name: newName.value }), `Depot ${newName.value} angelegt.`) }, "Depot anlegen")),
      el("p", { class: "muted small" }, "Gesamt fasst alle aktiven Depots zusammen. Löschen geht nur ohne Transaktionen; das Standard-Depot bleibt immer erhalten."));
  };
  await refresh();
  return panel("Depots", box, message);
}

const FIELDS = [["ollama_base_url", "Ollama-URL"], ["ollama_model", "Ollama-Modell"], ["trading_db_path", "Datenbank"],
  ["refresh_interval_s", "Aktualisierung (Sekunden)"], ["think_enabled", "Thinking im Chat"]];

export async function render(target) {
  const [diag, current, depots] = await Promise.all([get("/api/diagnostics"), get("/api/settings"), depotPanel()]);
  const inputs = {};
  const message = el("div");
  const formRows = FIELDS.map(([key, label]) => {
    const value = current.settings[key];
    const input = typeof value === "boolean" ? el("input", { type: "checkbox", checked: value })
      : key === "ollama_model" && diag.models.length ? modelSelect(value, diag.models)
      : el("input", { value: value ?? "", style: "width:100%" });
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
      window.dispatchEvent(new Event("viper:status"));
    } catch (error) {
      message.replaceChildren(el("div", { class: "note error" }, `Nicht gespeichert: ${error.message}`));
    }
  };
  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Einstellungen und Diagnose"))),
    diag.warnings.length ? diag.warnings.map((w) => el("div", { class: "note warning" }, w)) : "",
    el("div", { class: "grid cols-2" },
      panel("Diagnose", kv(diag.rows)),
      panel("Einstellungen", kv(formRows),
        diag.models.length ? "" : el("p", { class: "muted small" }, "Ollama ist nicht erreichbar: das Modell kann nur von Hand eingetragen werden."),
        el("div", { class: "head", style: "margin-top:10px" }, el("button", { class: "primary", onclick: save }, "Speichern")), message),
      el("div", { class: "span-2" }, depots),
      el("div", { class: "span-2" }, panel("Tool-Klassifikation (tool_policy.py)", el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ["Tool", "Klasse", "Web"].map((h) => el("th", {}, h)))),
        el("tbody", {}, diag.tools.map(([name, cls, allowed]) => el("tr", {}, el("td", {}, name), el("td", {}, cls), el("td", { class: allowed === "erlaubt" ? "up" : "down" }, allowed))))))))));
}

/** Every model the running Ollama offers; a configured model Ollama does not have stays selectable, marked as not installed. */
function modelSelect(value, models) {
  const names = [...models].sort((a, b) => a.localeCompare(b));
  const missing = value && !names.includes(value) && !names.includes(`${value}:latest`);
  const select = el("select", { style: "width:100%" },
    missing ? el("option", { value }, `${value} (nicht installiert)`) : null,
    names.map((m) => el("option", { value: m }, m)));
  select.value = missing ? value : names.includes(value) ? value : `${value}:latest`;
  return select;
}

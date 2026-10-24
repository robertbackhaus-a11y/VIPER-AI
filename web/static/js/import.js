// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Import: a transaction export (Parqet or canonical CSV). "Prüfen" is a dry run (difference and reconciliation, nothing written); open points
// are decided here and need a new check; "Importieren" only after a valid check of the unchanged form, with its token and a confirmation.

import { el, get, panel, post, vmTable } from "./core.js";

export async function render(target) {
  const options = await get("/api/import/options");
  const file = el("input", { type: "file", accept: ".csv,text/csv" });
  const format = el("select", {}, options.formats.map((f) => el("option", { value: f }, f)));
  const create = el("input", { type: "checkbox" });
  const historical = el("input", { type: "checkbox" });
  const checkBtn = el("button", { class: "primary" }, "Prüfen");
  const importBtn = el("button", { class: "danger", disabled: true }, "Importieren");
  const out = el("div");
  const decisions = { strategies: {}, aliases: {} };
  let uploaded = null, checked = null;

  const form = () => ({ file_id: uploaded && uploaded.file_id, format_text: format.value, create_securities: create.checked, include_historical: historical.checked,
    strategies: { ...decisions.strategies }, aliases: { ...decisions.aliases } });
  const invalidate = () => { checked = null; importBtn.disabled = true; };
  [format, create, historical].forEach((i) => i.addEventListener("change", invalidate));
  file.addEventListener("change", () => { uploaded = null; invalidate(); decisions.strategies = {}; decisions.aliases = {}; });

  const show = (view) => {
    const tables = [["Neue Wertpapiere", "securities"], ["Stammdaten", "masterdata"], ["Positionen", "positions"], ["Probleme", "problems"], ["Offene Punkte", "open_items"], ["Abweichungen", "differences"]];
    out.replaceChildren(
      el("div", { class: `note ${view.level}`, "data-tip": view.tooltip || null }, view.headline),
      view.lines.length ? el("ul", {}, view.lines.map((l) => el("li", {}, l))) : "",
      decisionPanel(view),
      ...tables.filter(([, key]) => view[key] && view[key].length).map(([title, key]) => panel(title, vmTable({ headers: options.headers[key], rows: view[key] }))),
      view.closing.length ? panel("Abgleich", el("ul", {}, view.closing.map((l) => el("li", {}, l)))) : "");
  };

  const decisionPanel = (view) => {
    const rows = [];
    for (const [identifier, info] of Object.entries(view.alias_infos || {})) {
      const select = el("select", { onchange: (e) => { if (options.alias_values[e.target.value]) decisions.aliases[identifier] = info.security; else delete decisions.aliases[identifier]; invalidate(); } },
        options.aliases.map((a) => el("option", { value: a, selected: (decisions.aliases[identifier] ? a !== "Offen" : a === "Offen") }, a)));
      rows.push(el("tr", {}, el("td", {}, "Identifier-Alias"), el("td", {}, `${identifier} → ${info.name || info.security}`),
        el("td", { class: "wrap" }, [info.wkn ? `WKN ${info.wkn}` : "", info.database_isin ? `ISIN in der Datenbank ${info.database_isin}` : "", info.same_wkn ? "gleiche WKN" : ""].filter(Boolean).join(", ")),
        el("td", {}, select), el("td", {}, `${info.status}${info.reason ? ` – ${info.reason}` : ""}`)));
    }
    for (const [key, info] of Object.entries(view.strategy_infos || {})) {
      const select = el("select", { onchange: (e) => { const v = options.strategy_values[e.target.value]; if (v) decisions.strategies[key] = v; else delete decisions.strategies[key]; invalidate(); } },
        options.strategies.map((s) => el("option", { value: s, selected: options.strategy_values[s] === (decisions.strategies[key] || null) }, s)));
      rows.push(el("tr", {}, el("td", {}, "Strategie"), el("td", {}, `${info.symbol || ""} ${info.name || ""}`), el("td", {}, `${info.shares ?? ""} Stück, neu gehalten ohne Strategie`), el("td", {}, select), el("td", {}, decisions.strategies[key] ? "gewählt" : "offen")));
    }
    if (!rows.length) return "";
    return panel("Zu entscheiden (jede Entscheidung braucht eine neue Prüfung)", el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Art", "Wertpapier", "Angaben", "Entscheidung", "Stand"].map((h) => el("th", {}, h)))), el("tbody", {}, rows))));
  };

  checkBtn.addEventListener("click", async () => {
    if (!file.files.length) { out.replaceChildren(el("div", { class: "note warning" }, "Bitte zuerst eine Datei wählen.")); return; }
    checkBtn.disabled = true;
    out.replaceChildren(el("div", { class: "loading" }, "prüfe … (nichts wird geschrieben)"));
    try {
      if (!uploaded) {
        const f = file.files[0];
        uploaded = await (await fetch(`/api/import/upload?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f })).json();
        if (!uploaded.file_id) throw new Error(uploaded.detail || "Upload fehlgeschlagen");
      }
      const request = form();
      const view = await post("/api/import/check", request);
      show(view);
      checked = view.can_import ? { request: JSON.stringify(request), token: view.plan_token } : null;
      importBtn.disabled = !checked;
    } catch (error) {
      out.replaceChildren(el("div", { class: "note blocker" }, `Fehler: ${error.message}`));
    } finally {
      checkBtn.disabled = false;
    }
  });

  importBtn.addEventListener("click", async () => {
    const request = form();
    if (!checked || checked.request !== JSON.stringify(request)) { invalidate(); return; }
    if (!confirm("Jetzt importieren? Die Datenbank wird vorher gesichert und alles in einer Transaktion geschrieben.")) return;
    importBtn.disabled = true;
    const view = await post("/api/import/run", { ...request, plan_token: checked.token });
    checked = null;
    show(view);
  });

  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Import"), el("div", { class: "muted small" }, "Parqet-Export oder kanonisches CSV · Prüfen schreibt nichts"))),
    panel(null, el("div", { class: "head" }, file, format, el("label", {}, create, " neue Wertpapiere anlegen"), el("label", {}, historical, " historische Zeilen einbeziehen"), checkBtn, importBtn),
      el("p", { class: "muted small" }, "Die Datei wird nur in den Einstellungsordner kopiert, damit die Prüfung sie lesen kann. Die Seite startet nie eine Campaign.")),
    out);
}

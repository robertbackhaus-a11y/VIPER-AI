// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Planner / Actions: the view model of the desktop planner page (/api/views/planner). Simulation only, no orders.

import { el, get, kv, notes, panel, vmTable } from "./core.js";

export async function render(target) {
  const refresh = location.hash.includes("refresh=1");
  const vm = await get(`/api/views/planner${refresh ? "?refresh=true" : ""}`);
  const head = el("div", { class: "head" },
    el("div", {}, el("h1", {}, "Planner / Aktionen"), el("div", { class: "muted small" }, "Simulation der Engine: keine Order, keine Buchung")),
    el("div", { class: "right" }, el("button", { onclick: () => { location.hash = "#/planner?refresh=1"; } }, "Neu berechnen")));
  if (!vm.available) {
    target.replaceChildren(head, el("div", { class: "note error" }, vm.error || "Keine Daten"));
    return;
  }
  target.replaceChildren(head,
    el("div", { class: "grid cols-2" },
      panel("Status", kv(vm.header)),
      panel("Aktionen der Engine", el("p", {}, vm.counts), vm.notes.length ? notes(vm.notes.map((n) => ["info", n])) : null)),
    el("div", { class: "grid", style: "margin-top:16px" },
      panel("Positionen und Kandidaten", vmTable(vm.positions, { numeric: [3, 4] })),
      panel("Geplante Entries", vmTable(vm.planned, { numeric: [2, 3, 4, 5, 6, 7, 8, 9] })),
      panel("Zurückgestellte Entries", vmTable(vm.deferred, { numeric: [2] })),
      panel("Entry-Kandidaten (PROMOTE-Bewertung)", vmTable(vm.candidates, { numeric: [3] }))));
}

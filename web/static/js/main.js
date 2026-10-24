// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Router of the single page: #/<page>[/<argument>] loads js/<module>.js and calls its render(target, argument).

import { disposeCharts, el, failure, get, loading } from "./core.js";

const ROUTES = {
  dashboard: "dashboard",
  portfolio: "portfolio",
  position: "portfolio",
  transactions: "portfolio",
  planner: "planner",
  watchlist: "watchlist",
  sectors: "sectors",
  tasks: "tasks",
  import: "import",
  chat: "chat",
  settings: "settings",
  intelligence: "intelligence",
  analysis: "intelligence",
  jobs: "jobs",
};

const page = document.getElementById("page");
const nav = document.getElementById("nav");

async function route() {
  const [name = "dashboard", arg = ""] = location.hash.replace(/^#\/?/, "").split("?")[0].split("/");
  const module = ROUTES[name] ? ROUTES[name] : "dashboard";
  for (const link of nav.querySelectorAll("a")) link.classList.toggle("active", link.dataset.page === ({ position: "portfolio", analysis: "intelligence" }[name] || name));
  nav.classList.remove("open");
  disposeCharts();
  loading(page);
  try {
    const mod = await import(`./${module}.js`);
    await mod.render(page, decodeURIComponent(arg), name);
  } catch (error) {
    failure(page, error);
  }
}

async function status() {
  const box = document.getElementById("status");
  try {
    const s = await get("/api/status");
    const ok = s.database && s.database.ok;
    const warn = s.warnings && s.warnings.length ? el("span", { style: "color:#f2c14e;margin-left:8px", "data-tip": s.warnings.join("\n\n") }, `⚠ ${s.warnings.length} Hinweis${s.warnings.length > 1 ? "e" : ""}`) : "";
    box.replaceChildren(el("span", { class: `dot ${ok ? "" : "err"}` }), `Datenbank ${ok ? "OK" : "Fehler"} · v${s.version}`, warn);
  } catch (error) {
    box.replaceChildren(el("span", { class: "dot err" }), "Server nicht erreichbar");
  }
}

document.getElementById("menu").addEventListener("click", () => nav.classList.toggle("open"));
window.addEventListener("hashchange", route);
route();
status();
setInterval(status, 300000);

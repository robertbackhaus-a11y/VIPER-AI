// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Router of the single page: #/<page>[/<argument>] loads js/<module>.js and calls its render(target, argument).

import { disposeCharts, el, failure, get, loading, portfolioId, setPortfolioId } from "./core.js";

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
  jobs: "tasks", // the former page of the integrated scheduler (removed): old links open the tasks
};

const page = document.getElementById("page");
const nav = document.getElementById("nav");
const gear = document.querySelector(".gear");
const NAV_KEY = "viper.nav.collapsed"; // collapsed navigation: a setting of this browser only (localStorage), nothing on the server
const narrow = window.matchMedia("(max-width: 820px)");

async function route() {
  const [name = "dashboard", arg = ""] = location.hash.replace(/^#\/?/, "").split("?")[0].split("/");
  const module = ROUTES[name] ? ROUTES[name] : "dashboard";
  const current = { position: "portfolio", analysis: "intelligence", jobs: "tasks" }[name] || name;
  for (const link of nav.querySelectorAll("a")) link.classList.toggle("active", link.dataset.page === current);
  gear.classList.toggle("active", current === "settings");
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

// Header status: database and Ollama as compact chips; the detail of a failure is in the tooltip and on the settings page.
// A reachable Ollama without the configured model is a warning (choose the model on the settings page), not an outage.
const chip = (label, ok, tip, level = ok ? "" : "err", text = ok ? "OK" : "Fehler") =>
  el("span", { class: `chip ${level}`, title: tip || null }, el("span", { class: `dot ${level}` }), `${label} ${text}`);
const ollamaChip = (o) => (o.ok ? chip("Ollama", true, `${o.model} · Ollama ${o.version}`)
  : o.reachable ? chip("Ollama", false, `Ollama ${o.version} läuft, aber das Modell ${o.model} der Einstellungen ist nicht installiert – Modell unter Einstellungen wählen`, "warn", "Modell fehlt")
  : chip("Ollama", false, `${o.error || "nicht erreichbar"} – betrifft nur Chat und Analysen`));

async function status() {
  const box = document.getElementById("status");
  try {
    const s = await get("/api/status");
    const db = s.database || {}, ollama = s.ollama || {};
    const warn = s.warnings && s.warnings.length ? el("span", { style: "color:#f2c14e", "data-tip": s.warnings.join("\n\n") }, `⚠ ${s.warnings.length} Hinweis${s.warnings.length > 1 ? "e" : ""}`) : "";
    box.replaceChildren(warn, chip("Datenbank", db.ok, db.ok ? s.db_path : db.message || db.error),
      ollamaChip(ollama),
      el("span", { class: "muted" }, `v${s.version}`));
  } catch (error) {
    box.replaceChildren(el("span", { class: "chip err" }, el("span", { class: "dot err" }), "Server nicht erreichbar"));
  }
}

// Depot switcher: Gesamt (all active depots) or one active depot; the choice lasts for this browser session and every page reads in it.
async function depots() {
  const box = document.getElementById("depot-box"), select = document.getElementById("depot");
  try {
    const data = await get("/api/portfolios");
    const active = (data.portfolios || []).filter((p) => p.active);
    if (!data.available || !active.length) { box.hidden = true; setPortfolioId(""); return; }
    if (portfolioId() && !active.some((p) => String(p.id) === portfolioId())) setPortfolioId(""); // deleted or deactivated: back to the total
    select.replaceChildren(el("option", { value: "" }, data.total_label || "Gesamt"), ...active.map((p) => el("option", { value: String(p.id) }, p.name)));
    select.value = portfolioId();
    box.hidden = false;
  } catch (error) {
    box.hidden = true;
  }
}
document.getElementById("depot").addEventListener("change", (event) => { setPortfolioId(event.target.value); route(); });
window.addEventListener("viper:depots", depots); // the depot management of the settings page changed the list

const storedCollapsed = () => { try { return localStorage.getItem(NAV_KEY) === "1"; } catch { return false; } };
document.body.classList.toggle("nav-collapsed", storedCollapsed());
document.getElementById("menu").addEventListener("click", () => {
  if (narrow.matches) { nav.classList.toggle("open"); return; } // small window: the navigation slides over the page as before
  const collapsed = document.body.classList.toggle("nav-collapsed");
  try { localStorage.setItem(NAV_KEY, collapsed ? "1" : "0"); } catch { /* storage blocked: the state lasts until the page is reloaded */ }
});
window.addEventListener("viper:status", status); // the settings page asks for a new check after saving
window.addEventListener("hashchange", route);
depots().finally(route);
status();
setInterval(status, 300000);

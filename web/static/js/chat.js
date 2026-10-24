// Copyright (C) 2026 Robert Backhaus
// SPDX-License-Identifier: Apache-2.0
// Chat: questions to the local Ollama model, which reads through the READ tools only (/api/chat, streamed as Server-Sent Events).
// The transcript shows user, answer, thinking, tool calls and errors separately; tool results are never shown, only name, duration and size.

import { el, get, post } from "./core.js";

export async function render(target) {
  const status = await get("/api/chat/status");
  const log = el("div", { class: "chat-log" });
  const input = el("textarea", { rows: 3, placeholder: "Frage zum Depot, zum Planner, zu Nachrichten … (Strg+Enter sendet)", style: "width:100%" });
  const send = el("button", { class: "primary" }, "Senden");
  const stop = el("button", { disabled: true }, "Stopp");
  const reset = el("button", {}, "Neuer Chat");
  const state = el("span", { class: "muted small" }, `Modell ${status.model} · Thinking ${status.think ? "an" : "aus"}${status.turns ? ` · ${status.turns} Fragen im Verlauf` : ""}`);

  const add = (cls, text) => { const node = el("div", { class: `msg ${cls}` }, text); log.append(node); log.scrollTop = log.scrollHeight; return node; };

  const ask = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    add("user", text);
    send.disabled = true; stop.disabled = false; reset.disabled = true;
    let answer = null, thinking = null;
    const statusLine = add("status", "…");
    try {
      const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const kind = (block.match(/^event: (.*)$/m) || [])[1];
          const payload = JSON.parse((block.match(/^data: (.*)$/m) || [, "{}"])[1]);
          if (kind === "content") { answer = answer || add("assistant", ""); answer.textContent += payload.text; }
          else if (kind === "thinking") { thinking = thinking || add("thinking", ""); thinking.textContent += payload.text; }
          else if (kind === "status") statusLine.textContent = payload.text;
          else if (kind === "tool_call") add("tool", `${payload.data.allowed ? "Tool" : "Abgelehnt"}: ${payload.text}(${JSON.stringify(payload.data.arguments || {})})`);
          else if (kind === "tool_result") add("tool", `${payload.text}: ${payload.data.ok ? "ok" : payload.data.kind} · ${payload.data.seconds} s · ${payload.data.chars} Zeichen${payload.data.note ? ` · ${payload.data.note}` : ""}`);
          else if (kind === "warning") add("warning", payload.text);
          else if (kind === "error") add("error", payload.text);
          else if (kind === "done") statusLine.textContent = { done: "fertig", cancelled: "abgebrochen", error: "Fehler", round_limit: "Rundenlimit erreicht" }[payload.data.outcome] || payload.data.outcome;
          if (kind === "content" || kind === "tool_call") { thinking = null; }
          if (kind === "tool_result") answer = null;
          log.scrollTop = log.scrollHeight;
        }
      }
    } catch (error) {
      add("error", `Verbindung unterbrochen: ${error.message}`);
    } finally {
      send.disabled = false; stop.disabled = true; reset.disabled = false;
    }
  };

  send.addEventListener("click", ask);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) ask(); });
  stop.addEventListener("click", () => post("/api/chat/stop"));
  reset.addEventListener("click", async () => { const r = await post("/api/chat/reset"); if (r.ok) log.replaceChildren(); });

  target.replaceChildren(
    el("div", { class: "head" }, el("div", {}, el("h1", {}, "Chat"), el("div", { class: "muted small" }, "Lokales Modell, nur lesende Tools; kann nichts kaufen, verkaufen, ändern oder starten")),
      el("div", { class: "right" }, state)),
    el("section", { class: "panel" }, log, input, el("div", { class: "head", style: "margin-top:8px" }, send, stop, reset)));
}

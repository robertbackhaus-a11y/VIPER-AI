# Web-Frontend

English version: [web.en.md](web.en.md)

Das Web-Frontend ist die Oberfläche von VIPER-AI im lokalen Browser. Es hat die frühere Qt-Desktop-GUI abgelöst (M6); Plan und Stand stehen in der Roadmap des Repositorys. Es enthält keine Fachlogik: Jede Zahl kommt aus der bestehenden Engine.

## 1. Architektur

```
Browser (http://127.0.0.1:8765/)
  |-- statische Seite (HTML, CSS, JavaScript)
  `-- /api ----- FastAPI (web/app.py)
                   |-- Fassade trading_sqlite.Tools im eigenen Prozess (READ-Tools + GUI-Aktionen)
                   `-- Engine-Modul portfolio_history (nur lesend)  ----- SQLite
```

- Die Tools sind dieselben Methoden der Klasse `Tools`, die der MCP-Server anbietet; sie laufen im Prozess des Web-Servers, ohne MCP-Kindprozess.
- Erlaubt ist genau, was die frühere Desktop-GUI durfte: die READ-Tools und die Aktionen aus `GUI_ACTIONS` von `tool_policy.py`. `sql_execute`, `approve_swing_promotion` und jeder unbekannte Name werden abgelehnt (HTTP 403, `TOOL_NOT_ALLOWED`).
- Werte, die kein Tool liefert (Bestände mit Einstandswert, Transaktionen, Tageskurse, Depotverlauf), kommen aus `portfolio_history.py` über eine nur lesende Verbindung (`mode=ro`, `query_only`).
- Die Seite nutzt Apache ECharts 5.6.0 (Dashboard-Grafiken) und TradingView Lightweight Charts 4.2.3 (Kursverläufe), beide unter `web/static/vendor` mitgeliefert; sie lädt nichts aus dem Internet.
- Der Server hört nur auf 127.0.0.1. Ein schreibender Aufruf (alles außer `GET`/`HEAD`/`OPTIONS`) wird nur mit dem `Host` `127.0.0.1:<Port>` oder `localhost:<Port>` dieses Servers angenommen und, falls der Browser einen `Origin` mitschickt, nur mit `http://127.0.0.1:<Port>` oder `http://localhost:<Port>`; alles andere bekommt HTTP 403 (`FOREIGN_ORIGIN`). Das hält fremde Webseiten im Browser und umgebogene Hostnamen (DNS-Rebinding) ab. Es gibt bewusst keine Anmeldung, keine Benutzer und keine Tokens.

## 2. Installation und Start

Das Web-Frontend braucht das Extra `web` (Paket `fastapi`; `uvicorn` kommt mit `mcp`):

```
<venv>\Scripts\python.exe -m pip install "VIPER-AI[web] @ <Quelle>"
viper-ai-web
viper-ai-web --port 8766 --no-browser
```

- Das Web startet nie selbst einen Job, auch nicht beim Start oder Neustart: Die geplanten Jobs sind die sieben Windows-Tasks (Abschnitt 6). `--no-scheduler` wird noch angenommen, hat aber keine Wirkung mehr.
- Ohne `--db` gilt `trading_db_path` der Einstellungsdatei (Abschnitt 6), sonst `<root>\data\trading.db` von `VIPER_TRADING_ROOT`.
- Im Repository startet `python -m web` dasselbe.

## 3. API

| Pfad | Inhalt | Quelle |
|---|---|---|
| `GET /api/status` | Version, Datenbankstatus, Pfad, Hinweise | `database_status` |
| `GET /api/portfolio` | offene Positionen mit Marktwert, Einstandswert, unrealisiertem Ergebnis, Tagesänderung; Summen | `portfolio_history.holdings` |
| `GET /api/portfolio/history` | Tageswerte (investiertes Kapital, Marktwert, Nettozufluss, Dividenden in EUR), Kennzahlen je Zeitraum, fehlende Kurse, EZB-Stand | `portfolio_history.portfolio_history` |
| `GET /api/transactions` | gespeicherte Transaktionen, neueste zuerst (`security_id`, `limit`) | `portfolio_history.transactions` |
| `GET /api/securities/{id}/prices` | Tagesbalken eines Wertpapiers in der Handelswährung | `portfolio_history.price_history` |
| `GET /api/views/dashboard` | Planner- und Engine-Block des Desktop-Dashboards (dieselben Texte und Tabellen) | `run_trading_orchestrator`, `database_status` |
| `GET /api/views/planner`, `/api/views/watchlist`, `/api/views/sectors`, `/api/views/tasks` | Tabellen und Texte der Desktop-Seiten Planner, Watchlist, Sektoren (mit Einzelwertrisiko) und Tasks | View-Modelle von `gui/viewmodels` |
| `POST /api/watchlist/add`, `/api/watchlist/{id}/remove`, `/api/watchlist/{id}/edit`, `/api/onboarding` | Watchlist-Aktionen und Neuaufnahme eines Wertpapiers (Prüfen, dann Anlegen) | `add_watchlist_candidate`, `remove_watchlist_candidate`, `set_security_strategy`, `set_watchlist_priority`, `set_security_sector`, `onboard_security` |
| `POST /api/tasks/{name}/run`, `GET /api/tasks/{name}/log` | geplanten Task jetzt starten, Log-Ende lesen | Windows Task Scheduler |
| `POST /api/import/upload`, `/api/import/check`, `/api/import/run` | Exportdatei ablegen, prüfen, mit dem Token der Prüfung importieren | `check_transaction_import`, `import_transactions` |
| `POST /api/chat` | Frage an das lokale Modell, Antwort als Server-Sent Events; `/api/chat/stop`, `/api/chat/reset` | Ollama, READ-Tools |
| `GET /api/diagnostics`, `GET` und `POST /api/settings` | Pfade, Status, Tool-Klassifikation; Einstellungen | Einstellungsdatei |
| `GET /api/intelligence`, `GET /api/intelligence/cases/{id}`, `GET /api/intelligence/securities` | aktuelle Thesen mit Neubewertungsgründen, Analysefälle mit Schrittprotokoll, wählbare Wertpapiere | `analysis_store` |
| `POST /api/intelligence/analyze`, `POST /api/intelligence/cases/{id}/resume` | Analyse starten (`security_id`, `question`, `supersedes_id`), abgebrochene Analyse fortsetzen | `viper_reasoning`, Ollama |
| `GET /api/orchestrator` | vollständiges Ergebnis des Orchestrators (60 Sekunden zwischengespeichert, `refresh=true` rechnet neu) | `run_trading_orchestrator` |
| `POST /api/read/{tool}` | jedes READ-Tool mit seinen Argumenten als JSON | READ-Tools |
| `POST /api/action/{tool}` | eine GUI-Aktion mit ihren Argumenten als JSON | `GUI_ACTIONS` |

Die OpenAPI-Beschreibung liegt unter `/api/docs`.

## 4. Berechnungsgrundlagen

- Marktwert einer Position: der Wert von `build_portfolio_context` (Stück x letzter Snapshot-Preis x EZB-Kurs). Fehlt eine Bewertung, gibt es keine Summe.
- Einstandswert: `remaining_cost_basis` der Tabelle `positions` (Durchschnittskostenmodell des Imports); unrealisiertes Ergebnis = Marktwert - Einstandswert.
- Tagesänderung: `close / previous_close - 1` der beiden letzten Tagesbalken (Handelswährung, ohne Währungseffekt), wie auf der Sektorseite.
- Realisiertes Ergebnis: Summe von `realized_gain` aus `positions`; Dividenden: DIVIDEND-Beträge abzüglich Steuern und Gebühren.
- Depotverlauf: Die Transaktionen werden Tag für Tag mit demselben Kostenmodell (`apply_cost_basis`) und denselben Split-Faktoren nachgespielt. Der Marktwert eines Tages ist Stück x Schlusskurs x EZB-Kurs dieses Tages; ist eine gehaltene Position an einem Tag ohne Kurs oder ohne EZB-Kurs (älter als 5 Tage), hat der Tag keinen Marktwert (`missing` nennt die Symbole). Das investierte Kapital ist immer bekannt, weil die Beträge in EUR gespeichert sind.
- Performance je Zeitraum (1M, 3M, 6M, 1J, 3J, 5J, MAX): Nettozufluss eines Tages = Käufe (Betrag + Gebühren + Steuern) minus Verkäufe (Betrag - Gebühren - Steuern); Dividenden sind Ertrag. Tagesrendite = (Depotwert - Nettozufluss + Dividenden) / Depotwert des Vortags - 1, verkettet zur zeitgewichteten Rendite; ein Tag ohne Depotwert im Zeitraum macht die Rendite „nicht berechenbar“. MAX beginnt mit dem ersten vollständig bewerteten Tag. Depotwert und Rendite sind getrennte Kennzahlen.

## 5. Seiten

| Seite | Inhalt |
|---|---|
| Dashboard | Depotwert mit Tagesänderung, Einstandswert, unrealisiertes und gespeichertes realisiertes Ergebnis, Dividenden, Cash; Depot- und Kapitalentwicklung mit Zeiträumen (1M, 3M, 6M, 1J, 3J, 5J, MAX) und den Kennzahlen des Zeitraums; Allokation nach Position, Strategie und Sektor; Top/Flop heute und nach Ergebnis; Positionen; Planner und Hinweise der Engine |
| Portfolio | alle offenen Positionen mit Kurs, Marktwert, Einstandswert, Ergebnis, Anteil, Tagesänderung und Bewertungsstatus; sortierbar |
| Position | Kursverlauf als Kerzenchart mit Käufen und Verkäufen, Kennzahlen der Position, Transaktionen des Wertpapiers |
| Transaktionen | alle gespeicherten Transaktionen mit Filter nach Symbol, Name und Art |
| Planner / Aktionen | Engine-Aktionen je Position, geplante und zurückgestellte Entries, Entry-Kandidaten mit Sektor- und Korrelationsrisiko |
| Watchlist | Tabelle mit Filtern und Sortierung; Strategie, Priorität und Sektor in der Tabelle wählen, „Übernehmen“ speichert (Strategie, dann Priorität, dann Sektor; ein Fehler stoppt die Folge); Hinzufügen mit Prüfen, Entfernen nach Bestätigung; „Neues Wertpapier aufnehmen“ (Prüfen, Anlegen nur nach sauberer Prüfung des unveränderten Formulars) |
| Sektoren | Treemap und Tabellen der Sektoren, ETF-Look-through; Reiter „Einzelwertrisiko“ mit eigener Treemap, Unternehmens- und ETF-Tabelle |
| Aufgaben | die sieben Windows-Tasks mit Zustand (auch „deaktiviert“), letztem Lauf und Ergebnis, nächstem Lauf, Datenstand, „Jetzt ausführen“ nach Bestätigung (startet genau den bestehenden Windows-Task, verfolgt bis zum Ende), Log-Ende, ETF-Datenstand; Zeitplan, Aktivieren und Anlegen über `viper-ai tasks` |
| Import | Datei wählen, Prüfen (schreibt nichts), offene Punkte entscheiden (jede Entscheidung braucht eine neue Prüfung), Importieren mit dem Token der Prüfung nach Bestätigung |
| Chat | Fragen an das lokale Modell mit den READ-Tools des MCP-Profils `read`; Antwort, Thinking, Tool-Aufrufe und Fehler getrennt; Stopp, Neuer Chat |
| Intelligence | Analyse starten; aktive Thesen mit Vorschlag, Engine-Aktion, Konfidenz, Datenstand, Gültigkeit und Neubewertungsgründen; Analysen und Prozesse (Fortsetzen nach Abbruch); letzte Ereignisse |
| Analyse | ein Analysefall: Vorschlag neben der Engine-Aktion (keine Order), These und Gegenthese mit Belegen, Annahmen, Unsicherheit, Datenlücken, Risiko- und Portfoliokontext, Belegkatalog, tatsächliche Transaktionen seit der Analyse, Schrittprotokoll |
| Einstellungen | Diagnose (Pfade, Datenbank, Ollama, Tool-Klassifikation) und die Einstellungen, die das Web nutzt |

- Datenstand, Währung und Berechnungsbasis stehen im Kopf jeder Seite; der Tooltip „Berechnungsbasis“ nennt die Regeln aus Abschnitt 4.
- Die Zeiträume und ihre Kennzahlen berechnet die Engine (Abschnitt 4); die Seite schneidet nur die Tageswerte für den Chart aus und zeigt Lücken als Unterbrechung der Linie.
- Jede Seite ruft dasselbe Tool mit denselben Argumenten und demselben View-Modell auf wie die frühere Desktop-GUI; Wortlaut, Reason-Code-Texte, Prüfungen und die Reihenfolge der Schreibschritte sind gleich.
- Eine Importdatei wird in `%LOCALAPPDATA%\TradingAgentGui\web-import` abgelegt (die Tools lesen nur einen lokalen Pfad); sie ist nie Teil des Pakets oder des Runtime-Roots.
- Der Chat baut den MCP-Server im Profil `read` im eigenen Prozess (ohne ihn zu starten) und gibt dem Modell dessen Tools: dieselben Namen, Beschreibungen, Schemas, kompakten Antworten und dieselbe Grenze von 40.000 Zeichen wie jedem MCP-Client.
- „Neu berechnen“ lässt den Orchestrator sofort neu rechnen statt das bis zu 60 Sekunden alte Ergebnis zu zeigen.

## 6. Start, Einstellungen und Produktion

- `viper-ai-gui` ist der Standardstart (ohne Konsolenfenster): Er startet den Server, falls er noch nicht läuft, und öffnet den Browser; läuft VIPER schon, öffnet er nur den Browser. Die Desktop-Verknüpfung, die bisher `viper-ai-gui.exe` startete, behält damit ihr Ziel. `viper-ai-web` tut dasselbe mit Konsole.
- Ohne Konsole schreibt der Server seine Ausgaben nach `%LOCALAPPDATA%\TradingAgentGui\web.log`. Die Einstellungen liegen wie bisher in `%LOCALAPPDATA%\TradingAgentGui\settings.json` (Ollama-URL und -Modell, Datenbank, Log-Ordner der Tasks); die Seite Einstellungen ändert sie.
- Die Windows-Aufgabenplanung ist der einzige Scheduler. Die Jobs laufen nach ihren Windows-Triggern (lokale Ortszeit, DST-fest, `StartWhenAvailable`, `MultipleInstances = IgnoreNew`), unabhängig davon, ob VIPER läuft; angelegt, geändert, aktiviert und gelöscht werden sie mit `viper-ai tasks list|deploy|edit|delete`. Der Autostart (`viper-ai-gui --no-browser` im Ordner `shell:startup`) startet nur die Oberfläche.
- Der in M3a eingeführte integrierte Scheduler (APScheduler) wurde wieder entfernt. Die Tabellen `scheduled_job` und `job_run` einer Datenbank, in der er lief, bleiben unverändert liegen und werden nicht mehr gelesen.

Umstellung der Produktion (je Schritt umkehrbar, Laufzeitdaten bleiben unberührt):

1. Die neue Version mit dem Extra `web` installieren (`[gui]` ist ein Alias und installiert dasselbe).
2. `viper-ai run Migrate-TradingAnalysisCases.py --write` (additive Tabelle; zuerst ohne `--write` prüfen).
3. Autostart wie oben einrichten und VIPER starten.
4. Die Windows-Tasks bereitstellen und aktivieren: `viper-ai tasks deploy`, dann für jeden der sieben Tasks `viper-ai tasks edit <task> --enable` (`deploy` behält den Aktiviert-Zustand; ohne diesen Schritt läuft kein Job, wenn sie zuvor für die Übernahme durch den integrierten Scheduler deaktiviert wurden). Mit `viper-ai tasks list` prüfen.
5. Rückweg: das lokal aufbewahrte v0.6.0-Wheel wieder installieren (`pip install "<Pfad>\viper_ai-0.6.0-py3-none-any.whl[gui]"`; der Tag ist nicht mehr auf GitHub) und die Windows-Tasks mit `viper-ai tasks edit <task> --enable` wieder aktivieren.

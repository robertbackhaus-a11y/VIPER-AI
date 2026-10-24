# Web-Frontend

English version: [web.en.md](web.en.md)

Das Web-Frontend ist die Oberfläche von VIPER-AI im lokalen Browser. Es ist die einzige Oberfläche (die frühere Qt-Desktop-GUI ist vollständig entfernt); Plan und Stand stehen in der Roadmap des Repositorys. Es enthält keine Fachlogik: Jede Zahl kommt aus der bestehenden Engine.

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
viper-ai start
```

- `viper-ai-web` läuft mit Konsole; `viper-ai start` und `viper-ai-start` (ohne Konsolenfenster, für Verknüpfung und Autostart) starten denselben Server im Hintergrund und öffnen den Browser. Läuft VIPER schon, wird nur der Browser geöffnet.
- Das Web startet nie selbst einen Job, auch nicht beim Start oder Neustart (Abschnitt 7). `--no-scheduler` wird noch angenommen, hat aber keine Wirkung.
- Ohne `--db` gilt `trading_db_path` der Einstellungsdatei (Abschnitt 6), sonst `<root>\data\trading.db` aus `runtime_paths`.
- Im Repository startet `python -m web` dasselbe; in der Installation auch `python -m viper_ai.web`.

## 3. API

| Pfad | Inhalt | Quelle |
|---|---|---|
| `GET /api/status` | Version, Datenbankstatus, Ollama-Status (echte Prüfung mit kurzem Timeout: erreichbar und gewähltes Modell vorhanden), Pfad, Hinweise | `database_status`, Ollama |
| `GET /api/portfolio` | offene Positionen mit Marktwert, Einstandswert, unrealisiertem Ergebnis, Tagesänderung; Summen | `portfolio_history.holdings` |
| `GET /api/portfolio/history` | Tageswerte (investiertes Kapital, Marktwert, Nettozufluss, Dividenden in EUR), Kennzahlen je Zeitraum, fehlende Kurse, EZB-Stand | `portfolio_history.portfolio_history` |
| `GET /api/transactions` | gespeicherte Transaktionen, neueste zuerst (`security_id`, `limit`) | `portfolio_history.transactions` |
| `GET /api/securities/{id}/prices` | Tagesbalken eines Wertpapiers in der Handelswährung | `portfolio_history.price_history` |
| `GET /api/views/dashboard` | Planner- und Engine-Block des Desktop-Dashboards (dieselben Texte und Tabellen) | `run_trading_orchestrator`, `database_status` |
| `GET /api/views/planner`, `/api/views/watchlist`, `/api/views/sectors`, `/api/views/tasks` | Tabellen und Texte der Seiten Planner, Watchlist, Sektoren (mit Einzelwertrisiko) und Aufgaben | View-Modelle von `viper_ai/shared/viewmodels` |
| `POST /api/watchlist/add`, `/api/watchlist/{id}/remove`, `/api/watchlist/{id}/edit`, `/api/onboarding` | Watchlist-Aktionen und Neuaufnahme eines Wertpapiers (Prüfen, dann Anlegen) | `add_watchlist_candidate`, `remove_watchlist_candidate`, `set_security_strategy`, `set_watchlist_priority`, `set_security_sector`, `onboard_security` |
| `POST /api/tasks/deploy`, `/api/tasks/{name}/edit` (`times`, `day`, `enabled`), `/delete`, `/restore`, `/run`; `GET /api/tasks/{name}/log` | Definitionen in den Scheduler übertragen, Task ändern, aktivieren/deaktivieren, löschen, wiederherstellen, jetzt ausführen, Log-Ende lesen | `viper_ai.tasks` (Abschnitt 7) |
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
| Aufgaben | die sieben VIPER-Tasks: Zeitplan (Uhrzeit(en) und Tag) und Aktiv-Schalter der VIPER-Definition, Zustand im Scheduler mit letztem Lauf und Ergebnis, nächster Lauf, Datenstand, Abweichung des Schedulers von der Definition (⚠), „Jetzt ausführen“ nach Bestätigung (über den Scheduler, verfolgt bis zum Ende), Log-Ende, Löschen und Wiederherstellen, „Deployen“; darunter der ETF-Datenstand |
| Import | Datei wählen, Prüfen (schreibt nichts), offene Punkte entscheiden (jede Entscheidung braucht eine neue Prüfung), Importieren mit dem Token der Prüfung nach Bestätigung |
| Chat | Fragen an das lokale Modell mit den READ-Tools des MCP-Profils `read`; Antwort, Thinking, Tool-Aufrufe und Fehler getrennt; Stopp, Neuer Chat |
| Intelligence | Analyse starten; aktive Thesen mit Vorschlag, Engine-Aktion, Konfidenz, Datenstand, Gültigkeit und Neubewertungsgründen; Analysen und Prozesse (Fortsetzen nach Abbruch); letzte Ereignisse |
| Analyse | ein Analysefall: Vorschlag neben der Engine-Aktion (keine Order), These und Gegenthese mit Belegen, Annahmen, Unsicherheit, Datenlücken, Risiko- und Portfoliokontext, Belegkatalog, tatsächliche Transaktionen seit der Analyse, Schrittprotokoll |
| Einstellungen | Diagnose (Pfade, Datenbank, Ollama, Tool-Klassifikation) und die Einstellungen, die das Web nutzt; das Modell wird aus allen Modellen des laufenden Ollama gewählt (ein nicht installiertes ist markiert) |

- Datenstand, Währung und Berechnungsbasis stehen im Kopf jeder Seite; der Tooltip „Berechnungsbasis“ nennt die Regeln aus Abschnitt 4.
- Rechts oben stehen „Datenbank OK/Fehler“ und „Ollama OK/Fehler“ (gelb „Modell fehlt“, wenn Ollama läuft, das eingestellte Modell aber nicht installiert ist; Details im Tooltip und auf der Seite Einstellungen) und das Zahnrad, das die Einstellungen öffnet. Ist Ollama nicht erreichbar, betrifft das nur Chat und Analysen; Dashboard, Portfolio und Engine arbeiten weiter.
- ☰ oben links klappt die Navigation auf eine Symbolleiste ein (Name als Tooltip) und wieder aus; der Inhalt nutzt dann die frei werdende Breite. Der Zustand gilt für diesen Browser (localStorage). In schmalen Fenstern blendet ☰ die Navigation wie bisher ein und aus.
- Die Zeiträume und ihre Kennzahlen berechnet die Engine (Abschnitt 4); die Seite schneidet nur die Tageswerte für den Chart aus und zeigt Lücken als Unterbrechung der Linie.
- Jede Seite ruft dasselbe Tool mit denselben Argumenten und demselben View-Modell auf wie früher die Desktop-GUI; Wortlaut, Reason-Code-Texte, Prüfungen und die Reihenfolge der Schreibschritte sind gleich.
- Eine Importdatei wird im Einstellungsordner unter `web-import` abgelegt (die Tools lesen nur einen lokalen Pfad); sie ist nie Teil des Pakets oder des Runtime-Roots.
- Der Chat baut den MCP-Server im Profil `read` im eigenen Prozess (ohne ihn zu starten) und gibt dem Modell dessen Tools: dieselben Namen, Beschreibungen, Schemas, kompakten Antworten und dieselbe Grenze von 40.000 Zeichen wie jedem MCP-Client.
- „Neu berechnen“ lässt den Orchestrator sofort neu rechnen statt das bis zu 60 Sekunden alte Ergebnis zu zeigen.

## 6. Start, Einstellungen und Produktion

- `viper-ai-start` ist der Standardstart ohne Konsolenfenster (Ziel der Desktop-Verknüpfung und des Autostarts `viper-ai-start --no-browser` im Ordner `shell:startup`); `viper-ai start` tut dasselbe aus einer Konsole, `viper-ai-web` läuft mit Konsole. Das frühere `viper-ai-gui` gibt es nicht mehr.
- Ohne Konsole schreibt der Server seine Ausgaben nach `web.log` im Einstellungsordner. Der Einstellungsordner kommt aus `runtime_paths`: Windows `%LOCALAPPDATA%\VIPER-AI`, Linux `$XDG_CONFIG_HOME/viper-ai` (`~/.config/viper-ai`). Er enthält `settings.json` (Ollama-URL und -Modell, Datenbank, Aktualisierung, Thinking); die Seite Einstellungen ändert sie. Solange es dort keine `settings.json` gibt, wird die des früheren Ordners `%LOCALAPPDATA%\TradingAgentGui` gelesen (nie geändert); das erste Speichern schreibt in den neuen Ordner.
- Die Log-Ordner der Tasks und die Task-Definitionen liegen im Runtime-Root (`<root>\logs`, `<root>\scheduler\tasks.json`); sie sind keine Einstellung mehr.

Umstellung der Produktion auf diese Version (je Schritt umkehrbar, Laufzeitdaten bleiben unberührt):

1. Die neue Version mit dem Extra `web` installieren (auch aus einem Checkout ohne vorheriges Aufräumen: jeder Build beginnt mit einem leeren `build/lib`). Das Extra `[gui]` gibt es nicht mehr.
2. Verknüpfungen umstellen: Autostart (`shell:startup\VIPER-AI.lnk`) und Desktop-Verknüpfung auf `<venv>\Scripts\viper-ai-start.exe` (Autostart mit `--no-browser`); `viper-ai-gui.exe` wird beim Upgrade entfernt.
3. `viper-ai tasks deploy`: überträgt die sieben Definitionen (Aktion, Zeitplan, Einstellungen, **Aktiviert-Zustand**) in die Windows-Aufgabenplanung. Waren die Tasks deaktiviert (zum Beispiel nach der Übernahme durch den inzwischen entfernten integrierten Scheduler), sind sie danach wieder aktiv. Mit `viper-ai tasks list` prüfen.
4. Rückweg: die vorige Version wieder installieren; die Tasks bleiben dabei, wie sie sind (Name, Aktion und Zeitplan haben sich nicht geändert).

## 7. Aufgaben (geplante Tasks)

VIPER hält die Definitionen der sieben Tasks selbst; der Scheduler des Betriebssystems ist nur das Ziel, auf das sie übertragen werden.

```
viper_ai/tasks/definitions.py   die sieben Tasks (Name, Skript, Argumente, Zeitplan, aktiv, Zeitlimit, bei Anmeldung)
  + <root>/scheduler/tasks.json  Änderungen dieser Installation (Zeitplan, aktiv, gelöscht)
          |
          +--> Windows: Aufgabenplanung   (viper_ai/tasks/windows.py, Task-XML, Register-ScheduledTask)
          +--> Linux:   systemd-Benutzer-Timer (viper_ai/tasks/linux.py, viper-<task>.service + .timer)
```

| Task | Skript und Argumente | Zeitplan (Ortszeit) | Zeitlimit | bei Anmeldung |
|---|---|---|---|---|
| `Trading-MarketData-Backfill` | `Backfill-TradingMarketData.py --write` | täglich 17:15 | 72 h | ja |
| `Trading-FXRates-Backfill` | `Backfill-TradingFXRatesECB.py --write --quote-currency USD,GBP,AUD,KRW` | täglich 17:20 | 72 h | ja |
| `Trading-EventsNews-Backfill` | `Backfill-TradingEventsNews.py --write` | täglich 17:25 | 72 h | ja |
| `Trading-Fundamentals-SEC` | `Backfill-TradingFundamentalsSEC.py` | So 10:00 | 72 h | ja |
| `Trading-Candidate-Discovery` | `Discover-TradingCandidates.py` | täglich 17:35 | 2 h | nein |
| `Trading-Market-Intelligence` | `Collect-TradingMarketIntelligence.py` | täglich 08:15 und 17:45 | 2 h | nein |
| `Trading-ETF-Refresh` | `Refresh-TradingEtfData.py --write` | So 11:00 | 1 h | nein |

- Jeder Lauf ist `Run-TradingTask.py <root>/logs/<log> <Skript> [Argumente]` mit dem Python dieser Installation (Windows `pythonw.exe`, ohne Konsolenfenster); der Starter hängt die Ausgabe an das Log und gibt den Exitcode weiter.
- `viper-ai tasks deploy` (oder „Deployen“ auf der Seite Aufgaben) legt fehlende Tasks an, passt abweichende an (Aktion, Pfade, Zeitplan, Einstellungen, aktiv) und entfernt gelöschte; ein zweiter Lauf ändert nichts. Wird ein Task außerhalb von VIPER geändert, zeigt die Seite die Abweichung, und der nächste Deploy stellt die VIPER-Definition wieder her.
- `viper-ai tasks edit <task> --at HH:MM[,HH:MM] --day daily|monday..sunday --enable|--disable` (oder die Felder der Seite) ändert die VIPER-Definition und überträgt den Task sofort. `delete <task>` markiert ihn in VIPER als gelöscht und entfernt ihn aus dem Scheduler; `restore <task>` holt die eingebaute Definition zurück. `run <task>` startet ihn jetzt über den Scheduler (nie einen laufenden, deaktivierten oder nicht deployten). `list` und `status` zeigen Definition, Zustand, Läufe und Abweichungen.
- Nur die sieben VIPER-Tasks werden je angefasst; ein fremder Name wird abgewiesen, systemd-Dateien ohne VIPER-Markierung werden nie überschrieben oder gelöscht.
- Windows: Startzeiten als Ortszeit ohne UTC-Versatz (`2026-01-01T17:15:00`), dadurch bleibt die Uhrzeit über die Sommer-/Winterzeit gleich; `StartWhenAvailable` holt verpasste Läufe nach, `MultipleInstancesPolicy IgnoreNew` verhindert einen zweiten gleichzeitigen Lauf; der Task läuft unter dem angemeldeten Benutzer ohne gespeichertes Passwort. `deploy` verweigert, wenn die Benutzer-Umgebungsvariable `VIPER_TRADING_ROOT` (bzw. der Standard) nicht der Root ist, für den deployt wird.
- Linux: `OnCalendar=*-*-* 17:15:00` bzw. `Sun *-*-* 11:00:00` in der lokalen Zeitzone (sommerzeitfest), `Persistent=true` holt verpasste Läufe nach, ein laufender `oneshot`-Dienst wird nicht ein zweites Mal gestartet, `TimeoutStartSec` ist das Zeitlimit, `VIPER_TRADING_ROOT` steht im Dienst. „Bei Anmeldung“ = der Dienst hängt an `default.target` des Benutzer-Managers. Damit Timer auch ohne Anmeldung laufen: `loginctl enable-linger <user>` (optional).
- Es gibt keinen Scheduler-Prozess von VIPER: Der Web-Server plant und startet nichts von sich aus. Der in M3a eingeführte integrierte Scheduler (APScheduler) ist entfernt; die Tabellen `scheduled_job` und `job_run` einer Datenbank, in der er lief, bleiben unverändert liegen und werden nicht gelesen.

## 8. Ansichten

Aufgenommen mit einer Demo-Datenbank mit erfundenen Werten (Beispieldatei `examples/trading-import-example.csv`, künstliche Kurse); keine echten Depotdaten.

![Dashboard](images/web-dashboard.png)
![Portfolio](images/web-portfolio.png)
![Planner / Aktionen](images/web-planner.png)
![Watchlist](images/web-watchlist.png)
![Sektoren](images/web-sectors.png)
![Intelligence](images/web-intelligence.png)
![Aufgaben](images/web-tasks.png)

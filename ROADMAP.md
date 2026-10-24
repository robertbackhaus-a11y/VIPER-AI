# VIPER-AI – Roadmap

**Stand:** 2026-10-24
**Ausgangspunkt:** VIPER-AI v0.6.0
**Ziel:** Eigenständige lokale Trading- und Analyseplattform mit Weboberfläche und VIPER-spezifischer Reasoning-Orchestrierung.

Diese Roadmap ist die verbindliche Arbeitsgrundlage. Architektur und Begründungen: [ARCHITECTURE.md](ARCHITECTURE.md).
Status wird erst nach Prüfung auf „erledigt“ gesetzt.

## Status

| Etappe | Status | Ergebnis |
|---|---|---|
| M0 Bestand und Architekturgrenzen | erledigt | Bestandsübersicht, Modulgrenzen, Migrationsliste (unten) |
| M1 Dünne Web-API | erledigt | `web/` (FastAPI, `viper-ai-web`), Engine-Modul `portfolio_history.py` |
| M2 Portfolio-Dashboard | erledigt | Seiten Dashboard, Portfolio, Position, Transaktionen |
| M3 Qt-Funktionsparität | erledigt | alle Qt-Workflows im Browser (Liste unten); Qt bleibt bis M6 verfügbar |
| M4 Reasoning-Orchestrierung | erledigt | LangGraph-Workflow `viper_reasoning.py`, Speicherung `analysis_store.py` |
| M5 Intelligence-Cockpit | erledigt | Seite Intelligence mit Thesen, Analysen, Prozessen, Ereignissen und Analysedetail |
| M3a Integrierte Aufgabenverwaltung | verworfen (24.10.2026) | APScheduler wieder entfernt; Windows-Aufgabenplanung bleibt einziger Scheduler, das Web verwaltet sie nur. Produktion läuft bis zum nächsten Update noch mit dem integrierten Scheduler |
| M3b Depotwert-Graph mit vollständiger Historie | erledigt (Code) | Zeiträume bis MAX, zeitgewichtete Rendite; EZB-Backfill in der Produktion offen |
| M3c ETF-Datentask: Aktualität und Zeitzone | erledigt | Ursachen: UTC-verankerte Windows-Trigger, Checkout-Instanz ohne private ETF-Dateien |
| M3d Windows-Tasks per CLI verwalten | erledigt (Code) | `viper-ai tasks list/deploy/edit/delete` über `Manage-TradingTasks.ps1` |
| M6 Web-Cutover und Qt-Ablösung | erledigt (Code) | Web ist Standardstart, Qt entfernt; Produktion seit 24.10.2026 umgestellt |
| Architektur-Bereinigung | erledigt (Code) | ein Runtime-Root aus `runtime_paths.py` (`C:\tools\Viper-AI`), Windows-Tasks als einziger Scheduler, Host/Origin-Prüfung für schreibende Web-Aufrufe |

## Leitlinien

- VIPER bleibt eigenständig; keine Laufzeitabhängigkeit von REAPER-AI.
- Bestehende Trading-Engine, SQLite-Daten und bewährte Scheduler-Funktionen wiederverwenden.
- Parqet dient als Referenz für Portfolioübersichten, nicht als Vorlage für einen vollständigen Nachbau.
- Reasoning und Prozessarchitektur werden für Trading entwickelt, nicht aus REAPER übernommen.
- Schlanke lokale Single-User-Architektur; keine unnötigen Frameworks oder Enterprise-Strukturen.
- Dokumentation zusammen mit jeder funktionalen Änderung aktualisieren; wenige sinnvolle Tests statt großer Testsuiten.

## Etappen

### M0 – Bestand und Architekturgrenzen (P0) – erledigt

**Aufgaben**
- Vorhandene Module und Datenflüsse der v0.6.0 erfassen: Qt, Engine, SQLite, Scheduler, Datenquellen, Analysefunktionen.
- Klären, wo UI und Business-Logik gekoppelt sind.
- Wiederverwendbare Funktionen und notwendige API-Schnittstellen festlegen.
- Bestehende Berechnungskonventionen für Depotwert, Performance, FX und historische Zeitreihen dokumentieren.

**Abnahme:** Bestandsübersicht, Modulgrenzen und Migrationsliste liegen vor; keine funktionsgleiche Neuentwicklung eingeplant.

**Ergebnis**
- Bestandsübersicht, Kopplungen und Berechnungskonventionen: [ARCHITECTURE.md, Abschnitt 2](ARCHITECTURE.md#2-ist-stand-v060).
- Die Qt-Oberfläche enthält keine Fachlogik. Sie liest ausschließlich über die MCP-Fassade `trading_sqlite.Tools`; ihre View-Modelle
  (`gui/viewmodels/`) und die Dienste für Scheduler, Ollama und Einstellungen sind Qt-frei und werden vom Web weiterverwendet.
- Die Web-API ruft dieselbe Fassade im eigenen Prozess auf, mit derselben Tool-Policy wie die Qt-GUI (READ + `GUI_ACTIONS`).
- Fehlend und für M2/M3 neu nötig (keine Doppelung, es gibt sie nirgends): Transaktionsliste, Kursverlauf je Wertpapier und Depotverlauf
  aus Transaktionen. Sie kommen als ein rein lesendes Engine-Modul nach `tools/trading/`, nicht in die Endpunkte.
- Datenlage für Zeitreihen: Transaktionen reichen weiter zurück als Tageskurse und EZB-Kurse; ein Depotwert in EUR ist nur ab dem Tag
  belastbar, an dem jede gehaltene Position einen Kurs und einen EZB-Kurs hat. Davor zeigt der Verlauf nur das investierte Kapital.

**Migrationsliste Qt → Web**

| Qt-Workflow | Quelle | Ziel |
|---|---|---|
| Dashboard: Portfolio, Allokation, Cash, External Funding, Planner-Status, Entries, Blocker | `run_trading_orchestrator`, `database_status` | M2 |
| Planner / Actions: Engine-Aktionen, geplante und zurückgestellte Entries, Sektor-/Korrelationsrisiko | `run_trading_orchestrator` | M3 |
| Watchlist: Tabelle, Filter, Sortierung | `get_watchlist` | M3 |
| Watchlist: hinzufügen, entfernen, Priorität, Strategie, Sektor | `add_watchlist_candidate`, `remove_watchlist_candidate`, `set_watchlist_priority`, `set_security_strategy`, `set_security_sector` | M3 |
| Watchlist: neues Wertpapier aufnehmen (Prüfen, Anlegen) | `onboard_security` | M3 |
| Sektoren: Treemap, Sektor-, Positions- und ETF-Tabelle | `get_sector_overview` | M3 |
| Sektoren: Einzelwertrisiko mit Treemap | `get_effective_exposure` | M3 |
| Tasks: sieben Scheduler-Tasks, Datenstand, Jetzt ausführen, Log-Ende, ETF-Datenstand | Windows Task Scheduler, `get_data_freshness` | M3 |
| Import: Prüfen, offene Punkte entscheiden, Importieren, Abgleich | `check_transaction_import`, `import_transactions` | M3 |
| Chat: lokales Modell mit READ-Tools | Ollama, Tool-Loop | M3 |
| Diagnostics / Settings: Status, Pfade, Tool-Klassifikation, Einstellungen | `database_status`, Einstellungsdatei | M3 |
| Neu: Positionsdetail, Kursverlauf, Transaktionshistorie, Depotverlauf | neues Modul `portfolio_history.py` | M2/M3 |

### M1 – Dünne Web-API (P0; nach M0)

**Aufgaben**
- FastAPI als lokale HTTP-Schicht anbinden.
- Endpunkte für Depotübersicht, Positionen, Transaktionen, Performance, Watchlist, Marktdaten und bestehende Analyseergebnisse bereitstellen.
- Berechnungen in der vorhandenen Engine belassen; keine Geschäftslogik in Endpunkten duplizieren.
- Server-Sent Events (SSE) für länger laufende Aktionen und Statusmeldungen nur bei Bedarf ergänzen.

**Abnahme:** API-Werte stimmen mit den bestehenden VIPER-Berechnungen überein; bestehender Scheduler läuft weiter.

**Ergebnis**
- `web/app.py` (FastAPI, nur 127.0.0.1), `web/engine.py` (Fassade `Tools` im Prozess, nur READ + `GUI_ACTIONS`), Start `viper-ai-web`
  bzw. `python -m web`, Extra `web` (`fastapi`). Endpunkte: [docs/web.de.md](docs/web.de.md#3-api).
- Neues, rein lesendes Engine-Modul `tools/trading/portfolio_history.py`: Bestände mit Einstandswert, Transaktionen, Tageskurse,
  Depotverlauf. Der Kostenschritt des Imports ist dafür als `transaction_import.apply_cost_basis` herausgelöst (unverändertes Verhalten).
- Geprüft gegen die Produktionsdatenbank (nur lesend): API-Depotwert = Wert von `build_portfolio_context`, Einstandswert des
  nachgespielten Verlaufs = Summe `remaining_cost_basis`. Scheduler und MCP-Server unverändert.
- SSE: noch kein Bedarf; kommt mit den ersten länger laufenden Aktionen (Tasks, Analysen).

### M2 – Portfolio-Dashboard (P0; nach M1)

**Aufgaben**
- Lokal bereitgestelltes Webfrontend mit Navigation und responsivem Dashboard umsetzen.
- Kennzahlen: aktueller Depotwert, investiertes Kapital, absoluter/relativer Gewinn und Verlust, Tagesänderung; Performance-Kennzahlen nur soweit Daten und Berechnung sie belastbar tragen.
- Depot- und Kapitalentwicklung mit Zeitraumfiltern, Top-/Flop-Positionen und Allokation anzeigen.
- Charts für Portfolioverläufe und geeignete Finanzzeitreihen integrieren.
- Datenstand, Währung und Berechnungsbasis sichtbar machen.

**Abnahme:** Mit echten VIPER-Daten nutzbare Portfolioübersicht; Kennzahlen fachlich konsistent.

**Ergebnis**
- Seite `web/static` (HTML, CSS, JavaScript-Module, ohne Build-Schritt), dunkles Theme mit dem VIPER-Logo; ECharts und Lightweight Charts
  liegen lokal unter `web/static/vendor`. Seiten: [docs/web.de.md](docs/web.de.md#5-seiten).
- Mit der Produktionsdatenbank im Browser geprüft (nur lesend): Depotwert, Einstand, Tagesänderung und Allokation stimmen mit Engine
  und Sektorseite überein, der Planner-Block mit dem Desktop-Dashboard; Kursverlauf mit Transaktionsmarkern.
- Nicht gezeigt, weil nicht belastbar: Marktwert vor dem ersten vollständig bewertbaren Tag (fehlende EZB-Kurse), zeitgewichtete Rendite (keine Ein-/Auszahlungsdaten).
  Ein Backfill älterer EZB-Kurse würde den Marktwertverlauf verlängern.

### M3 – Qt-Funktionsparität (P0; nach M2)

**Aufgaben**
- Positionseinzelansichten, Kursverlauf, Watchlist, Transaktionshistorie und bestehende Analysen im Web anbieten.
- Bestehende Einstellungen und Bedienaktionen übernehmen, soweit sie im Alltag benötigt werden.
- Workflow-Liste der heutigen Qt-Oberfläche Punkt für Punkt gegen den Webstand prüfen.
- Qt bis zur bestätigten Parität weiter verfügbar halten.

**Abnahme:** Alle betriebsnotwendigen Qt-Workflows funktionieren im Browser, ohne Verlust vorhandener Daten oder Funktionen.

**Ergebnis** (Endpunkte `web/pages.py`, `web/chat.py`; Seiten [docs/web.de.md](docs/web.de.md#5-seiten))

| Qt-Workflow | Web | Prüfung |
|---|---|---|
| Dashboard | erledigt (M2) | Browser, echte Daten |
| Planner / Actions | erledigt | Browser, echte Daten |
| Watchlist: Tabelle, Filter, Sortierung | erledigt | Browser, echte Daten |
| Watchlist: hinzufügen, entfernen, Priorität, Strategie, Sektor | erledigt | Test (synthetische Datenbank) |
| Neues Wertpapier aufnehmen | erledigt | Test (Prüfen schreibt nichts, Anlegen danach) |
| Sektoren und Einzelwertrisiko mit Treemaps | erledigt | Browser, echte Daten |
| Tasks: Datenstand, Jetzt ausführen, Log, ETF-Datenstand | erledigt | Browser, echte Daten (ohne Start eines Produktionstasks) |
| Import: Prüfen, Entscheidungen, Importieren, Abgleich | erledigt | Test (ohne Token kein Import) |
| Chat mit READ-Tools | erledigt | Test mit Fake-Ollama (Aktionen werden abgelehnt) |
| Diagnostics / Settings | erledigt | Browser |
| Positionsdetail, Kursverlauf, Transaktionshistorie | erledigt (M2) | Browser, echte Daten |

- Nicht übernommen, weil im Web ohne Funktion: „MCP neu starten“ (das Web nutzt keinen MCP-Kindprozess), Fenstergeometrie.
- In einem Repository-Checkout liest die Engine die privaten Dateien (`*_local.json`) aus `tools/trading/universe`, wie bisher; nur die
  pip-Installation liest sie aus dem Runtime-Root.

### M4 – VIPER-spezifische Reasoning-Orchestrierung (P1; Entwurf parallel ab M1)

**Aufgaben**
- Fachlichen Analyseprozess definieren: Beobachten → quantitative/qualitative Analyse → Hypothesen/Gegenhypothesen → Abwägung → Handlungsvorschlag → Wiedervorlage.
- Deterministische Berechnungen klar von LLM-Interpretation trennen.
- Analyseauftrag, Datenzeitpunkt, Quellen/Evidenz, Annahmen, Gegenargumente, Unsicherheiten und Ergebnis nachvollziehbar speichern.
- Zunächst einen kompletten End-to-End-Analysefall implementieren; nur bei erwiesenem Nutzen weitere Spezialagenten einführen.
- Gezieltes, bedarfsabhängiges Reasoning statt dauerhaftem Agent-Loop verwenden.
- Prozesse bei neuen Kursen, Unternehmensmeldungen oder Portfolioänderungen gezielt erneut auswertbar machen.

**Abnahme:** Ein Analyseauftrag wird nachvollziehbar ausgeführt, gespeichert, überprüft und mit aktualisierten Daten erneut bewertet; keine automatische Orderausführung aus einem LLM-Ergebnis.

**Ergebnis** (Architektur: [ARCHITECTURE.md, 4.4](ARCHITECTURE.md#44-reasoning-orchestrierung--eigenständig-für-viper))
- Ein vollständiger Analysefall je Wertpapier mit LangGraph (Vorgabe): Beobachten, Datenlage, quantitativ und qualitativ parallel, These und
  Gegenthese, Belegprüfung mit Rücksprung, Risiko- und Portfoliokontext, Vorschlag, deterministische Prüfung, Speicherung.
- Recovery über den SQLite-Checkpointer von LangGraph; Neubewertung über den Fingerabdruck der Eingangsdaten und `supersedes_id`.
- Web-Endpunkte `/api/intelligence/*` (Analyse starten, fortsetzen, lesen). Geprüft (damals mit Tests, die mit der dauerhaften Testsuite entfernt wurden): kompletter Fall,
  Rücksprung bei erfundenen Belegen, Wiederaufnahme nach Modellfehler, Prüfung des Vorschlags, Neubewertung, Lauf über das Web mit Fake-Ollama.
- Auf einer Kopie der Produktionsdatenbank mit einem geskripteten Modell geprüft (Abbruch bei `decide` und Wiederaufnahme ab dem
  Checkpoint). Ein Lauf mit dem echten lokalen Modell steht aus: Ollama war während der Umsetzung nicht erreichbar.
- Betrieb: `viper-ai run Migrate-TradingAnalysisCases.py --write` lief am 24.10.2026 auf der Produktionsdatenbank (additiv; ohne die
  Tabellen schreibt die Analyse nichts und die Seite nennt den Befehl).

### M5 – Intelligence-Cockpit (P1; nach M4)

**Aufgaben**
- Aktive Investmentthesen, Analysen, Handlungsvorschläge und deren Gültigkeits-/Datenstand darstellen.
- Evidenz, Gegenargumente, Unsicherheit, Risikobeitrag und Portfoliokontext einsehbar machen.
- Analysehistorie, laufende Prozesse, letzte Ereignisse und bei Bedarf detaillierte Reasoning-/Tool-Protokolle verfügbar machen.
- Klare Trennung zwischen Empfehlungen, bestätigten Nutzeraktionen und tatsächlichen Transaktionen beibehalten.

**Abnahme:** Jede Empfehlung lässt sich ihrer Analyse, ihren Eingangsdaten und dem Bewertungszeitpunkt zuordnen.

**Ergebnis** (`web/static/js/intelligence.js`, Seite [docs/web.de.md](docs/web.de.md#5-seiten))
- Übersicht: Analyse starten (Wertpapier aus Bestand und Watchlist, optionale Frage), aktive Thesen mit Vorschlag, Engine-Aktion, Konfidenz,
  Datenstand, Gültigkeit und Neubewertungsgründen („Neu bewerten“ startet den Folgefall), Analysen und Prozesse mit Status (läuft,
  unterbrochen, fehlgeschlagen mit „Fortsetzen“), letzte Ereignisse des Market-Intelligence-Reports.
- Analysedetail: Vorschlag und Engine-Aktion nebeneinander, These und Gegenthese mit verlinkten Belegen, Annahmen, Unsicherheit,
  Datenlücken, Belegprüfung, Meldungslage, Risiko- und Portfoliokontext, Kennzahlen, Belegkatalog, Schrittprotokoll mit Ausgabe und Fehlern.
- Trennung: Der Vorschlag ist als „keine Order, keine Buchung“ markiert; tatsächliche Transaktionen seit der Analyse stehen getrennt daneben.
- Im Browser auf einer Kopie der Produktionsdatenbank geprüft (eine Analyse mit Abbruch und Wiederaufnahme).

### M3a – Integrierte Aufgabenverwaltung (P1; nach M5)

**Ziel:** Sämtliche VIPER-spezifischen Windows-Scheduled-Tasks künftig innerhalb von VIPER konfigurieren, ausführen und überwachen.

**Aufgaben**
- Vorhandene VIPER-Aufgaben der Windows-Aufgabenplanung mit Triggern, Parametern, Arbeitsverzeichnissen und Skripten ermitteln.
- Vorhandene Scheduling- und Jobmechanismen in VIPER prüfen und bevorzugt weiterverwenden; sonst APScheduler, keine eigene Scheduler-Engine.
- Jobdefinitionen, Aktivierungszustände, Zeitpläne und Ausführungsergebnisse in der bestehenden SQLite-Datenbank speichern.
- Web-Aufgabenverwaltung: Übersicht, Aktivieren/Deaktivieren, Zeitplan bearbeiten, Sofortstart, letztes Ergebnis, Fehlerdetails.
- Parallele Doppelausführung desselben Jobs verhindern; nach einem regulären Neustart wieder einplanen.
- Windows-Tasks ohne Änderung von Funktion und Intervallen übernehmen, mit wenigen gezielten Funktionstests prüfen.
- Die bisherigen Windows-Tasks erst nach erfolgreicher Übernahme deaktivieren, nie automatisch löschen.

Keine zusätzliche Datenbank, kein Message-Broker, kein separater Worker-Dienst. LangGraph bleibt für KI-Analyseworkflows zuständig, der
Scheduler für zeit- und ereignisgesteuerte Aufgaben.

**Abnahme:** Alle VIPER-Aufgaben laufen mit unveränderten Intervallen aus VIPER heraus, sind im Web steuerbar und überleben einen Neustart.

**Ergebnis**
- Bestand (Get-ScheduledTask, 24.10.2026): sieben Tasks, alle `pythonw.exe Run-TradingTask.py <log> <script> [args]`, `IgnoreNew`,
  `StartWhenAvailable`; Marktdaten 17:15, FX 17:20, Events/News 17:25 (jeweils auch bei Anmeldung, 72 h), Fundamentals So 10:00 (auch bei
  Anmeldung, 72 h), Discovery 17:35 (2 h), Market Intelligence 08:15 und 17:45 (2 h), ETF-Daten So 11:00 (1 h).
- Vorhandener Mechanismus weiterverwendet: der Starter `Run-TradingTask.py` (gleiche Logs, gleicher Exitcode) und die Task-Fachinfos der
  GUI; neu ist nur die Planung, und die macht APScheduler 3.11 (`BackgroundScheduler`, Cron-Trigger, `max_instances=1`).
- `tools/trading/job_store.py` + `Migrate-TradingScheduler.py`: Tabellen `scheduled_job` und `job_run` in der Trading-Datenbank, die sieben
  Jobs mit unveränderten Skripten, Argumenten, Logs, Zeiten und Zeitlimits, **deaktiviert** angelegt (kein Doppellauf neben Windows).
- `web/scheduler.py`: läuft im Prozess von `viper-ai-web` (kein Worker-Dienst), plant aktive Jobs nach jedem Start neu, holt verpasste Läufe
  einmal nach (wie `StartWhenAvailable`), „bei VIPER-Start“ ersetzt „bei Anmeldung“, verhindert Doppelläufe über eine `RUNNING`-Zeile in der
  Datenbank (auch zwischen zwei Prozessen), beendet Läufe nach dem Zeitlimit, markiert Läufe eines beendeten Prozesses als unterbrochen.
- Seite „Aufgaben“: Übersicht, Aktivieren/Deaktivieren, Zeitplan bearbeiten, Jetzt ausführen, letzter Lauf und Erfolg, Fehlerdetails,
  Zustand des Windows-Tasks, „Übernehmen“ (erst nach einem erfolgreichen Lauf in VIPER: Job aktivieren, Windows-Task deaktivieren, nie löschen).
- Prüfung (damals mit Tests, die mit der dauerhaften Testsuite entfernt wurden): Seeds unverändert, Lauf mit Starter/Log/Exitcode, kein Doppellauf, Zeitlimit, Neustart und
  Nachholen, Zeitplan, Übernahme; echte Skripte im Probelauf über den Scheduler (FX-Kurse, ETF-Daten: Exitcode 0).
- Die Übernahme wurde am 24.10.2026 in der Produktion ausgeführt (alle sieben Jobs, Windows-Tasks deaktiviert, nicht gelöscht).

**Korrektur (24.10.2026): verworfen.** Der integrierte Scheduler ist wieder entfernt (`web/scheduler.py`, `job_store.py`,
`Migrate-TradingScheduler.py`, die Seite und die API `/api/scheduler`, die Abhängigkeit `APScheduler`, `--no-scheduler` ohne Wirkung).
Begründung: Für sieben lokale Jobs braucht es keine zweite Scheduler-Wahrheit in SQLite neben der Windows-Aufgabenplanung; zwei Wege führen
nur zu Doppelläufen oder zu Jobs, die nicht laufen. Die Windows-Aufgabenplanung bleibt bewusst der einzige Scheduler (`viper-ai tasks`, M3d),
das Web ist nur die Verwaltungsoberfläche (Seite Aufgaben: Zustand, Läufe, Log, „Jetzt ausführen“ startet genau den Windows-Task). Das Web
startet keinen Job mehr beim Start, `StartWhenAvailable` und `IgnoreNew` der Tasks bleiben. Die Tabellen `scheduled_job` / `job_run` bleiben in
der Produktionsdatenbank unverändert liegen und werden nicht mehr gelesen.
- **Offen bis zum nächsten Produktionsupdate:** Die Produktion läuft noch mit dem integrierten Scheduler (Version `045f5f2`), die sieben
  Windows-Tasks sind deaktiviert. Beim Update im selben Schritt `viper-ai tasks deploy` und für alle sieben `viper-ai tasks edit <task> --enable`,
  sonst läuft danach kein Job ([docs/web.de.md, Abschnitt 6](docs/web.de.md#6-start-einstellungen-und-produktion)).

### M3b – Depotwert-Graph mit vollständiger Historie (P1; nach M3a)

**Aufgaben**
- Ursache der Begrenzung des Depotwert-Graphen prüfen (Anzeige, Abfrage oder fehlende historische Daten).
- Zeiträume 1M, 3M, 6M, 1J, 3J, 5J und MAX; MAX zeigt die gesamte tatsächlich verfügbare Depothistorie.
- Fehlende historische Werte nur aus Transaktionen, historischen Kursen und FX-Daten rekonstruieren, nie schätzen; Lücken im Chart zeigen.
- Ein- und Auszahlungen bei der Performance berücksichtigen; Depotwert und Performance sind getrennte Kennzahlen.
- Keine neuen Tabellen, Frameworks oder Dienste ohne Bedarf; wenige gezielte Tests.

**Abnahme:** Depotwertentwicklung von der ersten verfügbaren historischen Bewertung bis heute, ohne künstliche Grenze.

**Ergebnis**
- Ursache: Die „drei Monate“ waren nur die Vorauswahl der Anzeige; die Abfrage liefert seit M1 alle Tage ab der ersten Transaktion.
  Der Depotwert selbst fehlt aber dort, wo EZB-Kurse fehlen (die Datenbank hatte sie nur für die letzten Wochen, mit Lücken: der tägliche
  Task holt nur den jeweils letzten Kurs) und wo Tageskurse fehlen (Yahoo-Backfill zwei Jahre; früher verkaufte Wertpapiere haben keine).
- Rekonstruktion geprüft (Kopie der Produktionsdatenbank): Mit den amtlichen EZB-Kursen mehrerer Jahre (`Backfill-TradingFXRatesECB.py
  --start-date <Datum> --write`, bestehendes Skript) reicht der lückenlos bewertbare Verlauf deutlich weiter zurück; davor fehlen Kurse
  gehaltener Wertpapiere. Diese Tage bleiben Lücken; nichts wird geschätzt.
- `portfolio_history.portfolio_history` liefert zusätzlich je Tag Nettozufluss (Käufe inkl. Gebühren/Steuern minus Verkaufserlöse) und
  Dividenden sowie je Zeitraum (1M, 3M, 6M, 1J, 3J, 5J, MAX) Depotwert-Änderung, Nettozuflüsse, Dividenden, Ergebnis ohne Zuflüsse und die
  zeitgewichtete Rendite. Die Rendite wird nur über lückenlos bewertete Tage verkettet; ein Zeitraum, der vor den ersten Depotwert reicht,
  sagt „nicht berechenbar“ mit Grund. MAX beginnt beim ersten vollständig bewerteten Tag; der Chart zeigt davor das investierte Kapital.
- Dashboard: Zeiträume 1M bis MAX (Vorauswahl MAX), Kennzahlen des Zeitraums unter dem Chart, Lücken als Unterbrechung der Linie, Hinweis mit
  den fehlenden Kursen und dem EZB-Stand. Keine neue Tabelle, kein neuer Dienst. Test: Zuflüsse sind keine Rendite, Lücken werden nicht überbrückt.
- **Offen:** der einmalige EZB-Backfill in der Produktionsdatenbank (additiv; ergänzt amtliche Kurse, ändert keine Portfoliodaten) – nur nach
  deiner Freigabe.

### M3c – ETF-Datentask: Aktualität und Zeitzone (P1; nach M3b)

**Aufgaben**
- Prüfen, warum der ETF-Datenstatus „veraltet“ zeigt, obwohl der letzte Lauf am 24.10.2026 um 11:09 erfolgreich war.
- Prüfen, warum für den Sonntag-11:00-Task der nächste Lauf am 25.10.2026 um 10:00 angezeigt wird.
- Datenzeitstempel, Aktualitätsberechnung, Task-Ergebnisse und Zeitzonenverarbeitung prüfen; Zeitpläne in Europe/Berlin einschließlich
  Sommer-/Winterzeit auswerten, UTC nur als interne Zeitbasis.
- Technischen Task-Erfolg und fachliche Datenaktualität sauber trennen; tatsächliche Ursachen beheben, keine kosmetischen Korrekturen.

**Abnahme:** Status und nächster Lauf des ETF-Tasks stimmen mit Daten und Zeitplan überein; wenige gezielte Tests.

**Ergebnis**
- **Nächster Lauf 10:00 statt 11:00:** `New-ScheduledTaskTrigger -At` legt die Startzeit mit festem Versatz an (`2026-10-16T11:00:00+02:00`).
  Die Aufgabenplanung hält damit die UTC-Zeit fest; nach der Umstellung auf Winterzeit am 25.10.2026 wäre der Task um 10:00 gelaufen
  (Fundamentals 09:00). Windows meldete das selbst so; die Anzeige war richtig, der Zeitplan falsch. Behoben in `Manage-TradingTasks.ps1`:
  Startzeiten werden ohne Versatz angelegt (Ortszeit Europe/Berlin), vorhandene Tasks werden umgestellt (heute: `viper-ai tasks deploy`; nur die
  Startzeit, Uhrzeit, Tage, Anmelde-Auslöser, Aktion und Einstellungen bleiben). In der Produktion waren die Startzeiten bei der Prüfung am
  24.10.2026 bereits ohne Versatz; der Probelauf meldet „unverändert“.
- Die integrierte Aufgabenverwaltung rechnet ausdrücklich in `Europe/Berlin` (APScheduler-Trigger und Scheduler mit dieser Zeitzone; UTC nur
  intern). Test: Sonntag 11:00 bleibt am 25.10.2026 11:00 MEZ und im Frühjahr 11:00 MESZ.
- **„veraltet“ trotz erfolgreichem Lauf:** Der Lauf vom 24.10. 11:09 war technisch und fachlich erfolgreich (fünf Datensätze geschrieben,
  Exitcode 0). Die Produktion zeigt den ETF-Datenstand deshalb als „älter“, nicht „veraltet“: Die iShares-Daten sind aktuell, die
  monatliche Invesco-Liste war beim Lauf älter als die Grenze von 45 Tagen, weil der Anbieter keine neuere geliefert hat; der Task hat sie
  richtig übernommen. „Veraltet“ zeigte die Test-Instanz auf Port 8790: Sie lief aus dem Repository-Checkout,
  der die privaten Dateien (`*_local.json`) neben dem Code sucht, während die Datenbank aus dem Runtime-Root kam; dort fehlten die ETF-Dateien,
  der Datenstand galt als fehlend. Die Web-Oberfläche meldet diese Mischung jetzt im Kopf als Hinweis, statt sie still anzuzeigen.
- Technischer Erfolg (Exitcode des Laufs) und fachliche Aktualität (Datenstand der Dateien) bleiben getrennt angezeigt: Spalte Status gegen
  Spalte Datenstand und die ETF-Tabelle mit Stand je Datensatz.

### M3d – Windows-Tasks per CLI verwalten (Vorgabe vom 24.10.2026, vor M6)

**Ergebnis**
- `viper-ai tasks list|deploy|edit|delete` ruft das bestehende `scheduler/Manage-TradingTasks.ps1` auf; das Skript ist auf die vier Aktionen
  `List`, `Deploy`, `Edit`, `Delete` vereinfacht (vorher `Status`, `Register`, `UpdateAction`, `FixTriggerTime`, `Enable`, `Disable`,
  `Remove`). Keine zweite Scheduler-Architektur, keine Datenbank: die Aufgabenplanung selbst ist der Speicher.
- `deploy` legt die sieben Tasks an und aktualisiert vorhandene (Aktion `C:\tools\Viper-AI\.venv\Scripts\pythonw.exe` mit Starter,
  Einstellungen, Startzeiten als Ortszeit, DST-fest); idempotent; ein mit `edit` geänderter Zeitplan und der Aktiviert-Zustand bleiben.
  `edit` ändert Zeitplan und aktiviert/deaktiviert, `delete` löscht einen oder alle VIPER-Tasks und weist fremde Namen ab, `list` zeigt
  Name, aktiviert, Zeitplan, nächsten Lauf und Aktion. Das Skript liegt jetzt auch im Paket (`viper_ai/scheduler`).
- Gefundener Fehler: Windows PowerShell 5.1 liefert die Startzeit eines neuen Triggers in UTC (`...Z`); sie wird jetzt in die Ortszeit
  umgerechnet statt den Versatz nur abzuschneiden.
- Test: echte Test-Tasks unter eigenem Präfix (`ViperTest-*`) anlegen, zweites `deploy` unverändert, `edit` (Zeit, deaktivieren),
  `list`, fremden Namen abweisen, alle löschen; Probelauf gegen die Produktions-Tasks: alle sieben „unveraendert“. Nicht produktiv ausgerollt.
- **Verhältnis zu M3a (überholt, M3a ist verworfen; `viper-ai tasks` ist der einzige Weg):** Beide Wege verwalten dieselben sieben Aufgaben. Solange die Produktion die Windows-Aufgabenplanung nutzt, ist
  `viper-ai tasks` der Weg dafür; die integrierte Aufgabenverwaltung (M3a) ersetzt ihn erst nach der Übernahme je Job. Welcher Weg
  dauerhaft gilt, ist deine Entscheidung; beide gleichzeitig aktiv zu lassen würde jeden Job doppelt ausführen.

### M6 – Web-Cutover und Qt-Ablösung (P1; nach M3, vorzugsweise nach M5)

**Aufgaben**
- Webfrontend als Standardstart für die lokale Installation unter `C:\tools\Viper-AI` setzen.
- Datenpfade, Start/Stop, Logs und vorhandene Scheduler-Prozesse prüfen.
- Qt erst nach bestätigter Funktionsparität aus dem normalen Installations- und Startpfad entfernen.
- README, ARCHITECTURE.md und ROADMAP.md im selben Änderungsschritt aktualisieren.

**Abnahme:** VIPER ist lokal vollständig im Browser bedienbar; kein Qt für den Regelbetrieb erforderlich. Der Qt-Cutover darf schon nach M3 erfolgen, wenn M4/M5 unabhängig weiterentwickelt werden.

**Ergebnis**
- Standardstart: `viper-ai-gui` (ohne Konsole) startet das Web-Frontend und öffnet den Browser; läuft VIPER schon, wird nur der Browser
  geöffnet. Die Desktop-Verknüpfung (Ziel `viper-ai-gui.exe`) behält ihr Ziel. Ohne Konsole gehen die Ausgaben nach `web.log`.
- Qt entfernt: `gui/app.py`, `gui/views/`, die Qt-Dienste (`mcp_client`, `chat_service`, `background`), `requirements-gui.txt` und die
  Qt-Tests. Das Paket `gui` behält nur die Qt-freien View-Modelle und Dienste, die das Web nutzt. Extra `gui` ist ein Alias von `web`, damit
  der bisherige Installationsbefehl weiter funktioniert. Der klassische Deploy liefert keine GUI mehr aus.
- Datenpfade, Start/Stopp und Logs geprüft: Datenbank und private Dateien über den Runtime-Root, Einstellungen in derselben Datei wie
  bisher, Scheduler damals im Web-Prozess (seit der Korrektur von M3a: Windows-Aufgabenplanung), Doppelstart verhindert.
- Prüfung: vollständige Suiten vor dem Entfernen der dauerhaften Testsuite grün; Installation aus dem Wheel in frischer venv geprüft (Standardstart liefert das Web auf der Root-Datenbank, Doppelstart nutzt die laufende Instanz, Upgrade lässt den Runtime-Root unverändert).
- **Produktion umgestellt am 24.10.2026** nach [docs/web.de.md, Abschnitt 6](docs/web.de.md#6-start-einstellungen-und-produktion):
  Version installiert, zwei additive Migrationen, Autostart, alle sieben Jobs nach einem erfolgreichen Lauf übernommen (Windows-Tasks
  deaktiviert, nicht gelöscht). Vorher gesichert: Datenbank und `sector_map_local.json` im Ordner `backup` des Roots.

## Empfohlene Arbeitsfolge

1. **Block 1 – Webfundament:** M0 → M1 → M2 → M3.
2. **Block 2 – Intelligence:** M4 (Konzeption ab M1) → M5, danach M3a (integrierte Aufgabenverwaltung) M3b (vollständiger Depotwert-Graph) und M3c (ETF-Datentask).
3. **Block 3 – Cutover:** M6, sobald die tatsächlich benötigte Funktionsparität erreicht ist.

## Minimaler Prüfungsumfang

- Konsistenz zentraler Depot-/Performancewerte zwischen Engine und API.
- Ein vollständiger realer Dashboard- und Qt-Ersatz-Workflow.
- Ein vollständiger Reasoning-Analysefall mit Evidenz und gespeicherter Historie.
- Datenbestand und Startverhalten beim Cutover.

Keine pauschale Testquotenvorgabe, keine neue umfangreiche Testinfrastruktur.

## Nicht Bestandteil dieser Roadmap

- Vollständiger Parqet-Nachbau.
- Migration der VIPER-Fachlogik nach REAPER.
- Gemeinsame universelle Agenten-Orchestrierung für REAPER und VIPER.
- Microservices, Kubernetes, Message-Broker oder neue Datenbank ohne nachgewiesenen Bedarf.
- Automatische Handelsausführung allein durch LLM-Entscheidungen.

## Pflege

Bei Abschluss jeder Etappe: Abnahmeergebnis, tatsächlich geänderte Module, offene Punkte und README/Architektur dokumentieren. Status erst nach Prüfung auf „erledigt“ setzen.

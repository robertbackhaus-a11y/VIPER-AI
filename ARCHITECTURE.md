# VIPER-AI – Architektur

**Stand:** 2026-10-24
**Basis:** VIPER-AI v0.6.0

Dieses Dokument beschreibt die Gesamtarchitektur, den tatsächlichen Ist-Stand und das Ziel der Roadmap ([ROADMAP.md](ROADMAP.md)).
Abschnitt 2 beschreibt nur, was im Code existiert; Abschnitte 3 bis 6 beschreiben das Ziel und sagen jeweils, was davon umgesetzt ist.
Die Detailreferenz der Engine (Module, Schwellenwerte, MCP-Tools, Schema, Produktionspfade) bleibt
[docs/trading-agent-architecture.de.md](docs/trading-agent-architecture.de.md); dieses Dokument wiederholt sie nicht.

## 1. Architekturentscheidung

VIPER-AI bleibt eine eigenständige lokale Single-User-Trading-Plattform. Die frühere Qt-Oberfläche ist durch ein lokales Webfrontend ersetzt (M6). Die Python-Trading-Engine bleibt fachlicher Kern. Zusätzlich erhält VIPER eine eigene, auf Marktanalyse und Investmententscheidungen ausgerichtete Reasoning-Orchestrierung.

REAPER-AI ist weder Laufzeitvoraussetzung noch Orchestrierungs-Framework für VIPER. Einzelne technisch neutrale Konzepte oder Implementierungen (z. B. Modellanbindung, MCP-Adapter, Protokollierung) können nach Prüfung übernommen werden.

## 2. Ist-Stand v0.6.0

Abschnitt 2 beschreibt den Ausgangsstand v0.6.0 (Grundlage von M0); den heutigen Stand nennen die Umsetzungsstände in Abschnitt 4.

### 2.1 Module und Datenflüsse

```text
Windows Task Scheduler (7 Tasks, pythonw + Run-TradingTask.py)
  │  Backfill-* / Discover / Collect / Refresh-TradingEtfData  (Yahoo, EZB, SEC, RSS, iShares/Invesco)
  ▼
SQLite <root>\data\trading.db  +  private *_local.json  +  Reports (candidate-discovery, market-intelligence)
  ▲ lesend (mode=ro)                                  ▲ schreibend nur über explizite Aktionen
  │                                                   │
Trading-Engine tools/trading/  (analysis_engine, decision_engine, trading_orchestrator, portfolio_action_planner,
  │                             sector_overview, effective_exposure, transaction_import, watchlist_admin, ...)
  ▼
Fassade mcp-tools/trading_sqlite.py: Klasse Tools (27 Methoden; Policy tool_policy.py: 17 READ, 10 ACTION)
  ├── MCP-Server server.py (stdio; Profile read / gui / admin)  →  MCP-Clients (LLM-Frontends)
  └── Qt-GUI gui/ (PySide6)  über einen eigenen MCP-Kindprozess (Profil gui)
        ├── View-Modelle gui/viewmodels/  (reine Formatierung, Qt-frei)
        ├── Tasks: gui/services/task_service.py  (PowerShell → Task Scheduler, Qt-frei)
        └── Chat:  gui/services/ollama_client.py + tool_loop.py  (lokales Ollama, nur READ-Tools, Qt-frei)
```

`<root>` ist `VIPER_TRADING_ROOT` (Produktion `C:\tools\Viper-AI`). Paket `viper_ai` (pip): `tools/trading` → `viper_ai.app`,
`mcp-tools` → `viper_ai.mcp`, `gui` → `viper_ai.gui`.

### 2.2 Kopplung von Oberfläche und Fachlogik

- Die Qt-GUI enthält keine Fachlogik und keinen Datenbankzugriff. Jeder Wert kommt aus einem Tool der Fassade; die View-Modelle formatieren nur.
- Die einzige Kopplung an Qt liegt in `gui/views/`, `gui/app.py`, `gui/services/mcp_client.py` (QThread), `chat_service.py` und `background.py`.
- Qt-frei und wiederverwendbar: `gui/viewmodels/*`, `gui/services/settings.py`, `task_service.py`, `task_model.py`, `ollama_client.py`,
  `tool_loop.py`, `tool_policy.py`.
- Die Fassade `Tools` lädt die Engine-Module selbst (`_load_runtime_trading_module`) und kann im eigenen Prozess aufgerufen werden;
  der MCP-Server ist nur ein Adapter darüber.

### 2.3 Lücken für das Web

Keine der bestehenden Schnittstellen liefert Transaktionen, Kursverläufe oder einen Depotverlauf; die Qt-GUI zeigt sie nicht. Diese
Funktionen gibt es im Code nicht und sie werden deshalb neu, aber als rein lesendes Engine-Modul gebaut (nicht in der API):
`tools/trading/portfolio_history.py` (seit M1).

### 2.4 Berechnungskonventionen

| Größe | Regel im Code |
|---|---|
| Depotwert (EUR) | `analysis_engine.build_portfolio_context`: je offener Position Stück × letzter `market_snapshot`-Preis × EZB-Kurs; fehlt eine Bewertung, wird kein Gesamtwert gebildet (kein Teilnenner) |
| FX | `fx_resolver.resolve_fx_rate`: nur direkte EUR-Paare der EZB, Kurs am oder vor dem Stichtag, höchstens 5 Tage alt (`fx_max_age_days`); `GBp` = 0,01 GBP |
| Marktdaten | höchstens 5 Tage alt (`market_data_max_age_days`); Tagesänderung = `close / previous_close - 1` der beiden letzten Tagesbalken |
| Einstandswert | `transaction_import._rebuild_position`: Durchschnittskostenmodell; BUY/TRANSFERIN erhöhen um Betrag + Gebühren + Steuern, SELL/TRANSFEROUT senken anteilig; Splits über `corporate_action` |
| Realisierter Gewinn | `positions.realized_gain` (persistiert, Herkunft teils Parqet) |
| Historische Zeitreihen | Transaktionen in EUR; ein EUR-Depotwert eines Tages braucht für jede gehaltene Position einen Tageskurs und einen EZB-Kurs dieses Tages (höchstens 5 Tage alt) |

## 3. Zielbild

```text
Browser (lokal)
┌──────────────────────────────────────────────────────────────┐
│ VIPER Web-UI                                                 │
│ Dashboard | Portfolio | Watchlist | Analysen | Intelligence │
│ Prozesse | Einstellungen                                     │
└─────────────────────────────┬────────────────────────────────┘
                              │ REST / bei Bedarf SSE
┌─────────────────────────────▼────────────────────────────────┐
│ Python / FastAPI                                             │
│ API, UI-Datenmodelle, Auftragsannahme und Statusmeldungen    │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
┌──────────────▼─────────────────┐ ┌──────────▼────────────────┐
│ Bestehende VIPER Trading-Engine│ │ VIPER Reasoning           │
│ Kurse, Portfolio, FX, Risiko,  │◄┤ Orchestrierung            │
│ Performance, Strategien        │ │ Hypothesen, Evidenz,       │
│ Bestehender Scheduler          │ │ Abwägung, Wiedervorlage   │
└──────────────┬─────────────────┘ └──────────┬────────────────┘
               │                              │
┌──────────────▼──────────────────────────────▼───────────────┐
│ Bestehende SQLite-Datenhaltung + getrennte Analysezustände │
└──────────────────────────────────────────────────────────────┘
```

Dies ist eine logische Darstellung; es werden keine getrennten Prozesse oder Dienste vorgeschrieben.

## 4. Komponenten und Verantwortlichkeiten

### 4.1 Webfrontend

- **Technik:** schlankes HTML/CSS/JavaScript ohne verpflichtendes großes SPA-Framework.
- **Dashboard:** Depotwert, Gewinn/Verlust, Depot-/Kapitalentwicklung, Allokation, Top-/Flop-Positionen.
- **Portfolio:** Positionen, Einzelwerte, Transaktionen, Zeitreihen, relevante Performancekennzahlen und Datenstand.
- **Watchlist:** beobachtete Titel, Kurs-/Bewertungsdaten und Beobachtungsstatus.
- **Analysen:** bestehende Trading-Engine-Ergebnisse.
- **Intelligence:** Investmentthesen, Annahmen, Evidenz, Empfehlungen und Analysehistorie (umgesetzt in M5).
- **Prozesse:** laufende Aufgaben, Status, Ereignisse und bei Bedarf Diagnosedetails.
- **Einstellungen:** bereits vorhandene Konfiguration und Betriebsfunktionen.

Charts nach praktischem Bedarf: TradingView Lightweight Charts für Finanzzeitreihen, Apache ECharts für Dashboard-Visualisierungen; native Tabellen zunächst bevorzugen, Tabellenbibliothek nur bei echtem Bedarf.

Das Webfrontend berechnet weder Portfolio-Performance noch Investmententscheidungen selbst.

**Umsetzungsstand (M2):** `web/static` mit ES-Modulen ohne Build-Schritt (`js/core.js` für Abruf, Formatierung und Tabellen, eine Datei
je Seitengruppe); Dashboard, Portfolio, Positionsdetail und Transaktionen. Tabellen und Texte des Desktop-Dashboards kommen als JSON der
bestehenden View-Modelle (`web/views.py`), damit Wortlaut und Reason-Code-Texte gleich bleiben.

**Umsetzungsstand (M6):** Das Web ist der Standardstart (`viper-ai-gui`); Qt ist entfernt, das Paket `gui` enthält nur noch die Qt-freien
View-Modelle und Dienste.

**Umsetzungsstand (M3):** alle Seiten der Qt-GUI (Planner, Watchlist mit Aktionen, Sektoren, Tasks, Import, Chat, Einstellungen).
`web/pages.py` bildet die Handler von `gui/app.py` eins zu eins nach (gleiches Tool, gleiche Argumente, gleiche Reihenfolge der
Schreibschritte). Der Chat (`web/chat.py`) nutzt den Tool-Loop der GUI und streamt per SSE (einziger SSE-Bedarf bisher); Task-Läufe
werden per Abfrage alle 3 Sekunden verfolgt.

### 4.2 API-Schicht

FastAPI stellt vorhandene Berechnungen bereit, bündelt UI-Daten und nimmt Benutzeraufträge entgegen. REST dient regulären Abfragen und Aktionen. Server-Sent Events sind für einseitige Status-/Ereignisströme vorgesehen; keine Live-Infrastruktur ohne konkreten Bedarf.

API-Endpunkte werden aus dem Bestand abgeleitet, nicht durch eine umfangreiche vorsorgliche Schnittstellenspezifikation.

**Festgelegt in M0:** Die API ruft die Fassade `trading_sqlite.Tools` im eigenen Prozess auf (kein MCP-Kindprozess) und lässt nur zu,
was die Qt-GUI darf: READ-Tools und `GUI_ACTIONS` aus `tool_policy.py`. Tabellen werden mit den bestehenden View-Modellen aufbereitet.

**Umsetzungsstand (M1):** `web/app.py` und `web/engine.py`, Endpunkte in [docs/web.de.md](docs/web.de.md#3-api). Der Server hört nur auf
127.0.0.1; ein schreibender Aufruf mit fremdem `Origin` wird abgelehnt (einzige Schutzmaßnahme: verhindert, dass eine fremde Webseite im
Browser eine Aktion auslöst). Der Orchestrator wird 60 Sekunden zwischengespeichert. SSE ist noch nicht nötig.

### 4.3 Trading-Engine

Bestehende Funktionen für Marktdaten, Währungen, Depot-/Transaktionsdaten, Bewertung, Performance, technische bzw. fundamentale Analysen und Strategien bleiben dort, wo sie sich heute fachlich bewährt haben. Schnittstellen sollen von Qt entkoppelt werden, ohne Berechnungslogik zu duplizieren.

Der bestehende Scheduler bleibt für deterministische, zeitgesteuerte Aufgaben zuständig (z. B. Datenaktualisierung). Nicht jede Kursaktualisierung erfordert ein LLM.

**Umsetzungsstand (M3a):** Die zeitgesteuerten Aufgaben laufen künftig im VIPER-Prozess statt in der Windows-Aufgabenplanung: APScheduler
(`web/scheduler.py`) plant die Jobs aus `scheduled_job`, jeder Lauf startet wie bisher `Run-TradingTask.py` mit Skript, Argumenten und Log und
wird in `job_run` festgehalten. LangGraph bleibt den KI-Analysen vorbehalten, der Scheduler den zeit- und ereignisgesteuerten Aufgaben.
Die Windows-Tasks bleiben bis zur Übernahme je Job aktiv; die Übernahme deaktiviert sie, gelöscht werden sie nie. Zeitpläne sind Uhrzeiten in
`Europe/Berlin` (mit Sommer-/Winterzeit); UTC dient nur intern. Windows-Trigger werden deshalb ohne festen UTC-Versatz angelegt (M3c).

### 4.4 Reasoning-Orchestrierung – eigenständig für VIPER

**REAPER und VIPER benötigen unterschiedliche Prozessmodelle:** REAPER bearbeitet primär ausführbare Arbeitsaufträge mit überprüfbarem Endzustand. VIPER wertet Unsicherheit aus, entwickelt Hypothesen, prüft Gegenthesen und hält Investmentthesen über längere Zeiträume aktuell.

Vorgeschlagener fachlicher Ablauf:

```text
Daten/Ereignis oder manueller Analyseauftrag
                     │
                     ▼
             Datenlage bewerten
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
   Quantitative Analyse    Qualitative Analyse
   (deterministisch)       (LLM + Belege)
         └───────────┬───────────┘
                     ▼
         Hypothese und Gegenthese
                     ▼
       Evidenz / Datenlücken prüfen
                     ▼
         Risiko / Portfolio-Kontext
                     ▼
     Begründeter Handlungsvorschlag
                     ▼
   Analyse speichern / Wiedervorlage
                     │
       Neue relevante Informationen
                     └────► Neubewertung
```

**Reasoning ist kein globaler Ein/Aus-Schalter.** Das Modell wird für Interpretation, Hypothesenbildung, Gegenargumente und begründete Abwägung eingesetzt. Rechenbare Kennzahlen, Risikoregeln und Datenoperationen bleiben deterministisch. Einzelne Analyseschritte dürfen parallel laufen, wenn es sinnvoll ist; eine permanente Multi-Agent-Schleife ist nicht vorgegeben.

Analysezustände sollten mindestens Auftrag/Instrument, Datenstand, Eingangsbefunde, Annahmen, Evidenzquellen, Gegenargumente, Unsicherheit, Ergebnis, Status und Zeitstempel nachvollziehbar halten. Die konkrete Schemaerweiterung erfolgt erst beim Implementieren des ersten End-to-End-Falls.

**Umsetzungsstand (M4):** `tools/trading/viper_reasoning.py` mit LangGraph als technischer Grundlage (Vorgabe), ohne eigene Workflow-Engine.

```text
observe -> assess_data --(unzureichend)--> finalize
                       |--> quantitative (deterministisch) --.
                       `--> qualitative  (LLM + Belege) -----+--> hypothesis (LLM) -> evidence_check --(Lücken, einmal)--> hypothesis
                                                                                                  `--> risk_context -> decide (LLM) -> review -> finalize
```

- Geprüft und genutzt aus LangGraph 1.2.17: `StateGraph`, Conditional Edges (Datenlage, Rücksprung der Belegprüfung), parallele Zweige mit
  Zusammenführung (quantitativ und qualitativ), Checkpointing mit `SqliteSaver` (`langgraph-checkpoint-sqlite` 3.1.2) und Recovery: ein
  abgebrochener Fall läuft mit `resume` ab dem letzten Checkpoint weiter, fertige Schritte werden nicht wiederholt.
- REAPER-AI nutzt `StateGraph` ebenfalls, speichert Checkpoints aber in einem eigenen Store und bildet einen Coding-Ablauf ab; beides wird
  nicht übernommen. VIPER nutzt den Checkpointer von LangGraph.
- Deterministisch (Engine, nur lesend): Snapshot (`analysis_engine`), Engine-Entscheidung und Planner (`trading_orchestrator`), Bestand und
  Gewicht (`portfolio_history`), Sektor und Guard (`sector_overview`), Meldungen und Ereignisse. Daraus entsteht ein Belegkatalog
  (`metric:*`, `fundamental:*`, `engine:action`, `portfolio:*`, `news:<id>`, `event:<id>`).
- LLM (bestehende Ollama-Anbindung `gui/services/ollama_client.py`, JSON-Antworten): Meldungslage, These und Gegenthese mit Annahmen,
  Unsicherheit und Datenlücken, begründeter Vorschlag. Jede Aussage zitiert Beleg-IDs; unbekannte IDs schickt `evidence_check` einmal zurück.
- `review` prüft deterministisch: zulässige Aktion für den Bestand (gehalten: HOLD/ADD/TRIM/SELL, sonst WATCH/BUY/NO_ACTION), Abweichung
  von der Engine-Aktion, Konfidenz bei eingeschränkter Datenlage höchstens mittel, Gültigkeit höchstens 90 Tage. Ein Vorschlag ist nie eine Order.
- Speicherung: Fachergebnis in zwei additiven Tabellen der Trading-Datenbank (`analysis_case`, `analysis_step`; `analysis_store.py`,
  Migration `Migrate-TradingAnalysisCases.py`), getrennt von Portfolio- und Transaktionsdaten. Die Checkpoints von LangGraph (`SqliteSaver`,
  Tabellen `checkpoints` und `writes`) liegen ebenfalls in der Trading-Datenbank; die Migration legt sie an. Keine zusätzliche Datenbank.
- Neubewertung: Jeder Fall speichert einen Fingerabdruck seiner Eingangsdaten; `staleness` nennt neue Kurse, Meldungen, Ereignisse, eine
  geänderte Position oder die abgelaufene Gültigkeit. Ein Folgefall verweist mit `supersedes_id` auf den vorherigen. Kein Dauer-Loop.

### 4.5 Datenhaltung und externe Anbindungen

- Bestehende SQLite-Trading-Datenbank als fachliche Datenquelle beibehalten.
- Analysezustände logisch von Portfolio-/Transaktionsdaten trennen; additive Schemaänderungen bevorzugen.
- Vorhandene Markt- und FX-Datenquellen wiederverwenden.
- Optional MCP für ergänzende Daten- und Recherchetools, wenn die direkte vorhandene Integration nicht ausreicht.
- Keine zusätzliche Datenbank, Broker- oder Cloud-Abhängigkeit ohne echten Bedarf.

## 5. Daten- und Entscheidungsregeln

1. **Eine fachliche Wahrheit:** Enginewerte werden in der API/UI angezeigt und nicht neu berechnet.
2. **Zeitbezug:** Bewertungen nennen Daten- und Analysezeitpunkt; historische Aussagen nutzen die damals gültige Datenbasis.
3. **Nachvollziehbarkeit:** Empfehlungen referenzieren Eingangsgrößen, Quellen, Annahmen und Gegenargumente.
4. **Trennung von Vorschlag und Aktion:** Ein KI-Handlungsvorschlag ist keine Depotbuchung oder Order. Tatsächliche Transaktionen folgen ausschließlich bestehenden, expliziten Funktionen bzw. Nutzeraktionen.
5. **Neubewertung statt Endlosschleife:** Neue relevante Informationen oder Benutzeraufträge lösen gezielte Analysen aus.
6. **Autarkie:** VIPER startet und arbeitet ohne laufendes REAPER.

## 6. Technologieentscheidung

| Bereich | Ziel |
|---|---|
| Frontend | HTML, CSS, JavaScript |
| Backend/API | Python / FastAPI |
| Portfolio- und Dashboardcharts | Apache ECharts, falls benötigt |
| Finanzzeitreihen | TradingView Lightweight Charts, falls benötigt |
| Live-Status | SSE nach Bedarf |
| Datenhaltung | Bestehendes SQLite |
| Tradingberechnung | Bestehende VIPER-Engine |
| Reasoning | VIPER-eigene Fachorchestrierung mit bestehender lokaler LLM-Anbindung, soweit geeignet |
| Installation | Lokal: `C:\tools\Viper-AI` |

Die konkrete Frontend-Bibliothekswahl wird anhand von Funktion und Wartungsaufwand entschieden; sie darf nicht zum eigenständigen Großprojekt werden.

## 7. Migration von Qt auf Web

1. Ist-Zustand und Kopplungen erfassen.
2. API ohne Änderungen an der fachlichen Berechnung einziehen.
3. Portfolio-Dashboard auf echten Daten aufbauen.
4. Bestehende Workflows im Web vervollständigen.
5. Qt-Funktionsparität gezielt prüfen.
6. Web als Standardstart setzen; Qt anschließend aus dem Regelbetrieb entfernen.

Reasoning-Orchestrierung und Intelligence-Ansichten können parallel entworfen und nach dem Webfundament unabhängig ergänzt werden. Qt darf nicht allein deshalb länger benötigt werden, weil Intelligence-Funktionen noch in Entwicklung sind.

Die Workflow-Liste der Qt-Oberfläche steht in [ROADMAP.md (M0)](ROADMAP.md#m0--bestand-und-architekturgrenzen-p0--erledigt).

## 8. Bewusst ausgeschlossen

- VIPER als REAPER-Modul oder REAPER als VIPER-Laufzeitvoraussetzung.
- Eine gemeinsame generische REAPER/VIPER-Orchestrierung als Selbstzweck.
- Vorauseilendes Multi-Agent-Framework, Microservices, Kubernetes oder Message-Broker.
- Vollständiges Kopieren der Parqet-Funktionslandschaft.
- Neue große Testsuite und abstrakte Schichten ohne konkreten Bedarf.

## 9. Abnahme und Pflege

Maßgeblich sind funktionierende Depotkennzahlen, Qt-Funktionsparität, unveränderte Trading-Daten, ein belegbarer End-to-End-Reasoning-Fall und reproduzierbarer lokaler Start. Dokumentation (`README.md`, `ROADMAP.md`, `ARCHITECTURE.md`) wird mit den jeweiligen Änderungen gepflegt und vor dem Release abgeglichen.

**Detaillierte Umsetzungsfolge:** siehe [`ROADMAP.md`](ROADMAP.md).

# Repository-Inhalt

## Hauptverzeichnisse

| Pfad | Inhalt |
|---|---|
| `src/aisurgeon_decentralised/` | gemeinsam importierbarer Corpus-, Retrieval-, RAG-, Studien-, Statistik-, Export- und Archivkern |
| `scripts/` | dünne CLI-Einstiegspunkte für Aufbau, Import, Retrieval, Studie, Validierung und Archivierung |
| `tests/` | automatisierte Phase-1-, Phase-2-, Policy-, Provenienz- und Archivtests |
| `db/migrations/` | idempotente PostgreSQL-/pgvector-Migrationen |
| `docs/` | Architektur, Datenmodell, Policy, Retrieval, Evaluation, Protokoll und SAP |
| `outputs/knowledge_corpus/` | ausschließlich ausgewählte Schemas, Hashmanifeste und aggregierte QA-Berichte |
| `outputs/retrieval_phase/` | ausgewählte Bridge-, Retrieval-, Development- und Abschlussberichte ohne Vektoren/Volltext |
| `outputs/study_phase2/` | eingefrorene Inputs, Konfigurationen, Compliance, Aggregate und Hashmanifeste; keine unredigierten Resultledger |
| `archive/` | neu erzeugte redigierte Studienresultate, Excel-Dateien, Quellenmanifest und Releaseprüfungen |

## Maschinenlesbare Entscheidungsliste

- `archive/repository_file_decisions.json`
- `archive/repository_file_decisions.csv`
- `archive/repository_allowlist.txt`
- `archive/ARCHIVE_SHA256SUMS`

Die Entscheidungsliste erfasst jede lokale Datei außerhalb von `.git` als
`include` oder `exclude` mit Kategorie, Begründung und Größe. Nur `include`-
Pfade werden per `git add --pathspec-from-file` gestagt. `git add .` ist für
diesen Archivworkflow ausdrücklich nicht vorgesehen.

## Redigierte Studienresultate

Unter `archive/study_phase2/results/` liegen genau 800 eindeutige geplante
Ergebnisse und sämtliche 802 tatsächlichen API-Versuche in redigierter Form.
Entfernt wurden operative Providerkennungen, Rate-Limit-Header, lokale Pfade,
Containerdetails und nicht ausgewählte Evidenzvolltexte. Token-, Kosten-,
Latenz-, Modell-, Status-, Rang-, Quellen- und Validatorfelder bleiben erhalten.

Die Excel-Masterdatei unter `archive/study_phase2/excel/` besitzt die zwölf
prä-spezifizierten Tabellenblätter. Vier zusätzliche armbezogene Dateien
enthalten jeweils genau 200 Ergebnisse.

## Ausgeschlossene Kategorien

Die vollständige Liste steht im Maschinenmanifest. Die wesentlichen Kategorien
sind Original-PDFs, Korpusvolltext, Vektoren, Query-Caches, Datenbankzustand,
Roh-API-Ledger, Logs, Umgebungsdateien, Credentials, virtuelle Umgebungen und
temporäre Office-Dateien.

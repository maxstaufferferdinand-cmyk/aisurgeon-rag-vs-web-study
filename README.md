# AISurgeon Decentralised – lokale Retrieval-Forschungsinfrastruktur

Dieses Repository enthält einen Offline-Forschungsprototyp für die lokal
kontrollierte, provenance-erhaltende gemeinsame Abfrage deutscher klinischer
Leitlinien und Arzneimittelinformationen. Es ist kein klinisch validiertes
Medizinprodukt. Technische Coverage, vollständige Quellenfelder und bestandene
Validatoren sind keine Garantie klinischer Extraktions- oder Antwortgenauigkeit.

Der eingefrorene aktuelle Snapshot ist
`cs-f61b3d4e90089c1b890c23cb`. Seine kanonischen JSONL-Dateien bleiben die
Source of Truth; PostgreSQL/pgvector ist ausschließlich ein regenerierbarer
Index.

## Verifizierter Korpusstand

| Merkmal | Aktueller Stand |
|---|---:|
| öffentliche Quell-PDFs | 12 (3 Leitlinien, 9 Arzneimittelinformationen) |
| Originalseiten | 2.060 |
| validierte Extraktionsbatches | 831/831 |
| nicht aggregierte kanonische Records | 7.306 |
| formale Items | 558 (433 primär, 125 sekundär/ausgeschlossen) |
| Retrieval-Einheiten | 4.469 (1.263 Leitlinie, 3.206 SmPC) |
| Evidenzspans | 12.492 |
| HCC-History-Records mit Policy-Ausschluss | 99; normales Leakage 0 |
| Baseline-Embeddings | 4.469/4.469 |

Die früher berichteten Werte 529 formale Items und 4.585 Retrieval-Einheiten
entsprechen nicht dem aktuellen kanonischen Stand. Die Abweichung ist im
Snapshot und im QA-Bericht festgehalten. `targeted_repair_remaining.csv` ist
header-only; unabhängig davon verbleiben 2.785 Review-Flags.

## Schnellstart – geprüft

Voraussetzungen sind Python 3.13, `uv` und Docker Desktop beziehungsweise eine
kompatible Docker Engine. Der Stack bindet PostgreSQL ausschließlich an
`127.0.0.1:55432`. Ein zufälliges lokales Datenbankpasswort wird beim ersten
Start in der ignorierten Datei `.env.retrieval` erzeugt. Das Skript setzt auf
POSIX-Dateisystemen Modus 0600; auf der aktuellen WSL/NTFS-Mount gelten die
Windows-ACLs, obwohl `stat` dort 0777 darstellen kann.

```bash
uv sync --dev
uv run python scripts/retrieval_stack.py start
uv run python scripts/migrate_retrieval_db.py
uv run python scripts/import_corpus_snapshot.py --verify-idempotent
uv run python scripts/embed_retrieval_units.py --full --resume --batch-size 64
uv run python scripts/validate_retrieval_layer.py
uv run pytest -q
```

Geprüftes Ergebnis: PostgreSQL 18.6, pgvector 0.8.6, 4.469 importierte
Retrieval-Einheiten, 4.469 Embeddings, End-to-End-Validator 37/37 und Testlauf
70/70 plus 12 Subtests.

Status und Stop:

```bash
uv run python scripts/retrieval_stack.py status
uv run python scripts/retrieval_stack.py stop
```

Der vollständige zerstörende Rebuild ist absichtlich bestätigt und betrifft nur
das benannte regenerierbare Volume `aisurgeon_retrieval_pgdata`:

```bash
uv run python scripts/rebuild_retrieval_db.py --yes-really-reset
```

Er stellt alle Embeddings aus validierten lokalen Checkpoints wieder her und
verursacht beim geprüften Resume keine neuen API-Aufrufe.

## Geschlossene RAG-CLI (Phase 1)

Die Phase-1-Pipeline besitzt einen gemeinsam importierbaren Kern unter
`src/aisurgeon_decentralised/rag_core.py`; CLI-Skripte enthalten keine eigene
Retrieval- oder Claim-Geschäftslogik. Es wurde keine Web-App und kein Frontend
gebaut.

Lokaler Dry-Run ohne irgendeinen OpenAI-Aufruf:

```bash
uv run python scripts/run_rag_query.py \
  --question "Was soll vor der Indikationsstellung zur VTE-Prophylaxe evaluiert werden?" \
  --question-id local-dry-001 \
  --retrieval-mode fts \
  --routing guideline_first \
  --dry-run
```

Geschlossene Einzelabfrage mit exakter Vektorsuche, RRF und gerichteter
SmPC→Leitlinien-Bridge:

```bash
uv run python scripts/run_rag_query.py \
  --question "Zu welchem Leitlinienitem führt die Eliquis-Fachinformation über Apixaban?" \
  --question-id synthetic-study-001 \
  --retrieval-mode hybrid_rrf_bridge \
  --routing smpc_first \
  --output-jsonl outputs/retrieval_phase/manual/query_results.jsonl \
  --output-csv outputs/retrieval_phase/manual/query_results.csv
```

Ohne `--question` startet dieselbe Datei einen interaktiven Modus. Operative
Telemetrie speichert nur Question-ID und SHA-256, nie standardmäßig Frage oder
Antwort. `--include-question-in-study-export` ist ein explizites Opt-in für
freigegebene synthetische Studiendaten.

Der vollständige, checkpoint-/resume-fähige VTE-Development-Lauf lautet:

```bash
uv run python scripts/run_rag_benchmark.py \
  --snapshot-id cs-f61b3d4e90089c1b890c23cb
```

Er vergleicht PostgreSQL-FTS, exakte pgvector-Suche, Hybrid-RRF sowie
Hybrid-RRF+Bridge. Anschließend laufen zuerst drei synthetische Fragen in beiden
Responses-Armen. Nur wenn die konservative maximale Zusatzkostenschätzung
höchstens 2 USD beträgt, werden die übrigen Aufrufe sequenziell ausgeführt.
Validierte API- und Query-Embedding-Checkpoints werden beim Resume nicht neu
berechnet.

Die gerichtete Bridge und ihre QA-Artefakte werden reproduzierbar erzeugt mit:

```bash
uv run python scripts/build_smpc_guideline_bridge.py
uv run python scripts/audit_rationale_relations.py
```

Aktueller geprüfter Bridge-Stand: 140 Matrixzeilen, 139 aktive belegte
Relationen (17 formale und 122 nichtformale Leitlinienziele), 1 zulässiger
`unmatched_no_error`-Fall und 0 Rückwärtsrelationen. Treffer in der
HCC/BCC-Konsultationsfassung tragen weiter deren nichtfinalen Dokumentstatus.

## Niedrigstufiges Retrieval verwenden

Lexikalischer Hybridlauf mit automatischem Routing:

```bash
uv run python scripts/query_retrieval.py \
  --snapshot-id cs-f61b3d4e90089c1b890c23cb \
  --query "12.43" \
  --routing guideline_first \
  --top-k 5
```

Dense Retrieval verwendet einen lokal bereitgestellten 1.536-dimensionalen
Query-Vektor über `--embedding-json`. Die Baseline scannt pgvector exakt; es
existiert kein HNSW- oder IVFFlat-Index. German/simple FTS ist PostgreSQL-
Volltextsuche und wird nicht als BM25 bezeichnet. Ranglisten werden mit
rangbasierter Reciprocal Rank Fusion (`k=60` als technischer Default) verbunden;
inkompatible Rohscores werden nicht addiert.

## Externe API-Grenze und Secrets

Der API-Key wird ausschließlich programmgesteuert aus
`${AISURGEON_SECRET_ENV_FILE}` oder standardmäßig
`${XDG_CONFIG_HOME:-~/.config}/aisurgeon-decentralised/.env` geladen. Diese Datei
wird weder verändert noch protokolliert. Für den aktuellen Snapshot wurde
OpenAI extern für `text-embedding-3-small`, technische Structured-Output-Smokes
und den klar abgegrenzten 20-Fragen-VTE-Development-Vergleich mit öffentlichen,
synthetischen Fragen und ausgewählten öffentlichen Evidenzeinheiten aufgerufen.
Es wurden keine Patientendaten verarbeitet.

Gemini war ausschließlich der frühere Offline-PDF-Extractor für öffentliche
Dokumente. In der Retrievalphase wurde Gemini nicht erneut verwendet.

Resume der Embeddings:

```bash
uv run python scripts/embed_retrieval_units.py --full --resume --batch-size 64
```

## Evidence- und Claim-Vertrag

Das Backend erzeugt eine snapshotgebundene Allowlist. Ein Modell darf nur IDs
aus diesem Paket verwenden; Dokumentname, Version, Dokumentstatus, Seite und
Link rendert ausschließlich das Backend. Öffentliche Labels lauten exakt:

- `supported`
- `partially_supported`
- `no_validated_evidence`

`no_validated_evidence` ist ausschließlich nach vollständig durchlaufenem
Fallback zulässig, wenn kein Kandidat im freigegebenen Snapshot eine
ausreichende Beleggrundlage liefert. Das gilt auch dann, wenn der Retriever zwar
Kandidaten fand, diese aber nicht hinreichend sind. Widersprüchliche oder
technisch nicht ausreichend belegte Claims werden verworfen oder herabgestuft.

## Evaluation

Das reproduzierbare Annotation Package unter
`outputs/retrieval_phase/evaluation/` enthält 50 Development- und 250 versiegelte
Testslots. Alle 300 automatisch formulierten Fragen sind `synthetic_draft`; alle
klinischen Goldfelder sind leer und warten auf unabhängige Human-Annotation und
Adjudikation. 25 % sind No-evidence-/Out-of-scope-Kandidaten.

```bash
uv run python scripts/build_annotation_package.py \
  --snapshot-manifest outputs/knowledge_corpus/manifests/corpus_snapshots/cs-f61b3d4e90089c1b890c23cb.json \
  --output-dir outputs/retrieval_phase/evaluation
```

Zusätzlich liegt unter `outputs/retrieval_phase/vte_development/` eine separat
gekennzeichnete Demonstration mit 20 source-derived synthetischen
Development-Fragen (17 belegte, 3 No-evidence). Sie ist kein finaler
unangetasteter Studientestsatz. Die geprüften Retrievalresultate, alle 40
Responses-Läufe, Zitationsvalidierung, Telemetrie-, Token-, Latenz- und
Kostenübersichten sowie das 2-USD-Kostengate sind dort maschinenlesbar abgelegt.

## Phase 2 – prä-spezifizierte RAG-versus-Web-Studie

Phase 2 vergleicht dieselbe klinische Aufgabe in zwei Quellenräumen: GPT mit
verpflichtender Live-Websuche (`WEB`) und GPT mit lokalem Closed-Corpus-RAG
(`RAG`). Es werden zwei Deploymentkonfigurationen untersucht:
`gpt-5.5-2026-04-23`/Reasoning `medium` sowie `gpt-5.6-sol`/Reasoning `high`.
Wegen der unterschiedlichen Reasoning-Einstellungen wird kein isolierter
Modelleffekt behauptet. Am Zugriffstag 2026-08-29 war für GPT-5.6 Sol kein
datierter Snapshot in der offiziellen Modelldokumentation ausgewiesen.

Die 100 bestehenden Frage-/Goldentwürfe wurden vom Study Owner in ihrer
aktuellen Form als `study_owner_pre_freeze_approval` freigegeben. Die leeren
Reviewerfelder bleiben absichtlich leer; eine unabhängige klinische
Fragevalidierung wird nicht behauptet. Vollständig erzeugt sind 100 neue
synthetische, quellengestützte Frage-/Goldentwürfe (80 lokal
abgedeckt, 20 lokal nicht ausreichend abgedeckt), 800 randomisierte
Studienplatzhalter, Review-, Rating- und Citation-Audit-Dateien sowie alle
vorläufigen JSONL-, CSV- und Excel-Exporte. Diese Entwürfe sind noch kein
unabhängiger klinischer Goldstandard.

Der Development-Kostenpilot wurde mit fünf alten Phase-1-Fragen, zwei Modellen
und beiden Armen abgeschlossen: 20/20 Responses, 20 HTTP-Versuche, keine
Retries, 30 Web-Search-Aufrufe und geschätzte Pilotkosten von 2,1674894 USD.
Die konservative, für bis zu drei Versuche je Zelle retry-inklusive kumulative
Projektion einschließlich Vorbereitung, Pilot und 800 Hauptzellen beträgt
394,08431008 USD und liegt unter dem aktiven kumulativen 500-USD-Limit. Die
frühere 400-USD-Grenze ist versioniert archiviert und als abgelöst markiert;
bereits entstandene Kosten wurden nicht zurückgesetzt.
`max_output_tokens=6000` blieb nach einem beobachteten Maximum
von 2.786 bestehen. Der Web-Pilot startete mit fünf maximal angeforderten
Toolaufrufen; weil eine Antwort sechs tatsächliche Search-/Open-/Find-Aktionen
auswies, wurde der Hauptstudienwert prospektiv auf sechs eingefroren. Der
anschließende Hauptlauf schloss 800/800 eindeutige Studienzellen ab. Erfasste
802 HTTP-Versuche enthalten zwei transparent protokollierte transiente Retries
und keine terminal fehlgeschlagene Zelle; die kumulativen externen Phase-2-
Kosten einschließlich Vorbereitung und Pilot betrugen geschätzt
80,93783728 USD.

Kostenfreie Vorbereitung, Nachvalidierung und Pre-Freeze-Export:

```bash
PYTHONPATH=src uv run python scripts/prepare_rag_vs_web_study.py --audit-retrieval
PYTHONPATH=src uv run python scripts/revalidate_rag_vs_web_results.py --dataset pilot
PYTHONPATH=src uv run python scripts/finalize_rag_vs_web_prefreeze.py
PYTHONPATH=src uv run python scripts/validate_rag_vs_web_study.py
```

Die unveränderten Datensätze werden ohne Ausfüllen der leeren Reviewfelder mit
dem dokumentierten Owner-Approval-Gate eingefroren:

```bash
PYTHONPATH=src uv run python scripts/verify_openai_study_models.py
PYTHONPATH=src uv run python scripts/freeze_study_questions.py \
  --approval-basis study_owner_pre_freeze_approval
PYTHONPATH=src uv run python scripts/run_rag_vs_web_study.py main
```

Die Modellprüfung liest ausschließlich die drei prä-spezifizierten offiziellen
OpenAI-Seiten und muss beim Human-Freeze höchstens 24 Stunden alt sein. Ein
geänderter oder mehrdeutiger Snapshot stoppt den Workflow vor API-Kosten.

Der Haupt-Runner ist sequenziell, checkpoint-/resume-fähig und prüft vor jeder
neuen Zelle Kostenlimit und Restprojektion. `main` kann nach einer Unterbrechung
mit demselben Befehl fortgesetzt werden; terminal gespeicherte Run-IDs werden
nicht erneut kostenpflichtig ausgeführt. Nach 800 terminalen Zellen werden
technische Statistik, Master-Excel und vier Modell-/Systemdateien automatisch
aus den kanonischen JSONL-Dateien abgeleitet.

Nach zwei ausgefüllten verblindeten klinischen Ratings, Citation Audit und
Adjudikation erfolgt der idempotente Import mit:

```bash
PYTHONPATH=src uv run python scripts/import_rag_vs_web_ratings.py
```

Das Studienprotokoll und der Analyseplan liegen unter
`docs/STUDY_PROTOCOL_RAG_VS_WEB.md` und
`docs/STATISTICAL_ANALYSIS_PLAN_RAG_VS_WEB.md`. Die prä-spezifizierte
80/20-Mischung ist eine Benchmarkgewichtung und keine Schätzung realer
klinischer Coverage-Prävalenz. Technische Provenienzvalidierung ist keine
klinische Richtigkeitsvalidierung.

## Dokumentation

- [Architektur und ER-Modell](docs/ARCHITECTURE.md)
- [Datenwörterbuch](docs/DATA_DICTIONARY.md)
- [Corpus Snapshot](docs/CORPUS_SNAPSHOT.md)
- [Policy und Eligibility](docs/POLICY_AND_ELIGIBILITY.md)
- [Retrievaldesign](docs/RETRIEVAL.md)
- [Geschlossene RAG-CLI Phase 1](docs/CLI_RAG_PHASE1.md)
- [Reproduzierbarkeit und Betrieb](docs/REPRODUCIBILITY.md)
- [Evaluationsprotokoll](docs/EVALUATION_PROTOCOL.md)
- [Phase-2-Studienprotokoll](docs/STUDY_PROTOCOL_RAG_VS_WEB.md)
- [Phase-2-Analyseplan](docs/STATISTICAL_ANALYSIS_PLAN_RAG_VS_WEB.md)
- [Bekannte Limitationen](docs/LIMITATIONS.md)
- [Methods-Draft](docs/METHODS_DRAFT.md)

Die aktuelle Generalisierbarkeit ist auf drei Leitlinien und neun
Arzneimittelinformations-PDFs begrenzt.

## Privates Reproduzierbarkeitsarchiv

Dieses Repository wird als kuratierter, privater Studienbestand archiviert.
Original-PDFs, Korpusvolltexte, Datenbankzustand, Embeddingvektoren und
unredigierte operative API-Ledger sind absichtlich nicht Bestandteil von Git.
Ihre Provenienz und lokale Wiederherstellung sind in
[`PROVENANCE.md`](PROVENANCE.md), [`DATA_AVAILABILITY.md`](DATA_AVAILABILITY.md)
und [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) beschrieben.

Der technische Phase-2-Lauf ist mit 800/800 Studienzellen abgeschlossen. Der
zulässige Status bleibt
`TECHNICAL_STUDY_COMPLETE_CLINICAL_RATING_PENDING`, weil unabhängige
verblindete klinische Ratings, Citation Audit und Adjudikation noch ausstehen.
Die GitHub-tauglichen JSONL-, CSV- und Excel-Ableitungen unter `archive/`
entfernen sensible operative Kennungen und enthalten keine Korpusvolltexte oder
Vektoren.

Archiv neu bauen und kostenfrei prüfen:

```bash
PYTHONPATH=src uv run python scripts/build_github_archive.py
PYTHONPATH=src uv run python scripts/validate_github_archive.py
```

Die vollständige Aufnahme-/Ausschlussentscheidung ist maschinenlesbar in
`archive/repository_file_decisions.json` dokumentiert. Dem privaten Repository
wurde bewusst keine Open-Source-Lizenz hinzugefügt.

# Reproduzierbarkeit des GitHub-Archivs

## Geltungsbereich

Dieses private Repository archiviert Code, Konfiguration, Datenbankschema,
Prompts, eingefrorene Studieninputs, Prüfhases, aggregierte Auswertungen und
redigierte Studienresultate der Phase 1 und Phase 2. Es ist ein technischer
Forschungsprototyp und kein klinisch validiertes Medizinprodukt.

Die Original-PDFs, der extrahierte Korpusvolltext, PostgreSQL-Volumes und die
4.469 Embeddingvektoren werden aus urheberrechtlichen und sicherheitstechnischen
Gründen nicht verteilt. Der lokal vorhandene historische Bestand wurde für die
Archivierung nicht verändert. Redigierte Ableitungen liegen ausschließlich
unter `archive/`.

## Voraussetzungen

- Python 3.13
- `uv`
- Docker Engine mit Compose-Plug-in für den optionalen Datenbank-Rebuild
- PostgreSQL 18.6 und pgvector 0.8.6 über das digest-gepinnte Compose-Image

Installation ohne externe Modellaufrufe:

```bash
uv sync --locked --dev
PYTHONPATH=src uv run python -c "import aisurgeon_decentralised.archive_release"
PYTHONPATH=src uv run python scripts/validate_github_archive.py
```

Keiner dieser Befehle ruft OpenAI oder Gemini auf.

## Quellen lokal wiederherstellen

1. `archive/corpus/source_manifest.json` lesen.
2. Jede Quelle von der dort angegebenen offiziellen Bezugsseite beziehen und
   die jeweiligen Nutzungsbedingungen prüfen.
3. Dateien mit dem dokumentierten Originaldateinamen unter `source_pdfs/`
   ablegen.
4. Für jede Datei SHA-256 und Seitenzahl gegen das Manifest prüfen. Eine
   abweichende aktuelle Version darf nicht still als der eingefrorene Snapshot
   behandelt werden.
5. Die vollständigen kanonischen JSONL-Dateien aus dem kontrollierten
   Forschungsarchiv unter `outputs/knowledge_corpus/canonical/` bereitstellen
   oder die dokumentierte Extraktionspipeline lokal reproduzieren. Eine neue
   Extraktion erzeugt einen neuen Snapshot und ist keine byte-identische
   Rekonstruktion von `cs-f61b3d4e90089c1b890c23cb`.

Die HCC/BCC-Quelle ist eine Konsultationsfassung. Ihr Inhalt wird in diesem
Repository nicht weiterverbreitet; der dokumentierte `source_status` bleibt
`consultation_draft`.

## Datenbank und Retrieval

Nach lokaler Bereitstellung der ausgeschlossenen Snapshotdaten:

```bash
uv run python scripts/retrieval_stack.py start
uv run python scripts/migrate_retrieval_db.py
uv run python scripts/import_corpus_snapshot.py --verify-idempotent
uv run python scripts/validate_retrieval_layer.py
```

Die Datenbank ist ein regenerierbarer Index. Die Baseline verwendet exakte
pgvector-Suche, PostgreSQL-FTS und pg_trgm; sie verwendet weder HNSW noch
IVFFlat als primären Index. Die 4.469 historischen Embeddings sind nicht im
Repository. Sie können aus einem kontrollierten lokalen Checkpoint importiert
oder, nur nach bewusster Kostenfreigabe, neu erzeugt werden.

## Archivexport reproduzieren

Wenn die historischen lokalen Artefakte vorhanden sind, werden Redaktionen und
Excel-Dateien deterministisch neu erzeugt mit:

```bash
PYTHONPATH=src uv run python scripts/build_github_archive.py
PYTHONPATH=src uv run python scripts/validate_github_archive.py
```

Die Transformation entfernt insbesondere Provider-Request-/Response-IDs,
Rate-Limit-Header, lokale Benutzerpfade, Containerdetails, Volltext-Evidence
und Vektoren. Die semantischen Antworten, Messwerte, Kosten, Ränge,
Evidence-IDs und Quellenmetadaten bleiben für die technische Auswertung
erhalten. `archive/repository_file_decisions.json` dokumentiert pro lokaler
Datei die Aufnahme- oder Ausschlussentscheidung.

## Tests

Vollständige lokale Prüfung mit vorhandenem Korpus und laufender Datenbank:

```bash
uv run pytest -q
PYTHONPATH=src uv run python scripts/validate_knowledge_corpus.py
PYTHONPATH=src uv run python scripts/validate_retrieval_layer.py
PYTHONPATH=src uv run python scripts/validate_cli_rag_phase1.py
PYTHONPATH=src uv run python scripts/validate_rag_vs_web_study.py
uv run ruff check src scripts tests
PYTHONPATH=src uv run python scripts/validate_github_archive.py
```

In einem reinen GitHub-Checkout ohne die ausgeschlossenen Quellen sind
Installation, alle Python-Imports, die Archivvalidierung und datenunabhängige
Tests ausführbar. Korpus-, Datenbank- und Live-Retrievaltests benötigen die
lokal wiederhergestellten, nicht verteilten Daten. Studien-API-Aufrufe dürfen
nicht zur bloßen Archivprüfung erneut ausgeführt werden.

## Secrets

Secrets liegen ausschließlich außerhalb des Repositorys unter dem Pfad aus
`AISURGEON_SECRET_ENV_FILE` oder standardmäßig unter
`${XDG_CONFIG_HOME:-~/.config}/aisurgeon-decentralised/.env`. Die
`.env.example` enthält nur Variablennamen und leere beziehungsweise öffentliche
Platzhalter.

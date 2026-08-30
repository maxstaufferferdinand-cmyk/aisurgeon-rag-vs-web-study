# Reproduzierbarkeit und lokaler Betrieb

## Versionspins

- Image:
  `pgvector/pgvector:0.8.6-pg18-trixie@sha256:78bf48b801e792f99e3ac62b5036fd3876e9be48afda16c1e331af1c75ceb2ff`
- Runtime: PostgreSQL `18.6 (Debian 18.6-1.pgdg13+2)`;
  `server_version_num=180006`
- pgvector: `0.8.6`
- beobachteter Runtime-Image-ID/Digest:
  `sha256:78bf48b801e792f99e3ac62b5036fd3876e9be48afda16c1e331af1c75ceb2ff`
- Linux/amd64-Manifest:
  `sha256:ff8da7b0714e5efa413d77f43e24d93064dd66469d418d12608c1bbc91fcf045`

Offizielle Referenzen:

- PostgreSQL 18.6 Release Notes:
  <https://www.postgresql.org/docs/release/18.6/>
- pgvector-Dokumentation/Releases: <https://github.com/pgvector/pgvector>

Der Port ist ausschließlich `127.0.0.1:55432`. Das persistente Volume heißt
`aisurgeon_retrieval_pgdata`. Healthcheck: `pg_isready` plus `SELECT 1`.

## Secrets

`scripts/retrieval_stack.py start` erzeugt `.env.retrieval` bei Bedarf mit
zufälligem Datenbankpasswort und versucht Modus 0600. Die Datei ist
git-ignoriert. Auf der aktuellen WSL/NTFS-Mount werden POSIX-Modusbits nicht
erzwungen (`stat` kann 0777 anzeigen); dort schützen die Host-ACLs und die reine
Loopback-Bindung. `.env.example` enthält nur Platzhalter.

Der OpenAI-Key wird programmgesteuert aus `${AISURGEON_SECRET_ENV_FILE}` oder
standardmäßig `${XDG_CONFIG_HOME:-~/.config}/aisurgeon-decentralised/.env` geladen. Die Datei wird
nicht verändert. Schlüsselwerte erscheinen weder in Checkpoints noch in
Reports, Telemetrie oder Tests.

## Start, Migration und Import

```bash
uv sync --dev
uv run python scripts/retrieval_stack.py start
uv run python scripts/migrate_retrieval_db.py
uv run python scripts/import_corpus_snapshot.py --verify-idempotent
```

Migrationen werden mit SHA-256 registriert und durch Advisory Lock serialisiert.
Eine nachträgliche Änderung einer bereits angewandten Migration bricht ab.
Importe laufen transaktional mit separatem Advisory Lock; Konflikte werden nicht
still überschrieben.

## Embeddings

Erster, bereits ausgeführter Smoke und Vollbaseline:

```bash
uv run python scripts/embed_retrieval_units.py --smoke
uv run python scripts/embed_retrieval_units.py --full --resume --batch-size 64
```

API-Batches sind strikt sequenziell. Jeder Batch wird sofort dimensional,
numerisch, normativ und per Roundtrip validiert und atomar als gzip-JSON
checkpointed. HTTP 400/401/403 werden nicht automatisch wiederholt; 408, 429 und
5xx erhalten höchstens fünf exponentielle Versuche. Die OpenAI-SDK-eigenen
Retries sind deaktiviert.

## Vollständiger Rebuild

```bash
uv run python scripts/rebuild_retrieval_db.py --yes-really-reset
```

Der geprüfte Rebuild:

1. entfernte ausschließlich `aisurgeon_retrieval_pgdata`;
2. startete den gepinnten Container gesund;
3. wandte Migrationen `0001`–`0009` an;
4. bestätigte den zweiten Migrationslauf mit 0 Änderungen;
5. importierte den Snapshot zweimal mit identischen Counts;
6. stellte 4.469 Embeddings aus Checkpoints bei 0 API-Aufrufen wieder her;
7. stellte den validierten Structured-Output-Vertrag ohne API-Aufruf wieder her;
8. bestand 37/37 End-to-End-Checks;
9. bestätigte alle Quell-PDF-Hashes unverändert.

Report:
`outputs/retrieval_phase/cs-f61b3d4e90089c1b890c23cb/qa/database_rebuild.json`.

## Tests und Validatoren

```bash
uv run python scripts/validate_knowledge_corpus.py
uv run python scripts/validate_retrieval_layer.py
uv run pytest -q
```

Aktueller Stand: Legacy-Abschlussvalidator 50/50, Legacy-Regression 18/18,
Retrieval-Validator 37/37, Gesamtsuite 70 Tests plus 12 Subtests.

## Stop und kontrollierter Reset

```bash
uv run python scripts/retrieval_stack.py stop
uv run python scripts/retrieval_stack.py reset --yes-really-reset
```

Der Reset entfernt keine PDFs, Canonical-Dateien, Snapshots oder Embedding-
Checkpoints. Es wurde kein Git-Commit und kein Git-Push durchgeführt.

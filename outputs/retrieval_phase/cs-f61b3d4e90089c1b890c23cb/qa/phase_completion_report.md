# QA-Abschlussbericht – Retrievalphase

**Ergebnis:** PASS  
**Snapshot:** `cs-f61b3d4e90089c1b890c23cb`  
**Status:** Offline-Forschungsprototyp, nicht klinisch validiert.

## Implementiert

Unveränderlicher Snapshot, versioniertes Provenienzschema, digest-gepinnter
PostgreSQL/pgvector-Stack, neun idempotente Migrationen, transaktionaler Import,
zentrale Eligibility-View, Exakt-/German-/simple-/Trigramm-/exakte Vektorsuche,
RRF, Quellenrouting, typisierte Relationsexpansion, Evidence-Allowlist,
Claim-Validatoren, Backend-Citations, datensparsame Telemetrie und Human-
Annotation-Package.

## Zahlen

- 12 PDFs / 2.060 Seiten / 7.306 kanonische Records
- 558 formale Items (433 primär, 125 sekundär)
- 4.469 Retrieval-Einheiten / 12.492 Evidenzspans / 195 Relationen
- 4.469 `text-embedding-3-small`-Vektoren mit 1.536 Dimensionen
- 1.217.859 Baseline-Input-Tokens; geschätzt 0,02435718 USD
- Gesamte dokumentierte externe Schätzkosten: 0.02446845 USD

## Validierung

- DB-Rebuild: PASS; Migration und Import idempotent
- Retrieval-End-to-End: 37/37
- Policy-Leakage: 0/99 HCC-History-Canaries
- Legacy-Abschlussvalidator: 50/50
- Legacy-Regression: 18/18
- Gesamtsuite: 70 bestanden, 0 Fehler, 12 Subtests
- Structured Output: PASS; eine Allowlist-ID, Backend-Locator, kein Textlogging

## Nicht blockierende Limitationen

- 2,785 review-severity QA flags remain.
- Current generalisability is limited to three guidelines and nine medicinal-product PDFs.
- HCC/BCC is a consultation draft, not a demonstrated final version.
- 39 product and 30 active-substance references remain deliberately unresolved.
- Table header paths are not explicitly encoded and remain null/QA-flagged.
- The WSL/NTFS workspace does not enforce POSIX mode 0600 for .env.retrieval; the file is git-ignored and governed by host ACLs.
- Retrieval defaults and synthetic drafts are not clinically validated.

## Resume

```bash
uv run python scripts/retrieval_stack.py start && uv run python scripts/migrate_retrieval_db.py && uv run python scripts/import_corpus_snapshot.py --verify-idempotent && uv run python scripts/embed_retrieval_units.py --full --resume --batch-size 64 && uv run python scripts/validate_retrieval_layer.py
```

Keine Quell-PDF wurde verändert. Gemini wurde in dieser Retrievalphase nicht
erneut verwendet. Es wurden keine Patientendaten verarbeitet. Kein Git-Commit
und kein Git-Push wurden durchgeführt.

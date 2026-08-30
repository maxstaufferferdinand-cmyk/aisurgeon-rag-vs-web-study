# Abschlussbericht – reproduzierbare geschlossene RAG-CLI, Phase 1

## Ergebnis und Scope

Phase 1 ist als gemeinsam importierbare Python-/CLI-Pipeline abgeschlossen.
Es wurde keine Web-App und kein Frontend gebaut. Verwendet wurde ausschließlich
der versiegelte Corpus Snapshot `cs-f61b3d4e90089c1b890c23cb` mit 4.469
Retrieval-Einheiten und den bestehenden 4.469 Embeddings. Quell-PDFs und
kanonische JSONL-Dateien sind SHA-256-identisch zum Snapshot geblieben. Gemini
wurde in dieser Phase nicht aufgerufen; es wurden keine Patientendaten
verarbeitet.

Der Prototyp ist nicht klinisch validiert. Technische Zitationsgültigkeit,
Coverage und Development-Metriken sind keine Garantie klinischer Richtigkeit.

## Implementierte Dateien

Gemeinsamer Kern:

- `src/aisurgeon_decentralised/query_normalization.py`
- `src/aisurgeon_decentralised/query_embedding_cache.py`
- `src/aisurgeon_decentralised/smpc_guideline_bridge.py`
- `src/aisurgeon_decentralised/rag_core.py`
- `src/aisurgeon_decentralised/rag_responses.py`
- `src/aisurgeon_decentralised/rag_telemetry.py`
- `src/aisurgeon_decentralised/rag_exports.py`
- `src/aisurgeon_decentralised/vte_development.py`
- erweitert: `src/aisurgeon_decentralised/hybrid_retrieval.py`

CLI und QA:

- `scripts/run_rag_query.py`
- `scripts/run_rag_benchmark.py`
- `scripts/build_smpc_guideline_bridge.py`
- `scripts/audit_rationale_relations.py`
- `scripts/validate_cli_rag_phase1.py`
- Tests: `tests/test_rag_core.py`, `tests/test_rag_responses.py`,
  `tests/test_rag_telemetry.py`, `tests/test_query_embedding_cache.py`,
  `tests/test_smpc_guideline_bridge.py`, `tests/test_cli_rag_phase1.py`

Dokumentation:

- `README.md`, `AGENTS.md`
- `docs/CLI_RAG_PHASE1.md`

## Bestand, Datenbank und Rationale-Audit

| Prüfung | Ergebnis |
|---|---:|
| Quell-PDFs | 12, SHA-256 unverändert |
| Seiten | 2.060 |
| nicht aggregierte kanonische Records | 7.306 |
| formale Items | 558 (433 primär, 125 sekundär/ausgeschlossen) |
| Retrieval-Einheiten | 4.469 |
| eligible Retrieval-Einheiten | 4.469 |
| Datenbank-Embeddings | 4.469 |
| historische HCC-Records | 99 policy-exkludiert |
| PostgreSQL | 18.6 |
| pgvector | 0.8.6 |

Die zwei gemeldeten VTE-Rationale-Beziehungen waren bereits eindeutig in
`explicit_linked_rationale_record_ids` belegt und als validierte gerichtete
Relationen im Index vorhanden:

- `rec-7a2c99c3a6dffc908b5eb111` →
  `rec-5ea56ee4466171d5d4fe21dd` (Item 15.4, PDF-S. 136→137);
- `rec-95dfba9075cff2c1f6940c9c` →
  `rec-f89ace17e294f2bf3cac9942` (gedrucktes Duplikat 15.4,
  `source_item_number=null`, PDF-S. 139→139).

Es war keine Reparatur und keine kanonische Mutation erforderlich.

## Gerichtete SmPC→Leitlinien-Bridge

Die Matrix umfasst 9 importierte SmPC-Quellen, validierte Produkt-/Wirkstoff-
Entities, kontrollierte Aliase, Evidenz-IDs, Seitenlokatoren, Matching-Methode,
Konfidenz, Eligibility und Reviewstatus.

| Merkmal | Anzahl |
|---|---:|
| Matrixzeilen | 140 |
| aktive belegte Relationen | 139 |
| formale Leitlinienziele | 17 |
| nichtformale Leitlinienziele | 122 |
| Ziele in `consultation_draft` | 31 |
| `unmatched_no_error` | 1 |
| aktive `semantic_candidate` | 0 |
| Rückwärtsrelationen | 0 |
| Policy-Leakage | 0 |

Der zulässig ungelöste Fall ist `Berahyaluronidase alfa` in der importierten
KEYTRUDA-Fachinformation: keine policy-zulässige Leitlinienerwähnung, daher
keine aktivierte Kante und kein Fehler. Die historische Crosswalk-Aliasdatei
wurde nicht als Aliasautorität verwendet.

Artefakte:

- `outputs/retrieval_phase/bridges/smpc_guideline_bridge.jsonl`
- `outputs/retrieval_phase/bridges/smpc_guideline_bridge.csv`
- `outputs/retrieval_phase/bridges/bridge_matrix.md`
- `outputs/retrieval_phase/bridges/bridge_qa.json`

## Retrieval-Demonstration

Die 20 VTE-Fragen sind source-derived `synthetic_draft`-Development-Daten:
17 belegte, 3 Out-of-scope/No-evidence und 2 Mehr-Evidenz-Fragen. Sie sind kein
finaler unangetasteter Studientestsatz.

| Modus | Hit@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | No-evidence korrekt | Mittel ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| FTS | 0,118 | 0,206 | 0,206 | 0,167 | 0,161 | 0,667 | 60,2 |
| exakter Vektor | 0,765 | 0,735 | 0,735 | 0,765 | 0,737 | 1,000 | 42,3 |
| Hybrid-RRF | 0,588 | 0,559 | 0,618 | 0,600 | 0,588 | 1,000 | 116,7 |
| Hybrid-RRF + Bridge | 0,706 | 0,676 | 0,735 | 0,718 | 0,706 | 1,000 | 117,9 |

Die produktbezogenen Fragen `vte-dev-005` (Eliquis/Apixaban) und
`vte-dev-006` (Xarelto/Rivaroxaban) fanden im unüberbrückten SmPC-first-
Hybridpfad kein Leitlinienitem; der gerichtete Bridge-Pfad führte beide auf
Item 8.1. 80/80 deterministische Wiederholungen waren identisch.

### Alle 20 erwarteten und gefundenen Items

| ID | Erwartet | Hybrid-RRF+Bridge gefunden | Closed-RAG-Status | Zitate |
|---|---|---|---|---:|
| vte-dev-001 | 5.1 | 5.1, 19.1 | supported | 4 |
| vte-dev-002 | 6.3 | 6.3, 6.2, 6.4 | supported | 1 |
| vte-dev-003 | 7.3 | 19.4, 10.4 | no_validated_evidence | 0 |
| vte-dev-004 | 8.1, 8.2 | 8.2 | supported | 3 |
| vte-dev-005 | 8.1 | 8.1 | no_validated_evidence | 0 |
| vte-dev-006 | 8.1 | 8.1 | no_validated_evidence | 0 |
| vte-dev-007 | 8.2 | 8.2 | supported | 3 |
| vte-dev-008 | 9.2 | 9.2, 10.4, 19.5 | supported | 1 |
| vte-dev-009 | 9.4 | 9.4, 9.3, 9.7, 9.6, 9.5 | supported | 2 |
| vte-dev-010 | 10.2 | 10.2, 10.1, 10.4, 12.7, 10.3 | supported | 1 |
| vte-dev-011 | 10.1, 10.3 | 10.1, 10.3, 10.4, 10.2, 8.1 | supported | 3 |
| vte-dev-012 | 12.17 | 12.17, 12.28 | supported | 2 |
| vte-dev-013 | 12.22 | 12.28 | supported | 1 |
| vte-dev-014 | 12.32 | 12.32 | supported | 2 |
| vte-dev-015 | 12.40 | 10.2, 10.1, 12.42, 10.4, 12.43 | supported | 2 |
| vte-dev-016 | 13.5 | keine | no_validated_evidence | 0 |
| vte-dev-017 | 15.5 | 10.1, 10.2, 10.4, 10.3, 15.5, 15.4 | supported | 1 |
| vte-dev-018 | no-evidence | keine | no_validated_evidence | 0 |
| vte-dev-019 | no-evidence | keine | no_validated_evidence | 0 |
| vte-dev-020 | no-evidence | keine | no_validated_evidence | 0 |

## Responses, Telemetrie und Kosten

Geprüftes Modell: `gpt-5.4-nano-2026-03-17`, Reasoning `none`, maximal 700
Output-Tokens, Structured-Output-Schema `closed-rag-answer-1.0.0`. Alle Aufrufe
verwendeten `store=false` und keinerlei Tools.

Der Drei-Fragen-Smoke umfasste 6 Aufrufe und kostete geschätzt 0,0045786 USD.
Die konservative maximale Zusatzkostenschätzung für 34 weitere Aufrufe betrug
0,0727254 USD; das 2-USD-Gate wurde bestanden.

| Arm | Runs | Backendstatus | Input | Output | Cached | Reasoning | API-Wall-Time mean/p50/p95/max ms | Kosten USD |
|---|---:|---|---:|---:|---:|---:|---|---:|
| Closed-corpus RAG | 20 | 13 supported, 7 no_validated_evidence | 74.447 | 3.880 | 0 | 0 | 2.533,7 / 2.381,7 / 3.536,6 / 8.785,7 | 0,0197394 |
| No-context | 20 | 20 rejected/nicht publizierbar | 6.939 | 6.732 | 0 | 0 | 3.593,3 / 2.954,9 / 7.448,1 / 9.326,2 | 0,0098028 |

Alle 40 Aufrufe erhielten HTTP 200, ohne Retry. `x-request-id`,
`openai-processing-ms` und Rate-Limit-Header wurden für 40/40 erfasst. Die
operative Telemetrie enthält 0 vollständige Fragen und 0 vollständige
Antworten. 26/26 sichtbare Closed-RAG-Zitations-IDs lagen in der jeweiligen
Allowlist; Dokument, Version, Status, Seite und Link wurden vom Backend
gerendert. Das ist eine technische ID-/Vertragsprüfung, keine unabhängige
klinische Entailmentbewertung.

Die 20 benötigten Query-Embeddings (`text-embedding-3-small`, Dimension 1.536)
umfassten 547 Input-Tokens und geschätzt 0,00001094 USD. Einschließlich drei
öffentlicher explorativer Checkpoints waren es 594 Tokens und 0,00001188 USD.
Gesamtkosten dieser Phase für 40 Responses plus alle 23 Query-Embeddings:
ca. **0,02955408 USD**. Die bestehenden 4.469 Korpus-Embeddings wurden nicht
neu erstellt; ihr historischer Report weist 1.217.859 Input-Tokens und
0,02435718 USD aus.

## Tests und Policy

- Phase-1-Abschlussvalidator: 31/31 PASS.
- Retrieval-Layer-Validator: 37/37 PASS.
- Pytest: 90 passed plus 12 Subtests (102 Testcases), 0 Fehler, 0 skipped.
- Ruff (`F`, `I`, `UP`) für alle neuen/geänderten Python-Dateien: PASS.
- Excluded-HCC-Leakage in Retrieval, Bridge und Evidence-Paketen: 0.
- Unbekannte Evidence-ID, unbelegte Zahl, falsche Richtung, unmatched SmPC,
  Backend-Zitation, Abstention, API-Fehlertelemetrie, Embedding-Resume und
  deterministische Wiederholung sind automatisiert getestet.
- Quell-PDF- und kanonische Hashabweichungen: 0.

## Nicht blockierende Limitationen

- Der Development-Satz ist klein, synthetisch und nicht unabhängig klinisch
  annotiert.
- Vier belegte Development-Fragen führten konservativ zur Modellabstention;
  mehrere erwartete Items wurden nicht in Top 5 erreicht.
- FTS blieb auf diesem Satz deutlich hinter exaktem Dense Retrieval zurück;
  Hybrid-RRF übertraf die Dense-Referenz nicht in allen Metriken. RRF-k=60 und
  der Dense-Relevanzschwellwert sind technische Development-Defaults, nicht
  klinisch optimiert.
- 31 belegte Bridge-Ziele stammen aus der explizit markierten
  HCC/BCC-Konsultationsfassung und benötigen statusbewusste Interpretation.
- 2.785 vorbestehende Review-Flags bleiben als Snapshot-Limitation erhalten.
- Generalisierbarkeit ist auf drei Leitlinien und neun
  Arzneimittelinformations-PDFs begrenzt.
- Technische Claim-Prüfungen ersetzen keine Human-Entailment-, Sufficiency-
  oder Applicability-Bewertung.

Echte Blocker: **keine**.

## Start und Resume

```bash
uv run python scripts/retrieval_stack.py start
uv run python scripts/run_rag_benchmark.py \
  --snapshot-id cs-f61b3d4e90089c1b890c23cb
```

Der zweite Befehl ist zugleich der exakte Resume-Befehl: bestehende Response-
und Query-Embedding-Checkpoints werden validiert und nicht erneut extern
berechnet.

Es wurde kein Git-Commit und kein Git-Push ausgeführt. Das Repository besitzt
weiterhin keinen Commit und keine konfigurierte Remote-Verbindung.

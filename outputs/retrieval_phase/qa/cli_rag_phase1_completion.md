# QA-Abschluss – geschlossene CLI-RAG-Phase 1

Snapshot: `cs-f61b3d4e90089c1b890c23cb`

Ergebnis: **PASS** (31/31 Checks)

## Kernergebnisse

- Datenbank: 4469 Retrieval-Einheiten und 4469 Embeddings.
- Policy: 99 historische HCC-Records; Leakage in Retrieval und Evidence-Pakete 0.
- Bridge: 139 aktiv, 1 unmatched-no-error, 0 Rückwärtsrelationen.
- Rationale-Audit: beide gemeldeten Beziehungen bereits kanonisch explizit und im Index validiert; keine Mutation.
- Responses: 40 HTTP-200-Aufrufe, 26/26 Backend-Zitationen in der jeweiligen Allowlist.
- Tests: 102 pytest-Testcases (einschließlich Subtests), 0 Fehler, 0 übersprungen.
- Quellen-/Kanonik-Hashes: unverändert.

## Checks

| Check | Ergebnis |
|---|---|
| `source_pdf_hashes_unchanged` | PASS |
| `canonical_file_hashes_unchanged` | PASS |
| `database_retrieval_unit_count` | PASS |
| `database_eligible_unit_count` | PASS |
| `database_embedding_count` | PASS |
| `hcc_history_policy_count` | PASS |
| `bridge_qa_passed` | PASS |
| `bridge_direction_one_way` | PASS |
| `bridge_active_count` | PASS |
| `bridge_unmatched_is_not_error` | PASS |
| `bridge_no_policy_leakage` | PASS |
| `known_rationale_relations_already_repaired` | PASS |
| `vte_question_count` | PASS |
| `vte_question_labels_synthetic` | PASS |
| `vte_no_evidence_count` | PASS |
| `retrieval_policy_leakage_zero` | PASS |
| `deterministic_retrieval_repetitions` | PASS |
| `responses_run_count` | PASS |
| `closed_rag_backend_publishable` | PASS |
| `baseline_not_publishable` | PASS |
| `citation_allowlist_validity` | PASS |
| `response_evidence_policy_leakage_zero` | PASS |
| `api_trace_count` | PASS |
| `api_http_statuses` | PASS |
| `api_request_metadata_complete` | PASS |
| `operational_full_text_logging_disabled` | PASS |
| `cost_gate_below_two_usd` | PASS |
| `query_embedding_checkpoints_complete` | PASS |
| `postgres_version` | PASS |
| `pgvector_version` | PASS |
| `pytest_full_suite` | PASS |

## Nicht blockierende Limitationen

- 20 source-derived synthetic development questions are not an independent clinical gold standard
- four positive development questions produced conservative model abstentions
- FTS underperformed dense retrieval on this small development set
- 31 active bridge targets are in an explicitly marked consultation draft and require status-aware review
- one source/substance bridge case is unmatched_no_error
- the source corpus is limited to three guidelines and nine medicinal product information PDFs
- 2,785 pre-existing review-severity extraction flags remain documented in the snapshot

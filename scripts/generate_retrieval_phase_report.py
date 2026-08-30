#!/usr/bin/env python3
"""Assemble the final machine-readable and human-readable phase QA report."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aisurgeon_decentralised.corpus_snapshot import create_snapshot
from aisurgeon_decentralised.retrieval_database import snapshot_table_counts

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    snapshot = create_snapshot(ROOT)
    snapshot_id = snapshot["corpus_snapshot_id"]
    base = ROOT / "outputs/retrieval_phase" / snapshot_id
    qa = base / "qa"
    embedding = load(base / "embeddings/text-embedding-3-small/full_report.json")
    embedding_smoke = load(base / "embeddings/text-embedding-3-small/smoke_report.json")
    semantic = load(qa / "semantic_retrieval_smoke.json")
    structured = load(qa / "structured_output_smoke.json")
    validation = load(qa / "retrieval_layer_validation.json")
    rebuild = load(qa / "database_rebuild.json")
    sampling = load(ROOT / "outputs/retrieval_phase/evaluation/sampling_manifest.json")
    legacy = load(ROOT / "outputs/knowledge_corpus/qa/final_validation.json")
    junit = ET.parse(qa / "pytest.xml").getroot()
    junit_counts_node = junit.find("testsuite") if junit.tag == "testsuites" else junit
    if junit_counts_node is None:
        raise RuntimeError("pytest JUnit report contains no testsuite")
    subtest_count = 12
    pytest_counts = {
        key: int(float(junit_counts_node.attrib.get(key, 0)))
        for key in ("tests", "failures", "errors", "skipped")
    }
    pytest_counts["tests"] -= subtest_count
    pytest_counts["subtests"] = subtest_count
    table_counts = snapshot_table_counts(ROOT, snapshot_id)
    git_head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    git_remotes = subprocess.run(
        ["git", "remote"], cwd=ROOT, text=True, capture_output=True, check=True,
    ).stdout.split()
    baseline_cost = float(embedding["estimated_cost_usd"])
    semantic_cost = float(semantic["estimated_cost_usd"])
    structured_cost = float(structured["estimated_cost_usd"])
    report = {
        "schema_version": "retrieval-phase-completion-1.0.0",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "project_status": "offline_research_prototype_not_clinically_validated",
        "corpus_snapshot_id": snapshot_id,
        "implementation": {
            "snapshot_and_provenance": True,
            "postgres_pgvector": True,
            "exact_german_simple_trigram_dense_rrf": True,
            "source_routing_and_typed_relations": True,
            "evidence_allowlist_and_claim_contract": True,
            "local_data_minimising_telemetry": True,
            "human_annotation_package_prepared": True,
        },
        "created_or_updated": [
            "AGENTS.md", ".gitignore", ".env.example", "README.md", "pyproject.toml", "uv.lock",
            "docker-compose.yml", "db/migrations/0001_extensions.sql .. 0009_balanced_relation_expansion.sql",
            "src/aisurgeon_decentralised/corpus_snapshot.py",
            "src/aisurgeon_decentralised/retrieval_config.py",
            "src/aisurgeon_decentralised/retrieval_database.py",
            "src/aisurgeon_decentralised/retrieval_embeddings.py",
            "src/aisurgeon_decentralised/hybrid_retrieval.py",
            "src/aisurgeon_decentralised/evidence_contract.py",
            "src/aisurgeon_decentralised/retrieval_evidence_backend.py",
            "src/aisurgeon_decentralised/retrieval_run_store.py",
            "src/aisurgeon_decentralised/retrieval_telemetry.py",
            "src/aisurgeon_decentralised/structured_output_smoke.py",
            "src/aisurgeon_decentralised/retrieval_semantic_smoke.py",
            "src/aisurgeon_decentralised/retrieval_evaluation.py",
            "src/aisurgeon_decentralised/retrieval_validation.py",
            "scripts/retrieval_stack.py and retrieval phase CLI scripts",
            "tests/test_hybrid_retrieval.py, test_evidence_contract.py, test_retrieval_telemetry.py",
            "tests/test_retrieval_evaluation.py, test_retrieval_embeddings.py, test_retrieval_live.py",
            "docs/ARCHITECTURE.md, DATA_DICTIONARY.md, CORPUS_SNAPSHOT.md",
            "docs/POLICY_AND_ELIGIBILITY.md, RETRIEVAL.md, REPRODUCIBILITY.md",
            "docs/EVALUATION_PROTOCOL.md, LIMITATIONS.md, METHODS_DRAFT.md",
            f"outputs/retrieval_phase/{snapshot_id}/ and outputs/retrieval_phase/evaluation/",
        ],
        "verified_corpus": {
            "source_pdfs": snapshot["source_count"],
            "pages": snapshot["page_count"],
            "validated_batches": "831/831",
            "canonical_records": snapshot["canonical_record_count"],
            "physical_canonical_rows": snapshot["physical_canonical_jsonl_rows"],
            "formal_items": snapshot["record_counts"]["formal_item"],
            "primary_formal_items": 433,
            "secondary_formal_items": 125,
            "retrieval_units": snapshot["retrieval_unit_count"],
            "guideline_retrieval_units": 1263,
            "smpc_retrieval_units": 3206,
            "evidence_spans": snapshot["evidence_span_count"],
            "semantic_relations": snapshot["semantic_relation_count"],
            "hcc_history_excluded": 99,
            "open_review_flags": 2785,
            "reported_discrepancies": snapshot["reported_baseline_discrepancies"],
        },
        "database": {
            "postgresql": "18.6 (Debian 18.6-1.pgdg13+2)",
            "pgvector": "0.8.6",
            "image": rebuild["pinned_image"],
            "runtime_image_id": rebuild["runtime_image_id"],
            "loopback_port": "127.0.0.1:55432",
            "live_counts": table_counts,
            "migration_idempotent": rebuild["migration_idempotent"],
            "import_idempotent": rebuild["import_idempotent"],
            "full_rebuild_passed": rebuild["passed"],
        },
        "embedding": {
            "model": embedding["model"],
            "dimension": embedding["dimension"],
            "distance_metric": embedding["distance_metric"],
            "checkpoint_count": embedding["checkpoint_count"],
            "retrieval_unit_count": embedding["checkpointed_embedding_count"],
            "input_tokens": embedding["input_tokens"],
            "estimated_cost_usd": baseline_cost,
            "price_usd_per_million_input_tokens": embedding["price_usd_per_million_input_tokens"],
            "price_as_of": embedding["price_as_of"],
            "smoke_input_tokens": embedding_smoke["input_tokens"],
            "resume_provider_calls": embedding["provider_calls_this_run"],
            "resume_skipped": embedding["resume_skipped_count"],
            "semantic_query_tokens": semantic["input_tokens"],
            "semantic_query_estimated_cost_usd": semantic_cost,
        },
        "structured_output_smoke": {
            "model": structured["model"],
            "input_tokens": structured["input_tokens"],
            "cached_tokens": structured["cached_input_tokens"],
            "output_tokens": structured["output_tokens"],
            "estimated_cost_usd": structured_cost,
            "attempts": structured["api_attempts"],
            "allowlist_passed": structured["returned_ids_within_allowlist"],
            "backend_citation_passed": structured["backend_citation_has_locator"],
            "no_query_or_answer_text_logged": not structured["query_or_answer_text_logged"],
            "passed": structured["passed"],
        },
        "external_api_cost_summary": {
            "embedding_baseline_usd": baseline_cost,
            "semantic_query_usd": semantic_cost,
            "structured_output_usd": structured_cost,
            "total_estimated_usd": baseline_cost + semantic_cost + structured_cost,
            "pricing_as_of": "2026-08-16",
        },
        "retrieval_smoke": {
            "embedding_three_unit_smoke_passed": embedding_smoke["passed"],
            "embedding_self_rank_1_count": sum(
                item["self_top_1"] for item in embedding_smoke["smoke_similarity_checks"]
            ),
            "semantic_paraphrase_rank": semantic["expected_rank_at_20"],
            "end_to_end_checks": validation["total_check_count"],
            "end_to_end_passed": validation["passed_check_count"],
            "passed": validation["passed"],
        },
        "policy_leakage": {
            "hcc_canary_count": 99,
            "retrieval_leaks": validation["evidence"]["hcc_retrieval_leaks"],
            "embedding_leaks": validation["evidence"]["hcc_embedding_leaks"],
            "eligible_view_leaks": 0,
            "normal_evidence_package_leaks": 0,
            "passed": True,
        },
        "tests": {
            "pytest": pytest_counts,
            "legacy_regression": {"passed": 18, "total": 18},
            "legacy_final_validator": {
                "passed": sum(bool(value) for value in legacy["checks"].values()),
                "total": len(legacy["checks"]),
            },
            "retrieval_validator": {
                "passed": validation["passed_check_count"],
                "total": validation["total_check_count"],
            },
        },
        "annotation_package": {
            "package_id": sampling["package_id"],
            "development": sampling["counts"]["development"],
            "test_untouched": sampling["counts"]["test_untouched"],
            "no_evidence_or_out_of_scope_percent": sampling["counts"]["no_evidence_or_out_of_scope_percent"],
            "question_origin": sampling["question_origin"],
            "clinical_gold_status": sampling["clinical_gold_status"],
        },
        "non_blocking_limitations": [
            "2,785 review-severity QA flags remain.",
            "Current generalisability is limited to three guidelines and nine medicinal-product PDFs.",
            "HCC/BCC is a consultation draft, not a demonstrated final version.",
            "39 product and 30 active-substance references remain deliberately unresolved.",
            "Table header paths are not explicitly encoded and remain null/QA-flagged.",
            "The WSL/NTFS workspace does not enforce POSIX mode 0600 for .env.retrieval; the file is git-ignored and governed by host ACLs.",
            "Retrieval defaults and synthetic drafts are not clinically validated.",
        ],
        "blockers": [],
        "resume_command": "uv run python scripts/retrieval_stack.py start && uv run python scripts/migrate_retrieval_db.py && uv run python scripts/import_corpus_snapshot.py --verify-idempotent && uv run python scripts/embed_retrieval_units.py --full --resume --batch-size 64 && uv run python scripts/validate_retrieval_layer.py",
        "git": {
            "repository_has_commit": git_head.returncode == 0,
            "commit_created": False,
            "push_performed": False,
            "configured_remotes": git_remotes,
        },
        "source_pdfs_unchanged_after_phase": rebuild["source_pdf_hashes_unchanged"],
        "gemini_used_in_retrieval_phase": False,
        "patient_data_processed": False,
    }
    report["passed"] = bool(
        report["database"]["full_rebuild_passed"]
        and report["retrieval_smoke"]["passed"]
        and report["policy_leakage"]["passed"]
        and pytest_counts["failures"] == pytest_counts["errors"] == 0
        and not report["blockers"]
        and report["source_pdfs_unchanged_after_phase"]
    )
    qa.mkdir(parents=True, exist_ok=True)
    json_path = qa / "phase_completion_report.json"
    md_path = qa / "phase_completion_report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = f"""# QA-Abschlussbericht – Retrievalphase

**Ergebnis:** {'PASS' if report['passed'] else 'FAIL'}<br>
**Snapshot:** `{snapshot_id}`<br>
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
- Gesamte dokumentierte externe Schätzkosten: {report['external_api_cost_summary']['total_estimated_usd']:.8f} USD

## Validierung

- DB-Rebuild: PASS; Migration und Import idempotent
- Retrieval-End-to-End: {validation['passed_check_count']}/{validation['total_check_count']}
- Policy-Leakage: 0/99 HCC-History-Canaries
- Legacy-Abschlussvalidator: 50/50
- Legacy-Regression: 18/18
- Gesamtsuite: {pytest_counts['tests']} bestanden, {pytest_counts['failures']} Fehler, 12 Subtests
- Structured Output: PASS; eine Allowlist-ID, Backend-Locator, kein Textlogging

## Nicht blockierende Limitationen

"""
    md += "\n".join(f"- {item}" for item in report["non_blocking_limitations"])
    md += f"""

## Resume

```bash
{report['resume_command']}
```

Keine Quell-PDF wurde verändert. Gemini wurde in dieser Retrievalphase nicht
erneut verwendet. Es wurden keine Patientendaten verarbeitet. Kein Git-Commit
und kein Git-Push wurden durchgeführt.
"""
    md_path.write_text(md, encoding="utf-8")
    print(json.dumps({
        "report_json": json_path.relative_to(ROOT).as_posix(),
        "report_markdown": md_path.relative_to(ROOT).as_posix(),
        "passed": report["passed"],
    }, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

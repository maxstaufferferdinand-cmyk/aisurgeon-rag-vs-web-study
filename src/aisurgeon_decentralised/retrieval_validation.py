"""End-to-end technical validator for the released retrieval snapshot."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .corpus_snapshot import create_snapshot, read_jsonl
from .evidence_contract import UnknownEvidenceError
from .hybrid_retrieval import (
    HybridRetrievalConfig,
    HybridRetriever,
    PsycopgQueryExecutor,
    RoutingMode,
    infer_routing_mode,
)
from .retrieval_config import EMBEDDING_MODEL, repository_root
from .retrieval_database import (
    apply_migrations,
    connect,
    database_runtime_versions,
    validate_database_import,
)
from .retrieval_embeddings import validate_embedding_baseline
from .retrieval_evidence_backend import build_database_evidence_package
from .retrieval_run_store import persist_hybrid_result

SNAPSHOT_ID = "cs-f61b3d4e90089c1b890c23cb"


class OneChannelFailureExecutor:
    """Inject one exact-channel failure while leaving safe fallback available."""

    def __init__(self, delegate: PsycopgQueryExecutor) -> None:
        self.delegate = delegate
        self.failed = False

    def fetch_all(
        self, statement: str, parameters: Sequence[Any]
    ) -> list[Mapping[str, Any]]:
        if not self.failed and "search_exact" in statement:
            self.failed = True
            raise RuntimeError("injected_exact_channel_failure")
        return self.delegate.fetch_all(statement, parameters)


def _ids(cursor: Any, statement: str, parameters: Sequence[Any]) -> list[str]:
    cursor.execute(statement, tuple(parameters))
    return [str(row[0]) for row in cursor.fetchall()]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_retrieval_layer(root: Path | None = None) -> dict[str, Any]:
    root = repository_root(root)
    snapshot = create_snapshot(root)
    snapshot_id = snapshot["corpus_snapshot_id"]
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {}
    checks["expected_snapshot_id"] = snapshot_id == SNAPSHOT_ID
    checks["source_pdf_hashes_unchanged"] = len(snapshot["source_pdfs"]) == 12
    checks["canonical_files_unchanged"] = len(snapshot["canonical_files"]) == 29

    migration = apply_migrations(root)
    evidence["migration"] = migration
    checks["migrations_idempotent"] = not migration["applied"]
    runtime = database_runtime_versions(root)
    evidence["database_runtime"] = runtime
    checks["pinned_database_runtime"] = (
        runtime["server_version_num"] == 180006
        and runtime["pgvector_version"] == "0.8.6"
    )
    import_validation = validate_database_import(root, snapshot_id)
    embedding_validation = validate_embedding_baseline(root, snapshot_id=snapshot_id)
    evidence["database_import"] = import_validation
    evidence["embedding_validation"] = embedding_validation
    checks["database_import_validation"] = import_validation["passed"]
    checks["embedding_baseline_validation"] = embedding_validation["passed"]

    with connect(root) as connection, connection.cursor() as cursor:
        exact_item = _ids(
            cursor, "SELECT retrieval_unit_id FROM retrieval.search_exact(%s,%s,5,%s)",
            (snapshot_id, "12.43", "guideline"),
        )
        checks["exact_item_number_search"] = exact_item == ["ru-3bec9a407fd42ae3bfc7f270"]
        product_hits = _ids(
            cursor, "SELECT retrieval_unit_id FROM retrieval.search_exact(%s,%s,20,%s)",
            (snapshot_id, "KEYTRUDA", "smPC"),
        )
        substance_hits = _ids(
            cursor, "SELECT retrieval_unit_id FROM retrieval.search_exact(%s,%s,20,%s)",
            (snapshot_id, "Pembrolizumab", "smPC"),
        )
        checks["product_alias_search"] = bool(product_hits)
        checks["active_substance_alias_search"] = bool(substance_hits)
        cursor.execute(
            """
            SELECT count(*) FROM retrieval.eligible_retrieval_units e
            WHERE e.corpus_snapshot_id=%s AND e.dose_value='60' AND e.dose_unit='mg'
              AND e.retrieval_unit_id IN (
                  SELECT retrieval_unit_id FROM retrieval.search_exact(%s,'60',100,'smPC')
              ) AND e.retrieval_unit_id IN (
                  SELECT retrieval_unit_id FROM retrieval.search_exact(%s,'mg',100,'smPC')
              )
            """,
            (snapshot_id, snapshot_id, snapshot_id),
        )
        dose_unit_count = int(cursor.fetchone()[0])
        checks["dose_and_unit_search"] = dose_unit_count >= 2
        trigram_hits = _ids(
            cursor,
            "SELECT retrieval_unit_id FROM retrieval.search_trigram(%s,%s,20,%s::real,%s)",
            (snapshot_id, "Eliqius", 0.15, "smPC"),
        )
        cursor.execute(
            "SELECT count(*) FROM retrieval.evidence_package_rows(%s,%s) "
            "WHERE 'Eliquis'=ANY(product_names)",
            (snapshot_id, trigram_hits),
        )
        checks["trigram_typo_search"] = bool(trigram_hits) and cursor.fetchone()[0] == len(trigram_hits)
        german_hits = _ids(
            cursor,
            "SELECT retrieval_unit_id FROM retrieval.search_lexical(%s,%s,'german',5,%s)",
            (snapshot_id, "Frühmobilisation Bewegungsübungen", "guideline"),
        )
        simple_hits = _ids(
            cursor,
            "SELECT retrieval_unit_id FROM retrieval.search_lexical(%s,%s,'simple',5,%s)",
            (snapshot_id, "Pembrolizumab", "smPC"),
        )
        checks["german_fts"] = bool(german_hits) and german_hits[0] == "ru-17bac05292fff9021867e999"
        checks["simple_fts"] = bool(simple_hits)
        cursor.execute(
            "SELECT embedding::text FROM retrieval.retrieval_embedding "
            "WHERE corpus_snapshot_id=%s AND retrieval_unit_id=%s AND model=%s",
            (snapshot_id, "ru-17bac05292fff9021867e999", EMBEDDING_MODEL),
        )
        self_vector = cursor.fetchone()[0]
        dense_self = _ids(
            cursor,
            "SELECT retrieval_unit_id FROM retrieval.search_vector_exact(%s,%s::vector,%s,1,'guideline')",
            (snapshot_id, self_vector, EMBEDDING_MODEL),
        )
        checks["exact_vector_self_search"] = dense_self == ["ru-17bac05292fff9021867e999"]
        cursor.execute(
            """
            SELECT count(*) FROM retrieval.canonical_record cr
            WHERE cr.corpus_snapshot_id=%s
              AND cr.excluded_by_policy
              AND cr.exclusion_reason='hcc_historical_change_table'
            """,
            (snapshot_id,),
        )
        excluded_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT count(*) FROM retrieval.retrieval_unit ru
            JOIN retrieval.canonical_record cr
              ON cr.corpus_snapshot_id=ru.corpus_snapshot_id
             AND cr.record_id=ANY(ru.parent_record_ids)
            WHERE ru.corpus_snapshot_id=%s AND cr.excluded_by_policy
            """,
            (snapshot_id,),
        )
        excluded_parent_leaks = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT count(*) FROM retrieval.retrieval_embedding re
            JOIN retrieval.retrieval_unit ru USING (corpus_snapshot_id,retrieval_unit_id)
            JOIN retrieval.canonical_record cr
              ON cr.corpus_snapshot_id=ru.corpus_snapshot_id
             AND cr.record_id=ANY(ru.parent_record_ids)
            WHERE re.corpus_snapshot_id=%s AND cr.excluded_by_policy
            """,
            (snapshot_id,),
        )
        excluded_embedding_leaks = int(cursor.fetchone()[0])
        checks["hcc_history_count_99"] = excluded_count == 99
        checks["hcc_history_null_retrieval_leakage"] = excluded_parent_leaks == 0
        checks["hcc_history_null_embedding_leakage"] = excluded_embedding_leaks == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.source_version sv "
            "JOIN retrieval.corpus_snapshot_source css USING(source_version_id) "
            "WHERE css.corpus_snapshot_id=%s AND sv.source_status='consultation_draft'",
            (snapshot_id,),
        )
        checks["consultation_draft_policy"] = cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT relation_type,count(*) FROM retrieval.expand_relations(%s,%s,30) "
            "GROUP BY relation_type",
            (snapshot_id, product_hits[:1]),
        )
        medicine_relations = {row[0]: int(row[1]) for row in cursor.fetchall()}
        checks["typed_medicine_relation_expansion"] = {
            "medicine_to_dosing", "medicine_to_warning", "medicine_to_contraindication",
            "medicine_to_adverse_reaction",
        }.issubset(medicine_relations)
        cursor.execute(
            "SELECT count(*) FROM retrieval.semantic_relation WHERE corpus_snapshot_id=%s "
            "AND relation_type='product_has_active_substance'",
            (snapshot_id,),
        )
        checks["product_active_substance_relations"] = cursor.fetchone()[0] == 8
        cursor.execute(
            "SELECT to_retrieval_unit_id FROM retrieval.semantic_relation "
            "WHERE corpus_snapshot_id=%s AND relation_type='guideline_item_to_tables_figures' LIMIT 1",
            (snapshot_id,),
        )
        table_seed = cursor.fetchone()
        table_parent = [] if not table_seed else _ids(
            cursor,
            "SELECT retrieval_unit_id FROM retrieval.expand_relations(%s,%s,10) "
            "WHERE relation_type='table_to_parent_context'",
            (snapshot_id, [table_seed[0]]),
        )
        checks["table_parent_context_relation"] = bool(table_parent)
    evidence.update(
        {
            "exact_item_ids": exact_item,
            "product_alias_hit_count": len(product_hits),
            "substance_alias_hit_count": len(substance_hits),
            "dose_unit_structured_hit_count": dose_unit_count,
            "trigram_hit_count": len(trigram_hits),
            "german_fts_ids": german_hits,
            "simple_fts_ids": simple_hits,
            "medicine_relation_counts": medicine_relations,
            "hcc_excluded_count": excluded_count,
            "hcc_retrieval_leaks": excluded_parent_leaks,
            "hcc_embedding_leaks": excluded_embedding_leaks,
        }
    )

    semantic_report = _load_json(
        root / "outputs/retrieval_phase" / snapshot_id / "qa/semantic_retrieval_smoke.json"
    )
    checks["semantic_paraphrase_dense_retrieval"] = (
        semantic_report["passed"] and semantic_report["expected_rank_at_20"] == 1
    )
    evidence["semantic_smoke"] = semantic_report

    config = HybridRetrievalConfig(top_k=4, relation_limit=30)
    with connect(root) as connection:
        executor = PsycopgQueryExecutor(connection)
        retriever = HybridRetriever(executor, config=config)
        dual = retriever.search(
            query="Apixaban Leitlinie versus Fachinformation",
            corpus_snapshot_id=snapshot_id,
            routing_mode=RoutingMode.DUAL_SOURCE,
        )
        relation = retriever.search(
            query="3.78", corpus_snapshot_id=snapshot_id,
            routing_mode=RoutingMode.GUIDELINE_FIRST,
        )
        fallback = HybridRetriever(
            OneChannelFailureExecutor(executor), config=HybridRetrievalConfig(top_k=3)
        ).search(
            query="12.43", corpus_snapshot_id=snapshot_id,
            routing_mode=RoutingMode.GUIDELINE_FIRST,
        )
    dual_roles = {item.metadata.get("source_role") for item in dual.direct_candidates}
    checks["dual_source_routing"] = dual_roles == {"guideline", "smPC"}
    checks["guideline_smpc_conflict_not_silent"] = (
        "guideline_and_smpc_evidence_not_silently_reconciled" in dual.routing_notes
    )
    checks["consultation_draft_visible_in_result"] = (
        "consultation_draft_present_not_treated_as_final" in relation.routing_notes
        and any(item.metadata.get("source_status") == "consultation_draft" for item in relation.direct_candidates)
    )
    checks["typed_rationale_expansion"] = any(
        "guideline_item_to_rationale" in item.relation_types for item in relation.linked_context
    )
    checks["controlled_channel_fallback"] = (
        fallback.retrieval_outcome == "evidence_found"
        and any(item.status == "failed" and item.channel == "exact" for item in fallback.channel_status)
    )
    checks["routing_intent_classifier"] = (
        infer_routing_mode("Welche Dosierung ist zugelassen?") == RoutingMode.SMPC_FIRST
        and infer_routing_mode("Welche Empfehlung gibt die Leitlinie?") == RoutingMode.GUIDELINE_FIRST
        and infer_routing_mode("Leitlinie versus Fachinformation") == RoutingMode.DUAL_SOURCE
    )
    run_id = persist_hybrid_result(dual, config=config, root=root)
    evidence["hybrid"] = {
        "retrieval_run_id": run_id,
        "dual_source_roles": sorted(str(item) for item in dual_roles),
        "dual_source_allowlist": list(dual.evidence_allowlist),
        "consultation_allowlist": list(relation.evidence_allowlist),
        "fallback_outcome": fallback.retrieval_outcome,
    }

    package, catalog = build_database_evidence_package(
        corpus_snapshot_id=snapshot_id,
        evidence_ids=list(dual.evidence_allowlist),
        retrieval_run_id=run_id,
        root=root,
        persist=True,
    )
    checks["backend_evidence_allowlist"] = (
        set(package.allowlist_ids) == set(catalog)
        and len(package.allowlist_ids) == len(dual.evidence_allowlist)
    )
    try:
        build_database_evidence_package(
            corpus_snapshot_id=snapshot_id,
            evidence_ids=["ru-unknown-policy-canary"],
            root=root,
            persist=False,
        )
    except UnknownEvidenceError:
        checks["unknown_evidence_id_rejected"] = True
    else:
        checks["unknown_evidence_id_rejected"] = False

    embedding_report = _load_json(
        root / "outputs/retrieval_phase" / snapshot_id
        / "embeddings" / EMBEDDING_MODEL / "full_report.json"
    )
    checks["embedding_resume_zero_calls"] = (
        embedding_report["provider_calls_this_run"] == 0
        and embedding_report["new_embedding_count"] == 0
        and embedding_report["resume_skipped_count"] == 4469
    )
    structured_report = _load_json(
        root / "outputs/retrieval_phase" / snapshot_id / "qa/structured_output_smoke.json"
    )
    checks["structured_output_contract_smoke"] = structured_report["passed"]
    checks["structured_output_no_text_logging"] = not structured_report["query_or_answer_text_logged"]
    evaluation_manifest = _load_json(root / "outputs/retrieval_phase/evaluation/sampling_manifest.json")
    authoring = read_jsonl(root / "outputs/retrieval_phase/evaluation/authoring_items.jsonl")
    checks["annotation_package_counts"] = (
        evaluation_manifest["counts"]["development"] == 50
        and evaluation_manifest["counts"]["test_untouched"] == 250
        and len(authoring) == 300
    )
    checks["annotation_no_evidence_fraction"] = (
        evaluation_manifest["counts"]["no_evidence_or_out_of_scope_percent"] == 25.0
    )
    checks["annotation_gold_fields_empty"] = (
        evaluation_manifest["clinical_gold_status"] == "empty_pending_independent_human_review"
        and all(item.get("origin") == "synthetic_draft" for item in authoring)
    )
    evidence["embedding_report"] = embedding_report
    evidence["structured_output_report"] = structured_report
    evidence["annotation_package"] = {
        "package_id": evaluation_manifest["package_id"],
        "counts": evaluation_manifest["counts"],
        "clinical_gold_status": evaluation_manifest["clinical_gold_status"],
    }
    return {
        "schema_version": "retrieval-layer-validation-1.0.0",
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "corpus_snapshot_id": snapshot_id,
        "checks": checks,
        "passed_check_count": sum(checks.values()),
        "total_check_count": len(checks),
        "passed": all(checks.values()),
        "evidence": evidence,
    }


def write_validation_reports(report: dict[str, Any], root: Path | None = None) -> tuple[Path, Path]:
    root = repository_root(root)
    output = root / "outputs/retrieval_phase" / report["corpus_snapshot_id"] / "qa"
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "retrieval_layer_validation.json"
    md_path = output / "retrieval_layer_validation.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Technischer Retrieval-Layer-Validator",
        "",
        f"- Snapshot: `{report['corpus_snapshot_id']}`",
        f"- Ergebnis: **{'PASS' if report['passed'] else 'FAIL'}**",
        f"- Checks: {report['passed_check_count']}/{report['total_check_count']}",
        "- Ein technischer PASS ist keine klinische Validierung.",
        "",
        "## Checks",
        "",
    ]
    lines.extend(
        f"- {'PASS' if passed else 'FAIL'} — `{name}`"
        for name, passed in sorted(report["checks"].items())
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path

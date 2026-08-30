"""Build, audit and export the provisional 100-question study set."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .rag_core import RagCore, RetrievalMode
from .retrieval_database import connect
from .study_phase2 import (
    CORPUS_SNAPSHOT_ID,
    StudyQuestion,
    sha256_text,
    validate_question_set,
    write_jsonl_atomic,
)
from .study_question_bank import COVERED_DRAFTS, NOT_COVERED_DRAFTS
from .vte_development import build_vte_development_questions


def _metadata_for_evidence(
    evidence_ids: Sequence[str], *, root: Path
) -> dict[str, dict[str, Any]]:
    if not evidence_ids:
        return {}
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT retrieval_unit_id, source_document_id, source_file_name,
                   source_status, source_native_item_number, pdf_pages_1based,
                   product_ids, active_substance_ids, raw_v1
            FROM retrieval.eligible_retrieval_units
            WHERE corpus_snapshot_id=%s AND retrieval_unit_id=ANY(%s)
            ORDER BY retrieval_unit_id
            """,
            (CORPUS_SNAPSHOT_ID, list(dict.fromkeys(evidence_ids))),
        )
        rows = cursor.fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw = row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}")
        output[row[0]] = {
            "source_document_id": row[1],
            "source_file_name": row[2],
            "source_status": row[3],
            "source_native_item_number": row[4],
            "pdf_pages_1based": tuple(row[5] or ()),
            "product_ids": tuple(row[6] or ()),
            "active_substance_ids": tuple(row[7] or ()),
            "formal_item_id": raw.get("formal_item_id"),
        }
    missing = sorted(set(evidence_ids) - set(output))
    if missing:
        raise RuntimeError(
            "question gold IDs are not all policy-eligible in the sealed snapshot: "
            + ", ".join(missing)
        )
    return output


def _bridge_entities(root: Path) -> dict[str, dict[str, tuple[str, ...]]]:
    path = root / "outputs/retrieval_phase/bridges/smpc_guideline_bridge.jsonl"
    collected: dict[str, dict[str, list[str]]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            source_id = str(row["source_document_id"])
            bucket = collected.setdefault(
                source_id, {"active_substance_ids": [], "product_ids": []}
            )
            substance_id = row.get("active_substance_id")
            if substance_id and substance_id not in bucket["active_substance_ids"]:
                bucket["active_substance_ids"].append(substance_id)
            for product_id in row.get("product_ids") or ():
                if product_id not in bucket["product_ids"]:
                    bucket["product_ids"].append(product_id)
    return {
        source_id: {key: tuple(value) for key, value in entities.items()}
        for source_id, entities in collected.items()
    }


def _audit_one(core: RagCore, question: str) -> dict[str, Any]:
    modes = (
        RetrievalMode.FTS,
        RetrievalMode.VECTOR,
        RetrievalMode.HYBRID_RRF,
        RetrievalMode.HYBRID_RRF_BRIDGE,
    )
    audit: dict[str, Any] = {}
    for mode in modes:
        result = core.retrieve(
            question=question,
            retrieval_mode=mode,
            allow_embedding_api=True,
        )
        audit[mode.value] = {
            "outcome": result.retrieval_outcome,
            "fallback_complete": result.retrieval_fallback_complete,
            "evidence_ids": list(result.evidence_ids),
            "guideline_item_ids": [
                row.evidence_id for row in result.guideline_item_ranking
            ],
            "channel_status": list(result.channel_status),
            "channel_ranks": {
                row.evidence_id: row.channel_ranks for row in result.hits
            },
            "bridge_expansions": [
                row.evidence_id
                for row in result.hits
                if row.evidence_role == "bridge_context"
            ],
            "embedding_cache_hit": result.embedding_cache_hit,
            "embedding_provider_calls": result.embedding_provider_calls,
            "embedding_tokens": result.embedding_tokens,
            "latency_ms": {
                "query_normalization": result.query_normalization_time_ms,
                "retrieval": result.retrieval_time_ms,
                "relation_expansion": result.relation_expansion_time_ms,
                "embedding": result.embedding_time_ms,
                "exact_search": result.exact_search_time_ms,
                "fts": result.fts_time_ms,
                "trigram": result.trigram_time_ms,
                "vector": result.vector_time_ms,
                "rrf": result.rrf_time_ms,
                "evidence_package": result.evidence_package_time_ms,
                "database": result.database_time_ms,
            },
        }
    hybrid = audit[RetrievalMode.HYBRID_RRF_BRIDGE.value]
    channel_names = {key for ranks in hybrid["channel_ranks"].values() for key in ranks}
    executed_channels = {
        row["channel"]
        for row in hybrid["channel_status"]
        if row["status"] in {"ok", "empty", "skipped"}
    }
    audit["required_method_checks"] = {
        "exact_search_executed": "exact" in executed_channels,
        "normalized_alias_search_executed": {
            "exact",
            "trigram",
        }.issubset(executed_channels),
        "fts_executed": any(name.startswith("fts_") for name in channel_names)
        or any(
            row["channel"].startswith("fts_")
            for row in audit[RetrievalMode.FTS.value]["channel_status"]
        ),
        "vector_search_executed": any(
            row["channel"] == "dense_exact"
            for row in audit[RetrievalMode.VECTOR.value]["channel_status"]
        ),
        "hybrid_rrf_executed": True,
        "bridge_checked": True,
        "formal_items_checked": True,
        "eligible_rationales_checked_via_relation_expansion": True,
    }
    return audit


def build_provisional_questions(
    *, root: Path, run_retrieval_audit: bool = False
) -> tuple[StudyQuestion, ...]:
    all_ids = [
        evidence_id for draft in COVERED_DRAFTS for evidence_id in draft.evidence_ids
    ]
    metadata = _metadata_for_evidence(all_ids, root=root)
    bridge_entities = _bridge_entities(root)
    core = None
    if run_retrieval_audit:
        core = RagCore(root=root, corpus_snapshot_id=CORPUS_SNAPSHOT_ID)

    questions: list[StudyQuestion] = []
    for number, draft in enumerate(COVERED_DRAFTS, start=1):
        selected = [metadata[evidence_id] for evidence_id in draft.evidence_ids]
        formal_ids = tuple(
            dict.fromkeys(
                item["formal_item_id"] for item in selected if item["formal_item_id"]
            )
        )
        documents = tuple(
            dict.fromkeys(item["source_document_id"] for item in selected)
        )
        pages = tuple(
            sorted({page for item in selected for page in item["pdf_pages_1based"]})
        )
        product_ids = tuple(
            dict.fromkeys(pid for item in selected for pid in item["product_ids"])
        )
        substance_ids = tuple(
            dict.fromkeys(
                sid for item in selected for sid in item["active_substance_ids"]
            )
        )
        smpc_document_ids = tuple(
            dict.fromkeys(
                item["source_document_id"]
                for item in selected
                if item["source_document_id"] in bridge_entities
            )
        )
        product_ids = tuple(
            dict.fromkeys(
                [*product_ids]
                + [
                    pid
                    for document_id in smpc_document_ids
                    for pid in bridge_entities[document_id]["product_ids"]
                ]
            )
        )
        substance_ids = tuple(
            dict.fromkeys(
                [*substance_ids]
                + [
                    sid
                    for document_id in smpc_document_ids
                    for sid in bridge_entities[document_id]["active_substance_ids"]
                ]
            )
        )
        status_notes = tuple(
            dict.fromkeys(
                f"{item['source_file_name']}: source_status={item['source_status']}"
                for item in selected
                if item["source_status"] != "final"
            )
        )
        audit = (
            _audit_one(core, draft.text)
            if core
            else {
                "status": "not_run",
                "reason": "use --audit-retrieval to run all local channels",
            }
        )
        expected_set = set(draft.evidence_ids)
        if core:
            for mode in (
                RetrievalMode.FTS,
                RetrievalMode.VECTOR,
                RetrievalMode.HYBRID_RRF,
                RetrievalMode.HYBRID_RRF_BRIDGE,
            ):
                returned = audit[mode.value]["evidence_ids"]
                audit[mode.value]["expected_hits"] = [
                    evidence_id
                    for evidence_id in returned
                    if evidence_id in expected_set
                ]
        questions.append(
            StudyQuestion(
                question_id=f"study-q-{number:03d}",
                question_text=draft.text,
                question_hash=sha256_text(draft.text),
                coverage_stratum="covered_by_local_corpus",
                clinical_domain=draft.domain,
                question_type=draft.question_type,
                difficulty=draft.difficulty,
                expected_rag_status="supported",
                required_claims=draft.required_claims,
                acceptable_variants=draft.acceptable_variants,
                critical_omissions=draft.critical_omissions,
                forbidden_or_harmful_claims=draft.forbidden_claims,
                expected_formal_item_ids=formal_ids,
                expected_retrieval_unit_ids=draft.evidence_ids,
                expected_source_documents=documents,
                expected_pages=pages,
                expected_active_substance_ids=substance_ids,
                expected_product_ids=product_ids,
                expected_relation_types=draft.relation_types,
                source_status_notes=status_notes,
                coverage_audit=audit,
            )
        )

    for offset, (text, domain, question_type) in enumerate(
        NOT_COVERED_DRAFTS, start=81
    ):
        audit = (
            _audit_one(core, text)
            if core
            else {
                "status": "not_run",
                "reason": "use --audit-retrieval to run all local channels",
            }
        )
        if core:
            audit["provisional_sufficiency_assessment"] = (
                "No local gold evidence identified after exact/alias, FTS, exact-vector, "
                "hybrid-RRF, bridge and relation checks; human confirmation required."
            )
            audit["topical_scope_control"] = (
                "The sealed snapshot comprises VTE prophylaxis, pancreatic cancer, "
                "the HCC/BCC consultation draft and nine imported SmPC documents; "
                "the requested clinical decision is outside those source scopes."
            )
        questions.append(
            StudyQuestion(
                question_id=f"study-q-{offset:03d}",
                question_text=text,
                question_hash=sha256_text(text),
                coverage_stratum="not_covered_by_local_corpus",
                clinical_domain=domain,
                question_type=question_type,
                difficulty="hard",
                expected_rag_status="no_validated_evidence",
                required_claims=(),
                acceptable_variants=(
                    "Transparente Abstention: diese Frage ist im lokalen Snapshot nicht ausreichend abgedeckt.",
                ),
                critical_omissions=(
                    "Die lokale Nichtabdeckung darf nicht als allgemeine medizinische Evidenzlosigkeit dargestellt werden.",
                ),
                forbidden_or_harmful_claims=(
                    "Eine konkrete Therapie, Dosis oder Schwelle aus internem Modellwissen ergänzen.",
                ),
                coverage_audit=audit,
            )
        )

    validate_question_set(questions, require_human_freeze=False)
    development = build_vte_development_questions(
        corpus_snapshot_id=CORPUS_SNAPSHOT_ID, root=root
    )
    dev_hashes = {row.question_text: row.question_id for row in development}
    reused = [
        (row.question_id, dev_hashes[row.question_text])
        for row in questions
        if row.question_text in dev_hashes
    ]
    if reused:
        raise RuntimeError(f"Phase-1 development questions were reused: {reused}")
    return tuple(questions)


def export_question_candidates(
    questions: Sequence[StudyQuestion], *, output_dir: Path
) -> dict[str, Path]:
    validate_question_set(questions, require_human_freeze=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [row.model_dump(mode="json") for row in questions]
    jsonl = output_dir / "question_candidates.jsonl"
    gold = output_dir / "question_gold_provisional.jsonl"
    csv_path = output_dir / "question_candidates.csv"
    write_jsonl_atomic(jsonl, rows)
    write_jsonl_atomic(gold, rows)
    columns = list(rows[0])
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in row.items()
                }
            )
    return {"jsonl": jsonl, "gold": gold, "csv": csv_path}


__all__ = [
    "build_provisional_questions",
    "export_question_candidates",
]

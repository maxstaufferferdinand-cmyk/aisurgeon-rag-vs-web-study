#!/usr/bin/env python3
"""Freeze the unchanged 100-question set on explicit study-owner approval."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from aisurgeon_decentralised.study_analysis import build_artifact_hash_manifest
from aisurgeon_decentralised.study_exports import (
    build_study_workbooks,
    export_planned_results,
    validate_study_workbooks,
)
from aisurgeon_decentralised.study_model_verification import (
    require_recent_model_verification,
)
from aisurgeon_decentralised.study_phase2 import (
    PROTOCOL_VERSION,
    STUDY_MAX_ESTIMATED_API_COST_USD,
    STUDY_OWNER_APPROVED_QUESTIONS_SHA256,
    SUPERSEDED_STUDY_COST_CEILING_USD,
    StudyQuestion,
    build_randomization_manifest,
    read_jsonl,
    sha256_file,
    utc_now,
    validate_question_set,
    write_jsonl_atomic,
)

APPROVAL_BASIS = "study_owner_pre_freeze_approval"
FREEZE_METADATA_FIELDS = {
    "confirmed_coverage_status",
    "confirmed_critical_errors",
    "confirmed_gold_sources_or_abstention",
    "confirmed_required_claims",
    "freeze_timestamp",
    "gold_standard_version",
    "human_review_status",
}


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _assert_substantive_fields_unchanged(
    source: StudyQuestion, frozen: StudyQuestion
) -> None:
    before = source.model_dump(mode="json")
    after = frozen.model_dump(mode="json")
    for field in FREEZE_METADATA_FIELDS:
        before.pop(field, None)
        after.pop(field, None)
    if before != after:
        raise RuntimeError(
            f"freeze changed substantive question/gold content: {source.question_id}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--approval-basis",
        choices=(APPROVAL_BASIS,),
        required=True,
        help="Explicit non-independent study-owner approval recorded by PD-001.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    base = root / "outputs/study_phase2"
    model_verification = require_recent_model_verification(root=root)

    candidate_path = base / "questions/question_candidates.jsonl"
    provisional_path = base / "questions/question_gold_provisional.jsonl"
    for path in (candidate_path, provisional_path):
        if sha256_file(path) != STUDY_OWNER_APPROVED_QUESTIONS_SHA256:
            raise RuntimeError(
                f"study-owner-approved question input changed before freeze: {path}"
            )
    candidate_rows = read_jsonl(candidate_path)
    provisional_rows = read_jsonl(provisional_path)
    if candidate_rows != provisional_rows:
        raise RuntimeError("candidate and provisional-gold records differ")
    provisional = tuple(StudyQuestion.model_validate(row) for row in provisional_rows)
    validate_question_set(provisional, require_human_freeze=False)

    approval_path = base / "questions/study_owner_pre_freeze_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if (
        approval.get("approval_type") != APPROVAL_BASIS
        or approval.get("question_candidates_sha256")
        != STUDY_OWNER_APPROVED_QUESTIONS_SHA256
        or approval.get("question_gold_provisional_sha256")
        != STUDY_OWNER_APPROVED_QUESTIONS_SHA256
        or approval.get("independent_question_set_review_completed") is not False
    ):
        raise RuntimeError("study-owner approval record is missing or inconsistent")

    freeze_timestamp = utc_now()
    frozen = tuple(
        source.model_copy(
            update={
                "human_review_status": APPROVAL_BASIS,
                "confirmed_coverage_status": True,
                "confirmed_required_claims": True,
                "confirmed_critical_errors": True,
                "confirmed_gold_sources_or_abstention": True,
                "freeze_timestamp": freeze_timestamp,
                "gold_standard_version": "study-owner-prefreeze-frozen-1",
            }
        )
        for source in provisional
    )
    for source, frozen_row in zip(provisional, frozen, strict=True):
        _assert_substantive_fields_unchanged(source, frozen_row)
    validate_question_set(frozen, require_human_freeze=True)

    output = base / "questions/study_questions_frozen.jsonl"
    write_jsonl_atomic(output, [row.model_dump(mode="json") for row in frozen])
    randomization = build_randomization_manifest(frozen, frozen=True)
    randomization_path = base / "manifest/randomization_manifest.csv"
    randomization_rows = [row.model_dump(mode="json") for row in randomization]
    temporary_randomization = randomization_path.with_suffix(".csv.tmp")
    with temporary_randomization.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(randomization_rows[0]))
        writer.writeheader()
        writer.writerows(randomization_rows)
    temporary_randomization.replace(randomization_path)

    manifest_path = base / "manifest/study_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    amendment_path = base / "manifest/protocol_deviations.json"
    amendments = json.loads(amendment_path.read_text(encoding="utf-8"))
    deviation_ids = {row.get("deviation_id") for row in amendments["deviations"]}
    if deviation_ids != {"PD-001", "PD-002"}:
        raise RuntimeError("required pre-freeze protocol amendments are incomplete")

    model_verification_path = (
        base / "manifest/model_availability_verification.json"
    )
    review_path = base / "questions/question_freeze_review.xlsx"
    frozen_input_paths = (
        root / "docs/STUDY_PROTOCOL_RAG_VS_WEB.md",
        root / "docs/STATISTICAL_ANALYSIS_PLAN_RAG_VS_WEB.md",
        base / "prompts/COMMON_TASK_v1.txt",
        base / "prompts/SOURCE_POLICY_WEB_v1.txt",
        base / "prompts/SOURCE_POLICY_RAG_v1.txt",
        base / "prompts/RESPONSE_SCHEMA_v1.json",
        base / "manifest/retrieval_config.json",
        base / "manifest/web_search_config.json",
        base / "manifest/price_table.json",
        amendment_path,
        approval_path,
        candidate_path,
        provisional_path,
    )
    manifest.update(
        {
            "protocol_version": PROTOCOL_VERSION,
            "status": "STUDY_OWNER_QUESTION_FREEZE_COMPLETE",
            "main_study_calls_allowed": True,
            "cost_ceiling_usd": STUDY_MAX_ESTIMATED_API_COST_USD,
            "superseded_cost_ceiling_usd": SUPERSEDED_STUDY_COST_CEILING_USD,
            "cost_counters_reset": False,
            "question_freeze_hash": sha256_file(output),
            "gold_freeze_hash": sha256_file(output),
            "approved_candidate_input_sha256": sha256_file(candidate_path),
            "approved_provisional_gold_input_sha256": sha256_file(
                provisional_path
            ),
            "study_owner_pre_freeze_approval_sha256": sha256_file(approval_path),
            "question_freeze_review_sha256": sha256_file(review_path),
            "question_review_workbook_sha256": sha256_file(review_path),
            "question_review_workbook_used_for_approval": False,
            "reviewer_names_or_signatures_recorded": False,
            "independent_question_set_review_completed": False,
            "later_independent_blinded_answer_review_required": True,
            "randomization_manifest_sha256": sha256_file(randomization_path),
            "final_freeze_at_utc": freeze_timestamp,
            "freeze_approval_basis": APPROVAL_BASIS,
            "model_availability_verification_sha256": sha256_file(
                model_verification_path
            ),
            "model_availability_verified_at_utc": model_verification[
                "verified_at_utc"
            ],
            "protocol_deviations": amendments["deviations"],
            "pre_run_freeze_hashes": {
                str(path.relative_to(root)): sha256_file(path)
                for path in frozen_input_paths
            },
            "questions": {
                **manifest["questions"],
                "human_review_status": APPROVAL_BASIS,
                "independent_clinical_validation": False,
                "candidate_jsonl_sha256": sha256_file(candidate_path),
                "provisional_gold_sha256": sha256_file(provisional_path),
                "frozen_jsonl_sha256": sha256_file(output),
            },
        }
    )
    _write_json(manifest_path, manifest)
    frozen_manifest_path = (
        base / "manifest/study_manifest_at_freeze_v2_500usd.json"
    )
    _write_json(frozen_manifest_path, manifest)

    export_planned_results(root=root, questions=frozen)
    build_study_workbooks(root=root, questions=frozen)
    excel_qa = validate_study_workbooks(root=root)
    _write_json(base / "qa/excel_integrity.json", excel_qa)

    freeze_hash_paths = [
        *frozen_input_paths,
        output,
        randomization_path,
        model_verification_path,
        frozen_manifest_path,
        review_path,
    ]
    freeze_hashes = build_artifact_hash_manifest(
        root=root, paths=freeze_hash_paths
    )
    freeze_hashes.update(
        {
            "freeze_scope": "study_owner_pre_freeze_approval; cost ceiling 500 USD",
            "approval_basis": APPROVAL_BASIS,
            "independent_question_set_review_completed": False,
            "supersedes": (
                "outputs/study_phase2/manifest/"
                "artifact_hashes_pre_human_freeze.json"
            ),
        }
    )
    _write_json(
        base / "manifest/artifact_hashes_study_owner_freeze_v2_500usd.json",
        freeze_hashes,
    )
    print(
        json.dumps(
            {
                "status": "STUDY_OWNER_QUESTION_FREEZE_COMPLETE",
                "approval_basis": APPROVAL_BASIS,
                "independent_question_set_review_completed": False,
                "questions": len(frozen),
                "planned_cells": len(randomization),
                "cost_ceiling_usd": STUDY_MAX_ESTIMATED_API_COST_USD,
                "sha256": sha256_file(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

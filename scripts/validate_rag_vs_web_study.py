#!/usr/bin/env python3
"""Validate Phase-2 design, files, policy gates and Excel integrity."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from aisurgeon_decentralised.study_exports import validate_study_workbooks
from aisurgeon_decentralised.study_phase2 import (
    MODEL_CONFIGURATIONS,
    PRIMARY_RESULT_COUNT,
    PROTOCOL_VERSION,
    STUDY_MAX_ESTIMATED_API_COST_USD,
    STUDY_OWNER_APPROVED_QUESTIONS_SHA256,
    StudyQuestion,
    build_randomization_manifest,
    read_jsonl,
    sha256_file,
    validate_question_set,
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    question_path = root / "outputs/study_phase2/questions/question_candidates.jsonl"
    questions = tuple(
        StudyQuestion.model_validate(row) for row in read_jsonl(question_path)
    )
    validate_question_set(questions, require_human_freeze=False)
    cells = build_randomization_manifest(questions, frozen=False)
    base = root / "outputs/study_phase2"
    pilot_results = read_jsonl(
        base / "pilot/development_cost_pilot_results.jsonl"
    )
    pilot_attempts = read_jsonl(
        base / "pilot/development_cost_pilot_attempts.jsonl"
    )
    pilot_summary = json.loads(
        (base / "pilot/development_cost_pilot_summary.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads(
        (base / "manifest/study_manifest.json").read_text(encoding="utf-8")
    )
    price_table = json.loads(
        (base / "manifest/price_table.json").read_text(encoding="utf-8")
    )
    owner_approval = json.loads(
        (base / "questions/study_owner_pre_freeze_approval.json").read_text(
            encoding="utf-8"
        )
    )
    amendments = json.loads(
        (base / "manifest/protocol_deviations.json").read_text(encoding="utf-8")
    )
    model_verification_path = (
        base / "manifest/model_availability_verification.json"
    )
    model_verification = json.loads(
        model_verification_path.read_text(encoding="utf-8")
    )
    with (base / "manifest/REFINE_compliance.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        refine = list(csv.DictReader(handle))
    with (base / "manifest/MI_CLEAR_LLM_compliance.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        mi_clear = list(csv.DictReader(handle))
    bridge = read_jsonl(
        root / "outputs/retrieval_phase/bridges/smpc_guideline_bridge.jsonl"
    )
    development = {
        row["question_text"]
        for row in read_jsonl(
            root / "outputs/retrieval_phase/vte_development/vte_questions.jsonl"
        )
    }
    required_audit_checks = {
        "exact_search_executed",
        "normalized_alias_search_executed",
        "fts_executed",
        "vector_search_executed",
        "hybrid_rrf_executed",
        "bridge_checked",
        "formal_items_checked",
        "eligible_rationales_checked_via_relation_expansion",
    }
    external_secret_markers = ("sk-proj-", "sk-svcacct-", "sk-admin-")
    secret_paths = []
    for path in base.rglob("*"):
        if path.suffix.casefold() not in {
            ".json",
            ".jsonl",
            ".csv",
            ".md",
            ".txt",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(marker in text for marker in external_secret_markers):
            secret_paths.append(str(path.relative_to(root)))
    frozen_path = base / "questions/study_questions_frozen.jsonl"
    frozen_rows = read_jsonl(frozen_path)
    main_rows = read_jsonl(base / "results/study_results.jsonl")
    freeze_metadata = {
        "confirmed_coverage_status",
        "confirmed_critical_errors",
        "confirmed_gold_sources_or_abstention",
        "confirmed_required_claims",
        "freeze_timestamp",
        "gold_standard_version",
        "human_review_status",
    }
    substantive_freeze_match = len(frozen_rows) == len(read_jsonl(question_path))
    if substantive_freeze_match:
        for source, frozen in zip(read_jsonl(question_path), frozen_rows, strict=True):
            source_payload = {
                key: value for key, value in source.items() if key not in freeze_metadata
            }
            frozen_payload = {
                key: value for key, value in frozen.items() if key not in freeze_metadata
            }
            if source_payload != frozen_payload:
                substantive_freeze_match = False
                break
    checks = {
        "questions_exactly_100": len(questions) == 100,
        "covered_exactly_80": sum(
            q.coverage_stratum == "covered_by_local_corpus" for q in questions
        )
        == 80,
        "not_covered_exactly_20": sum(
            q.coverage_stratum == "not_covered_by_local_corpus" for q in questions
        )
        == 20,
        "two_model_configurations": len(MODEL_CONFIGURATIONS) == 2,
        "planned_results_exactly_800": len(cells) == PRIMARY_RESULT_COUNT,
        "unique_run_ids": len({cell.run_id for cell in cells}) == PRIMARY_RESULT_COUNT,
        "two_systems": {cell.system_arm for cell in cells} == {"WEB", "RAG"},
        "two_runs": {cell.repetition for cell in cells}
        == {"1_primary", "2_reproducibility"},
        "no_hcc_history_expected_gold": all(
            all(
                "hcc_historical" not in evidence_id
                for evidence_id in q.expected_retrieval_unit_ids
            )
            for q in questions
        ),
        "development_questions_not_reused": not {
            question.question_text for question in questions
        }.intersection(development),
        "all_coverage_audits_executed": all(
            required_audit_checks.issubset(
                (question.coverage_audit.get("required_method_checks") or {}).keys()
            )
            and all(
                (question.coverage_audit.get("required_method_checks") or {}).get(
                    check
                )
                is True
                for check in required_audit_checks
            )
            for question in questions
        ),
        "pilot_exactly_20_results": len(pilot_results) == 20,
        "pilot_exactly_20_attempts": len(pilot_attempts) == 20,
        "pilot_unique_run_ids": len({row["run_id"] for row in pilot_results}) == 20,
        "pilot_unique_attempt_ids": len(
            {row["attempt_id"] for row in pilot_attempts}
        )
        == 20,
        "pilot_http_200": all(row.get("http_status") == 200 for row in pilot_attempts),
        "pilot_no_retries": all(
            int(row.get("retry_number") or 0) == 0 for row in pilot_attempts
        ),
        "pilot_complete_request_telemetry": all(
            row.get("response_id")
            and row.get("x_request_id")
            and row.get("client_request_id")
            and row.get("time_to_first_token_ms") is not None
            and row.get("openai_processing_ms") is not None
            and row.get("local_resources_before")
            and row.get("local_resources_after")
            for row in pilot_attempts
        ),
        "pilot_returned_models_match_requested": all(
            row.get("requested_model") == row.get("returned_model")
            for row in pilot_attempts
        ),
        "pilot_cost_projection_within_500": (
            float(pilot_summary["conservative_total_projection_usd"])
            <= STUDY_MAX_ESTIMATED_API_COST_USD
            and float(pilot_summary["cost_limit_usd"])
            == STUDY_MAX_ESTIMATED_API_COST_USD
        ),
        "active_cost_gate_is_exactly_500": (
            STUDY_MAX_ESTIMATED_API_COST_USD == 500.0
            and float(manifest.get("cost_ceiling_usd") or 0) == 500.0
            and float(price_table.get("study_cost_ceiling_usd") or 0) == 500.0
            and float(pilot_summary.get("cost_limit_usd") or 0) == 500.0
            and manifest.get("cost_counters_reset") is False
        ),
        "former_400_gate_is_superseded_not_active": (
            float(manifest.get("superseded_cost_ceiling_usd") or 0) == 400.0
            and float(price_table.get("supersedes_study_cost_ceiling_usd") or 0)
            == 400.0
            and manifest.get("cost_ceiling_usd") != 400.0
        ),
        "protocol_version_is_owner_amendment": (
            PROTOCOL_VERSION == "rag-vs-web-1.1.0"
            and manifest.get("protocol_version") == PROTOCOL_VERSION
        ),
        "pilot_projection_includes_all_three_attempts": (
            "three permitted attempts" in pilot_summary["projection_method"]
        ),
        "official_model_availability_verified": (
            model_verification.get("status") == "verified"
            and model_verification.get("detected_gpt55_dated_snapshots")
            == ["gpt-5.5-2026-04-23"]
            and model_verification.get("detected_gpt56_sol_dated_snapshots") == []
            and model_verification.get("official_sources_only") is True
        ),
        "pilot_provenance_validation_terminal": all(
            row.get("validator_status") in {"accepted", "downgraded", "rejected"}
            for row in pilot_results
        ),
        "refine_44_items": len(refine) == 44,
        "mi_clear_all_eight_categories": len(
            {row["category"].split(" ", maxsplit=1)[0] for row in mi_clear}
        )
        == 8,
        "owner_approved_input_hash_exact": (
            sha256_file(question_path) == STUDY_OWNER_APPROVED_QUESTIONS_SHA256
            and sha256_file(
                base / "questions/question_gold_provisional.jsonl"
            )
            == STUDY_OWNER_APPROVED_QUESTIONS_SHA256
        ),
        "owner_approval_truthfully_scoped": (
            owner_approval.get("approval_type")
            == "study_owner_pre_freeze_approval"
            and owner_approval.get("independent_question_set_review_completed")
            is False
            and owner_approval.get("reviewer_name_recorded") is False
            and owner_approval.get("review_workbook", {}).get(
                "reviewer_and_adjudication_fields_intentionally_blank"
            )
            is True
        ),
        "prefreeze_amendments_recorded": (
            {row.get("deviation_id") for row in amendments.get("deviations", [])}
            == {"PD-001", "PD-002"}
        ),
        "directed_bridge_only": all(
            row.get("direction") == "smPC_to_guideline"
            for row in bridge
            if row.get("bridge_active") is True
        ),
        "bridge_unmatched_not_error_preserved": sum(
            row.get("review_status") == "unmatched_no_error" for row in bridge
        )
        == 1,
        "no_secret_markers_in_study_outputs": not secret_paths,
        "study_owner_freeze_gate_consistent": (
            len(frozen_rows) == 100
            and substantive_freeze_match
            and all(
                row.get("human_review_status")
                == "study_owner_pre_freeze_approval"
                and row.get("confirmed_coverage_status") is True
                and row.get("confirmed_required_claims") is True
                and row.get("confirmed_critical_errors") is True
                and row.get("confirmed_gold_sources_or_abstention") is True
                for row in frozen_rows
            )
        ),
        "main_study_state_consistent": (
            not main_rows
            or (
                len(main_rows) == PRIMARY_RESULT_COUNT
                and len({row["run_id"] for row in main_rows})
                == PRIMARY_RESULT_COUNT
                and all(row.get("status") in {"complete", "failed"} for row in main_rows)
            )
        ),
    }
    if not all(checks.values()):
        raise AssertionError({key: value for key, value in checks.items() if not value})
    excel = validate_study_workbooks(root=root)
    report = {
        "status": "passed",
        "checks": checks,
        "excel": excel,
        "human_freeze_present": (
            frozen_path
        ).is_file(),
        "main_results_present": bool(main_rows),
        "pilot": {
            "results": len(pilot_results),
            "attempts": len(pilot_attempts),
            "validator_status": {
                status: sum(row.get("validator_status") == status for row in pilot_results)
                for status in ("accepted", "downgraded", "rejected")
            },
            "conservative_total_projection_usd": pilot_summary[
                "conservative_total_projection_usd"
            ],
            "cost_limit_usd": STUDY_MAX_ESTIMATED_API_COST_USD,
        },
        "model_availability_verification": {
            "status": model_verification["status"],
            "verified_at_utc": model_verification["verified_at_utc"],
        },
        "secret_paths": secret_paths,
    }
    path = root / "outputs/study_phase2/qa/phase2_validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

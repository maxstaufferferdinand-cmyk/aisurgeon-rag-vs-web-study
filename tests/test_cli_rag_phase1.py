from __future__ import annotations

import json
from pathlib import Path

from aisurgeon_decentralised.vte_development import build_vte_development_questions

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ID = "cs-f61b3d4e90089c1b890c23cb"
OUTPUT = ROOT / "outputs/retrieval_phase/vte_development"


def _jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_vte_development_set_is_stratified_synthetic_and_source_resolved() -> None:
    questions = build_vte_development_questions(
        corpus_snapshot_id=SNAPSHOT_ID, root=ROOT
    )
    assert len(questions) == 20
    assert len({question.question_text for question in questions}) == 20
    assert sum(question.expected_no_evidence for question in questions) == 3
    assert sum(len(question.expected_evidence_ids) > 1 for question in questions) == 2
    assert all(
        question.label_origin == "synthetic_draft_source_derived"
        for question in questions
    )


def test_retrieval_benchmark_is_deterministic_and_bridge_finds_product_items() -> None:
    qa = json.loads((OUTPUT / "determinism_qa.json").read_text(encoding="utf-8"))
    assert qa["total_runs"] == qa["identical_runs"] == 80
    rows = _jsonl(OUTPUT / "retrieval_evaluation.jsonl")
    bridge_product = {
        row["question_id"]: row
        for row in rows
        if row["retrieval_mode"] == "hybrid_rrf_bridge"
        and row["question_id"] in {"vte-dev-005", "vte-dev-006"}
    }
    assert set(bridge_product) == {"vte-dev-005", "vte-dev-006"}
    assert all(row["found_item_numbers"][0] == "8.1" for row in bridge_product.values())


def test_responses_are_allowlisted_and_baseline_is_not_publishable() -> None:
    rows = _jsonl(OUTPUT / "response_runs_validated.jsonl")
    assert len(rows) == 40
    closed = [row for row in rows if row["arm"] == "closed_corpus_rag"]
    baseline = [row for row in rows if row["arm"] == "no_retrieval_context"]
    assert len(closed) == len(baseline) == 20
    assert all(row["validated_answer"]["publishable"] for row in closed)
    assert not any(row["validated_answer"]["publishable"] for row in baseline)
    for row in closed:
        allowlist = set(row["evidence_allowlist"])
        assert all(
            citation["evidence_id"] in allowlist
            for citation in row["validated_answer"]["citations"]
        )


def test_phase_completion_validator_passed_all_checks() -> None:
    qa = json.loads(
        (
            ROOT / "outputs/retrieval_phase/qa/cli_rag_phase1_completion.json"
        ).read_text(encoding="utf-8")
    )
    assert qa["passed"] is True
    assert qa["passed_check_count"] == qa["check_count"] == 31
    assert qa["source_pdf_mutations"] == []
    assert qa["canonical_mutations"] == []
    assert qa["gemini_calls_this_phase"] == 0

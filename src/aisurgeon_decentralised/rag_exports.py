"""Deterministic study exports for closed RAG runs."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .rag_core import RagRunResult


def json_export_row(
    result: RagRunResult,
    *,
    question: str | None = None,
    include_question: bool = False,
) -> dict[str, Any]:
    row = result.model_dump(mode="json")
    if include_question:
        if question is None:
            raise ValueError("include_question requires an explicit question")
        row["question"] = question
        row["question_logging_basis"] = "explicit_study_export_opt_in"
    return row


def csv_export_row(result: RagRunResult) -> dict[str, Any]:
    retrieval = result.retrieval
    answer = result.validated_answer
    return {
        "run_id": result.run_id,
        "question_id": result.question_id,
        "arm": result.arm,
        "corpus_snapshot_id": result.corpus_snapshot_id,
        "retrieval_mode": retrieval.retrieval_mode if retrieval else "none",
        "routing_mode": retrieval.routing_mode if retrieval else "none",
        "retrieval_outcome": retrieval.retrieval_outcome if retrieval else "not_run",
        "evidence_ids": json.dumps(result.evidence_allowlist, ensure_ascii=False),
        "guideline_item_numbers": json.dumps(
            [hit.source_native_item_number for hit in retrieval.guideline_item_ranking]
            if retrieval
            else [],
            ensure_ascii=False,
        ),
        "answer_status": answer.answer_status if answer else "",
        "validator_status": answer.validator_status if answer else "not_run",
        "validator_issue_codes": json.dumps(
            answer.validator_issue_codes if answer else (), ensure_ascii=False
        ),
        "citation_evidence_ids": json.dumps(
            [citation.evidence_id for citation in answer.citations] if answer else [],
            ensure_ascii=False,
        ),
        "input_tokens": result.telemetry.token_usage.input_tokens,
        "output_tokens": result.telemetry.token_usage.output_tokens,
        "cached_tokens": result.telemetry.token_usage.cached_tokens,
        "reasoning_tokens": result.telemetry.token_usage.reasoning_tokens,
        "embedding_tokens": result.telemetry.token_usage.embedding_tokens,
        "api_wall_time_ms": result.telemetry.api_wall_time_ms,
        "retrieval_time_ms": result.telemetry.retrieval_time_ms,
        "estimated_cost_usd": result.telemetry.cost.estimated_cost_usd,
        "x_request_id": result.telemetry.x_request_id,
        "dry_run": result.dry_run,
    }


def write_jsonl(
    rows: Sequence[dict[str, Any]], *, path: Path, append: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )


def write_csv(
    rows: Sequence[dict[str, Any]], *, path: Path, append: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.exists() and path.stat().st_size > 0
    mode = "a" if append else "w"
    fieldnames = list(rows[0]) if rows else []
    with path.open(mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames and (not append or not existing):
            writer.writeheader()
        writer.writerows(rows)


__all__ = [
    "csv_export_row",
    "json_export_row",
    "write_csv",
    "write_jsonl",
]

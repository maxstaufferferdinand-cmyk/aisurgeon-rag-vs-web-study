"""Deterministic post-processing for already completed study calls.

This module never calls OpenAI.  It rebuilds only local evidence packages and
re-applies the versioned arm-specific provenance validators to persisted raw
structured answers.  It is intentionally separate from generation so a
validator defect can be corrected without replacing a paid study response.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .rag_core import RagCore, RagHit
from .study_phase2 import (
    CORPUS_SNAPSHOT_ID,
    read_jsonl,
    sha256_file,
    utc_now,
    write_jsonl_atomic,
)
from .study_responses import StudyStructuredAnswer
from .study_runner import StudyRunResult
from .study_validators import validate_rag_answer, validate_web_answer


def revalidate_results(
    *, root: Path, results_path: Path, report_path: Path | None = None
) -> dict[str, Any]:
    """Revalidate completed rows in place without any external API usage."""

    root = root.resolve()
    results_path = results_path.resolve()
    rows = read_jsonl(results_path)
    attempts_path = Path(
        str(results_path).replace("_results.jsonl", "_attempts.jsonl")
    )
    if results_path.name == "study_results.jsonl":
        attempts_path = results_path.with_name("api_attempts.jsonl")
    attempts_by_run: dict[str, list[dict[str, Any]]] = {}
    for attempt in read_jsonl(attempts_path):
        attempts_by_run.setdefault(str(attempt["run_id"]), []).append(attempt)
    before_hash = sha256_file(results_path)
    before_status = Counter(str(row.get("validator_status")) for row in rows)
    core = RagCore(root=root, corpus_snapshot_id=CORPUS_SNAPSHOT_ID)
    validated_rows: list[dict[str, Any]] = []
    changed = 0
    for raw_row in rows:
        row = StudyRunResult.model_validate(raw_row)
        if row.status != "complete" or row.raw_model_answer is None:
            validated_rows.append(row.model_dump(mode="json"))
            continue
        answer = StudyStructuredAnswer.model_validate(row.raw_model_answer)
        if row.system_arm == "RAG":
            package, _ = core.build_evidence_package(row.evidence_allowlist)
            retrieval_hits = tuple(
                RagHit.model_validate(hit)
                for hit in (row.retrieval or {}).get("hits", ())
            )
            validated = validate_rag_answer(
                answer,
                package=package,
                retrieval_hits=retrieval_hits,
            )
        else:
            validated = validate_web_answer(
                answer,
                consulted_sources=row.web_sources_consulted,
                cited_sources=row.web_sources_cited,
            )
        update: dict[str, Any] = {
                "validated_system_answer": validated.model_dump(mode="json"),
                "backend_rendered_sources": validated.rendered_sources,
                "validator_status": validated.validator_status,
                "validator_issue_codes": validated.issue_codes,
        }
        if not row.local_resources and attempts_by_run.get(row.run_id):
            ordered_attempts = sorted(
                attempts_by_run[row.run_id],
                key=lambda item: int(item.get("attempt_number") or 0),
            )
            update["local_resources"] = {
                "scope_note": (
                    "legacy pilot: resources bracket the Responses call; "
                    "pre-retrieval capture was not yet available"
                ),
                "before_api": ordered_attempts[0].get("local_resources_before"),
                "after_api": ordered_attempts[-1].get("local_resources_after"),
            }
        updated = row.model_copy(update=update)
        updated_payload = updated.model_dump(mode="json")
        if updated_payload != raw_row:
            changed += 1
        validated_rows.append(updated_payload)

    write_jsonl_atomic(results_path, validated_rows)
    after_status = Counter(
        str(row.get("validator_status")) for row in validated_rows
    )
    report = {
        "schema_version": "study-deterministic-revalidation-1.0.0",
        "created_at_utc": utc_now(),
        "results_path": str(results_path.relative_to(root)),
        "external_api_calls": 0,
        "rows": len(rows),
        "rows_changed": changed,
        "before_sha256": before_hash,
        "after_sha256": sha256_file(results_path),
        "before_validator_status": dict(sorted(before_status.items())),
        "after_validator_status": dict(sorted(after_status.items())),
    }
    if report_path is not None:
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = report_path.with_suffix(report_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(report_path)
    return report


__all__ = ["revalidate_results"]

#!/usr/bin/env python3
"""Run the reproducible 20-question VTE development benchmark."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from aisurgeon_decentralised.rag_core import (
    RagCore,
    RagRunResult,
    RetrievalMode,
    validate_structured_answer,
)
from aisurgeon_decentralised.rag_exports import csv_export_row, write_csv, write_jsonl
from aisurgeon_decentralised.rag_responses import (
    ClosedResponsesClient,
    ClosedResponsesConfig,
    build_closed_request_text,
    closed_answer_instructions,
    estimate_response_cost,
    no_context_baseline_instructions,
)
from aisurgeon_decentralised.rag_telemetry import RagTokenUsage
from aisurgeon_decentralised.retrieval_config import (
    EMBEDDING_MODEL,
    EMBEDDING_PRICE_AS_OF,
    EMBEDDING_PRICE_USD_PER_MILLION_TOKENS,
)
from aisurgeon_decentralised.vte_development import (
    aggregate_retrieval_metrics,
    build_vte_development_questions,
    evaluate_retrieval_result,
    write_vte_question_package,
)

MODES = tuple(RetrievalMode)
SMOKE_QUESTION_IDS = ("vte-dev-001", "vte-dev-005", "vte-dev-018")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate FTS, exact vector, Hybrid-RRF and Hybrid-RRF+bridge; "
            "then optionally compare closed RAG with a no-context API arm."
        )
    )
    parser.add_argument("--snapshot-id")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/retrieval_phase/vte_development"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="perform no OpenAI call (including no new query embeddings)",
    )
    parser.add_argument("--skip-responses", action="store_true")
    parser.add_argument("--max-additional-cost-usd", type=float, default=2.0)
    return parser


def _write_retrieval_outputs(
    output_dir: Path,
    run_rows: list[dict[str, Any]],
    evaluation_rows: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> None:
    write_jsonl(run_rows, path=output_dir / "retrieval_runs.jsonl")
    write_jsonl(evaluation_rows, path=output_dir / "retrieval_evaluation.jsonl")
    with (output_dir / "retrieval_evaluation.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(evaluation_rows[0]))
        writer.writeheader()
        for row in evaluation_rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, list)
                    else value
                    for key, value in row.items()
                }
            )
    (output_dir / "retrieval_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _response_checkpoint(
    path: Path, *, core: RagCore
) -> dict[tuple[str, str], RagRunResult]:
    if not path.exists():
        return {}
    result: dict[tuple[str, str], RagRunResult] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = RagRunResult.model_validate_json(line)
        if row.model_answer is not None:
            package, catalog = core.build_evidence_package(row.evidence_allowlist)
            retrieval = row.retrieval
            validated = validate_structured_answer(
                row.model_answer,
                package=package,
                evidence_catalog=catalog,
                retrieval_outcome=(
                    retrieval.retrieval_outcome if retrieval else "retrieval_failure"
                ),
                retrieval_fallback_complete=(
                    retrieval.retrieval_fallback_complete if retrieval else False
                ),
                baseline_without_retrieval=row.arm == "no_retrieval_context",
            )
            row = row.model_copy(update={"validated_answer": validated})
        result[(row.question_id, row.arm)] = row
    return result


def _append_response(path: Path, result: RagRunResult) -> None:
    write_jsonl(
        [result.model_dump(mode="json")],
        path=path,
        append=path.exists() and path.stat().st_size > 0,
    )


def _max_response_cost(
    *,
    core: RagCore,
    config: ClosedResponsesConfig,
    questions: tuple[Any, ...],
    retrieval_by_key: dict[tuple[str, RetrievalMode], Any],
    pending: set[tuple[str, str]],
) -> tuple[float | None, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    total = 0.0
    for question in questions:
        for arm in ("closed_corpus_rag", "no_retrieval_context"):
            key = (question.question_id, arm)
            if key not in pending:
                continue
            if arm == "closed_corpus_rag":
                retrieval = retrieval_by_key[
                    (question.question_id, RetrievalMode.HYBRID_RRF_BRIDGE)
                ]
                package, _ = core.build_evidence_package(retrieval.evidence_ids)
                instructions = closed_answer_instructions()
            else:
                package, _ = core.build_evidence_package(())
                instructions = no_context_baseline_instructions()
            request = build_closed_request_text(question.question_text, package)
            # UTF-8 bytes are a conservative upper bound on BPE token count:
            # each token consumes at least one input byte.
            input_upper_bound = len((instructions + request).encode("utf-8"))
            usage = RagTokenUsage(
                input_tokens=input_upper_bound,
                output_tokens=config.max_output_tokens,
                total_tokens=input_upper_bound + config.max_output_tokens,
            )
            estimate = estimate_response_cost(config.model, usage)
            amount = estimate.estimated_cost_usd
            rows.append(
                {
                    "question_id": question.question_id,
                    "arm": arm,
                    "input_token_upper_bound": input_upper_bound,
                    "max_output_tokens": config.max_output_tokens,
                    "max_cost_usd": amount,
                }
            )
            if amount is None:
                return None, rows
            total += amount
    return total, rows


def _response_summary(results: list[RagRunResult]) -> dict[str, Any]:
    by_arm: dict[str, list[RagRunResult]] = defaultdict(list)
    for result in results:
        by_arm[result.arm].append(result)
    output: dict[str, Any] = {}
    for arm, rows in sorted(by_arm.items()):
        model_statuses = Counter(
            row.model_answer.answer_status if row.model_answer else "missing" for row in rows
        )
        backend_statuses = Counter(
            (
                row.validated_answer.answer_status.value
                if row.validated_answer and row.validated_answer.answer_status
                else "rejected"
            )
            for row in rows
        )
        citation_valid = 0
        citation_total = 0
        for row in rows:
            if not row.validated_answer:
                continue
            for citation in row.validated_answer.citations:
                citation_total += 1
                citation_valid += int(citation.evidence_id in row.evidence_allowlist)
        costs = [float(row.telemetry.cost.estimated_cost_usd or 0.0) for row in rows]
        latencies = [float(row.telemetry.api_wall_time_ms or 0.0) for row in rows]
        ordered_latencies = sorted(latencies)

        def percentile(fraction: float) -> float:
            if not ordered_latencies:
                return 0.0
            index = min(
                len(ordered_latencies) - 1,
                max(0, int(math.ceil(fraction * len(ordered_latencies))) - 1),
            )
            return ordered_latencies[index]

        output[arm] = {
            "runs": len(rows),
            "model_answer_status_counts": dict(sorted(model_statuses.items())),
            "backend_answer_status_counts": dict(sorted(backend_statuses.items())),
            "backend_publishable": sum(
                bool(row.validated_answer and row.validated_answer.publishable)
                for row in rows
            ),
            "citation_ids_valid": citation_valid,
            "citation_ids_total": citation_total,
            "citation_validity_rate": (
                citation_valid / citation_total if citation_total else None
            ),
            "tokens": {
                "input": sum(row.telemetry.token_usage.input_tokens for row in rows),
                "output": sum(row.telemetry.token_usage.output_tokens for row in rows),
                "cached": sum(row.telemetry.token_usage.cached_tokens for row in rows),
                "reasoning": sum(row.telemetry.token_usage.reasoning_tokens for row in rows),
                "embedding": sum(
                    row.telemetry.token_usage.embedding_tokens for row in rows
                ),
            },
            "api_wall_time_ms": {
                "mean": statistics.fmean(latencies) if latencies else 0.0,
                "p50": statistics.median(latencies) if latencies else 0.0,
                "p95": percentile(0.95),
                "max": max(latencies, default=0.0),
            },
            "http_status_counts": dict(
                sorted(Counter(row.telemetry.http_status for row in rows).items())
            ),
            "retry_count_total": sum(row.telemetry.retry_count for row in rows),
            "x_request_id_present": sum(
                row.telemetry.x_request_id is not None for row in rows
            ),
            "openai_processing_ms_present": sum(
                row.telemetry.openai_processing_ms is not None for row in rows
            ),
            "rate_limit_headers_present": sum(
                bool(row.telemetry.rate_limit_headers) for row in rows
            ),
            "estimated_cost_usd": sum(costs),
        }
    return output


def _query_embedding_summary(
    *, core: RagCore, questions: tuple[Any, ...]
) -> dict[str, Any]:
    directory = (
        core.root
        / "outputs/retrieval_phase"
        / core.corpus_snapshot_id
        / "query_embeddings"
        / EMBEDDING_MODEL
    )
    tokens = 0
    cost = 0.0
    checkpoints = 0
    missing: list[str] = []
    for question in questions:
        digest = hashlib.sha256(question.question_text.encode("utf-8")).hexdigest()
        path = directory / f"{digest}.json.gz"
        if not path.exists():
            missing.append(question.question_id)
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        tokens += int(payload.get("input_tokens") or 0)
        cost += float(payload.get("estimated_cost_usd") or 0.0)
        checkpoints += 1
    return {
        "model": EMBEDDING_MODEL,
        "dimension": 1536,
        "question_count": len(questions),
        "checkpoint_count": checkpoints,
        "input_tokens": tokens,
        "estimated_cost_usd": cost,
        "price_usd_per_million_input_tokens": (
            EMBEDDING_PRICE_USD_PER_MILLION_TOKENS
        ),
        "price_as_of": EMBEDDING_PRICE_AS_OF,
        "missing_question_ids": missing,
    }


def _write_markdown_report(
    *,
    output_dir: Path,
    snapshot_id: str,
    questions: tuple[Any, ...],
    metrics: dict[str, Any],
    evaluations: list[dict[str, Any]],
    deterministic: dict[str, Any],
    cost_gate: dict[str, Any] | None,
    responses: dict[str, Any] | None,
) -> None:
    lines = [
        "# VTE Development-Demonstration",
        "",
        f"Corpus Snapshot: `{snapshot_id}`",
        "",
        "Die 20 Fragen sind source-derived `synthetic_draft`-Development-Daten "
        "und kein finaler unangetasteter klinischer Testdatensatz.",
        "",
        "## Retrievalmetriken",
        "",
        "| Modus | Hit@1 | Recall@3 | Recall@5 | MRR | nDCG@5 | No-evidence korrekt | Mittel ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        row = metrics[mode.value]
        lines.append(
            f"| {mode.value} | {row['hit_at_1']:.3f} | {row['recall_at_3']:.3f} | "
            f"{row['recall_at_5']:.3f} | {row['mrr']:.3f} | {row['ndcg_at_5']:.3f} | "
            f"{row['no_evidence_correct_rate']:.3f} | {row['latency_ms']['mean']:.1f} |"
        )
    lines.extend(
        [
            "",
            "## Erwartete und gefundene Items (Hybrid-RRF plus Bridge)",
            "",
            "| ID | Erwartet | Gefunden (Rangfolge) |",
            "|---|---|---|",
        ]
    )
    hybrid = {
        row["question_id"]: row
        for row in evaluations
        if row["retrieval_mode"] == RetrievalMode.HYBRID_RRF_BRIDGE.value
    }
    for question in questions:
        row = hybrid[question.question_id]
        lines.append(
            f"| {question.question_id} | "
            f"{', '.join(question.expected_item_numbers) or 'no-evidence'} | "
            f"{', '.join(value or 'unnumbered' for value in row['found_item_numbers']) or 'keine'} |"
        )
    lines.extend(
        [
            "",
            "## Determinismus",
            "",
            f"Deterministische Wiederholungen: **{deterministic['identical_runs']}/"
            f"{deterministic['total_runs']} identisch**.",
        ]
    )
    if cost_gate:
        lines.extend(
            [
                "",
                "## Responses-Kostengate",
                "",
                f"Drei-Fragen-Smoke (beide Arme): {cost_gate['smoke_api_calls']} API-Aufrufe.  ",
                f"Konservative maximale Zusatzkostenschätzung: "
                f"${cost_gate['estimated_additional_max_usd']:.6f}; Grenze: "
                f"${cost_gate['cost_limit_usd']:.2f}; Entscheidung: "
                f"`{cost_gate['decision']}`.",
            ]
        )
    if responses:
        lines.extend(["", "## Antwortgenerierung", ""])
        for arm, row in responses.items():
            lines.append(
                f"- `{arm}`: {row['runs']} Runs, Backend-publizierbar "
                f"{row['backend_publishable']}, Kosten ${row['estimated_cost_usd']:.6f}, "
                f"Input/Output-Tokens {row['tokens']['input']}/{row['tokens']['output']}, "
                f"mittlere API-Wall-Time {row['api_wall_time_ms']['mean']:.1f} ms."
            )
        lines.extend(
            [
                "",
                "Der No-context-Arm ist absichtlich nicht als evidenzvalidierte Ausgabe "
                "publizierbar; er dient nur als API-Vergleich. Quellenangaben im "
                "Closed-RAG-Arm werden ausschließlich aus Backend-Lokatoren gerendert.",
            ]
        )
    lines.extend(
        [
            "",
            "## Technische Einordnung",
            "",
            "Die Resultate sind eine technische Development-Demonstration. Automatische "
            "Retrievalerwartungen und technische Claim-Validatoren ersetzen keine "
            "unabhängige klinische Annotation oder Validierung.",
        ]
    )
    (output_dir / "vte_development_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    args = _parser().parse_args()
    if args.max_additional_cost_usd < 0:
        raise SystemExit("--max-additional-cost-usd must be non-negative")
    cost_limit = min(args.max_additional_cost_usd, 2.0)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    response_client = None
    if not args.dry_run and not args.skip_responses:
        response_client = ClosedResponsesClient(config=ClosedResponsesConfig.from_environment())
    core = RagCore(
        corpus_snapshot_id=args.snapshot_id,
        responses_client=response_client,
    )
    questions = build_vte_development_questions(
        corpus_snapshot_id=core.corpus_snapshot_id
    )
    write_vte_question_package(questions, output_dir=output_dir)

    retrieval_by_key: dict[tuple[str, RetrievalMode], Any] = {}
    run_rows: list[dict[str, Any]] = []
    evaluation_rows: list[dict[str, Any]] = []
    grouped: dict[RetrievalMode, list[dict[str, Any]]] = defaultdict(list)
    for mode in MODES:
        for question in questions:
            result = core.retrieve(
                question=question.question_text,
                retrieval_mode=mode,
                routing_mode=question.routing_mode,
                allow_embedding_api=not args.dry_run,
            )
            retrieval_by_key[(question.question_id, mode)] = result
            run_rows.append(
                {
                    "question_id": question.question_id,
                    "question_sha256": result.query_sha256,
                    "synthetic_question_text": question.question_text,
                    "study_text_logging_basis": "public_synthetic_development_question",
                    "result": result.model_dump(mode="json"),
                }
            )
            evaluated = evaluate_retrieval_result(question, result)
            evaluation_rows.append(evaluated)
            grouped[mode].append(evaluated)

    metrics = {
        mode.value: aggregate_retrieval_metrics(grouped[mode]) for mode in MODES
    }
    _write_retrieval_outputs(output_dir, run_rows, evaluation_rows, metrics)
    query_embedding_usage = _query_embedding_summary(core=core, questions=questions)
    (output_dir / "query_embedding_usage.json").write_text(
        json.dumps(query_embedding_usage, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    identical = 0
    total_repeat = 0
    mismatches: list[dict[str, str]] = []
    for mode in MODES:
        for question in questions:
            repeated = core.retrieve(
                question=question.question_text,
                retrieval_mode=mode,
                routing_mode=question.routing_mode,
                allow_embedding_api=False,
            )
            first = retrieval_by_key[(question.question_id, mode)]
            total_repeat += 1
            if (
                repeated.evidence_ids == first.evidence_ids
                and tuple(hit.evidence_id for hit in repeated.guideline_item_ranking)
                == tuple(hit.evidence_id for hit in first.guideline_item_ranking)
            ):
                identical += 1
            else:
                mismatches.append(
                    {"question_id": question.question_id, "retrieval_mode": mode.value}
                )
    deterministic = {
        "total_runs": total_repeat,
        "identical_runs": identical,
        "mismatches": mismatches,
    }
    (output_dir / "determinism_qa.json").write_text(
        json.dumps(deterministic, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    cost_gate: dict[str, Any] | None = None
    response_summary: dict[str, Any] | None = None
    if not args.dry_run and not args.skip_responses:
        response_path = output_dir / "response_runs.jsonl"
        checkpoint = _response_checkpoint(response_path, core=core)
        by_id = {question.question_id: question for question in questions}
        smoke_keys = {
            (question_id, arm)
            for question_id in SMOKE_QUESTION_IDS
            for arm in ("closed_corpus_rag", "no_retrieval_context")
        }
        for question_id, arm in sorted(smoke_keys):
            if (question_id, arm) in checkpoint:
                continue
            question = by_id[question_id]
            result = core.run(
                question=question.question_text,
                question_id=question.question_id,
                retrieval_mode=RetrievalMode.HYBRID_RRF_BRIDGE,
                routing_mode=question.routing_mode,
                baseline_without_retrieval=arm == "no_retrieval_context",
                run_id=f"vte-development-{question.question_id}-{arm}",
            )
            checkpoint[(question_id, arm)] = result
            _append_response(response_path, result)

        all_keys = {
            (question.question_id, arm)
            for question in questions
            for arm in ("closed_corpus_rag", "no_retrieval_context")
        }
        pending = all_keys - set(checkpoint)
        # Reconstruct the same planned 34-call ceiling on every resume.  This
        # preserves the scientific cost-gate evidence after all calls finish.
        pending_for_cost_gate = all_keys - smoke_keys
        config = response_client.config
        estimated_max, estimate_rows = _max_response_cost(
            core=core,
            config=config,
            questions=questions,
            retrieval_by_key=retrieval_by_key,
            pending=pending_for_cost_gate,
        )
        decision = (
            "proceed"
            if estimated_max is not None and estimated_max <= cost_limit
            else "stop"
        )
        cost_gate = {
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "max_output_tokens": config.max_output_tokens,
            "smoke_question_ids": list(SMOKE_QUESTION_IDS),
            "smoke_api_calls": len(smoke_keys),
            "smoke_actual_cost_usd": sum(
                float(checkpoint[key].telemetry.cost.estimated_cost_usd or 0.0)
                for key in smoke_keys
            ),
            "planned_additional_api_calls_at_gate": len(pending_for_cost_gate),
            "pending_api_calls_on_this_resume": len(pending),
            "estimated_additional_max_usd": estimated_max,
            "cost_limit_usd": cost_limit,
            "decision": decision,
            "method": "UTF-8-byte upper bound for input tokens plus configured output cap",
            "per_call_estimates": estimate_rows,
        }
        (output_dir / "response_cost_gate.json").write_text(
            json.dumps(cost_gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if decision == "stop":
            _write_markdown_report(
                output_dir=output_dir,
                snapshot_id=core.corpus_snapshot_id,
                questions=questions,
                metrics=metrics,
                evaluations=evaluation_rows,
                deterministic=deterministic,
                cost_gate=cost_gate,
                responses=None,
            )
            print(json.dumps(cost_gate, ensure_ascii=False, indent=2), file=sys.stderr)
            return 2

        for question in questions:
            for arm in ("closed_corpus_rag", "no_retrieval_context"):
                key = (question.question_id, arm)
                if key in checkpoint:
                    continue
                result = core.run(
                    question=question.question_text,
                    question_id=question.question_id,
                    retrieval_mode=RetrievalMode.HYBRID_RRF_BRIDGE,
                    routing_mode=question.routing_mode,
                    baseline_without_retrieval=arm == "no_retrieval_context",
                    run_id=f"vte-development-{question.question_id}-{arm}",
                )
                checkpoint[key] = result
                _append_response(response_path, result)

        ordered_results = [
            checkpoint[(question.question_id, arm)]
            for question in questions
            for arm in ("closed_corpus_rag", "no_retrieval_context")
        ]
        write_jsonl(
            [row.model_dump(mode="json") for row in ordered_results],
            path=output_dir / "response_runs_validated.jsonl",
        )
        write_csv(
            [csv_export_row(row) for row in ordered_results],
            path=output_dir / "response_runs.csv",
        )
        response_summary = _response_summary(ordered_results)
        (output_dir / "response_summary.json").write_text(
            json.dumps(response_summary, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    _write_markdown_report(
        output_dir=output_dir,
        snapshot_id=core.corpus_snapshot_id,
        questions=questions,
        metrics=metrics,
        evaluations=evaluation_rows,
        deterministic=deterministic,
        cost_gate=cost_gate,
        responses=response_summary,
    )
    summary = {
        "corpus_snapshot_id": core.corpus_snapshot_id,
        "questions": len(questions),
        "retrieval_metrics": metrics,
        "query_embedding_usage": query_embedding_usage,
        "determinism": deterministic,
        "cost_gate": cost_gate,
        "response_summary": response_summary,
    }
    (output_dir / "benchmark_qa.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

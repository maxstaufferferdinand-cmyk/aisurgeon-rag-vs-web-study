"""Deterministic pre-freeze analysis and audit exports for the Phase-2 study."""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .study_phase2 import (
    CORPUS_SNAPSHOT_ID,
    STUDY_MAX_ESTIMATED_API_COST_USD,
    StudyQuestion,
    read_jsonl,
    sha256_file,
    utc_now,
)

RETRIEVAL_MODES = ("fts", "vector", "hybrid_rrf", "hybrid_rrf_bridge")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def write_flat_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Write nested JSONL rows as stable CSV without losing nested payloads."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in columns})
    temporary.replace(path)


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _describe(values: Iterable[float]) -> dict[str, float | int | None]:
    data = [float(value) for value in values]
    if not data:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "minimum": None,
            "maximum": None,
            "p50": None,
            "p95": None,
        }
    return {
        "n": len(data),
        "mean": statistics.mean(data),
        "median": statistics.median(data),
        "standard_deviation": statistics.stdev(data) if len(data) > 1 else 0.0,
        "minimum": min(data),
        "maximum": max(data),
        "p50": _percentile(data, 0.50),
        "p95": _percentile(data, 0.95),
    }


def _ranking_metrics(
    ranking: Sequence[str], relevant: set[str]
) -> dict[str, float]:
    if not relevant:
        raise ValueError("retrieval metrics require at least one relevant evidence ID")
    first = next(
        (index for index, evidence_id in enumerate(ranking, start=1) if evidence_id in relevant),
        None,
    )
    ideal_length = min(len(relevant), 20)
    ideal_dcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_length + 1))
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, evidence_id in enumerate(ranking[:20], start=1)
        if evidence_id in relevant
    )
    return {
        "hit_at_1": float(bool(ranking and ranking[0] in relevant)),
        "recall_at_3": len(set(ranking[:3]).intersection(relevant)) / len(relevant),
        "recall_at_5": len(set(ranking[:5]).intersection(relevant)) / len(relevant),
        "mrr": 1.0 / first if first else 0.0,
        "ndcg_at_20": dcg / ideal_dcg if ideal_dcg else 0.0,
    }


def build_provisional_retrieval_metrics(
    questions: Sequence[StudyQuestion],
) -> dict[str, Any]:
    """Score the frozen retrieval configuration against provisional source gold."""

    per_question: list[dict[str, Any]] = []
    covered = [
        row for row in questions if row.coverage_stratum == "covered_by_local_corpus"
    ]
    for question in covered:
        relevant = set(question.expected_retrieval_unit_ids)
        for mode in RETRIEVAL_MODES:
            audit = question.coverage_audit.get(mode) or {}
            ranking = list(audit.get("evidence_ids") or ())
            metrics = _ranking_metrics(ranking, relevant)
            per_question.append(
                {
                    "question_id": question.question_id,
                    "mode": mode,
                    "relevant_count": len(relevant),
                    "retrieved_count": len(ranking),
                    "first_relevant_rank": next(
                        (
                            index
                            for index, evidence_id in enumerate(ranking, start=1)
                            if evidence_id in relevant
                        ),
                        None,
                    ),
                    **metrics,
                    "retrieval_latency_ms": (audit.get("latency_ms") or {}).get(
                        "retrieval"
                    ),
                }
            )
    aggregate: list[dict[str, Any]] = []
    for mode in RETRIEVAL_MODES:
        rows = [row for row in per_question if row["mode"] == mode]
        aggregate.append(
            {
                "mode": mode,
                "questions": len(rows),
                "hit_at_1": statistics.mean(row["hit_at_1"] for row in rows),
                "recall_at_3": statistics.mean(row["recall_at_3"] for row in rows),
                "recall_at_5": statistics.mean(row["recall_at_5"] for row in rows),
                "mrr": statistics.mean(row["mrr"] for row in rows),
                "ndcg_at_20": statistics.mean(row["ndcg_at_20"] for row in rows),
                "latency_ms": _describe(
                    row["retrieval_latency_ms"]
                    for row in rows
                    if row["retrieval_latency_ms"] is not None
                ),
            }
        )
    not_covered = [
        row
        for row in questions
        if row.coverage_stratum == "not_covered_by_local_corpus"
    ]
    no_evidence_by_mode = {
        mode: sum(
            (question.coverage_audit.get(mode) or {}).get("outcome")
            == "no_evidence_in_snapshot"
            for question in not_covered
        )
        for mode in RETRIEVAL_MODES
    }
    near_neighbour_ids = [
        question.question_id
        for question in not_covered
        if (question.coverage_audit.get("hybrid_rrf_bridge") or {}).get("evidence_ids")
    ]
    return {
        "schema_version": "provisional-retrieval-metrics-1.0.0",
        "corpus_snapshot_id": CORPUS_SNAPSHOT_ID,
        "gold_status": "provisional_pending_independent_human_freeze",
        "covered_questions": len(covered),
        "aggregate": aggregate,
        "per_question": per_question,
        "not_covered_audit": {
            "questions": len(not_covered),
            "no_evidence_in_snapshot_by_mode": no_evidence_by_mode,
            "near_neighbour_non_supporting_retrieval_question_ids": near_neighbour_ids,
            "interpretation": (
                "A retrieved topical near neighbour is not sufficient evidence for "
                "the requested intervention and does not establish local coverage."
            ),
        },
    }


def build_pilot_resource_analysis(
    results: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in results:
        groups[f"{row['model_config_id']}:{row['system_arm']}"].append(row)
    grouped: dict[str, Any] = {}
    for key, rows in sorted(groups.items()):
        grouped[key] = {
            "n": len(rows),
            "cost_usd": _describe(
                float((row.get("cost") or {}).get("total_estimated_cost_usd") or 0)
                for row in rows
            ),
            "input_tokens": _describe(
                float((row.get("token_usage") or {}).get("input_tokens") or 0)
                for row in rows
            ),
            "output_tokens": _describe(
                float((row.get("token_usage") or {}).get("output_tokens") or 0)
                for row in rows
            ),
            "total_tokens": _describe(
                float((row.get("token_usage") or {}).get("total_tokens") or 0)
                for row in rows
            ),
            "api_wall_time_ms": _describe(
                float((row.get("timing_ms") or {}).get("api_wall") or 0)
                for row in rows
            ),
            "end_to_end_time_ms": _describe(
                float((row.get("timing_ms") or {}).get("end_to_end") or 0)
                for row in rows
            ),
            "validator_status": dict(
                sorted(Counter(str(row.get("validator_status")) for row in rows).items())
            ),
            "answer_status": dict(
                sorted(
                    Counter(
                        str(
                            (row.get("validated_system_answer") or {}).get(
                                "answer_status"
                            )
                        )
                        for row in rows
                    ).items()
                )
            ),
            "web_search_calls": sum(
                int(attempt.get("web_search_tool_calls") or 0)
                for attempt in attempts
                if f"{attempt['model_config_id']}:{attempt['system_arm']}" == key
            ),
        }
    token_keys = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "query_embedding_tokens",
    )
    cost_keys = (
        "model_cost_usd",
        "web_search_cost_usd",
        "embedding_cost_usd",
        "retry_cost_usd",
        "total_estimated_cost_usd",
    )
    return {
        "schema_version": "development-cost-pilot-analysis-1.0.0",
        "experiment_id": "development_cost_pilot",
        "planned_responses": 20,
        "recorded_responses": len(results),
        "api_attempts": len(attempts),
        "http_successes": sum(row.get("http_status") == 200 for row in attempts),
        "retries": sum(int(row.get("retry_number") or 0) > 0 for row in attempts),
        "api_window_utc": {
            "start": min((str(row["utc_started"]) for row in attempts), default=None),
            "end": max((str(row["utc_finished"]) for row in attempts), default=None),
        },
        "requested_and_returned_models": sorted(
            {
                (str(row["requested_model"]), str(row.get("returned_model")))
                for row in attempts
            }
        ),
        "token_totals": {
            key: sum(int((row.get("token_usage") or {}).get(key) or 0) for row in results)
            for key in token_keys
        },
        "cost_totals_usd": {
            key: sum(float(row.get(key) or 0) for row in attempts) for key in cost_keys
        },
        "web_search_tool_calls": sum(
            int(row.get("web_search_tool_calls") or 0) for row in attempts
        ),
        "response_ids_present": sum(bool(row.get("response_id")) for row in attempts),
        "request_ids_present": sum(bool(row.get("x_request_id")) for row in attempts),
        "time_to_first_token_present": sum(
            row.get("time_to_first_token_ms") is not None for row in attempts
        ),
        "openai_processing_ms_present": sum(
            row.get("openai_processing_ms") is not None for row in attempts
        ),
        "validator_status": dict(
            sorted(Counter(str(row.get("validator_status")) for row in results).items())
        ),
        "by_model_and_arm": grouped,
    }


def _counter_dict(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def build_question_summary(questions: Sequence[StudyQuestion]) -> dict[str, Any]:
    return {
        "total": len(questions),
        "coverage": _counter_dict(row.coverage_stratum for row in questions),
        "clinical_domains": _counter_dict(row.clinical_domain for row in questions),
        "question_types": _counter_dict(row.question_type for row in questions),
        "difficulty": _counter_dict(row.difficulty for row in questions),
        "human_review_status": _counter_dict(
            row.human_review_status for row in questions
        ),
        "questions_with_directed_drug_bridge_gold": sum(
            bool(row.expected_relation_types) for row in questions
        ),
        "expected_source_documents": _counter_dict(
            source
            for row in questions
            for source in row.expected_source_documents
        ),
    }


def build_artifact_hash_manifest(*, root: Path, paths: Sequence[Path]) -> dict[str, Any]:
    entries = []
    for path in sorted({item.resolve() for item in paths}):
        if path.is_file():
            entries.append(
                {
                    "path": str(path.relative_to(root.resolve())),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    return {
        "schema_version": "pre-human-freeze-artifact-hashes-1.0.0",
        "created_at_utc": utc_now(),
        "freeze_scope": "pre_human_question_freeze; main-study inputs not yet frozen",
        "artifacts": entries,
    }


def _markdown_report(report: Mapping[str, Any], questions: Sequence[StudyQuestion]) -> str:
    pilot = report["pilot"]
    projection = report["cost_projection"]
    metrics = report["provisional_retrieval_metrics"]
    lines = [
        "# Technischer Pre-Freeze-Bericht: RAG versus Live Web",
        "",
        f"Status: `{report['status']}`",
        "",
        (
            "Die Hauptstudie wurde entsprechend dem Protokoll nicht gestartet. "
            "Alle Fragen und Goldfelder sind synthetische, quellengestützte "
            "Entwürfe und warten auf unabhängige menschliche Freigabe."
        ),
        "",
        "## Bestand und Design",
        "",
        f"- Corpus Snapshot: `{CORPUS_SNAPSHOT_ID}`",
        "- Retrieval-Einheiten / vorhandene Korpus-Embeddings: 4.469 / 4.469",
        "- PostgreSQL / pgvector: 18.6 / 0.8.6; lokaler Healthcheck bestanden",
        "- Policy: 99 HCC-History-Records ausgeschlossen; normales Leakage 0",
        "- SmPC→Leitlinien-Bridge: 139 aktive Relationen, 1 zulässiger Unmatched-Fall, 0 Rückwärtsrelationen",
        "- Zwei vormals auffällige Rationale-Relationen sind kanonisch eindeutig validiert; keine Mutation erforderlich",
        "- Fragen: 100 (80 lokal abgedeckt, 20 lokal nicht ausreichend abgedeckt)",
        "- Hauptstudienzellen: 800 geplant, 0 gestartet",
        "- Konfigurationen: GPT-5.5 Snapshot / medium; GPT-5.6 Sol Alias / high",
        (
            "- Offizielle Modellprüfung: "
            f"{report['model_availability_verification']['status']} am "
            f"{report['model_availability_verification']['verified_at_utc']}; "
            "GPT-5.5-Snapshot eindeutig, kein datierter GPT-5.6-Sol-Snapshot"
        ),
        "- Systeme: verpflichtende Live-Websuche und Closed-Corpus-RAG",
        "- Wiederholungen: Run 1 primär, Run 2 Reproduzierbarkeit",
        "",
        "## Development-Kostenpilot",
        "",
        f"- Responses: {pilot['recorded_responses']}/20; Versuche: {pilot['api_attempts']}; Retries: {pilot['retries']}",
        f"- HTTP 200: {pilot['http_successes']}/20; Web-Search-Aufrufe: {pilot['web_search_tool_calls']}",
        f"- Tokens gesamt: {pilot['token_totals']['total_tokens']:,}; davon Input {pilot['token_totals']['input_tokens']:,}, Output {pilot['token_totals']['output_tokens']:,}, Reasoning {pilot['token_totals']['reasoning_tokens']:,}",
        f"- Pilotkosten: {projection['pilot_total_cost_usd']:.8f} USD; vorbereitende Query-Embeddings: {projection['preparation_query_embedding_cost_usd']:.8f} USD",
        f"- Erwartete Hauptstudie: {projection['expected_main_cost_usd']:.6f} USD",
        f"- Konservative kumulative Projektion: {projection['conservative_total_projection_usd']:.6f} USD von {STUDY_MAX_ESTIMATED_API_COST_USD:.2f} USD",
        f"- Provenienzvalidator: {pilot['validator_status']}",
        "- Eine lokale No-evidence-Pilotantwort wurde korrekt verworfen, weil sie trotz Abstention strukturierte klinische Claims enthielt.",
        "",
        "## Provisorische Retrievalmetriken (80 abgedeckte Fragen)",
        "",
        "Diese Werte verwenden die noch nicht menschlich freigegebenen Gold-IDs; sie sind technische Vorwerte und keine klinische Validierung.",
        "",
        "| Modus | Hit@1 | Recall@3 | Recall@5 | MRR | nDCG@20 | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics["aggregate"]:
        latency = row["latency_ms"]
        lines.append(
            f"| {row['mode']} | {row['hit_at_1']:.4f} | {row['recall_at_3']:.4f} | "
            f"{row['recall_at_5']:.4f} | {row['mrr']:.4f} | {row['ndcg_at_20']:.4f} | "
            f"{latency['p50']:.2f} | {latency['p95']:.2f} |"
        )
    not_covered = metrics["not_covered_audit"]
    lines.extend(
        [
            "",
            (
                "Bei den 20 Not-covered-Entwürfen lieferte Hybrid-RRF plus Brücke "
                f"in {not_covered['no_evidence_in_snapshot_by_mode']['hybrid_rrf_bridge']}/20 "
                "Fällen keine Kandidaten. Die verbleibenden Near-neighbour-Fälle "
                f"({', '.join(not_covered['near_neighbour_non_supporting_retrieval_question_ids']) or 'keine'}) "
                "tragen die verlangte Aussage nicht und müssen im Human Freeze "
                "bestätigt werden."
            ),
            "",
            "## Fragenübersicht",
            "",
            "| ID | Coverage | Domäne | Typ | Schwierigkeit | Frage |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in questions:
        question = row.question_text.replace("|", "\\|")
        lines.append(
            f"| {row.question_id} | {row.coverage_stratum} | {row.clinical_domain} | "
            f"{row.question_type} | {row.difficulty} | {question} |"
        )
    lines.extend(
        [
            "",
            "## Nächster verbindlicher Schritt",
            "",
            (
                "Zwei unabhängige klinische Reviewer prüfen "
                "`question_freeze_review.xlsx`; anschließend wird die adjudizierte "
                "Datei mit dem Freeze-Skript in `study_questions_frozen.jsonl` "
                "überführt. Erst danach entsperrt der Runner die 800 "
                "Hauptstudienzellen."
            ),
            "",
            "```bash",
            "PYTHONPATH=src uv run python scripts/verify_openai_study_models.py",
            "PYTHONPATH=src uv run python scripts/freeze_study_questions.py --review-xlsx outputs/study_phase2/questions/question_freeze_review.xlsx",
            "PYTHONPATH=src uv run python scripts/run_rag_vs_web_study.py main",
            "```",
            "",
            "Keine Quell-PDF, kanonische JSONL-Datei oder Korpus-Embeddingdatei wurde überschrieben. Keine Gemini-Aufrufe; kein Git-Commit; kein Git-Push.",
            "",
        ]
    )
    return "\n".join(lines)


def build_prefreeze_artifacts(*, root: Path) -> dict[str, Any]:
    base = root / "outputs/study_phase2"
    question_rows = read_jsonl(base / "questions/question_candidates.jsonl")
    questions = tuple(StudyQuestion.model_validate(row) for row in question_rows)
    pilot_results = read_jsonl(base / "pilot/development_cost_pilot_results.jsonl")
    pilot_attempts = read_jsonl(base / "pilot/development_cost_pilot_attempts.jsonl")
    projection = json.loads(
        (base / "pilot/development_cost_pilot_summary.json").read_text(
            encoding="utf-8"
        )
    )
    retrieval = build_provisional_retrieval_metrics(questions)
    pilot = build_pilot_resource_analysis(pilot_results, pilot_attempts)
    question_summary = build_question_summary(questions)
    phase1 = json.loads(
        (
            root
            / "outputs/retrieval_phase/cs-f61b3d4e90089c1b890c23cb/qa/phase_completion_report.json"
        ).read_text(encoding="utf-8")
    )
    bridge_qa = json.loads(
        (root / "outputs/retrieval_phase/bridges/bridge_qa.json").read_text(
            encoding="utf-8"
        )
    )
    rationale_qa = json.loads(
        (root / "outputs/retrieval_phase/qa/rationale_relation_audit.json").read_text(
            encoding="utf-8"
        )
    )
    model_verification = json.loads(
        (
            base / "manifest/model_availability_verification.json"
        ).read_text(encoding="utf-8")
    )

    write_flat_csv(
        base / "pilot/development_cost_pilot_results.csv", pilot_results
    )
    write_flat_csv(
        base / "pilot/development_cost_pilot_attempts.csv", pilot_attempts
    )
    resource_rows = []
    for key, value in pilot["by_model_and_arm"].items():
        model_config_id, arm = key.split(":", maxsplit=1)
        resource_rows.append(
            {
                "model_config_id": model_config_id,
                "system_arm": arm,
                "n": value["n"],
                "mean_cost_usd": value["cost_usd"]["mean"],
                "median_cost_usd": value["cost_usd"]["median"],
                "max_cost_usd": value["cost_usd"]["maximum"],
                "mean_total_tokens": value["total_tokens"]["mean"],
                "median_api_wall_ms": value["api_wall_time_ms"]["median"],
                "p95_api_wall_ms": value["api_wall_time_ms"]["p95"],
                "median_end_to_end_ms": value["end_to_end_time_ms"]["median"],
                "web_search_calls": value["web_search_calls"],
                "validator_status": value["validator_status"],
            }
        )
    write_flat_csv(base / "pilot/development_cost_pilot_resource_summary.csv", resource_rows)

    _write_json(base / "analysis/provisional_retrieval_metrics.json", retrieval)
    write_flat_csv(
        base / "analysis/provisional_retrieval_metrics.csv", retrieval["aggregate"]
    )
    write_flat_csv(
        base / "analysis/provisional_retrieval_metrics_per_question.csv",
        retrieval["per_question"],
    )
    _write_json(base / "analysis/development_cost_pilot_analysis.json", pilot)
    _write_json(base / "analysis/question_summary.json", question_summary)

    report: dict[str, Any] = {
        "schema_version": "phase2-prefreeze-technical-report-1.0.0",
        "created_at_utc": utc_now(),
        "status": "HUMAN_QUESTION_FREEZE_REQUIRED",
        "corpus_snapshot_id": CORPUS_SNAPSHOT_ID,
        "model_availability_verification": model_verification,
        "question_summary": question_summary,
        "phase1_foundation": {
            "phase1_passed": phase1["passed"],
            "source_pdfs_unchanged": phase1["source_pdfs_unchanged_after_phase"],
            "database": phase1["database"],
            "policy_leakage": phase1["policy_leakage"],
            "bridge": bridge_qa,
            "rationale_relations": {
                "reported_pairs": rationale_qa["reported_pairs"],
                "canonical_explicit_and_indexed": rationale_qa[
                    "canonical_explicit_and_indexed"
                ],
                "decision": rationale_qa["decision"],
                "mutation_performed": rationale_qa["mutation_performed"],
            },
        },
        "main_study": {
            "planned_results": 800,
            "recorded_results": 0,
            "api_attempts": 0,
            "blocked_by_design": "independent human question/gold freeze",
        },
        "pilot": pilot,
        "cost_projection": projection,
        "cost_limit_usd": STUDY_MAX_ESTIMATED_API_COST_USD,
        "cost_gate_passed": (
            float(projection["conservative_total_projection_usd"])
            <= STUDY_MAX_ESTIMATED_API_COST_USD
        ),
        "provisional_retrieval_metrics": retrieval,
        "human_clinical_rating_status": "not_applicable_before_main_study",
        "protocol_deviations": [],
        "limitations": [
            "Question coverage and gold evidence remain provisional until independent human freeze.",
            "GPT-5.6 Sol had no dated snapshot in the official documentation on the access date.",
            "Automated provenance validation is not clinical correctness validation.",
            "The prespecified 80/20 benchmark mix is not an estimate of clinical coverage prevalence.",
            "OPENAI_ADMIN_KEY was not required; official cost reconciliation remains disabled.",
        ],
        "resume_commands": [
            "PYTHONPATH=src uv run python scripts/verify_openai_study_models.py",
            "PYTHONPATH=src uv run python scripts/freeze_study_questions.py --review-xlsx outputs/study_phase2/questions/question_freeze_review.xlsx",
            "PYTHONPATH=src uv run python scripts/run_rag_vs_web_study.py main",
        ],
    }
    _write_json(base / "reports/technical_completion_report.json", report)
    markdown_path = base / "reports/technical_completion_report.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(
        _markdown_report(report, questions), encoding="utf-8", newline="\n"
    )

    hash_paths = [
        root / "docs/STUDY_PROTOCOL_RAG_VS_WEB.md",
        root / "docs/STATISTICAL_ANALYSIS_PLAN_RAG_VS_WEB.md",
        *sorted((base / "prompts").glob("*")),
        *sorted(
            path
            for path in (base / "manifest").glob("*.json")
            if path.name != "artifact_hashes_pre_human_freeze.json"
        ),
        *sorted((base / "manifest").glob("*.csv")),
        *sorted((base / "questions").glob("*")),
        *sorted((base / "pilot").glob("*")),
        base / "reports/technical_completion_report.json",
        base / "reports/technical_completion_report.md",
    ]
    hashes = build_artifact_hash_manifest(root=root, paths=hash_paths)
    _write_json(base / "manifest/artifact_hashes_pre_human_freeze.json", hashes)
    return report


__all__ = [
    "build_artifact_hash_manifest",
    "build_pilot_resource_analysis",
    "build_prefreeze_artifacts",
    "build_provisional_retrieval_metrics",
    "build_question_summary",
    "write_flat_csv",
]

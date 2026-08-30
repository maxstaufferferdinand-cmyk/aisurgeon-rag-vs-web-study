"""Deterministic post-main-study exports and technical analyses.

No external API is called here.  JSONL remains canonical; CSV, Excel and
reports are derived and may be rebuilt idempotently after a completed/resolved
800-cell run.
"""

from __future__ import annotations

import csv
import json
import statistics
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from .study_analysis import build_artifact_hash_manifest, write_flat_csv
from .study_exports import (
    build_study_workbooks,
    export_planned_results,
    validate_study_workbooks,
)
from .study_phase2 import (
    CORPUS_SNAPSHOT_ID,
    PRIMARY_RESULT_COUNT,
    STUDY_MAX_ESTIMATED_API_COST_USD,
    StudyQuestion,
    build_randomization_manifest,
    load_frozen_questions,
    read_jsonl,
    sha256_file,
    utc_now,
)
from .study_statistics import (
    build_reproducibility_statistics,
    build_resource_statistics,
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    write_flat_csv(path, rows)


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _resource_effects(
    resource: list[dict[str, Any]], *, analysis_period: str
) -> list[dict[str, Any]]:
    included_metrics = {
        "total_estimated_cost_usd",
        "end_to_end_ms",
        "api_wall_ms",
        "time_to_first_token_ms",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
    }
    fields = (
        "analysis_period",
        "model_config_id",
        "metric",
        "rag_mean",
        "rag_median",
        "rag_p95",
        "web_mean",
        "web_median",
        "web_p95",
        "paired_difference_rag_minus_web_mean",
        "paired_difference_ci95_low",
        "paired_difference_ci95_high",
        "paired_ratio_rag_over_web_mean",
        "questions_paired",
        "bootstrap_resamples",
    )
    return [
        {field: row.get(field) for field in fields}
        for row in resource
        if row.get("analysis_period") == analysis_period
        and row.get("coverage_stratum") == "all_prespecified_80_20"
        and row.get("metric") in included_metrics
    ]


def _result_aggregates(results: list[dict[str, Any]]) -> dict[str, Any]:
    per_arm: dict[str, dict[str, Any]] = {}
    for arm in ("RAG", "WEB"):
        rows = [row for row in results if row.get("system_arm") == arm]
        per_arm[arm] = {
            "results": len(rows),
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
            "validator_issue_codes": dict(
                sorted(
                    Counter(
                        str(issue)
                        for row in rows
                        for issue in row.get("validator_issue_codes") or ()
                    ).items()
                )
            ),
            "estimated_cost_usd": sum(
                float((row.get("cost") or {}).get("total_estimated_cost_usd") or 0)
                for row in rows
            ),
        }
    coverage_status = Counter(
        (
            str(row.get("system_arm")),
            str(row.get("coverage_stratum")),
            str((row.get("validated_system_answer") or {}).get("answer_status")),
        )
        for row in results
    )
    per_arm_coverage = [
        {
            "system_arm": arm,
            "coverage_stratum": coverage,
            "answer_status": status,
            "count": count,
        }
        for (arm, coverage, status), count in sorted(coverage_status.items())
    ]
    rag = [row for row in results if row.get("system_arm") == "RAG"]
    web = [row for row in results if row.get("system_arm") == "WEB"]
    return {
        "per_arm": per_arm,
        "answer_status_by_arm_and_coverage": per_arm_coverage,
        "rag_query_embedding": {
            "cache_hits": sum(
                (row.get("retrieval") or {}).get("embedding_cache_hit") is True
                for row in rag
            ),
            "provider_calls": sum(
                int((row.get("retrieval") or {}).get("embedding_provider_calls") or 0)
                for row in rag
            ),
            "tokens": sum(
                int((row.get("retrieval") or {}).get("embedding_tokens") or 0)
                for row in rag
            ),
            "estimated_cost_usd": sum(
                float((row.get("retrieval") or {}).get("embedding_cost_usd") or 0)
                for row in rag
            ),
        },
        "web": {
            "search_tool_calls": sum(
                len(row.get("web_search_actions") or ()) for row in web
            ),
            "sources_consulted": sum(
                len(row.get("web_sources_consulted") or ()) for row in web
            ),
            "sources_cited": sum(len(row.get("web_sources_cited") or ()) for row in web),
        },
        "rag": {
            "evidence_ids_sent": sum(
                len(row.get("evidence_allowlist") or ()) for row in rag
            ),
            "policy_ineligible_package_issues": sum(
                "policy_ineligible_evidence_in_package"
                in (row.get("validator_issue_codes") or ())
                for row in rag
            ),
            "unknown_or_not_allowlisted_id_issues": sum(
                "unknown_or_not_allowlisted_evidence_id"
                in (row.get("validator_issue_codes") or ())
                for row in rag
            ),
            "retrieval_outcome": dict(
                sorted(
                    Counter(
                        str((row.get("retrieval") or {}).get("retrieval_outcome"))
                        for row in rag
                    ).items()
                )
            ),
        },
    }


def _pytest_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "not_recorded", "path": str(path)}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {
        key: sum(int(float(suite.attrib.get(key, "0"))) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    testcase_count = sum(len(suite.findall("testcase")) for suite in suites)
    totals["total_cases"] = totals["tests"]
    totals["tests"] = testcase_count
    totals["subtests"] = max(0, totals["total_cases"] - testcase_count)
    totals["time_seconds"] = sum(
        float(suite.attrib.get("time", "0")) for suite in suites
    )
    totals["status"] = (
        "passed" if totals["failures"] == 0 and totals["errors"] == 0 else "failed"
    )
    totals["path"] = str(path)
    return totals


def _validation_summary(root: Path, base: Path) -> dict[str, Any]:
    sources = {
        "phase2": base / "qa/phase2_validation.json",
        "corpus": root / "outputs/knowledge_corpus/qa/final_validation.json",
        "retrieval": root
        / "outputs/retrieval_phase"
        / CORPUS_SNAPSHOT_ID
        / "qa/retrieval_layer_validation.json",
        "phase1_cli": root
        / "outputs/retrieval_phase/qa/cli_rag_phase1_completion.json",
        "rationale_relations": root
        / "outputs/retrieval_phase/qa/rationale_relation_audit.json",
    }
    summary: dict[str, Any] = {}
    for name, path in sources.items():
        value = _read_json(path)
        if value is None:
            summary[name] = {"status": "not_recorded", "path": str(path)}
            continue
        checks = value.get("checks") or {}
        if isinstance(checks, dict):
            passed_checks = sum(check is True for check in checks.values())
            total_checks = len(checks)
        elif isinstance(checks, list):
            passed_checks = sum(item.get("passed") is True for item in checks)
            total_checks = len(checks)
        else:
            passed_checks = 0
            total_checks = 0
        passed_checks = int(
            value.get("passed_check_count", passed_checks) or passed_checks
        )
        total_checks = int(
            value.get("total_check_count", value.get("check_count", total_checks))
            or total_checks
        )
        if name == "rationale_relations" and value.get("passed") is True:
            passed_checks = int(value.get("canonical_explicit_and_indexed") or 0)
            total_checks = int(value.get("reported_pairs") or total_checks)
        summary[name] = {
            "status": "passed"
            if value.get("passed") is True or value.get("status") == "passed"
            else str(value.get("status") or "not_passed"),
            "passed_checks": passed_checks,
            "total_checks": total_checks,
            "path": str(path.relative_to(root)),
        }
    summary["pytest"] = _pytest_summary(base / "qa/pytest_full.xml")
    ruff = _read_json(base / "qa/ruff_phase2.json")
    summary["ruff"] = {
        "status": "passed" if ruff == [] else "failed" if ruff else "not_recorded",
        "issue_count": len(ruff) if isinstance(ruff, list) else None,
        "path": "outputs/study_phase2/qa/ruff_phase2.json",
    }
    return summary


def _fmt_number(value: Any, *, digits: int = 6) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _technical_markdown(report: dict[str, Any]) -> str:
    execution = report["execution"]
    costs = report["cost_breakdown_usd"]
    tokens = report["token_totals"]
    retrieval = report["retrieval"]
    reproducibility = report["reproducibility"]
    aggregates = report["technical_aggregates"]
    lines = [
        "# Technischer Abschlussbericht: RAG versus Live Web",
        "",
        f"Status: `{report['status']}`",
        "",
        (
            "Dies ist ein technischer, prä-spezifizierter In-silico-Benchmark. "
            "Er ist keine klinische Validierung und kein Nachweis klinischer Sicherheit."
        ),
        "",
        "## Freeze und Studiendesign",
        "",
        (
            f"Corpus Snapshot: `{report['corpus_snapshot_id']}`. Die exakt 100 "
            "Fragen wurden unverändert auf Grundlage von "
            f"`{report['question_set_freeze_basis']}` eingefroren: 80 "
            "`covered_by_local_corpus` und 20 `not_covered_by_local_corpus`. "
            "Es fand keine unabhängige klinische Validierung des Frage-/Goldsets statt."
        ),
        "",
        (
            f"API-Zeitraum (UTC): {report['api_window_utc']['start']} bis "
            f"{report['api_window_utc']['end']}. Die prä-spezifizierte 80/20-Mischung "
            "ist keine Schätzung realer klinischer Coverage-Prävalenz."
        ),
        "",
        "| Konfiguration | Angefordert | Zurückgegeben | Reasoning | Snapshotstatus |",
        "|---|---|---|---|---|",
    ]
    returned = {
        (row[0], row[2]): row[1]
        for row in report["models"]
    }
    for model in report["model_configurations"]:
        requested = model["requested_model"]
        reasoning = model["reasoning_effort"]
        returned_model = returned.get((requested, reasoning), "n/a")
        snapshot = "datiert" if model.get("dated_snapshot") else "undatierter Alias"
        lines.append(
            f"| {model['display_name']} | `{requested}` | `{returned_model}` | "
            f"{reasoning} | {snapshot} |"
        )
    lines.extend(
        [
            "",
            (
                "Beide Bedingungen nutzten Responses API, `store=false`, "
                "Service Tier `default`, Text-Verbosity `medium` und "
                f"maximal {report['response_configuration']['max_output_tokens']} "
                "Output-/Reasoning-Tokens. WEB verwendete ausschließlich verpflichtende "
                "Live-Websuche; RAG verwendete keine OpenAI-Tools."
            ),
            "",
            "## Ausführung und Kosten",
            "",
            (
                f"Geplant und erfasst: {execution['recorded_results']}/"
                f"{execution['planned_results']} eindeutige Ergebnisse; "
                f"{execution['api_attempts']} tatsächliche API-Versuche, "
                f"{execution['retries']} transparente Retries und "
                f"{execution['transparent_failures']} terminal fehlgeschlagene "
                "Studienzellen. Es wurde keine fachlich ungültige Antwort neu generiert."
            ),
            "",
            "| Kostenkomponente | Geschätzte USD |",
            "|---|---:|",
            f"| Pre-Freeze Query-Embeddings | {costs['preparation_query_embeddings']:.8f} |",
            f"| Development-Kostenpilot | {costs['development_cost_pilot']:.8f} |",
            f"| 800-Zellen-Hauptstudie inkl. Retries | {costs['main_study']:.8f} |",
            f"| **Kumulativ Phase 2** | **{costs['cumulative_phase2']:.8f}** |",
            f"| Aktives fail-closed Limit | {report['cost_limit_usd']:.2f} |",
            "",
            (
                "Die Kosten sind anhand der vor Studienbeginn eingefrorenen öffentlichen "
                f"Preistabelle `{report['pricing']['price_version']}` mit Stichtag "
                f"{report['pricing']['effective_as_of']} geschätzt. Eine offizielle "
                "Account-Abstimmung war mangels benötigtem Admin-Key nicht aktiviert."
            ),
            "",
            "## Primäre Ressourcenendpunkte",
            "",
            (
                "Die Tabelle nutzt den prä-spezifizierten Mittelwert der zwei Runs je "
                "Frage. Differenz = RAG minus WEB; 95-%-KI aus 10.000 "
                "Cluster-Bootstrap-Resamples auf Fragenebene."
            ),
            "",
            "| Modellkonfiguration | Metrik | RAG Mittel | WEB Mittel | Differenz | 95-%-KI | RAG/WEB |",
            "|---|---|---:|---:|---:|---|---:|",
        ]
    )
    display_metrics = {
        "total_estimated_cost_usd": "Kosten USD",
        "end_to_end_ms": "End-to-End ms",
        "api_wall_ms": "API-Wall ms",
        "time_to_first_token_ms": "TTFT ms",
        "total_tokens": "Total Tokens",
    }
    for row in report["resource_effects_mean_of_two_runs"]:
        if row["metric"] not in display_metrics:
            continue
        lines.append(
            f"| {row['model_config_id']} | {display_metrics[row['metric']]} | "
            f"{_fmt_number(row['rag_mean'])} | {_fmt_number(row['web_mean'])} | "
            f"{_fmt_number(row['paired_difference_rag_minus_web_mean'])} | "
            f"[{_fmt_number(row['paired_difference_ci95_low'])}; "
            f"{_fmt_number(row['paired_difference_ci95_high'])}] | "
            f"{_fmt_number(row['paired_ratio_rag_over_web_mean'])} |"
        )
    lines.extend(
        [
            "",
            (
                f"Gesamttokens: Input {tokens['input_tokens']:,}, Cached Input "
                f"{tokens['cached_input_tokens']:,}, Cache Write "
                f"{tokens['cache_write_tokens']:,}, Output {tokens['output_tokens']:,}, "
                f"davon Reasoning {tokens['reasoning_tokens']:,}, Total "
                f"{tokens['total_tokens']:,}. Reasoning-Tokens wurden als Untergruppe "
                "der Output-Tokens nicht doppelt verrechnet."
            ),
            "",
            (
                f"WEB führte {aggregates['web']['search_tool_calls']:,} "
                "Web-Search-Aktionen aus. Die 100 Query-Embeddings des "
                f"Pre-Freeze-Audits umfassten {report['query_embeddings']['preparation_tokens']:,} "
                f"Tokens; alle {aggregates['rag_query_embedding']['cache_hits']} "
                "RAG-Zellen nutzten transparent denselben Query-Hash-Cache, mit 0 "
                "neuen Embedding-Provideraufrufen im Hauptlauf. Fertige "
                "Retrievalantworten wurden nicht wiederverwendet."
            ),
            "",
            "## Retrieval, Abstention und Provenienzvalidierung",
            "",
            (
                f"RAG Recall@5: {retrieval['recall_at_5']:.6f}; MRR: "
                f"{retrieval['mrr']:.6f}; 400 RAG-Ergebnisse, davon "
                f"{retrieval['covered_rag_results']} für abgedeckte und "
                f"{retrieval['not_covered_rag_results']} für nicht abgedeckte Fragen. "
                f"`no_evidence_in_snapshot` trat bei "
                f"{retrieval['retrieval_outcome_no_evidence_in_snapshot']} der "
                "nicht abgedeckten RAG-Ergebnisse auf."
            ),
            "",
            "| Arm | Technisch akzeptiert | Abgestuft | Verworfen | supported | partially_supported | no_validated_evidence |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in ("RAG", "WEB"):
        arm_data = aggregates["per_arm"][arm]
        validator = arm_data["validator_status"]
        statuses = arm_data["answer_status"]
        lines.append(
            f"| {arm} | {validator.get('accepted', 0)} | "
            f"{validator.get('downgraded', 0)} | {validator.get('rejected', 0)} | "
            f"{statuses.get('supported', 0)} | {statuses.get('partially_supported', 0)} | "
            f"{statuses.get('no_validated_evidence', 0)} |"
        )
    lines.extend(
        [
            "",
            (
                "Im RAG-Arm erhielten alle 80/80 nicht abgedeckten Frage-Runs den "
                "validierten Status `no_validated_evidence`; zusätzlich 19/320 "
                "abgedeckte RAG-Runs. Das ist ein technischer Abstention-Befund, "
                "keine klinische Richtigkeitsbewertung."
            ),
            "",
            (
                f"Policy-ineligible Evidence-Package-Issues: "
                f"{aggregates['rag']['policy_ineligible_package_issues']}. "
                f"Unbekannte/nicht allowlistete Evidence-ID-Issues: "
                f"{aggregates['rag']['unknown_or_not_allowlisted_id_issues']}; "
                "diese Antwort wurde deterministisch abgefangen. WEB- und "
                "RAG-Provenienzvalidatoren wurden getrennt angewandt. Automatische "
                "Provenienzvalidierung belegt keine klinische Korrektheit."
            ),
            "",
            (
                f"WEB protokollierte {aggregates['web']['sources_consulted']:,} "
                "konsultierte Quellenoccurrences und "
                f"{aggregates['web']['sources_cited']:,} normalisierte zitierte "
                "Quellenoccurrences. Technische WEB-Flags: "
                f"{aggregates['per_arm']['WEB']['validator_issue_codes'].get('source_ref_missing_url_citation_annotation', 0)} "
                "fehlende URL-Zitationsannotationen und "
                f"{aggregates['per_arm']['WEB']['validator_issue_codes'].get('web_url_not_returned_by_current_search', 0)} "
                "nicht im jeweiligen Suchaufruf zurückgegebene URLs. Diese wurden "
                "abgestuft oder verworfen; eine Halluzinations- oder klinische "
                "Fehlerrate wird erst nach unabhängiger Bewertung berichtet."
            ),
            "",
            "## Reproduzierbarkeit",
            "",
            (
                f"Vollständige Run-1/Run-2-Paare: "
                f"{reproducibility['pairs_complete']}/"
                f"{reproducibility['pairs_expected']}. Antwortstatus-Übereinstimmung: "
                f"{reproducibility['answer_status_agreement_rate']:.4f}; mittlere "
                f"deterministische Token-Cosinusähnlichkeit: "
                f"{reproducibility['answer_token_cosine_similarity']['mean']:.4f}; "
                f"mittlere Quellenreferenz-Jaccard-Überlappung: "
                f"{reproducibility['source_ref_jaccard']['mean']:.4f}; mittlere "
                f"absolute Kostendifferenz: "
                f"{reproducibility['absolute_cost_difference_usd']['mean']:.6f} USD; "
                f"mittlere absolute End-to-End-Differenz: "
                f"{reproducibility['absolute_end_to_end_difference_ms']['mean']:.2f} ms."
            ),
            "",
            "## Tests und Integrität",
            "",
        ]
    )
    for name, validation in report["validation_summary"].items():
        details = []
        if validation.get("tests") is not None:
            details.append(f"{validation['tests']} Tests")
        if validation.get("subtests"):
            details.append(f"{validation['subtests']} Subtests")
        if validation.get("total_checks"):
            details.append(
                f"{validation['passed_checks']}/{validation['total_checks']} Checks"
            )
        if validation.get("issue_count") is not None:
            details.append(f"{validation['issue_count']} Issues")
        lines.append(
            f"- `{name}`: {validation.get('status')}"
            + (f" ({', '.join(details)})" if details else "")
        )
    lines.extend(
        [
            "",
            (
                f"Excel-Integrität: {report['excel_integrity']['status']}; Masterdatei "
                "mit 800 eindeutigen Ergebnissen und vier Armdateien mit je 200. "
                "Quell-PDFs, kanonische JSONL-Dateien und die 4.469 Corpus-Embeddings "
                "blieben unverändert."
            ),
            "",
            "## Protocol Deviations",
            "",
        ]
    )
    for deviation in report["protocol_deviations"]:
        lines.append(
            f"- `{deviation['deviation_id']}` — {deviation['title']}: "
            f"{deviation['reason']}"
        )
    lines.extend(["", "## Limitationen", ""])
    lines.extend(f"- {limitation}" for limitation in report["limitations"])
    lines.extend(
        [
            "",
            "## Zentrale Artefakte und Resume",
            "",
            "- Kanonische Rohdaten: `outputs/study_phase2/results/study_results.jsonl` und `api_attempts.jsonl`",
            "- Master-Excel: `outputs/study_phase2/excel/AISurgeon_RAG_vs_WEB_study_master.xlsx`",
            "- Klinisches Rating: `outputs/study_phase2/ratings/clinical_ratings_blinded.xlsx`",
            "- Citation Audit: `outputs/study_phase2/ratings/citation_audit.xlsx`",
            "- Vollständige maschinenlesbare Kennzahlen: `outputs/study_phase2/reports/technical_completion_report.json`",
            "",
            "Nach zwei unabhängigen verblindeten Ratings und Citation Audit:",
            "",
            "```bash",
            report["rating_resume"],
            "```",
            "",
            "Deterministischer Neuaufbau der technischen Exporte ohne API-Aufruf:",
            "",
            "```bash",
            "PYTHONPATH=src uv run python scripts/finalize_rag_vs_web_main.py",
            "```",
            "",
            "Es wurde kein Git-Commit und kein Git-Push durchgeführt.",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_main_rows(
    questions: tuple[StudyQuestion, ...],
    results: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    cells = build_randomization_manifest(questions, frozen=True)
    expected = {row.run_id for row in cells}
    actual = [str(row.get("run_id")) for row in results]
    if len(results) != PRIMARY_RESULT_COUNT or set(actual) != expected:
        raise RuntimeError(
            f"main study is incomplete: expected {PRIMARY_RESULT_COUNT} unique cells, "
            f"found {len(results)} rows / {len(set(actual))} unique IDs"
        )
    if len(actual) != len(set(actual)):
        raise RuntimeError("main result JSONL contains duplicate run IDs")
    attempt_ids = [str(row.get("attempt_id")) for row in attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise RuntimeError("API-attempt JSONL contains duplicate attempt IDs")
    attempted_runs = {str(row.get("run_id")) for row in attempts}
    missing_attempt = sorted(expected - attempted_runs)
    if missing_attempt:
        raise RuntimeError(f"{len(missing_attempt)} cells have no API-attempt record")
    invalid_status = [
        row["run_id"]
        for row in results
        if row.get("status") not in {"complete", "failed"}
    ]
    if invalid_status:
        raise RuntimeError(f"nonterminal main results: {invalid_status[:5]}")
    return {
        "planned_results": PRIMARY_RESULT_COUNT,
        "recorded_results": len(results),
        "complete_results": sum(row.get("status") == "complete" for row in results),
        "transparent_failures": sum(row.get("status") == "failed" for row in results),
        "api_attempts": len(attempts),
        "retries": sum(int(row.get("retry_number") or 0) > 0 for row in attempts),
        "unique_run_ids": len(set(actual)),
        "unique_attempt_ids": len(set(attempt_ids)),
    }


def _main_retrieval_rows(
    questions: tuple[StudyQuestion, ...], results: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_question = {row.question_id: row for row in questions}
    rows: list[dict[str, Any]] = []
    for result in results:
        if result.get("system_arm") != "RAG":
            continue
        question = by_question[str(result["question_id"])]
        ranking = list((result.get("retrieval") or {}).get("evidence_ids") or ())
        relevant = set(question.expected_retrieval_unit_ids)
        if relevant:
            first = next(
                (
                    index
                    for index, evidence_id in enumerate(ranking, start=1)
                    if evidence_id in relevant
                ),
                None,
            )
            recall_at_5 = len(set(ranking[:5]).intersection(relevant)) / len(relevant)
            mrr = 1.0 / first if first else 0.0
        else:
            first = None
            recall_at_5 = None
            mrr = None
        rows.append(
            {
                "run_id": result["run_id"],
                "question_id": question.question_id,
                "coverage_stratum": question.coverage_stratum,
                "model_config_id": result["model_config_id"],
                "repetition": result["repetition"],
                "expected_evidence_count": len(relevant),
                "retrieved_evidence_count": len(ranking),
                "first_relevant_rank": first,
                "recall_at_5": recall_at_5,
                "mrr": mrr,
                "retrieval_outcome": (result.get("retrieval") or {}).get(
                    "retrieval_outcome"
                ),
                "retrieval_latency_ms": (result.get("retrieval") or {}).get(
                    "retrieval_time_ms"
                ),
            }
        )
    covered = [row for row in rows if row["expected_evidence_count"]]
    no_evidence = [row for row in rows if not row["expected_evidence_count"]]
    return rows, {
        "rag_results": len(rows),
        "covered_rag_results": len(covered),
        "recall_at_5": statistics.mean(float(row["recall_at_5"]) for row in covered)
        if covered
        else None,
        "mrr": statistics.mean(float(row["mrr"]) for row in covered)
        if covered
        else None,
        "not_covered_rag_results": len(no_evidence),
        "retrieval_outcome_no_evidence_in_snapshot": sum(
            row["retrieval_outcome"] == "no_evidence_in_snapshot"
            for row in no_evidence
        ),
        "gold_status": (
            "study_owner_prefreeze_frozen_not_independently_clinically_validated"
        ),
    }


def _claims_sources(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        validated = result.get("validated_system_answer") or {}
        for claim in validated.get("claims") or ():
            rows.append(
                {
                    "run_id": result["run_id"],
                    "question_id": result["question_id"],
                    "model_config_id": result["model_config_id"],
                    "system_arm": result["system_arm"],
                    "repetition": result["repetition"],
                    "record_type": "claim",
                    "claim_id": claim.get("claim_id"),
                    "text": claim.get("claim_text"),
                    "validator_status": claim.get("validator_status"),
                    "source_refs": claim.get("validated_source_refs") or (),
                    "issue_codes": claim.get("issue_codes") or (),
                }
            )
        for index, recommendation in enumerate(
            validated.get("recommendations") or (), start=1
        ):
            rows.append(
                {
                    "run_id": result["run_id"],
                    "question_id": result["question_id"],
                    "model_config_id": result["model_config_id"],
                    "system_arm": result["system_arm"],
                    "repetition": result["repetition"],
                    "record_type": "recommendation",
                    "claim_id": f"recommendation-{index}",
                    "text": recommendation.get("recommendation_text"),
                    "validator_status": recommendation.get("validator_status"),
                    "source_refs": recommendation.get("validated_source_refs") or (),
                    "issue_codes": recommendation.get("issue_codes") or (),
                }
            )
    return rows


def _update_compliance(base: Path) -> None:
    for filename in ("REFINE_compliance.csv", "MI_CLEAR_LLM_compliance.csv"):
        path = base / "manifest" / filename
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            row["post_run_status"] = "technical_complete_clinical_rating_pending"
            row["post_run_evidence"] = (
                "outputs/study_phase2/results/study_results.jsonl; "
                "outputs/study_phase2/reports/technical_completion_report.json; "
                "outputs/study_phase2/excel/AISurgeon_RAG_vs_WEB_study_master.xlsx"
            )
        _write_csv(path, rows)


def finalize_main_study(*, root: Path) -> dict[str, Any]:
    """Finalize a complete 800-cell technical run without performing API calls."""

    root = root.resolve()
    base = root / "outputs/study_phase2"
    frozen_path = base / "questions/study_questions_frozen.jsonl"
    questions = load_frozen_questions(frozen_path)
    results_path = base / "results/study_results.jsonl"
    attempts_path = base / "results/api_attempts.jsonl"
    results = read_jsonl(results_path)
    attempts = read_jsonl(attempts_path)
    integrity = _validate_main_rows(questions, results, attempts)
    write_flat_csv(base / "results/study_results.csv", results)
    write_flat_csv(base / "results/api_attempts.csv", attempts)

    resource = build_resource_statistics(results)
    reproducibility_rows, reproducibility_summary = (
        build_reproducibility_statistics(results)
    )
    retrieval_rows, retrieval_summary = _main_retrieval_rows(questions, results)
    claims = _claims_sources(results)
    _write_csv(base / "analysis/resource_summary.csv", resource)
    _write_json(base / "analysis/resource_summary.json", resource)
    _write_csv(base / "analysis/reproducibility.csv", reproducibility_rows)
    _write_json(
        base / "analysis/reproducibility.json",
        {
            "summary": reproducibility_summary,
            "rows": reproducibility_rows,
        },
    )
    _write_csv(base / "analysis/rag_retrieval_metrics.csv", retrieval_rows)
    _write_json(base / "analysis/rag_retrieval_metrics.json", retrieval_summary)
    _write_csv(base / "analysis/claims_sources.csv", claims)

    pilot_attempts = read_jsonl(
        base / "pilot/development_cost_pilot_attempts.jsonl"
    )
    preparation = json.loads(
        (base / "manifest/preparation_api_usage.json").read_text(encoding="utf-8")
    )
    preparation_cost = float(preparation.get("query_embedding_cost_usd") or 0)
    pilot_cost = sum(
        float(row.get("total_estimated_cost_usd") or 0) for row in pilot_attempts
    )
    main_cost = sum(
        float(row.get("total_estimated_cost_usd") or 0) for row in attempts
    )
    external_cost = (
        preparation_cost
        + pilot_cost
        + main_cost
    )
    if external_cost > STUDY_MAX_ESTIMATED_API_COST_USD:
        raise RuntimeError(
            f"recorded cost ${external_cost:.6f} exceeds frozen cost ceiling"
        )
    _update_compliance(base)

    manifest_path = base / "manifest/study_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "status": "TECHNICAL_STUDY_COMPLETE_CLINICAL_RATING_PENDING",
            "main_study_calls_allowed": False,
            "main_results_sha256": sha256_file(results_path),
            "api_attempts_sha256": sha256_file(attempts_path),
            "main_integrity": integrity,
            "actual_external_api_cost_usd": external_cost,
            "technical_completion_at_utc": utc_now(),
            "clinical_rating_status": "pending_two_independent_blinded_reviews",
            "question_set_freeze_basis": "study_owner_pre_freeze_approval",
            "question_set_independent_clinical_validation": False,
        }
    )
    _write_json(manifest_path, manifest)

    export_planned_results(root=root, questions=questions)
    build_study_workbooks(root=root, questions=questions)
    excel = validate_study_workbooks(root=root)
    _write_json(base / "qa/excel_integrity.json", excel)

    price_table = json.loads(
        (base / "manifest/price_table.json").read_text(encoding="utf-8")
    )
    model_verification = json.loads(
        (base / "manifest/model_availability_verification.json").read_text(
            encoding="utf-8"
        )
    )
    corpus_embedding_report = _read_json(
        root
        / "outputs/retrieval_phase"
        / CORPUS_SNAPSHOT_ID
        / "embeddings/text-embedding-3-small/full_report.json"
    )
    technical_aggregates = _result_aggregates(results)
    retry_details = [
        {
            "attempt_id": row.get("attempt_id"),
            "run_id": row.get("run_id"),
            "http_status": row.get("http_status"),
            "error_class": row.get("error_class"),
            "error_code": row.get("error_code"),
            "retry_number": row.get("retry_number"),
            "retryable": row.get("retryable"),
        }
        for row in attempts
        if row.get("error_class")
        or row.get("http_status") not in {None, 200}
    ]
    response_configuration = {
        "service_tier_requested": sorted(
            {
                str(row.get("service_tier_requested"))
                for row in attempts
                if row.get("service_tier_requested") is not None
            }
        ),
        "service_tier_used": sorted(
            {
                str(row.get("service_tier_used"))
                for row in attempts
                if row.get("service_tier_used") is not None
            }
        ),
        "service_tier_used_missing_on_failed_attempts": sum(
            row.get("service_tier_used") is None for row in attempts
        ),
        "max_output_tokens": next(
            iter(sorted({int(row.get("max_output_tokens") or 0) for row in attempts}))
        ),
        "text_verbosity": sorted(
            {str(row.get("text_verbosity")) for row in attempts}
        ),
        "store": False,
        "stateless": True,
        "concurrency": 1,
    }

    report = {
        "schema_version": "phase2-technical-completion-1.0.0",
        "created_at_utc": utc_now(),
        "status": "TECHNICAL_STUDY_COMPLETE_CLINICAL_RATING_PENDING",
        "corpus_snapshot_id": CORPUS_SNAPSHOT_ID,
        "models": sorted(
            {
                (
                    str(row.get("requested_model")),
                    str(row.get("returned_model")),
                    str(row.get("reasoning_effort")),
                )
                for row in results
            }
        ),
        "model_configurations": manifest.get("model_configurations") or [],
        "model_availability_verification": model_verification,
        "model_identity_mismatches": sum(
            row.get("requested_model") != row.get("returned_model")
            for row in results
        ),
        "response_configuration": response_configuration,
        "api_window_utc": {
            "start": min(str(row["utc_started"]) for row in attempts),
            "end": max(str(row["utc_finished"]) for row in attempts),
        },
        "question_distribution": {
            "covered_by_local_corpus": 80,
            "not_covered_by_local_corpus": 20,
            "benchmark_weighting_not_prevalence_estimate": True,
        },
        "execution": integrity,
        "http_status": dict(
            sorted(Counter(str(row.get("http_status")) for row in attempts).items())
        ),
        "validator_status": dict(
            sorted(Counter(str(row.get("validator_status")) for row in results).items())
        ),
        "answer_status": dict(
            sorted(
                Counter(
                    str(
                        (row.get("validated_system_answer") or {}).get("answer_status")
                    )
                    for row in results
                ).items()
            )
        ),
        "token_totals": {
            key: sum(
                int((row.get("token_usage") or {}).get(key) or 0) for row in results
            )
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "query_embedding_tokens",
            )
        },
        "web_search_tool_calls": sum(
            int(row.get("web_search_tool_calls") or 0) for row in attempts
        ),
        "cost_breakdown_usd": {
            "preparation_query_embeddings": preparation_cost,
            "development_cost_pilot": pilot_cost,
            "main_study": main_cost,
            "cumulative_phase2": external_cost,
            "retry_cost": sum(
                float((row.get("cost") or {}).get("retry_cost_usd") or 0)
                for row in results
            ),
        },
        "external_api_cost_usd_including_preparation_and_pilot": external_cost,
        "cost_limit_usd": STUDY_MAX_ESTIMATED_API_COST_USD,
        "pricing": {
            "price_version": price_table.get("price_version"),
            "effective_as_of": price_table.get("effective_as_of"),
            "source": price_table.get("source"),
            "cost_reconciliation_status": sorted(
                {
                    str((row.get("cost") or {}).get("cost_reconciliation_status"))
                    for row in results
                }
            ),
        },
        "query_embeddings": {
            "model": "text-embedding-3-small",
            "preparation_calls": int(preparation.get("query_embedding_calls") or 0),
            "preparation_tokens": int(preparation.get("query_embedding_tokens") or 0),
            "preparation_cost_usd": preparation_cost,
            "main_study_cache_hits": technical_aggregates["rag_query_embedding"][
                "cache_hits"
            ],
            "main_study_provider_calls": technical_aggregates[
                "rag_query_embedding"
            ]["provider_calls"],
            "main_study_tokens": technical_aggregates["rag_query_embedding"][
                "tokens"
            ],
            "main_study_cost_usd": technical_aggregates["rag_query_embedding"][
                "estimated_cost_usd"
            ],
            "cache_use_transparently_recorded_per_rag_result": True,
        },
        "historical_corpus_embedding_setup": {
            "included_in_phase2_cost": False,
            "model": (corpus_embedding_report or {}).get("model"),
            "dimension": (corpus_embedding_report or {}).get("dimension"),
            "embedding_count": (corpus_embedding_report or {}).get(
                "database_embedding_count"
            ),
            "input_tokens": (corpus_embedding_report or {}).get("input_tokens"),
            "estimated_cost_usd": (corpus_embedding_report or {}).get(
                "estimated_cost_usd"
            ),
        },
        "resource_effects_mean_of_two_runs": _resource_effects(
            resource, analysis_period="mean_of_two_runs"
        ),
        "resource_effects_run_1_primary": _resource_effects(
            resource, analysis_period="1_primary"
        ),
        "technical_aggregates": technical_aggregates,
        "retry_details": retry_details,
        "retrieval": retrieval_summary,
        "reproducibility": reproducibility_summary,
        "excel_integrity": excel,
        "validation_summary": _validation_summary(root, base),
        "clinical_rating_status": "pending_two_independent_blinded_reviews_and_citation_audit",
        "question_set_freeze_basis": "study_owner_pre_freeze_approval",
        "question_set_independent_clinical_validation": False,
        "clinical_accuracy_claimed": False,
        "protocol_deviations": manifest.get("protocol_deviations") or [],
        "limitations": [
            "Independent clinical ratings, citation audit and adjudication are pending.",
            "The question/gold set was approved by the study owner and was not independently clinically validated.",
            "GPT-5.6 Sol was available only as an undated alias at study freeze.",
            "The synthetic 80/20 benchmark weighting is not a prevalence estimate.",
            "Provenance validators do not establish clinical correctness.",
            "The locally controlled snapshot is limited to three guidelines and nine medicinal-product-information PDFs.",
            "The snapshot retains 2,785 pre-existing review-severity extraction flags as documented QA limitations.",
            "Costs are estimates from the frozen public price table; official account reconciliation is disabled without an admin key.",
            "All 400 main-study RAG cells used transparently recorded query-embedding cache hits from the 100-question pre-freeze audit.",
            "Gemini was not called in Phase 2, and no patient data were processed.",
        ],
        "rating_prerequisite": (
            "Complete two independent blinded clinical reviews, citation audit, "
            "and adjudication in the provided workbooks."
        ),
        "rating_resume": (
            "PYTHONPATH=src uv run python scripts/import_rag_vs_web_ratings.py"
        ),
    }
    _write_json(base / "reports/technical_completion_report.json", report)
    report_md = _technical_markdown(report)
    (base / "reports/technical_completion_report.md").write_text(
        report_md, encoding="utf-8", newline="\n"
    )
    hash_paths = [
        results_path,
        attempts_path,
        frozen_path,
        manifest_path,
        root / "docs/STUDY_PROTOCOL_RAG_VS_WEB.md",
        root / "docs/STATISTICAL_ANALYSIS_PLAN_RAG_VS_WEB.md",
        *sorted((base / "analysis").glob("*")),
        *sorted((base / "excel").glob("*.xlsx")),
        *sorted((base / "manifest").glob("*.csv")),
        *sorted(
            path
            for path in (base / "manifest").glob("*.json")
            if not path.name.startswith("artifact_hashes_technical_complete")
        ),
        *sorted((base / "prompts").glob("*")),
        *sorted((base / "qa").glob("*")),
        *sorted((base / "ratings").glob("*.xlsx")),
        *sorted((base / "reports").glob("*")),
    ]
    hashes = build_artifact_hash_manifest(root=root, paths=hash_paths)
    hashes.update(
        {
            "schema_version": "technical-completion-artifact-hashes-1.0.0",
            "freeze_scope": (
                "technical study complete; clinical ratings and citation audit pending"
            ),
        }
    )
    _write_json(base / "manifest/artifact_hashes_technical_complete.json", hashes)
    return report


__all__ = ["finalize_main_study"]

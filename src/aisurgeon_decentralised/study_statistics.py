"""Question-clustered technical statistics for completed Phase-2 study cells."""

from __future__ import annotations

import math
import random
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .study_phase2 import RANDOMIZATION_SEED

RESOURCE_METRICS = {
    "total_estimated_cost_usd": lambda row: (row.get("cost") or {}).get(
        "total_estimated_cost_usd"
    ),
    "end_to_end_ms": lambda row: (row.get("timing_ms") or {}).get("end_to_end"),
    "api_wall_ms": lambda row: (row.get("timing_ms") or {}).get("api_wall"),
    "time_to_first_token_ms": lambda row: (row.get("timing_ms") or {}).get(
        "time_to_first_token"
    ),
    "total_tokens": lambda row: (row.get("token_usage") or {}).get("total_tokens"),
    "input_tokens": lambda row: (row.get("token_usage") or {}).get("input_tokens"),
    "output_tokens": lambda row: (row.get("token_usage") or {}).get("output_tokens"),
    "reasoning_tokens": lambda row: (row.get("token_usage") or {}).get(
        "reasoning_tokens"
    ),
    "local_retrieval_ms": lambda row: (row.get("retrieval") or {}).get(
        "retrieval_time_ms"
    ),
}


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def describe(values: Iterable[float]) -> dict[str, float | int | None]:
    data = [float(value) for value in values]
    if not data:
        return {
            "n": 0,
            "mean": None,
            "standard_deviation": None,
            "median": None,
            "iqr": None,
            "p50": None,
            "p95": None,
        }
    q25 = _percentile(data, 0.25)
    q75 = _percentile(data, 0.75)
    return {
        "n": len(data),
        "mean": statistics.mean(data),
        "standard_deviation": statistics.stdev(data) if len(data) > 1 else 0.0,
        "median": statistics.median(data),
        "iqr": (q75 - q25) if q25 is not None and q75 is not None else None,
        "p50": _percentile(data, 0.50),
        "p95": _percentile(data, 0.95),
    }


def cluster_bootstrap_mean_ci(
    values_by_question: Mapping[str, float],
    *,
    resamples: int = 10_000,
    seed: int = RANDOMIZATION_SEED,
) -> tuple[float | None, float | None]:
    """Percentile CI resampling the question, the prespecified cluster unit."""

    keys = sorted(values_by_question)
    if not keys:
        return None, None
    rng = random.Random(seed)
    sample_size = len(keys)
    distributions = []
    for _ in range(resamples):
        distributions.append(
            sum(values_by_question[rng.choice(keys)] for _ in range(sample_size))
            / sample_size
        )
    return _percentile(distributions, 0.025), _percentile(distributions, 0.975)


def _cell_value(row: Mapping[str, Any], metric: str) -> float | None:
    raw = RESOURCE_METRICS[metric](row)
    return float(raw) if raw is not None and raw != "" else None


def _mean_runs(
    rows: Sequence[Mapping[str, Any]], metric: str, repetition: str
) -> float | None:
    selected = [
        value
        for row in rows
        if repetition == "mean_of_two_runs" or row.get("repetition") == repetition
        if (value := _cell_value(row, metric)) is not None
    ]
    expected = 2 if repetition == "mean_of_two_runs" else 1
    if len(selected) != expected:
        return None
    return statistics.mean(selected)


def build_resource_statistics(
    results: Sequence[Mapping[str, Any]], *, bootstrap_resamples: int = 10_000
) -> list[dict[str, Any]]:
    """Build arm summaries and question-paired RAG-minus-WEB estimates."""

    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[
            (
                str(row["question_id"]),
                str(row["model_config_id"]),
                str(row["system_arm"]),
            )
        ].append(row)
    question_coverage = {
        str(row["question_id"]): str(row["coverage_stratum"]) for row in results
    }
    output: list[dict[str, Any]] = []
    models = sorted({str(row["model_config_id"]) for row in results})
    strata = (
        "all_prespecified_80_20",
        "covered_by_local_corpus",
        "not_covered_by_local_corpus",
    )
    periods = ("mean_of_two_runs", "1_primary")
    for model in models:
        for stratum in strata:
            question_ids = sorted(
                {
                    question_id
                    for question_id, candidate_model, _arm in grouped
                    if candidate_model == model
                    and (
                        stratum == "all_prespecified_80_20"
                        or question_coverage[question_id] == stratum
                    )
                }
            )
            for period in periods:
                for metric in RESOURCE_METRICS:
                    arm_values: dict[str, dict[str, float]] = {
                        "RAG": {},
                        "WEB": {},
                    }
                    for question_id in question_ids:
                        for arm in ("RAG", "WEB"):
                            value = _mean_runs(
                                grouped[(question_id, model, arm)], metric, period
                            )
                            if value is not None:
                                arm_values[arm][question_id] = value
                    paired_ids = sorted(
                        set(arm_values["RAG"]).intersection(arm_values["WEB"])
                    )
                    differences = {
                        question_id: arm_values["RAG"][question_id]
                        - arm_values["WEB"][question_id]
                        for question_id in paired_ids
                    }
                    ratios = {
                        question_id: arm_values["RAG"][question_id]
                        / arm_values["WEB"][question_id]
                        for question_id in paired_ids
                        if arm_values["WEB"][question_id] != 0
                    }
                    ci_low, ci_high = cluster_bootstrap_mean_ci(
                        differences,
                        resamples=bootstrap_resamples,
                        seed=RANDOMIZATION_SEED + len(output),
                    )
                    ratio_low, ratio_high = cluster_bootstrap_mean_ci(
                        ratios,
                        resamples=bootstrap_resamples,
                        seed=RANDOMIZATION_SEED + len(output) + 1,
                    )
                    rag_summary = describe(arm_values["RAG"].values())
                    web_summary = describe(arm_values["WEB"].values())
                    diff_summary = describe(differences.values())
                    ratio_summary = describe(ratios.values())
                    output.append(
                        {
                            "model_config_id": model,
                            "coverage_stratum": stratum,
                            "analysis_period": period,
                            "metric": metric,
                            "questions_expected": len(question_ids),
                            "questions_paired": len(paired_ids),
                            **{
                                f"rag_{key}": value
                                for key, value in rag_summary.items()
                            },
                            **{
                                f"web_{key}": value
                                for key, value in web_summary.items()
                            },
                            **{
                                f"paired_difference_rag_minus_web_{key}": value
                                for key, value in diff_summary.items()
                            },
                            "paired_difference_ci95_low": ci_low,
                            "paired_difference_ci95_high": ci_high,
                            "paired_ratio_rag_over_web_mean": ratio_summary["mean"],
                            "paired_ratio_ci95_low": ratio_low,
                            "paired_ratio_ci95_high": ratio_high,
                            "bootstrap_resamples": bootstrap_resamples,
                            "bootstrap_cluster": "question_id",
                        }
                    )
    return output


def _normalized_units(text: str) -> set[str]:
    return set(re.findall(r"[a-zäöüß0-9]+", text.casefold()))


def _cosine_token_similarity(left: str, right: str) -> float:
    a = Counter(re.findall(r"[a-zäöüß0-9]+", left.casefold()))
    b = Counter(re.findall(r"[a-zäöüß0-9]+", right.casefold()))
    if not a or not b:
        return 0.0
    dot = sum(count * b.get(token, 0) for token, count in a.items())
    denominator = math.sqrt(sum(value * value for value in a.values())) * math.sqrt(
        sum(value * value for value in b.values())
    )
    return dot / denominator if denominator else 0.0


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _source_refs(row: Mapping[str, Any]) -> set[str]:
    validated = row.get("validated_system_answer") or {}
    refs = {
        str(ref)
        for claim in validated.get("claims") or ()
        for ref in claim.get("validated_source_refs") or ()
    }
    refs.update(
        str(ref)
        for recommendation in validated.get("recommendations") or ()
        for ref in recommendation.get("validated_source_refs") or ()
    )
    return refs


def build_reproducibility_statistics(
    results: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in results:
        grouped[
            (
                str(row["question_id"]),
                str(row["model_config_id"]),
                str(row["system_arm"]),
            )
        ][str(row["repetition"])] = row
    rows: list[dict[str, Any]] = []
    for (question_id, model, arm), repetitions in sorted(grouped.items()):
        primary = repetitions.get("1_primary")
        repeat = repetitions.get("2_reproducibility")
        if primary is None or repeat is None:
            rows.append(
                {
                    "question_id": question_id,
                    "model_config_id": model,
                    "system_arm": arm,
                    "pair_complete": False,
                }
            )
            continue
        primary_answer = primary.get("validated_system_answer") or {}
        repeat_answer = repeat.get("validated_system_answer") or {}
        primary_claims = {
            " ".join(str(row.get("claim_text") or "").casefold().split())
            for row in primary_answer.get("claims") or ()
        }
        repeat_claims = {
            " ".join(str(row.get("claim_text") or "").casefold().split())
            for row in repeat_answer.get("claims") or ()
        }
        primary_recommendations = {
            " ".join(str(row.get("recommendation_text") or "").casefold().split())
            for row in primary_answer.get("recommendations") or ()
        }
        repeat_recommendations = {
            " ".join(str(row.get("recommendation_text") or "").casefold().split())
            for row in repeat_answer.get("recommendations") or ()
        }
        source_primary = _source_refs(primary)
        source_repeat = _source_refs(repeat)
        evidence_primary = set(primary.get("evidence_allowlist") or ())
        evidence_repeat = set(repeat.get("evidence_allowlist") or ())
        rows.append(
            {
                "question_id": question_id,
                "coverage_stratum": primary.get("coverage_stratum"),
                "model_config_id": model,
                "system_arm": arm,
                "pair_complete": True,
                "answer_status_agreement": (
                    primary_answer.get("answer_status")
                    == repeat_answer.get("answer_status")
                ),
                "claim_exact_set_jaccard": _jaccard(primary_claims, repeat_claims),
                "recommendation_exact_set_jaccard": _jaccard(
                    primary_recommendations, repeat_recommendations
                ),
                "answer_token_cosine_similarity": _cosine_token_similarity(
                    str(primary_answer.get("answer_text") or ""),
                    str(repeat_answer.get("answer_text") or ""),
                ),
                "answer_token_set_jaccard": _jaccard(
                    _normalized_units(str(primary_answer.get("answer_text") or "")),
                    _normalized_units(str(repeat_answer.get("answer_text") or "")),
                ),
                "source_ref_jaccard": _jaccard(source_primary, source_repeat),
                "evidence_allowlist_jaccard": _jaccard(
                    evidence_primary, evidence_repeat
                ),
                "absolute_token_difference": abs(
                    int((primary.get("token_usage") or {}).get("total_tokens") or 0)
                    - int((repeat.get("token_usage") or {}).get("total_tokens") or 0)
                ),
                "absolute_cost_difference_usd": abs(
                    float((primary.get("cost") or {}).get("total_estimated_cost_usd") or 0)
                    - float((repeat.get("cost") or {}).get("total_estimated_cost_usd") or 0)
                ),
                "absolute_end_to_end_difference_ms": abs(
                    float((primary.get("timing_ms") or {}).get("end_to_end") or 0)
                    - float((repeat.get("timing_ms") or {}).get("end_to_end") or 0)
                ),
                "clinical_rating_agreement": None,
            }
        )
    complete = [row for row in rows if row.get("pair_complete")]
    summary = {
        "pairs_expected": 400,
        "pairs_complete": len(complete),
        "answer_status_agreement_rate": statistics.mean(
            float(row["answer_status_agreement"]) for row in complete
        )
        if complete
        else None,
        "claim_exact_set_jaccard": describe(
            float(row["claim_exact_set_jaccard"]) for row in complete
        ),
        "recommendation_exact_set_jaccard": describe(
            float(row["recommendation_exact_set_jaccard"]) for row in complete
        ),
        "answer_token_cosine_similarity": describe(
            float(row["answer_token_cosine_similarity"]) for row in complete
        ),
        "source_ref_jaccard": describe(
            float(row["source_ref_jaccard"]) for row in complete
        ),
        "evidence_allowlist_jaccard": describe(
            float(row["evidence_allowlist_jaccard"]) for row in complete
        ),
        "absolute_token_difference": describe(
            float(row["absolute_token_difference"]) for row in complete
        ),
        "absolute_cost_difference_usd": describe(
            float(row["absolute_cost_difference_usd"]) for row in complete
        ),
        "absolute_end_to_end_difference_ms": describe(
            float(row["absolute_end_to_end_difference_ms"]) for row in complete
        ),
        "semantic_method": "local deterministic bag-of-token cosine; no LLM judge",
        "clinical_rating_agreement": "pending independent human ratings",
    }
    return rows, summary


__all__ = [
    "RESOURCE_METRICS",
    "build_reproducibility_statistics",
    "build_resource_statistics",
    "cluster_bootstrap_mean_ci",
    "describe",
]

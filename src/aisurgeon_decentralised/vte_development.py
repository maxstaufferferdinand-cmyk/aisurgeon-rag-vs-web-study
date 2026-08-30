"""Synthetic VTE development set and deterministic retrieval evaluation.

These questions are development material derived from the public VTE guideline.
They are explicitly not an untouched clinical gold standard.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .rag_core import RagRetrievalResult
from .retrieval_database import connect

VTE_SOURCE_FILE = "003-001l_S3_Prophylaxe-venoese-Thromboembolie-VTE_2026-04.pdf"
VTE_DEVELOPMENT_SCHEMA_VERSION = "vte-synthetic-development-1.0.0"


class VteDevelopmentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = VTE_DEVELOPMENT_SCHEMA_VERSION
    question_id: str
    question_text: str
    split: Literal["development"] = "development"
    label_origin: Literal["synthetic_draft_source_derived"] = (
        "synthetic_draft_source_derived"
    )
    category: str
    routing_mode: Literal["guideline_first", "smpc_first"]
    expected_item_numbers: tuple[str, ...]
    expected_evidence_ids: tuple[str, ...]
    expected_pdf_pages_1based: tuple[int, ...]
    expected_no_evidence: bool
    notes: str


@dataclass(frozen=True)
class _QuestionDraft:
    question_id: str
    text: str
    category: str
    routing: Literal["guideline_first", "smpc_first"]
    items: tuple[str, ...]
    notes: str


_DRAFTS: tuple[_QuestionDraft, ...] = (
    _QuestionDraft(
        "vte-dev-001",
        "Was soll vor der Indikationsstellung zu VTE-Prophylaxemaßnahmen evaluiert werden?",
        "clear_recommendation",
        "guideline_first",
        ("5.1",),
        "VTE- und Blutungsrisiken vor Indikationsstellung.",
    ),
    _QuestionDraft(
        "vte-dev-002",
        "Welche zusätzliche Maßnahme ist bei mittlerem oder hohem VTE-Risiko neben Basismaßnahmen vorgesehen?",
        "semantic_paraphrase",
        "guideline_first",
        ("6.3",),
        "Medikamentöse Prophylaxe zusätzlich zu Basismaßnahmen.",
    ),
    _QuestionDraft(
        "vte-dev-003",
        "Was empfiehlt die Leitlinie bei mittlerem oder hohem VTE-Risiko, wenn eine medikamentöse Prophylaxe kontraindiziert ist?",
        "contraindication_physical_measure",
        "guideline_first",
        ("7.3",),
        "Physikalische Maßnahmen bei Kontraindikation.",
    ),
    _QuestionDraft(
        "vte-dev-004",
        "Welche Antikoagulanzien werden vorzugsweise genannt und welche parenteralen Optionen werden gegenüber UFH bevorzugt?",
        "multi_evidence_drug_choice",
        "guideline_first",
        ("8.1", "8.2"),
        "Zwei getrennte formale Items sind zur vollständigen Beantwortung nötig.",
    ),
    _QuestionDraft(
        "vte-dev-005",
        "Zu welchem retrievalfähigen Leitlinienitem führt die importierte Eliquis-Fachinformation über Apixaban?",
        "product_substance_bridge",
        "smpc_first",
        ("8.1",),
        "Gerichtete SmPC→Produkt→Wirkstoff→Leitlinien-Brücke.",
    ),
    _QuestionDraft(
        "vte-dev-006",
        "Zu welchem retrievalfähigen Leitlinienitem führt die Xarelto-Fachinformation über Rivaroxaban?",
        "product_substance_bridge",
        "smpc_first",
        ("8.1",),
        "Gerichtete SmPC→Produkt→Wirkstoff→Leitlinien-Brücke.",
    ),
    _QuestionDraft(
        "vte-dev-007",
        "Soll bei parenteraler VTE-Prophylaxe NMH beziehungsweise Fondaparinux gegenüber UFH bevorzugt werden?",
        "abbreviation",
        "guideline_first",
        ("8.2",),
        "Abkürzungen NMH und UFH.",
    ),
    _QuestionDraft(
        "vte-dev-008",
        "Welche Organfunktionen sollen bei Auswahl und Anwendung der Medikamente zur VTE-Prophylaxe berücksichtigt werden?",
        "semantic_paraphrase",
        "guideline_first",
        ("9.2",),
        "Nieren- und Leberfunktion.",
    ),
    _QuestionDraft(
        "vte-dev-009",
        "Was sollte vor einer Heparinanwendung mit Blick auf die Thrombozytenzahl geschehen?",
        "monitoring",
        "guideline_first",
        ("9.4",),
        "Thrombozytenzahl vor Heparin.",
    ),
    _QuestionDraft(
        "vte-dev-010",
        "Innerhalb welchen Zeitraums sollte die medikamentöse VTE-Prophylaxe nach einem elektiven chirurgischen Eingriff begonnen werden?",
        "interval",
        "guideline_first",
        ("10.2",),
        "24-Stunden-Intervall.",
    ),
    _QuestionDraft(
        "vte-dev-011",
        "Wann soll eine medikamentöse VTE-Prophylaxe beginnen und woran soll sich ihre Dauer orientieren?",
        "multi_evidence_timing_duration",
        "guideline_first",
        ("10.1", "10.3"),
        "Beginn und Dauer stehen in zwei Items.",
    ),
    _QuestionDraft(
        "vte-dev-012",
        "Wie lange soll die medikamentöse VTE-Prophylaxe nach Hüftgelenkendoprothetik erfolgen?",
        "dose_duration",
        "guideline_first",
        ("12.17",),
        "Dauer 28–35 Tage.",
    ),
    _QuestionDraft(
        "vte-dev-013",
        "Welche Prophylaxedauer gilt nach Kniegelenksersatz?",
        "dose_duration",
        "guideline_first",
        ("12.22",),
        "Dauer 10–14 Tage.",
    ),
    _QuestionDraft(
        "vte-dev-014",
        "Soll nach einer Arthroskopie ohne zusätzliche VTE-Risikofaktoren eine medikamentöse Prophylaxe erfolgen?",
        "negation",
        "guideline_first",
        ("12.32",),
        "Negierte Empfehlung bei fehlenden Zusatzrisiken.",
    ),
    _QuestionDraft(
        "vte-dev-015",
        "Wann soll bei Polytrauma eine medikamentöse VTE-Prophylaxe begonnen werden?",
        "timing_polytrauma",
        "guideline_first",
        ("12.40",),
        "Beginn so früh wie möglich nach Kontrolle der Blutung.",
    ),
    _QuestionDraft(
        "vte-dev-016",
        "Soll bei onkologischen Patienten in der Finalphase grundsätzlich eine VTE-Prophylaxe durchgeführt werden?",
        "negation_palliative",
        "guideline_first",
        ("13.5",),
        "Keine Prophylaxe ohne symptomorientierte Indikation.",
    ),
    _QuestionDraft(
        "vte-dev-017",
        "Wie lange sollen Frauen mit vorausgegangener VTE nach der Geburt eine medikamentöse Prophylaxe erhalten?",
        "population_interval",
        "guideline_first",
        ("15.5",),
        "Postpartal mindestens sechs Wochen.",
    ),
    _QuestionDraft(
        "vte-dev-018",
        "Welche Antibiotikadosis behandelt eine ambulant erworbene Pneumonie?",
        "no_evidence_treatment",
        "guideline_first",
        (),
        "Out of scope: Antibiotikatherapie ist nicht im Snapshot validiert.",
    ),
    _QuestionDraft(
        "vte-dev-019",
        "Welche Impfung verhindert Masern?",
        "no_evidence_prevention",
        "guideline_first",
        (),
        "Out of scope: Impfprävention ist nicht im Snapshot validiert.",
    ),
    _QuestionDraft(
        "vte-dev-020",
        "Welche Insulindosis soll bei diabetischer Ketoazidose infundiert werden?",
        "no_evidence_unrelated_dose",
        "guideline_first",
        (),
        "Out of scope: Insulintherapie ist nicht im Snapshot validiert.",
    ),
)


def build_vte_development_questions(
    *, corpus_snapshot_id: str, root: Path | None = None
) -> tuple[VteDevelopmentQuestion, ...]:
    requested = sorted({item for draft in _DRAFTS for item in draft.items})
    with connect(root, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT source_native_item_number, retrieval_unit_id, pdf_pages_1based
            FROM retrieval.eligible_retrieval_units
            WHERE corpus_snapshot_id=%s AND source_file_name=%s
              AND source_role='guideline'
              AND source_native_item_number=ANY(%s)
            ORDER BY source_native_item_number, retrieval_unit_id
            """,
            (corpus_snapshot_id, VTE_SOURCE_FILE, requested),
        )
        rows = cursor.fetchall()
    by_item: dict[str, tuple[str, tuple[int, ...]]] = {}
    duplicates: set[str] = set()
    for item_number, evidence_id, pages in rows:
        if item_number in by_item:
            duplicates.add(item_number)
        by_item[item_number] = (evidence_id, tuple(pages))
    missing = sorted(set(requested) - set(by_item))
    if missing or duplicates:
        raise RuntimeError(
            f"VTE development item resolution is not unique; missing={missing}, "
            f"duplicates={sorted(duplicates)}"
        )

    output: list[VteDevelopmentQuestion] = []
    for draft in _DRAFTS:
        evidence_ids = tuple(by_item[item][0] for item in draft.items)
        pages = tuple(
            dict.fromkeys(page for item in draft.items for page in by_item[item][1])
        )
        output.append(
            VteDevelopmentQuestion(
                question_id=draft.question_id,
                question_text=draft.text,
                category=draft.category,
                routing_mode=draft.routing,
                expected_item_numbers=draft.items,
                expected_evidence_ids=evidence_ids,
                expected_pdf_pages_1based=pages,
                expected_no_evidence=not draft.items,
                notes=draft.notes,
            )
        )
    return tuple(output)


def write_vte_question_package(
    questions: tuple[VteDevelopmentQuestion, ...], *, output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_rows = [question.model_dump(mode="json") for question in questions]
    (output_dir / "vte_questions.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in json_rows
        ),
        encoding="utf-8",
    )
    with (output_dir / "vte_questions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(json_rows[0]))
        writer.writeheader()
        for row in json_rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, list)
                    else value
                    for key, value in row.items()
                }
            )
    lines = [
        "# Synthetischer VTE-Development-Satz",
        "",
        "Diese 20 source-derived Fragen sind `synthetic_draft`-Development-Daten, "
        "kein unangetasteter klinischer Studientestsatz.",
        "",
        "| ID | Kategorie | Frage | Erwartete Items | Seiten |",
        "|---|---|---|---|---|",
    ]
    for question in questions:
        lines.append(
            "| "
            + " | ".join(
                (
                    question.question_id,
                    question.category,
                    question.question_text.replace("|", "\\|"),
                    ", ".join(question.expected_item_numbers) or "no-evidence",
                    ", ".join(map(str, question.expected_pdf_pages_1based)) or "–",
                )
            )
            + " |"
        )
    (output_dir / "vte_questions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dcg(expected: set[str], found: list[str], k: int) -> float:
    return sum(
        (1.0 / math.log2(rank + 1))
        for rank, evidence_id in enumerate(found[:k], start=1)
        if evidence_id in expected
    )


def evaluate_retrieval_result(
    question: VteDevelopmentQuestion, result: RagRetrievalResult
) -> dict[str, Any]:
    found_hits = list(result.guideline_item_ranking)
    found = [hit.evidence_id for hit in found_hits]
    expected = set(question.expected_evidence_ids)
    if expected:
        ranks = [index + 1 for index, value in enumerate(found) if value in expected]
        ideal = _dcg(expected, list(expected), 5)
        row = {
            "hit_at_1": float(bool(found and found[0] in expected)),
            "recall_at_3": len(expected.intersection(found[:3])) / len(expected),
            "recall_at_5": len(expected.intersection(found[:5])) / len(expected),
            "reciprocal_rank": 1.0 / min(ranks) if ranks else 0.0,
            "ndcg_at_5": _dcg(expected, found, 5) / ideal if ideal else 0.0,
            "correct_no_evidence": None,
            "false_positive_no_evidence": None,
        }
    else:
        row = {
            "hit_at_1": None,
            "recall_at_3": None,
            "recall_at_5": None,
            "reciprocal_rank": None,
            "ndcg_at_5": None,
            "correct_no_evidence": not found,
            "false_positive_no_evidence": bool(found),
        }
    return {
        "question_id": question.question_id,
        "retrieval_mode": result.retrieval_mode.value,
        "expected_item_numbers": list(question.expected_item_numbers),
        "expected_evidence_ids": list(question.expected_evidence_ids),
        "found_item_numbers": [hit.source_native_item_number for hit in found_hits],
        "found_evidence_ids": found,
        "retrieval_outcome": result.retrieval_outcome,
        "retrieval_latency_ms": result.retrieval_time_ms,
        "relation_expansion_latency_ms": result.relation_expansion_time_ms,
        **row,
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def aggregate_retrieval_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [row for row in rows if row["hit_at_1"] is not None]
    negative = [row for row in rows if row["correct_no_evidence"] is not None]

    def mean(name: str) -> float:
        return statistics.fmean(float(row[name]) for row in positive) if positive else 0.0

    latencies = [float(row["retrieval_latency_ms"]) for row in rows]
    return {
        "positive_questions": len(positive),
        "no_evidence_questions": len(negative),
        "hit_at_1": mean("hit_at_1"),
        "recall_at_3": mean("recall_at_3"),
        "recall_at_5": mean("recall_at_5"),
        "mrr": mean("reciprocal_rank"),
        "ndcg_at_5": mean("ndcg_at_5"),
        "no_evidence_correct_rate": (
            statistics.fmean(float(row["correct_no_evidence"]) for row in negative)
            if negative
            else 0.0
        ),
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else 0.0,
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies, default=0.0),
        },
    }


__all__ = [
    "VTE_DEVELOPMENT_SCHEMA_VERSION",
    "VteDevelopmentQuestion",
    "aggregate_retrieval_metrics",
    "build_vte_development_questions",
    "evaluate_retrieval_result",
    "write_vte_question_package",
]

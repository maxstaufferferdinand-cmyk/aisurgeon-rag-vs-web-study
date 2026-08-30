"""Deterministic human-annotation package and retrieval evaluation utilities.

The generated questions are *synthetic drafts*.  Seed records are retained only
to make sampling reproducible; they are never copied into clinical gold fields.
Human reviewers must create and adjudicate every gold label independently.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

PACKAGE_SCHEMA_VERSION = "human-annotation-package-1.0.0"
ANNOTATION_ITEM_SCHEMA_VERSION = "human-annotation-item-1.0.0"
EVALUATION_SCHEMA_VERSION = "retrieval-evaluation-1.0.0"
DEFAULT_SAMPLING_SEED = "aisurgeon-annotation-v1-20260816"

SUPPORT_LABELS = ("supported", "partially_supported", "no_validated_evidence")
ENTAILMENT_STATUSES = ("supported", "partial", "contradicted", "insufficient")
RETRIEVAL_OUTCOMES = (
    "evidence_found",
    "retrieval_failure",
    "no_evidence_in_snapshot",
)
CONFLICT_STATUSES = (
    "none",
    "guideline_vs_smpc",
    "within_guideline",
    "version_conflict",
)
APPLICABILITY_STATUSES = ("applicable", "uncertain", "not_applicable")

# Exactly 25 strata make the requested 50/250 split transparent: two
# development and ten untouched-test slots per stratum.
STRATA = (
    "item_number",
    "semantic_paraphrase",
    "recommendation",
    "statement",
    "evidence_grade",
    "rationale",
    "dose",
    "preparation",
    "route",
    "interval",
    "contraindication",
    "warning",
    "adverse_reaction",
    "product_substance_mapping",
    "multi_source",
    "guideline_smpc_conflict",
    "near_neighbour_medicine",
    "negation",
    "typo",
    "version",
    "hcc_history_canary",
    "consultation_draft_leakage",
    "multi_turn",
    "no_evidence",
    "out_of_scope",
)

GOLD_FIELDS = (
    "gold_evidence_ids",
    "gold_evidence_groups",
    "gold_source_spans",
    "gold_support_label",
    "gold_entailment_status",
    "gold_retrieval_outcome",
    "gold_conflict_status",
    "gold_applicability_status",
    "gold_source_status",
    "gold_source_authority",
    "gold_should_abstain",
    "gold_dose_value",
    "gold_dose_unit",
    "gold_frequency_interval",
    "gold_route",
    "gold_population",
    "gold_negation",
    "gold_page_locators",
    "gold_answer_notes",
    "potential_harm_if_wrong",
)

REVIEW_COLUMNS = (
    "question_id",
    "split",
    "blind_order",
    "reviewer_id",
    "annotation_status",
    *GOLD_FIELDS,
    "reviewer_rationale",
    "reviewer_confidence",
    "needs_adjudication",
)


def _canonical_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
        separators=(",", ":") if indent is None else None,
    )


def _stable_digest(*parts: Any) -> str:
    material = "|".join(_canonical_json(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_canonical_json(dict(row)) + "\n")


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return _canonical_json(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _csv_value(row.get(name)) for name in fieldnames})


def discover_snapshot_manifest(project_root: Path) -> Path:
    candidates = sorted(
        project_root.glob("outputs/knowledge_corpus/manifests/corpus_snapshots/*.json")
    )
    if not candidates:
        raise FileNotFoundError("No corpus snapshot manifest found")
    parsed = [(json.loads(path.read_text(encoding="utf-8")), path) for path in candidates]
    parsed.sort(
        key=lambda item: (
            str(item[0].get("created_at_utc") or ""),
            str(item[0].get("corpus_snapshot_id") or ""),
        )
    )
    return parsed[-1][1]


def _eligible(unit: Mapping[str, Any]) -> bool:
    return (
        unit.get("eligibility_status") == "eligible"
        and unit.get("excluded_by_policy") is not True
        and unit.get("exclusion_reason") != "hcc_historical_change_table"
        and unit.get("retrieval_eligible") is not False
        and unit.get("answer_eligible") is not False
    )


def _text(unit: Mapping[str, Any]) -> str:
    return str(
        unit.get("retrieval_segment_text")
        or unit.get("exact_source_text")
        or unit.get("retrieval_text")
        or ""
    )


def _topic(unit: Mapping[str, Any]) -> str:
    chapter = unit.get("chapter_path") or []
    if chapter:
        return str(chapter[-1]).strip() or "den beschriebenen Sachverhalt"
    item_type = str(unit.get("source_native_item_type") or "").replace("_", " ")
    return item_type or "den beschriebenen Sachverhalt"


def _entity(unit: Mapping[str, Any]) -> str:
    products = [str(value).strip() for value in unit.get("product_names") or [] if str(value).strip()]
    if products:
        return products[0]
    substances = [
        str(value).strip()
        for value in unit.get("active_substance_names") or []
        if str(value).strip()
    ]
    if substances:
        return substances[0]
    return _topic(unit)


def _item_type(unit: Mapping[str, Any]) -> str:
    return str(unit.get("source_native_item_type") or "")


def _source_label(unit: Mapping[str, Any]) -> str:
    file_name = str(unit.get("source_file_name") or "Quelle")
    folded = file_name.casefold()
    if "003-001" in folded or "thromboembolie" in folded:
        return "VTE-Leitlinie"
    if "032-010" in folded or "pankreas" in folded:
        return "Pankreaskarzinom-Leitlinie"
    if "hcc" in folded and "bcc" in folded:
        return "HCC/BCC-Konsultationsfassung"
    return Path(file_name).stem


def _has_evidence_grade(unit: Mapping[str, Any]) -> bool:
    metadata = (unit.get("raw_v1") or {}).get("evidence_metadata") or {}
    return any(
        metadata.get(name) not in (None, "")
        for name in ("evidence_level", "recommendation_grade", "consensus_strength")
    )


def _ordered_pool(
    units: Iterable[Mapping[str, Any]], seed: str, name: str
) -> list[dict[str, Any]]:
    return sorted(
        (dict(unit) for unit in units),
        key=lambda unit: _stable_digest(seed, name, unit.get("retrieval_unit_id")),
    )


def _build_dual_source_pairs(
    units: Sequence[dict[str, Any]], seed: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    guidelines = [unit for unit in units if unit.get("source_role") == "guideline"]
    smpc = [unit for unit in units if unit.get("source_role") == "smPC"]
    smpc_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    display: dict[str, str] = {}
    for unit in smpc:
        for name in unit.get("active_substance_names") or []:
            normalized = str(name).casefold().strip()
            if len(normalized) >= 4:
                smpc_by_name[normalized].append(unit)
                display.setdefault(normalized, str(name))
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for guideline in guidelines:
        haystack = _text(guideline).casefold()
        for name in sorted(smpc_by_name, key=lambda value: (-len(value), value)):
            if name in haystack:
                candidate = sorted(
                    smpc_by_name[name],
                    key=lambda unit: _stable_digest(
                        seed,
                        "dual-source",
                        guideline.get("retrieval_unit_id"),
                        unit.get("retrieval_unit_id"),
                    ),
                )[0]
                left = dict(guideline)
                right = dict(candidate)
                left["_matched_entity"] = display[name]
                right["_matched_entity"] = display[name]
                pairs.append((left, right))
                break
    if not pairs and guidelines and smpc:
        pairs = list(zip(guidelines, itertools.cycle(smpc)))[: min(len(guidelines), len(smpc))]
    return sorted(
        pairs,
        key=lambda pair: _stable_digest(
            seed,
            "dual-pair-order",
            pair[0].get("retrieval_unit_id"),
            pair[1].get("retrieval_unit_id"),
        ),
    )


def _build_near_neighbour_pairs(
    units: Sequence[dict[str, Any]], seed: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    candidates = [
        unit
        for unit in units
        if unit.get("source_role") == "smPC"
        and (unit.get("product_names") or [])
        and _item_type(unit)
        in {"dosing_rule", "warning", "contraindication", "adverse_reaction"}
    ]
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_type[_item_type(candidate)].append(candidate)
    for item_type, same_type in sorted(by_type.items()):
        ordered = _ordered_pool(same_type, seed, f"near-neighbour-{item_type}")
        for index, left in enumerate(ordered):
            left_name = _entity(left).casefold()
            for right in ordered[index + 1 :] + ordered[:index]:
                if _entity(right).casefold() != left_name:
                    pairs.append((left, right))
                    break
    if not pairs:
        ordered = _ordered_pool(candidates, seed, "near-neighbour-fallback")
        for index, left in enumerate(ordered):
            right = next(
                (
                    candidate
                    for candidate in ordered[index + 1 :] + ordered[:index]
                    if _entity(candidate).casefold() != _entity(left).casefold()
                ),
                None,
            )
            if right is not None:
                pairs.append((left, right))
    return pairs


def _build_pools(units: Sequence[dict[str, Any]], seed: str) -> dict[str, list[Any]]:
    negation = re.compile(r"\b(nicht|kein(?:e|en|er|es)?|ohne|darf nicht|soll nicht)\b", re.I)
    filters: dict[str, Any] = {
        "item_number": lambda u: bool(u.get("source_native_item_number")),
        "semantic_paraphrase": lambda u: True,
        "recommendation": lambda u: _item_type(u) == "recommendation",
        "statement": lambda u: _item_type(u) in {"statement", "consensus_statement"},
        "evidence_grade": _has_evidence_grade,
        "rationale": lambda u: _item_type(u) == "rationale_block",
        "dose": lambda u: _item_type(u) == "dosing_rule" or u.get("dose_value") is not None,
        "preparation": lambda u: _item_type(u) == "preparation_administration",
        "route": lambda u: u.get("source_role") == "smPC"
        and (bool(u.get("route")) or _item_type(u) == "preparation_administration"),
        "interval": lambda u: u.get("source_role") == "smPC"
        and (bool(u.get("frequency")) or _item_type(u) == "dosing_rule"),
        "contraindication": lambda u: _item_type(u) == "contraindication",
        "warning": lambda u: _item_type(u) == "warning",
        "adverse_reaction": lambda u: _item_type(u) == "adverse_reaction",
        "product_substance_mapping": lambda u: bool(u.get("product_names"))
        and bool(u.get("active_substance_names"))
        and _item_type(u) in {"drug_product", "composition"},
        "negation": lambda u: bool(negation.search(_text(u))),
        "typo": lambda u: bool(u.get("product_names") or u.get("active_substance_names")),
        "version": lambda u: u.get("source_role") == "guideline",
        "consultation_draft_leakage": lambda u: u.get("source_status")
        == "consultation_draft",
        "multi_turn": lambda u: True,
    }
    pools: dict[str, list[Any]] = {}
    for name, predicate in filters.items():
        selected = [unit for unit in units if predicate(unit)]
        pools[name] = _ordered_pool(selected or units, seed, name)
    dual = _build_dual_source_pairs(units, seed)
    pools["multi_source"] = dual
    pools["guideline_smpc_conflict"] = dual
    pools["near_neighbour_medicine"] = _build_near_neighbour_pairs(units, seed)
    return pools


def _transpose_typo(value: str) -> str:
    characters = list(value)
    for index in range(1, len(characters) - 1):
        if characters[index].isalpha() and characters[index + 1].isalpha():
            characters[index], characters[index + 1] = characters[index + 1], characters[index]
            return "".join(characters)
    return value + "x"


def _question_for_unit(stratum: str, unit: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    entity = _entity(unit)
    topic = _topic(unit)
    item_number = unit.get("source_native_item_number") or unit.get(
        "printed_source_item_number"
    )
    questions = {
        "item_number": f"Welche Aussage gehört zum quellennativen Item {item_number} der {_source_label(unit)}?",
        "semantic_paraphrase": f"Welche validierte Evidenz enthält {_source_label(unit)} sinngemäß zum Thema {topic}?",
        "recommendation": f"Welche Empfehlung wird in {_source_label(unit)} im Abschnitt {topic} ausgesprochen?",
        "statement": f"Welche Kernaussage oder welches Statement ist in {_source_label(unit)} im Abschnitt {topic} dokumentiert?",
        "evidence_grade": f"Welche Kernaussage und welcher Evidenz- beziehungsweise Empfehlungsgrad sind in {_source_label(unit)} für Item {item_number or topic} belegt?",
        "rationale": f"Welche Begründung nennt {_source_label(unit)} im Zusammenhang mit {topic}?",
        "dose": f"Welche zugelassene Dosierung nennt die Fachinformation für {entity} im Kontext {topic}?",
        "preparation": f"Wie soll {entity} laut Fachinformation im Kontext {topic} zubereitet oder angewendet werden?",
        "route": f"Welcher Applikationsweg ist für {entity} im Abschnitt {topic} beschrieben?",
        "interval": f"Welches Dosierungsintervall nennt die Fachinformation für {entity} im Kontext {topic}?",
        "contraindication": f"Welche Gegenanzeige nennt die Fachinformation für {entity} im Kontext {topic}?",
        "warning": f"Welche Warnung oder Vorsichtsmaßnahme nennt die Fachinformation für {entity} im Kontext {topic}?",
        "adverse_reaction": f"Welche Nebenwirkung wird für {entity} im Kontext {topic} beschrieben?",
        "product_substance_mapping": f"Welcher Wirkstoff gehört zur Produktvariante {entity} im Abschnitt {topic}, und wie ist die Variante bezeichnet?",
        "negation": f"Welche ausdrücklich verneinte oder eingeschränkte Aussage enthält {_source_label(unit)} im Abschnitt {topic}?",
        "typo": f"Welche Evidenz findet sich zu {_transpose_typo(entity)} im Kontext {topic} trotz dieser Schreibvariante?",
        "version": f"Welche Dokumentversion und welcher Dokumentstatus gelten für {_source_label(unit)} im Abschnitt {topic}?",
        "consultation_draft_leakage": f"Welche Aussage enthält die HCC/BCC-Konsultationsfassung zu Item {item_number or '[unnummeriert]'} im Abschnitt {topic}, und wie muss ihr nicht-finaler Dokumentstatus dargestellt werden?",
        "multi_turn": f"Welche Evidenz enthält {_source_label(unit)} zu {topic}?",
    }
    question = questions.get(stratum, f"Welche Evidenz enthält der Snapshot zu {topic}?")
    turns = [{"turn_index": 1, "role": "user", "text": question}]
    if stratum == "multi_turn":
        turns.append(
            {
                "turn_index": 2,
                "role": "user",
                "text": "Ordne die Aussage im Anschluss ihrer Quellenrolle zu und verwende nur Evidence-IDs aus dem aktuellen Paket.",
            }
        )
    return question, turns


def _question_for_pair(
    stratum: str, pair: tuple[Mapping[str, Any], Mapping[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    left, right = pair
    if stratum == "near_neighbour_medicine":
        question = (
            f"Ordne Dosis-, Warnungs- oder Gegenanzeigenangaben getrennt {_entity(left)} und "
            f"{_entity(right)} in den Kontexten {_topic(left)} und {_topic(right)} zu; "
            "übertrage keine Angabe zwischen den Präparaten."
        )
    else:
        entity = str(left.get("_matched_entity") or right.get("_matched_entity") or _entity(right))
        if stratum == "guideline_smpc_conflict":
            question = (
                f"Vergleiche Leitlinie und Fachinformation zu {entity}. Kennzeichne Unterschiede "
                f"zwischen den Kontexten {_topic(left)} und {_topic(right)} ausdrücklich und löse sie nicht still auf."
            )
        else:
            question = (
                f"Welche Aussagen liefern Leitlinie und Fachinformation gemeinsam zu {entity}, "
                f"bezogen auf {_topic(left)} und {_topic(right)}, und welche Quellenrolle trägt jeweils die Evidenz?"
            )
    return question, [{"turn_index": 1, "role": "user", "text": question}]


def _negative_question(
    scope: str, stratum: str, split: str, ordinal: int
) -> tuple[str, list[dict[str, Any]]]:
    code = _stable_digest(scope, stratum, split, ordinal)[:6].upper()
    if scope == "out_of_scope_candidate":
        options = (
            "Welche steuerrechtliche Abschreibungsfrist gilt für eine private Solaranlage in Österreich?",
            "Wie wird morgen das Wetter in einer nicht angegebenen Stadt?",
            "Welche Ersatzteile benötigt ein historischer Verbrennungsmotor für eine Restaurierung?",
            "Welche Anlagestrategie garantiert im nächsten Jahr eine positive Rendite?",
        )
        question = options[int(code[:2], 16) % len(options)] + f" Bezugsfall {code}."
    else:
        question = (
            f"Welche validierte Evidenz enthält der Snapshot zur angeblich zugelassenen {stratum.replace('_', ' ')} "
            f"von Aisurgex-{code} bei der fiktiven Population Zeta-{ordinal + 1}?"
        )
    turns = [{"turn_index": 1, "role": "user", "text": question}]
    if stratum == "multi_turn":
        turns.append(
            {
                "turn_index": 2,
                "role": "user",
                "text": "Prüfe nach vollständigem Fallback erneut, ohne eine nicht belegte Aussage zu ergänzen.",
            }
        )
    return question, turns


def _empty_gold() -> dict[str, None]:
    return {field: None for field in GOLD_FIELDS}


def _schedule(split: str, count: int) -> list[dict[str, Any]]:
    return [
        {"split": split, "split_ordinal": ordinal + 1, "primary_stratum": STRATA[ordinal % len(STRATA)]}
        for ordinal in range(count)
    ]


def _assign_sampling_scopes(
    schedule: list[dict[str, Any]], target_negative: int, seed: str
) -> None:
    naturally_negative = {
        "hcc_history_canary": "no_evidence_candidate",
        "no_evidence": "no_evidence_candidate",
        "out_of_scope": "out_of_scope_candidate",
    }
    for slot in schedule:
        slot["sampling_scope"] = naturally_negative.get(
            slot["primary_stratum"], "evidence_candidate"
        )
    current = sum(
        slot["sampling_scope"] in {"no_evidence_candidate", "out_of_scope_candidate"}
        for slot in schedule
    )
    if target_negative < current:
        raise ValueError("Negative target is below the mandatory canary/no-evidence quota")
    preserve_probe = {
        "multi_source",
        "guideline_smpc_conflict",
        "near_neighbour_medicine",
        "negation",
        "typo",
        "version",
        "consultation_draft_leakage",
        "multi_turn",
    }
    candidates = [
        slot
        for slot in schedule
        if slot["sampling_scope"] == "evidence_candidate"
        and slot["primary_stratum"] not in preserve_probe
    ]
    if len(candidates) < target_negative - current:
        raise ValueError("Not enough non-probe slots for the negative sampling quota")
    candidates.sort(
        key=lambda slot: _stable_digest(
            seed, slot["split"], slot["split_ordinal"], slot["primary_stratum"], "negative"
        )
    )
    for index, slot in enumerate(candidates[: target_negative - current]):
        slot["sampling_scope"] = (
            "no_evidence_candidate" if index % 2 == 0 else "out_of_scope_candidate"
        )


def _gold_schema_properties() -> dict[str, Any]:
    nullable_string = {"type": ["string", "null"]}
    nullable_bool = {"type": ["boolean", "null"]}
    nullable_array = {"type": ["array", "null"]}
    gold_properties: dict[str, Any] = {
        name: nullable_string for name in GOLD_FIELDS
    }
    for name in (
        "gold_evidence_ids",
        "gold_evidence_groups",
        "gold_source_spans",
        "gold_page_locators",
    ):
        gold_properties[name] = nullable_array
    gold_properties["gold_should_abstain"] = nullable_bool
    gold_properties["potential_harm_if_wrong"] = nullable_bool
    gold_properties["gold_support_label"] = {
        "type": ["string", "null"],
        "enum": [*SUPPORT_LABELS, None],
    }
    gold_properties["gold_entailment_status"] = {
        "type": ["string", "null"],
        "enum": [*ENTAILMENT_STATUSES, None],
    }
    gold_properties["gold_retrieval_outcome"] = {
        "type": ["string", "null"],
        "enum": [*RETRIEVAL_OUTCOMES, None],
    }
    gold_properties["gold_conflict_status"] = {
        "type": ["string", "null"],
        "enum": [*CONFLICT_STATUSES, None],
    }
    gold_properties["gold_applicability_status"] = {
        "type": ["string", "null"],
        "enum": [*APPLICABILITY_STATUSES, None],
    }
    return gold_properties


def _annotation_schema() -> dict[str, Any]:
    gold_properties = _gold_schema_properties()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": ANNOTATION_ITEM_SCHEMA_VERSION,
        "title": "AISurgeon human annotation item",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "question_id",
            "split",
            "origin",
            "question_text",
            "turns",
            "gold",
        ],
        "properties": {
            "schema_version": {"const": ANNOTATION_ITEM_SCHEMA_VERSION},
            "question_id": {"type": "string", "pattern": "^q-[a-f0-9]{20}$"},
            "split": {"enum": ["development", "test"]},
            "split_ordinal": {"type": "integer", "minimum": 1},
            "split_lock": {"enum": ["development", "untouched_test"]},
            "origin": {"const": "synthetic_draft"},
            "primary_stratum": {"enum": list(STRATA)},
            "secondary_strata": {"type": "array", "items": {"type": "string"}},
            "sampling_scope": {
                "enum": [
                    "evidence_candidate",
                    "no_evidence_candidate",
                    "out_of_scope_candidate",
                ]
            },
            "language": {"const": "de"},
            "corpus_snapshot_id": {"type": "string"},
            "question_text": {"type": "string", "minLength": 1},
            "turns": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["turn_index", "role", "text"],
                    "properties": {
                        "turn_index": {"type": "integer", "minimum": 1},
                        "role": {"const": "user"},
                        "text": {"type": "string", "minLength": 1},
                    },
                },
            },
            "routing_probe": {
                "enum": ["guideline_first", "smpc_first", "dual_source", "full_fallback"]
            },
            "seed_evidence": {"type": "array", "items": {"type": "object"}},
            "policy_canary": {"type": ["object", "null"]},
            "gold": {
                "type": "object",
                "additionalProperties": False,
                "required": list(GOLD_FIELDS),
                "properties": gold_properties,
            },
        },
    }


def _adjudication_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "question_id": {"type": "string"},
        "reviewer_a_id": {"type": ["string", "null"]},
        "reviewer_b_id": {"type": ["string", "null"]},
        "disagreement_fields": {"type": ["array", "null"], "items": {"type": "string"}},
        "adjudicator_id": {"type": ["string", "null"]},
        "adjudication_status": {
            "enum": ["pending", "adjudicated", "requires_third_review"]
        },
        "adjudication_rationale": {"type": ["string", "null"]},
    }
    properties.update(_gold_schema_properties())
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "human-annotation-adjudication-1.0.0",
        "title": "AISurgeon annotation adjudication",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def planned_metrics() -> dict[str, Any]:
    return {
        "schema_version": "retrieval-metrics-plan-1.0.0",
        "status": "planned_not_clinically_validated",
        "ranking_metrics": {
            "evidence_recall_at_k": {"k": [5, 10, 20], "unit": "question"},
            "mrr": {"relevance": "human-adjudicated evidence IDs"},
            "ndcg_at_k": {"k": [5, 10, 20], "gain": "graded when available, binary otherwise"},
            "precision_at_k": {"k": [5, 10, 20], "denominator": "k"},
            "complete_multi_evidence_coverage_at_k": {"k": [5, 10, 20]},
        },
        "citation_metrics": {
            "citation_precision": "cited adjudicated evidence / all cited evidence",
            "citation_completeness": "cited adjudicated evidence / all required evidence",
            "source_span_sufficiency": "mean of human span-sufficiency assessments",
            "entity_attribution_accuracy": "mean of human entity-attribution assessments",
            "exact_dose_accuracy": "exact value, unit, interval and route assessment",
        },
        "answer_metrics": {
            "support_label_macro_f1": list(SUPPORT_LABELS),
            "correct_abstention_rate": "abstained / gold-should-abstain",
            "false_abstention_rate": "abstained / gold-answerable",
            "unsupported_claim_rate": "unsupported claims / all assessed claims",
            "harmful_unsupported_claim_rate": "potentially harmful unsupported claims / all assessed claims",
            "exclusion_leakage_rate": "policy-excluded retrieved IDs / all retrieved IDs",
        },
        "operations_metrics": {
            "stability_top10_jaccard": "pairwise Jaccard over repeated runs per question",
            "latency_ms": ["p50", "p95", "p99"],
            "token_load": ["input", "output", "cached", "embedding"],
            "cost_load": "sum and mean using the recorded price-list timestamp",
        },
        "mandatory_slices": [
            "split",
            "primary_stratum",
            "sampling_scope",
            "source_role",
            "source_status",
            "document_component",
            "single_vs_multi_turn",
        ],
        "reporting_constraints": [
            "No synthetic_draft item is an independent clinical gold label.",
            "Report denominators and confidence intervals with human-adjudicated results.",
            "No-evidence means only no sufficient evidence in the released snapshot after full fallback.",
        ],
    }


def _reviewer_guide() -> str:
    return """# Reviewer-Anleitung für den Human-Goldstandard

## Status und Ziel

Alle Fragen in diesem Paket sind `synthetic_draft`. Sie sind Sampling- und
Annotationsträger, kein klinischer Goldstandard. Zwei Reviewer annotieren
unabhängig; erst die dokumentierte Adjudikation erzeugt Goldfelder.

## Verblindung

Verwenden Sie ausschließlich den zugeteilten Blindexport. Der unblinded
Authoring-Export enthält technische Seed-IDs und darf Reviewern nicht gezeigt
werden. Primärstratum, vorgesehener Scope und Seed-Evidenz sind im Blindexport
nicht enthalten.

## Vorgehen

1. Führen Sie den vollständigen Retrieval-Fallback im im Paket fixierten Corpus
   Snapshot aus.
2. Prüfen Sie Evidence-ID, exakten Quellspan, Dokumentkomponente, Version,
   Dokumentstatus und Seitenlocator im Backend.
3. Annotieren Sie jede erforderliche Evidence-ID; ein Seed ist niemals Gold.
4. Prüfen Sie Dosiswert, Einheit, Intervall, Route, Population und Negation
   getrennt. Fehlende Angaben dürfen nicht ergänzt werden.
5. Leitlinie und Fachinformation haben verschiedene Quellenrollen. Unterschiede
   sind kein automatischer Extraktionsfehler und dürfen nicht still aufgelöst
   werden.
6. Die HCC/BCC-Konsultationsfassung ist `consultation_draft`. Historische
   HCC/BCC-Änderungstabellen mit `excluded_by_policy` sind keine zulässige
   normale Evidenz.

## Labelvertrag

- `supported`: Der vollständige konkrete Claim wird durch die adjudizierte
  Evidenz getragen.
- `partially_supported`: Nur ein Teil ist getragen oder relevante
  Einschränkungen bleiben.
- `no_validated_evidence`: Erst nach vollständigem Retrieval-Fallback ist im
  freigegebenen Snapshot keine ausreichende Evidenz vorhanden.

Intern zusätzlich: `entailment_status`, `retrieval_outcome`, `conflict_status`
und `applicability_status`. Modell-Selbstvertrauen ist kein Kriterium.

## Adjudikation

Abweichungen werden feldweise dokumentiert. Der Adjudikator sieht beide
Begründungen, prüft die Quelle erneut und trägt die Entscheidung samt kurzer
Rationale ein. Bei ungelöstem fachlichem Dissens wird `requires_third_review`
verwendet; es wird kein Mehrheitslabel erfunden.
"""


def _package_readme() -> str:
    return """# Human-Annotation-Package

Deterministisch erzeugte Vorbereitung für 50 Development- und 250 versiegelte
Testfragen. Genau 25 % der Slots sind als No-evidence-/Out-of-scope-Kandidaten
stratifiziert. Sämtliche Fragen sind `synthetic_draft`; alle klinischen
Goldfelder sind leer.

- `authoring_items.jsonl`: unblinded technische Sampling-Provenienz, nicht an
  Reviewer verteilen
- `development_blind_questions.*`, `test_blind_questions.*`: blindierbare
  Fragen ohne Seed-, Scope- oder Stratumhinweise
- `reviewer_a_annotations.csv`, `reviewer_b_annotations.csv`: unabhängige leere
  Annotationstabellen in verschiedener Reihenfolge
- `adjudication_template.*`: leere feldweise Adjudikation
- `annotation.schema.json`, `adjudication.schema.json`: Datenverträge
- `sampling_manifest.json`: Snapshot, Quoten und Dateihashes
- `metrics_plan.json`: vorab spezifizierte technische und spätere Humanmetriken

Der Testsplit bleibt bis zum festgelegten Evaluationslauf unangetastet und darf
nicht für Prompt-, Retrieval- oder Schwellenwertoptimierung verwendet werden.
"""


def _routing_probe(stratum: str) -> str:
    if stratum in {"recommendation", "statement", "evidence_grade", "rationale", "item_number"}:
        return "guideline_first"
    if stratum in {
        "dose",
        "preparation",
        "route",
        "interval",
        "contraindication",
        "warning",
        "adverse_reaction",
        "product_substance_mapping",
        "near_neighbour_medicine",
    }:
        return "smpc_first"
    if stratum in {"multi_source", "guideline_smpc_conflict"}:
        return "dual_source"
    return "full_fallback"


def _seed_projection(unit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "retrieval_unit_id": unit.get("retrieval_unit_id"),
        "parent_record_ids": unit.get("parent_record_ids") or [],
        "source_version_id": unit.get("source_version_id"),
        "source_role": unit.get("source_role"),
        "source_status": unit.get("source_status"),
        "document_component": unit.get("document_component"),
        "source_native_item_type": unit.get("source_native_item_type"),
        "source_native_item_number": unit.get("source_native_item_number"),
        "source_file_name": unit.get("source_file_name"),
        "pdf_pages_1based": unit.get("pdf_pages_1based") or [],
    }


def build_annotation_package(
    *,
    project_root: Path,
    output_dir: Path,
    snapshot_manifest_path: Path | None = None,
    retrieval_units_path: Path | None = None,
    hcc_exclusions_path: Path | None = None,
    development_count: int = 50,
    test_count: int = 250,
    sampling_seed: str = DEFAULT_SAMPLING_SEED,
) -> dict[str, Any]:
    """Build the deterministic package and return its sampling manifest."""

    if development_count < len(STRATA) or test_count < len(STRATA):
        raise ValueError("Each split must contain every required stratum")
    snapshot_manifest_path = snapshot_manifest_path or discover_snapshot_manifest(project_root)
    if not snapshot_manifest_path.is_absolute():
        snapshot_manifest_path = project_root / snapshot_manifest_path
    snapshot = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
    snapshot_id = str(snapshot["corpus_snapshot_id"])
    retrieval_units_path = retrieval_units_path or (
        project_root
        / "outputs/retrieval_phase"
        / snapshot_id
        / "provenance/retrieval_units_v2.jsonl"
    )
    if not retrieval_units_path.is_absolute():
        retrieval_units_path = project_root / retrieval_units_path
    hcc_exclusions_path = hcc_exclusions_path or (
        project_root / "outputs/knowledge_corpus/qa/hcc_historical_exclusions.jsonl"
    )
    if not hcc_exclusions_path.is_absolute():
        hcc_exclusions_path = project_root / hcc_exclusions_path
    units = [row for row in read_jsonl(retrieval_units_path) if _eligible(row)]
    if not units:
        raise ValueError("No eligible retrieval units available for annotation sampling")
    pools = _build_pools(units, sampling_seed)
    exclusions = sorted(
        read_jsonl(hcc_exclusions_path),
        key=lambda row: _stable_digest(sampling_seed, "hcc-canary", row.get("record_id")),
    )
    if not exclusions:
        raise ValueError("HCC historical policy canaries are required")
    excluded_ids = {str(row.get("record_id")) for row in exclusions}
    excluded_canonical: dict[str, dict[str, Any]] = {}
    canonical_dir = project_root / "outputs/knowledge_corpus/canonical"
    if canonical_dir.exists():
        for path in sorted(canonical_dir.glob("*.jsonl")):
            if path.name in {"documents.jsonl", "pharmacology.jsonl"}:
                continue
            for row in read_jsonl(path):
                record_id = str(row.get("record_id") or "")
                if record_id in excluded_ids and record_id not in excluded_canonical:
                    excluded_canonical[record_id] = row

    schedules = {
        "development": _schedule("development", development_count),
        "test": _schedule("test", test_count),
    }
    # Exactly 24% in development and 25.2% in test; aggregate is exactly 25%.
    target_negative = {
        "development": round(development_count * 0.24),
        "test": round(test_count * 0.252),
    }
    if development_count == 50 and test_count == 250:
        target_negative = {"development": 12, "test": 63}
    for split, schedule in schedules.items():
        _assign_sampling_scopes(schedule, target_negative[split], sampling_seed)

    pool_positions: Counter[str] = Counter()
    items: list[dict[str, Any]] = []
    seen_question_texts: set[str] = set()
    for split in ("development", "test"):
        for slot in schedules[split]:
            stratum = slot["primary_stratum"]
            scope = slot["sampling_scope"]
            ordinal = slot["split_ordinal"]
            seed_evidence: list[dict[str, Any]] = []
            policy_canary: dict[str, Any] | None = None
            if stratum == "hcc_history_canary":
                for _ in range(len(exclusions)):
                    exclusion = exclusions[
                        pool_positions[stratum] % len(exclusions)
                    ]
                    pool_positions[stratum] += 1
                    page = ", ".join(
                        str(value) for value in exclusion.get("pdf_pages_1based") or []
                    )
                    source_record = excluded_canonical.get(
                        str(exclusion.get("record_id"))
                    ) or {}
                    item_number = source_record.get(
                        "source_item_number"
                    ) or source_record.get("printed_source_item_number")
                    source_topic = _topic(
                        {
                            "chapter_path": source_record.get("section_path") or [],
                            "source_native_item_type": exclusion.get("record_type"),
                        }
                    )
                    if item_number:
                        question = (
                            f"Welche aktuell zulässige Empfehlung ist für das HCC/BCC-Item "
                            f"{item_number} im Kontext der Quellseite {page} belegt?"
                        )
                    else:
                        question = (
                            f"Welche aktuell zulässige HCC/BCC-Evidenz enthält der Snapshot zum Thema "
                            f"{source_topic} auf der geprüften Quellseite {page}?"
                        )
                    if question not in seen_question_texts:
                        break
                else:
                    question = (
                        f"{question} Technisch getrennter synthetic_draft-Samplingfall "
                        f"{split}-{ordinal}."
                    )
                turns = [{"turn_index": 1, "role": "user", "text": question}]
                policy_canary = {
                    "record_id": exclusion.get("record_id"),
                    "exclusion_reason": exclusion.get("exclusion_reason"),
                    "expected_policy_gate": "excluded_from_normal_retrieval",
                }
            elif scope in {"no_evidence_candidate", "out_of_scope_candidate"}:
                question, turns = _negative_question(scope, stratum, split, ordinal)
            elif stratum in {
                "multi_source",
                "guideline_smpc_conflict",
                "near_neighbour_medicine",
            }:
                pool = pools.get(stratum) or []
                if not pool:
                    raise ValueError(f"No sampling candidates for {stratum}")
                for _ in range(len(pool)):
                    pair = pool[pool_positions[stratum] % len(pool)]
                    pool_positions[stratum] += 1
                    question, turns = _question_for_pair(stratum, pair)
                    if question not in seen_question_texts:
                        break
                else:
                    question = (
                        f"{question} Technisch getrennter synthetic_draft-Samplingfall "
                        f"{split}-{ordinal}."
                    )
                    turns[0]["text"] = question
                seed_evidence = [_seed_projection(pair[0]), _seed_projection(pair[1])]
            else:
                pool = pools.get(stratum) or pools["semantic_paraphrase"]
                for _ in range(len(pool)):
                    unit = pool[pool_positions[stratum] % len(pool)]
                    pool_positions[stratum] += 1
                    question, turns = _question_for_unit(stratum, unit)
                    if question not in seen_question_texts:
                        break
                else:
                    question = (
                        f"{question} Technisch getrennter synthetic_draft-Samplingfall "
                        f"{split}-{ordinal}."
                    )
                    turns[0]["text"] = question
                seed_evidence = [_seed_projection(unit)]
            if question in seen_question_texts:
                raise ValueError(f"Duplicate question text: {question}")
            seen_question_texts.add(question)
            question_id = "q-" + _stable_digest(
                snapshot_id,
                split,
                ordinal,
                stratum,
                scope,
                [row.get("retrieval_unit_id") for row in seed_evidence],
                policy_canary,
                question,
            )[:20]
            secondary: list[str] = []
            if len(turns) > 1:
                secondary.append("multi_turn")
            if stratum in {"multi_source", "guideline_smpc_conflict"}:
                secondary.append("multi_source")
            item = {
                "schema_version": ANNOTATION_ITEM_SCHEMA_VERSION,
                "question_id": question_id,
                "split": split,
                "split_ordinal": ordinal,
                "split_lock": "untouched_test" if split == "test" else "development",
                "origin": "synthetic_draft",
                "primary_stratum": stratum,
                "secondary_strata": sorted(set(secondary)),
                "sampling_scope": scope,
                "language": "de",
                "corpus_snapshot_id": snapshot_id,
                "question_text": question,
                "turns": turns,
                "routing_probe": _routing_probe(stratum),
                "seed_evidence": seed_evidence,
                "policy_canary": policy_canary,
                "gold": _empty_gold(),
            }
            items.append(item)

    if len({item["question_id"] for item in items}) != len(items):
        raise ValueError("Question IDs are not unique")
    if any(any(value is not None for value in item["gold"].values()) for item in items):
        raise AssertionError("Automatically generated clinical gold labels are forbidden")

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "annotation.schema.json", _annotation_schema())
    _write_json(output_dir / "adjudication.schema.json", _adjudication_schema())
    _write_json(output_dir / "metrics_plan.json", planned_metrics())
    (output_dir / "REVIEWER_GUIDE.md").write_text(
        _reviewer_guide(), encoding="utf-8", newline="\n"
    )
    (output_dir / "README.md").write_text(
        _package_readme(), encoding="utf-8", newline="\n"
    )
    _write_jsonl(output_dir / "authoring_items.jsonl", items)

    blind_fields = (
        "question_id",
        "split",
        "split_lock",
        "origin",
        "language",
        "question_text",
        "turns",
        "corpus_snapshot_id",
        "blind_order",
    )
    for split in ("development", "test"):
        split_items = [item for item in items if item["split"] == split]
        split_items.sort(
            key=lambda item: _stable_digest(sampling_seed, "blind", split, item["question_id"])
        )
        blind_rows = [
            {
                **{field: item[field] for field in blind_fields if field != "blind_order"},
                "blind_order": index,
            }
            for index, item in enumerate(split_items, start=1)
        ]
        base = "development" if split == "development" else "test"
        _write_jsonl(output_dir / f"{base}_blind_questions.jsonl", blind_rows)
        _write_csv(output_dir / f"{base}_blind_questions.csv", blind_rows, blind_fields)

    question_by_id = {item["question_id"]: item for item in items}
    for reviewer in ("a", "b"):
        ordered_ids = sorted(
            question_by_id,
            key=lambda question_id: _stable_digest(
                sampling_seed, "reviewer", reviewer, question_id
            ),
        )
        review_rows: list[dict[str, Any]] = []
        for blind_order, question_id in enumerate(ordered_ids, start=1):
            item = question_by_id[question_id]
            row: dict[str, Any] = {
                "question_id": question_id,
                "split": item["split"],
                "blind_order": blind_order,
                "reviewer_id": None,
                "annotation_status": "unreviewed",
                **_empty_gold(),
                "reviewer_rationale": None,
                "reviewer_confidence": None,
                "needs_adjudication": None,
            }
            review_rows.append(row)
        _write_csv(output_dir / f"reviewer_{reviewer}_annotations.csv", review_rows, REVIEW_COLUMNS)
        _write_jsonl(output_dir / f"reviewer_{reviewer}_annotations.jsonl", review_rows)

    adjudication_rows = [
        {
            "question_id": question_id,
            "reviewer_a_id": None,
            "reviewer_b_id": None,
            "disagreement_fields": None,
            "adjudicator_id": None,
            "adjudication_status": "pending",
            **_empty_gold(),
            "adjudication_rationale": None,
        }
        for question_id in sorted(question_by_id)
    ]
    adjudication_fields = list(adjudication_rows[0])
    _write_jsonl(output_dir / "adjudication_template.jsonl", adjudication_rows)
    _write_csv(output_dir / "adjudication_template.csv", adjudication_rows, adjudication_fields)

    files = sorted(path for path in output_dir.iterdir() if path.is_file())
    file_integrity = {
        path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in files
        if path.name != "sampling_manifest.json"
    }
    scope_counts = Counter(item["sampling_scope"] for item in items)
    split_counts = Counter(item["split"] for item in items)
    stratum_by_split: dict[str, dict[str, int]] = {}
    for split in ("development", "test"):
        stratum_by_split[split] = dict(
            sorted(Counter(item["primary_stratum"] for item in items if item["split"] == split).items())
        )
    negative_count = scope_counts["no_evidence_candidate"] + scope_counts[
        "out_of_scope_candidate"
    ]
    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": "hap-" + _stable_digest(
            snapshot_id, sampling_seed, development_count, test_count, STRATA
        )[:24],
        "corpus_snapshot_id": snapshot_id,
        "corpus_snapshot_manifest": str(snapshot_manifest_path.relative_to(project_root)),
        "corpus_snapshot_content_fingerprint_sha256": snapshot.get(
            "content_fingerprint_sha256"
        ),
        "source_snapshot_created_at_utc": snapshot.get("created_at_utc"),
        "sampling_seed": sampling_seed,
        "question_origin": "synthetic_draft",
        "clinical_gold_status": "empty_pending_independent_human_review",
        "counts": {
            "total": len(items),
            "development": split_counts["development"],
            "test_untouched": split_counts["test"],
            "no_evidence_or_out_of_scope": negative_count,
            "no_evidence_or_out_of_scope_percent": round(negative_count / len(items) * 100, 4),
        },
        "scope_counts": dict(sorted(scope_counts.items())),
        "stratum_counts_by_split": stratum_by_split,
        "required_strata": list(STRATA),
        "test_set_policy": {
            "status": "sealed_unannotated",
            "use_for_development": False,
            "release_condition": "predeclared final evaluation after development freeze",
        },
        "blinding": {
            "blind_exports_omit": [
                "primary_stratum",
                "secondary_strata",
                "sampling_scope",
                "routing_probe",
                "seed_evidence",
                "policy_canary",
                "gold",
            ],
            "reviewer_orders_independent": True,
        },
        "input_integrity": {
            "snapshot_manifest_sha256": sha256_file(snapshot_manifest_path),
            "retrieval_units_v2_sha256": sha256_file(retrieval_units_path),
            "hcc_historical_exclusions_sha256": sha256_file(hcc_exclusions_path),
            "eligible_retrieval_unit_count": len(units),
            "hcc_policy_canary_count": len(exclusions),
        },
        "files": file_integrity,
        "limitations": [
            "Synthetic drafts are not independent clinical validation.",
            "Sampling seeds aid coverage but never constitute gold evidence.",
            "Final labels require independent review and adjudication.",
        ],
    }
    _write_json(output_dir / "sampling_manifest.json", manifest)
    return manifest


def _metric(values: Sequence[float]) -> dict[str, Any]:
    return {
        "value": None if not values else sum(values) / len(values),
        "n": len(values),
    }


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _ndcg(
    retrieved: Sequence[str], gold: set[str], grades: Mapping[str, float], k: int
) -> float:
    def gain(identifier: str) -> float:
        relevance = float(grades.get(identifier, 1.0 if identifier in gold else 0.0))
        return (2**relevance - 1) if relevance > 0 else 0.0

    dcg = sum(gain(identifier) / math.log2(rank + 2) for rank, identifier in enumerate(retrieved[:k]))
    ideal_gains = sorted((gain(identifier) for identifier in gold), reverse=True)[:k]
    ideal = sum(value / math.log2(rank + 2) for rank, value in enumerate(ideal_gains))
    return 0.0 if ideal == 0 else dcg / ideal


def _macro_f1(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = [
        row
        for row in rows
        if row.get("gold_support_label") in SUPPORT_LABELS
        and row.get("predicted_support_label") in SUPPORT_LABELS
    ]
    per_label: dict[str, float] = {}
    for label in SUPPORT_LABELS:
        true_positive = sum(
            row["gold_support_label"] == label and row["predicted_support_label"] == label
            for row in usable
        )
        false_positive = sum(
            row["gold_support_label"] != label and row["predicted_support_label"] == label
            for row in usable
        )
        false_negative = sum(
            row["gold_support_label"] == label and row["predicted_support_label"] != label
            for row in usable
        )
        denominator = 2 * true_positive + false_positive + false_negative
        per_label[label] = 0.0 if denominator == 0 else 2 * true_positive / denominator
    return {
        "value": None if not usable else sum(per_label.values()) / len(SUPPORT_LABELS),
        "n": len(usable),
        "per_label": per_label,
    }


def evaluate_records(
    records: Sequence[Mapping[str, Any]], k_values: Sequence[int] = (5, 10, 20)
) -> dict[str, Any]:
    """Evaluate adjudicated retrieval/answer result rows.

    Metrics with unavailable human fields are returned with ``value=null`` and
    ``n=0`` instead of silently manufacturing labels or denominators.
    """

    k_values = tuple(sorted({int(value) for value in k_values if int(value) > 0}))
    ranking_rows = [
        row
        for row in records
        if isinstance(row.get("gold_evidence_ids"), list)
        and bool(row.get("gold_evidence_ids"))
        and isinstance(row.get("retrieved_evidence_ids"), list)
    ]
    ranking: dict[str, Any] = {}
    for k in k_values:
        recalls: list[float] = []
        precisions: list[float] = []
        ndcgs: list[float] = []
        complete: list[float] = []
        for row in ranking_rows:
            gold = set(map(str, row["gold_evidence_ids"]))
            retrieved = list(map(str, row["retrieved_evidence_ids"]))
            top = retrieved[:k]
            hits = len(gold.intersection(top))
            recalls.append(hits / len(gold))
            precisions.append(hits / k)
            ndcgs.append(_ndcg(retrieved, gold, row.get("gold_relevance_grades") or {}, k))
            if len(gold) > 1:
                complete.append(float(gold.issubset(set(top))))
        ranking[f"evidence_recall_at_{k}"] = _metric(recalls)
        ranking[f"precision_at_{k}"] = _metric(precisions)
        ranking[f"ndcg_at_{k}"] = _metric(ndcgs)
        ranking[f"complete_multi_evidence_coverage_at_{k}"] = _metric(complete)
    reciprocal_ranks: list[float] = []
    for row in ranking_rows:
        gold = set(map(str, row["gold_evidence_ids"]))
        rank = next(
            (index for index, identifier in enumerate(row["retrieved_evidence_ids"], start=1) if str(identifier) in gold),
            None,
        )
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
    ranking["mrr"] = _metric(reciprocal_ranks)

    citation_precision: list[float] = []
    citation_completeness: list[float] = []
    for row in records:
        gold_value = row.get("gold_evidence_ids")
        cited_value = row.get("cited_evidence_ids")
        if not isinstance(gold_value, list) or not isinstance(cited_value, list):
            continue
        gold = set(map(str, gold_value))
        cited = set(map(str, cited_value))
        if cited:
            citation_precision.append(len(gold.intersection(cited)) / len(cited))
        if gold:
            citation_completeness.append(len(gold.intersection(cited)) / len(gold))

    def reviewed_boolean(name: str) -> dict[str, Any]:
        values = [float(bool(row[name])) for row in records if row.get(name) is not None]
        return _metric(values)

    gold_abstain = [row for row in records if row.get("gold_should_abstain") is True]
    gold_answerable = [row for row in records if row.get("gold_should_abstain") is False]
    correct_abstention = _metric(
        [float(row.get("predicted_abstained") is True) for row in gold_abstain]
    )
    false_abstention = _metric(
        [float(row.get("predicted_abstained") is True) for row in gold_answerable]
    )

    total_claims = sum(int(row.get("total_claims") or 0) for row in records)
    unsupported_claims = sum(int(row.get("unsupported_claims") or 0) for row in records)
    harmful_unsupported = sum(
        int(row.get("harmful_unsupported_claims") or 0) for row in records
    )
    total_retrieved = 0
    leaked_retrieved = 0
    leaked_queries = 0
    retrieval_queries = 0
    for row in records:
        retrieved = row.get("retrieved_evidence_ids")
        if not isinstance(retrieved, list):
            continue
        retrieval_queries += 1
        retrieved_set = set(map(str, retrieved))
        excluded = set(map(str, row.get("excluded_evidence_ids") or []))
        excluded.update(map(str, row.get("excluded_retrieved_evidence_ids") or []))
        hits = retrieved_set.intersection(excluded)
        total_retrieved += len(retrieved)
        leaked_retrieved += len(hits)
        leaked_queries += bool(hits)

    by_question: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        if row.get("question_id") and isinstance(row.get("retrieved_evidence_ids"), list):
            by_question[str(row["question_id"])].append(row)
    stability_values: list[float] = []
    for repetitions in by_question.values():
        for left, right in itertools.combinations(repetitions, 2):
            left_set = set(map(str, left["retrieved_evidence_ids"][:10]))
            right_set = set(map(str, right["retrieved_evidence_ids"][:10]))
            union = left_set.union(right_set)
            stability_values.append(1.0 if not union else len(left_set.intersection(right_set)) / len(union))

    latencies = [float(row["latency_ms"]) for row in records if row.get("latency_ms") is not None]
    token_fields = ("input_tokens", "output_tokens", "cached_tokens", "embedding_tokens")
    tokens = {
        name: {
            "total": sum(int(row.get(name) or 0) for row in records),
            "n_reported": sum(row.get(name) is not None for row in records),
        }
        for name in token_fields
    }
    costs = [float(row["cost"]) for row in records if row.get("cost") is not None]

    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "record_count": len(records),
        "ranking": ranking,
        "citations": {
            "citation_precision": _metric(citation_precision),
            "citation_completeness": _metric(citation_completeness),
            "source_span_sufficiency": reviewed_boolean("source_span_sufficient"),
            "entity_attribution_accuracy": reviewed_boolean("entity_attribution_correct"),
            "exact_dose_accuracy": reviewed_boolean("dose_exact_match"),
        },
        "answers": {
            "support_label_macro_f1": _macro_f1(records),
            "correct_abstention_rate": correct_abstention,
            "false_abstention_rate": false_abstention,
            "unsupported_claim_rate": {
                "value": None if total_claims == 0 else unsupported_claims / total_claims,
                "numerator": unsupported_claims,
                "denominator": total_claims,
            },
            "harmful_unsupported_claim_rate": {
                "value": None if total_claims == 0 else harmful_unsupported / total_claims,
                "numerator": harmful_unsupported,
                "denominator": total_claims,
            },
        },
        "policy": {
            "exclusion_leakage_rate": {
                "value": None if total_retrieved == 0 else leaked_retrieved / total_retrieved,
                "numerator": leaked_retrieved,
                "denominator": total_retrieved,
            },
            "queries_with_exclusion_leakage_rate": {
                "value": None if retrieval_queries == 0 else leaked_queries / retrieval_queries,
                "numerator": leaked_queries,
                "denominator": retrieval_queries,
            },
        },
        "operations": {
            "stability_top10_jaccard": _metric(stability_values),
            "latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "p99": _percentile(latencies, 0.99),
                "n": len(latencies),
            },
            "tokens": tokens,
            "cost": {
                "total": sum(costs),
                "mean": None if not costs else sum(costs) / len(costs),
                "n": len(costs),
                "price_list_timestamps": sorted(
                    {
                        str(row["price_list_timestamp"])
                        for row in records
                        if row.get("price_list_timestamp") is not None
                    }
                ),
            },
        },
        "limitations": [
            "Metrics with n=0 are unavailable, not zero performance.",
            "Clinical interpretation requires independently adjudicated gold annotations.",
        ],
    }

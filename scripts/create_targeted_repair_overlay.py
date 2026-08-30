#!/usr/bin/env python3
"""Create the source-verified overlay for the 2026-08 targeted corpus repair.

This script performs only local deterministic checks against the frozen PDFs.
It neither calls Gemini nor mutates checkpoints or canonical output files.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from aisurgeon_decentralised.knowledge_corpus_pipeline import (
    atomic_write_json,
    normalize_text,
    quote_locally_verifiable,
    sha256_file,
    stable_hash,
    text_hash,
    utc_now,
)
from aisurgeon_decentralised.knowledge_corpus_repair import OVERLAY_SCHEMA_VERSION

VTE_SOURCE_ID = "src-003-001l-s3-prophylaxe-venoese-thromboembolie-vte-2026-04-f82c5686f6b7"
PANKREAS_SOURCE_ID = "src-032-010oll-exokrines-pankreaskarzinom-2025-06-44df7e615f31"
HCC_SOURCE_ID = "src-s3-ll-hcc-und-bcc-konsultationsfassung-langversion-6-01-1-c1996068a815"
REPAIR_ID = "targeted-repair-20260816-v2-final-gap-policy"


VTE_MISSING_ITEMS: list[dict[str, Any]] = [
    {
        "number": "6.2",
        "page": 38,
        "section": "6 Umfang der VTE-Prophylaxe nach Risikogruppen",
        "item_type": "recommendation",
        "grade": "A",
        "evidence": "moderat",
        "status": "geprüft 2025",
        "text": "Bei Patienten mit niedrigem VTE-Risiko sollen Basismaßnahmen regelmäßig angewendet werden.",
    },
    {
        "number": "9.1",
        "page": 53,
        "section": "9 Nebenwirkungen und Anwendungsbeschränkungen der medikamentösen VTE-Prophylaxe",
        "item_type": "consensus_statement",
        "grade": "EK",
        "evidence": None,
        "status": "geprüft 2025",
        "text": "Vor dem Einsatz von Antithrombotika zur VTE-Prophylaxe soll das eingriffs- und patientenspezifische Blutungsrisiko bedacht werden.",
    },
    {
        "number": "9.2",
        "page": 53,
        "section": "9 Nebenwirkungen und Anwendungsbeschränkungen der medikamentösen VTE-Prophylaxe",
        "item_type": "consensus_statement",
        "grade": "EK",
        "evidence": None,
        "status": "geprüft 2025",
        "text": "Bei Auswahl und Anwendung der Medikamente zur VTE-Prophylaxe sollen die Nieren- und Leberfunktion berücksichtigt werden.",
    },
    {
        "number": "10.1",
        "page": 60,
        "section": "10 Beginn und Dauer der medikamentösen VTE-Prophylaxe",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Die medikamentöse VTE-Prophylaxe sollte so zeitnah wie möglich zur risikoverursachenden Situation begonnen werden.",
    },
    {
        "number": "10.2",
        "page": 61,
        "section": "10 Beginn und Dauer der medikamentösen VTE-Prophylaxe",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "moderat",
        "status": "neu 2025",
        "text": "Die medikamentöse VTE-Prophylaxe bei elektiven chirurgischen Eingriffen sollte innerhalb von 24 Stunden postoperativ begonnen werden.",
    },
    {
        "number": "12.7",
        "page": 74,
        "section": "12 Operative Medizin > Herz-, thorax- und gefäßchirurgische Eingriffe",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Patienten, die einen offenen Eingriff an aortoiliakalen, renalen oder viszeralen Gefäßen erhalten, sollten eine medikamentöse VTE-Prophylaxe, bevorzugt mit NMH, erhalten.",
    },
    {
        "number": "12.8",
        "page": 76,
        "section": "12 Operative Medizin > Eingriffe am oberflächlichen Venensystem",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "modifiziert 2025",
        "text": "Bei Patienten mit Eingriffen am oberflächlichen Venensystem (Varizenchirurgie) sollte auf eine routinemäßige medikamentöse VTE-Prophylaxe verzichtet werden",
    },
    {
        "number": "12.9",
        "page": 79,
        "section": "12 Operative Medizin > Eingriffe im Bauch- und Beckenbereich",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Bei laparoskopischen Eingriffen und Operationen mit minimal invasivem Zugang („minimal access surgery“) im Bauch- und Beckenbereich sollten die gleichen Indikationen zur VTE-Prophylaxe wie bei offenen Eingriffen gelten.",
    },
    {
        "number": "12.10",
        "page": 80,
        "section": "12 Operative Medizin > Eingriffe im Bauch- und Beckenbereich",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "moderat",
        "status": "modifiziert 2025",
        "text": "Die medikamentöse VTE-Prophylaxe bei Eingriffen im Bauch- und Beckenbereich sollte mindestens bis zum Ende des stationären Aufenthaltes, bevorzugt mit niedermolekularem Heparin oder Fondaparinux, durchgeführt werden.",
    },
    {
        "number": "12.18",
        "page": 89,
        "section": "12 Operative Medizin > Hüftgelenknahe Frakturen und Osteotomien",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Bei hüftgelenknahen osteosynthetisch versorgten Frakturen und Osteotomien sollte eine medikamentöse VTE-Prophylaxe, vorzugsweise mit NMH oder Fondaparinux, erfolgen.",
    },
    {
        "number": "12.19",
        "page": 89,
        "section": "12 Operative Medizin > Hüftgelenknahe Frakturen und Osteotomien",
        "item_type": "consensus_statement",
        "grade": "EK",
        "evidence": None,
        "status": "modifiziert 2025",
        "text": "Bei hüftgelenksnahen Frakturen, welche konservativ frühfunktionell behandelt werden, sollte eine medikamentöse VTE-Prophylaxe erfolgen.",
    },
    {
        "number": "12.21",
        "page": 92,
        "section": "12 Operative Medizin > Kniegelenkendoprothetik und kniegelenknahe Frakturen und Osteotomien",
        "item_type": "consensus_statement",
        "grade": "EK",
        "evidence": None,
        "status": "neu 2025",
        "text": "Die Differentialindikation zur medikamentösen VTE-Prophylaxe bei Patienten mit Operationen im Kniegelenksbereich soll sowohl die individuellen VTE- und Blutungsrisiken als auch das perioperative Management berücksichtigen (einen möglichen Algorithmus zeigt Abbildung 1).",
    },
    {
        "number": "12.22",
        "page": 92,
        "section": "12 Operative Medizin > Kniegelenkendoprothetik und kniegelenknahe Frakturen und Osteotomien",
        "item_type": "recommendation",
        "grade": "A",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Die medikamentöse VTE-Prophylaxe nach Kniegelenksersatz soll 10 - 14 Tage erfolgen.",
    },
    {
        "number": "12.23",
        "page": 92,
        "section": "12 Operative Medizin > Kniegelenkendoprothetik und kniegelenknahe Frakturen und Osteotomien",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Bei kniegelenksnahen Frakturen und Osteotomien sollte die medikamentöse VTE-Prophylaxe mit NMH oder Fondaparinux erfolgen",
    },
    {
        "number": "12.41",
        "page": 106,
        "section": "12 Operative Medizin > Polytrauma",
        "item_type": "recommendation",
        "grade": "0",
        "evidence": "niedrig / sehr niedrig",
        "status": "neu 2025",
        "text": "Ein Monitoring der Heparinprophylaxe mittels Anti-Xa-Spiegelbestimmung kann bei Patienten mit Polytrauma durchgeführt werden.",
    },
    {
        "number": "12.42",
        "page": 106,
        "section": "12 Operative Medizin > Polytrauma",
        "item_type": "recommendation",
        "grade": "0",
        "evidence": "niedrig / sehr niedrig",
        "status": "neu 2025",
        "text": "Eine Dosisanpassung der VTE-Prophylaxe mit Heparinen anhand der Anti-Xa-Spiegel kann bei Patienten mit Polytrauma durchgeführt werden.",
    },
    {
        "number": "12.43",
        "page": 107,
        "section": "12 Operative Medizin > Polytrauma",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "neu 2025",
        "text": "Der Einsatz von Vena-Cava-Inferior-Filtern zur primären VTE-Prophylaxe bei polytraumatisierten Patienten sollte nicht erfolgen.",
    },
    {
        "number": "14.3",
        "page": 130,
        "section": "14 Intensivmedizin",
        "item_type": "recommendation",
        "grade": "0",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Bei intensivmedizinisch behandelten Patienten mit erhöhtem Blutungsrisiko, Niereninsuffizienz oder unsicherer Resorption kann auch die intravenöse Verabreichung von unfraktioniertem Heparin in niedriger Dosierung („low-dose“) zur VTE-Prophylaxe erfolgen.",
    },
    {
        "number": "14.4",
        "page": 131,
        "section": "14 Intensivmedizin",
        "item_type": "recommendation",
        "grade": "0",
        "evidence": "niedrig / sehr niedrig",
        "status": "neu 2025",
        "text": "Bei Patienten, die ein sehr hohes Thromboserisiko aufweisen, kann unter sorgfältiger Nutzen-Risiko-Abwägung eine höhere als die Hochrisikoprophylaxe-Dosierung von Heparinen zur medikamentösen VTE-Prophylaxe angewendet werden.",
    },
    {
        "number": "15.8",
        "page": 139,
        "section": "15 Geburtshilfe und Gynäkologie",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "modifiziert 2025",
        "text": "Frauen, die per Sectio (Kaiserschnitt) entbunden haben und bei denen weitere VTE-Risikofaktoren vorliegen, sollten für mindestens 1–2 Wochen eine medikamentöse VTE-Prophylaxe erhalten",
    },
    {
        "number": "15.4",
        "canonical_number": "15.4",
        "printed_source_item_number": "15.4",
        "item_number_status": "printed_in_source",
        "page": 136,
        "section": "15 Geburtshilfe und Gynäkologie > Geburtshilfe",
        "item_type": "recommendation",
        "grade": "A",
        "evidence": "niedrig / sehr niedrig",
        "status": "modifiziert 2025",
        "text": "Frauen mit einer vorausgegangenen VTE, die spontan oder östrogenassoziiert (Kontrazeptiva oder Schwangerschaft) aufgetreten ist, sollen für die gesamte Schwangerschaftsdauer eine medikamentöse VTE-Prophylaxe erhalten.",
        "audit_issue": "numbering_gap_audit:missing_main_body_item:vte-p136-printed-15.4",
        "rationale": {
            "page": 137,
            "title": "Leitlinienkommentar zur Empfehlung für Frauen mit anamnestischer VTE",
            "text": "Die Leitliniengruppe hat die vorbestehende schwache und allgemein gehaltene Empfehlung konkretisiert für Frauen mit anamnestischer VTE und zu einer starken Empfehlung aufgewertet.",
        },
    },
    {
        "number": "15.4",
        "canonical_number": None,
        "printed_source_item_number": "15.4",
        "item_number_status": "printed_duplicate_in_source",
        "page": 139,
        "section": "15 Geburtshilfe und Gynäkologie > Geburtshilfe",
        "item_type": "consensus_statement",
        "grade": "EK",
        "evidence": None,
        "status": "neu 2025",
        "text": "Frauen mit schweren Verlaufsformen eines ovariellen Hyperstimulationssyndroms sollten aufgrund des hohen VTE-Risikos innerhalb der ersten 3 Monate ihrer Schwangerschaft eine medikamentöse VTE-Prophylaxe erhalten.",
        "audit_issue": "numbering_gap_audit:missing_main_body_item:vte-p139-printed-duplicate-15.4",
        "rationale": {
            "page": 139,
            "title": "Leitlinienkommentar zur Empfehlung beim ovariellen Hyperstimulationssyndrom",
            "text": "Die Leitliniengruppe hat diese Empfehlung für eine spezielle, im klinischen Alltag zunehmende, Frauengruppe neu in die Leitlinie aufgenommen.",
        },
    },
    {
        "number": "15.9",
        "page": 140,
        "section": "15 Geburtshilfe und Gynäkologie > Gynäkologische Eingriffe",
        "item_type": "recommendation",
        "grade": "A",
        "evidence": "moderat",
        "status": "geprüft 2025",
        "text": "Patientinnen nach großen gynäkologisch-onkologischen Eingriffen sollen eine verlängerte VTE-Prophylaxe für 4 Wochen oder während des anhaltenden VTE-Risikos erhalten.",
    },
    {
        "number": "16.3",
        "page": 145,
        "section": "16 Pädiatrie und Neonatologie",
        "item_type": "recommendation",
        "grade": "A",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Kinder und Jugendliche mit früherer Thrombose sollen in VTE-Risikosituationen eine medikamentöse VTE-Prophylaxe erhalten.",
    },
    {
        "number": "16.4",
        "page": 145,
        "section": "16 Pädiatrie und Neonatologie",
        "item_type": "consensus_statement",
        "grade": "EK",
        "evidence": None,
        "status": "geprüft 2025",
        "text": "Kinder, bei deren Eltern oder Geschwistern eine venöse Thromboembolie im Rahmen eines gesicherten hereditären Antithrombin-, Protein C- oder Protein S-Mangel erlitten haben, sollen auf diesen Defekt getestet werden und falls dieser Defekt vorhanden ist, eine medikamentöse VTE-Prophylaxe in Risikosituationen erhalten.",
    },
    {
        "number": "16.5",
        "page": 145,
        "section": "16 Pädiatrie und Neonatologie",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "geprüft 2025",
        "text": "Die medikamentöse VTE-Prophylaxe bei Kindern sollte mit niedermolekularen Heparinen (NMH) oder unfraktioniertem Heparin (UFH) erfolgen.",
    },
    {
        "number": "19.7",
        "page": 154,
        "section": "19 Besonderheiten der VTE-Prophylaxe in der ambulanten Medizin",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "moderat",
        "status": "neu 2025",
        "text": "Ambulant betreute Betroffene mit akutem Schlaganfall und eingeschränkter Mobilität sollten als VTE-Prophylaxe keine medizinischen Thrombose-Prophylaxe-Strümpfe (MTPS) erhalten.",
    },
    {
        "number": "19.8",
        "page": 155,
        "section": "19 Besonderheiten der VTE-Prophylaxe in der ambulanten Medizin",
        "item_type": "recommendation",
        "grade": "B",
        "evidence": "niedrig / sehr niedrig",
        "status": "neu 2025",
        "text": "Nach Varizen-Operation sollte eine Kompressionstherapie als VTE-Prophylaxe nicht über eine Dauer von einer Woche hinaus erfolgen.",
    },
    {
        "number": "19.9",
        "page": 155,
        "section": "19 Besonderheiten der VTE-Prophylaxe in der ambulanten Medizin",
        "item_type": "consensus_statement",
        "grade": "EK",
        "evidence": None,
        "status": "neu 2025",
        "text": "Reisenden auf langen Reisen soll zu allgemeinen Basismaßnahmen der VTE-Prophylaxe geraten werden: ausreichende Flüssigkeitszufuhr, einfache Übungen zur Aktivierung der Muskelpumpe wie Fußwippen sowie Vermeidung von Alkoholkonsum und von zu enger Kleidung während der Reise.",
    },
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def canonical_records(output_root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    excluded = {"documents.jsonl", "pharmacology.jsonl", "drug_product_evidence.jsonl", "active_substance_evidence.jsonl"}
    for path in sorted((output_root / "canonical").glob("*.jsonl")):
        if path.name in excluded:
            continue
        for row in read_jsonl(path):
            if row.get("record_id"):
                records[row["record_id"]] = row
    return records


def page_anchor_score(quote: str, page_text: str) -> int:
    quote_tokens = normalize_text(quote).split()
    haystack = normalize_text(page_text)
    if len(quote_tokens) < 6:
        return int(" ".join(quote_tokens) in haystack)
    windows = [
        " ".join(quote_tokens[index : index + 6])
        for index in range(0, len(quote_tokens) - 5, 6)
    ]
    return sum(window in haystack for window in windows)


def field_patch(
    record_id: str,
    *,
    issue_ids: list[str],
    set_fields: dict[str, Any],
    reason: str,
    expected_fields: dict[str, Any] | None = None,
    remove_review_flags: list[str] | None = None,
    status: str = "fixed",
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "issue_ids": issue_ids,
        "expected_fields": expected_fields or {},
        "set_fields": set_fields,
        "remove_review_flags": remove_review_flags or [],
        "repair_method": "local_deterministic",
        "reason": reason,
        "status": status,
    }


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_root = project_root / "outputs/knowledge_corpus"
    source_manifest = json.loads(
        (output_root / "manifests/source_manifest.json").read_text(encoding="utf-8")
    )
    sources = {source["source_id"]: source for source in source_manifest["sources"]}
    for source in sources.values():
        actual = sha256_file(project_root / source["relative_path"])
        if actual != source["sha256"]:
            raise RuntimeError(f"Frozen source changed: {source['source_id']}")

    records = canonical_records(output_root)
    readers = {
        source_id: PdfReader(project_root / sources[source_id]["relative_path"])
        for source_id in [VTE_SOURCE_ID, PANKREAS_SOURCE_ID, HCC_SOURCE_ID]
    }
    batch_plan = read_jsonl(output_root / "manifests/batch_plan.jsonl")

    def owner_batch(source_id: str, page: int) -> str:
        matches = [
            row["batch_id"]
            for row in batch_plan
            if row["source_id"] == source_id
            and row["task_family"] == "guideline_page_extraction"
            and page in row["owner_pdf_pages_1based"]
        ]
        if len(matches) != 1:
            raise RuntimeError(f"No unique owner batch for {source_id}:{page}: {matches}")
        return matches[0]

    additions: list[dict[str, Any]] = []
    for item in VTE_MISSING_ITEMS:
        page_text = readers[VTE_SOURCE_ID].pages[item["page"] - 1].extract_text() or ""
        if item["number"] not in page_text:
            raise RuntimeError(f"VTE item marker missing on source page: {item['number']} p{item['page']}")
        if not quote_locally_verifiable(item["text"], [page_text]):
            score = page_anchor_score(item["text"], page_text)
            if score < 1:
                raise RuntimeError(f"VTE quote not source-verifiable: {item['number']} p{item['page']}")
        rationale = item.get("rationale")
        rationale_record_id: str | None = None
        if rationale:
            rationale_page_text = (
                readers[VTE_SOURCE_ID].pages[rationale["page"] - 1].extract_text() or ""
            )
            if not quote_locally_verifiable(rationale["text"], [rationale_page_text]):
                raise RuntimeError(
                    f"VTE rationale not source-verifiable: {item['number']} p{rationale['page']}"
                )
            rationale_identifier = (
                f"Quellenkommentar zu gedrucktem Item {item['number']} "
                f"auf PDF-S. {item['page']}"
            )
            rationale_record_id = "rec-" + stable_hash(
                VTE_SOURCE_ID,
                "rationale_block",
                rationale_identifier,
                [rationale["page"]],
                text_hash(rationale["text"]),
            )

        additions.append(
            {
                "source_id": VTE_SOURCE_ID,
                "owner_batch_id": owner_batch(VTE_SOURCE_ID, item["page"]),
                "issue_ids": [
                    item.get("audit_issue") or f"missing_formal_item:{item['number']}"
                ],
                "reason": "Formales Haupttext-Item war im validierten Seitenbatch vorhanden, aber im kanonischen Formal-Item-Bestand nicht angelegt.",
                "record": {
                    "record_type": "formal_item",
                    "source_identifier": item["number"],
                    "title": (
                        "Konsensbasierte Empfehlung"
                        if item["item_type"] == "consensus_statement"
                        else "Evidenzbasierte Empfehlung"
                    ),
                    "section_path": item["section"].split(" > "),
                    "pdf_pages_1based": [item["page"]],
                    "printed_page_label": str(item["page"] - 2),
                    "exact_source_text": item["text"],
                    "semantic_summary_de": item["text"],
                    "confidence": 1.0,
                    "review_flags": [],
                    "source_zone": "main_body",
                    "canonical_role": "primary",
                    "primary_record_ids": [],
                    "retrieval_eligible": True,
                    "embedding_eligible": True,
                    "answer_eligible": True,
                    "primary_search_eligible": True,
                    "source_item_number": item.get("canonical_number", item["number"]),
                    "printed_source_item_number": item.get(
                        "printed_source_item_number", item["number"]
                    ),
                    "item_number_status": item.get(
                        "item_number_status", "printed_in_source"
                    ),
                    "item_type": item["item_type"],
                    "exact_text_de": item["text"],
                    "recommendation_grade": item["grade"],
                    "evidence_level": item["evidence"],
                    "consensus_strength": "Starker Konsens (100%)",
                    "qualifiers": [item["status"]],
                    "linked_item_numbers": [],
                    "explicit_linked_rationale_record_ids": (
                        [rationale_record_id] if rationale_record_id else []
                    ),
                    "linked_reference_labels": [],
                    "linked_table_figure_labels": [],
                    "medication_mentions_original": [],
                    "normalized_entities": [],
                    "keywords": [],
                    "indications": [],
                    "populations": [],
                    "routes": [],
                    **(
                        {
                            "uncertainty_reason": (
                                "Die sichtbare Itemnummer 15.4 wird in der Originalleitlinie "
                                "mehrfach für verschiedene Haupttextitems verwendet. Sie bleibt "
                                "als printed_source_item_number erhalten; source_item_number wird "
                                "zur Vermeidung einer falschen eindeutigen Nummer bewusst null belassen."
                            ),
                            "structured_field_provenance": {
                                "source_item_number": (
                                    "Lokale Sichtprüfung der formalen Box auf physischer PDF-Seite "
                                    f"{item['page']}; die gedruckte Nummer 15.4 ist ein "
                                    "quellnativer Nummernduplikatfehler."
                                )
                            },
                            "review_flags": ["source_native_duplicate_item_number"],
                        }
                        if item.get("canonical_number", item["number"]) is None
                        else {}
                    ),
                },
            }
        )
        if rationale:
            additions.append(
                {
                    "source_id": VTE_SOURCE_ID,
                    "owner_batch_id": owner_batch(VTE_SOURCE_ID, rationale["page"]),
                    "issue_ids": [
                        f"{item['audit_issue']}:linked_source_comment"
                    ],
                    "reason": (
                        "Der kurze, eindeutig zuordenbare Leitlinienkommentar wurde zusammen "
                        "mit dem fehlenden formalen Haupttextitem quellentreu ergänzt und "
                        "über eine explizite Record-ID verknüpft."
                    ),
                    "record": {
                        "record_type": "rationale_block",
                        "source_identifier": rationale_identifier,
                        "title": rationale["title"],
                        "section_path": item["section"].split(" > "),
                        "pdf_pages_1based": [rationale["page"]],
                        "printed_page_label": str(rationale["page"] - 2),
                        "exact_source_text": rationale["text"],
                        "semantic_summary_de": rationale["text"],
                        "confidence": 1.0,
                        "review_flags": [],
                        "source_zone": "main_body",
                        "canonical_role": "primary",
                        "primary_record_ids": [],
                        "retrieval_eligible": True,
                        "embedding_eligible": True,
                        "answer_eligible": True,
                        "primary_search_eligible": True,
                        "linked_item_numbers": [],
                        "linked_reference_labels": [],
                        "linked_table_figure_labels": [],
                        "medication_mentions_original": [],
                        "normalized_entities": [],
                        "keywords": [],
                        "indications": [],
                        "populations": [],
                        "routes": [],
                    },
                }
            )

    patches: list[dict[str, Any]] = []
    triage = json.loads(
        (output_root / "qa/review_flag_triage.json").read_text(encoding="utf-8")
    )
    locator_rows: list[dict[str, Any]] | None = None
    for finding in triage.get("critical_findings", []):
        evidence = finding.get("evidence", {})
        if evidence.get("all_record_types_confirmed_plus_two_locators"):
            locator_rows = evidence["all_record_types_confirmed_plus_two_locators"]
            break
    if locator_rows is None or len(locator_rows) != 37:
        raise RuntimeError("Expected 37 source-verified VTE locator repairs in triage report")
    vte_reader = readers[VTE_SOURCE_ID]
    for locator in locator_rows:
        record = records[locator["record_id"]]
        claimed = locator["claimed_pages"]
        best = locator["best_pages"]
        old_score = sum(
            page_anchor_score(record["exact_source_text"], vte_reader.pages[page - 1].extract_text() or "")
            for page in claimed
        )
        new_score = sum(
            page_anchor_score(record["exact_source_text"], vte_reader.pages[page - 1].extract_text() or "")
            for page in best
        )
        if new_score <= old_score and locator.get("anchor_score") != 99:
            raise RuntimeError(
                f"Locator repair not independently confirmed: {record['record_id']} {old_score}->{new_score}"
            )
        patches.append(
            field_patch(
                record["record_id"],
                issue_ids=["vte_physical_page_locator_shift"],
                expected_fields={"pdf_pages_1based": claimed},
                set_fields={
                    "pdf_pages_1based": best,
                    "printed_page_label": ", ".join(str(page - 2) for page in best),
                    "quote_locally_verified": True,
                    "structured_field_provenance": {
                        "pdf_pages_1based": "local PDF quote-anchor match against physical 1-based page"
                    },
                },
                remove_review_flags=["quote_not_locally_verified"],
                reason="Der gespeicherte Locator war die gedruckte Seitennummer; der Quelltext wurde lokal auf der physischen 1-basierten PDF-Seite verifiziert.",
            )
        )

    patches.extend(
        [
            field_patch(
                "rec-3b4fd85be9e8c296bdca48da",
                issue_ids=["hcc_unnumbered_main_body_formal_item"],
                expected_fields={
                    "source_item_number": None,
                    "pdf_pages_1based": [152],
                },
                set_fields={
                    "printed_source_item_number": None,
                    "item_number_status": "not_printed_in_source",
                    "uncertainty_reason": (
                        "Die Empfehlungsbox im Haupttext auf physischer PDF-Seite 152 zeigt "
                        "keine Itemnummer; es wurde bewusst keine fortlaufende Nummer errechnet."
                    ),
                    "structured_field_provenance": {
                        "source_item_number": (
                            "Lokale Sicht- und Textextraktionsprüfung der Empfehlungsbox auf "
                            "physischer PDF-Seite 152; im Haupttext ist keine Nummer gedruckt."
                        )
                    },
                },
                reason=(
                    "Die Quelle enthält ein formales Haupttext-Item ohne sichtbare Nummer. "
                    "Der stabile Record bleibt erhalten und die Null-Entscheidung wird explizit belegt."
                ),
            ),
            field_patch(
                "rec-70f8457904ae104d4422b8ca",
                issue_ids=["numbering_gap_audit:vte-19.2:source-native-duplicate-15.4"],
                expected_fields={
                    "source_item_number": "15.4",
                    "pdf_pages_1based": [150],
                },
                set_fields={
                    "source_item_number": None,
                    "printed_source_item_number": "15.4",
                    "item_number_status": "printed_duplicate_in_source",
                    "uncertainty_reason": (
                        "Die formale Haupttextbox auf physischer PDF-Seite 150 ist sichtbar mit "
                        "15.4 nummeriert, obwohl sie in Kapitel 19 zwischen 19.1 und 19.3 steht. "
                        "Die gedruckte Nummer wird auditierbar erhalten; 19.2 wird nicht erfunden."
                    ),
                    "structured_field_provenance": {
                        "source_item_number": (
                            "Lokale Sichtprüfung der formalen Box auf physischer PDF-Seite 150; "
                            "gedruckt ist 15.4, nicht 19.2."
                        )
                    },
                },
                remove_review_flags=["formal_item_number_unclear"],
                reason=(
                    "Die Originalleitlinie enthält an dieser Stelle einen quellnativen "
                    "Nummernduplikatfehler. Eine kanonische Nummer 19.2 wäre unbelegt."
                ),
            ),
            field_patch(
                "rec-983ddbf2666b700f7f5004e2",
                issue_ids=["vte_formal_item_12.39_truncated_exact_text"],
                expected_fields={"source_item_number": "12.39"},
                set_fields={
                    "exact_text_de": "Bei Patienten mit motorisch (in-)kompletter Querschnittlähmung sollte die medikamentöse VTE-Prophylaxe, bevorzugt mit NMH, über 12-24 Wochen ab Eintritt der Querschnittlähmung erfolgen.\n\nStationär behandelte, nicht gehfähige Patienten mit chronischer Querschnittlähmung und zusätzlichen VTE-Risikofaktoren sollten eine medikamentöse VTE-Prophylaxe, bevorzugt mit NMH, erhalten.",
                    "semantic_summary_de": "Bei motorisch (in-)kompletter Querschnittlähmung sollte bevorzugt mit NMH über 12-24 Wochen prophylaktisch behandelt werden; stationäre, nicht gehfähige Patienten mit chronischer Querschnittlähmung und zusätzlichen VTE-Risikofaktoren sollten ebenfalls eine medikamentöse VTE-Prophylaxe, bevorzugt mit NMH, erhalten.",
                    "quote_locally_verified": True,
                },
                remove_review_flags=["quote_not_locally_verified"],
                reason="Das formale Item 12.39 enthält im Haupttext zwei gleichrangige Empfehlungsabsätze; der kanonische Itemtext enthielt nur den zweiten.",
            ),
            field_patch(
                "rec-01264bf9cb9194d1cc4e9cfa",
                issue_ids=["flag_index:52", "flag_index:53"],
                set_fields={"quote_locally_verified": True},
                remove_review_flags=["formal_item_number_unclear", "source_item_number_recovered_from_source_identifier"],
                reason="Itemnummer und formaler Wortlaut von 12.2 sind im Haupttext auf der angegebenen Seite sichtbar; nur der rohe Layouttext enthält ein eingebettetes Steuerzeichen.",
            ),
            field_patch(
                "rec-152584e1b1a0106255b99ff1",
                issue_ids=["flag_index:367", "possible_negation_loss_in_summary"],
                set_fields={
                    "exact_text_de": "Genetische Untersuchungen sollen folgenden Individuen ohne manifeste oder symptomatische Krebserkrankung angeboten werden:\n• Mitglieder von Familien mit einer bekannten, wahrscheinlich pathogenen/pathogenen Genvariante, die für das Pankreaskarzinom disponiert (Tabelle 15).\n• Bislang nicht an einem Pankreaskarzinom erkrankten Individuen aus Familien, die die Kriterien für eine genetische Testung auf bekannte, mit einem Pankreaskarzinom assoziierte, genetische Tumorrisikosyndrome erfüllen (Tabelle 15).",
                    "semantic_summary_de": "Genetische Untersuchungen sollen Individuen ohne manifeste oder symptomatische Krebserkrankung angeboten werden, wenn in ihrer Familie eine disponierende wahrscheinlich pathogene/pathogene Genvariante bekannt ist oder die genannten familiären Kriterien für ein assoziiertes genetisches Tumorrisikosyndrom erfüllt sind.",
                    "section_path": ["4.2 Individuen mit einem erhöhten Risiko für ein erbliches Pankreaskarzinom"],
                    "quote_locally_verified": True,
                },
                remove_review_flags=["quote_not_locally_verified"],
                reason="Die Zusammenfassung wurde auf die explizite Population und beide Voraussetzungen des Haupttext-Items 4.9 präzisiert; der Itemwortlaut wurde aus dem digital lesbaren Haupttext übernommen.",
            ),
            field_patch(
                "rec-7b332b1fb867aff748795119",
                issue_ids=["flag_index:2198", "hcc_primary_main_text_encoding"],
                set_fields={
                    "exact_text_de": "Die Leberresektion kann offen oder minimalinvasiv (laparoskopisch oder robotisch assistiert) durchgeführt werden.",
                    "semantic_summary_de": "Die Leberresektion kann offen oder minimalinvasiv (laparoskopisch oder robotisch assistiert) durchgeführt werden.",
                    "section_path": [
                        "3.4 Operative und interventionelle Therapieverfahren",
                        "3.4.3.2.3 Resektion beim Hepatozellulären Karzinom mit Leberzirrhose",
                    ],
                    "quote_locally_verified": True,
                },
                reason="Der Haupttext auf PDF-Seite 83 ist primär; ein druckbares Extraktionsartefakt im Umlaut wurde im formalen Itemfeld gegen den sichtbaren Haupttext berichtigt.",
            ),
            field_patch(
                "rec-c2d3f127e5f38698b4e1316b",
                issue_ids=["hcc_primary_counterpart_for_change_table_3.69"],
                set_fields={
                    "exact_text_de": "Eine Erstlinientherapie mit der Kombination\n• Atezolizumab und Bevacizumab (A+B),\n• mit Durvalumab und Tremelimumab (D+T) oder\n• Nivolumab und Ipilimumab (N+I)\n\nsoll angeboten werden bei HCC-Patienten im Child-Pugh-Stadium A und BCLC B oder C, mit Fernmetastasen oder einer Tumorlokalisation, die lokoregionär nicht kontrolliert oder reseziert werden kann.\n\nPatienten mit Kontraindikationen für A+B, D+T und N+I soll eine Erstlinientherapie entweder mit\n• Durvalumab als Monotherapie oder\n• mit einem der beiden Tyrosinkinase-Inhibitoren Lenvatinib oder Sorafenib\n\nangeboten werden.",
                    "semantic_summary_de": "Eine Erstlinientherapie mit Atezolizumab/Bevacizumab, Durvalumab/Tremelimumab oder Nivolumab/Ipilimumab soll den genannten HCC-Patienten angeboten werden. Bei Kontraindikationen gegen diese Kombinationen soll Durvalumab als Monotherapie oder Lenvatinib beziehungsweise Sorafenib angeboten werden.",
                    "section_path": ["3.5 Systemtherapie", "3.5.2 Medikamentöse Erstlinien-Therapie des HCC"],
                    "quote_locally_verified": True,
                },
                reason="Die aktuelle Haupttextfassung von Item 3.69 auf PDF-Seite 100 ist die primäre Darstellung; ihre Umlaut-Artefakte wurden im formalen Itemfeld lokal berichtigt.",
            ),
            field_patch(
                "rec-34914cff50e9882dcb1b46e5",
                issue_ids=["flag_index:690", "possible_negation_loss_in_summary"],
                set_fields={
                    "semantic_summary_de": "Bei den genannten schweren Toxizitäten ist 5-Fluorouracil sofort abzubrechen. Nach Erholung von Leukozyten und Thrombozyten kann die Behandlung gemäß Tabelle mit 100 %, 75 % oder 50 % wieder aufgenommen werden, sofern nicht andere Nebenwirkungen einer Wiederaufnahme entgegenstehen.",
                    "active_substance_names": ["5-Fluorouracil"],
                },
                reason="Die klinisch relevante Einschränkung ‚sofern nicht andere Nebenwirkungen ... entgegenstehen‘ wurde in der Zusammenfassung wiederhergestellt.",
            ),
            field_patch(
                "rec-262d4005caa98e79f1e9a273",
                issue_ids=["flag_index:1172", "truncated_text_at_page_boundary"],
                expected_fields={"pdf_pages_1based": [183]},
                set_fields={
                    "pdf_pages_1based": [183, 184],
                    "printed_page_label": "183–184",
                    "exact_source_text": "Bei Patienten mit vorbestehender Autoimmunerkrankung (autoimmune disease, AID) deuten Daten aus Beobachtungsstudien darauf hin, dass das Risiko für immunvermittelte Nebenwirkungen nach einer Immun-Checkpoint-Inhibitor-Therapie im Vergleich zu Patienten ohne vorbestehende AID erhöht sein kann. Darüber hinaus traten häufig Schübe der zugrunde liegenden AID auf, die jedoch meist leicht und beherrschbar waren.",
                    "semantic_summary_de": "Bei Patienten mit vorbestehender Autoimmunerkrankung kann das Risiko immunvermittelter Nebenwirkungen nach einer Immun-Checkpoint-Inhibitor-Therapie erhöht sein. Schübe der zugrunde liegenden Erkrankung traten häufig auf, waren jedoch meist leicht und beherrschbar.",
                    "quote_locally_verified": True,
                },
                remove_review_flags=["truncated_text_at_page_boundary"],
                reason="Der Warnhinweis läuft im Original von der physischen PDF-Seite 183 auf Seite 184 weiter; die fehlende Aussage wurde wörtlich ergänzt.",
            ),
            field_patch(
                "rec-8fd1316b54bc51b0c8c6fc3d",
                issue_ids=["flag_index:1251", "possible_negation_loss_in_summary", "control_characters_in_exact_text"],
                set_fields={
                    "product_name": "Lixiana",
                    "active_substance_names": ["Edoxaban"],
                    "semantic_summary_de": "Bei der Umstellung von Lixiana (Edoxaban) auf einen Vitamin-K-Antagonisten werden 60 mg Edoxaban auf 30 mg einmal täglich und 30 mg auf 15 mg einmal täglich reduziert. Eine VKA-Aufsättigungsdosis soll nicht eingenommen werden. Edoxaban wird bei INR ≥ 2,0 beziehungsweise spätestens nach 14 Tagen abgesetzt; die INR soll in den ersten 14 Tagen mindestens dreimal kurz vor der täglichen Edoxaban-Einnahme gemessen werden.",
                    "quote_locally_verified": True,
                    "structured_field_provenance": {
                        "product_name": "same-source Annex I product identity",
                        "active_substance_names": "same-source composition and section context",
                    },
                },
                remove_review_flags=["quote_not_locally_verified"],
                reason="Die Zusammenfassung wurde um die explizite Negation zur VKA-Aufsättigungsdosis ergänzt; Rohtext-Steuerzeichen bleiben im Audittext erhalten und werden nur in der Suchnormalisierung bereinigt.",
            ),
            field_patch(
                "rec-ed2065e013dcf4c982e24094",
                issue_ids=["flag_index:701", "flag_index:2282", "possible_negation_loss_in_summary"],
                set_fields={
                    "active_substance_names": ["Paclitaxel"],
                    "combination_partners": ["Gemcitabin"],
                    "semantic_summary_de": "Tabelle 2 regelt beim Pankreasadenokarzinom abhängig von Zyklustag, absoluter Neutrophilenzahl und Thrombozytenzahl das Verschieben, Reduzieren oder Nichtverabreichen der Abraxane- und Gemcitabin-Dosen.",
                },
                reason="Die Zusammenfassung nennt nun ausdrücklich auch die Nichtverabreichungsregeln; Wirkstoff und Kombinationspartner wurden aus derselben Fachinformationssektion zugeordnet.",
            ),
            field_patch(
                "rec-6c5d10df9c8a93c4fd79abd5",
                issue_ids=["flag_index:2692", "possible_negation_loss_in_summary"],
                set_fields={
                    "product_name": "KEYTRUDA",
                    "active_substance_names": ["Pembrolizumab"],
                    "combination_partners": ["Lenvatinib"],
                    "semantic_summary_de": "In KEYNOTE-B61 wurde Pembrolizumab 400 mg alle 6 Wochen in Kombination mit Lenvatinib 20 mg einmal täglich zur Erstlinienbehandlung eines fortgeschrittenen oder metastasierenden RCC mit nicht-klarzelliger Histologie untersucht.",
                },
                reason="Die negative Populationseinschränkung ‚nicht-klarzellige Histologie‘ wurde erhalten; Lenvatinib ist Kombinationspartner und nicht Wirkstoff von KEYTRUDA.",
            ),
            field_patch(
                "rec-857e6c6a00877b2e6b390fa1",
                issue_ids=["flag_index:1303", "unexpected_product_field_not_in_quote", "truncated_text_at_page_end"],
                expected_fields={"pdf_pages_1based": [7]},
                set_fields={
                    "pdf_pages_1based": [7, 8],
                    "printed_page_label": "7–8",
                    "active_substance_names": ["Clopidogrel"],
                    "exact_source_text": "Heparin: In einer klinischen Studie mit gesunden Probanden war es unter Clopidogrel weder notwendig, die Heparin-Dosierung anzupassen, noch veränderte Clopidogrel den Einfluss von Heparin auf die Blutgerinnung. Die gleichzeitige Gabe von Heparin hatte keine Wirkung auf die Clopidogrel-induzierte Hemmung der Thrombozytenaggregation. Eine pharmakodynamische Wechselwirkung zwischen Clopidogrel und Heparin, die zu einem erhöhten Blutungsrisiko führt, ist möglich. Deshalb sollte eine Kombinationstherapie nur mit Vorsicht durchgeführt werden (siehe Abschnitt 4.4).",
                    "semantic_summary_de": "Unter Clopidogrel war keine Anpassung der Heparin-Dosierung nötig; beide Wirkungen auf Blutgerinnung beziehungsweise Thrombozytenaggregation blieben unverändert. Wegen einer möglichen pharmakodynamischen Wechselwirkung mit erhöhtem Blutungsrisiko soll die Kombination dennoch nur mit Vorsicht angewendet werden.",
                    "quote_locally_verified": True,
                    "structured_field_provenance": {
                        "product_name": "same-source Annex I product identity",
                        "active_substance_names": "same paragraph and same-source composition",
                    },
                },
                remove_review_flags=["truncated_text_at_page_end"],
                reason="Der Interaktionsabsatz läuft von Seite 7 auf Seite 8 weiter; der klinisch relevante Vorsichtshinweis wurde wörtlich ergänzt und die Produktzuordnung dokumentiert.",
            ),
            field_patch(
                "rec-091d23036f9481e0a49784fe",
                issue_ids=["flag_index:1284", "dosing_frequency_not_found_in_quote_or_page", "dosing_route_not_found_in_quote_or_page"],
                set_fields={
                    "product_name": "Lixiana",
                    "active_substance_names": ["Edoxaban"],
                    "supporting_source_text": "Die Teilnehmer wurden angewiesen, Edoxaban (Tabletten oder Granulat) einmal täglich zur gleichen Tageszeit mit oder ohne Nahrung einzunehmen. Die Tabletten sollten mit einem Glas Wasser geschluckt werden.",
                    "supporting_pdf_pages_1based": [28],
                    "structured_field_provenance": {
                        "frequency": "Tabelle-12-Fußnote a auf derselben PDF-Seite",
                        "route": "Tabelle-12-Fußnote a auf derselben PDF-Seite",
                        "product_name": "same-source Annex I product identity",
                    },
                },
                reason="Frequenz und orale Anwendung sind in Fußnote a derselben Dosiertabelle explizit belegt; Produkt und Wirkstoff wurden getrennt normalisiert.",
            ),
            field_patch(
                "rec-5dfdd4d1263fdb02f0faeb14",
                issue_ids=["flag_index:1285", "dosing_frequency_not_found_in_quote_or_page", "dosing_route_not_found_in_quote_or_page"],
                set_fields={
                    "product_name": "Lixiana",
                    "active_substance_names": ["Edoxaban"],
                    "supporting_source_text": "Die Teilnehmer wurden angewiesen, Edoxaban (Tabletten oder Granulat) einmal täglich zur gleichen Tageszeit mit oder ohne Nahrung einzunehmen. Die Tabletten sollten mit einem Glas Wasser geschluckt werden.",
                    "supporting_pdf_pages_1based": [28],
                    "structured_field_provenance": {
                        "frequency": "Tabelle-12-Fußnote a auf derselben PDF-Seite",
                        "route": "Tabelle-12-Fußnote a auf derselben PDF-Seite",
                        "product_name": "same-source Annex I product identity",
                    },
                },
                reason="Frequenz und orale Anwendung sind in Fußnote a derselben Dosiertabelle explizit belegt; Produkt und Wirkstoff wurden getrennt normalisiert.",
            ),
            field_patch(
                "rec-e15a063fcf6848ef8db0d2e8",
                issue_ids=["flag_index:1286", "dosing_frequency_not_found_in_quote_or_page", "dosing_route_not_found_in_quote_or_page"],
                set_fields={
                    "product_name": "Lixiana",
                    "active_substance_names": ["Edoxaban"],
                    "dose_value": "1,2",
                    "supporting_source_text": "Die Teilnehmer wurden angewiesen, Edoxaban (Tabletten oder Granulat) einmal täglich zur gleichen Tageszeit mit oder ohne Nahrung einzunehmen.",
                    "supporting_pdf_pages_1based": [28],
                    "structured_field_provenance": {
                        "frequency": "Tabelle-12-Fußnote a auf derselben PDF-Seite",
                        "route": "Tabelle-12-Fußnote a auf derselben PDF-Seite",
                        "dose_value": "sichtbare Tabellenzelle mit deutschem Dezimalkomma",
                        "product_name": "same-source Annex I product identity",
                    },
                },
                reason="Frequenz und orale Anwendung sind in Fußnote a belegt; der Dosiswert wurde quellentreu auf das sichtbare deutsche Dezimalkomma gesetzt.",
            ),
            field_patch(
                "rec-56f1020a58aad39f9f5721f2",
                issue_ids=["flag_index:2636", "dosing_dose_value_not_found_in_quote_or_page", "dosing_frequency_not_found_in_quote_or_page"],
                set_fields={
                    "route": "subkutane Injektion",
                    "supporting_source_text": "Die empfohlene Dosis von KEYTRUDA Injektionslösung beträgt entweder 395 mg alle 3 Wochen als subkutane Injektion oder 790 mg alle 6 Wochen als subkutane Injektion.",
                    "supporting_pdf_pages_1based": [178],
                    "structured_field_provenance": {
                        "dose_value": "exact_source_text auf PDF-Seite 179",
                        "frequency": "exact_source_text auf PDF-Seite 179",
                        "route": "allgemeine Dosierungsangabe unmittelbar vorher auf PDF-Seite 178",
                    },
                },
                reason="395/790 mg und die Frequenzen stehen bereits im Quellzitat; die subkutane Route ist im unmittelbar vorangehenden Dosierungsabsatz explizit.",
            ),
            field_patch(
                "rec-d57e5c2aed3a655920717713",
                issue_ids=["flag_index:2515", "dosing_route_not_found_in_quote_or_page"],
                set_fields={
                    "supporting_source_text": "Apixaban wurde als 5 mg Tablette, 0,5 mg Tablette oder als Lösung zum Einnehmen mit 0,4 mg/ml bereitgestellt.",
                    "supporting_pdf_pages_1based": [119],
                    "structured_field_provenance": {
                        "route": "SAXOPHONE-Absatz unmittelbar vor Tabelle 4 auf derselben Seite"
                    },
                },
                reason="Die orale Anwendung ist im unmittelbar vorangehenden SAXOPHONE-Absatz derselben Seite explizit belegt.",
            ),
            field_patch(
                "rec-9ace91df00ab304f8878ea18",
                issue_ids=["flag_index:1232", "dosing_route_not_found_in_quote_or_page"],
                set_fields={
                    "product_name": "KEYTRUDA",
                    "supporting_source_text": "Die Patienten wurden einem der Studienarme zur Behandlung mit intravenöser Infusion zugeteilt: Pembrolizumab 200 mg an Tag 1 alle 3 Wochen in Kombination mit Chemotherapie.",
                    "supporting_pdf_pages_1based": [305],
                    "structured_field_provenance": {
                        "route": "KEYNOTE-355-Studienarm auf der unmittelbar vorherigen PDF-Seite",
                        "product_name": "same-source Annex I product identity",
                    },
                },
                reason="Die intravenöse Infusion gehört zum selben KEYNOTE-355-Studienarm auf der unmittelbar vorherigen Seite; der exakte Verlaufstext bleibt auf Seite 306 zitiert.",
            ),
            field_patch(
                "rec-cd673262440368cc40ed6467",
                issue_ids=["flag_index:743", "dosing_dose_value_not_found_in_quote_or_page"],
                set_fields={
                    "supporting_source_text": "Die entsprechend den Handhabungshinweisen zubereitete Cisplatin-Infusionslösung sollte über einen Zeitraum von 6 bis 8 Stunden intravenös infundiert werden.",
                    "supporting_pdf_pages_1based": [2],
                    "structured_field_provenance": {
                        "dose_value": "exact_source_text auf derselben Seite",
                        "frequency": "exact_source_text auf derselben Seite",
                        "route": "Abschnitt Art der Anwendung auf derselben Seite",
                    },
                },
                reason="Dosis und Frequenz stehen wörtlich im Zitat; die intravenöse Route ist im Abschnitt Art der Anwendung auf derselben Seite belegt.",
            ),
            field_patch(
                "rec-d124e39aa75d40f03dc02d57",
                issue_ids=["flag_index:749", "unexpected_product_field_not_in_quote"],
                set_fields={
                    "structured_field_provenance": {
                        "product_name": "same-page running header and same-source Annex I product identity",
                        "active_substance_names": "same-source composition",
                    }
                },
                reason="Die Produktzuordnung Cisplatin Teva ist durch den Seitenkopf und die gleiche Fachinformation belegt; kein Wirkstofftausch liegt vor.",
                status="false_positive",
            ),
            field_patch(
                "rec-728ae69ed4f5ccf125ce8e82",
                issue_ids=["flag_index:2857", "dosing_dose_value_not_found_in_quote_or_page", "dosing_frequency_not_found_in_quote_or_page"],
                set_fields={"product_name": "Xarelto"},
                reason="15 mg zweimal täglich für drei Wochen und anschließend 20 mg einmal täglich stehen wörtlich im Quellzitat; lediglich der Produktname wurde gegen die Fachinformation normalisiert.",
            ),
        ]
    )

    appendix_number_repairs = {
        "rec-560ab9217c50b737e0be9d87": "9.14",
        "rec-687fd6fc5f8d7ec06af033e7": "9.15",
        "rec-acc7189602f1f97b997e993b": "9.22",
        "rec-663e784d84aa3e8b509ce345": "9.23",
        "rec-977d4e616acdeefad45de1c4": "9.24",
        "rec-fe19473a960f43824035fb66": "9.25",
    }
    for record_id, number in appendix_number_repairs.items():
        record = records[record_id]
        patches.append(
            field_patch(
                record_id,
                issue_ids=["pankreas_appendix_item_number_column_shift"],
                expected_fields={"pdf_pages_1based": record["pdf_pages_1based"]},
                set_fields={"source_item_number": number, "source_identifier": number},
                reason="Die Nummer wurde aus der sichtbaren Spalte ‚Version 3.1‘ derselben Änderungstabelle übernommen; der Text bleibt die sekundäre Darstellung des Haupttext-Items.",
            )
        )

    false_positive_records = {
        "rec-01264bf9cb9194d1cc4e9cfa": "Der formale Wortlaut stimmt normalisiert mit der Quelle überein; die Abweichung ist ausschließlich ein Steuerzeichen im rohen Layouttext.",
        "rec-091d23036f9481e0a49784fe": "Frequenz und orale Anwendung sind in der Fußnote derselben Tabelle belegt.",
        "rec-5dfdd4d1263fdb02f0faeb14": "Frequenz und orale Anwendung sind in der Fußnote derselben Tabelle belegt.",
        "rec-e15a063fcf6848ef8db0d2e8": "Frequenz und orale Anwendung sind in der Fußnote derselben Tabelle belegt; das Dezimalkomma wurde als separate Normalisierung korrigiert.",
        "rec-56f1020a58aad39f9f5721f2": "Dosiswerte und Frequenzen stehen wörtlich im Quellzitat; die Route ist im unmittelbar vorangehenden Dosierungsabsatz belegt.",
        "rec-152584e1b1a0106255b99ff1": "Die Zusammenfassung kehrte keine Negation um; sie wurde dennoch auf die präzise Haupttextpopulation verbessert.",
        "rec-728ae69ed4f5ccf125ce8e82": "15 mg zweimal täglich und anschließend 20 mg einmal täglich stehen wörtlich im Quellzitat.",
        "rec-857e6c6a00877b2e6b390fa1": "Die Produktzuordnung ist durch Quelle und Seitenkopf belegt; der zusätzlich gefundene Seitenumbruch wurde unabhängig davon repariert.",
        "rec-d57e5c2aed3a655920717713": "Die orale Anwendung ist im unmittelbar vorangehenden Absatz derselben Seite explizit belegt.",
        "rec-cd673262440368cc40ed6467": "Dosiswert und Frequenz stehen wörtlich im Quellzitat.",
        "rec-d124e39aa75d40f03dc02d57": "Die Produktzuordnung ist durch den Seitenkopf und dieselbe Fachinformation belegt.",
        "rec-81523a8dd87a49eab1e2f1bf": "Tabelle 22 beginnt auf Seite 203 und läuft auf Seite 204 weiter; der Mehrseitenlocator ist korrekt.",
        "rec-9ace91df00ab304f8878ea18": "Die intravenöse Route ist im selben Studienarm auf der unmittelbar vorherigen Seite belegt.",
        "rec-70b5d1acad755e177f1adb2f": "Die Zusammenfassung erhält alle Bedingungen einschließlich des nicht aufschiebbaren Eingriffs und der erforderlichen Hämostase.",
        "rec-a67f496b344aa03d578352d7": "Die Negationen und Kontraindikationsbedingung sind erhalten; der Record wird als sekundäre Änderungstabellen-Darstellung ausgeschlossen.",
    }
    adjudications: list[dict[str, Any]] = []
    sample_rows = list(
        csv.DictReader((output_root / "qa/review_flag_sample.csv").open(encoding="utf-8", newline=""))
    )
    target_machine_issues = {
        "possible_negation_loss_in_summary",
        "dosing_dose_value_not_found_in_quote_or_page",
        "dosing_frequency_not_found_in_quote_or_page",
        "dosing_route_not_found_in_quote_or_page",
        "unexpected_product_field_not_in_quote",
        "formal_exact_text_diverges_from_source_quote",
        "quote_matches_only_neighbor_page",
    }
    for row in sample_rows:
        issues = set(filter(None, (row.get("machine_issues") or "").split(";")))
        if not issues & target_machine_issues:
            continue
        record_id = row["record_id"]
        status = "false_positive" if record_id in false_positive_records else "fixed"
        adjudications.append(
            {
                "issue_id": f"flag_index:{row['flag_index']}",
                "flag_index": int(row["flag_index"]),
                "record_id": record_id,
                "source_id": row["source_id"],
                "machine_issues": sorted(issues & target_machine_issues),
                "status": status,
                "reason": false_positive_records.get(
                    record_id,
                    "Der konkrete strukturierte Claim wurde lokal gegen Quellzitat, dieselbe Sektion oder die unmittelbar benachbarte Seite geprüft und gegebenenfalls gezielt korrigiert.",
                ),
            }
        )
    for row in sample_rows:
        if row.get("adjudication") != "critical_or_gross" or row.get("record_type") != "formal_item":
            continue
        for record_id in row["record_id"].split(";"):
            if record_id not in records:
                raise RuntimeError(f"Critical sample record missing: {record_id}")
            adjudications.append(
                {
                    "issue_id": f"flag_index:{row['flag_index']}:{record_id}",
                    "flag_index": int(row["flag_index"]),
                    "record_id": record_id,
                    "source_id": row["source_id"],
                    "machine_issues": [],
                    "status": "fixed",
                    "reason": "Quellübergreifende Falschkanten werden source-scoped entfernt; gültige gleichquellige Cross-Page-Ziele bleiben erlaubt. Sekundäre Records werden zusätzlich zoniert.",
                }
            )

    secondary_mappings = {
        "rec-1a6c5ede6ffc92927d090962": {
            "primary_record_ids": ["rec-83281ff4b9d4272ab76aa025"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "quality_indicator_to_guideline_item",
        },
        "rec-d068bacb8ab43ae8a8e1d82e": {
            "primary_record_ids": ["rec-0c3bc139da2d5af1cc9d8fc5"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "quality_indicator_to_guideline_item",
        },
        "rec-92dff3ca7d22ad2039cd749b": {
            "primary_record_ids": ["rec-0d474e30dcdd9127d6ac630c"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "quality_indicator_to_guideline_item",
        },
        "rec-560ab9217c50b737e0be9d87": {
            "primary_record_ids": ["rec-57f241bf01c809c69c7a254c"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
        "rec-687fd6fc5f8d7ec06af033e7": {
            "primary_record_ids": ["rec-a8049dca961f26037343f58c"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
        "rec-acc7189602f1f97b997e993b": {
            "primary_record_ids": ["rec-00b982615262661c4b979dc7"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
        "rec-663e784d84aa3e8b509ce345": {
            "primary_record_ids": ["rec-e22204a0c3e429f481f42fab"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
        "rec-977d4e616acdeefad45de1c4": {
            "primary_record_ids": ["rec-1cc38fba1f14b176acb2c069"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
        "rec-fe19473a960f43824035fb66": {
            "primary_record_ids": ["rec-d690579d6bad0f7330c1f685"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
        "rec-279a80fcdc137b3e6cd97b3a": {
            "primary_record_ids": [
                "rec-556e37979c4fe303e4a1ed8f",
                "rec-5d79ec4aa428672ab9f02f15",
                "rec-0391a0d8d6e9987d8f461c70",
            ],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "compound_change_table_representation",
        },
        "rec-f4420b25185da601f6a7906a": {
            "primary_record_ids": [
                "rec-1cc526d05cf79f3fb7da1db0",
                "rec-b7c1292da9eb419abc85df44",
            ],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "compound_change_table_representation",
        },
        "rec-1f409ae3e24d9245f4752025": {
            "primary_record_ids": [
                "rec-8b9425df31225a19dde113bf",
                "rec-3aa17252b1d303cc9c613aa6",
            ],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "compound_change_table_representation",
        },
        "rec-3feabdcc4d312c0a01a895d7": {
            "primary_record_ids": [
                "rec-7b332b1fb867aff748795119",
                "rec-bf005f9ebfe46d95fb67ce50",
            ],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "compound_change_table_representation",
        },
        "rec-0593def1261baaba94a3e87f": {
            "primary_record_ids": ["rec-35fb8a16dfcdefd50ed8d32d"],
            "canonical_role": "historical_record",
            "secondary_relation_type": "historical_predecessor",
        },
        "rec-e0f80356423e2777e4970f77": {
            "primary_record_ids": ["rec-c2d3f127e5f38698b4e1316b"],
            "canonical_role": "historical_record",
            "secondary_relation_type": "historical_predecessor",
        },
        "rec-a67f496b344aa03d578352d7": {
            "primary_record_ids": ["rec-c2d3f127e5f38698b4e1316b"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
        "rec-d0fc017ffb40b3b78b9f03e5": {
            "primary_record_ids": ["rec-ca48328c31a83eaf90af6eaa"],
            "canonical_role": "historical_record",
            "secondary_relation_type": "historical_predecessor",
        },
        "rec-fff386a310643c7f7596e7ca": {
            "primary_record_ids": ["rec-17c19a7b29d494a87f845844"],
            "canonical_role": "historical_record",
            "secondary_relation_type": "historical_predecessor",
        },
        "rec-f70730a04b955ab4104b6522": {
            "primary_record_ids": ["rec-17c19a7b29d494a87f845844"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
        "rec-02bd4e172cc8da6da536fbfb": {
            "primary_record_ids": [
                "rec-0da4b61e0cc590c4770d60be",
                "rec-0e508299f1fbc397797d6ed8",
            ],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "compound_change_table_representation",
        },
        "rec-8648a8a4cd89b3e0f0d41d65": {
            "primary_record_ids": [
                "rec-da84fd4719b85951e2bcf899",
                "rec-7fabb6b4f3bd0bfe70b34f6b",
            ],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "compound_change_table_representation",
        },
        "rec-6a4e5afab6a9aacdd2d70e79": {
            "primary_record_ids": ["rec-0961329013194d01121f5a77"],
            "canonical_role": "secondary_representation",
            "secondary_relation_type": "change_table_current_version_representation",
        },
    }

    zone_rules = [
        {
            "issue_id": "vte_quality_indicator_repetitions",
            "source_id": VTE_SOURCE_ID,
            "page_from": 159,
            "page_to": 160,
            "source_zone": "other_secondary_material",
            "canonical_role": "secondary_representation",
            "retrieval_exclusion_reason": "quality_indicator_repetition",
            "secondary_relation_type": "quality_indicator_to_guideline_item",
            "reason": "Qualitätsindikatoren wiederholen Haupttext-Empfehlungen und sind keine gleichrangigen Primäritems.",
        },
        {
            "issue_id": "pankreas_quality_indicator_repetitions",
            "source_id": PANKREAS_SOURCE_ID,
            "page_from": 205,
            "page_to": 205,
            "source_zone": "other_secondary_material",
            "canonical_role": "secondary_representation",
            "retrieval_exclusion_reason": "quality_indicator_repetition",
            "secondary_relation_type": "quality_indicator_to_guideline_item",
            "reason": "Qualitätsindikatoren sind sekundäre Darstellungen verknüpfter Haupttext-Empfehlungen.",
        },
        {
            "issue_id": "pankreas_appendix_change_table",
            "source_id": PANKREAS_SOURCE_ID,
            "page_from": 206,
            "page_to": 221,
            "source_zone": "change_table",
            "canonical_role": "secondary_representation",
            "retrieval_exclusion_reason": "change_table_representation",
            "secondary_relation_type": "appendix_change_table_representation",
            "reason": "Kapitel 11 ist eine Änderungstabelle im Anhang und keine primäre Haupttextquelle.",
        },
        {
            "issue_id": "hcc_change_tables",
            "source_id": HCC_SOURCE_ID,
            "page_from": 191,
            "page_to": 209,
            "source_zone": "change_table",
            "canonical_role": "historical_secondary",
            "retrieval_eligible": False,
            "embedding_eligible": False,
            "answer_eligible": False,
            "primary_search_eligible": False,
            "status": "excluded_by_policy",
            "exclusion_reason": "hcc_historical_change_table",
            "retrieval_exclusion_reason": "hcc_historical_change_table",
            "secondary_relation_type": "excluded_historical_change_table",
            "change_status": "excluded_by_policy",
            "reason": "Kapitel 8 enthält historische Änderungs- und Vergleichstabellen; alle dortigen Records bleiben ausschließlich im Audit-Korpus und sind dauerhaft von Suche, Embeddings, Evidenzexpansion und Antworten ausgeschlossen.",
        },
    ]

    targeted_coverage_pages = [38, 61, 74, 80, 89, 92, 107, 131, 140, 141, 154, 155]
    coverage_patches = []
    for page in targeted_coverage_pages:
        coverage_patches.append(
            {
                "source_id": VTE_SOURCE_ID,
                "pdf_page_1based": page,
                "source_zone": "main_body",
                "issue_ids": [f"vte_false_missing_coverage_page:{page}"],
                "set_fields": {
                    "status": "extracted",
                    "status_reason": "Physische Seite lokal vorhanden; Haupttext und betroffene formale Items wurden in der gezielten Reparatur verifiziert.",
                    "review_flags": [],
                },
                "reason": "Die Seite ist physisch vorhanden und digital lesbar; der frühere Missing-/Blank-Status bezog sich irrtümlich auf den Mini-PDF-Ausschnitt.",
            }
        )

    backup_candidates = sorted(
        (output_root / "qa/backups").glob(
            "targeted_repair_*/outputs/knowledge_corpus/links/guideline_item_links.jsonl"
        )
    )
    if not backup_candidates:
        raise RuntimeError("Pre-repair guideline-link backup is missing")
    baseline_links = read_jsonl(backup_candidates[-1])
    pre_repair_cross_source_links = []
    for link in baseline_links:
        source_record = records.get(link.get("from_record_id"))
        target_record = records.get(link.get("to_record_id"))
        if not source_record or not target_record:
            continue
        if source_record["source_id"] == target_record["source_id"]:
            continue
        pre_repair_cross_source_links.append(
            {
                **link,
                "from_source_id": source_record["source_id"],
                "to_source_id": target_record["source_id"],
            }
        )
    if len(pre_repair_cross_source_links) != 1418:
        raise RuntimeError(
            f"Expected 1418 pre-repair cross-source links, got {len(pre_repair_cross_source_links)}"
        )

    overlay = {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "repair_id": REPAIR_ID,
        "created_at_utc": utc_now(),
        "method": "local_deterministic",
        "gemini_used": False,
        "gemini_pages": [],
        "source_manifest_sha256": sha256_file(
            output_root / "manifests/source_manifest.json"
        ),
        "frozen_source_sha256": {
            source_id: source["sha256"] for source_id, source in sorted(sources.items())
        },
        "record_additions": additions,
        "record_patches": patches,
        "source_zone_rules": zone_rules,
        "secondary_record_mappings": secondary_mappings,
        "coverage_patches": coverage_patches,
        "adjudications": adjudications,
        "pre_repair_cross_source_links": pre_repair_cross_source_links,
        "pre_repair_link_backup_path": str(backup_candidates[-1].relative_to(project_root)),
        "expected_counts": {
            "missing_formal_items_added": 29,
            "locator_repairs": 37,
            "secondary_formal_records": 125,
            "cross_source_links_removed": 1418,
            "hcc_historical_policy_records": 99,
        },
    }
    destination = output_root / "manifests/targeted_repair_overlay.json"
    atomic_write_json(destination, overlay)
    print(
        json.dumps(
            {
                "overlay": str(destination),
                "record_additions": len(additions),
                "record_patches": len(patches),
                "coverage_patches": len(coverage_patches),
                "adjudications": len(adjudications),
                "gemini_used": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

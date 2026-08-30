#!/usr/bin/env python3
"""Build the final local numbering-gap audit and representative flag examples.

The script is deliberately deterministic and source-local.  It never calls a
model and refuses to write an audit result unless the expected physical PDF
markers and canonical records are present.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from aisurgeon_decentralised.knowledge_corpus_pipeline import (
    atomic_write_json,
    quote_locally_verifiable,
    sha256_file,
    utc_now,
)
from aisurgeon_decentralised.knowledge_corpus_policy import (
    HCC_HISTORICAL_EXCLUSION_REASON,
    is_primary_use_eligible,
)

VTE_SOURCE_ID = (
    "src-003-001l-s3-prophylaxe-venoese-thromboembolie-vte-2026-04-"
    "f82c5686f6b7"
)
HCC_SOURCE_ID = (
    "src-s3-ll-hcc-und-bcc-konsultationsfassung-langversion-6-01-1-"
    "c1996068a815"
)
HCC_UNNUMBERED_RECORD_ID = "rec-3b4fd85be9e8c296bdca48da"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def canonical_records(output_root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    excluded = {
        "documents.jsonl",
        "pharmacology.jsonl",
        "drug_products.jsonl",
        "active_substances.jsonl",
    }
    for path in sorted((output_root / "canonical").glob("*.jsonl")):
        if path.name in excluded:
            continue
        for row in read_jsonl(path):
            if row.get("record_id"):
                records.setdefault(row["record_id"], row)
    return records


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            if not value.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def page_texts(reader: PdfReader, pages: list[int]) -> dict[int, str]:
    return {page: reader.pages[page - 1].extract_text() or "" for page in pages}


def formal_marker(number: str, texts: dict[int, str]) -> bool:
    pattern = re.compile(
        rf"(?m)^\s*{re.escape(number)}\s+(?:Evidenzbasierte|Konsensbasierte|Statement|Empfehlung)"
    )
    return any(pattern.search(text) for text in texts.values())


def short_exact_excerpt(value: str, limit: int = 420) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[:limit].rstrip() + " […]"


def source_context_identity(
    records: dict[str, dict[str, Any]], source_id: str
) -> tuple[str, str]:
    source_records = [row for row in records.values() if row.get("source_id") == source_id]
    product_candidates = [
        row.get("product_name") or row.get("original_product_name")
        for row in source_records
        if row.get("product_name") or row.get("original_product_name")
    ]
    substance_candidates = [
        name
        for row in source_records
        for name in (
            (row.get("active_substance_names") or [])
            + (row.get("active_substance_original_names") or [])
        )
        if name and "unmittelbaren Quellkontext" not in name
    ]
    product = Counter(product_candidates).most_common(1)[0][0]
    substance = Counter(substance_candidates).most_common(1)[0][0]
    return product, substance


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_root = project_root / "outputs/knowledge_corpus"
    qa_root = output_root / "qa"
    manifest = json.loads(
        (output_root / "manifests/source_manifest.json").read_text(encoding="utf-8")
    )
    sources = {source["source_id"]: source for source in manifest["sources"]}
    for source_id in (VTE_SOURCE_ID, HCC_SOURCE_ID):
        source = sources[source_id]
        if sha256_file(project_root / source["relative_path"]) != source["sha256"]:
            raise RuntimeError(f"Frozen source changed: {source_id}")

    records = canonical_records(output_root)
    formal = [row for row in records.values() if row.get("record_type") == "formal_item"]
    vte_reader = PdfReader(project_root / sources[VTE_SOURCE_ID]["relative_path"])
    hcc_reader = PdfReader(project_root / sources[HCC_SOURCE_ID]["relative_path"])

    # Each window covers at least two physical pages on both sides of the
    # reported sequence break and is small enough to remain a targeted review.
    vte_15_pages = page_texts(vte_reader, list(range(136, 142)))
    vte_19_pages = page_texts(vte_reader, list(range(148, 154)))
    hcc_4_pages = page_texts(hcc_reader, list(range(149, 155)))

    if formal_marker("15.7", vte_15_pages):
        raise RuntimeError("A formal 15.7 marker unexpectedly appeared in the source")
    if formal_marker("19.2", vte_19_pages):
        raise RuntimeError("A formal 19.2 marker unexpectedly appeared in the source")
    if formal_marker("4.29", hcc_4_pages):
        raise RuntimeError("A formal 4.29 marker unexpectedly appeared in the source")
    required_markers = {
        "15.5": vte_15_pages,
        "15.6": vte_15_pages,
        "15.8": vte_15_pages,
        "19.1": vte_19_pages,
        "19.3": vte_19_pages,
        "19.4": vte_19_pages,
        "4.28": hcc_4_pages,
        "4.30": hcc_4_pages,
    }
    for number, texts in required_markers.items():
        if not formal_marker(number, texts):
            raise RuntimeError(f"Expected adjacent formal marker missing: {number}")

    p136_item = next(
        row
        for row in formal
        if row.get("source_id") == VTE_SOURCE_ID
        and row.get("pdf_pages_1based") == [136]
        and row.get("source_item_number") == "15.4"
        and "vorausgegangenen VTE" in (row.get("exact_text_de") or "")
    )
    p139_item = next(
        row
        for row in formal
        if row.get("source_id") == VTE_SOURCE_ID
        and row.get("pdf_pages_1based") == [139]
        and row.get("printed_source_item_number") == "15.4"
        and "Hyperstimulationssyndrom" in (row.get("exact_text_de") or "")
    )
    p150_item = records["rec-70f8457904ae104d4422b8ca"]
    hcc_unnumbered = records[HCC_UNNUMBERED_RECORD_ID]
    for item, reader in (
        (p136_item, vte_reader),
        (p139_item, vte_reader),
        (p150_item, vte_reader),
        (hcc_unnumbered, hcc_reader),
    ):
        texts = [reader.pages[page - 1].extract_text() or "" for page in item["pdf_pages_1based"]]
        if not quote_locally_verifiable(item["exact_text_de"], texts):
            raise RuntimeError(f"Formal item not locally source-verifiable: {item['record_id']}")

    if hcc_unnumbered.get("source_item_number") is not None:
        raise RuntimeError("The visibly unnumbered HCC item was assigned a number")
    if hcc_unnumbered.get("item_number_status") != "not_printed_in_source":
        raise RuntimeError("The HCC unnumbered status is missing")
    if p139_item.get("source_item_number") is not None:
        raise RuntimeError("The duplicated printed VTE 15.4 must not become a second canonical 15.4")
    if p150_item.get("source_item_number") is not None:
        raise RuntimeError("The source-native VTE duplicate on page 150 must remain canonically null")

    gap_reviews = [
        {
            "audit_id": "gap-vte-15-missing-7",
            "source_id": VTE_SOURCE_ID,
            "source_file_name": sources[VTE_SOURCE_ID]["original_file_name"],
            "reported_gap": "15.7",
            "classification": "C",
            "classification_label": "source_native_numbering_gap",
            "physical_pdf_pages_reviewed_1based": list(range(136, 142)),
            "adjacent_formal_items": ["15.5 (PDF-S. 138)", "15.6 (PDF-S. 138)", "15.8 (PDF-S. 139)"],
            "source_evidence": (
                "Zwischen 15.6 und 15.8 ist keine formale Box 15.7 gedruckt. Auf Seite 139 "
                "steht stattdessen eine eigenständige, formal ausgezeichnete Box mit der erneut "
                "gedruckten Nummer 15.4."
            ),
            "action": "Keine 15.7 ergänzt; zwei tatsächlich fehlende formale Haupttextitems auf PDF-S. 136 und 139 quellentreu aufgenommen.",
            "new_formal_item_record_ids": [p136_item["record_id"], p139_item["record_id"]],
            "gemini_used": False,
        },
        {
            "audit_id": "gap-vte-19-missing-2",
            "source_id": VTE_SOURCE_ID,
            "source_file_name": sources[VTE_SOURCE_ID]["original_file_name"],
            "reported_gap": "19.2",
            "classification": "C",
            "classification_label": "source_native_numbering_gap",
            "physical_pdf_pages_reviewed_1based": list(range(148, 154)),
            "adjacent_formal_items": ["19.1 (PDF-S. 149)", "gedruckt 15.4 (PDF-S. 150)", "19.3 und 19.4 (PDF-S. 151)"],
            "source_evidence": (
                "19.2 ist auf Seite 151 eine Unterkapitelüberschrift, keine formale Itembox. "
                "Die zwischen 19.1 und 19.3 gedruckte formale Box trägt sichtbar die quellnative "
                "Duplikatnummer 15.4."
            ),
            "action": "Keine 19.2 erfunden; bestehender Record behält die gedruckte 15.4 als Auditmetadatum und source_item_number=null.",
            "corrected_record_ids": [p150_item["record_id"]],
            "gemini_used": False,
        },
        {
            "audit_id": "gap-hcc-4-missing-29",
            "source_id": HCC_SOURCE_ID,
            "source_file_name": sources[HCC_SOURCE_ID]["original_file_name"],
            "reported_gap": "4.29",
            "classification": "C",
            "classification_label": "source_native_numbering_gap",
            "physical_pdf_pages_reviewed_1based": list(range(149, 155)),
            "adjacent_formal_items": ["4.28 (PDF-S. 151)", "unnummerierte formale Box (PDF-S. 152)", "4.30 (PDF-S. 153)"],
            "source_evidence": (
                "Die formale Haupttextbox auf Seite 152 zeigt Empfehlungsgrad, Evidenzlevel und "
                "Konsens, aber keine gedruckte Itemnummer. Die nächste gedruckte Nummer ist 4.30."
            ),
            "action": "Keine 4.29 ergänzt; das gültige unnummerierte Haupttextitem bleibt unter stabiler interner ID erhalten.",
            "existing_formal_item_record_ids": [HCC_UNNUMBERED_RECORD_ID],
            "gemini_used": False,
        },
    ]
    unnumbered_review = {
        "audit_id": "hcc-page-152-unnumbered-formal-item",
        "record_id": HCC_UNNUMBERED_RECORD_ID,
        "source_id": HCC_SOURCE_ID,
        "physical_pdf_page_1based": 152,
        "printed_page_label": hcc_unnumbered.get("printed_page_label"),
        "result": "valid_primary_main_body_formal_item_without_printed_number",
        "source_item_number": None,
        "item_number_status": "not_printed_in_source",
        "canonical_role": hcc_unnumbered.get("canonical_role"),
        "source_zone": hcc_unnumbered.get("source_zone"),
        "retrieval_eligible": is_primary_use_eligible(hcc_unnumbered),
        "exact_text_de": hcc_unnumbered["exact_text_de"],
        "gemini_used": False,
    }

    historical = [
        row
        for row in records.values()
        if row.get("exclusion_reason") == HCC_HISTORICAL_EXCLUSION_REASON
    ]
    if len(historical) != 99:
        raise RuntimeError(f"Expected 99 HCC historical policy records, got {len(historical)}")
    if not all(
        row.get("status") == "excluded_by_policy"
        and row.get("canonical_role") == "historical_secondary"
        and not is_primary_use_eligible(row)
        for row in historical
    ):
        raise RuntimeError("HCC historical policy fields are incomplete")

    audit = {
        "schema_version": "numbering-gap-audit-1.0.0",
        "checked_at_utc": utc_now(),
        "method": "local_deterministic_pdf_text_and_visual_review",
        "gemini_used": False,
        "gemini_pages": [],
        "reported_gap_count": 3,
        "source_native_numbering_gap_count": 3,
        "new_formal_item_count": 2,
        "new_formal_item_record_ids": [p136_item["record_id"], p139_item["record_id"]],
        "existing_item_number_corrections": 1,
        "existing_item_number_corrected_record_ids": [p150_item["record_id"]],
        "invented_item_number_count": 0,
        "reviews": gap_reviews,
        "unnumbered_item_review": unnumbered_review,
        "hcc_historical_policy_record_count": len(historical),
        "source_integrity": {
            source_id: sha256_file(project_root / sources[source_id]["relative_path"])
            == sources[source_id]["sha256"]
            for source_id in (VTE_SOURCE_ID, HCC_SOURCE_ID)
        },
    }
    atomic_write_json(qa_root / "numbering_gap_audit.json", audit)

    gap_csv_rows: list[dict[str, Any]] = []
    for review in gap_reviews:
        gap_csv_rows.append(
            {
                "audit_id": review["audit_id"],
                "source_id": review["source_id"],
                "source_file_name": review["source_file_name"],
                "reported_gap": review["reported_gap"],
                "classification": review["classification"],
                "classification_label": review["classification_label"],
                "physical_pdf_pages_reviewed_1based": json.dumps(
                    review["physical_pdf_pages_reviewed_1based"]
                ),
                "adjacent_formal_items": "; ".join(review["adjacent_formal_items"]),
                "source_evidence": review["source_evidence"],
                "action": review["action"],
                "record_ids": ";".join(
                    review.get("new_formal_item_record_ids")
                    or review.get("corrected_record_ids")
                    or review.get("existing_formal_item_record_ids")
                    or []
                ),
                "gemini_used": False,
            }
        )
    atomic_csv(
        qa_root / "numbering_gap_audit.csv",
        [
            "audit_id",
            "source_id",
            "source_file_name",
            "reported_gap",
            "classification",
            "classification_label",
            "physical_pdf_pages_reviewed_1based",
            "adjacent_formal_items",
            "source_evidence",
            "action",
            "record_ids",
            "gemini_used",
        ],
        gap_csv_rows,
    )

    markdown = [
        "# Audit der gemeldeten Itemnummernlücken",
        "",
        "**Ergebnis:** Alle drei gemeldeten Lücken sind `source_native_numbering_gap` (Kategorie C). Es wurde keine Nummer erfunden.",
        "",
        "| Lücke | Ergebnis | Quellenprüfung | Maßnahme |",
        "|---|---|---|---|",
    ]
    for review in gap_reviews:
        markdown.append(
            f"| {review['reported_gap']} | C – source_native_numbering_gap | "
            f"{review['source_evidence']} | {review['action']} |"
        )
    markdown.extend(
        [
            "",
            "## Unnummeriertes HCC/BCC-Item auf PDF-Seite 152",
            "",
            f"`{HCC_UNNUMBERED_RECORD_ID}` ist eine gültige primäre Haupttextbox. Im Original ist keine Itemnummer gedruckt; `source_item_number` bleibt `null` und `item_number_status=not_printed_in_source`.",
            "",
            "## Quellentreue Ergänzungen",
            "",
            f"Zwei zuvor fehlende formale VTE-Haupttextitems wurden aufgenommen: `{p136_item['record_id']}` (PDF-S. 136, gedruckt 15.4) und `{p139_item['record_id']}` (PDF-S. 139, gedrucktes Nummernduplikat 15.4; kanonische Nummer bewusst null).",
            "",
            "Gemini wurde nicht verwendet. Die beiden geprüften Quell-PDFs stimmen weiterhin mit dem eingefrorenen SHA-256-Manifest überein.",
        ]
    )
    atomic_text(qa_root / "numbering_gap_audit.md", "\n".join(markdown))

    # Five exact, source-verifiable examples per requested machine flag.
    selections = {
        "dose_entity_recovered_from_immediate_context_or_flagged": [
            "rec-6ee3957c3f39ba28bb8d9851",
            "rec-0481080d3cee63440dcd7325",
            "rec-47fadb938d29693993073fdb",
            "rec-a3950911e5805e3bb83d92d1",
            "rec-26cbad752500e0de2a9991b0",
        ],
        "adverse_reaction_term_recovered_from_exact_source_text": [
            "rec-9b4ae6e8b4c18e2052e37164",
            "rec-151cd4d1e9721a6859bcb02e",
            "rec-0ac775f1e3d308b22b103a79",
            "rec-0c91f10f53f30e7c7d9d429e",
            "rec-99b925b1315b5d2990cff626",
        ],
        "dose_value_not_explicit": [
            "rec-7dc1c688de87e5193b408131",
            "rec-3b2f1cdf2622b57292d1e005",
            "rec-fd2ae17ed19b1f38a4272546",
            "rec-2cd17818cdce4748227bb581",
            "rec-2843e667da049c80602c63fd",
        ],
    }
    source_readers: dict[str, PdfReader] = {}
    examples: list[dict[str, Any]] = []
    for flag_type, record_ids in selections.items():
        for record_id in record_ids:
            record = records[record_id]
            if flag_type not in (record.get("review_flags") or []):
                raise RuntimeError(f"Selected record lacks {flag_type}: {record_id}")
            source_id = record["source_id"]
            if source_id not in source_readers:
                source_readers[source_id] = PdfReader(
                    project_root / sources[source_id]["relative_path"]
                )
            source_page_texts = [
                source_readers[source_id].pages[page - 1].extract_text() or ""
                for page in record["pdf_pages_1based"]
            ]
            if not quote_locally_verifiable(record["exact_source_text"], source_page_texts):
                raise RuntimeError(f"Example quote not locally verifiable: {record_id}")
            product, substance = source_context_identity(records, source_id)
            if flag_type == "dose_entity_recovered_from_immediate_context_or_flagged":
                affected_field = "product_name / active_substance_names"
                structured_value = json.dumps(
                    {
                        "product_name": record.get("product_name") or product,
                        "active_substance": (
                            (record.get("active_substance_names") or [substance])[0]
                        ),
                    },
                    ensure_ascii=False,
                )
                why = (
                    "Im atomaren Gemini-Record fehlten Produkt und Wirkstoff zunächst; die Pipeline "
                    "übernahm die Produktidentität aus dem unmittelbar vorausgehenden, gleichquelligen "
                    "Fachinformationskontext und ließ den transparenten Flag bestehen."
                )
                assessment = "correct_contextual_recovery"
                action = "Keine klinische Korrektur; gleichquellige Identitätsprovenienz beim Datenbankimport beibehalten."
            elif flag_type == "adverse_reaction_term_recovered_from_exact_source_text":
                affected_field = "adverse_reaction_term"
                structured_value = record.get("adverse_reaction_term")
                why = (
                    "Der Modellrecord enthielt die vollständige Nebenwirkungszeile im exakten Quelltext, "
                    "ließ aber das atomare Termfeld leer; dieses wurde deterministisch aus exakt demselben "
                    "Text übernommen."
                )
                assessment = "correct_contextual_recovery"
                action = "Keine Korrektur; exakten Tabellen-/Absatzkontext erhalten, spätere feinere Atomisierung nur abgeleitet vornehmen."
            else:
                affected_field = "dose_value"
                structured_value = json.dumps(record.get("dose_value"), ensure_ascii=False)
                why = (
                    "Die Passage enthält eine qualitative Anpassungs-, Unterbrechungs- oder Einnahmeregel "
                    "beziehungsweise mehrere alternative Dosen, aber keinen einzelnen atomaren Dosiswert, "
                    "der verlustfrei in dose_value übernommen werden könnte."
                )
                assessment = "correct_contextual_recovery"
                action = (
                    "dose_value=null beibehalten; Regel über exact_source_text und die spezifischen "
                    "Unterbrechungs-/Kontextfelder retrieveln."
                )
            immediate = record.get("supporting_source_text") or record["exact_source_text"]
            examples.append(
                {
                    "flag_type": flag_type,
                    "source_file_name": record["source_file_name"],
                    "record_id": record_id,
                    "active_substance": substance,
                    "product": product,
                    "pdf_pages_1based": json.dumps(record["pdf_pages_1based"]),
                    "exact_source_excerpt": short_exact_excerpt(record["exact_source_text"]),
                    "affected_structured_field": affected_field,
                    "structured_value": structured_value,
                    "immediate_context": short_exact_excerpt(immediate),
                    "flag_reason": why,
                    "assessment": assessment,
                    "retrieval_eligible": is_primary_use_eligible(record),
                    "required_action": action,
                    "quote_locally_verified": True,
                }
            )
    if Counter(row["flag_type"] for row in examples) != Counter(
        {flag_type: 5 for flag_type in selections}
    ):
        raise RuntimeError("Flag example distribution is not exactly five per category")

    example_fields = [
        "flag_type",
        "source_file_name",
        "record_id",
        "active_substance",
        "product",
        "pdf_pages_1based",
        "exact_source_excerpt",
        "affected_structured_field",
        "structured_value",
        "immediate_context",
        "flag_reason",
        "assessment",
        "retrieval_eligible",
        "required_action",
        "quote_locally_verified",
    ]
    atomic_csv(qa_root / "flag_examples.csv", example_fields, examples)
    example_markdown = [
        "# Repräsentative Arzneimittel-Flagbeispiele",
        "",
        "Je Flagtyp wurden fünf unterschiedliche, lokal gegen die angegebene Original-PDF-Seite verifizierte Records ausgewählt. Die Beispiele sind keine medizinische Neubewertung.",
    ]
    for flag_type in selections:
        example_markdown.extend(["", f"## `{flag_type}`", ""])
        for row in [item for item in examples if item["flag_type"] == flag_type]:
            excerpt = row["exact_source_excerpt"].replace("\n", " ")
            example_markdown.extend(
                [
                    f"- **{row['record_id']}** — {row['product']} / {row['active_substance']}, PDF-S. {row['pdf_pages_1based']}",
                    f"  - Quellausschnitt: „{excerpt}“",
                    f"  - Feld/Wert: `{row['affected_structured_field']}` = {row['structured_value']}",
                    f"  - Bewertung: `{row['assessment']}`; Retrieval: `{str(row['retrieval_eligible']).lower()}`",
                    f"  - Aktion: {row['required_action']}",
                ]
            )
    atomic_text(qa_root / "flag_examples.md", "\n".join(example_markdown))

    print(
        json.dumps(
            {
                "numbering_gap_classifications": [
                    review["classification_label"] for review in gap_reviews
                ],
                "new_formal_items": 2,
                "hcc_historical_policy_records": len(historical),
                "flag_examples": len(examples),
                "gemini_used": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

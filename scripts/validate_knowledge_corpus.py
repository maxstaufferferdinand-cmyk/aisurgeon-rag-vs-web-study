#!/usr/bin/env python3
"""Independent deterministic validation of the compiled knowledge corpus."""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from aisurgeon_decentralised.knowledge_corpus_pipeline import (
    Batch,
    atomic_write_json,
    load_validated_checkpoints,
    sha256_file,
    utc_now,
)
from aisurgeon_decentralised.knowledge_corpus_policy import (
    HCC_HISTORICAL_EXCLUSION_REASON,
    build_answer_evidence_package,
    filter_normal_search_records,
    is_primary_use_eligible,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise RuntimeError(f"JSONL row is not an object at {path}:{line_number}")
            rows.append(value)
    return rows


def load_plan(path: Path) -> list[Batch]:
    batches: list[Batch] = []
    for row in read_jsonl(path):
        batch = Batch(
            source_id=row["source_id"],
            source_file_name=row["source_file_name"],
            source_sha256=row["source_sha256"],
            document_type=row["document_type"],
            request_pages=tuple(row["request_pdf_pages_1based"]),
            owner_pages=tuple(row["owner_pdf_pages_1based"]),
            task_family=row["task_family"],
        )
        if batch.batch_id != row["batch_id"]:
            raise RuntimeError(f"Batch ID is not reproducible: {row['batch_id']}")
        batches.append(batch)
    return batches


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_root = project_root / "outputs/knowledge_corpus"
    source_manifest = json.loads(
        (output_root / "manifests/source_manifest.json").read_text(encoding="utf-8")
    )
    run_manifest = json.loads(
        (output_root / "manifests/run_manifest.json").read_text(encoding="utf-8")
    )
    batches = load_plan(output_root / "manifests/batch_plan.jsonl")
    checkpoints = load_validated_checkpoints(output_root, batches)

    coverage = read_jsonl(output_root / "manifests/coverage_manifest.jsonl")
    expected_pages = {
        (source["source_id"], page)
        for source in source_manifest["sources"]
        for page in range(1, source["page_count"] + 1)
    }
    actual_pages = {(row["source_id"], row["pdf_page_1based"]) for row in coverage}

    canonical_paths = sorted((output_root / "canonical").glob("*.jsonl"))
    canonical_rows_by_path = {path: read_jsonl(path) for path in canonical_paths}
    canonical_record_ids = {
        row["record_id"]
        for path, rows in canonical_rows_by_path.items()
        if path.name not in {"documents.jsonl", "pharmacology.jsonl"}
        for row in rows
        if row.get("record_id")
    }
    retrieval_units = read_jsonl(output_root / "retrieval/retrieval_units.jsonl")
    embedding_input = read_jsonl(output_root / "retrieval/embedding_input.jsonl")
    retrieval_parent_ids = {
        parent_id for unit in retrieval_units for parent_id in unit.get("parent_record_ids", [])
    }
    citation = json.loads(
        (output_root / "qa/citation_completeness.json").read_text(encoding="utf-8")
    )
    schema_rows = read_jsonl(output_root / "qa/schema_validation.jsonl")
    formal_items = canonical_rows_by_path[output_root / "canonical/formal_items.jsonl"]
    guideline_links = read_jsonl(output_root / "links/guideline_item_links.jsonl")
    repair_overlay = json.loads(
        (output_root / "manifests/targeted_repair_overlay.json").read_text(
            encoding="utf-8"
        )
    )
    repair_report = json.loads(
        (output_root / "qa/targeted_repair_report.json").read_text(encoding="utf-8")
    )
    repair_ledger = read_jsonl(output_root / "qa/targeted_repair_changes.jsonl")
    hcc_historical_exclusions = read_jsonl(
        output_root / "qa/hcc_historical_exclusions.jsonl"
    )
    numbering_gap_audit = json.loads(
        (output_root / "qa/numbering_gap_audit.json").read_text(encoding="utf-8")
    )
    with (output_root / "qa/flag_examples.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        flag_examples = list(csv.DictReader(handle))
    medication_mentions = read_jsonl(
        output_root / "links/medication_mentions.jsonl"
    )
    active_crosswalk = read_jsonl(
        output_root / "links/active_substance_crosswalk.jsonl"
    )
    product_ids = {
        row["product_id"]
        for row in canonical_rows_by_path[output_root / "canonical/drug_products.jsonl"]
        if row.get("product_id")
    }
    active_substance_ids = {
        row["active_substance_id"]
        for row in canonical_rows_by_path[
            output_root / "canonical/active_substances.jsonl"
        ]
        if row.get("active_substance_id")
    }

    # Exclude aggregate convenience partitions that repeat or consolidate
    # records already held by their page-level evidence/source partitions.
    record_lookup: dict[str, dict[str, Any]] = {}
    conflicting_record_ids: list[str] = []
    for path, rows in canonical_rows_by_path.items():
        if path.name in {
            "documents.jsonl",
            "pharmacology.jsonl",
            "drug_products.jsonl",
            "active_substances.jsonl",
        }:
            continue
        for row in rows:
            record_id = row.get("record_id")
            if not record_id:
                continue
            previous = record_lookup.setdefault(record_id, row)
            if previous != row:
                conflicting_record_ids.append(record_id)
    # Entity IDs may denote a source-faithful concept carried by a page-level
    # canonical record even when no consolidated product-information entity
    # exists (for example a medication mention in a guideline).  Such IDs
    # resolve through that canonical record and the alias crosswalk.
    resolvable_product_ids = product_ids | {
        row["product_id"] for row in record_lookup.values() if row.get("product_id")
    }
    resolvable_active_substance_ids = active_substance_ids | {
        entity_id
        for row in record_lookup.values()
        for entity_id in row.get("active_substance_ids") or []
    }

    primary_formal = [
        row for row in formal_items if row.get("canonical_role") == "primary"
    ]
    secondary_formal = [
        row for row in formal_items if row.get("canonical_role") != "primary"
    ]
    primary_by_id = {row["record_id"]: row for row in primary_formal}
    secondary_ids = {row["record_id"] for row in secondary_formal}
    retrieval_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in retrieval_units:
        for parent_id in unit.get("parent_record_ids") or []:
            retrieval_by_parent[parent_id].append(unit)

    primary_number_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in primary_formal:
        number = (row.get("source_item_number") or "").strip()
        if number:
            primary_number_groups[(row["source_id"], number)].append(row["record_id"])
    duplicate_primary_numbers = {
        f"{source_id}:{number}": record_ids
        for (source_id, number), record_ids in primary_number_groups.items()
        if len(record_ids) > 1
    }

    secondary_mappings_valid = all(
        all(
            primary_id in primary_by_id
            and primary_by_id[primary_id]["source_id"] == row["source_id"]
            for primary_id in row.get("primary_record_ids") or []
        )
        and (
            bool(row.get("primary_record_ids"))
            or (
                row.get("canonical_role") == "historical_record"
                and bool(row.get("uncertainty_reason"))
            )
            or (
                row.get("canonical_role") == "historical_secondary"
                and row.get("status") == "excluded_by_policy"
                and row.get("exclusion_reason")
                == HCC_HISTORICAL_EXCLUSION_REASON
            )
        )
        for row in secondary_formal
    )
    all_links_resolve = all(
        link.get("from_record_id") in record_lookup
        and link.get("to_record_id") in record_lookup
        for link in guideline_links
    )
    link_metadata_valid = all(
        link.get("source_id") == record_lookup[link["from_record_id"]].get("source_id")
        and link.get("to_source_id") == record_lookup[link["to_record_id"]].get("source_id")
        and link.get("to_record_type") == record_lookup[link["to_record_id"]].get("record_type")
        and link.get("to_pdf_pages_1based")
        == record_lookup[link["to_record_id"]].get("pdf_pages_1based")
        for link in guideline_links
        if link.get("from_record_id") in record_lookup
        and link.get("to_record_id") in record_lookup
    )
    no_cross_source_links = all(
        record_lookup[link["from_record_id"]].get("source_id")
        == record_lookup[link["to_record_id"]].get("source_id")
        for link in guideline_links
        if link.get("from_record_id") in record_lookup
        and link.get("to_record_id") in record_lookup
    )
    valid_cross_page_links = [
        link
        for link in guideline_links
        if link.get("from_record_id") in record_lookup
        and link.get("to_record_id") in record_lookup
        and record_lookup[link["from_record_id"]].get("source_id")
        == record_lookup[link["to_record_id"]].get("source_id")
        and set(record_lookup[link["from_record_id"]].get("pdf_pages_1based") or []).isdisjoint(
            record_lookup[link["to_record_id"]].get("pdf_pages_1based") or []
        )
    ]

    vte_source_id = (
        "src-003-001l-s3-prophylaxe-venoese-thromboembolie-vte-2026-04-"
        "f82c5686f6b7"
    )
    coverage_lookup = {
        (row["source_id"], row["pdf_page_1based"]): row for row in coverage
    }
    vte_special_pages_valid = all(
        coverage_lookup[(vte_source_id, page)].get("status") == "extracted"
        and bool(coverage_lookup[(vte_source_id, page)].get("canonical_owner_batch_id"))
        and bool(coverage_lookup[(vte_source_id, page)].get("validated_batch_ids"))
        for page in (140, 154)
    )
    vte_special_item_ids = {
        row["record_id"]
        for row in primary_formal
        if row.get("source_id") == vte_source_id
        and row.get("source_item_number") in {"15.9", "19.7"}
        and row.get("pdf_pages_1based") in ([140], [154])
    }
    vte_special_pages_retrievable = len(vte_special_item_ids) == 2 and all(
        retrieval_by_parent.get(record_id) for record_id in vte_special_item_ids
    )

    formal_additions = [
        addition
        for addition in repair_overlay.get("record_additions", [])
        if addition.get("record", {}).get("record_type") == "formal_item"
    ]
    primary_vte_by_number = {
        row.get("source_item_number"): row
        for row in primary_formal
        if row.get("source_id") == vte_source_id
    }
    repaired_vte_items_match_overlay = len(formal_additions) == 29 and all(
        any(
            row.get("source_id") == addition["source_id"]
            and row.get("source_item_number")
            == addition["record"].get("source_item_number")
            and row.get("source_identifier")
            == addition["record"].get("source_identifier")
            and row.get("exact_text_de") == addition["record"].get("exact_text_de")
            and row.get("pdf_pages_1based")
            == addition["record"].get("pdf_pages_1based")
            and row.get("quote_locally_verified") is True
            for row in primary_formal
        )
        for addition in formal_additions
    )
    locator_records_valid = (
        record_lookup["rec-bfadaf51375042bb9981d516"].get("pdf_pages_1based") == [81]
        and record_lookup["rec-cbbe0386195f92a39edd7227"].get("pdf_pages_1based")
        == [55]
    )

    unnumbered_primary = [
        row for row in primary_formal if row.get("source_item_number") is None
    ]
    unnumbered_items_transparent = len(unnumbered_primary) == 3 and all(
        row.get("uncertainty_reason")
        and (row.get("structured_field_provenance") or {}).get("source_item_number")
        and row.get("item_number_status")
        in {"not_printed_in_source", "printed_duplicate_in_source"}
        for row in unnumbered_primary
    )

    hcc_policy_ids = {row["record_id"] for row in hcc_historical_exclusions}
    hcc_policy_fields_valid = len(hcc_policy_ids) == 99 and all(
        row.get("exclusion_reason") == HCC_HISTORICAL_EXCLUSION_REASON
        and row.get("status") == "excluded_by_policy"
        and row.get("canonical_role") == "historical_secondary"
        and row.get("source_zone") in {"change_table", "historical_table"}
        and row.get("retrieval_eligible") is False
        and row.get("embedding_eligible") is False
        and row.get("answer_eligible") is False
        and row.get("primary_search_eligible") is False
        and not is_primary_use_eligible(row)
        for row in (record_lookup[record_id] for record_id in hcc_policy_ids)
    )
    hcc_policy_absent_from_retrieval = not any(
        hcc_policy_ids.intersection(unit.get("parent_record_ids") or [])
        for unit in retrieval_units
    )
    hcc_policy_absent_from_embedding = not any(
        hcc_policy_ids.intersection(
            row.get("metadata", {}).get("parent_record_ids") or []
        )
        for row in embedding_input
    )
    hcc_policy_absent_from_links = not any(
        link.get("from_record_id") in hcc_policy_ids
        or link.get("to_record_id") in hcc_policy_ids
        for link in guideline_links
    ) and not any(
        mention.get("source_record_id") in hcc_policy_ids
        for mention in medication_mentions
    )
    normal_search_ids = {
        row["record_id"]
        for row in filter_normal_search_records(record_lookup.values())
    }
    hcc_policy_absent_from_normal_search = hcc_policy_ids.isdisjoint(
        normal_search_ids
    )
    eligible_seed_id = next(
        row["record_id"] for row in primary_formal if is_primary_use_eligible(row)
    )
    historical_seed_id = sorted(hcc_policy_ids)[0]
    synthetic_policy_probe = [
        {
            "from_record_id": eligible_seed_id,
            "to_record_id": historical_seed_id,
        },
        {
            "from_record_id": historical_seed_id,
            "to_record_id": eligible_seed_id,
        },
    ]
    evidence_package_ids = {
        row["record_id"]
        for row in build_answer_evidence_package(
            [eligible_seed_id, historical_seed_id],
            record_lookup,
            [*guideline_links, *synthetic_policy_probe],
        )
    }
    hcc_policy_absent_from_evidence_package = hcc_policy_ids.isdisjoint(
        evidence_package_ids
    ) and eligible_seed_id in evidence_package_ids

    gap_reviews = numbering_gap_audit.get("reviews") or []
    numbering_gap_audit_valid = (
        numbering_gap_audit.get("gemini_used") is False
        and numbering_gap_audit.get("source_native_numbering_gap_count") == 3
        and numbering_gap_audit.get("new_formal_item_count") == 2
        and numbering_gap_audit.get("invented_item_number_count") == 0
        and {row.get("reported_gap") for row in gap_reviews}
        == {"15.7", "19.2", "4.29"}
        and all(row.get("classification") == "C" for row in gap_reviews)
        and all(numbering_gap_audit.get("source_integrity", {}).values())
    )
    no_invented_gap_numbers = (
        not any(row.get("source_item_number") == "15.7" for row in primary_formal)
        and not any(row.get("source_item_number") == "19.2" for row in primary_formal)
        and not any(row.get("source_item_number") == "4.29" for row in primary_formal)
    )
    new_gap_items_have_rationale_links = all(
        any(
            link.get("from_record_id") == record_id
            and link.get("link_type") == "guideline_item_to_rationale"
            and link.get("to_record_type") == "rationale_block"
            for link in guideline_links
        )
        for record_id in numbering_gap_audit.get("new_formal_item_record_ids", [])
    ) and len(numbering_gap_audit.get("new_formal_item_record_ids", [])) == 2
    named_unnumbered_records_valid = (
        record_lookup["rec-3b4fd85be9e8c296bdca48da"].get("source_item_number")
        is None
        and record_lookup["rec-3b4fd85be9e8c296bdca48da"].get(
            "item_number_status"
        )
        == "not_printed_in_source"
        and record_lookup["rec-70f8457904ae104d4422b8ca"].get(
            "source_item_number"
        )
        is None
        and record_lookup["rec-70f8457904ae104d4422b8ca"].get(
            "printed_source_item_number"
        )
        == "15.4"
    )
    flag_example_counts = Counter(
        row.get("flag_type") for row in flag_examples
    )
    flag_examples_valid = (
        len(flag_examples) == 15
        and flag_example_counts
        == Counter(
            {
                "dose_entity_recovered_from_immediate_context_or_flagged": 5,
                "adverse_reaction_term_recovered_from_exact_source_text": 5,
                "dose_value_not_explicit": 5,
            }
        )
        and all(row.get("quote_locally_verified") == "True" for row in flag_examples)
        and all(row.get("record_id") in record_lookup for row in flag_examples)
    )
    remaining_lines = (
        output_root / "qa/targeted_repair_remaining.csv"
    ).read_text(encoding="utf-8").splitlines()

    negation_checks = {
        "vte_12_43": "sollte nicht erfolgen"
        in primary_vte_by_number["12.43"].get("exact_text_de", ""),
        "five_fu_restriction": "sofern nicht andere Nebenwirkungen"
        in record_lookup["rec-34914cff50e9882dcb1b46e5"].get(
            "semantic_summary_de", ""
        ),
        "keytruda_non_clear_cell": "nicht-klarzelliger Histologie"
        in record_lookup["rec-6c5d10df9c8a93c4fd79abd5"].get(
            "semantic_summary_de", ""
        ),
        "plavix_caution": "nur mit Vorsicht"
        in record_lookup["rec-857e6c6a00877b2e6b390fa1"].get(
            "semantic_summary_de", ""
        ),
    }
    dosing_checks = {
        "lixiana_60_mg": all(
            [
                record_lookup["rec-091d23036f9481e0a49784fe"].get("dose_value")
                == "60",
                record_lookup["rec-091d23036f9481e0a49784fe"].get("dose_unit")
                == "mg",
                record_lookup["rec-091d23036f9481e0a49784fe"].get("frequency")
                == "einmal täglich",
                record_lookup["rec-091d23036f9481e0a49784fe"].get("route")
                == "oral",
                "einmal täglich"
                in (record_lookup["rec-091d23036f9481e0a49784fe"].get("supporting_source_text") or ""),
            ]
        ),
        "lixiana_1_2_mg_per_kg": all(
            [
                record_lookup["rec-e15a063fcf6848ef8db0d2e8"].get("dose_value")
                == "1,2",
                record_lookup["rec-e15a063fcf6848ef8db0d2e8"].get("dose_unit")
                == "mg/kg",
                "1,2 mg/kg"
                in record_lookup["rec-e15a063fcf6848ef8db0d2e8"].get(
                    "exact_source_text", ""
                ),
            ]
        ),
        "keytruda_subcutaneous": all(
            [
                record_lookup["rec-56f1020a58aad39f9f5721f2"].get("route")
                == "subkutane Injektion",
                "395 mg alle 3 Wochen"
                in (record_lookup["rec-56f1020a58aad39f9f5721f2"].get("supporting_source_text") or ""),
                "790 mg alle 6 Wochen"
                in (record_lookup["rec-56f1020a58aad39f9f5721f2"].get("supporting_source_text") or ""),
            ]
        ),
    }

    search_audit_rows = [
        row for row in record_lookup.values() if row.get("exact_source_text") is not None
    ]
    normalized_search_audit_valid = all(
        not any(
            unicodedata.category(character) == "Cc"
            for character in (row.get("normalized_search_text") or "")
        )
        and hashlib.sha256(row["exact_source_text"].encode("utf-8")).hexdigest()
        == row.get("exact_source_text_raw_sha256")
        and hashlib.sha256((row.get("normalized_search_text") or "").encode("utf-8")).hexdigest()
        == row.get("normalized_search_text_sha256")
        for row in search_audit_rows
    )

    medication_links_valid = all(
        mention.get("source_record_id") in record_lookup
        and record_lookup[mention["source_record_id"]].get("source_id")
        == mention.get("source_id")
        and (
            mention.get("product_id") is None
            or mention.get("product_id") in resolvable_product_ids
        )
        and (
            mention.get("active_substance_id") is None
            or mention.get("active_substance_id") in resolvable_active_substance_ids
        )
        for mention in medication_mentions
    )
    crosswalk_links_valid = all(
        row.get("active_substance_id") in resolvable_active_substance_ids
        and (
            row.get("product_id") is None
            or row.get("product_id") in resolvable_product_ids
        )
        for row in active_crosswalk
    )
    retrieval_entity_links_valid = all(
        all(
            value in resolvable_active_substance_ids
            for value in unit.get("active_substance_ids") or []
        )
        and all(value in resolvable_product_ids for value in unit.get("product_ids") or [])
        for unit in retrieval_units
    )

    artifact_parse_errors: list[dict[str, Any]] = []
    for path in sorted(output_root.rglob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            artifact_parse_errors.append(
                {"path": str(path.relative_to(output_root)), "error": str(exc)}
            )
    for path in sorted(output_root.rglob("*.jsonl")):
        try:
            read_jsonl(path)
        except (OSError, UnicodeError, RuntimeError) as exc:
            artifact_parse_errors.append(
                {"path": str(path.relative_to(output_root)), "error": str(exc)}
            )

    canonical_schema = json.loads(
        (output_root / "schemas/canonical_record.schema.json").read_text(encoding="utf-8")
    )
    retrieval_schema = json.loads(
        (output_root / "schemas/retrieval_unit.schema.json").read_text(encoding="utf-8")
    )
    canonical_validator = Draft202012Validator(canonical_schema)
    retrieval_validator = Draft202012Validator(retrieval_schema)
    schema_errors: list[dict[str, Any]] = []
    for path, rows in canonical_rows_by_path.items():
        for row_number, row in enumerate(rows, start=1):
            for error in canonical_validator.iter_errors(row):
                schema_errors.append(
                    {
                        "file": path.name,
                        "row": row_number,
                        "path": list(error.absolute_path),
                        "message": error.message,
                    }
                )
                if len(schema_errors) >= 100:
                    break
            if len(schema_errors) >= 100:
                break
        if len(schema_errors) >= 100:
            break
    if len(schema_errors) < 100:
        for row_number, row in enumerate(retrieval_units, start=1):
            for error in retrieval_validator.iter_errors(row):
                schema_errors.append(
                    {
                        "file": "retrieval_units.jsonl",
                        "row": row_number,
                        "path": list(error.absolute_path),
                        "message": error.message,
                    }
                )
                if len(schema_errors) >= 100:
                    break
            if len(schema_errors) >= 100:
                break

    hashes_unchanged = all(
        sha256_file(project_root / source["relative_path"]) == source["sha256"]
        for source in source_manifest["sources"]
    )
    checks = {
        "run_complete": run_manifest.get("status") == "complete",
        "all_planned_checkpoints_revalidated": len(checkpoints) == len(batches),
        "coverage_exactly_once": len(coverage) == len(expected_pages)
        and len(actual_pages) == len(coverage)
        and actual_pages == expected_pages,
        "all_schema_rows_valid": len(schema_rows) == len(batches)
        and all(row.get("valid") is True for row in schema_rows),
        "compiled_json_schema_valid": not schema_errors,
        "retrieval_parent_records_resolve": retrieval_parent_ids <= canonical_record_ids,
        "retrieval_namespaces_separated": all(
            unit.get("corpus_namespace") in {"guideline", "drug_label"}
            for unit in retrieval_units
        ),
        "citation_completeness_100_percent": citation.get("citation_completeness_percent")
        == 100.0,
        "source_sha256_unchanged": hashes_unchanged,
        "no_openai_api_called": run_manifest.get("openai_api_called") is False,
        "no_model_fallback": run_manifest.get("model_fallback_used") is False,
        "no_git_push": run_manifest.get("git_push_performed") is False,
        "canonical_record_ids_not_conflicting": not conflicting_record_ids,
        "primary_item_numbers_unique_per_source": not duplicate_primary_numbers,
        "primary_items_are_main_body_and_retrieval_eligible": all(
            row.get("source_zone") == "main_body"
            and row.get("retrieval_eligible") is True
            and bool(row.get("exact_text_de"))
            for row in primary_formal
        ),
        "secondary_items_are_classified_and_excluded": all(
            row.get("source_zone") != "main_body"
            and row.get("retrieval_eligible") is False
            and bool(row.get("retrieval_exclusion_reason"))
            for row in secondary_formal
        ),
        "secondary_items_linked_or_transparently_quarantined": secondary_mappings_valid,
        "secondary_items_absent_from_retrieval": not any(
            secondary_ids.intersection(unit.get("parent_record_ids") or [])
            for unit in retrieval_units
        ),
        "all_primary_formal_items_have_retrieval_units": all(
            retrieval_by_parent.get(record_id) for record_id in primary_by_id
        ),
        "guideline_links_resolve": all_links_resolve,
        "guideline_link_target_metadata_valid": link_metadata_valid,
        "guideline_links_are_source_scoped": no_cross_source_links,
        "valid_same_source_cross_page_links_preserved": bool(valid_cross_page_links),
        "vte_pages_140_and_154_have_valid_coverage": vte_special_pages_valid,
        "vte_pages_140_and_154_are_retrievable": vte_special_pages_retrievable,
        "all_29_vte_missing_items_match_verified_overlay": repaired_vte_items_match_overlay,
        "named_vte_locator_repairs_valid": locator_records_valid,
        "unnumbered_formal_items_are_transparent": unnumbered_items_transparent,
        "hcc_historical_policy_record_count_and_fields_valid": hcc_policy_fields_valid,
        "hcc_historical_records_absent_from_primary_retrieval": hcc_policy_absent_from_retrieval,
        "hcc_historical_records_absent_from_embedding_input": hcc_policy_absent_from_embedding,
        "hcc_historical_records_absent_from_link_expansion": hcc_policy_absent_from_links,
        "hcc_historical_records_absent_from_normal_search": hcc_policy_absent_from_normal_search,
        "hcc_historical_records_absent_from_answer_evidence_package": hcc_policy_absent_from_evidence_package,
        "numbering_gap_audit_source_verified": numbering_gap_audit_valid,
        "reported_gap_numbers_were_not_invented": no_invented_gap_numbers,
        "new_gap_items_have_source_verified_rationale_links": new_gap_items_have_rationale_links,
        "unnumbered_and_duplicate_source_numbers_are_transparent": named_unnumbered_records_valid,
        "representative_drug_flag_examples_valid": flag_examples_valid,
        "targeted_repair_remaining_is_header_only": len(remaining_lines) == 1,
        "targeted_negations_and_restrictions_preserved": all(negation_checks.values()),
        "targeted_doses_units_frequencies_and_routes_supported": all(
            dosing_checks.values()
        ),
        "search_normalization_preserves_raw_audit_hashes": normalized_search_audit_valid,
        "repair_ledger_has_no_unresolved_blocker": not any(
            row.get("status") == "unresolved_blocker" for row in repair_ledger
        ),
        "targeted_repair_report_passed": repair_report.get("status")
        == "PASS_TARGETED_REPAIR"
        and repair_report.get("unresolved_blocker_count") == 0
        and repair_report.get("can_proceed_to_postgresql_pgvector") is True,
        "targeted_repair_did_not_call_models_or_reextract": run_manifest.get(
            "targeted_repair_gemini_used"
        )
        is False
        and run_manifest.get("targeted_repair_full_reextraction_performed") is False,
        "medication_links_resolve": medication_links_valid,
        "active_substance_crosswalk_links_resolve": crosswalk_links_valid,
        "retrieval_entity_links_resolve": retrieval_entity_links_valid,
        "all_json_and_jsonl_artifacts_parse": not artifact_parse_errors,
    }
    result = {
        "schema_version": "final-validation-1.0.0",
        "checked_at_utc": utc_now(),
        "passed": all(checks.values()),
        "checks": checks,
        "source_count": len(source_manifest["sources"]),
        "page_count": len(expected_pages),
        "planned_batch_count": len(batches),
        "retrieval_unit_count": len(retrieval_units),
        "canonical_partition_count": len(canonical_paths),
        "schema_errors": schema_errors,
        "regression_check_count": 12,
        "targeted_repair_check_count": len(checks) - 12,
        "primary_formal_item_count": len(primary_formal),
        "secondary_formal_item_count": len(secondary_formal),
        "valid_same_source_cross_page_link_count": len(valid_cross_page_links),
        "hcc_historical_policy_record_count": len(hcc_policy_ids),
        "numbering_gap_classifications": {
            row["reported_gap"]: row["classification_label"] for row in gap_reviews
        },
        "flag_example_counts": dict(sorted(flag_example_counts.items())),
        "duplicate_primary_item_numbers": duplicate_primary_numbers,
        "conflicting_record_ids": sorted(set(conflicting_record_ids)),
        "negation_checks": negation_checks,
        "dosing_checks": dosing_checks,
        "artifact_parse_errors": artifact_parse_errors,
    }
    atomic_write_json(output_root / "qa/final_validation.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
import unittest
from pathlib import Path

from aisurgeon_decentralised.knowledge_corpus_models import (
    ExtractedRecord,
    ExtractionEnvelope,
)
from aisurgeon_decentralised.knowledge_corpus_pipeline import (
    Batch,
    PageInfo,
    _narrower_batches,
    assign_entity_ids,
    build_citation_report,
    build_windows,
    consolidate_active_substance_records,
    mark_active_substance_identity_evidence,
    normalize_text,
    quote_locally_verifiable,
)
from aisurgeon_decentralised.knowledge_corpus_policy import (
    HCC_HISTORICAL_EXCLUSION_REASON,
    build_answer_evidence_package,
    filter_normal_search_records,
)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def canonical_lookup() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for path in sorted(Path("outputs/knowledge_corpus/canonical").glob("*.jsonl")):
        if path.name in {"documents.jsonl", "pharmacology.jsonl"}:
            continue
        for row in read_jsonl(path):
            if row.get("record_id"):
                records.setdefault(row["record_id"], row)
    return records


class KnowledgeCorpusTests(unittest.TestCase):
    def test_frozen_manifest_has_expected_unique_pages(self) -> None:
        manifest_path = Path("outputs/knowledge_corpus/manifests/source_manifest.json")
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(12, manifest["source_count"])
        self.assertEqual(2060, manifest["total_pages"])
        self.assertEqual(
            manifest["source_count"], len({source["source_id"] for source in manifest["sources"]})
        )
        for source in manifest["sources"]:
            self.assertEqual(source["page_count"], len(source["page_text_density"]))
            self.assertEqual(64, len(source["sha256"]))
            self.assertFalse(source["is_encrypted"])

    def test_batch_ids_are_stable_and_version_sensitive(self) -> None:
        first = Batch("s", "a.pdf", "a" * 64, "guideline", (1, 2, 3), (1, 2, 3), "guideline_grading")
        same = Batch("s", "a.pdf", "a" * 64, "guideline", (1, 2, 3), (1, 2, 3), "guideline_grading")
        other = Batch("s", "a.pdf", "a" * 64, "guideline", (1, 2, 3), (1, 2), "guideline_grading")
        self.assertEqual(first.batch_id, same.batch_id)
        self.assertNotEqual(first.batch_id, other.batch_id)

    def test_batch_windows_overlap_once_and_respect_maximum(self) -> None:
        infos = [PageInfo(page=index, text="clinical text", printed_label=None) for index in range(1, 16)]
        windows = build_windows(infos, "guideline")
        self.assertLessEqual(max(len(request) for request, _ in windows), 4)
        owned = [page for _, owner in windows for page in owner]
        self.assertEqual(list(range(1, 16)), owned)
        for previous, current in zip(windows, windows[1:]):
            self.assertEqual(previous[0][-1], current[0][0])

    def test_quote_verification_handles_pdf_line_hyphenation(self) -> None:
        page = "Die anti-\nkoagulatorische Therapie erfolgt regelmäßig."
        quote = "Die antikoagulatorische Therapie erfolgt regelmäßig."
        self.assertTrue(quote_locally_verifiable(quote, [page]))
        self.assertEqual("dosis 5 mg", normalize_text("Dosis  5 mg"))

    def test_failed_batch_shrinks_to_single_owner_pages_then_removes_context(self) -> None:
        batch = Batch("s", "a.pdf", "a" * 64, "drug_label", (5, 6, 7), (6, 7), "drug_page_extraction")
        children = _narrower_batches(batch)
        self.assertEqual([(5, 6), (6, 7)], [child.request_pages for child in children])
        self.assertEqual([(6,), (7,)], [child.owner_pages for child in children])
        minimum = _narrower_batches(children[0])
        self.assertEqual([(6,)], [child.request_pages for child in minimum])
        self.assertEqual([], _narrower_batches(minimum[0]))

    def test_formal_item_schema_requires_exact_item_text(self) -> None:
        with self.assertRaises(ValueError):
            ExtractedRecord.model_validate(
                {
                    "record_type": "formal_item",
                    "section_path": [],
                    "pdf_pages_1based": [1],
                    "exact_source_text": "Soll durchgeführt werden.",
                    "semantic_summary_de": "Empfehlung",
                    "confidence": 1.0,
                    "item_type": "recommendation",
                }
            )

    def test_response_schema_uses_json_schema_definitions(self) -> None:
        schema = ExtractionEnvelope.model_json_schema()
        self.assertEqual("object", schema["type"])
        self.assertIn("records", schema["properties"])
        self.assertIn("$defs", schema)

    def test_empty_citation_set_is_complete(self) -> None:
        report = build_citation_report([], [])
        self.assertEqual(100.0, report["citation_completeness_percent"])

    def test_composition_quote_recovers_missing_primary_substance_entity(self) -> None:
        common = {
            "source_id": "drug-source",
            "document_type": "drug_label",
            "pdf_pages_1based": [2],
            "review_flags": [],
        }
        records = [
            {
                **common,
                "record_id": "composition-record",
                "record_type": "composition",
                "exact_source_text": "Jede Tablette enthält 30 mg Edoxaban (als Tosilat).",
                "active_substance_names": [],
                "active_substance_original_names": [],
            },
            {
                **common,
                "record_id": "dose-record",
                "record_type": "dosing_rule",
                "exact_source_text": "Edoxaban 30 mg einmal täglich.",
                "active_substance_names": ["Edoxaban"],
                "active_substance_original_names": [],
            },
            {
                **common,
                "record_id": "interaction-record",
                "record_type": "interaction",
                "exact_source_text": "Zusammen mit Verapamil.",
                "active_substance_names": ["Verapamil"],
                "active_substance_original_names": [],
            },
        ]
        mark_active_substance_identity_evidence(records)
        _, active_ids = assign_entity_ids(records)
        entities = consolidate_active_substance_records(records, active_ids)
        self.assertEqual(["Edoxaban"], [entity["preferred_name"] for entity in entities])
        self.assertIn(
            "active_substance_identity_recovered_from_exact_composition_quote",
            records[0]["review_flags"],
        )

    def test_primary_guideline_items_are_unique_main_body_records(self) -> None:
        formal = read_jsonl(
            Path("outputs/knowledge_corpus/canonical/formal_items.jsonl")
        )
        primary = [row for row in formal if row.get("canonical_role") == "primary"]
        secondary = [row for row in formal if row.get("canonical_role") != "primary"]
        seen: set[tuple[str, str]] = set()
        for row in primary:
            self.assertEqual("main_body", row.get("source_zone"))
            self.assertIs(row.get("retrieval_eligible"), True)
            self.assertTrue(row.get("exact_text_de"))
            if row.get("source_item_number"):
                key = (row["source_id"], row["source_item_number"])
                self.assertNotIn(key, seen)
                seen.add(key)
        self.assertEqual(433, len(primary))
        self.assertEqual(125, len(secondary))
        for row in secondary:
            self.assertNotEqual("main_body", row.get("source_zone"))
            self.assertIs(row.get("retrieval_eligible"), False)
            self.assertTrue(row.get("retrieval_exclusion_reason"))

        retrieval = read_jsonl(
            Path("outputs/knowledge_corpus/retrieval/retrieval_units.jsonl")
        )
        secondary_ids = {row["record_id"] for row in secondary}
        self.assertFalse(
            any(
                secondary_ids.intersection(unit.get("parent_record_ids") or [])
                for unit in retrieval
            )
        )

    def test_guideline_links_are_source_scoped_and_cross_page_links_are_valid(self) -> None:
        records = canonical_lookup()
        links = read_jsonl(
            Path("outputs/knowledge_corpus/links/guideline_item_links.jsonl")
        )
        cross_page_count = 0
        for link in links:
            source = records[link["from_record_id"]]
            target = records[link["to_record_id"]]
            self.assertEqual(source["source_id"], target["source_id"])
            self.assertEqual(target["source_id"], link.get("to_source_id"))
            self.assertEqual(target["record_type"], link.get("to_record_type"))
            self.assertEqual(target["pdf_pages_1based"], link.get("to_pdf_pages_1based"))
            if set(source["pdf_pages_1based"]).isdisjoint(target["pdf_pages_1based"]):
                cross_page_count += 1
        self.assertGreater(cross_page_count, 0)

    def test_targeted_negations_and_dose_support_are_preserved(self) -> None:
        records = canonical_lookup()
        formal = read_jsonl(
            Path("outputs/knowledge_corpus/canonical/formal_items.jsonl")
        )
        vte_12_43 = next(
            row
            for row in formal
            if row.get("source_item_number") == "12.43"
            and row.get("canonical_role") == "primary"
        )
        self.assertIn("sollte nicht erfolgen", vte_12_43["exact_text_de"])
        self.assertIn(
            "sofern nicht andere Nebenwirkungen",
            records["rec-34914cff50e9882dcb1b46e5"]["semantic_summary_de"],
        )
        self.assertIn(
            "nicht-klarzelliger Histologie",
            records["rec-6c5d10df9c8a93c4fd79abd5"]["semantic_summary_de"],
        )

        lixiana = records["rec-e15a063fcf6848ef8db0d2e8"]
        self.assertEqual("1,2", lixiana["dose_value"])
        self.assertEqual("mg/kg", lixiana["dose_unit"])
        self.assertEqual("einmal täglich", lixiana["frequency"])
        self.assertEqual("oral", lixiana["route"])
        self.assertIn("einmal täglich", lixiana["supporting_source_text"])

        keytruda = records["rec-56f1020a58aad39f9f5721f2"]
        self.assertEqual("subkutane Injektion", keytruda["route"])
        self.assertIn("395 mg alle 3 Wochen", keytruda["supporting_source_text"])
        self.assertIn("790 mg alle 6 Wochen", keytruda["supporting_source_text"])

    def test_vte_repaired_pages_and_locators_are_physical_pdf_pages(self) -> None:
        records = canonical_lookup()
        self.assertEqual(
            [81], records["rec-bfadaf51375042bb9981d516"]["pdf_pages_1based"]
        )
        self.assertEqual(
            [55], records["rec-cbbe0386195f92a39edd7227"]["pdf_pages_1based"]
        )
        coverage = read_jsonl(
            Path("outputs/knowledge_corpus/manifests/coverage_manifest.jsonl")
        )
        vte = (
            "src-003-001l-s3-prophylaxe-venoese-thromboembolie-vte-2026-04-"
            "f82c5686f6b7"
        )
        rows = {
            row["pdf_page_1based"]: row
            for row in coverage
            if row["source_id"] == vte and row["pdf_page_1based"] in {140, 154}
        }
        self.assertEqual({140, 154}, set(rows))
        for row in rows.values():
            self.assertEqual("extracted", row["status"])
            self.assertTrue(row["canonical_owner_batch_id"])
            self.assertTrue(row["validated_batch_ids"])

    def test_unnumbered_item_and_control_characters_have_auditable_views(self) -> None:
        formal = read_jsonl(
            Path("outputs/knowledge_corpus/canonical/formal_items.jsonl")
        )
        unnumbered = [
            row
            for row in formal
            if row.get("canonical_role") == "primary"
            and row.get("source_item_number") is None
        ]
        self.assertEqual(3, len(unnumbered))
        for row in unnumbered:
            self.assertTrue(row.get("uncertainty_reason"))
            self.assertTrue(
                row.get("structured_field_provenance", {}).get(
                    "source_item_number"
                )
            )
        by_id = {row["record_id"]: row for row in unnumbered}
        self.assertEqual(
            "not_printed_in_source",
            by_id["rec-3b4fd85be9e8c296bdca48da"].get("item_number_status"),
        )
        self.assertEqual(
            "printed_duplicate_in_source",
            by_id["rec-70f8457904ae104d4422b8ca"].get("item_number_status"),
        )

        for record in canonical_lookup().values():
            if record.get("exact_source_text") is None:
                continue
            normalized = record.get("normalized_search_text") or ""
            self.assertFalse(
                any(unicodedata.category(character) == "Cc" for character in normalized)
            )
            self.assertEqual(
                hashlib.sha256(record["exact_source_text"].encode("utf-8")).hexdigest(),
                record.get("exact_source_text_raw_sha256"),
            )
            self.assertEqual(
                hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                record.get("normalized_search_text_sha256"),
            )

    def test_hcc_historical_change_tables_are_hard_policy_excluded(self) -> None:
        records = canonical_lookup()
        exclusions = read_jsonl(
            Path("outputs/knowledge_corpus/qa/hcc_historical_exclusions.jsonl")
        )
        self.assertEqual(99, len(exclusions))
        excluded_ids = {row["record_id"] for row in exclusions}
        self.assertEqual(99, len(excluded_ids))
        for record_id in excluded_ids:
            row = records[record_id]
            self.assertEqual(HCC_HISTORICAL_EXCLUSION_REASON, row.get("exclusion_reason"))
            self.assertEqual("historical_secondary", row.get("canonical_role"))
            self.assertEqual("excluded_by_policy", row.get("status"))
            self.assertIs(row.get("retrieval_eligible"), False)
            self.assertIs(row.get("embedding_eligible"), False)
            self.assertIs(row.get("answer_eligible"), False)
            self.assertIs(row.get("primary_search_eligible"), False)

        retrieval = read_jsonl(
            Path("outputs/knowledge_corpus/retrieval/retrieval_units.jsonl")
        )
        embedding = read_jsonl(
            Path("outputs/knowledge_corpus/retrieval/embedding_input.jsonl")
        )
        links = read_jsonl(
            Path("outputs/knowledge_corpus/links/guideline_item_links.jsonl")
        )
        self.assertFalse(
            any(excluded_ids.intersection(row.get("parent_record_ids") or []) for row in retrieval)
        )
        self.assertFalse(
            any(
                excluded_ids.intersection(row.get("metadata", {}).get("parent_record_ids") or [])
                for row in embedding
            )
        )
        self.assertFalse(
            any(
                row.get("from_record_id") in excluded_ids
                or row.get("to_record_id") in excluded_ids
                for row in links
            )
        )
        normal_ids = {
            row["record_id"] for row in filter_normal_search_records(records.values())
        }
        self.assertTrue(excluded_ids.isdisjoint(normal_ids))

        eligible_seed = next(
            record_id
            for record_id, row in records.items()
            if row.get("canonical_role") == "primary"
        )
        excluded_seed = sorted(excluded_ids)[0]
        synthetic_links = [
            {
                "from_record_id": eligible_seed,
                "to_record_id": excluded_seed,
            },
            {
                "from_record_id": excluded_seed,
                "to_record_id": eligible_seed,
            },
        ]
        evidence_ids = {
            row["record_id"]
            for row in build_answer_evidence_package(
                [eligible_seed, excluded_seed], records, [*links, *synthetic_links]
            )
        }
        self.assertIn(eligible_seed, evidence_ids)
        self.assertTrue(excluded_ids.isdisjoint(evidence_ids))

    def test_numbering_gap_audit_does_not_invent_item_numbers(self) -> None:
        audit = json.loads(
            Path("outputs/knowledge_corpus/qa/numbering_gap_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(3, audit["source_native_numbering_gap_count"])
        self.assertEqual(2, audit["new_formal_item_count"])
        self.assertEqual(0, audit["invented_item_number_count"])
        self.assertFalse(audit["gemini_used"])
        self.assertEqual(
            {"15.7", "19.2", "4.29"},
            {row["reported_gap"] for row in audit["reviews"]},
        )
        self.assertTrue(all(row["classification"] == "C" for row in audit["reviews"]))

        formal = read_jsonl(Path("outputs/knowledge_corpus/canonical/formal_items.jsonl"))
        primary = [row for row in formal if row.get("canonical_role") == "primary"]
        vte = [row for row in primary if row["source_id"].startswith("src-003-001l")]
        hcc = [row for row in primary if row["source_id"].startswith("src-s3-ll-hcc")]
        self.assertFalse(any(row.get("source_item_number") == "15.7" for row in vte))
        self.assertFalse(any(row.get("source_item_number") == "19.2" for row in vte))
        self.assertFalse(any(row.get("source_item_number") == "4.29" for row in hcc))
        self.assertEqual(
            "not_printed_in_source",
            next(
                row
                for row in hcc
                if row["record_id"] == "rec-3b4fd85be9e8c296bdca48da"
            )["item_number_status"],
        )
        self.assertTrue(
            set(audit["new_formal_item_record_ids"])
            <= {row["record_id"] for row in primary}
        )
        links = read_jsonl(
            Path("outputs/knowledge_corpus/links/guideline_item_links.jsonl")
        )
        for record_id in audit["new_formal_item_record_ids"]:
            self.assertTrue(
                any(
                    link.get("from_record_id") == record_id
                    and link.get("link_type") == "guideline_item_to_rationale"
                    and link.get("to_record_type") == "rationale_block"
                    for link in links
                )
            )

    def test_final_flag_examples_are_balanced_and_source_verified(self) -> None:
        with Path("outputs/knowledge_corpus/qa/flag_examples.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(15, len(rows))
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["flag_type"]] = counts.get(row["flag_type"], 0) + 1
            self.assertEqual("True", row["quote_locally_verified"])
            self.assertTrue(row["exact_source_excerpt"])
            self.assertTrue(row["record_id"])
        self.assertEqual(
            {
                "dose_entity_recovered_from_immediate_context_or_flagged": 5,
                "adverse_reaction_term_recovered_from_exact_source_text": 5,
                "dose_value_not_explicit": 5,
            },
            counts,
        )

    def test_targeted_repair_remaining_is_header_only(self) -> None:
        lines = Path("outputs/knowledge_corpus/qa/targeted_repair_remaining.csv").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(1, len(lines))


if __name__ == "__main__":
    unittest.main()

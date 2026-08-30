from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import jsonschema

from aisurgeon_decentralised.retrieval_evaluation import (
    GOLD_FIELDS,
    STRATA,
    build_annotation_package,
    evaluate_records,
    read_jsonl,
    sha256_file,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class AnnotationPackageTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        snapshot_id = "cs-testfixture000000000000"
        manifest = root / "outputs/knowledge_corpus/manifests/corpus_snapshots/snapshot.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "corpus_snapshot_id": snapshot_id,
                    "created_at_utc": "2026-08-16T00:00:00+00:00",
                    "content_fingerprint_sha256": "a" * 64,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        common = {
            "corpus_snapshot_id": snapshot_id,
            "eligibility_status": "eligible",
            "excluded_by_policy": False,
            "retrieval_eligible": True,
            "answer_eligible": True,
            "source_status": "final",
            "document_component": "guideline",
            "parent_record_ids": ["rec-1"],
            "pdf_pages_1based": [1],
            "raw_v1": {"evidence_metadata": {}},
        }
        units = [
            {
                **common,
                "retrieval_unit_id": "ru-guideline-rec",
                "source_role": "guideline",
                "source_native_item_type": "recommendation",
                "source_native_item_number": "3.1",
                "chapter_path": ["VTE-Prophylaxe"],
                "retrieval_segment_text": "Acetylsalicylsäure sollte nicht routinemäßig eingesetzt werden.",
                "raw_v1": {"evidence_metadata": {"evidence_level": "moderat"}},
                "source_version_id": "sv-guideline",
                "source_file_name": "guideline.pdf",
            },
            {
                **common,
                "retrieval_unit_id": "ru-guideline-statement",
                "source_role": "guideline",
                "source_native_item_type": "statement",
                "chapter_path": ["Antikoagulation"],
                "retrieval_segment_text": "Apixaban wird in diesem Abschnitt erwähnt.",
                "source_version_id": "sv-guideline",
                "source_file_name": "guideline.pdf",
            },
            {
                **common,
                "retrieval_unit_id": "ru-draft",
                "source_role": "guideline",
                "source_status": "consultation_draft",
                "source_native_item_type": "rationale_block",
                "chapter_path": ["HCC"],
                "retrieval_segment_text": "Konsultationsentwurf zum HCC.",
                "source_version_id": "sv-draft",
                "source_file_name": "consultation.pdf",
            },
            {
                **common,
                "retrieval_unit_id": "ru-dose",
                "source_role": "smPC",
                "document_component": "smPC",
                "source_native_item_type": "dosing_rule",
                "chapter_path": ["4.2 Dosierung"],
                "retrieval_segment_text": "Apixaban 5 mg zweimal täglich oral.",
                "product_names": ["Eliquis"],
                "active_substance_names": ["Apixaban"],
                "dose_value": "5",
                "dose_unit": "mg",
                "frequency": "zweimal täglich",
                "route": "oral",
                "source_version_id": "sv-smpc",
                "source_file_name": "eliquis.pdf",
            },
            {
                **common,
                "retrieval_unit_id": "ru-warning",
                "source_role": "smPC",
                "document_component": "smPC",
                "source_native_item_type": "warning",
                "chapter_path": ["4.4 Warnhinweise"],
                "retrieval_segment_text": "Warnhinweis für Xarelto.",
                "product_names": ["Xarelto"],
                "active_substance_names": ["Rivaroxaban"],
                "source_version_id": "sv-xarelto",
                "source_file_name": "xarelto.pdf",
            },
            {
                **common,
                "retrieval_unit_id": "ru-contra",
                "source_role": "smPC",
                "document_component": "smPC",
                "source_native_item_type": "contraindication",
                "chapter_path": ["4.3 Gegenanzeigen"],
                "retrieval_segment_text": "Gegenanzeige für Xarelto.",
                "product_names": ["Xarelto"],
                "active_substance_names": ["Rivaroxaban"],
                "source_version_id": "sv-xarelto",
                "source_file_name": "xarelto.pdf",
            },
            {
                **common,
                "retrieval_unit_id": "ru-preparation",
                "source_role": "smPC",
                "document_component": "smPC",
                "source_native_item_type": "preparation_administration",
                "chapter_path": ["Zubereitung"],
                "retrieval_segment_text": "Zubereitung von KEYTRUDA.",
                "product_names": ["KEYTRUDA"],
                "active_substance_names": ["Pembrolizumab"],
                "route": "intravenös",
                "source_version_id": "sv-keytruda",
                "source_file_name": "keytruda.pdf",
            },
            {
                **common,
                "retrieval_unit_id": "ru-adverse",
                "source_role": "smPC",
                "document_component": "smPC",
                "source_native_item_type": "adverse_reaction",
                "chapter_path": ["4.8 Nebenwirkungen"],
                "retrieval_segment_text": "Nebenwirkung von KEYTRUDA.",
                "product_names": ["KEYTRUDA"],
                "active_substance_names": ["Pembrolizumab"],
                "source_version_id": "sv-keytruda",
                "source_file_name": "keytruda.pdf",
            },
        ]
        retrieval = root / "retrieval.jsonl"
        write_jsonl(retrieval, units)
        exclusions = root / "hcc_exclusions.jsonl"
        write_jsonl(
            exclusions,
            [
                {
                    "record_id": "rec-historical",
                    "pdf_pages_1based": [191],
                    "exclusion_reason": "hcc_historical_change_table",
                }
            ],
        )
        return manifest, retrieval, exclusions

    def test_package_is_deterministic_stratified_and_unlabelled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, retrieval, exclusions = self.fixture(root)
            first = root / "first"
            second = root / "second"
            result = build_annotation_package(
                project_root=root,
                output_dir=first,
                snapshot_manifest_path=manifest,
                retrieval_units_path=retrieval,
                hcc_exclusions_path=exclusions,
            )
            build_annotation_package(
                project_root=root,
                output_dir=second,
                snapshot_manifest_path=manifest,
                retrieval_units_path=retrieval,
                hcc_exclusions_path=exclusions,
            )
            self.assertEqual(300, result["counts"]["total"])
            self.assertEqual(50, result["counts"]["development"])
            self.assertEqual(250, result["counts"]["test_untouched"])
            self.assertEqual(75, result["counts"]["no_evidence_or_out_of_scope"])
            self.assertEqual(25.0, result["counts"]["no_evidence_or_out_of_scope_percent"])
            items = read_jsonl(first / "authoring_items.jsonl")
            self.assertEqual(set(STRATA), {row["primary_stratum"] for row in items})
            self.assertEqual(300, len({row["question_text"] for row in items}))
            self.assertEqual(
                {stratum: 2 for stratum in STRATA},
                dict(Counter(row["primary_stratum"] for row in items if row["split"] == "development")),
            )
            self.assertEqual(
                {stratum: 10 for stratum in STRATA},
                dict(Counter(row["primary_stratum"] for row in items if row["split"] == "test")),
            )
            self.assertTrue(all(row["origin"] == "synthetic_draft" for row in items))
            self.assertTrue(
                all(set(row["gold"]) == set(GOLD_FIELDS) for row in items)
            )
            self.assertTrue(
                all(all(value is None for value in row["gold"].values()) for row in items)
            )
            schema = json.loads((first / "annotation.schema.json").read_text(encoding="utf-8"))
            for item in items:
                jsonschema.validate(item, schema)
            adjudication_schema = json.loads(
                (first / "adjudication.schema.json").read_text(encoding="utf-8")
            )
            for row in read_jsonl(first / "adjudication_template.jsonl"):
                jsonschema.validate(row, adjudication_schema)
            self.assertEqual(
                12,
                sum(len(row["turns"]) > 1 for row in items if row["primary_stratum"] == "multi_turn"),
            )
            blind = read_jsonl(first / "test_blind_questions.jsonl")
            self.assertEqual(250, len(blind))
            self.assertFalse(
                {"primary_stratum", "sampling_scope", "seed_evidence", "gold"}
                .intersection(blind[0])
            )
            self.assertEqual(
                sorted(path.name for path in first.iterdir()),
                sorted(path.name for path in second.iterdir()),
            )
            for path in first.iterdir():
                self.assertEqual(sha256_file(path), sha256_file(second / path.name), path.name)

    def test_canaries_never_become_gold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, retrieval, exclusions = self.fixture(root)
            output = root / "package"
            build_annotation_package(
                project_root=root,
                output_dir=output,
                snapshot_manifest_path=manifest,
                retrieval_units_path=retrieval,
                hcc_exclusions_path=exclusions,
            )
            canaries = [
                row
                for row in read_jsonl(output / "authoring_items.jsonl")
                if row["primary_stratum"] == "hcc_history_canary"
            ]
            self.assertEqual(12, len(canaries))
            self.assertTrue(all(row["policy_canary"]["record_id"] == "rec-historical" for row in canaries))
            self.assertTrue(all(row["gold"]["gold_evidence_ids"] is None for row in canaries))


class RetrievalMetricTests(unittest.TestCase):
    def test_core_metrics_and_policy_leakage(self) -> None:
        rows = [
            {
                "question_id": "q1",
                "gold_evidence_ids": ["a", "b"],
                "retrieved_evidence_ids": ["a", "x", "b"],
                "cited_evidence_ids": ["a"],
                "gold_support_label": "supported",
                "predicted_support_label": "supported",
                "gold_should_abstain": False,
                "predicted_abstained": False,
                "source_span_sufficient": True,
                "entity_attribution_correct": True,
                "dose_exact_match": True,
                "total_claims": 2,
                "unsupported_claims": 0,
                "harmful_unsupported_claims": 0,
                "latency_ms": 10,
                "input_tokens": 10,
                "output_tokens": 5,
                "cost": 0.01,
                "price_list_timestamp": "2026-08-01",
            },
            {
                "question_id": "q2",
                "gold_evidence_ids": ["c"],
                "retrieved_evidence_ids": ["x", "c"],
                "excluded_evidence_ids": ["x"],
                "cited_evidence_ids": ["x"],
                "gold_support_label": "no_validated_evidence",
                "predicted_support_label": "partially_supported",
                "gold_should_abstain": True,
                "predicted_abstained": False,
                "source_span_sufficient": False,
                "entity_attribution_correct": False,
                "dose_exact_match": False,
                "total_claims": 2,
                "unsupported_claims": 1,
                "harmful_unsupported_claims": 1,
                "latency_ms": 20,
                "input_tokens": 20,
                "output_tokens": 10,
                "cost": 0.02,
                "price_list_timestamp": "2026-08-01",
            },
        ]
        report = evaluate_records(rows, k_values=(1, 3))
        self.assertAlmostEqual(0.25, report["ranking"]["evidence_recall_at_1"]["value"])
        self.assertAlmostEqual(1.0, report["ranking"]["evidence_recall_at_3"]["value"])
        self.assertAlmostEqual(0.75, report["ranking"]["mrr"]["value"])
        self.assertAlmostEqual(0.5, report["citations"]["citation_precision"]["value"])
        self.assertAlmostEqual(0.25, report["citations"]["citation_completeness"]["value"])
        self.assertAlmostEqual(1 / 3, report["answers"]["support_label_macro_f1"]["value"])
        self.assertEqual(0.25, report["answers"]["unsupported_claim_rate"]["value"])
        self.assertEqual(0.2, report["policy"]["exclusion_leakage_rate"]["value"])
        self.assertEqual(15.0, report["operations"]["latency_ms"]["p50"])
        self.assertEqual(30, report["operations"]["tokens"]["input_tokens"]["total"])
        self.assertAlmostEqual(0.03, report["operations"]["cost"]["total"])

    def test_missing_human_fields_are_unavailable_not_zero(self) -> None:
        report = evaluate_records([{"question_id": "q", "retrieved_evidence_ids": []}])
        self.assertIsNone(report["ranking"]["mrr"]["value"])
        self.assertEqual(0, report["ranking"]["mrr"]["n"])
        self.assertIsNone(report["answers"]["unsupported_claim_rate"]["value"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aisurgeon_decentralised.smpc_guideline_bridge import (
    BridgeLocator,
    SmPCGuidelineBridgeCatalog,
    SmPCGuidelineBridgeRow,
    write_bridge_artifacts,
)


def _locator(evidence_id: str, role: str) -> BridgeLocator:
    return BridgeLocator(
        evidence_id=evidence_id,
        source_document_id=f"src-{role}",
        source_version_id=f"sv-{role}",
        source_file_name=f"{role}.pdf",
        source_status="final",
        source_role=role,
        pdf_pages_1based=(1,),
    )


def _row(*, active: bool = True) -> SmPCGuidelineBridgeRow:
    guideline = _locator("ru-guideline", "guideline") if active else None
    return SmPCGuidelineBridgeRow(
        bridge_id="bridge-test",
        corpus_snapshot_id="cs-test",
        source_document_id="src-smpc",
        source_version_id="sv-smpc",
        source_file_name="smpc.pdf",
        source_document_title="Test SmPC",
        product_name="Testprodukt",
        product_ids=("product-test",),
        trade_names_and_variants=("Testprodukt",),
        active_substance_id="substance-test",
        normalized_active_substance="Teststoff",
        active_substance_aliases=("Teststoff",),
        matched_alias="Teststoff" if active else None,
        guideline_evidence_id="ru-guideline" if active else None,
        guideline_source_document_id="src-guideline" if active else None,
        guideline_record_ids=("rec-guideline",) if active else (),
        guideline_formal_item_ids=("formal-guideline",) if active else (),
        guideline_item_number="1.1" if active else None,
        matching_method="exact" if active else None,
        confidence=1.0 if active else 0.0,
        smpc_evidence=(_locator("ru-smpc", "smPC"),),
        guideline_evidence=guideline,
        evidence_ids=("ru-smpc", "ru-guideline") if active else ("ru-smpc",),
        policy_eligible=active,
        bridge_active=active,
        review_status="active_validated" if active else "unmatched_no_error",
    )


class DirectedBridgeTests(unittest.TestCase):
    def test_expansion_requires_smpc_seed_and_is_never_reverse(self) -> None:
        catalog = SmPCGuidelineBridgeCatalog((_row(),))
        self.assertEqual(
            catalog.expand_from_smpc_candidates(
                [
                    {
                        "retrieval_unit_id": "ru-smpc",
                        "source_document_id": "src-smpc",
                        "source_role": "smPC",
                    }
                ]
            )[0].retrieval_unit_id,
            "ru-guideline",
        )
        self.assertEqual(
            catalog.expand_from_smpc_candidates(
                [
                    {
                        "retrieval_unit_id": "ru-guideline",
                        "source_document_id": "src-guideline",
                        "source_role": "guideline",
                    }
                ]
            ),
            (),
        )

    def test_unmatched_smpc_is_not_an_error_or_active_relation(self) -> None:
        row = _row(active=False)
        catalog = SmPCGuidelineBridgeCatalog((row,))
        self.assertEqual(row.review_status, "unmatched_no_error")
        self.assertFalse(row.bridge_active)
        self.assertEqual(
            catalog.expand_from_smpc_candidates(
                [
                    {
                        "retrieval_unit_id": "ru-smpc",
                        "source_document_id": "src-smpc",
                        "source_role": "smPC",
                    }
                ]
            ),
            (),
        )

    def test_formal_guideline_target_is_expanded_before_nonformal_context(self) -> None:
        formal = _row()
        context_locator = _locator("ru-context", "guideline")
        context = formal.model_copy(
            update={
                "bridge_id": "bridge-context",
                "guideline_evidence_id": "ru-context",
                "guideline_record_ids": ("rec-context",),
                "guideline_formal_item_ids": (),
                "guideline_item_number": None,
                "guideline_evidence": context_locator,
                "evidence_ids": ("ru-smpc", "ru-context"),
            }
        )
        expanded = SmPCGuidelineBridgeCatalog((context, formal)).expand_from_smpc_candidates(
            [
                {
                    "retrieval_unit_id": "ru-smpc",
                    "source_document_id": "src-smpc",
                    "source_role": "smPC",
                }
            ]
        )
        self.assertEqual("ru-guideline", expanded[0].retrieval_unit_id)

    def test_artifacts_are_written_with_zero_reverse_relations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            qa = write_bridge_artifacts((_row(), _row(active=False)), output_dir=Path(temporary))
            self.assertEqual(qa["reverse_relation_count"], 0)
            self.assertEqual(qa["unmatched_no_error_count"], 1)
            self.assertTrue((Path(temporary) / "smpc_guideline_bridge.csv").exists())


if __name__ == "__main__":
    unittest.main()

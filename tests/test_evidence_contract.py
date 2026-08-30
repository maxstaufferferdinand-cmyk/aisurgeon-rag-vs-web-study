from __future__ import annotations

import unittest

from aisurgeon_decentralised.evidence_contract import (
    ApplicabilityStatus,
    ClaimDraft,
    ConflictStatus,
    EntailmentStatus,
    EvidenceRecord,
    ExcludedEvidenceError,
    PublicSupportLabel,
    RetrievalOutcome,
    UnknownEvidenceError,
    ValidatorStatus,
    build_evidence_package,
    render_backend_citation,
    validate_claim,
)

SNAPSHOT_ID = "snapshot-test"


def evidence(evidence_id: str, **changes: object) -> EvidenceRecord:
    values: dict[str, object] = {
        "evidence_id": evidence_id,
        "corpus_snapshot_id": SNAPSHOT_ID,
        "source_document_id": "source-document-1",
        "source_version_id": "source-version-1",
        "document_name": "Öffentliche Fachinformation",
        "version_label": "Version 1",
        "source_status": "final",
        "source_role": "smPC",
        "source_authority": "regulatory_product_information",
        "document_component": "smPC",
        "source_file_name": "public.pdf",
        "source_link": "/sources/public.pdf",
        "exact_source_text": (
            "Erwachsene erhalten 200 mg alle 3 Wochen als intravenöse Gabe."
        ),
        "pdf_pages_1based": (7,),
        "printed_page_label": "5",
        "dose_value": "200",
        "dose_unit": "mg",
        "frequency": "alle 3 Wochen",
        "route": "intravenöse Gabe",
        "population": "Erwachsene",
        "negated": False,
    }
    values.update(changes)
    return EvidenceRecord.model_validate(values)


class EvidenceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.direct = evidence("ru-direct")
        self.other = evidence("ru-other", dose_value="100")
        self.draft = evidence(
            "ru-draft",
            source_document_id="source-document-draft",
            source_version_id="source-version-draft",
            document_name="Konsultationsfassung",
            source_status="consultation_draft",
        )
        self.excluded = evidence(
            "ru-excluded",
            eligibility_status="ineligible",
            retrieval_eligible=False,
            answer_eligible=False,
            excluded_by_policy=True,
            exclusion_reason="hcc_historical_change_table",
        )
        self.catalog = {
            row.evidence_id: row
            for row in (self.direct, self.other, self.draft, self.excluded)
        }
        self.package = build_evidence_package(
            corpus_snapshot_id=SNAPSHOT_ID,
            evidence_ids=(self.direct.evidence_id, self.draft.evidence_id),
            evidence_catalog=self.catalog,
        )

    def claim(self, **changes: object) -> ClaimDraft:
        values: dict[str, object] = {
            "answer_claim_id": "claim-1",
            "claim_text": "Technischer Testclaim.",
            "evidence_ids": ("ru-direct",),
            "proposed_public_label": "supported",
            "entailment_status": "supported",
            "retrieval_outcome": "evidence_found",
            "retrieval_fallback_complete": True,
            "conflict_status": "none",
            "applicability_status": "applicable",
            "dose_value": "200.0",
            "dose_unit": "mg",
            "frequency": "alle 3 Wochen",
            "route": "i.v.",
            "population": "Erwachsene",
            "negated": False,
        }
        values.update(changes)
        return ClaimDraft.model_validate(values)

    def test_public_and_internal_status_values_are_exact(self) -> None:
        self.assertEqual(
            {"supported", "partially_supported", "no_validated_evidence"},
            {item.value for item in PublicSupportLabel},
        )
        self.assertEqual(
            {"supported", "partial", "contradicted", "insufficient"},
            {item.value for item in EntailmentStatus},
        )
        self.assertEqual(
            {"none", "guideline_vs_smpc", "within_guideline", "version_conflict"},
            {item.value for item in ConflictStatus},
        )
        self.assertEqual(
            {"applicable", "uncertain", "not_applicable"},
            {item.value for item in ApplicabilityStatus},
        )

    def test_package_allowlist_rejects_unknown_and_excluded_ids(self) -> None:
        with self.assertRaises(UnknownEvidenceError):
            build_evidence_package(
                corpus_snapshot_id=SNAPSHOT_ID,
                evidence_ids=("unknown",),
                evidence_catalog=self.catalog,
            )
        with self.assertRaises(ExcludedEvidenceError):
            build_evidence_package(
                corpus_snapshot_id=SNAPSHOT_ID,
                evidence_ids=("ru-excluded",),
                evidence_catalog=self.catalog,
            )

    def test_backend_renders_document_version_page_status_and_link(self) -> None:
        citation = render_backend_citation(self.draft)
        self.assertIn("Konsultationsfassung", citation.label)
        self.assertIn("Version 1", citation.label)
        self.assertIn("PDF-S. 7", citation.label)
        self.assertIn("consultation_draft", citation.label)
        self.assertEqual("/sources/public.pdf#page=7", citation.link)

    def test_claim_cannot_supply_document_version_page_or_link(self) -> None:
        payload = self.claim().model_dump(mode="json")
        for forbidden_field in (
            "document_name",
            "version_label",
            "pdf_pages_1based",
            "source_link",
        ):
            with self.subTest(field=forbidden_field):
                with self.assertRaises(ValueError):
                    ClaimDraft.model_validate(
                        {**payload, forbidden_field: "model-supplied metadata"}
                    )

    def test_supported_claim_is_accepted_with_backend_citation(self) -> None:
        result = validate_claim(
            self.claim(), package=self.package, evidence_catalog=self.catalog
        )
        self.assertEqual(ValidatorStatus.ACCEPTED, result.validator_status)
        self.assertEqual(PublicSupportLabel.SUPPORTED, result.public_support_label)
        self.assertEqual(("ru-direct",), result.validated_evidence_ids)
        self.assertEqual(1, len(result.citations))
        self.assertTrue(result.publishable)

    def test_unknown_excluded_and_out_of_package_ids_are_rejected(self) -> None:
        cases = {
            "unknown": "unknown_evidence_id",
            "ru-excluded": "excluded_evidence_id",
            "ru-other": "evidence_id_not_in_current_package",
        }
        for evidence_id, expected_code in cases.items():
            with self.subTest(evidence_id=evidence_id):
                result = validate_claim(
                    self.claim(evidence_ids=(evidence_id,)),
                    package=self.package,
                    evidence_catalog=self.catalog,
                )
                self.assertEqual(ValidatorStatus.REJECTED, result.validator_status)
                self.assertIsNone(result.public_support_label)
                self.assertIn(expected_code, {issue.code for issue in result.issues})

    def test_consultation_draft_cannot_satisfy_expected_final_status(self) -> None:
        result = validate_claim(
            self.claim(
                evidence_ids=("ru-draft",), expected_source_status="final"
            ),
            package=self.package,
            evidence_catalog=self.catalog,
        )
        self.assertEqual(ValidatorStatus.REJECTED, result.validator_status)
        self.assertIn("source_status_mismatch", {issue.code for issue in result.issues})

    def test_dose_unit_frequency_route_and_population_mismatches_reject(self) -> None:
        cases = {
            "dose_value": "201",
            "dose_unit": "mg/kg",
            "frequency": "täglich",
            "route": "oral",
            "population": "Kinder",
        }
        for field, bad_value in cases.items():
            with self.subTest(field=field):
                result = validate_claim(
                    self.claim(**{field: bad_value}),
                    package=self.package,
                    evidence_catalog=self.catalog,
                )
                self.assertEqual(ValidatorStatus.REJECTED, result.validator_status)
                self.assertIn(
                    f"{field}_mismatch", {issue.code for issue in result.issues}
                )
                if field == "population":
                    self.assertEqual(
                        ApplicabilityStatus.NOT_APPLICABLE,
                        result.applicability_status,
                    )

    def test_missing_structured_field_downgrades_to_partial(self) -> None:
        missing_frequency = evidence("ru-missing-frequency", frequency=None)
        catalog = {missing_frequency.evidence_id: missing_frequency}
        package = build_evidence_package(
            corpus_snapshot_id=SNAPSHOT_ID,
            evidence_ids=(missing_frequency.evidence_id,),
            evidence_catalog=catalog,
        )
        result = validate_claim(
            self.claim(evidence_ids=(missing_frequency.evidence_id,)),
            package=package,
            evidence_catalog=catalog,
        )
        self.assertEqual(ValidatorStatus.DOWNGRADED, result.validator_status)
        self.assertEqual(
            PublicSupportLabel.PARTIALLY_SUPPORTED, result.public_support_label
        )
        self.assertEqual(EntailmentStatus.PARTIAL, result.entailment_status)

    def test_negation_mismatch_is_rejected_as_insufficient(self) -> None:
        negative = evidence(
            "ru-negative",
            exact_source_text="Die Maßnahme sollte nicht erfolgen.",
            negated=True,
        )
        catalog = {negative.evidence_id: negative}
        package = build_evidence_package(
            corpus_snapshot_id=SNAPSHOT_ID,
            evidence_ids=(negative.evidence_id,),
            evidence_catalog=catalog,
        )
        result = validate_claim(
            self.claim(evidence_ids=(negative.evidence_id,), negated=False),
            package=package,
            evidence_catalog=catalog,
        )
        self.assertEqual(ValidatorStatus.REJECTED, result.validator_status)
        self.assertEqual(EntailmentStatus.CONTRADICTED, result.entailment_status)
        self.assertIn("negation_mismatch", {issue.code for issue in result.issues})

    def test_package_binding_rejects_changed_backend_metadata(self) -> None:
        changed_catalog = dict(self.catalog)
        changed_catalog["ru-direct"] = self.direct.model_copy(
            update={"dose_value": "999"}
        )
        result = validate_claim(
            self.claim(), package=self.package, evidence_catalog=changed_catalog
        )
        self.assertEqual(ValidatorStatus.REJECTED, result.validator_status)
        self.assertIn(
            "evidence_metadata_differs_from_current_package",
            {issue.code for issue in result.issues},
        )

    def test_missing_locator_is_rejected(self) -> None:
        no_locator = evidence("ru-no-locator", pdf_pages_1based=(), source_link=None)
        catalog = {no_locator.evidence_id: no_locator}
        package = build_evidence_package(
            corpus_snapshot_id=SNAPSHOT_ID,
            evidence_ids=(no_locator.evidence_id,),
            evidence_catalog=catalog,
        )
        result = validate_claim(
            self.claim(evidence_ids=(no_locator.evidence_id,)),
            package=package,
            evidence_catalog=catalog,
        )
        self.assertEqual(ValidatorStatus.REJECTED, result.validator_status)
        self.assertIn("missing_source_location", {issue.code for issue in result.issues})

    def test_no_validated_evidence_requires_complete_successful_fallback(self) -> None:
        valid = validate_claim(
            self.claim(
                evidence_ids=(),
                proposed_public_label="no_validated_evidence",
                entailment_status="insufficient",
                retrieval_outcome="no_evidence_in_snapshot",
                retrieval_fallback_complete=True,
                dose_value=None,
                dose_unit=None,
                frequency=None,
                route=None,
                population=None,
                negated=None,
            ),
            package=self.package,
            evidence_catalog=self.catalog,
        )
        self.assertEqual(ValidatorStatus.ACCEPTED, valid.validator_status)
        self.assertEqual(
            PublicSupportLabel.NO_VALIDATED_EVIDENCE, valid.public_support_label
        )

        incomplete = validate_claim(
            self.claim(
                evidence_ids=(),
                entailment_status="insufficient",
                retrieval_outcome="no_evidence_in_snapshot",
                retrieval_fallback_complete=False,
                dose_value=None,
                dose_unit=None,
                frequency=None,
                route=None,
                population=None,
                negated=None,
            ),
            package=self.package,
            evidence_catalog=self.catalog,
        )
        self.assertEqual(ValidatorStatus.REJECTED, incomplete.validator_status)
        self.assertIsNone(incomplete.public_support_label)

    def test_retrieval_failure_never_becomes_no_validated_evidence(self) -> None:
        result = validate_claim(
            self.claim(
                evidence_ids=(),
                proposed_public_label="no_validated_evidence",
                entailment_status="insufficient",
                retrieval_outcome=RetrievalOutcome.RETRIEVAL_FAILURE,
                dose_value=None,
                dose_unit=None,
                frequency=None,
                route=None,
                population=None,
                negated=None,
            ),
            package=self.package,
            evidence_catalog=self.catalog,
        )
        self.assertEqual(ValidatorStatus.REJECTED, result.validator_status)
        self.assertIsNone(result.public_support_label)


if __name__ == "__main__":
    unittest.main()

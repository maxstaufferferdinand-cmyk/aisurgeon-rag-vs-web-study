from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from aisurgeon_decentralised.evidence_contract import (
    EvidenceRecord,
    build_evidence_package,
)
from aisurgeon_decentralised.rag_core import (
    RagCore,
    validate_structured_answer,
)
from aisurgeon_decentralised.rag_responses import (
    ClosedResponsesError,
    RagStructuredAnswer,
)
from aisurgeon_decentralised.rag_telemetry import RagTelemetrySink


def _record() -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ru-allowed",
        corpus_snapshot_id="cs-test",
        source_document_id="doc-vte",
        source_version_id="version-vte",
        document_name="VTE-Leitlinie",
        version_label="4.1",
        source_status="final",
        source_role="guideline",
        source_authority="AWMF",
        document_component="guideline",
        source_file_name="vte.pdf",
        source_link="source_pdfs/vte.pdf",
        exact_source_text="Die Prophylaxe soll für 28-35 Tage erfolgen.",
        pdf_pages_1based=(86,),
    )


def _package():
    record = _record()
    package = build_evidence_package(
        corpus_snapshot_id="cs-test",
        evidence_ids=(record.evidence_id,),
        evidence_catalog={record.evidence_id: record},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return package, {record.evidence_id: record}


class RagCoreValidationTests(unittest.TestCase):
    def test_backend_renders_citation_for_allowed_claim(self) -> None:
        package, catalog = _package()
        answer = RagStructuredAnswer(
            answer_status="supported",
            answer_text="Die Prophylaxe soll 28-35 Tage erfolgen.",
            claims=[
                {
                    "claim_text": "Die Prophylaxe soll 28-35 Tage erfolgen.",
                    "evidence_ids": ["ru-allowed"],
                    "support_status": "supported",
                }
            ],
            limitations=[],
            abstention_reason=None,
        )
        result = validate_structured_answer(
            answer,
            package=package,
            evidence_catalog=catalog,
            retrieval_outcome="evidence_found",
            retrieval_fallback_complete=True,
        )
        self.assertTrue(result.publishable)
        self.assertEqual("supported", result.answer_status)
        self.assertEqual("ru-allowed", result.citations[0].evidence_id)
        self.assertIn("PDF-S. 86", result.citations[0].label)

    def test_unknown_evidence_id_rejects_entire_answer(self) -> None:
        package, catalog = _package()
        answer = RagStructuredAnswer(
            answer_status="supported",
            answer_text="Nicht belegter Inhalt.",
            claims=[
                {
                    "claim_text": "Nicht belegter Inhalt.",
                    "evidence_ids": ["ru-invented"],
                    "support_status": "supported",
                }
            ],
            limitations=[],
            abstention_reason=None,
        )
        result = validate_structured_answer(
            answer,
            package=package,
            evidence_catalog=catalog,
            retrieval_outcome="evidence_found",
            retrieval_fallback_complete=True,
        )
        self.assertFalse(result.publishable)
        self.assertEqual("", result.answer_text)
        self.assertIn("unknown_evidence_id", result.validator_issue_codes)

    def test_unseen_numeric_value_is_rejected(self) -> None:
        package, catalog = _package()
        answer = RagStructuredAnswer(
            answer_status="supported",
            answer_text="Die Prophylaxe soll 99 Tage erfolgen.",
            claims=[
                {
                    "claim_text": "Die Prophylaxe soll 99 Tage erfolgen.",
                    "evidence_ids": ["ru-allowed"],
                    "support_status": "supported",
                }
            ],
            limitations=[],
            abstention_reason=None,
        )
        result = validate_structured_answer(
            answer,
            package=package,
            evidence_catalog=catalog,
            retrieval_outcome="evidence_found",
            retrieval_fallback_complete=True,
        )
        self.assertFalse(result.publishable)
        self.assertIn(
            "numeric_value_not_in_cited_evidence",
            result.validator_issue_codes,
        )

    def test_complete_empty_fallback_accepts_required_abstention(self) -> None:
        package = build_evidence_package(
            corpus_snapshot_id="cs-test", evidence_ids=(), evidence_catalog={}
        )
        answer = RagStructuredAnswer(
            answer_status="no_validated_evidence",
            answer_text="",
            claims=[],
            limitations=["Keine passende Evidenz im Snapshot."],
            abstention_reason="Keine passende Evidenz.",
        )
        result = validate_structured_answer(
            answer,
            package=package,
            evidence_catalog={},
            retrieval_outcome="no_evidence_in_snapshot",
            retrieval_fallback_complete=True,
        )
        self.assertTrue(result.publishable)
        self.assertEqual("no_validated_evidence", result.answer_status)

    def test_abstention_is_allowed_when_candidates_are_insufficient(self) -> None:
        package, catalog = _package()
        answer = RagStructuredAnswer(
            answer_status="no_validated_evidence",
            answer_text="",
            claims=[],
            limitations=["Die Kandidaten reichen für eine Antwort nicht aus."],
            abstention_reason="Keine ausreichende Evidenz im Paket.",
        )
        result = validate_structured_answer(
            answer,
            package=package,
            evidence_catalog=catalog,
            retrieval_outcome="evidence_found",
            retrieval_fallback_complete=True,
        )
        self.assertTrue(result.publishable)
        self.assertEqual("no_validated_evidence", result.answer_status)

    def test_no_context_baseline_is_never_publishable(self) -> None:
        package = build_evidence_package(
            corpus_snapshot_id="cs-test", evidence_ids=(), evidence_catalog={}
        )
        answer = RagStructuredAnswer(
            answer_status="supported",
            answer_text="Modellwissen ohne lokale Evidenz.",
            claims=[
                {
                    "claim_text": "Modellwissen ohne lokale Evidenz.",
                    "evidence_ids": [],
                    "support_status": "supported",
                }
            ],
            limitations=[],
            abstention_reason=None,
        )
        result = validate_structured_answer(
            answer,
            package=package,
            evidence_catalog={},
            retrieval_outcome="retrieval_failure",
            retrieval_fallback_complete=False,
            baseline_without_retrieval=True,
        )
        self.assertFalse(result.publishable)
        self.assertIn(
            "baseline_without_retrieval_not_publishable",
            result.validator_issue_codes,
        )


class _FailingResponsesClient:
    config = SimpleNamespace(
        model="test-model",
        reasoning_effort="none",
        max_output_tokens=100,
    )

    def answer(self, **_kwargs):
        raise ClosedResponsesError(
            "test failure",
            status_code=500,
            retry_count=3,
            retry_statuses=(500, 500, 500, 500),
            api_wall_time_ms=12.0,
            error_code="InternalServerError",
        )


class RagCoreErrorTelemetryTests(unittest.TestCase):
    def test_api_error_is_traced_without_question_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            core = RagCore(
                root=Path(__file__).resolve().parents[1],
                corpus_snapshot_id="cs-f61b3d4e90089c1b890c23cb",
                responses_client=_FailingResponsesClient(),
                telemetry_sink=RagTelemetrySink(path),
            )
            with self.assertRaises(ClosedResponsesError):
                core.run(
                    question="synthetische Fehlerfrage",
                    question_id="error-1",
                    baseline_without_retrieval=True,
                )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(500, payload["http_status"])
            self.assertEqual(3, payload["retry_count"])
            self.assertNotIn("synthetische Fehlerfrage", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from aisurgeon_decentralised.evidence_contract import (
    EvidenceRecord,
    build_evidence_package,
)
from aisurgeon_decentralised.rag_responses import (
    ClosedResponsesClient,
    ClosedResponsesConfig,
    RagStructuredAnswer,
    build_closed_request_text,
)


def _package():
    evidence = EvidenceRecord(
        evidence_id="ru-1",
        corpus_snapshot_id="cs-1",
        source_document_id="src-1",
        source_version_id="sv-1",
        document_name="Leitlinie",
        source_status="final",
        source_role="guideline",
        source_authority="AWMF",
        document_component="guideline",
        source_file_name="source.pdf",
        source_link="source_pdfs/source.pdf",
        exact_source_text="Belegter Quelltext.",
        pdf_pages_1based=(1,),
    )
    return build_evidence_package(
        corpus_snapshot_id="cs-1",
        evidence_ids=("ru-1",),
        evidence_catalog={"ru-1": evidence},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class _Raw:
    status_code = 200
    request_id = "req-test"
    headers = {
        "x-request-id": "req-test",
        "openai-processing-ms": "12.5",
        "x-ratelimit-remaining-requests": "10",
        "authorization": "must-not-persist",
    }

    def parse(self):
        return SimpleNamespace(
            output_parsed=RagStructuredAnswer(
                answer_status="supported",
                answer_text="Antwort",
                claims=[
                    {
                        "claim_text": "Belegter Claim",
                        "evidence_ids": ["ru-1"],
                        "support_status": "supported",
                    }
                ],
                limitations=[],
                abstention_reason=None,
            ),
            model="gpt-5.4-nano-2026-03-17",
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                input_tokens_details=SimpleNamespace(cached_tokens=10),
                output_tokens_details=SimpleNamespace(reasoning_tokens=5),
            ),
        )


class _Parser:
    def __init__(self):
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return _Raw()


class _FakeClient:
    def __init__(self):
        parser = _Parser()
        self.parser = parser
        self.responses = SimpleNamespace(
            with_raw_response=SimpleNamespace(parse=parser.parse)
        )


class ClosedResponsesTests(unittest.TestCase):
    def test_request_contains_only_question_and_finite_evidence(self) -> None:
        text = build_closed_request_text("Frage", _package())
        self.assertIn('"question":"Frage"', text)
        self.assertIn('"evidence_id":"ru-1"', text)
        self.assertNotIn("POSTGRES", text)
        self.assertNotIn("source_pdfs/source.pdf", text)

    def test_api_call_disables_storage_and_every_tool(self) -> None:
        fake = _FakeClient()
        result = ClosedResponsesClient(
            config=ClosedResponsesConfig(), client=fake
        ).answer(question="Frage", package=_package())
        kwargs = fake.parser.kwargs
        self.assertFalse(kwargs["store"])
        self.assertEqual(kwargs["tools"], [])
        self.assertEqual(kwargs["tool_choice"], "none")
        self.assertFalse(kwargs["parallel_tool_calls"])
        self.assertEqual(result.metadata.x_request_id, "req-test")
        self.assertNotIn("authorization", result.metadata.rate_limit_headers)
        self.assertEqual(result.metadata.token_usage.reasoning_tokens, 5)


if __name__ == "__main__":
    unittest.main()

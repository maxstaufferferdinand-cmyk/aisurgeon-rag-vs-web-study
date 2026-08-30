from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from aisurgeon_decentralised.retrieval_telemetry import (
    CostUsage,
    ErrorEvent,
    FusedCandidate,
    JsonlTelemetrySink,
    LocalInfrastructureMetrics,
    RankedCandidate,
    RetryEvent,
    TokenUsage,
    TraceValidatorStatus,
    ValidatorTrace,
    build_retrieval_trace,
    capture_local_infrastructure,
    redact_text,
)


class RetrievalTelemetryTests(unittest.TestCase):
    def trace(self, **changes: object):
        values: dict[str, object] = {
            "corpus_snapshot_id": "snapshot-test",
            "embedding_model": "text-embedding-3-small",
            "query_text": "Welche Evidenz ist vorhanden?",
            "channel_candidates": {
                "exact": [
                    RankedCandidate(
                        retrieval_unit_id="ru-1", rank=1, raw_score=1.0
                    )
                ],
                "dense": [
                    RankedCandidate(
                        retrieval_unit_id="ru-2", rank=1, raw_score=0.12
                    )
                ],
            },
            "rrf_result": [
                FusedCandidate(
                    retrieval_unit_id="ru-1",
                    rank=1,
                    rrf_score=0.032,
                    contributing_channels=("exact", "dense"),
                )
            ],
            "sent_evidence_ids": ("ru-1",),
            "token_usage": TokenUsage(
                input_tokens=10,
                output_tokens=5,
                cached_tokens=2,
                embedding_tokens=20,
            ),
            "cost": CostUsage(
                amount_usd=0.001,
                pricing_as_of="2026-08-16",
                estimation_method="recorded_or_estimated_from_versioned_price_list",
            ),
            "latency_ms": {
                "routing": 1.0,
                "database": 4.0,
                "fusion": 0.5,
                "validation": 2.0,
            },
            "database_time_ms": 4.0,
            "validator_status": ValidatorTrace(
                status=TraceValidatorStatus.ACCEPTED, issue_codes=()
            ),
            "retry_status": (
                RetryEvent(
                    stage="embedding",
                    attempt=1,
                    status_code=429,
                    retryable=True,
                    outcome="retry_scheduled",
                ),
            ),
            "error_status": (
                ErrorEvent(
                    stage="dense", error_code="channel_timeout", retryable=True
                ),
            ),
            "local_infrastructure": LocalInfrastructureMetrics(
                cpu_percent=12.5,
                process_cpu_user_seconds=1.0,
                process_cpu_system_seconds=0.2,
                ram_rss_bytes=1024,
                io_read_bytes=128,
                io_write_bytes=64,
            ),
            "trace_id": "trace-test",
        }
        values.update(changes)
        return build_retrieval_trace(**values)  # type: ignore[arg-type]

    def test_default_trace_drops_query_and_answer_full_text(self) -> None:
        secret_query = "Patientin Alice fragt nach 200 mg"
        secret_answer = "Antwort für Alice"
        trace = self.trace(query_text=secret_query, answer_text=secret_answer)
        payload = json.dumps(trace.model_dump(mode="json"), ensure_ascii=False)
        self.assertNotIn(secret_query, payload)
        self.assertNotIn(secret_answer, payload)
        self.assertNotIn("Alice", payload)
        self.assertIsNone(trace.query_text_redacted)
        self.assertIsNone(trace.answer_text_redacted)
        self.assertFalse(trace.full_text_logging_opt_in)
        self.assertEqual(64, len(trace.query_sha256))

    def test_explicit_opt_in_redacts_sensitive_patterns_and_custom_terms(self) -> None:
        text = (
            "Alice alice@example.org +43 660 1234567 2025-01-02 "
            "sk-proj-abcdefghijklmnop"
        )
        trace = self.trace(
            query_text=text,
            answer_text=text,
            full_text_logging_opt_in=True,
            extra_redactions=("Alice",),
        )
        for redacted in (trace.query_text_redacted, trace.answer_text_redacted):
            self.assertIsNotNone(redacted)
            assert redacted is not None
            self.assertNotIn("Alice", redacted)
            self.assertNotIn("example.org", redacted)
            self.assertNotIn("1234567", redacted)
            self.assertNotIn("2025-01-02", redacted)
            self.assertNotIn("sk-proj", redacted)
            self.assertIn("[REDACTED_CUSTOM]", redacted)
            self.assertIn("[REDACTED_EMAIL]", redacted)
            self.assertIn("[REDACTED_PHONE]", redacted)
            self.assertIn("[REDACTED_DATE]", redacted)
            self.assertIn("[REDACTED_SECRET]", redacted)

    def test_trace_preserves_rank_token_cost_latency_retry_and_local_metrics(self) -> None:
        trace = self.trace()
        self.assertEqual("ru-1", trace.channel_candidates["exact"][0].retrieval_unit_id)
        self.assertEqual(1, trace.channel_candidates["exact"][0].rank)
        self.assertEqual(0.032, trace.rrf_result[0].rrf_score)
        self.assertEqual(20, trace.token_usage.embedding_tokens)
        self.assertEqual("2026-08-16", trace.cost.pricing_as_of)
        self.assertEqual(4.0, trace.database_time_ms)
        self.assertEqual(429, trace.retry_status[0].status_code)
        self.assertEqual("channel_timeout", trace.error_status[0].error_code)
        self.assertEqual("local_process", trace.local_infrastructure.measurement_scope)
        self.assertEqual(1024, trace.local_infrastructure.ram_rss_bytes)
        self.assertEqual(TraceValidatorStatus.ACCEPTED, trace.validator_status.status)

    def test_candidate_schema_forbids_arbitrary_full_text(self) -> None:
        with self.assertRaises(ValidationError):
            RankedCandidate.model_validate(
                {
                    "retrieval_unit_id": "ru-1",
                    "rank": 1,
                    "query_text": "must not be accepted",
                }
            )

        with self.assertRaises(ValidationError):
            ValidatorTrace.model_validate(
                {"status": "rejected", "issue_codes": ["full question text"]}
            )

    def test_negative_latency_and_token_counts_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TokenUsage(input_tokens=-1)
        with self.assertRaises(ValidationError):
            self.trace(latency_ms={"database": -1.0})

    def test_jsonl_sink_contains_only_validated_minimised_payload(self) -> None:
        trace = self.trace(query_text="Alice private question")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            JsonlTelemetrySink(path).append(trace)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("trace-test", payload["trace_id"])
        self.assertNotIn("Alice", json.dumps(payload))
        self.assertIsNone(payload["query_text_redacted"])
        self.assertEqual(["ru-1"], payload["sent_evidence_ids"])

    def test_redaction_is_bounded(self) -> None:
        self.assertEqual("abc[TRUNCATED]", redact_text("abcdef", max_length=3))

    def test_local_resource_capture_is_explicitly_local(self) -> None:
        metrics = capture_local_infrastructure()
        self.assertEqual("local_process", metrics.measurement_scope)
        self.assertIn(
            metrics.measurement_status,
            {"measured", "unavailable_psutil_not_installed"},
        )


if __name__ == "__main__":
    unittest.main()

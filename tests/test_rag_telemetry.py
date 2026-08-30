from __future__ import annotations

import unittest

from aisurgeon_decentralised.rag_telemetry import make_rag_trace


class RagTelemetryTests(unittest.TestCase):
    def test_operational_trace_never_contains_full_question_or_answer(self) -> None:
        trace = make_rag_trace(
            run_id="run-1",
            question_id="q-1",
            arm="dry_run",
            question_text="synthetische Frage",
            corpus_snapshot_id="cs-1",
            retrieval_mode="fts",
            model=None,
            model_snapshot=None,
            embedding_model="text-embedding-3-small",
            prompt_version="prompt-1",
            output_schema_version="schema-1",
            reasoning_effort="none",
            max_output_tokens=100,
            candidates_by_channel={},
            rrf_result=(),
            sent_evidence_ids=(),
            retrieval_time_ms=1.0,
            relation_expansion_time_ms=0.0,
            database_time_ms=1.0,
        )
        dumped = trace.model_dump_json()
        self.assertNotIn("synthetische Frage", dumped)
        self.assertFalse(trace.full_text_logged)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import math
import unittest
from collections.abc import Mapping, Sequence
from typing import Any

from aisurgeon_decentralised.hybrid_retrieval import (
    ALLOWED_RELATION_TYPES,
    ChannelHit,
    HybridRetrievalConfig,
    HybridRetriever,
    RetrievalChannel,
    RetrievalFailure,
    RoutingMode,
    infer_routing_mode,
    reciprocal_rank_fusion,
)

SAFE_FUNCTIONS = (
    "retrieval.search_exact(",
    "retrieval.search_lexical(",
    "retrieval.search_trigram(",
    "retrieval.search_vector_exact(",
    "retrieval.expand_relations(",
    "retrieval.evidence_package_rows(",
)


def _hit(unit_id: str, rank: int, score: float = 1.0) -> dict[str, Any]:
    return {
        "retrieval_unit_id": unit_id,
        "rank": rank,
        "score": score,
        "cosine_distance": score,
        "match_kind": "structured_text",
    }


def _unit(
    unit_id: str,
    *,
    role: str = "guideline",
    source_status: str = "final",
) -> dict[str, Any]:
    return {
        "retrieval_unit_id": unit_id,
        "corpus_snapshot_id": "snapshot-1",
        "evidence_span_id": f"span-{unit_id}",
        "source_version_id": f"version-{unit_id}",
        "source_document_id": f"document-{unit_id}",
        "document_kind": (
            "guideline" if role == "guideline" else "medicinal_product_information"
        ),
        "source_status": source_status,
        "document_component": "guideline" if role == "guideline" else "smPC",
        "source_role": role,
        "source_authority": "test_authority",
        "source_sha256": "a" * 64,
        "text_sha256": "b" * 64,
        "exact_source_text": f"Exact source text for {unit_id}",
        "chapter_path": ["Test"],
        "source_native_item_type": "recommendation",
        "source_native_item_number": "1.1",
        "printed_source_item_number": "1.1",
        "pdf_page_index": 0,
        "pdf_pages_1based": [1],
        "printed_page_label": "1",
        "table_id": None,
        "row_header_path": None,
        "column_header_path": None,
        "exact_table_cell_text": None,
        "product_ids": [],
        "active_substance_ids": [],
        "product_names": [],
        "active_substance_names": [],
        "strength": None,
        "pharmaceutical_form": None,
        "route": None,
        "dose_value": None,
        "dose_unit": None,
        "frequency": None,
        "population": None,
        "qa_status": "validated",
        "qa_flags": [],
        "conflict_status": "none",
        "citation_label": f"Test, p. 1 [{unit_id}]",
        "source_file_name": "public-test.pdf",
        "extraction_pipeline_version": "test",
    }


class FakeExecutor:
    """Programmable fake implementing only the public query-executor contract."""

    def __init__(self) -> None:
        self.results: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.failures: set[tuple[str, str]] = set()
        self.units: dict[str, dict[str, Any]] = {}
        self.relations: list[dict[str, Any]] = []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @staticmethod
    def _key(statement: str, parameters: Sequence[Any]) -> tuple[str, str]:
        if "retrieval.search_exact(" in statement:
            return RetrievalChannel.EXACT.value, str(parameters[-1])
        if "retrieval.search_lexical(" in statement:
            configuration = str(parameters[2])
            return f"fts_{configuration}", str(parameters[-1])
        if "retrieval.search_trigram(" in statement:
            return RetrievalChannel.TRIGRAM.value, str(parameters[-1])
        if "retrieval.search_vector_exact(" in statement:
            return RetrievalChannel.DENSE_EXACT.value, str(parameters[-1])
        raise AssertionError("unknown search function")

    def fetch_all(
        self, statement: str, parameters: Sequence[Any]
    ) -> list[Mapping[str, Any]]:
        parameters = tuple(parameters)
        self.calls.append((statement, parameters))
        if "retrieval.evidence_package_rows(" in statement:
            requested = parameters[1]
            return [self.units[item] for item in requested if item in self.units]
        if "retrieval.expand_relations(" in statement:
            seeds = set(parameters[1])
            limit = int(parameters[2])
            return [
                relation
                for relation in self.relations
                if relation["seed_retrieval_unit_id"] in seeds
            ][:limit]
        key = self._key(statement, parameters)
        if key in self.failures:
            raise ConnectionError(f"simulated failure for {key[0]}")
        return list(self.results.get(key, []))


def _retriever(
    executor: FakeExecutor,
    *,
    top_k: int = 2,
    expand_relations: bool = False,
) -> HybridRetriever:
    return HybridRetriever(
        executor,
        config=HybridRetrievalConfig(
            top_k=top_k,
            expand_relations=expand_relations,
        ),
    )


class ReciprocalRankFusionTests(unittest.TestCase):
    def test_rrf_uses_rank_only_and_not_incompatible_raw_scores(self) -> None:
        first = {
            "exact:guideline": [
                ChannelHit("u1", 1, "exact", "guideline", -1_000_000.0),
                ChannelHit("u2", 2, "exact", "guideline", 1_000_000.0),
            ],
            "fts_german:guideline": [
                ChannelHit("u2", 1, "fts_german", "guideline", -1_000_000.0)
            ],
        }
        second = {
            channel: [
                ChannelHit(
                    hit.retrieval_unit_id,
                    hit.rank,
                    hit.channel,
                    hit.source_role,
                    -(hit.raw_score or 0.0),
                )
                for hit in hits
            ]
            for channel, hits in first.items()
        }
        fused_first = reciprocal_rank_fusion(first, k=60)
        fused_second = reciprocal_rank_fusion(second, k=60)
        self.assertEqual(
            [item.retrieval_unit_id for item in fused_first],
            [item.retrieval_unit_id for item in fused_second],
        )
        self.assertEqual("u2", fused_first[0].retrieval_unit_id)
        self.assertTrue(
            math.isclose(fused_first[0].rrf_score, 1 / 62 + 1 / 61)
        )

    def test_rrf_deduplicates_a_unit_within_one_channel(self) -> None:
        fused = reciprocal_rank_fusion(
            {
                "exact:guideline": [
                    ChannelHit("u1", 2, "exact", "guideline"),
                    ChannelHit("u1", 1, "exact", "guideline"),
                ]
            },
            k=60,
        )
        self.assertEqual(1, len(fused))
        self.assertEqual({"exact:guideline": 1}, fused[0].channel_ranks)
        self.assertTrue(math.isclose(1 / 61, fused[0].rrf_score))


class HybridRetrieverTests(unittest.TestCase):
    def test_every_executed_statement_is_a_policy_safe_function(self) -> None:
        fake = FakeExecutor()
        fake.results[("exact", "guideline")] = [_hit("g1", 1)]
        fake.units["g1"] = _unit("g1")

        result = _retriever(fake, top_k=2, expand_relations=True).search(
            query="Empfehlung",
            corpus_snapshot_id="snapshot-1",
            routing_mode=RoutingMode.DUAL_SOURCE,
        )

        self.assertEqual(("g1",), result.evidence_allowlist)
        self.assertGreater(len(fake.calls), 0)
        for statement, _ in fake.calls:
            self.assertTrue(any(name in statement for name in SAFE_FUNCTIONS))
            self.assertNotIn("retrieval.retrieval_unit", statement)
            self.assertNotIn("retrieval.semantic_relation", statement)
        lexical_configurations = [
            parameters[2]
            for statement, parameters in fake.calls
            if "retrieval.search_lexical(" in statement
        ]
        self.assertEqual(2, lexical_configurations.count("german"))
        self.assertEqual(2, lexical_configurations.count("simple"))
        trigram_statements = [
            statement
            for statement, _ in fake.calls
            if "retrieval.search_trigram(" in statement
        ]
        self.assertTrue(trigram_statements)
        self.assertTrue(all("%s::real" in statement for statement in trigram_statements))

    def test_guideline_first_skips_secondary_when_primary_fills_top_k(self) -> None:
        fake = FakeExecutor()
        fake.results[("exact", "guideline")] = [_hit("g1", 1)]
        fake.units["g1"] = _unit("g1")

        result = _retriever(fake, top_k=1).search(
            query="Welche Empfehlung gilt?",
            corpus_snapshot_id="snapshot-1",
            routing_mode="guideline_first",
        )

        self.assertEqual(RoutingMode.GUIDELINE_FIRST, result.routing_mode)
        self.assertEqual("g1", result.direct_candidates[0].retrieval_unit_id)
        search_roles = [
            parameters[-1]
            for statement, parameters in fake.calls
            if "retrieval.search_" in statement
        ]
        self.assertEqual({"guideline"}, set(search_roles))
        secondary = [
            status
            for status in result.channel_status
            if status.source_role == "smPC"
        ]
        self.assertTrue(secondary)
        self.assertTrue(all(status.status == "skipped" for status in secondary))

    def test_smpc_first_queries_smpc_before_controlled_secondary_fallback(self) -> None:
        fake = FakeExecutor()
        fake.results[("exact", "smPC")] = [_hit("s1", 1)]
        fake.units["s1"] = _unit("s1", role="smPC")

        result = _retriever(fake, top_k=1).search(
            query="Welche zugelassene Dosierung gilt?",
            corpus_snapshot_id="snapshot-1",
            routing_mode="smpc_first",
        )

        self.assertEqual("s1", result.direct_candidates[0].retrieval_unit_id)
        first_search = next(
            parameters
            for statement, parameters in fake.calls
            if "retrieval.search_" in statement
        )
        self.assertEqual("smPC", first_search[-1])

    def test_dual_source_queries_and_retains_both_source_roles(self) -> None:
        fake = FakeExecutor()
        fake.results[("exact", "guideline")] = [_hit("g1", 1)]
        fake.results[("exact", "smPC")] = [_hit("s1", 1)]
        fake.units.update(
            {"g1": _unit("g1"), "s1": _unit("s1", role="smPC")}
        )

        result = _retriever(fake, top_k=2).search(
            query="Leitlinie versus Fachinformation",
            corpus_snapshot_id="snapshot-1",
            routing_mode="dual_source",
        )

        self.assertEqual({"g1", "s1"}, set(result.evidence_allowlist))
        self.assertIn(
            "guideline_and_smpc_evidence_not_silently_reconciled",
            result.routing_notes,
        )
        self.assertIn("dual_source_conflict_assessment_required", result.routing_notes)

    def test_dual_source_reserves_best_candidate_from_each_available_role(self) -> None:
        fake = FakeExecutor()
        # Two guideline candidates dominate four channel lists. The only SmPC
        # candidate remains required by dual-source routing, not by score mixing.
        for channel in ("exact", "fts_german", "fts_simple", "trigram"):
            fake.results[(channel, "guideline")] = [
                _hit("g1", 1),
                _hit("g2", 2),
            ]
        fake.results[("exact", "smPC")] = [_hit("s1", 20)]
        fake.units.update(
            {
                "g1": _unit("g1"),
                "g2": _unit("g2"),
                "s1": _unit("s1", role="smPC"),
            }
        )

        result = _retriever(fake, top_k=2).search(
            query="Leitlinie versus Fachinformation",
            corpus_snapshot_id="snapshot-1",
            routing_mode="dual_source",
        )

        self.assertEqual({"g1", "s1"}, set(result.evidence_allowlist))
        self.assertEqual(
            {"guideline", "smPC"},
            {item.metadata["source_role"] for item in result.direct_candidates},
        )

    def test_failed_channel_is_isolated_and_other_channels_continue(self) -> None:
        fake = FakeExecutor()
        fake.failures.add(("fts_german", "guideline"))
        fake.results[("exact", "guideline")] = [_hit("g1", 1)]
        fake.results[("trigram", "smPC")] = [_hit("s1", 1)]
        fake.units.update(
            {"g1": _unit("g1"), "s1": _unit("s1", role="smPC")}
        )

        result = _retriever(fake, top_k=2).search(
            query="Empfehlung mit kontrolliertem Fallback",
            corpus_snapshot_id="snapshot-1",
            routing_mode="guideline_first",
        )

        self.assertEqual("evidence_found", result.retrieval_outcome)
        self.assertEqual({"g1", "s1"}, set(result.evidence_allowlist))
        failed = [status for status in result.channel_status if status.status == "failed"]
        self.assertEqual(1, len(failed))
        self.assertEqual("fts_german", failed[0].channel)
        self.assertEqual("ConnectionError", failed[0].error_type)

    def test_all_attempted_channels_failed_raises_controlled_failure(self) -> None:
        fake = FakeExecutor()
        for role in ("guideline", "smPC"):
            for channel in ("exact", "fts_german", "fts_simple", "trigram"):
                fake.failures.add((channel, role))

        with self.assertRaises(RetrievalFailure) as caught:
            _retriever(fake).search(
                query="Konflikt",
                corpus_snapshot_id="snapshot-1",
                routing_mode="dual_source",
            )
        self.assertTrue(
            all(
                status.status in {"failed", "skipped"}
                for status in caught.exception.statuses
            )
        )

    def test_partial_channel_failure_suppresses_a_no_evidence_claim(self) -> None:
        fake = FakeExecutor()
        fake.failures.add(("fts_german", "guideline"))

        result = _retriever(fake).search(
            query="Empfehlung ohne Treffer",
            corpus_snapshot_id="snapshot-1",
            routing_mode="guideline_first",
        )

        self.assertEqual("retrieval_failure", result.retrieval_outcome)
        self.assertEqual((), result.evidence_allowlist)
        self.assertIn(
            "no_evidence_claim_suppressed_after_channel_failure",
            result.routing_notes,
        )

    def test_dense_channel_uses_exact_vector_function_and_expected_dimension(self) -> None:
        fake = FakeExecutor()
        fake.results[("dense_exact", "smPC")] = [_hit("s1", 1, 0.05)]
        fake.units["s1"] = _unit("s1", role="smPC")
        embedding = [1.0, *([0.0] * 1535)]

        result = _retriever(fake, top_k=1).search(
            query="Dosierung",
            corpus_snapshot_id="snapshot-1",
            routing_mode="smpc_first",
            query_embedding=embedding,
        )

        self.assertEqual(("s1",), result.evidence_allowlist)
        vector_parameters = next(
            parameters
            for statement, parameters in fake.calls
            if "retrieval.search_vector_exact(" in statement
        )
        self.assertTrue(str(vector_parameters[1]).startswith("[1,"))
        self.assertEqual(1536, str(vector_parameters[1]).count(",") + 1)
        self.assertEqual("text-embedding-3-small", vector_parameters[2])

        untouched = FakeExecutor()
        with self.assertRaisesRegex(ValueError, "dimension"):
            _retriever(untouched).search(
                query="Dosierung",
                corpus_snapshot_id="snapshot-1",
                query_embedding=[1.0, 2.0],
            )
        self.assertEqual([], untouched.calls)

    def test_relation_expansion_is_typed_and_linked_context_stays_distinct(self) -> None:
        fake = FakeExecutor()
        fake.results[("exact", "guideline")] = [_hit("g1", 1)]
        fake.units.update(
            {
                "g1": _unit("g1"),
                "r1": _unit("r1"),
                "not-allowed": _unit("not-allowed"),
            }
        )
        fake.relations.extend(
            [
                {
                    "seed_retrieval_unit_id": "g1",
                    "retrieval_unit_id": "r1",
                    "relation_type": "guideline_item_to_rationale",
                    "evidence_role": "linked_context",
                },
                {
                    "seed_retrieval_unit_id": "g1",
                    "retrieval_unit_id": "r1",
                    "relation_type": "guideline_item_to_references",
                    "evidence_role": "linked_context",
                },
                {
                    "seed_retrieval_unit_id": "g1",
                    "retrieval_unit_id": "not-allowed",
                    "relation_type": "untyped_unsafe_relation",
                    "evidence_role": "linked_context",
                },
            ]
        )

        result = _retriever(fake, top_k=1, expand_relations=True).search(
            query="Empfehlung",
            corpus_snapshot_id="snapshot-1",
            routing_mode="guideline_first",
        )

        self.assertEqual(["g1"], [item.retrieval_unit_id for item in result.direct_candidates])
        self.assertEqual(["r1"], [item.retrieval_unit_id for item in result.linked_context])
        self.assertEqual("direct", result.direct_candidates[0].evidence_role)
        self.assertEqual("linked_context", result.linked_context[0].evidence_role)
        self.assertEqual(
            ("guideline_item_to_rationale", "guideline_item_to_references"),
            result.linked_context[0].relation_types,
        )
        self.assertNotIn("not-allowed", result.evidence_allowlist)
        self.assertIn("guideline_item_to_rationale", ALLOWED_RELATION_TYPES)

    def test_evidence_package_function_is_the_final_allowlist_gate(self) -> None:
        fake = FakeExecutor()
        fake.results[("exact", "guideline")] = [_hit("excluded-or-stale", 1)]
        # The safe evidence-package function deliberately does not return this ID.

        result = _retriever(fake, top_k=1).search(
            query="Empfehlung",
            corpus_snapshot_id="snapshot-1",
            routing_mode="guideline_first",
        )

        self.assertEqual((), result.evidence_allowlist)
        self.assertEqual((), result.direct_candidates)
        self.assertEqual("no_evidence_in_snapshot", result.retrieval_outcome)

    def test_consultation_draft_is_explicitly_flagged(self) -> None:
        fake = FakeExecutor()
        fake.results[("exact", "guideline")] = [_hit("draft", 1)]
        fake.units["draft"] = _unit(
            "draft", role="guideline", source_status="consultation_draft"
        )

        result = _retriever(fake, top_k=1).search(
            query="Empfehlung",
            corpus_snapshot_id="snapshot-1",
            routing_mode="guideline_first",
        )

        self.assertIn(
            "consultation_draft_present_not_treated_as_final", result.routing_notes
        )
        self.assertEqual(
            "consultation_draft",
            result.direct_candidates[0].metadata["source_status"],
        )

    def test_empty_successful_channels_are_no_evidence_not_retrieval_failure(self) -> None:
        result = _retriever(FakeExecutor()).search(
            query="Nicht im Snapshot",
            corpus_snapshot_id="snapshot-1",
            routing_mode="dual_source",
        )
        self.assertEqual("no_evidence_in_snapshot", result.retrieval_outcome)
        self.assertEqual((), result.evidence_allowlist)


class RoutingTests(unittest.TestCase):
    def test_intent_routing_is_deterministic(self) -> None:
        self.assertEqual(
            RoutingMode.GUIDELINE_FIRST,
            infer_routing_mode("Welche Empfehlung nennt die Leitlinie?"),
        )
        self.assertEqual(
            RoutingMode.SMPC_FIRST,
            infer_routing_mode("Welche Dosierung und welches Intervall sind zugelassen?"),
        )
        self.assertEqual(
            RoutingMode.DUAL_SOURCE,
            infer_routing_mode("Weichen Leitlinie und Fachinformation voneinander ab?"),
        )
        self.assertEqual(RoutingMode.DUAL_SOURCE, infer_routing_mode("Unklarer Fall"))


if __name__ == "__main__":
    unittest.main()

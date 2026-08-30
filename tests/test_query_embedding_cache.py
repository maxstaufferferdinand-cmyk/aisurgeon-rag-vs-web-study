from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from aisurgeon_decentralised.query_embedding_cache import QueryEmbeddingCache
from aisurgeon_decentralised.retrieval_embeddings import EmbeddingBatch


class _Provider:
    model = "text-embedding-3-small"
    dimension = 1536

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        value = 1 / math.sqrt(self.dimension)
        return EmbeddingBatch(
            vectors=[[value] * self.dimension for _ in texts],
            usage={"prompt_tokens": 7, "total_tokens": 7},
        )


class QueryEmbeddingCacheTests(unittest.TestCase):
    def test_checkpoint_resume_avoids_provider_cost(self) -> None:
        provider = _Provider()
        with tempfile.TemporaryDirectory() as temporary:
            cache = QueryEmbeddingCache(
                corpus_snapshot_id="cs-test",
                root=Path(temporary),
                provider=provider,
            )
            first = cache.get("synthetische Frage", allow_provider_call=True)
            second = cache.get("synthetische Frage", allow_provider_call=True)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            assert first is not None and second is not None
            self.assertEqual(first.provider_calls, 1)
            self.assertEqual(second.provider_calls, 0)
            self.assertTrue(second.cache_hit)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(first.vector, second.vector)

    def test_dry_run_does_not_call_provider_on_cache_miss(self) -> None:
        provider = _Provider()
        with tempfile.TemporaryDirectory() as temporary:
            result = QueryEmbeddingCache(
                corpus_snapshot_id="cs-test",
                root=Path(temporary),
                provider=provider,
            ).get("nicht gecacht", allow_provider_call=False)
            self.assertIsNone(result)
            self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()

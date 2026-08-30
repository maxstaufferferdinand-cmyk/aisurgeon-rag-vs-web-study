from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from aisurgeon_decentralised.corpus_snapshot import CorpusIntegrityError
from aisurgeon_decentralised.retrieval_embeddings import (
    DeterministicFakeEmbeddingProvider,
    _checkpoint_id,
    _validate_vector,
)

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ID = "cs-f61b3d4e90089c1b890c23cb"


def test_fake_provider_is_normalised_deterministic_and_1536_dimensional() -> None:
    provider = DeterministicFakeEmbeddingProvider()
    first = provider.embed(["öffentlicher Testtext"]).vectors[0]
    second = provider.embed(["öffentlicher Testtext"]).vectors[0]
    assert first == second
    assert len(first) == 1536
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)


@pytest.mark.parametrize(
    "vector",
    ([0.0] * 1535, [float("nan")] * 1536, [0.0] * 1536),
)
def test_invalid_vectors_fail_closed(vector: list[float]) -> None:
    with pytest.raises(CorpusIntegrityError):
        _validate_vector(vector, 1536)


def test_checkpoint_identity_binds_snapshot_model_and_hashes() -> None:
    units = [
        {
            "retrieval_unit_id": "ru-test",
            "embedding_text_sha256": "a" * 64,
            "text_sha256": "b" * 64,
        }
    ]
    first = _checkpoint_id(SNAPSHOT_ID, "text-embedding-3-small", units)
    assert first == _checkpoint_id(SNAPSHOT_ID, "text-embedding-3-small", units)
    assert first != _checkpoint_id("cs-other", "text-embedding-3-small", units)


def test_released_embedding_resume_used_zero_provider_calls() -> None:
    report = json.loads(
        (
            ROOT
            / "outputs/retrieval_phase"
            / SNAPSHOT_ID
            / "embeddings/text-embedding-3-small/full_report.json"
        ).read_text(encoding="utf-8")
    )
    assert report["complete"] is True
    assert report["checkpointed_embedding_count"] == 4469
    assert report["database_embedding_count"] == 4469
    assert report["new_embedding_count"] == 0
    assert report["provider_calls_this_run"] == 0
    assert report["resume_skipped_count"] == 4469


def test_semantic_paraphrase_smoke_is_checkpointed_and_rank_one() -> None:
    report = json.loads(
        (
            ROOT / "outputs/retrieval_phase" / SNAPSHOT_ID
            / "qa/semantic_retrieval_smoke.json"
        ).read_text(encoding="utf-8")
    )
    assert report["passed"] is True
    assert report["expected_rank_at_20"] == 1
    assert report["provider_calls_this_run"] == 0
    assert report["query_text_logged_in_report"] is False

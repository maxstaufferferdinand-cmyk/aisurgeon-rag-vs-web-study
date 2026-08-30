"""Checkpointed query embeddings for reproducible local vector retrieval."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .retrieval_config import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDING_PRICE_AS_OF,
    EMBEDDING_PRICE_USD_PER_MILLION_TOKENS,
    repository_root,
)
from .retrieval_embeddings import EmbeddingProvider, OpenAIEmbeddingProvider

QUERY_EMBEDDING_SCHEMA_VERSION = "query-embedding-checkpoint-1.0.0"


@dataclass(frozen=True)
class QueryEmbeddingResult:
    query_sha256: str
    model: str
    dimension: int
    vector: tuple[float, ...]
    input_tokens: int
    estimated_cost_usd: float
    price_as_of: str
    provider_calls: int
    cache_hit: bool
    latency_ms: float
    checkpoint_path: Path


def _validate_vector(vector: list[float] | tuple[float, ...]) -> tuple[float, ...]:
    if len(vector) != EMBEDDING_DIMENSION:
        raise ValueError("query embedding has an unexpected dimension")
    converted = tuple(float(value) for value in vector)
    if not all(math.isfinite(value) for value in converted):
        raise ValueError("query embedding contains a non-finite number")
    norm = math.sqrt(sum(value * value for value in converted))
    if not 0.99 <= norm <= 1.01:
        raise ValueError("query embedding is not approximately normalised")
    return converted


def _write_atomic_gzip(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with gzip.open(temporary_path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


class QueryEmbeddingCache:
    def __init__(
        self,
        *,
        corpus_snapshot_id: str,
        root: Path | None = None,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        # Tests and reusable callers may deliberately place checkpoints in an
        # isolated directory that is not itself a repository checkout.
        self.root = Path(root).resolve() if root is not None else repository_root()
        self.corpus_snapshot_id = corpus_snapshot_id
        self.provider = provider

    def _path(self, query_sha256: str, model: str) -> Path:
        return (
            self.root
            / "outputs/retrieval_phase"
            / self.corpus_snapshot_id
            / "query_embeddings"
            / model
            / f"{query_sha256}.json.gz"
        )

    def _load(self, path: Path, query_sha256: str, model: str) -> QueryEmbeddingResult:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("schema_version") != QUERY_EMBEDDING_SCHEMA_VERSION:
            raise ValueError("unsupported query embedding checkpoint schema")
        if payload.get("corpus_snapshot_id") != self.corpus_snapshot_id:
            raise ValueError("query embedding checkpoint snapshot mismatch")
        if payload.get("query_sha256") != query_sha256 or payload.get("model") != model:
            raise ValueError("query embedding checkpoint identity mismatch")
        vector = _validate_vector(payload["vector"])
        return QueryEmbeddingResult(
            query_sha256=query_sha256,
            model=model,
            dimension=EMBEDDING_DIMENSION,
            vector=vector,
            input_tokens=int(payload.get("input_tokens") or 0),
            estimated_cost_usd=float(payload.get("estimated_cost_usd") or 0),
            price_as_of=str(payload["price_as_of"]),
            provider_calls=0,
            cache_hit=True,
            latency_ms=0.0,
            checkpoint_path=path,
        )

    def get(
        self,
        query: str,
        *,
        allow_provider_call: bool,
        model: str = EMBEDDING_MODEL,
    ) -> QueryEmbeddingResult | None:
        query_sha256 = hashlib.sha256(query.encode("utf-8")).hexdigest()
        path = self._path(query_sha256, model)
        if path.exists():
            return self._load(path, query_sha256, model)
        if not allow_provider_call:
            return None
        provider = self.provider or OpenAIEmbeddingProvider(model=model)
        started = time.perf_counter()
        batch = provider.embed([query])
        latency_ms = (time.perf_counter() - started) * 1000
        if len(batch.vectors) != 1:
            raise ValueError("query embedding provider returned an unexpected batch size")
        vector = _validate_vector(batch.vectors[0])
        input_tokens = int(batch.usage.get("prompt_tokens") or 0)
        estimated_cost = (
            input_tokens * EMBEDDING_PRICE_USD_PER_MILLION_TOKENS / 1_000_000
        )
        payload = {
            "schema_version": QUERY_EMBEDDING_SCHEMA_VERSION,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "corpus_snapshot_id": self.corpus_snapshot_id,
            "query_sha256": query_sha256,
            "query_text_logged": False,
            "model": model,
            "dimension": EMBEDDING_DIMENSION,
            "vector": list(vector),
            "input_tokens": input_tokens,
            "estimated_cost_usd": estimated_cost,
            "price_as_of": EMBEDDING_PRICE_AS_OF,
        }
        _write_atomic_gzip(path, payload)
        return QueryEmbeddingResult(
            query_sha256=query_sha256,
            model=model,
            dimension=EMBEDDING_DIMENSION,
            vector=vector,
            input_tokens=input_tokens,
            estimated_cost_usd=estimated_cost,
            price_as_of=EMBEDDING_PRICE_AS_OF,
            provider_calls=1,
            cache_hit=False,
            latency_ms=latency_ms,
            checkpoint_path=path,
        )


__all__ = [
    "QUERY_EMBEDDING_SCHEMA_VERSION",
    "QueryEmbeddingCache",
    "QueryEmbeddingResult",
]

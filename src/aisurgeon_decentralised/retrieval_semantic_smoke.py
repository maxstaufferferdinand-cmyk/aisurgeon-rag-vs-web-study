"""Checkpointed dense-retrieval smoke using one synthetic public paraphrase."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .corpus_snapshot import create_snapshot
from .retrieval_config import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDING_PRICE_AS_OF,
    EMBEDDING_PRICE_USD_PER_MILLION_TOKENS,
    repository_root,
)
from .retrieval_database import connect
from .retrieval_embeddings import (
    OpenAIEmbeddingProvider,
    _validate_vector,
    _vector_literal,
)

SYNTHETIC_QUERY = (
    "Frage | Leitlinie | Allgemeine Basismaßnahmen | "
    "Welche grundlegenden Maßnahmen wie frühe Mobilisation und Übungen "
    "sollen regelmäßig bei allen Betroffenen durchgeführt werden?"
)
EXPECTED_RETRIEVAL_UNIT_ID = "ru-17bac05292fff9021867e999"


def run_semantic_smoke(root: Path | None = None) -> dict[str, Any]:
    root = repository_root(root)
    snapshot = create_snapshot(root)
    snapshot_id = snapshot["corpus_snapshot_id"]
    output_dir = root / "outputs/retrieval_phase" / snapshot_id / "qa"
    checkpoint = output_dir / "semantic_paraphrase_vector.json.gz"
    query_sha256 = hashlib.sha256(SYNTHETIC_QUERY.encode("utf-8")).hexdigest()
    provider_calls = 0
    if checkpoint.exists():
        with gzip.open(checkpoint, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if (
            payload["query_sha256"] != query_sha256
            or payload["model"] != EMBEDDING_MODEL
            or payload["dimension"] != EMBEDDING_DIMENSION
            or payload["corpus_snapshot_id"] != snapshot_id
        ):
            raise RuntimeError("semantic smoke checkpoint metadata mismatch")
        vector = payload["embedding"]
        usage = payload["api_usage"]
    else:
        provider = OpenAIEmbeddingProvider()
        result = provider.embed([SYNTHETIC_QUERY])
        vector = result.vectors[0]
        usage = result.usage
        provider_calls = provider.call_count
        _validate_vector(vector, EMBEDDING_DIMENSION)
        payload = {
            "schema_version": "semantic-query-checkpoint-1.0.0",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "corpus_snapshot_id": snapshot_id,
            "query_sha256": query_sha256,
            "synthetic_non_patient_query": True,
            "model": EMBEDDING_MODEL,
            "dimension": EMBEDDING_DIMENSION,
            "api_usage": usage,
            "embedding": vector,
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint.with_suffix(".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        temporary.replace(checkpoint)
    norm = _validate_vector(vector, EMBEDDING_DIMENSION)
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT retrieval_unit_id, rank, cosine_distance
            FROM retrieval.search_vector_exact(%s, %s::vector, %s, 20, 'guideline')
            """,
            (snapshot_id, _vector_literal(vector), EMBEDDING_MODEL),
        )
        rows = cursor.fetchall()
    ids = [row[0] for row in rows]
    expected_rank = ids.index(EXPECTED_RETRIEVAL_UNIT_ID) + 1 if EXPECTED_RETRIEVAL_UNIT_ID in ids else None
    input_tokens = int(usage.get("prompt_tokens") or 0)
    report = {
        "schema_version": "semantic-retrieval-smoke-1.0.0",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "corpus_snapshot_id": snapshot_id,
        "query_sha256": query_sha256,
        "query_text_logged_in_report": False,
        "synthetic_non_patient_query": True,
        "model": EMBEDDING_MODEL,
        "dimension": EMBEDDING_DIMENSION,
        "vector_l2_norm": norm,
        "expected_retrieval_unit_id": EXPECTED_RETRIEVAL_UNIT_ID,
        "expected_rank_at_20": expected_rank,
        "top_5_ids": ids[:5],
        "input_tokens": input_tokens,
        "estimated_cost_usd": (
            input_tokens * EMBEDDING_PRICE_USD_PER_MILLION_TOKENS / 1_000_000
        ),
        "pricing_as_of": EMBEDDING_PRICE_AS_OF,
        "provider_calls_this_run": provider_calls,
        "checkpoint_relative_path": checkpoint.relative_to(root).as_posix(),
        "passed": expected_rank is not None,
    }
    path = output_dir / "semantic_retrieval_smoke.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report

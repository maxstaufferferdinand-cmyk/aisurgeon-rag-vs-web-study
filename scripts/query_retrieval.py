#!/usr/bin/env python3
"""Run one policy-gated hybrid retrieval query against a sealed snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aisurgeon_decentralised.hybrid_retrieval import (
    HybridRetrievalConfig,
    HybridRetriever,
    PsycopgQueryExecutor,
    RetrievalFailure,
)
from aisurgeon_decentralised.retrieval_database import connect


def _embedding_from_json(path: Path) -> Sequence[float]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("embedding")
    if (
        not isinstance(payload, list)
        or isinstance(payload, (str, bytes))
        or not all(isinstance(value, (int, float)) for value in payload)
    ):
        raise ValueError(
            "embedding JSON must be a numeric array or an object with an embedding array"
        )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Policy-gated exact, German/simple FTS, trigram and optional exact "
            "pgvector retrieval with rank-only reciprocal rank fusion."
        )
    )
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument(
        "--routing",
        default="auto",
        choices=("auto", "guideline_first", "smpc_first", "dual_source"),
    )
    parser.add_argument(
        "--embedding-json",
        type=Path,
        help="optional local JSON array with exactly 1536 query-vector values",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--no-relations", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        embedding = (
            _embedding_from_json(args.embedding_json)
            if args.embedding_json is not None
            else None
        )
        config = HybridRetrievalConfig(
            top_k=args.top_k,
            rrf_k=args.rrf_k,
            expand_relations=not args.no_relations,
        )
        with connect() as connection:
            retriever = HybridRetriever(
                PsycopgQueryExecutor(connection), config=config
            )
            result = retriever.search(
                query=args.query,
                corpus_snapshot_id=args.snapshot_id,
                routing_mode=args.routing,
                query_embedding=embedding,
            )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    except RetrievalFailure as exc:
        failure = {
            "error": "retrieval_failure",
            "message": str(exc),
            "channel_status": [status.to_dict() for status in exc.statuses],
        }
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        failure = {"error": type(exc).__name__, "message": str(exc)}
    print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

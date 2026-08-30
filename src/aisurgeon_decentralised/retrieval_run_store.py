"""Idempotent persistence for metadata-only hybrid retrieval runs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .hybrid_retrieval import HybridRetrievalConfig, HybridSearchResult
from .retrieval_config import repository_root
from .retrieval_database import connect


def persist_hybrid_result(
    result: HybridSearchResult,
    *,
    config: HybridRetrievalConfig,
    root: Path | None = None,
) -> str:
    """Persist IDs, ranks and status only; never persist the full query text."""
    root = repository_root(root)
    identity = (
        f"{result.corpus_snapshot_id}|{result.query_sha256}|"
        f"{result.routing_mode.value}|{config.rrf_k}|{config.top_k}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    run_id = f"retrieval-run-{digest[:24]}"
    trace_id = f"retrieval-trace-{digest[:24]}"
    now = datetime.now(UTC)
    channel_status = [item.to_dict() for item in result.channel_status]
    config_payload = {
        "rrf_k": config.rrf_k,
        "top_k": config.top_k,
        "exact_candidates": config.exact_candidates,
        "lexical_candidates": config.lexical_candidates,
        "trigram_candidates": config.trigram_candidates,
        "dense_candidates": config.dense_candidates,
        "relation_seed_limit": config.relation_seed_limit,
        "relation_limit": config.relation_limit,
        "embedding_model": config.embedding_model,
    }
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO retrieval.retrieval_run(
                retrieval_run_id, corpus_snapshot_id, trace_id, started_at,
                completed_at, routing_mode, rrf_k, retrieval_outcome,
                channel_status, config, error_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                run_id, result.corpus_snapshot_id, trace_id, now, now,
                result.routing_mode.value, config.rrf_k, result.retrieval_outcome,
                json.dumps(channel_status), json.dumps(config_payload),
            ),
        )
        for candidate in result.direct_candidates:
            if candidate.channel_ranks:
                for channel, rank in candidate.channel_ranks.items():
                    cursor.execute(
                        """
                        INSERT INTO retrieval.retrieval_candidate(
                            retrieval_run_id, retrieval_unit_id, channel,
                            channel_rank, raw_score, rrf_score, final_rank, evidence_role
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'direct')
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            run_id, candidate.retrieval_unit_id, channel, rank,
                            candidate.raw_scores.get(channel), candidate.rrf_score,
                            candidate.final_rank,
                        ),
                    )
        for candidate in result.linked_context:
            for relation_type in candidate.relation_types:
                cursor.execute(
                    """
                    INSERT INTO retrieval.retrieval_candidate(
                        retrieval_run_id, retrieval_unit_id, channel,
                        channel_rank, raw_score, rrf_score, final_rank, evidence_role
                    ) VALUES (%s, %s, %s, 1, NULL, NULL, %s, 'linked_context')
                    ON CONFLICT DO NOTHING
                    """,
                    (run_id, candidate.retrieval_unit_id, f"relation:{relation_type}", candidate.final_rank),
                )
        connection.commit()
    return run_id

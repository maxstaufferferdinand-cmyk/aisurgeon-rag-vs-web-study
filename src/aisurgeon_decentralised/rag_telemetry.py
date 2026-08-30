"""Data-minimising telemetry contract for the closed CLI RAG pipeline."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .retrieval_telemetry import (
    LocalInfrastructureMetrics,
    capture_local_infrastructure,
)

RAG_TELEMETRY_SCHEMA_VERSION = "closed-rag-trace-1.0.0"


class RagTokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    embedding_tokens: int = Field(default=0, ge=0)


class RagCostUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    estimated_cost_usd: float | None = Field(default=None, ge=0)
    response_cost_usd: float | None = Field(default=None, ge=0)
    embedding_cost_usd: float = Field(default=0, ge=0)
    price_source: str | None = None
    price_as_of: str | None = None
    estimation_method: str = "not_estimated_unknown_model_price"


class RagCandidateRank(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    rank: int = Field(gt=0)
    evidence_role: Literal["direct", "linked_context", "bridge_context"]


class RagTelemetryRecord(BaseModel):
    """One retrieval/response attempt without operational full-text logging."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = RAG_TELEMETRY_SCHEMA_VERSION
    run_id: str
    question_id: str
    arm: str
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_snapshot_id: str
    retrieval_mode: str
    model: str | None = None
    model_snapshot: str | None = None
    embedding_model: str
    prompt_version: str
    output_schema_version: str
    reasoning_effort: str
    max_output_tokens: int = Field(gt=0)
    candidates_by_channel: dict[str, tuple[RagCandidateRank, ...]]
    rrf_result: tuple[RagCandidateRank, ...]
    sent_evidence_ids: tuple[str, ...]
    retrieval_time_ms: float = Field(ge=0)
    relation_expansion_time_ms: float = Field(ge=0)
    database_time_ms: float = Field(ge=0)
    embedding_time_ms: float = Field(default=0, ge=0)
    embedding_cache_hit: bool | None = None
    embedding_provider_calls: int = Field(default=0, ge=0)
    api_wall_time_ms: float | None = Field(default=None, ge=0)
    openai_processing_ms: float | None = Field(default=None, ge=0)
    x_request_id: str | None = None
    http_status: int | None = None
    retry_count: int = Field(default=0, ge=0)
    retry_statuses: tuple[int | None, ...] = ()
    rate_limit_headers: dict[str, str] = Field(default_factory=dict)
    token_usage: RagTokenUsage
    cost: RagCostUsage
    local_infrastructure: LocalInfrastructureMetrics
    validator_status: Literal["accepted", "downgraded", "rejected", "not_run"]
    validator_issue_codes: tuple[str, ...] = ()
    error_code: str | None = None
    full_text_logged: bool = False
    created_at: datetime

    @field_validator("rate_limit_headers")
    @classmethod
    def only_rate_limit_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.casefold().startswith("x-ratelimit-") for key in value):
            raise ValueError("only x-ratelimit-* headers may be persisted")
        return dict(sorted(value.items()))


def make_rag_trace(
    *,
    run_id: str,
    question_id: str,
    arm: str,
    question_text: str,
    corpus_snapshot_id: str,
    retrieval_mode: str,
    model: str | None,
    model_snapshot: str | None,
    embedding_model: str,
    prompt_version: str,
    output_schema_version: str,
    reasoning_effort: str,
    max_output_tokens: int,
    candidates_by_channel: dict[str, tuple[RagCandidateRank, ...]],
    rrf_result: tuple[RagCandidateRank, ...],
    sent_evidence_ids: tuple[str, ...],
    retrieval_time_ms: float,
    relation_expansion_time_ms: float,
    database_time_ms: float,
    embedding_time_ms: float = 0.0,
    embedding_cache_hit: bool | None = None,
    embedding_provider_calls: int = 0,
    token_usage: RagTokenUsage | None = None,
    cost: RagCostUsage | None = None,
    validator_status: Literal[
        "accepted", "downgraded", "rejected", "not_run"
    ] = "not_run",
    validator_issue_codes: tuple[str, ...] = (),
    api_wall_time_ms: float | None = None,
    openai_processing_ms: float | None = None,
    x_request_id: str | None = None,
    http_status: int | None = None,
    retry_count: int = 0,
    retry_statuses: tuple[int | None, ...] = (),
    rate_limit_headers: dict[str, str] | None = None,
    error_code: str | None = None,
    created_at: datetime | None = None,
) -> RagTelemetryRecord:
    return RagTelemetryRecord(
        run_id=run_id,
        question_id=question_id,
        arm=arm,
        question_sha256=hashlib.sha256(question_text.encode("utf-8")).hexdigest(),
        corpus_snapshot_id=corpus_snapshot_id,
        retrieval_mode=retrieval_mode,
        model=model,
        model_snapshot=model_snapshot,
        embedding_model=embedding_model,
        prompt_version=prompt_version,
        output_schema_version=output_schema_version,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        candidates_by_channel=candidates_by_channel,
        rrf_result=rrf_result,
        sent_evidence_ids=sent_evidence_ids,
        retrieval_time_ms=retrieval_time_ms,
        relation_expansion_time_ms=relation_expansion_time_ms,
        database_time_ms=database_time_ms,
        embedding_time_ms=embedding_time_ms,
        embedding_cache_hit=embedding_cache_hit,
        embedding_provider_calls=embedding_provider_calls,
        api_wall_time_ms=api_wall_time_ms,
        openai_processing_ms=openai_processing_ms,
        x_request_id=x_request_id,
        http_status=http_status,
        retry_count=retry_count,
        retry_statuses=retry_statuses,
        rate_limit_headers=rate_limit_headers or {},
        token_usage=token_usage or RagTokenUsage(),
        cost=cost or RagCostUsage(),
        local_infrastructure=capture_local_infrastructure(),
        validator_status=validator_status,
        validator_issue_codes=validator_issue_codes,
        error_code=error_code,
        full_text_logged=False,
        created_at=created_at or datetime.now(UTC),
    )


class RagTelemetrySink:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: RagTelemetryRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())


__all__ = [
    "RAG_TELEMETRY_SCHEMA_VERSION",
    "RagCandidateRank",
    "RagCostUsage",
    "RagTelemetryRecord",
    "RagTelemetrySink",
    "RagTokenUsage",
    "make_rag_trace",
]

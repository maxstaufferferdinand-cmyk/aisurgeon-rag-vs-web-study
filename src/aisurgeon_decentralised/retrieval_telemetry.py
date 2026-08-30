"""Local, data-minimising retrieval telemetry.

Full query and answer text is absent by default.  Explicit opt-in stores only a
bounded, redacted representation.  Resource measurements refer exclusively to
the locally controlled Python process and must not be interpreted as provider
server load.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:  # pragma: no cover - fallback is exercised only without the optional dependency
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


TELEMETRY_SCHEMA_VERSION = "retrieval-trace-1.0.0"
MAX_REDACTED_TEXT_LENGTH = 4096
SAFE_CODE_PATTERN = r"^[A-Za-z0-9_.:-]+$"


class CandidateEvidenceRole(StrEnum):
    DIRECT = "direct"
    LINKED_CONTEXT = "linked_context"


class TraceValidatorStatus(StrEnum):
    ACCEPTED = "accepted"
    DOWNGRADED = "downgraded"
    REJECTED = "rejected"
    NOT_RUN = "not_run"


class RankedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval_unit_id: str = Field(min_length=1)
    rank: int = Field(gt=0)
    raw_score: float | None = None
    evidence_role: CandidateEvidenceRole = CandidateEvidenceRole.DIRECT


class FusedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval_unit_id: str = Field(min_length=1)
    rank: int = Field(gt=0)
    rrf_score: float = Field(ge=0)
    contributing_channels: tuple[str, ...]
    evidence_role: CandidateEvidenceRole = CandidateEvidenceRole.DIRECT

    @field_validator("contributing_channels")
    @classmethod
    def safe_channel_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(SAFE_CODE_PATTERN, item) for item in value):
            raise ValueError("channel names must be machine-readable codes")
        return value


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    embedding_tokens: int = Field(default=0, ge=0)


class CostUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount_usd: float = Field(ge=0)
    pricing_as_of: str = Field(min_length=1)
    estimation_method: str = Field(min_length=1)


class RetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str = Field(min_length=1, pattern=SAFE_CODE_PATTERN)
    attempt: int = Field(gt=0)
    status_code: int | None = None
    retryable: bool
    outcome: str = Field(min_length=1, pattern=SAFE_CODE_PATTERN)


class ErrorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str = Field(min_length=1, pattern=SAFE_CODE_PATTERN)
    error_code: str = Field(min_length=1, pattern=SAFE_CODE_PATTERN)
    retryable: bool


class ValidatorTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TraceValidatorStatus = TraceValidatorStatus.NOT_RUN
    issue_codes: tuple[str, ...] = ()

    @field_validator("issue_codes")
    @classmethod
    def safe_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(SAFE_CODE_PATTERN, item) for item in value):
            raise ValueError("validator telemetry accepts issue codes, not free text")
        return value


class LocalInfrastructureMetrics(BaseModel):
    """Metrics for locally controlled infrastructure only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    measurement_scope: Literal[
        "local_process",
        "local_database",
        "local_container",
        "locally_controlled_infrastructure",
    ] = "local_process"
    measurement_status: str = "measured"
    cpu_percent: float | None = Field(default=None, ge=0)
    process_cpu_user_seconds: float | None = Field(default=None, ge=0)
    process_cpu_system_seconds: float | None = Field(default=None, ge=0)
    ram_rss_bytes: int | None = Field(default=None, ge=0)
    io_read_bytes: int | None = Field(default=None, ge=0)
    io_write_bytes: int | None = Field(default=None, ge=0)


class RetrievalTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = Field(min_length=1)
    corpus_snapshot_id: str = Field(min_length=1)
    schema_version: str = TELEMETRY_SCHEMA_VERSION
    prompt_version: str | None = None
    model: str | None = None
    embedding_model: str = Field(min_length=1)
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_text_redacted: str | None = None
    answer_text_redacted: str | None = None
    full_text_logging_opt_in: bool = False
    channel_candidates: dict[str, tuple[RankedCandidate, ...]]
    rrf_result: tuple[FusedCandidate, ...]
    sent_evidence_ids: tuple[str, ...]
    token_usage: TokenUsage
    cost: CostUsage
    latency_ms: dict[str, float]
    retry_status: tuple[RetryEvent, ...]
    error_status: tuple[ErrorEvent, ...]
    database_time_ms: float | None = Field(default=None, ge=0)
    local_infrastructure: LocalInfrastructureMetrics
    validator_status: ValidatorTrace
    created_at: datetime

    @field_validator("latency_ms")
    @classmethod
    def nonnegative_latencies(cls, value: dict[str, float]) -> dict[str, float]:
        if any(duration < 0 for duration in value.values()):
            raise ValueError("latencies must be non-negative")
        if any(not re.fullmatch(SAFE_CODE_PATTERN, stage) for stage in value):
            raise ValueError("latency stages must be machine-readable codes")
        return value

    @field_validator("channel_candidates")
    @classmethod
    def safe_channel_keys(
        cls, value: dict[str, tuple[RankedCandidate, ...]]
    ) -> dict[str, tuple[RankedCandidate, ...]]:
        if any(not re.fullmatch(SAFE_CODE_PATTERN, channel) for channel in value):
            raise ValueError("channel names must be machine-readable codes")
        return value

    @model_validator(mode="after")
    def full_text_requires_opt_in(self) -> RetrievalTrace:
        if not self.full_text_logging_opt_in and (
            self.query_text_redacted is not None
            or self.answer_text_redacted is not None
        ):
            raise ValueError("text telemetry requires explicit opt-in")
        return self


_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:sk|sk-proj)-[A-Za-z0-9_-]{12,}\b"),
        "[REDACTED_SECRET]",
    ),
    (
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        "[REDACTED_EMAIL]",
    ),
    (
        re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"),
        "[REDACTED_DATE]",
    ),
    (
        re.compile(r"(?<!\w)(?:\+\d{1,3}[\s./-]?)?(?:\d[\s./-]?){7,15}(?!\w)"),
        "[REDACTED_PHONE]",
    ),
)


def redact_text(
    value: str,
    *,
    extra_redactions: Iterable[str] = (),
    max_length: int = MAX_REDACTED_TEXT_LENGTH,
) -> str:
    """Apply deterministic redaction for explicit opt-in text logging."""

    redacted = value
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    for literal in sorted(
        {item for item in extra_redactions if item}, key=len, reverse=True
    ):
        redacted = re.sub(re.escape(literal), "[REDACTED_CUSTOM]", redacted, flags=re.I)
    if len(redacted) > max_length:
        redacted = redacted[:max_length] + "[TRUNCATED]"
    return redacted


def capture_local_infrastructure() -> LocalInfrastructureMetrics:
    """Capture CPU, RAM and I/O of this local process, never remote providers."""

    if psutil is None:  # pragma: no cover
        return LocalInfrastructureMetrics(
            measurement_status="unavailable_psutil_not_installed"
        )
    process = psutil.Process(os.getpid())
    cpu_times = process.cpu_times()
    memory = process.memory_info()
    try:
        io = process.io_counters()
        read_bytes = int(io.read_bytes)
        write_bytes = int(io.write_bytes)
    except (AttributeError, NotImplementedError, PermissionError):
        read_bytes = None
        write_bytes = None
    return LocalInfrastructureMetrics(
        measurement_scope="local_process",
        measurement_status="measured",
        cpu_percent=max(0.0, float(process.cpu_percent(interval=None))),
        process_cpu_user_seconds=max(0.0, float(cpu_times.user)),
        process_cpu_system_seconds=max(0.0, float(cpu_times.system)),
        ram_rss_bytes=max(0, int(memory.rss)),
        io_read_bytes=read_bytes,
        io_write_bytes=write_bytes,
    )


def _ranked_candidates(
    rows: Sequence[RankedCandidate | Mapping[str, Any]],
) -> tuple[RankedCandidate, ...]:
    return tuple(
        row if isinstance(row, RankedCandidate) else RankedCandidate.model_validate(row)
        for row in rows
    )


def _fused_candidates(
    rows: Sequence[FusedCandidate | Mapping[str, Any]],
) -> tuple[FusedCandidate, ...]:
    return tuple(
        row if isinstance(row, FusedCandidate) else FusedCandidate.model_validate(row)
        for row in rows
    )


def build_retrieval_trace(
    *,
    corpus_snapshot_id: str,
    embedding_model: str,
    query_text: str,
    channel_candidates: Mapping[
        str, Sequence[RankedCandidate | Mapping[str, Any]]
    ],
    rrf_result: Sequence[FusedCandidate | Mapping[str, Any]],
    sent_evidence_ids: Sequence[str],
    token_usage: TokenUsage,
    cost: CostUsage,
    latency_ms: Mapping[str, float],
    database_time_ms: float | None,
    validator_status: ValidatorTrace,
    prompt_version: str | None = None,
    model: str | None = None,
    retry_status: Sequence[RetryEvent] = (),
    error_status: Sequence[ErrorEvent] = (),
    answer_text: str | None = None,
    full_text_logging_opt_in: bool = False,
    extra_redactions: Iterable[str] = (),
    local_infrastructure: LocalInfrastructureMetrics | None = None,
    trace_id: str | None = None,
    created_at: datetime | None = None,
) -> RetrievalTrace:
    """Build one trace while dropping full text unless explicitly opted in."""

    query_redacted = None
    answer_redacted = None
    if full_text_logging_opt_in:
        query_redacted = redact_text(query_text, extra_redactions=extra_redactions)
        if answer_text is not None:
            answer_redacted = redact_text(
                answer_text, extra_redactions=extra_redactions
            )
    return RetrievalTrace(
        trace_id=trace_id or f"trace-{uuid4()}",
        corpus_snapshot_id=corpus_snapshot_id,
        prompt_version=prompt_version,
        model=model,
        embedding_model=embedding_model,
        query_sha256=hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
        query_text_redacted=query_redacted,
        answer_text_redacted=answer_redacted,
        full_text_logging_opt_in=full_text_logging_opt_in,
        channel_candidates={
            channel: _ranked_candidates(rows)
            for channel, rows in channel_candidates.items()
        },
        rrf_result=_fused_candidates(rrf_result),
        sent_evidence_ids=tuple(dict.fromkeys(sent_evidence_ids)),
        token_usage=token_usage,
        cost=cost,
        latency_ms=dict(latency_ms),
        retry_status=tuple(retry_status),
        error_status=tuple(error_status),
        database_time_ms=database_time_ms,
        local_infrastructure=local_infrastructure or capture_local_infrastructure(),
        validator_status=validator_status,
        created_at=created_at or datetime.now(UTC),
    )


class JsonlTelemetrySink:
    """Append validated, text-minimised traces to a local JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, trace: RetrievalTrace) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            trace.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())

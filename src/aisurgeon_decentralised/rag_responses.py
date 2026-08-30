"""Closed-corpus OpenAI Responses API adapter.

The adapter sends only the question, fixed answer rules and the finite evidence
package selected locally.  It deliberately supplies no tools and never sends
database credentials, filesystem access, PDFs or unselected corpus content.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

from .evidence_contract import EvidencePackage
from .local_config import secret_env_path
from .rag_telemetry import RagCostUsage, RagTokenUsage

OPENAI_ENV_PATH = secret_env_path()
RAG_OUTPUT_SCHEMA_VERSION = "closed-rag-answer-1.0.0"
RAG_PROMPT_VERSION = "closed-corpus-rag-de-1.0.0"
DEFAULT_RESPONSE_MODEL = "gpt-5.4-nano-2026-03-17"
DEFAULT_REASONING_EFFORT = "none"
DEFAULT_MAX_OUTPUT_TOKENS = 700
PRICE_SOURCE = "https://developers.openai.com/api/docs/models/gpt-5.4-nano"
PRICE_AS_OF = "2026-08-17"
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
FATAL_HTTP_STATUSES = {400, 401, 403}


_MODEL_PRICES: dict[str, tuple[float, float, float, str]] = {
    # USD per million: uncached input, cached input, output, source.
    "gpt-5.4-nano": (0.20, 0.02, 1.25, PRICE_SOURCE),
    "gpt-5.4-nano-2026-03-17": (0.20, 0.02, 1.25, PRICE_SOURCE),
}


class RagModelClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_text: str = Field(min_length=1)
    evidence_ids: list[str]
    support_status: Literal[
        "supported", "partially_supported", "no_validated_evidence"
    ]


class RagStructuredAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_status: Literal[
        "supported", "partially_supported", "no_validated_evidence"
    ]
    answer_text: str
    claims: list[RagModelClaim]
    limitations: list[str]
    abstention_reason: str | None


@dataclass(frozen=True)
class ClosedResponsesConfig:
    model: str = DEFAULT_RESPONSE_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    prompt_version: str = RAG_PROMPT_VERSION
    output_schema_version: str = RAG_OUTPUT_SCHEMA_VERSION
    timeout_seconds: float = 120.0
    max_attempts: int = 4

    @classmethod
    def from_environment(cls) -> ClosedResponsesConfig:
        values = dotenv_values(OPENAI_ENV_PATH) if OPENAI_ENV_PATH.exists() else {}
        model = (
            os.environ.get("OPENAI_RESPONSE_MODEL")
            or values.get("OPENAI_RESPONSE_MODEL")
            or DEFAULT_RESPONSE_MODEL
        )
        effort = (
            os.environ.get("OPENAI_REASONING_EFFORT")
            or values.get("OPENAI_REASONING_EFFORT")
            or DEFAULT_REASONING_EFFORT
        )
        maximum = int(
            os.environ.get("OPENAI_MAX_OUTPUT_TOKENS")
            or values.get("OPENAI_MAX_OUTPUT_TOKENS")
            or DEFAULT_MAX_OUTPUT_TOKENS
        )
        return cls(model=str(model), reasoning_effort=str(effort), max_output_tokens=maximum)


@dataclass(frozen=True)
class ResponsesCallMetadata:
    configured_model: str
    model_snapshot: str | None
    http_status: int
    x_request_id: str | None
    openai_processing_ms: float | None
    rate_limit_headers: dict[str, str]
    retry_count: int
    retry_statuses: tuple[int | None, ...]
    api_wall_time_ms: float
    token_usage: RagTokenUsage
    cost: RagCostUsage


@dataclass(frozen=True)
class ClosedResponseResult:
    answer: RagStructuredAnswer
    metadata: ResponsesCallMetadata


class ClosedResponsesError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        retry_count: int,
        retry_statuses: tuple[int | None, ...],
        api_wall_time_ms: float,
        error_code: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_count = retry_count
        self.retry_statuses = retry_statuses
        self.api_wall_time_ms = api_wall_time_ms
        self.error_code = error_code


def _api_key() -> str:
    values = dotenv_values(OPENAI_ENV_PATH)
    key = os.environ.get("OPENAI_API_KEY") or values.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(f"OPENAI_API_KEY missing in {OPENAI_ENV_PATH}")
    return str(key)


def _usage(response: Any) -> RagTokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return RagTokenUsage()
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return RagTokenUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        cached_tokens=int(getattr(input_details, "cached_tokens", 0) or 0),
        reasoning_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
    )


def estimate_response_cost(model: str, usage: RagTokenUsage) -> RagCostUsage:
    price = _MODEL_PRICES.get(model)
    if price is None:
        return RagCostUsage(
            estimated_cost_usd=None,
            price_source=None,
            price_as_of=None,
            estimation_method="not_estimated_unknown_model_price",
        )
    input_rate, cached_rate, output_rate, source = price
    uncached = max(usage.input_tokens - usage.cached_tokens, 0)
    amount = (
        uncached * input_rate
        + usage.cached_tokens * cached_rate
        + usage.output_tokens * output_rate
    ) / 1_000_000
    return RagCostUsage(
        estimated_cost_usd=amount,
        response_cost_usd=amount,
        price_source=source,
        price_as_of=PRICE_AS_OF,
        estimation_method="published_standard_token_rates",
    )


def _evidence_payload(package: EvidencePackage) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": evidence_id,
            "source_role": package.evidence_by_id[evidence_id].source_role,
            "source_status": package.evidence_by_id[evidence_id].source_status,
            "document_component": package.evidence_by_id[evidence_id].document_component,
            "exact_source_text": package.evidence_by_id[evidence_id].exact_source_text,
        }
        for evidence_id in package.allowlist_ids
    ]


def build_closed_request_text(question: str, package: EvidencePackage) -> str:
    """Build the complete, finite user payload sent to the provider."""

    payload = {
        "question": question,
        "evidence_allowlist": _evidence_payload(package),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def closed_answer_instructions() -> str:
    return (
        "Beantworte die Frage ausschließlich anhand der endlichen Evidence-Allowlist "
        "im Benutzereingang. Verwende keine anderen Fakten und keine externen Quellen. "
        "Jeder inhaltliche Claim muss mindestens eine Evidence-ID aus dieser Allowlist "
        "tragen. Erfinde keine IDs und gib keine Quellenmetadaten oder Links aus; diese "
        "rendert das Backend. Wenn die Allowlist nicht ausreicht, verwende "
        "partially_supported oder no_validated_evidence und benenne die Einschränkung. "
        "Bei leerer Allowlist sind claims leer, answer_text leer und answer_status ist "
        "no_validated_evidence. Dies ist ein Forschungsprototyp, keine individuelle "
        "medizinische Beratung."
    )


def no_context_baseline_instructions() -> str:
    """Instructions for the deliberately non-publishable comparison arm.

    This arm has no retrieved evidence and no external tools.  It may use only
    the model's pretrained knowledge.  The backend never presents its output as
    validated evidence-backed guidance.
    """

    return (
        "Beantworte die Frage knapp aus deinem vortrainierten Wissen. Dir stehen "
        "keine Retrieval-Evidenz und keine externen Werkzeuge zur Verfügung. "
        "Erfinde keine Evidence-IDs; evidence_ids bleibt für jeden Claim leer. "
        "Verwende dasselbe strukturierte Ausgabeschema. Kennzeichne Unsicherheit "
        "transparent. Dies ist ausschließlich ein nicht publizierbarer "
        "Forschungs-Vergleichsarm und keine individuelle medizinische Beratung."
    )


class ClosedResponsesClient:
    def __init__(
        self,
        *,
        config: ClosedResponsesConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or ClosedResponsesConfig.from_environment()
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=_api_key(),
                max_retries=0,
                timeout=self.config.timeout_seconds,
            )
        self.client = client

    def answer(
        self,
        *,
        question: str,
        package: EvidencePackage,
        baseline_without_retrieval: bool = False,
    ) -> ClosedResponseResult:
        request_text = build_closed_request_text(question, package)
        retry_statuses: list[int | None] = []
        started = time.perf_counter()
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                raw = self.client.responses.with_raw_response.parse(
                    model=self.config.model,
                    instructions=(
                        no_context_baseline_instructions()
                        if baseline_without_retrieval
                        else closed_answer_instructions()
                    ),
                    input=request_text,
                    text_format=RagStructuredAnswer,
                    reasoning={"effort": self.config.reasoning_effort},
                    max_output_tokens=self.config.max_output_tokens,
                    store=False,
                    tools=[],
                    tool_choice="none",
                    parallel_tool_calls=False,
                )
                response = raw.parse()
                parsed = response.output_parsed
                if parsed is None:
                    raise RuntimeError("Responses API returned no parsed structured output")
                headers = {key.casefold(): value for key, value in raw.headers.items()}
                processing = headers.get("openai-processing-ms")
                usage = _usage(response)
                model_snapshot = str(getattr(response, "model", "") or "") or None
                cost_model = model_snapshot if model_snapshot in _MODEL_PRICES else self.config.model
                metadata = ResponsesCallMetadata(
                    configured_model=self.config.model,
                    model_snapshot=model_snapshot,
                    http_status=int(raw.status_code),
                    x_request_id=raw.request_id or headers.get("x-request-id"),
                    openai_processing_ms=float(processing) if processing else None,
                    rate_limit_headers={
                        key: value
                        for key, value in headers.items()
                        if key.startswith("x-ratelimit-")
                    },
                    retry_count=attempt - 1,
                    retry_statuses=tuple(retry_statuses),
                    api_wall_time_ms=(time.perf_counter() - started) * 1000,
                    token_usage=usage,
                    cost=estimate_response_cost(cost_model, usage),
                )
                return ClosedResponseResult(answer=parsed, metadata=metadata)
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                status = int(status) if isinstance(status, int) else None
                retry_statuses.append(status)
                retryable = status in RETRYABLE_HTTP_STATUSES
                if status in FATAL_HTTP_STATUSES or not retryable or attempt >= self.config.max_attempts:
                    raise ClosedResponsesError(
                        "closed Responses API request failed",
                        status_code=status,
                        retry_count=attempt - 1,
                        retry_statuses=tuple(retry_statuses),
                        api_wall_time_ms=(time.perf_counter() - started) * 1000,
                        error_code=type(exc).__name__[:100],
                    ) from exc
                time.sleep(min(2 ** (attempt - 1), 30))
        raise AssertionError("unreachable")


__all__ = [
    "DEFAULT_RESPONSE_MODEL",
    "RAG_OUTPUT_SCHEMA_VERSION",
    "RAG_PROMPT_VERSION",
    "ClosedResponseResult",
    "ClosedResponsesClient",
    "ClosedResponsesConfig",
    "ClosedResponsesError",
    "RagModelClaim",
    "RagStructuredAnswer",
    "ResponsesCallMetadata",
    "build_closed_request_text",
    "closed_answer_instructions",
    "estimate_response_cost",
    "no_context_baseline_instructions",
]

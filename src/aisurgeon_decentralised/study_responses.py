"""One-call, stateless Responses API adapter shared by both study arms.

WEB receives the question and the fixed live-search policy.  RAG receives the
question, the fixed closed-corpus policy and only a finite local evidence
allowlist.  No database credentials, PDFs, filesystem access, conversations or
unselected corpus content are sent.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

from .evidence_contract import EvidencePackage
from .local_config import secret_env_path
from .study_phase2 import MAX_OUTPUT_TOKENS, MAX_WEB_TOOL_CALLS, sha256_text, utc_now

OPENAI_ENV_PATH = secret_env_path()
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
FATAL_HTTP_STATUSES = {400, 401, 402, 403, 404}


class StudyClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    claim_type: Literal["fact", "recommendation", "uncertainty"]
    support_status: Literal["supported", "partially_supported", "no_validated_evidence"]
    source_refs: list[str]


class StudyRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_text: str = Field(min_length=1)
    source_refs: list[str]


class StudyStructuredAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_status: Literal["supported", "partially_supported", "no_validated_evidence"]
    answer_text: str
    claims: list[StudyClaim]
    recommendations: list[StudyRecommendation]
    limitations: list[str]
    abstention_reason: str | None


@dataclass(frozen=True)
class ResponseCallConfig:
    model: str
    reasoning_effort: Literal["medium", "high"]
    system_arm: Literal["WEB", "RAG"]
    common_instructions: str
    source_policy: str
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    max_web_tool_calls: int = MAX_WEB_TOOL_CALLS
    service_tier: Literal["default"] = "default"
    text_verbosity: Literal["medium"] = "medium"
    timeout_seconds: float = 300.0


@dataclass(frozen=True)
class ResponseUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class StudyResponseCall:
    answer: StudyStructuredAnswer
    requested_model: str
    returned_model: str | None
    response_id: str | None
    client_request_id: str
    request_id: str | None
    http_status: int | None
    service_tier_requested: str
    service_tier_used: str | None
    created_at_utc: str | None
    api_wall_time_ms: float
    time_to_first_token_ms: float | None
    openai_processing_ms: float | None
    web_search_time_ms: float | None
    rate_limit_headers: dict[str, str]
    usage: ResponseUsage
    web_sources: tuple[dict[str, Any], ...]
    cited_web_sources: tuple[dict[str, Any], ...]
    web_search_actions: tuple[dict[str, Any], ...]
    web_search_tool_calls: int
    raw_response: dict[str, Any]


class StudyResponsesError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        error_code: str,
        client_request_id: str,
        api_wall_time_ms: float,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.client_request_id = client_request_id
        self.api_wall_time_ms = api_wall_time_ms


def response_json_schema() -> dict[str, Any]:
    """Return the SDK-derived strict schema persisted in the study manifest."""

    schema = StudyStructuredAnswer.model_json_schema()

    def enforce(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
            for value in node.values():
                enforce(value)
        elif isinstance(node, list):
            for value in node:
                enforce(value)

    enforce(schema)
    return {
        "name": "rag_vs_web_study_response_v1",
        "strict": True,
        "schema": schema,
    }


def _api_key() -> str:
    values = dotenv_values(OPENAI_ENV_PATH) if OPENAI_ENV_PATH.is_file() else {}
    key = os.environ.get("OPENAI_API_KEY") or values.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(f"OPENAI_API_KEY missing in {OPENAI_ENV_PATH}")
    return str(key)


def _usage(response: Any) -> ResponseUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ResponseUsage()
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached = int(getattr(input_details, "cached_tokens", 0) or 0)
    cache_write = int(
        getattr(input_details, "cache_write_tokens", 0)
        or getattr(input_details, "cache_creation_tokens", 0)
        or 0
    )
    return ResponseUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        cached_input_tokens=cached,
        cache_write_tokens=cache_write,
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        reasoning_tokens=int(getattr(output_details, "reasoning_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
    )


def _provider_created_at(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _normalize_source(
    source: dict[str, Any], *, accessed_at_utc: str
) -> dict[str, Any]:
    available_excerpt = (
        source.get("snippet") or source.get("content") or source.get("text")
    )
    return {
        "url": source.get("url"),
        "title": source.get("title"),
        "publisher": source.get("publisher") or source.get("site_name"),
        "published_at": source.get("published_at") or source.get("publication_date"),
        "type": source.get("type"),
        "accessed_at_utc": accessed_at_utc,
        "content_hash": source.get("content_hash")
        or (sha256_text(str(available_excerpt)) if available_excerpt else None),
        "citation_start_index": source.get("start_index"),
        "citation_end_index": source.get("end_index"),
        "citation_text": source.get("text") if source.get("start_index") is not None else None,
    }


def extract_web_provenance(
    raw_response: dict[str, Any],
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    int,
]:
    consulted: dict[str, dict[str, Any]] = {}
    cited: dict[str, dict[str, Any]] = {}
    actions: list[dict[str, Any]] = []
    calls = 0
    accessed_at_utc = utc_now()
    for output in raw_response.get("output") or ():
        output_type = str(output.get("type") or "")
        if output_type in {"web_search_call", "web_search"}:
            calls += 1
            action = output.get("action") or {}
            actions.append(action)
            for source in action.get("sources") or output.get("sources") or ():
                normalized = _normalize_source(
                    source, accessed_at_utc=accessed_at_utc
                )
                url = normalized.get("url")
                if url:
                    consulted[str(url)] = normalized
        if output_type != "message":
            continue
        for content in output.get("content") or ():
            for annotation in content.get("annotations") or ():
                if annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                if not url:
                    continue
                normalized = _normalize_source(
                    annotation, accessed_at_utc=accessed_at_utc
                )
                cited[str(url)] = normalized
                consulted.setdefault(str(url), normalized)
    return (
        tuple(consulted.values()),
        tuple(cited.values()),
        tuple(actions),
        calls,
    )


def rag_request_input(question: str, package: EvidencePackage) -> str:
    evidence = []
    for evidence_id in package.allowlist_ids:
        row = package.evidence_by_id[evidence_id]
        evidence.append(
            {
                "evidence_id": evidence_id,
                "source_role": row.source_role,
                "source_status": row.source_status,
                "document_component": row.document_component,
                "exact_source_text": row.exact_source_text,
            }
        )
    return json.dumps(
        {"question": question, "evidence_allowlist": evidence},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class StudyResponsesClient:
    def __init__(self, *, client: Any | None = None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=_api_key(), max_retries=0, timeout=300.0)
        self.client = client

    def call(
        self,
        *,
        question: str,
        config: ResponseCallConfig,
        package: EvidencePackage | None = None,
    ) -> StudyResponseCall:
        if config.system_arm == "RAG" and package is None:
            raise ValueError("RAG call requires a finite evidence package")
        if config.system_arm == "WEB" and package is not None:
            raise ValueError("WEB call must not receive a local evidence package")
        request_input = (
            rag_request_input(question, package)
            if package is not None
            else json.dumps({"question": question}, ensure_ascii=False)
        )
        client_request_id = str(uuid4())
        tools: list[dict[str, Any]] = []
        tool_choice: str = "none"
        include: list[str] = []
        max_tool_calls: int | None = None
        if config.system_arm == "WEB":
            tools = [
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "return_token_budget": "default",
                }
            ]
            tool_choice = "required"
            include = ["web_search_call.action.sources"]
            max_tool_calls = config.max_web_tool_calls

        kwargs: dict[str, Any] = {
            "model": config.model,
            "instructions": config.common_instructions + "\n\n" + config.source_policy,
            "input": request_input,
            "text_format": StudyStructuredAnswer,
            "reasoning": {"effort": config.reasoning_effort},
            "max_output_tokens": config.max_output_tokens,
            "text": {"verbosity": config.text_verbosity},
            "service_tier": config.service_tier,
            "store": False,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": False,
            "include": include,
            "extra_headers": {"X-Client-Request-Id": client_request_id},
            "timeout": config.timeout_seconds,
        }
        if max_tool_calls is not None:
            kwargs["max_tool_calls"] = max_tool_calls

        started = time.perf_counter()
        first_token_ms: float | None = None
        web_started: float | None = None
        web_elapsed_ms = 0.0
        try:
            with self.client.responses.stream(**kwargs) as stream:
                for event in stream:
                    event_type = str(getattr(event, "type", ""))
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    if (
                        event_type == "response.output_text.delta"
                        and first_token_ms is None
                    ):
                        first_token_ms = elapsed_ms
                    if "web_search" in event_type and event_type.endswith(
                        ".in_progress"
                    ):
                        web_started = time.perf_counter()
                    if (
                        "web_search" in event_type
                        and event_type.endswith(".completed")
                        and web_started is not None
                    ):
                        web_elapsed_ms += (time.perf_counter() - web_started) * 1000
                        web_started = None
                response = stream.get_final_response()
                http_response = getattr(stream, "_response", None)
                headers = dict(getattr(http_response, "headers", {}) or {})
                status_code = getattr(http_response, "status_code", 200)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            status = getattr(exc, "status_code", None)
            provider_code = str(getattr(exc, "code", "") or "").casefold()
            body = getattr(exc, "body", None)
            if isinstance(body, dict):
                error_body = body.get("error") if isinstance(body.get("error"), dict) else body
                provider_code = str(error_body.get("code") or provider_code).casefold()
            fatal_quota = provider_code in {
                "billing_hard_limit_reached",
                "insufficient_quota",
                "model_not_found",
            }
            code = "http_error" if status is not None else "network_or_sdk_error"
            if fatal_quota:
                code = "fatal_billing_quota_or_model_availability_error"
            elif status in FATAL_HTTP_STATUSES:
                code = "fatal_api_authorization_or_request_error"
            elif status in RETRYABLE_HTTP_STATUSES:
                code = "retryable_api_error"
            raise StudyResponsesError(
                f"Responses API call failed ({code}, status={status})",
                status_code=status,
                error_code=code,
                client_request_id=client_request_id,
                api_wall_time_ms=elapsed,
            ) from exc

        elapsed = (time.perf_counter() - started) * 1000
        parsed = response.output_parsed
        if not isinstance(parsed, StudyStructuredAnswer):
            raise StudyResponsesError(
                "Responses API returned no parseable strict structured output",
                status_code=status_code,
                error_code="structured_output_parse_error",
                client_request_id=client_request_id,
                api_wall_time_ms=elapsed,
            )
        raw = response.model_dump(mode="json", warnings=False)
        consulted, cited, actions, web_calls = extract_web_provenance(raw)
        rate_headers = {
            key.lower(): str(value)
            for key, value in headers.items()
            if key.lower().startswith("x-ratelimit-")
        }
        processing = headers.get("openai-processing-ms") or headers.get(
            "OpenAI-Processing-Ms"
        )
        return StudyResponseCall(
            answer=parsed,
            requested_model=config.model,
            returned_model=getattr(response, "model", None),
            response_id=getattr(response, "id", None),
            client_request_id=client_request_id,
            request_id=headers.get("x-request-id") or headers.get("X-Request-Id"),
            http_status=int(status_code) if status_code is not None else None,
            service_tier_requested=config.service_tier,
            service_tier_used=getattr(response, "service_tier", None),
            created_at_utc=_provider_created_at(getattr(response, "created_at", None)),
            api_wall_time_ms=elapsed,
            time_to_first_token_ms=first_token_ms,
            openai_processing_ms=float(processing) if processing else None,
            web_search_time_ms=web_elapsed_ms or None,
            rate_limit_headers=rate_headers,
            usage=_usage(response),
            web_sources=consulted,
            cited_web_sources=cited,
            web_search_actions=actions,
            web_search_tool_calls=web_calls,
            raw_response=raw,
        )


__all__ = [
    "FATAL_HTTP_STATUSES",
    "OPENAI_ENV_PATH",
    "RETRYABLE_HTTP_STATUSES",
    "ResponseCallConfig",
    "ResponseUsage",
    "StudyClaim",
    "StudyRecommendation",
    "StudyResponseCall",
    "StudyResponsesClient",
    "StudyResponsesError",
    "StudyStructuredAnswer",
    "extract_web_provenance",
    "rag_request_input",
    "response_json_schema",
]

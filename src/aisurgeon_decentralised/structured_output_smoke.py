"""One small public-evidence Structured Outputs contract smoke test."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

from .corpus_snapshot import create_snapshot
from .evidence_contract import (
    ApplicabilityStatus,
    ClaimDraft,
    ConflictStatus,
    EntailmentStatus,
    PublicSupportLabel,
    RetrievalOutcome,
    ValidatorStatus,
    validate_claim,
)
from .local_config import secret_env_path
from .retrieval_config import EMBEDDING_MODEL, repository_root
from .retrieval_database import connect
from .retrieval_evidence_backend import build_database_evidence_package
from .retrieval_telemetry import (
    CostUsage,
    FusedCandidate,
    JsonlTelemetrySink,
    RankedCandidate,
    RetrievalTrace,
    TokenUsage,
    TraceValidatorStatus,
    ValidatorTrace,
    build_retrieval_trace,
)

OPENAI_ENV_PATH = secret_env_path()
STRUCTURED_MODEL = "gpt-5.4-nano-2026-03-17"
PROMPT_VERSION = "evidence-contract-smoke-1.0.0"
PRICING_AS_OF = "2026-08-16"
INPUT_USD_PER_MILLION = 0.20
CACHED_INPUT_USD_PER_MILLION = 0.02
OUTPUT_USD_PER_MILLION = 1.25


class StructuredEvidenceDecision(BaseModel):
    """The model may choose status and allowlisted IDs, never citation metadata."""

    model_config = ConfigDict(extra="forbid")

    public_support_label: Literal[
        "supported", "partially_supported", "no_validated_evidence"
    ]
    entailment_status: Literal["supported", "partial", "contradicted", "insufficient"]
    evidence_ids: list[str] = Field(min_length=1, max_length=3)


def _select_public_smoke_evidence(root: Path, snapshot_id: str) -> str:
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT retrieval_unit_id FROM retrieval.search_exact(%s, %s, 1, %s)",
            (snapshot_id, "12.43", "guideline"),
        )
        row = cursor.fetchone()
        if row:
            return str(row[0])
        cursor.execute(
            """
            SELECT retrieval_unit_id
            FROM retrieval.eligible_retrieval_units
            WHERE corpus_snapshot_id=%s AND source_role='guideline' AND source_status='final'
            ORDER BY retrieval_unit_id LIMIT 1
            """,
            (snapshot_id,),
        )
        fallback = cursor.fetchone()
        if not fallback:
            raise RuntimeError("no eligible public guideline evidence for smoke test")
        return str(fallback[0])


def _call_structured(client: Any, *, instructions: str, user_input: str) -> tuple[Any, int]:
    attempts = 0
    while attempts < 5:
        attempts += 1
        try:
            response = client.responses.parse(
                model=STRUCTURED_MODEL,
                instructions=instructions,
                input=user_input,
                text_format=StructuredEvidenceDecision,
                reasoning={"effort": "none"},
                max_output_tokens=300,
                store=False,
            )
            return response, attempts
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in {400, 401, 403}:
                raise RuntimeError(
                    f"fatal OpenAI Structured Outputs HTTP {status}; no automatic retry"
                ) from exc
            retryable = status in {408, 429} or (
                isinstance(status, int) and status >= 500
            )
            if not retryable or attempts >= 5:
                raise
            time.sleep(min(2 ** (attempts - 1), 30))
    raise AssertionError("unreachable")


def _usage(response: Any) -> tuple[int, int, int]:
    usage = response.usage
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)
    return input_tokens, output_tokens, cached_tokens


def _cost(input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
    uncached = max(input_tokens - cached_tokens, 0)
    return (
        uncached * INPUT_USD_PER_MILLION
        + cached_tokens * CACHED_INPUT_USD_PER_MILLION
        + output_tokens * OUTPUT_USD_PER_MILLION
    ) / 1_000_000


def _persist_validated_claim(root: Path, claim: Any) -> None:
    if not claim.publishable or claim.public_support_label is None:
        raise RuntimeError("structured smoke claim was not publishable")
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO retrieval.answer_claim(
                answer_claim_id, corpus_snapshot_id, evidence_package_id,
                claim_text_sha256, public_support_label, entailment_status,
                retrieval_outcome, conflict_status, applicability_status,
                validator_status, validation_errors, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                claim.answer_claim_id, claim.corpus_snapshot_id,
                claim.evidence_package_id, claim.claim_text_sha256,
                claim.public_support_label.value, claim.entailment_status.value,
                claim.retrieval_outcome.value, claim.conflict_status.value,
                claim.applicability_status.value, claim.validator_status.value,
                json.dumps([issue.model_dump(mode="json") for issue in claim.issues]),
                datetime.now(UTC),
            ),
        )
        for evidence_id in claim.validated_evidence_ids:
            cursor.execute(
                """
                INSERT INTO retrieval.claim_evidence(
                    answer_claim_id, corpus_snapshot_id, retrieval_unit_id,
                    evidence_role, entailment_status
                ) VALUES (%s, %s, %s, 'direct', %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    claim.answer_claim_id, claim.corpus_snapshot_id, evidence_id,
                    claim.entailment_status.value,
                ),
            )
        connection.commit()


def _persist_trace(root: Path, trace: Any) -> None:
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO retrieval.retrieval_trace(
                trace_id, corpus_snapshot_id, schema_version, prompt_version,
                model, embedding_model, query_sha256, query_text_redacted,
                answer_text_redacted, full_text_logging_opt_in,
                channel_candidates, rrf_result, sent_evidence_ids, token_usage,
                cost, latency_ms, retry_status, error_status, database_time_ms,
                local_infrastructure, validator_status, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, NULL, NULL, false,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) ON CONFLICT DO NOTHING
            """,
            (
                trace.trace_id, trace.corpus_snapshot_id, trace.schema_version,
                trace.prompt_version, trace.model, trace.embedding_model,
                trace.query_sha256,
                json.dumps({key: [row.model_dump(mode="json") for row in value]
                            for key, value in trace.channel_candidates.items()}),
                json.dumps([row.model_dump(mode="json") for row in trace.rrf_result]),
                list(trace.sent_evidence_ids), json.dumps(trace.token_usage.model_dump(mode="json")),
                json.dumps(trace.cost.model_dump(mode="json")), json.dumps(trace.latency_ms),
                json.dumps([row.model_dump(mode="json") for row in trace.retry_status]),
                json.dumps([row.model_dump(mode="json") for row in trace.error_status]),
                trace.database_time_ms,
                json.dumps(trace.local_infrastructure.model_dump(mode="json")),
                json.dumps(trace.validator_status.model_dump(mode="json")), trace.created_at,
            ),
        )
        connection.commit()


def run_structured_output_smoke(root: Path | None = None) -> dict[str, Any]:
    """Exercise schema parsing, package allowlist, validators and backend citations."""
    from openai import OpenAI

    root = repository_root(root)
    snapshot = create_snapshot(root)
    snapshot_id = snapshot["corpus_snapshot_id"]
    evidence_id = _select_public_smoke_evidence(root, snapshot_id)
    package, catalog = build_database_evidence_package(
        corpus_snapshot_id=snapshot_id, evidence_ids=[evidence_id], root=root, persist=True
    )
    evidence = catalog[evidence_id]
    instructions = (
        "Dies ist ausschließlich ein technischer Structured-Output-Test mit einer "
        "öffentlichen Leitlinienpassage. Verwende nur die bereitgestellte Evidence-ID. "
        "Gib keine Quellenmetadaten und keinen zusätzlichen medizinischen Text aus."
    )
    user_input = (
        "Der zu prüfende Claim ist zeichenidentisch mit dem Evidenztext. "
        "Ordne den Supportstatus zu und nenne die einzige zulässige Evidence-ID.\n"
        f"Zulässige Evidence-ID: {evidence_id}\n"
        f"Claim und Evidenztext: {evidence.exact_source_text}"
    )
    values = dotenv_values(OPENAI_ENV_PATH)
    api_key = values.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(f"OPENAI_API_KEY missing in {OPENAI_ENV_PATH}")
    client = OpenAI(api_key=str(api_key), max_retries=0, timeout=90.0)
    started = time.perf_counter()
    response, attempts = _call_structured(
        client, instructions=instructions, user_input=user_input
    )
    latency_ms = (time.perf_counter() - started) * 1000
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("Structured Outputs response did not contain parsed output")
    unknown_ids = sorted(set(parsed.evidence_ids) - set(package.allowlist_ids))
    if unknown_ids:
        raise RuntimeError("Structured Outputs returned an ID outside the allowlist")
    claim_id = "claim-smoke-" + hashlib.sha256(
        f"{snapshot_id}|{package.package_sha256}|{PROMPT_VERSION}".encode()
    ).hexdigest()[:24]
    claim = validate_claim(
        ClaimDraft(
            answer_claim_id=claim_id,
            claim_text=evidence.exact_source_text,
            evidence_ids=tuple(parsed.evidence_ids),
            proposed_public_label=PublicSupportLabel(parsed.public_support_label),
            entailment_status=EntailmentStatus(parsed.entailment_status),
            retrieval_outcome=RetrievalOutcome.EVIDENCE_FOUND,
            retrieval_fallback_complete=True,
            conflict_status=ConflictStatus.NONE,
            applicability_status=ApplicabilityStatus.APPLICABLE,
            expected_source_status=evidence.source_status,
        ),
        package=package,
        evidence_catalog=catalog,
    )
    if claim.validator_status == ValidatorStatus.REJECTED:
        raise RuntimeError("Structured Outputs claim failed the deterministic contract")
    _persist_validated_claim(root, claim)
    input_tokens, output_tokens, cached_tokens = _usage(response)
    estimated_cost = _cost(input_tokens, output_tokens, cached_tokens)
    trace_id = f"trace-structured-smoke-{snapshot_id[3:]}"
    trace = build_retrieval_trace(
        trace_id=trace_id,
        corpus_snapshot_id=snapshot_id,
        embedding_model=EMBEDDING_MODEL,
        query_text=user_input,
        prompt_version=PROMPT_VERSION,
        model=STRUCTURED_MODEL,
        channel_candidates={
            "structured_smoke_seed": [
                RankedCandidate(retrieval_unit_id=evidence_id, rank=1, raw_score=1.0)
            ]
        },
        rrf_result=[
            FusedCandidate(
                retrieval_unit_id=evidence_id, rank=1, rrf_score=1.0,
                contributing_channels=("structured_smoke_seed",),
            )
        ],
        sent_evidence_ids=package.allowlist_ids,
        token_usage=TokenUsage(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cached_tokens=cached_tokens, embedding_tokens=0,
        ),
        cost=CostUsage(
            amount_usd=estimated_cost, pricing_as_of=PRICING_AS_OF,
            estimation_method="published_standard_token_rates",
        ),
        latency_ms={"openai_structured_output": latency_ms},
        database_time_ms=None,
        validator_status=ValidatorTrace(
            status=TraceValidatorStatus(claim.validator_status.value),
            issue_codes=tuple(issue.code for issue in claim.issues),
        ),
        full_text_logging_opt_in=False,
    )
    _persist_trace(root, trace)
    telemetry_path = (
        root / "outputs/retrieval_phase" / snapshot_id / "telemetry"
        / "structured_output_smoke.jsonl"
    )
    if not telemetry_path.exists():
        JsonlTelemetrySink(telemetry_path).append(trace)
    report = {
        "schema_version": "structured-output-smoke-1.0.0",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "corpus_snapshot_id": snapshot_id,
        "model": STRUCTURED_MODEL,
        "prompt_version": PROMPT_VERSION,
        "public_non_patient_evidence_only": True,
        "evidence_package_id": package.evidence_package_id,
        "evidence_package_sha256": package.package_sha256,
        "allowlist_ids": list(package.allowlist_ids),
        "returned_ids_within_allowlist": not unknown_ids,
        "backend_citation_count": len(claim.citations),
        "backend_citation_has_locator": all(citation.link for citation in claim.citations),
        "parsed_public_support_label": parsed.public_support_label,
        "derived_public_support_label": claim.public_support_label.value
        if claim.public_support_label else None,
        "derived_entailment_status": claim.entailment_status.value,
        "validator_status": claim.validator_status.value,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost,
        "pricing_as_of": PRICING_AS_OF,
        "api_attempts": attempts,
        "latency_ms": latency_ms,
        "query_or_answer_text_logged": False,
        "trace_id": trace.trace_id,
        "passed": (
            not unknown_ids and claim.publishable and len(claim.citations) == 1
            and trace.query_text_redacted is None and trace.answer_text_redacted is None
        ),
    }
    report_path = (
        root / "outputs/retrieval_phase" / snapshot_id / "qa"
        / "structured_output_smoke.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def restore_structured_smoke_persistence(root: Path | None = None) -> dict[str, Any]:
    """Restore derived DB rows from the validated local smoke artifacts, with no API call."""
    root = repository_root(root)
    snapshot_id = create_snapshot(root)["corpus_snapshot_id"]
    report_path = (
        root / "outputs/retrieval_phase" / snapshot_id / "qa/structured_output_smoke.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("passed") or report["corpus_snapshot_id"] != snapshot_id:
        raise RuntimeError("validated structured smoke report is unavailable or mismatched")
    package, catalog = build_database_evidence_package(
        corpus_snapshot_id=snapshot_id,
        evidence_ids=report["allowlist_ids"],
        root=root,
        persist=True,
    )
    evidence = catalog[report["allowlist_ids"][0]]
    claim_id = "claim-smoke-" + hashlib.sha256(
        f"{snapshot_id}|{package.package_sha256}|{PROMPT_VERSION}".encode()
    ).hexdigest()[:24]
    claim = validate_claim(
        ClaimDraft(
            answer_claim_id=claim_id,
            claim_text=evidence.exact_source_text,
            evidence_ids=tuple(report["allowlist_ids"]),
            proposed_public_label=PublicSupportLabel(report["parsed_public_support_label"]),
            entailment_status=EntailmentStatus(report["derived_entailment_status"]),
            retrieval_outcome=RetrievalOutcome.EVIDENCE_FOUND,
            retrieval_fallback_complete=True,
            conflict_status=ConflictStatus.NONE,
            applicability_status=ApplicabilityStatus.APPLICABLE,
            expected_source_status=evidence.source_status,
        ),
        package=package,
        evidence_catalog=catalog,
    )
    _persist_validated_claim(root, claim)
    telemetry_path = (
        root / "outputs/retrieval_phase" / snapshot_id / "telemetry"
        / "structured_output_smoke.jsonl"
    )
    telemetry_rows = [
        json.loads(line) for line in telemetry_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(telemetry_rows) != 1:
        raise RuntimeError("structured smoke telemetry artifact is not singular")
    trace = RetrievalTrace.model_validate(telemetry_rows[0])
    _persist_trace(root, trace)
    return {
        "restored": True,
        "api_calls": 0,
        "evidence_package_id": package.evidence_package_id,
        "answer_claim_id": claim.answer_claim_id,
        "trace_id": trace.trace_id,
    }

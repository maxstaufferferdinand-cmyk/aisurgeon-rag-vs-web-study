"""Sequential, resumable and cost-gated execution of the Phase-2 study."""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .rag_core import RagCore, RetrievalMode
from .retrieval_telemetry import capture_local_infrastructure
from .study_costs import calculate_cost
from .study_phase2 import (
    CORPUS_SNAPSHOT_ID,
    MAX_OUTPUT_TOKENS,
    MAX_WEB_TOOL_CALLS,
    MODEL_CONFIGURATIONS,
    PRICE_VERSION,
    PROMPT_VERSION,
    PROTOCOL_VERSION,
    RESPONSE_SCHEMA_VERSION,
    RETRIEVAL_CONFIG_VERSION,
    STUDY_MAX_ESTIMATED_API_COST_USD,
    WEB_CONFIG_VERSION,
    HumanQuestionFreezeRequired,
    PlannedStudyCell,
    StudyQuestion,
    build_randomization_manifest,
    load_frozen_questions,
    read_jsonl,
    sha256_file,
    sha256_text,
    utc_now,
    write_jsonl_atomic,
)
from .study_responses import (
    FATAL_HTTP_STATUSES,
    RETRYABLE_HTTP_STATUSES,
    ResponseCallConfig,
    StudyResponseCall,
    StudyResponsesClient,
    StudyResponsesError,
)
from .study_validators import validate_rag_answer, validate_web_answer
from .vte_development import build_vte_development_questions

PILOT_EXPERIMENT_ID = "development_cost_pilot"
MAIN_EXPERIMENT_ID = "rag_vs_web_main"


class StudyApiAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "api-attempt-1.1.0"
    experiment_id: str
    protocol_version: str = PROTOCOL_VERSION
    run_id: str
    attempt_id: str
    attempt_number: int = Field(gt=0)
    question_id: str
    question_hash: str
    coverage_stratum: str
    model_config_id: str
    requested_model: str
    returned_model: str | None = None
    reasoning_effort: str
    system_arm: Literal["WEB", "RAG"]
    repetition: str
    randomization_block: int
    corpus_snapshot_id: str = CORPUS_SNAPSHOT_ID
    prompt_version: str = PROMPT_VERSION
    schema_version_response: str = RESPONSE_SCHEMA_VERSION
    retrieval_config_version: str = RETRIEVAL_CONFIG_VERSION
    web_config_version: str = WEB_CONFIG_VERSION
    price_version: str = PRICE_VERSION
    service_tier_requested: str = "default"
    service_tier_used: str | None = None
    utc_started: str
    utc_finished: str
    provider_created_at_utc: str | None = None
    response_id: str | None = None
    x_request_id: str | None = None
    client_request_id: str
    http_status: int | None = None
    retry_number: int = 0
    api_wall_time_ms: float = 0.0
    openai_processing_ms: float | None = None
    time_to_first_token_ms: float | None = None
    web_search_time_ms: float | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    query_embedding_tokens: int = 0
    search_content_tokens: int | None = None
    web_search_tool_calls: int = 0
    rate_limit_headers: dict[str, str] = Field(default_factory=dict)
    model_cost_usd: float | None = 0.0
    web_search_cost_usd: float | None = 0.0
    embedding_cost_usd: float | None = 0.0
    retry_cost_usd: float | None = 0.0
    total_estimated_cost_usd: float | None = 0.0
    standardized_uncached_cost_usd: float | None = 0.0
    reconciled_cost_usd: float | None = None
    cost_reconciliation_status: str
    local_resources_before: dict[str, Any]
    local_resources_after: dict[str, Any]
    error_class: str | None = None
    error_code: str | None = None
    retryable: bool = False
    streaming: bool = True
    store: bool = False
    previous_response_id_used: bool = False
    text_verbosity: str = "medium"
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    max_web_tool_calls_requested: int | None = None
    sampling_parameters: dict[str, str] = Field(
        default_factory=lambda: {
            "temperature": "not_set",
            "top_p": "not_set",
            "top_logprobs": "not_set",
        }
    )


class StudyRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "study-result-1.1.0"
    experiment_id: str
    protocol_version: str = PROTOCOL_VERSION
    run_id: str
    question_id: str
    question_hash: str
    coverage_stratum: str
    question_text: str
    model_config_id: str
    requested_model: str
    returned_model: str | None
    reasoning_effort: str
    system_arm: Literal["WEB", "RAG"]
    repetition: str
    randomization_block: int
    planned_order: int
    actual_order: int
    status: Literal["complete", "failed"]
    started_at_utc: str
    finished_at_utc: str
    corpus_snapshot_id: str = CORPUS_SNAPSHOT_ID
    prompt_hashes: dict[str, str]
    response_schema_hash: str
    retrieval_config_hash: str
    web_config_hash: str
    raw_model_answer: dict[str, Any] | None
    validated_system_answer: dict[str, Any] | None
    backend_rendered_sources: tuple[dict[str, Any], ...]
    web_sources_consulted: tuple[dict[str, Any], ...]
    web_sources_cited: tuple[dict[str, Any], ...]
    web_search_actions: tuple[dict[str, Any], ...]
    retrieval: dict[str, Any] | None
    evidence_package_id: str | None
    evidence_allowlist: tuple[str, ...]
    attempt_ids: tuple[str, ...]
    retry_count: int
    token_usage: dict[str, int | None]
    timing_ms: dict[str, float | None]
    cost: dict[str, Any]
    validator_status: str
    validator_issue_codes: tuple[str, ...]
    error_class: str | None
    error_code: str | None
    protocol_deviation_ids: tuple[str, ...] = ()
    local_resources: dict[str, Any] = Field(default_factory=dict)


class CostLimitApprovalRequired(RuntimeError):
    status = "COST_LIMIT_APPROVAL_REQUIRED"


class ModelIdentityMismatch(RuntimeError):
    status = "MODEL_IDENTITY_MISMATCH"


def pending_study_cells(
    cells: list[PlannedStudyCell] | tuple[PlannedStudyCell, ...],
    recorded_results: list[dict[str, Any]],
) -> tuple[PlannedStudyCell, ...]:
    """Return only never-recorded cells; complete and transparent failures are terminal."""

    terminal_ids = {str(row["run_id"]) for row in recorded_results}
    return tuple(cell for cell in cells if cell.run_id not in terminal_ids)


def _prompt_material(root: Path, arm: str) -> tuple[str, str, dict[str, str]]:
    directory = root / "outputs/study_phase2/prompts"
    common_path = directory / "COMMON_TASK_v1.txt"
    policy_path = directory / f"SOURCE_POLICY_{arm}_v1.txt"
    schema_path = directory / "RESPONSE_SCHEMA_v1.json"
    paths = (common_path, policy_path, schema_path)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"study prompt freeze is incomplete: {missing}")
    return (
        common_path.read_text(encoding="utf-8"),
        policy_path.read_text(encoding="utf-8"),
        {path.name: sha256_file(path) for path in paths},
    )


def _config_hashes(root: Path) -> tuple[str, str, str]:
    manifest_dir = root / "outputs/study_phase2/manifest"
    retrieval = manifest_dir / "retrieval_config.json"
    web = manifest_dir / "web_search_config.json"
    schema = root / "outputs/study_phase2/prompts/RESPONSE_SCHEMA_v1.json"
    return sha256_file(retrieval), sha256_file(web), sha256_file(schema)


def _existing(path: Path, key: str) -> dict[str, dict[str, Any]]:
    return {str(row[key]): row for row in read_jsonl(path)}


def _local_container_status(root: Path) -> dict[str, Any]:
    """Capture only the locally controlled PostgreSQL container status."""

    try:
        completed = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "measurement_status": "unavailable",
            "error_class": type(exc).__name__,
        }
    rows = []
    for line in completed.stdout.splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if raw.get("Service") == "postgres":
            rows.append(
                {
                    "service": raw.get("Service"),
                    "name": raw.get("Name"),
                    "state": raw.get("State"),
                    "health": raw.get("Health"),
                }
            )
    return {
        "measurement_status": "measured" if completed.returncode == 0 else "error",
        "return_code": completed.returncode,
        "containers": rows,
    }


def _verify_main_freeze(root: Path, frozen_path: Path) -> dict[str, Any]:
    """Fail closed if any prospectively frozen input changed after approval."""

    manifest_path = root / "outputs/study_phase2/manifest/study_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("main_study_calls_allowed"):
        raise HumanQuestionFreezeRequired(
            "study manifest has not enabled main-study calls"
        )
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise RuntimeError("study manifest protocol version is not the active freeze")
    if float(manifest.get("cost_ceiling_usd") or 0) != float(
        STUDY_MAX_ESTIMATED_API_COST_USD
    ):
        raise RuntimeError("study manifest cost ceiling is not the active 500-USD gate")
    if manifest.get("freeze_approval_basis") != (
        "study_owner_pre_freeze_approval"
    ):
        raise RuntimeError("study-owner pre-freeze approval is not recorded")
    expected_question_hash = manifest.get("question_freeze_hash")
    if not expected_question_hash or sha256_file(frozen_path) != expected_question_hash:
        raise RuntimeError("human-frozen question/gold hash mismatch")
    mismatches: list[str] = []
    for relative, expected in (manifest.get("pre_run_freeze_hashes") or {}).items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected:
            mismatches.append(relative)
    randomization_path = (
        root / "outputs/study_phase2/manifest/randomization_manifest.csv"
    )
    expected_randomization = manifest.get("randomization_manifest_sha256")
    if expected_randomization and sha256_file(randomization_path) != expected_randomization:
        mismatches.append(str(randomization_path.relative_to(root)))
    verification_path = (
        root / "outputs/study_phase2/manifest/model_availability_verification.json"
    )
    expected_verification = manifest.get("model_availability_verification_sha256")
    if expected_verification and sha256_file(verification_path) != expected_verification:
        mismatches.append(str(verification_path.relative_to(root)))
    if mismatches:
        raise RuntimeError(
            "prospectively frozen study inputs changed; document a protocol "
            f"deviation before any API call: {mismatches}"
        )
    return manifest


def _persist_rows(path: Path, rows: list[dict[str, Any]], key: str) -> None:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_key = str(row[key])
        if row_key in by_key and by_key[row_key] != row:
            raise RuntimeError(f"conflicting duplicate {key}: {row_key}")
        by_key[row_key] = row
    write_jsonl_atomic(path, [by_key[value] for value in sorted(by_key)])


def _attempt_from_success(
    *,
    experiment_id: str,
    cell: PlannedStudyCell,
    question: StudyQuestion,
    response: StudyResponseCall,
    attempt_number: int,
    started: str,
    finished: str,
    embedding_tokens: int,
    resources_before: dict[str, Any],
    resources_after: dict[str, Any],
) -> StudyApiAttempt:
    cost = calculate_cost(
        model=cell.requested_model,
        usage=response.usage,
        web_search_calls=response.web_search_tool_calls,
        embedding_tokens=embedding_tokens,
        is_retry_attempt=attempt_number > 1,
    )
    return StudyApiAttempt(
        experiment_id=experiment_id,
        run_id=cell.run_id,
        attempt_id=f"{cell.run_id}:attempt-{attempt_number}",
        attempt_number=attempt_number,
        question_id=question.question_id,
        question_hash=question.question_hash,
        coverage_stratum=question.coverage_stratum,
        model_config_id=cell.model_config_id,
        requested_model=cell.requested_model,
        returned_model=response.returned_model,
        reasoning_effort=cell.reasoning_effort,
        system_arm=cell.system_arm,
        repetition=cell.repetition,
        randomization_block=cell.randomization_block,
        service_tier_used=response.service_tier_used,
        utc_started=started,
        utc_finished=finished,
        provider_created_at_utc=response.created_at_utc,
        response_id=response.response_id,
        x_request_id=response.request_id,
        client_request_id=response.client_request_id,
        http_status=response.http_status,
        retry_number=attempt_number - 1,
        api_wall_time_ms=response.api_wall_time_ms,
        openai_processing_ms=response.openai_processing_ms,
        time_to_first_token_ms=response.time_to_first_token_ms,
        web_search_time_ms=response.web_search_time_ms,
        input_tokens=response.usage.input_tokens,
        cached_input_tokens=response.usage.cached_input_tokens,
        cache_write_tokens=response.usage.cache_write_tokens,
        output_tokens=response.usage.output_tokens,
        reasoning_tokens=response.usage.reasoning_tokens,
        total_tokens=response.usage.total_tokens,
        query_embedding_tokens=embedding_tokens,
        search_content_tokens=None,
        web_search_tool_calls=response.web_search_tool_calls,
        rate_limit_headers=response.rate_limit_headers,
        local_resources_before=resources_before,
        local_resources_after=resources_after,
        max_web_tool_calls_requested=(
            MAX_WEB_TOOL_CALLS if cell.system_arm == "WEB" else None
        ),
        **cost.__dict__,
    )


def _attempt_from_error(
    *,
    experiment_id: str,
    cell: PlannedStudyCell,
    question: StudyQuestion,
    error: StudyResponsesError,
    attempt_number: int,
    started: str,
    finished: str,
    embedding_tokens: int,
    resources_before: dict[str, Any],
    resources_after: dict[str, Any],
) -> StudyApiAttempt:
    embedding_cost = embedding_tokens * 0.02 / 1_000_000
    return StudyApiAttempt(
        experiment_id=experiment_id,
        run_id=cell.run_id,
        attempt_id=f"{cell.run_id}:attempt-{attempt_number}",
        attempt_number=attempt_number,
        question_id=question.question_id,
        question_hash=question.question_hash,
        coverage_stratum=question.coverage_stratum,
        model_config_id=cell.model_config_id,
        requested_model=cell.requested_model,
        reasoning_effort=cell.reasoning_effort,
        system_arm=cell.system_arm,
        repetition=cell.repetition,
        randomization_block=cell.randomization_block,
        utc_started=started,
        utc_finished=finished,
        client_request_id=error.client_request_id,
        http_status=error.status_code,
        retry_number=attempt_number - 1,
        api_wall_time_ms=error.api_wall_time_ms,
        query_embedding_tokens=embedding_tokens,
        model_cost_usd=None,
        web_search_cost_usd=None,
        embedding_cost_usd=embedding_cost,
        retry_cost_usd=None,
        total_estimated_cost_usd=embedding_cost if embedding_tokens else None,
        standardized_uncached_cost_usd=None,
        cost_reconciliation_status="unavailable_failed_attempt_without_usage",
        local_resources_before=resources_before,
        local_resources_after=resources_after,
        error_class=type(error).__name__,
        error_code=error.error_code,
        retryable=(
            not error.error_code.startswith("fatal_")
            and (
                error.status_code is None
                or error.status_code in RETRYABLE_HTTP_STATUSES
            )
        ),
        max_web_tool_calls_requested=(
            MAX_WEB_TOOL_CALLS if cell.system_arm == "WEB" else None
        ),
    )


class StudyRunner:
    def __init__(
        self, *, root: Path, responses_client: StudyResponsesClient | None = None
    ):
        self.root = root.resolve()
        self.responses_client = responses_client or StudyResponsesClient()
        self.rag_core = RagCore(root=self.root, corpus_snapshot_id=CORPUS_SNAPSHOT_ID)
        self.base = self.root / "outputs/study_phase2"
        self._main_incurred_cost_usd: float | None = None
        self._pilot_group_reserves: dict[str, float] | None = None
        self._container_status = _local_container_status(self.root)

    def _incurred_external_cost(self, attempts_path: Path) -> float:
        if self._main_incurred_cost_usd is not None:
            return self._main_incurred_cost_usd
        preparation_path = self.base / "manifest/preparation_api_usage.json"
        preparation = (
            json.loads(preparation_path.read_text(encoding="utf-8"))
            if preparation_path.is_file()
            else {}
        )
        attempts = read_jsonl(
            self.base / "pilot/development_cost_pilot_attempts.jsonl"
        ) + read_jsonl(attempts_path)
        self._main_incurred_cost_usd = float(
            preparation.get("query_embedding_cost_usd") or 0
        ) + sum(
            float(row.get("total_estimated_cost_usd") or 0) for row in attempts
        )
        return self._main_incurred_cost_usd

    def _per_attempt_reserve(self, cell: PlannedStudyCell) -> float:
        if self._pilot_group_reserves is None:
            summary_path = self.base / "pilot/development_cost_pilot_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self._pilot_group_reserves = {
                key: float(value["max_cost_usd"])
                for key, value in summary["group_costs"].items()
            }
        key = f"{cell.model_config_id}:{cell.system_arm}"
        return self._pilot_group_reserves[key]

    def _execute_cell(
        self,
        *,
        experiment_id: str,
        cell: PlannedStudyCell,
        question: StudyQuestion,
        actual_order: int,
        attempts_path: Path,
    ) -> StudyRunResult:
        started_utc = utc_now()
        e2e_started = time.perf_counter()
        cell_resources_before = capture_local_infrastructure().model_dump(mode="json")
        common, policy, prompt_hashes = _prompt_material(self.root, cell.system_arm)
        retrieval_config_hash, web_config_hash, schema_hash = _config_hashes(self.root)
        retrieval = None
        package = None
        retrieval_payload: dict[str, Any] | None = None
        evidence_ids: tuple[str, ...] = ()
        if cell.system_arm == "RAG":
            retrieval = self.rag_core.retrieve(
                question=question.question_text,
                retrieval_mode=RetrievalMode.HYBRID_RRF_BRIDGE,
                allow_embedding_api=True,
            )
            evidence_ids = retrieval.evidence_ids
            package, _ = self.rag_core.build_evidence_package(evidence_ids)
            retrieval_payload = retrieval.model_dump(mode="json")

        call_config = ResponseCallConfig(
            model=cell.requested_model,
            reasoning_effort=cell.reasoning_effort,  # type: ignore[arg-type]
            system_arm=cell.system_arm,
            common_instructions=common,
            source_policy=policy,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            max_web_tool_calls=MAX_WEB_TOOL_CALLS,
        )
        attempt_rows = list(_existing(attempts_path, "attempt_id").values())
        call: StudyResponseCall | None = None
        error: StudyResponsesError | None = None
        prior_attempts = sorted(
            (
                StudyApiAttempt.model_validate(row)
                for row in attempt_rows
                if row.get("run_id") == cell.run_id
            ),
            key=lambda row: row.attempt_number,
        )
        attempt_ids: list[str] = [row.attempt_id for row in prior_attempts]
        mismatched_prior = next(
            (
                row
                for row in prior_attempts
                if row.http_status == 200
                and row.returned_model != row.requested_model
            ),
            None,
        )
        if mismatched_prior is not None:
            raise ModelIdentityMismatch(
                "persisted successful attempt returned the wrong model identity: "
                f"requested={mismatched_prior.requested_model}, "
                f"returned={mismatched_prior.returned_model}"
            )
        start_attempt = (
            max((row.attempt_number for row in prior_attempts), default=0) + 1
        )
        # A process interruption can occur after attempt persistence but before
        # result persistence. Never purchase a replacement for a successful
        # orphan attempt: retain its telemetry and record the cell as failed.
        if any(row.http_status == 200 for row in prior_attempts):
            orphan = prior_attempts[-1]
            error = StudyResponsesError(
                "successful API attempt lacks recoverable response checkpoint",
                status_code=orphan.http_status,
                error_code="orphaned_success_attempt_missing_response_payload",
                client_request_id=orphan.client_request_id,
                api_wall_time_ms=orphan.api_wall_time_ms,
            )
            start_attempt = 4
        elif prior_attempts and (
            not prior_attempts[-1].retryable or start_attempt > 3
        ):
            prior = prior_attempts[-1]
            error = StudyResponsesError(
                "persisted terminal attempt is not eligible for another retry",
                status_code=prior.http_status,
                error_code=prior.error_code or "persisted_terminal_api_attempt",
                client_request_id=prior.client_request_id,
                api_wall_time_ms=prior.api_wall_time_ms,
            )
            start_attempt = 4
        for attempt_number in range(start_attempt, 4):
            if experiment_id == MAIN_EXPERIMENT_ID:
                projected_after_attempt = self._incurred_external_cost(
                    attempts_path
                ) + self._per_attempt_reserve(cell)
                if projected_after_attempt > STUDY_MAX_ESTIMATED_API_COST_USD:
                    raise CostLimitApprovalRequired(
                        "retry/attempt reserve would exceed cost ceiling: "
                        f"${projected_after_attempt:.6f}"
                    )
            attempt_id = f"{cell.run_id}:attempt-{attempt_number}"
            attempt_ids.append(attempt_id)
            attempt_started = utc_now()
            before = capture_local_infrastructure().model_dump(mode="json")
            try:
                call = self.responses_client.call(
                    question=question.question_text,
                    config=call_config,
                    package=package,
                )
                after = capture_local_infrastructure().model_dump(mode="json")
                row = _attempt_from_success(
                    experiment_id=experiment_id,
                    cell=cell,
                    question=question,
                    response=call,
                    attempt_number=attempt_number,
                    started=attempt_started,
                    finished=utc_now(),
                    embedding_tokens=(
                        retrieval.embedding_tokens
                        if retrieval is not None and attempt_number == 1
                        else 0
                    ),
                    resources_before=before,
                    resources_after=after,
                )
                attempt_rows.append(row.model_dump(mode="json"))
                _persist_rows(attempts_path, attempt_rows, "attempt_id")
                if (
                    experiment_id == MAIN_EXPERIMENT_ID
                    and row.total_estimated_cost_usd is not None
                ):
                    assert self._main_incurred_cost_usd is not None
                    self._main_incurred_cost_usd += row.total_estimated_cost_usd
                if call.returned_model != cell.requested_model:
                    raise ModelIdentityMismatch(
                        "Responses API returned the wrong model identity: "
                        f"requested={cell.requested_model}, "
                        f"returned={call.returned_model}"
                    )
                error = None
                break
            except StudyResponsesError as exc:
                after = capture_local_infrastructure().model_dump(mode="json")
                error = exc
                row = _attempt_from_error(
                    experiment_id=experiment_id,
                    cell=cell,
                    question=question,
                    error=exc,
                    attempt_number=attempt_number,
                    started=attempt_started,
                    finished=utc_now(),
                    embedding_tokens=(
                        retrieval.embedding_tokens
                        if retrieval is not None and attempt_number == 1
                        else 0
                    ),
                    resources_before=before,
                    resources_after=after,
                )
                attempt_rows.append(row.model_dump(mode="json"))
                _persist_rows(attempts_path, attempt_rows, "attempt_id")
                if (
                    experiment_id == MAIN_EXPERIMENT_ID
                    and row.total_estimated_cost_usd is not None
                ):
                    assert self._main_incurred_cost_usd is not None
                    self._main_incurred_cost_usd += row.total_estimated_cost_usd
                if exc.status_code in FATAL_HTTP_STATUSES or exc.error_code.startswith(
                    "fatal_"
                ):
                    raise
                transient = (
                    exc.status_code is None
                    or exc.status_code in RETRYABLE_HTTP_STATUSES
                )
                if not transient or attempt_number >= 3:
                    break
                time.sleep(2 ** (attempt_number - 1))

        finished_utc = utc_now()
        all_attempts = [
            StudyApiAttempt.model_validate(row)
            for row in read_jsonl(attempts_path)
            if row["run_id"] == cell.run_id
        ]
        attempt_costs = [
            row.total_estimated_cost_usd
            for row in all_attempts
            if row.total_estimated_cost_usd is not None
        ]
        total_cost = sum(attempt_costs)
        if call is None:
            return StudyRunResult(
                experiment_id=experiment_id,
                run_id=cell.run_id,
                question_id=question.question_id,
                question_hash=question.question_hash,
                coverage_stratum=question.coverage_stratum,
                question_text=question.question_text,
                model_config_id=cell.model_config_id,
                requested_model=cell.requested_model,
                returned_model=None,
                reasoning_effort=cell.reasoning_effort,
                system_arm=cell.system_arm,
                repetition=cell.repetition,
                randomization_block=cell.randomization_block,
                planned_order=cell.planned_order,
                actual_order=actual_order,
                status="failed",
                started_at_utc=started_utc,
                finished_at_utc=finished_utc,
                prompt_hashes=prompt_hashes,
                response_schema_hash=schema_hash,
                retrieval_config_hash=retrieval_config_hash,
                web_config_hash=web_config_hash,
                raw_model_answer=None,
                validated_system_answer=None,
                backend_rendered_sources=(),
                web_sources_consulted=(),
                web_sources_cited=(),
                web_search_actions=(),
                retrieval=retrieval_payload,
                evidence_package_id=package.evidence_package_id if package else None,
                evidence_allowlist=evidence_ids,
                attempt_ids=tuple(attempt_ids),
                retry_count=max(len(attempt_ids) - 1, 0),
                token_usage={
                    "input_tokens": sum(row.input_tokens for row in all_attempts),
                    "cached_input_tokens": sum(
                        row.cached_input_tokens for row in all_attempts
                    ),
                    "cache_write_tokens": sum(
                        row.cache_write_tokens for row in all_attempts
                    ),
                    "output_tokens": sum(row.output_tokens for row in all_attempts),
                    "reasoning_tokens": sum(
                        row.reasoning_tokens for row in all_attempts
                    ),
                    "total_tokens": sum(row.total_tokens for row in all_attempts),
                    "query_embedding_tokens": sum(
                        row.query_embedding_tokens for row in all_attempts
                    ),
                    "search_content_tokens": None,
                },
                timing_ms={
                    "query_normalization": retrieval.query_normalization_time_ms
                    if retrieval
                    else 0.0,
                    "query_embedding": retrieval.embedding_time_ms
                    if retrieval
                    else 0.0,
                    "exact_search": retrieval.exact_search_time_ms
                    if retrieval
                    else 0.0,
                    "fts": retrieval.fts_time_ms if retrieval else 0.0,
                    "trigram": retrieval.trigram_time_ms if retrieval else 0.0,
                    "vector": retrieval.vector_time_ms if retrieval else 0.0,
                    "rrf": retrieval.rrf_time_ms if retrieval else 0.0,
                    "relation_expansion": retrieval.relation_expansion_time_ms
                    if retrieval
                    else 0.0,
                    "evidence_package": retrieval.evidence_package_time_ms
                    if retrieval
                    else 0.0,
                    "database": retrieval.database_time_ms if retrieval else 0.0,
                    "api_wall": sum(row.api_wall_time_ms for row in all_attempts),
                    "openai_processing": None,
                    "time_to_first_token": None,
                    "web_search": None,
                    "validation": 0.0,
                    "render": 0.0,
                    "end_to_end": (time.perf_counter() - e2e_started) * 1000,
                },
                cost={
                    "total_estimated_cost_usd": total_cost,
                    "cost_reconciliation_status": "failed_attempt_usage_may_be_unavailable",
                },
                validator_status="not_run",
                validator_issue_codes=(error.error_code if error else "unknown_error",),
                error_class=type(error).__name__ if error else "UnknownError",
                error_code=error.error_code if error else "unknown_error",
                local_resources={
                    "before_retrieval": cell_resources_before,
                    "after_cell": capture_local_infrastructure().model_dump(
                        mode="json"
                    ),
                    "local_container_status": self._container_status,
                },
            )

        validation_started = time.perf_counter()
        if cell.system_arm == "RAG":
            assert package is not None and retrieval is not None
            validated = validate_rag_answer(
                call.answer, package=package, retrieval_hits=retrieval.hits
            )
        else:
            validated = validate_web_answer(
                call.answer,
                consulted_sources=call.web_sources,
                cited_sources=call.cited_web_sources,
            )
        validation_ms = (time.perf_counter() - validation_started) * 1000
        summed = {
            "model_cost_usd": sum(row.model_cost_usd or 0 for row in all_attempts),
            "web_search_cost_usd": sum(
                row.web_search_cost_usd or 0 for row in all_attempts
            ),
            "embedding_cost_usd": sum(
                row.embedding_cost_usd or 0 for row in all_attempts
            ),
            "retry_cost_usd": sum(row.retry_cost_usd or 0 for row in all_attempts),
            "total_estimated_cost_usd": total_cost,
            "standardized_uncached_cost_usd": sum(
                row.standardized_uncached_cost_usd or 0 for row in all_attempts
            ),
            "reconciled_cost_usd": None,
            "cost_reconciliation_status": "not_reconciled_admin_key_not_required",
            "price_version": PRICE_VERSION,
        }
        return StudyRunResult(
            experiment_id=experiment_id,
            run_id=cell.run_id,
            question_id=question.question_id,
            question_hash=question.question_hash,
            coverage_stratum=question.coverage_stratum,
            question_text=question.question_text,
            model_config_id=cell.model_config_id,
            requested_model=cell.requested_model,
            returned_model=call.returned_model,
            reasoning_effort=cell.reasoning_effort,
            system_arm=cell.system_arm,
            repetition=cell.repetition,
            randomization_block=cell.randomization_block,
            planned_order=cell.planned_order,
            actual_order=actual_order,
            status="complete",
            started_at_utc=started_utc,
            finished_at_utc=finished_utc,
            prompt_hashes=prompt_hashes,
            response_schema_hash=schema_hash,
            retrieval_config_hash=retrieval_config_hash,
            web_config_hash=web_config_hash,
            raw_model_answer=call.answer.model_dump(mode="json"),
            validated_system_answer=validated.model_dump(mode="json"),
            backend_rendered_sources=validated.rendered_sources,
            web_sources_consulted=call.web_sources,
            web_sources_cited=call.cited_web_sources,
            web_search_actions=call.web_search_actions,
            retrieval=retrieval_payload,
            evidence_package_id=package.evidence_package_id if package else None,
            evidence_allowlist=evidence_ids,
            attempt_ids=tuple(attempt_ids),
            retry_count=max(len(attempt_ids) - 1, 0),
            token_usage={
                "input_tokens": sum(row.input_tokens for row in all_attempts),
                "cached_input_tokens": sum(
                    row.cached_input_tokens for row in all_attempts
                ),
                "cache_write_tokens": sum(
                    row.cache_write_tokens for row in all_attempts
                ),
                "output_tokens": sum(row.output_tokens for row in all_attempts),
                "reasoning_tokens": sum(row.reasoning_tokens for row in all_attempts),
                "total_tokens": sum(row.total_tokens for row in all_attempts),
                "query_embedding_tokens": sum(
                    row.query_embedding_tokens for row in all_attempts
                ),
                "search_content_tokens": None,
            },
            timing_ms={
                "query_normalization": retrieval.query_normalization_time_ms
                if retrieval
                else 0.0,
                "query_embedding": retrieval.embedding_time_ms if retrieval else 0.0,
                "exact_search": retrieval.exact_search_time_ms if retrieval else 0.0,
                "fts": retrieval.fts_time_ms if retrieval else 0.0,
                "trigram": retrieval.trigram_time_ms if retrieval else 0.0,
                "vector": retrieval.vector_time_ms if retrieval else 0.0,
                "rrf": retrieval.rrf_time_ms if retrieval else 0.0,
                "relation_expansion": retrieval.relation_expansion_time_ms
                if retrieval
                else 0.0,
                "evidence_package": retrieval.evidence_package_time_ms
                if retrieval
                else 0.0,
                "database": retrieval.database_time_ms if retrieval else 0.0,
                "api_wall": sum(row.api_wall_time_ms for row in all_attempts),
                "openai_processing": call.openai_processing_ms,
                "time_to_first_token": call.time_to_first_token_ms,
                "web_search": call.web_search_time_ms,
                "validation": validation_ms,
                "render": 0.0,
                "end_to_end": (time.perf_counter() - e2e_started) * 1000,
            },
            cost=summed,
            validator_status=validated.validator_status,
            validator_issue_codes=validated.issue_codes,
            error_class=None,
            error_code=None,
            local_resources={
                "before_retrieval": cell_resources_before,
                "after_cell": capture_local_infrastructure().model_dump(mode="json"),
                "local_container_status": self._container_status,
            },
        )

    def run_pilot(self) -> dict[str, Any]:
        results_path = self.base / "pilot/development_cost_pilot_results.jsonl"
        attempts_path = self.base / "pilot/development_cost_pilot_attempts.jsonl"
        existing = _existing(results_path, "run_id")
        development = build_vte_development_questions(
            corpus_snapshot_id=CORPUS_SNAPSHOT_ID, root=self.root
        )
        selected_ids = {
            "vte-dev-001",
            "vte-dev-004",
            "vte-dev-005",
            "vte-dev-012",
            "vte-dev-018",
        }
        selected = [row for row in development if row.question_id in selected_ids]
        if len(selected) != 5:
            raise RuntimeError("development pilot selection is incomplete")
        actual_order = len(existing)
        for question_index, dev in enumerate(selected, start=1):
            provisional = StudyQuestion(
                question_id=f"study-q-{question_index:03d}",
                question_text=dev.question_text,
                question_hash=sha256_text(dev.question_text),
                coverage_stratum=(
                    "not_covered_by_local_corpus"
                    if dev.expected_no_evidence
                    else "covered_by_local_corpus"
                ),
                clinical_domain="VTE development pilot",
                question_type=dev.category,
                difficulty="moderate",
                expected_rag_status=(
                    "no_validated_evidence" if dev.expected_no_evidence else "supported"
                ),
                required_claims=() if dev.expected_no_evidence else (dev.notes,),
                expected_retrieval_unit_ids=dev.expected_evidence_ids,
                expected_pages=dev.expected_pdf_pages_1based,
            )
            for model in MODEL_CONFIGURATIONS:
                arms: tuple[Literal["WEB", "RAG"], ...] = (
                    ("RAG", "WEB") if question_index % 2 else ("WEB", "RAG")
                )
                for arm in arms:
                    run_id = (
                        f"{PILOT_EXPERIMENT_ID}:{dev.question_id}:"
                        f"{model.model_config_id}:{arm}"
                    )
                    if run_id in existing:
                        continue
                    actual_order += 1
                    cell = PlannedStudyCell(
                        run_id=run_id,
                        question_id=provisional.question_id,
                        question_hash=provisional.question_hash,
                        coverage_stratum=provisional.coverage_stratum,
                        model_config_id=model.model_config_id,
                        requested_model=model.requested_model,
                        reasoning_effort=model.reasoning_effort,
                        system_arm=arm,
                        repetition="1_primary",
                        randomization_block=question_index,
                        planned_order=actual_order,
                        status="planned",
                    )
                    result = self._execute_cell(
                        experiment_id=PILOT_EXPERIMENT_ID,
                        cell=cell,
                        question=provisional,
                        actual_order=actual_order,
                        attempts_path=attempts_path,
                    )
                    existing[run_id] = result.model_dump(mode="json")
                    _persist_rows(results_path, list(existing.values()), "run_id")
        if len(existing) != 20:
            raise RuntimeError(f"pilot expected 20 results, got {len(existing)}")
        summary = build_pilot_cost_projection(list(existing.values()), root=self.root)
        summary_path = self.base / "pilot/development_cost_pilot_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = summary_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(summary_path)
        if (
            summary["conservative_total_projection_usd"]
            > STUDY_MAX_ESTIMATED_API_COST_USD
        ):
            raise CostLimitApprovalRequired(
                f"conservative projection ${summary['conservative_total_projection_usd']:.2f} "
                f"exceeds ${STUDY_MAX_ESTIMATED_API_COST_USD:.2f}"
            )
        return summary

    def run_main(self) -> dict[str, Any]:
        frozen_path = self.base / "questions/study_questions_frozen.jsonl"
        questions = load_frozen_questions(frozen_path)
        _verify_main_freeze(self.root, frozen_path)
        by_question = {row.question_id: row for row in questions}
        cells = build_randomization_manifest(questions, frozen=True)
        pilot_summary_path = self.base / "pilot/development_cost_pilot_summary.json"
        if not pilot_summary_path.is_file():
            raise RuntimeError(
                "development cost pilot must be completed before main study"
            )
        pilot_summary = json.loads(pilot_summary_path.read_text(encoding="utf-8"))
        if (
            pilot_summary["conservative_total_projection_usd"]
            > STUDY_MAX_ESTIMATED_API_COST_USD
        ):
            raise CostLimitApprovalRequired(
                "frozen pilot projection exceeds cost ceiling"
            )
        results_path = self.base / "results/study_results.jsonl"
        attempts_path = self.base / "results/api_attempts.jsonl"
        existing = _existing(results_path, "run_id")
        events_path = self.base / "results/execution_events.jsonl"
        events = read_jsonl(events_path)
        invocation_started = utc_now()
        events.append(
            {
                "event_id": f"main-invocation:{invocation_started}",
                "event_type": "resume" if existing else "start",
                "utc": invocation_started,
                "terminal_cells_before": len(existing),
                "cost_gate_usd": STUDY_MAX_ESTIMATED_API_COST_USD,
            }
        )
        _persist_rows(events_path, events, "event_id")
        actual_order = len(existing)
        for cell in sorted(cells, key=lambda row: row.planned_order):
            if cell.run_id in existing:
                continue
            attempt_cost = self._incurred_external_cost(attempts_path)
            remaining_cells = [
                row for row in cells if row.run_id not in existing
            ]
            projected = attempt_cost + sum(
                self._per_attempt_reserve(row) * 3 for row in remaining_cells
            )
            if projected > STUDY_MAX_ESTIMATED_API_COST_USD:
                raise CostLimitApprovalRequired(
                    f"pre-block projection ${projected:.2f} exceeds cost ceiling"
                )
            actual_order += 1
            result = self._execute_cell(
                experiment_id=MAIN_EXPERIMENT_ID,
                cell=cell,
                question=by_question[cell.question_id],
                actual_order=actual_order,
                attempts_path=attempts_path,
            )
            existing[cell.run_id] = result.model_dump(mode="json")
            _persist_rows(results_path, list(existing.values()), "run_id")
        completed_summary = {
            "status": "TECHNICAL_STUDY_COMPLETE_CLINICAL_RATING_PENDING",
            "planned_results": 800,
            "recorded_results": len(existing),
            "completed": sum(row["status"] == "complete" for row in existing.values()),
            "failed": sum(row["status"] == "failed" for row in existing.values()),
        }
        events = read_jsonl(events_path)
        finished = utc_now()
        events.append(
            {
                "event_id": f"main-complete:{finished}",
                "event_type": "complete",
                "utc": finished,
                "terminal_cells_after": len(existing),
            }
        )
        _persist_rows(events_path, events, "event_id")
        return completed_summary


def build_pilot_cost_projection(
    results: list[dict[str, Any]], *, root: Path | None = None
) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in results:
        groups[f"{row['model_config_id']}:{row['system_arm']}"].append(
            float(row["cost"].get("total_estimated_cost_usd") or 0)
        )
    summaries: dict[str, Any] = {}
    expected_main = 0.0
    conservative_main = 0.0
    for key, costs in sorted(groups.items()):
        mean = statistics.mean(costs)
        maximum = max(costs)
        summaries[key] = {
            "n": len(costs),
            "mean_cost_usd": mean,
            "median_cost_usd": statistics.median(costs),
            "max_cost_usd": maximum,
        }
        expected_main += mean * 200
        # Empirical upper projection: every one of 200 cells in this stratum
        # costs the observed pilot maximum on all three permitted attempts.
        conservative_main += maximum * 3 * 200
    pilot_cost = sum(
        float(row["cost"].get("total_estimated_cost_usd") or 0) for row in results
    )
    preparation_path = (root or Path.cwd()) / (
        "outputs/study_phase2/manifest/preparation_api_usage.json"
    )
    preparation_cost = 0.0
    if preparation_path.is_file():
        preparation_cost = float(
            json.loads(preparation_path.read_text(encoding="utf-8")).get(
                "query_embedding_cost_usd", 0
            )
        )
    conservative_total = preparation_cost + pilot_cost + conservative_main
    return {
        "experiment_id": PILOT_EXPERIMENT_ID,
        "created_at_utc": utc_now(),
        "pilot_results": len(results),
        "group_costs": summaries,
        "pilot_total_cost_usd": pilot_cost,
        "preparation_query_embedding_cost_usd": preparation_cost,
        "expected_main_cost_usd": expected_main,
        "conservative_main_cost_usd": conservative_main,
        "conservative_total_projection_usd": conservative_total,
        "cost_limit_usd": STUDY_MAX_ESTIMATED_API_COST_USD,
        "within_cost_limit": conservative_total <= STUDY_MAX_ESTIMATED_API_COST_USD,
        "conservative_remaining_cost_per_cell_usd": conservative_main / 800,
        "projection_method": (
            "Per model/system: observed pilot maximum x three permitted attempts "
            "x 200 cells; preparation and pilot actuals added. This is an empirical "
            "retry-inclusive projection, not a contractual provider-price cap."
        ),
    }


__all__ = [
    "MAIN_EXPERIMENT_ID",
    "PILOT_EXPERIMENT_ID",
    "CostLimitApprovalRequired",
    "ModelIdentityMismatch",
    "StudyApiAttempt",
    "StudyRunResult",
    "StudyRunner",
    "build_pilot_cost_projection",
    "pending_study_cells",
]

"""Immutable contracts and deterministic design for the RAG-versus-Web study.

This module is provider-independent.  It is the single source for the two
deployment configurations, two study arms, two repetitions, cost ceiling,
randomisation, hashes, and the explicit question-freeze approval gate.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = "rag-vs-web-1.1.0"
QUESTION_SCHEMA_VERSION = "study-question-1.0.0"
RESULT_SCHEMA_VERSION = "study-result-1.1.0"
ATTEMPT_SCHEMA_VERSION = "api-attempt-1.1.0"
PROMPT_VERSION = "rag-vs-web-common-task-v1"
RESPONSE_SCHEMA_VERSION = "rag-vs-web-response-v1"
RETRIEVAL_CONFIG_VERSION = "phase1-hybrid-rrf-bridge-frozen-v1"
WEB_CONFIG_VERSION = "live-web-search-frozen-v1"
PRICE_VERSION = "openai-public-prices-2026-08-29-v2-cap-500"
CORPUS_SNAPSHOT_ID = "cs-f61b3d4e90089c1b890c23cb"
RANDOMIZATION_SEED = 20260829
MAX_OUTPUT_TOKENS = 6_000
# Development pilot started with five. One successful Web response exposed six
# search/open/find actions despite the requested five-call cap, so the main-study
# request is prospectively frozen at six and actual calls remain fully recorded.
MAX_WEB_TOOL_CALLS = 6
STUDY_MAX_ESTIMATED_API_COST_USD = 500.00
SUPERSEDED_STUDY_COST_CEILING_USD = 400.00
STUDY_OWNER_APPROVED_QUESTIONS_SHA256 = (
    "c87a274eed277c262bc3b2343f3c56aad8f80c19eacbff3e63d76adbdf17e69c"
)
PRIMARY_RESULT_COUNT = 800

CoverageStratum = Literal[
    "covered_by_local_corpus", "not_covered_by_local_corpus", "partially_covered"
]
StudyArm = Literal["WEB", "RAG"]
Repetition = Literal["1_primary", "2_reproducibility"]


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_config_id: str
    display_name: str
    requested_model: str
    reasoning_effort: Literal["medium", "high"]
    dated_snapshot: bool
    snapshot_limitation: str | None = None


MODEL_CONFIGURATIONS: tuple[ModelConfiguration, ...] = (
    ModelConfiguration(
        model_config_id="gpt55_medium",
        display_name="GPT-5.5 / medium",
        requested_model="gpt-5.5-2026-04-23",
        reasoning_effort="medium",
        dated_snapshot=True,
    ),
    ModelConfiguration(
        model_config_id="gpt56_sol_high",
        display_name="GPT-5.6 Sol / high",
        requested_model="gpt-5.6-sol",
        reasoning_effort="high",
        dated_snapshot=False,
        snapshot_limitation=(
            "Am Studienzugriffstag war in der offiziellen Modelldokumentation "
            "kein datierter GPT-5.6-Sol-Snapshot aufgeführt."
        ),
    ),
)
SYSTEM_ARMS: tuple[StudyArm, ...] = ("WEB", "RAG")
REPETITIONS: tuple[Repetition, ...] = ("1_primary", "2_reproducibility")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class StudyQuestion(BaseModel):
    """Provisional or human-frozen gold record for one study question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = QUESTION_SCHEMA_VERSION
    question_id: str = Field(pattern=r"^study-q-(?:0[0-9]{2}|100)$")
    question_text: str = Field(min_length=15)
    question_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_stratum: CoverageStratum
    clinical_domain: str
    question_type: str
    difficulty: Literal["easy", "moderate", "hard"]
    expected_rag_status: Literal[
        "supported", "partially_supported", "no_validated_evidence"
    ]
    required_claims: tuple[str, ...]
    acceptable_variants: tuple[str, ...] = ()
    critical_omissions: tuple[str, ...] = ()
    forbidden_or_harmful_claims: tuple[str, ...] = ()
    expected_formal_item_ids: tuple[str, ...] = ()
    expected_retrieval_unit_ids: tuple[str, ...] = ()
    expected_source_documents: tuple[str, ...] = ()
    expected_pages: tuple[int, ...] = ()
    expected_active_substance_ids: tuple[str, ...] = ()
    expected_product_ids: tuple[str, ...] = ()
    expected_relation_types: tuple[str, ...] = ()
    gold_standard_version: str = "provisional-1"
    authoring_method: Literal["synthetic_draft_source_derived"] = (
        "synthetic_draft_source_derived"
    )
    human_review_status: Literal[
        "pending",
        "approved",
        "study_owner_pre_freeze_approval",
        "rejected",
        "revision_required",
    ] = "pending"
    confirmed_coverage_status: bool = False
    confirmed_required_claims: bool = False
    confirmed_critical_errors: bool = False
    confirmed_gold_sources_or_abstention: bool = False
    freeze_timestamp: str | None = None
    source_status_notes: tuple[str, ...] = ()
    coverage_audit: dict[str, Any] = Field(default_factory=dict)

    @field_validator("question_text")
    @classmethod
    def normalized_question(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned.endswith("?"):
            raise ValueError("study questions must end with a question mark")
        return cleaned

    @model_validator(mode="after")
    def validate_hash_and_freeze(self) -> StudyQuestion:
        if self.question_hash != sha256_text(self.question_text):
            raise ValueError("question_hash does not match question_text")
        if self.coverage_stratum == "not_covered_by_local_corpus":
            if self.expected_rag_status != "no_validated_evidence":
                raise ValueError("not-covered question must expect RAG abstention")
            if self.expected_retrieval_unit_ids or self.expected_formal_item_ids:
                raise ValueError(
                    "not-covered question cannot contain expected local evidence"
                )
        if self.human_review_status in {
            "approved",
            "study_owner_pre_freeze_approval",
        }:
            if not all(
                (
                    self.confirmed_coverage_status,
                    self.confirmed_required_claims,
                    self.confirmed_critical_errors,
                    self.confirmed_gold_sources_or_abstention,
                )
            ):
                raise ValueError("approved question lacks required human confirmations")
            if not self.freeze_timestamp:
                raise ValueError("approved question lacks freeze_timestamp")
        return self


class PlannedStudyCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: str = PROTOCOL_VERSION
    run_id: str
    question_id: str
    question_hash: str
    coverage_stratum: CoverageStratum
    model_config_id: str
    requested_model: str
    reasoning_effort: str
    system_arm: StudyArm
    repetition: Repetition
    randomization_block: int = Field(gt=0)
    planned_order: int = Field(gt=0)
    status: Literal[
        "human_freeze_pending", "planned", "running", "complete", "failed"
    ] = "human_freeze_pending"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl_atomic(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    dict(row),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
        handle.flush()
    temporary.replace(path)


def validate_question_set(
    questions: Sequence[StudyQuestion], *, require_human_freeze: bool
) -> None:
    if len(questions) != 100:
        raise ValueError(f"expected exactly 100 study questions, got {len(questions)}")
    ids = [row.question_id for row in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate question_id")
    hashes = [row.question_hash for row in questions]
    if len(hashes) != len(set(hashes)):
        raise ValueError("duplicate question text/hash")
    counts = {
        label: sum(row.coverage_stratum == label for row in questions)
        for label in (
            "covered_by_local_corpus",
            "not_covered_by_local_corpus",
            "partially_covered",
        )
    }
    if counts != {
        "covered_by_local_corpus": 80,
        "not_covered_by_local_corpus": 20,
        "partially_covered": 0,
    }:
        raise ValueError(f"coverage distribution must be 80/20/0, got {counts}")
    if require_human_freeze:
        unapproved = [
            row.question_id
            for row in questions
            if row.human_review_status
            not in {"approved", "study_owner_pre_freeze_approval"}
        ]
        if unapproved:
            raise HumanQuestionFreezeRequired(
                f"{len(unapproved)} questions are not human-approved"
            )


class HumanQuestionFreezeRequired(RuntimeError):
    status = "HUMAN_QUESTION_FREEZE_REQUIRED"


def load_frozen_questions(path: Path) -> tuple[StudyQuestion, ...]:
    if not path.is_file():
        raise HumanQuestionFreezeRequired(
            f"required human freeze file is missing: {path}"
        )
    questions = tuple(StudyQuestion.model_validate(row) for row in read_jsonl(path))
    validate_question_set(questions, require_human_freeze=True)
    return questions


def build_randomization_manifest(
    questions: Sequence[StudyQuestion], *, frozen: bool
) -> tuple[PlannedStudyCell, ...]:
    validate_question_set(questions, require_human_freeze=frozen)
    cells: list[PlannedStudyCell] = []
    planned_order = 0
    block = 0
    by_id = {row.question_id: row for row in questions}
    for repetition_index, repetition in enumerate(REPETITIONS, start=1):
        rng = random.Random(RANDOMIZATION_SEED + repetition_index)
        question_ids = sorted(by_id)
        rng.shuffle(question_ids)
        for question_id in question_ids:
            block += 1
            models = list(MODEL_CONFIGURATIONS)
            rng.shuffle(models)
            for model in models:
                arms: list[StudyArm] = list(SYSTEM_ARMS)
                rng.shuffle(arms)
                for arm in arms:
                    planned_order += 1
                    question = by_id[question_id]
                    run_id = (
                        f"{PROTOCOL_VERSION}:{repetition}:{question_id}:"
                        f"{model.model_config_id}:{arm}"
                    )
                    cells.append(
                        PlannedStudyCell(
                            run_id=run_id,
                            question_id=question_id,
                            question_hash=question.question_hash,
                            coverage_stratum=question.coverage_stratum,
                            model_config_id=model.model_config_id,
                            requested_model=model.requested_model,
                            reasoning_effort=model.reasoning_effort,
                            system_arm=arm,
                            repetition=repetition,
                            randomization_block=block,
                            planned_order=planned_order,
                            status="planned" if frozen else "human_freeze_pending",
                        )
                    )
    if len(cells) != PRIMARY_RESULT_COUNT:
        raise AssertionError(f"expected 800 cells, got {len(cells)}")
    if len({row.run_id for row in cells}) != PRIMARY_RESULT_COUNT:
        raise AssertionError("randomization produced duplicate run IDs")
    return tuple(cells)


__all__ = [
    "ATTEMPT_SCHEMA_VERSION",
    "CORPUS_SNAPSHOT_ID",
    "MAX_OUTPUT_TOKENS",
    "MAX_WEB_TOOL_CALLS",
    "MODEL_CONFIGURATIONS",
    "PRICE_VERSION",
    "PRIMARY_RESULT_COUNT",
    "PROMPT_VERSION",
    "PROTOCOL_VERSION",
    "QUESTION_SCHEMA_VERSION",
    "RANDOMIZATION_SEED",
    "REPETITIONS",
    "RESPONSE_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "RETRIEVAL_CONFIG_VERSION",
    "STUDY_MAX_ESTIMATED_API_COST_USD",
    "STUDY_OWNER_APPROVED_QUESTIONS_SHA256",
    "SUPERSEDED_STUDY_COST_CEILING_USD",
    "SYSTEM_ARMS",
    "WEB_CONFIG_VERSION",
    "HumanQuestionFreezeRequired",
    "PlannedStudyCell",
    "StudyQuestion",
    "build_randomization_manifest",
    "canonical_json_bytes",
    "load_frozen_questions",
    "read_jsonl",
    "sha256_file",
    "sha256_text",
    "utc_now",
    "validate_question_set",
    "write_jsonl_atomic",
]

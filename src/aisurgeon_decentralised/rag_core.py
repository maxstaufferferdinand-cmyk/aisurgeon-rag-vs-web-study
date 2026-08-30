"""Reusable, closed-corpus RAG core shared by CLI and a future API server.

The module deliberately contains the orchestration and validation logic rather
than placing it in command-line scripts.  PostgreSQL remains a regenerable
policy-gated index; evidence text and citations are selected locally before a
finite package is sent to the Responses API.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .evidence_contract import (
    ApplicabilityStatus,
    BackendCitation,
    ClaimDraft,
    ConflictStatus,
    EntailmentStatus,
    EvidencePackage,
    EvidenceRecord,
    PublicSupportLabel,
    RetrievalOutcome,
    ValidatorStatus,
    build_evidence_package,
    detect_negation,
    validate_claim,
)
from .hybrid_retrieval import (
    HybridRetrievalConfig,
    HybridRetriever,
    PsycopgQueryExecutor,
    QueryExecutor,
    RetrievalChannel,
    RoutingMode,
)
from .query_embedding_cache import QueryEmbeddingCache, QueryEmbeddingResult
from .query_normalization import NormalizedQuery, normalize_query
from .rag_responses import (
    RAG_OUTPUT_SCHEMA_VERSION,
    RAG_PROMPT_VERSION,
    ClosedResponseResult,
    ClosedResponsesClient,
    ClosedResponsesError,
    RagStructuredAnswer,
)
from .rag_telemetry import (
    RagCandidateRank,
    RagCostUsage,
    RagTelemetryRecord,
    RagTelemetrySink,
    RagTokenUsage,
    make_rag_trace,
)
from .retrieval_config import EMBEDDING_MODEL, repository_root
from .retrieval_database import connect
from .retrieval_evidence_backend import build_database_evidence_package
from .smpc_guideline_bridge import (
    BridgeExpansion,
    SmPCGuidelineBridgeCatalog,
)

DEFAULT_DENSE_DISTANCE_THRESHOLD = 0.45
DEFAULT_TRIGRAM_RELEVANCE_THRESHOLD = 0.30
RAG_CORE_SCHEMA_VERSION = "closed-rag-run-1.0.0"


class RetrievalMode(StrEnum):
    FTS = "fts"
    VECTOR = "vector"
    HYBRID_RRF = "hybrid_rrf"
    HYBRID_RRF_BRIDGE = "hybrid_rrf_bridge"


class RagHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    rank: int = Field(gt=0)
    evidence_role: Literal["direct", "linked_context", "bridge_context"]
    source_role: str
    source_status: str
    source_document_id: str
    source_native_item_number: str | None = None
    pdf_pages_1based: tuple[int, ...] = ()
    channel_ranks: dict[str, int] = Field(default_factory=dict)
    raw_scores: dict[str, float | None] = Field(default_factory=dict)
    rrf_score: float | None = None
    relation_types: tuple[str, ...] = ()
    seed_evidence_ids: tuple[str, ...] = ()
    bridge_id: str | None = None
    bridge_confidence: float | None = None
    bridge_matching_method: str | None = None
    formal_item_ids: tuple[str, ...] = ()


class RagRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_snapshot_id: str
    query_sha256: str
    retrieval_mode: RetrievalMode
    routing_mode: str
    normalized_query_expansions: tuple[str, ...]
    retrieval_outcome: Literal[
        "evidence_found", "retrieval_failure", "no_evidence_in_snapshot"
    ]
    retrieval_fallback_complete: bool
    hits: tuple[RagHit, ...]
    guideline_item_ranking: tuple[RagHit, ...]
    evidence_ids: tuple[str, ...]
    channel_status: tuple[dict[str, Any], ...]
    routing_notes: tuple[str, ...]
    retrieval_time_ms: float = Field(ge=0)
    query_normalization_time_ms: float = Field(default=0, ge=0)
    exact_search_time_ms: float = Field(default=0, ge=0)
    fts_time_ms: float = Field(default=0, ge=0)
    trigram_time_ms: float = Field(default=0, ge=0)
    vector_time_ms: float = Field(default=0, ge=0)
    rrf_time_ms: float = Field(default=0, ge=0)
    relation_expansion_time_ms: float = Field(ge=0)
    evidence_package_time_ms: float = Field(default=0, ge=0)
    database_time_ms: float = Field(ge=0)
    embedding_time_ms: float = Field(ge=0)
    embedding_cache_hit: bool | None = None
    embedding_provider_calls: int = Field(ge=0)
    embedding_tokens: int = Field(ge=0)
    embedding_cost_usd: float = Field(ge=0)


class ValidatedAnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_text: str
    evidence_ids: tuple[str, ...]
    support_status: PublicSupportLabel | None
    validator_status: ValidatorStatus
    issue_codes: tuple[str, ...]
    citations: tuple[BackendCitation, ...]


class ValidatedRagAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer_status: PublicSupportLabel | None
    answer_text: str
    claims: tuple[ValidatedAnswerClaim, ...]
    citations: tuple[BackendCitation, ...]
    limitations: tuple[str, ...]
    abstention_reason: str | None
    validator_status: ValidatorStatus
    validator_issue_codes: tuple[str, ...]
    model_answer_status: str
    publishable: bool


class RagRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = RAG_CORE_SCHEMA_VERSION
    run_id: str
    question_id: str
    arm: str
    corpus_snapshot_id: str
    retrieval: RagRetrievalResult | None
    evidence_package_id: str
    evidence_allowlist: tuple[str, ...]
    model_answer: RagStructuredAnswer | None
    validated_answer: ValidatedRagAnswer | None
    telemetry: RagTelemetryRecord
    dry_run: bool
    created_at: datetime


@dataclass(frozen=True)
class RagCoreConfig:
    top_k: int = 10
    max_evidence: int = 14
    rrf_k: int = 60
    relation_seed_limit: int = 5
    relation_limit: int = 30
    bridge_limit: int = 20
    dense_distance_threshold: float = DEFAULT_DENSE_DISTANCE_THRESHOLD
    trigram_relevance_threshold: float = DEFAULT_TRIGRAM_RELEVANCE_THRESHOLD

    def __post_init__(self) -> None:
        if min(
            self.top_k,
            self.max_evidence,
            self.rrf_k,
            self.relation_seed_limit,
            self.relation_limit,
            self.bridge_limit,
        ) <= 0:
            raise ValueError("RAG core integer limits must be positive")
        if not 0 <= self.dense_distance_threshold <= 2:
            raise ValueError("dense distance threshold must be between 0 and 2")
        if not 0 <= self.trigram_relevance_threshold <= 1:
            raise ValueError("trigram relevance threshold must be between 0 and 1")


class TimedQueryExecutor:
    """Measure only the locally controlled database calls."""

    def __init__(self, delegate: QueryExecutor) -> None:
        self.delegate = delegate
        self.database_time_ms = 0.0
        self.relation_expansion_time_ms = 0.0
        self.channel_time_ms: dict[str, float] = defaultdict(float)

    def fetch_all(
        self, statement: str, parameters: Sequence[Any]
    ) -> list[Mapping[str, Any]]:
        started = time.perf_counter()
        try:
            return self.delegate.fetch_all(statement, parameters)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            self.database_time_ms += elapsed
            if "retrieval.expand_relations(" in statement:
                self.relation_expansion_time_ms += elapsed
                self.channel_time_ms["relation_expansion"] += elapsed
            elif "retrieval.search_lexical(" in statement:
                self.channel_time_ms["fts"] += elapsed
            elif "retrieval.search_vector_exact(" in statement:
                self.channel_time_ms["vector"] += elapsed
            elif "retrieval.search_exact(" in statement:
                self.channel_time_ms["exact"] += elapsed
            elif "retrieval.search_trigram(" in statement:
                self.channel_time_ms["trigram"] += elapsed
            elif "retrieval.evidence_package_rows(" in statement:
                self.channel_time_ms["evidence_package"] += elapsed


def resolve_snapshot_id(root: Path, explicit: str | None = None) -> str:
    manifests = root / "outputs/knowledge_corpus/manifests/corpus_snapshots"
    if explicit:
        path = manifests / f"{explicit}.json"
        if not path.is_file():
            raise FileNotFoundError(f"snapshot manifest does not exist: {path}")
        return explicit
    configured = os.environ.get("AISURGEON_CORPUS_SNAPSHOT_ID")
    if configured:
        return resolve_snapshot_id(root, configured)
    candidates: list[tuple[str, str]] = []
    for path in manifests.glob("cs-*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("retrieval_unit_count") or 0) == 4469:
            candidates.append(
                (str(payload.get("created_at_utc") or ""), str(payload["corpus_snapshot_id"]))
            )
    if not candidates:
        raise RuntimeError("no sealed 4,469-unit corpus snapshot manifest found")
    return max(candidates)[1]


def _channels(mode: RetrievalMode) -> tuple[RetrievalChannel, ...]:
    if mode is RetrievalMode.FTS:
        return (RetrievalChannel.FTS_GERMAN, RetrievalChannel.FTS_SIMPLE)
    if mode is RetrievalMode.VECTOR:
        return (RetrievalChannel.DENSE_EXACT,)
    return tuple(RetrievalChannel)


def _candidate_is_relevant(candidate: Any, config: RagCoreConfig) -> bool:
    channels = candidate.channel_ranks
    if any(name.startswith(("exact:", "fts_german:", "fts_simple:")) for name in channels):
        return True
    trigram = [
        value
        for name, value in candidate.raw_scores.items()
        if name.startswith("trigram:") and value is not None
    ]
    if trigram and max(trigram) >= config.trigram_relevance_threshold:
        return True
    dense = [
        value
        for name, value in candidate.raw_scores.items()
        if name.startswith("dense_exact:") and value is not None
    ]
    return bool(dense and min(dense) <= config.dense_distance_threshold)


def _direct_hit(candidate: Any, rank: int) -> RagHit:
    metadata = candidate.metadata
    return RagHit(
        evidence_id=candidate.retrieval_unit_id,
        rank=rank,
        evidence_role="direct",
        source_role=str(metadata.get("source_role") or ""),
        source_status=str(metadata.get("source_status") or ""),
        source_document_id=str(metadata.get("source_document_id") or ""),
        source_native_item_number=metadata.get("source_native_item_number"),
        pdf_pages_1based=tuple(metadata.get("pdf_pages_1based") or ()),
        channel_ranks=dict(candidate.channel_ranks),
        raw_scores=dict(candidate.raw_scores),
        rrf_score=candidate.rrf_score,
    )


def _linked_hit(candidate: Any, rank: int) -> RagHit:
    metadata = candidate.metadata
    return RagHit(
        evidence_id=candidate.retrieval_unit_id,
        rank=rank,
        evidence_role="linked_context",
        source_role=str(metadata.get("source_role") or ""),
        source_status=str(metadata.get("source_status") or ""),
        source_document_id=str(metadata.get("source_document_id") or ""),
        source_native_item_number=metadata.get("source_native_item_number"),
        pdf_pages_1based=tuple(metadata.get("pdf_pages_1based") or ()),
        relation_types=tuple(candidate.relation_types),
        seed_evidence_ids=tuple(candidate.seed_retrieval_unit_ids),
    )


def _bridge_hit(expansion: BridgeExpansion, record: EvidenceRecord, rank: int) -> RagHit:
    return RagHit(
        evidence_id=expansion.retrieval_unit_id,
        rank=rank,
        evidence_role="bridge_context",
        source_role=record.source_role,
        source_status=record.source_status,
        source_document_id=record.source_document_id,
        source_native_item_number=expansion.guideline_item_number,
        pdf_pages_1based=record.pdf_pages_1based,
        relation_types=(expansion.relation_type,),
        seed_evidence_ids=(expansion.source_smpc_evidence_id,),
        bridge_id=expansion.bridge_id,
        bridge_confidence=expansion.confidence,
        bridge_matching_method=expansion.matching_method,
        formal_item_ids=expansion.guideline_formal_item_ids,
    )


def _claim_guard_codes(claim_text: str, evidence: Sequence[EvidenceRecord]) -> tuple[str, ...]:
    """Conservative text-level guards complement structured field validation."""

    combined = " ".join(row.exact_source_text.casefold() for row in evidence)
    claim = claim_text.casefold()
    issues: set[str] = set()

    for number in re.findall(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])", claim):
        alternatives = {number, number.replace(",", "."), number.replace(".", ",")}
        if not any(value in combined for value in alternatives):
            issues.add("numeric_value_not_in_cited_evidence")

    route_groups = {
        "intravenous": ("intravenös", "intravenous", "i.v.", " iv "),
        "oral": ("oral", "per os", "p.o."),
        "subcutaneous": ("subkutan", "subcutaneous", "s.c."),
    }
    for terms in route_groups.values():
        if any(term in claim for term in terms) and not any(term in combined for term in terms):
            issues.add("route_not_in_cited_evidence")

    population_terms = (
        "schwanger",
        "schwangerschaft",
        "kinder",
        "jugendliche",
        "ältere",
        "onkologisch",
        "tumorpatient",
        "niereninsuffizienz",
        "leberinsuffizienz",
    )
    for term in population_terms:
        if term in claim and term not in combined:
            issues.add("population_not_in_cited_evidence")

    if detect_negation(claim_text) and not any(
        detect_negation(row.exact_source_text) for row in evidence
    ):
        issues.add("negation_not_in_cited_evidence")
    if re.search(r"\bfinal(?:e|en|er|es)?\b", claim) and any(
        row.source_status != "final" for row in evidence
    ):
        issues.add("source_status_claim_mismatch")
    return tuple(sorted(issues))


def validate_structured_answer(
    answer: RagStructuredAnswer,
    *,
    package: EvidencePackage,
    evidence_catalog: Mapping[str, EvidenceRecord],
    retrieval_outcome: str,
    retrieval_fallback_complete: bool,
    baseline_without_retrieval: bool = False,
) -> ValidatedRagAnswer:
    """Derive public status and backend citations; fail closed on any invalid ID."""

    global_issues: set[str] = set()
    if baseline_without_retrieval:
        global_issues.add("baseline_without_retrieval_not_publishable")
    if retrieval_outcome == "retrieval_failure":
        global_issues.add("retrieval_failure")
    if not retrieval_fallback_complete and retrieval_outcome == "no_evidence_in_snapshot":
        global_issues.add("incomplete_retrieval_fallback")

    if answer.answer_status == "no_validated_evidence":
        if answer.claims or answer.answer_text.strip():
            global_issues.add("abstention_contains_unsupported_content")
        if not retrieval_fallback_complete:
            global_issues.add("abstention_requires_complete_retrieval_fallback")
    if (
        not package.allowlist_ids
        and retrieval_outcome == "no_evidence_in_snapshot"
        and answer.answer_status != "no_validated_evidence"
    ):
        global_issues.add("model_failed_required_abstention")
    if answer.answer_status != "no_validated_evidence" and not answer.claims:
        global_issues.add("non_abstaining_answer_has_no_claims")

    validated_claims: list[ValidatedAnswerClaim] = []
    all_citations: dict[str, BackendCitation] = {}
    for index, model_claim in enumerate(answer.claims, start=1):
        entailment = {
            "supported": EntailmentStatus.SUPPORTED,
            "partially_supported": EntailmentStatus.PARTIAL,
            "no_validated_evidence": EntailmentStatus.INSUFFICIENT,
        }[model_claim.support_status]
        proposed = PublicSupportLabel(model_claim.support_status)
        outcome = (
            RetrievalOutcome(retrieval_outcome)
            if retrieval_outcome in {item.value for item in RetrievalOutcome}
            else RetrievalOutcome.RETRIEVAL_FAILURE
        )
        draft = ClaimDraft(
            answer_claim_id=f"claim-{index}",
            claim_text=model_claim.claim_text,
            evidence_ids=tuple(model_claim.evidence_ids),
            proposed_public_label=proposed,
            entailment_status=entailment,
            retrieval_outcome=outcome,
            retrieval_fallback_complete=retrieval_fallback_complete,
            conflict_status=ConflictStatus.NONE,
            applicability_status=ApplicabilityStatus.APPLICABLE,
        )
        checked = validate_claim(draft, package=package, evidence_catalog=evidence_catalog)
        cited_rows = [
            evidence_catalog[evidence_id]
            for evidence_id in checked.validated_evidence_ids
            if evidence_id in evidence_catalog
        ]
        guard_codes = _claim_guard_codes(model_claim.claim_text, cited_rows)
        issue_codes = tuple(
            dict.fromkeys([*(issue.code for issue in checked.issues), *guard_codes])
        )
        status = checked.validator_status
        public_label = checked.public_support_label
        citations = checked.citations
        if guard_codes:
            status = ValidatorStatus.REJECTED
            public_label = None
            citations = ()
        if status == ValidatorStatus.REJECTED:
            global_issues.update(issue_codes or ("claim_rejected",))
        for citation in citations:
            all_citations[citation.evidence_id] = citation
        validated_claims.append(
            ValidatedAnswerClaim(
                claim_text=model_claim.claim_text,
                evidence_ids=tuple(model_claim.evidence_ids),
                support_status=public_label,
                validator_status=status,
                issue_codes=issue_codes,
                citations=citations,
            )
        )

    rejected = bool(global_issues) or any(
        claim.validator_status == ValidatorStatus.REJECTED for claim in validated_claims
    )
    if rejected:
        status = ValidatorStatus.REJECTED
        public_status = None
        answer_text = ""
        citations: tuple[BackendCitation, ...] = ()
    elif answer.answer_status == "no_validated_evidence":
        status = ValidatorStatus.ACCEPTED
        public_status = PublicSupportLabel.NO_VALIDATED_EVIDENCE
        answer_text = ""
        citations = ()
    elif any(
        claim.validator_status == ValidatorStatus.DOWNGRADED
        or claim.support_status == PublicSupportLabel.PARTIALLY_SUPPORTED
        for claim in validated_claims
    ) or answer.answer_status == "partially_supported":
        status = ValidatorStatus.DOWNGRADED
        public_status = PublicSupportLabel.PARTIALLY_SUPPORTED
        answer_text = answer.answer_text
        citations = tuple(all_citations.values())
    else:
        status = ValidatorStatus.ACCEPTED
        public_status = PublicSupportLabel.SUPPORTED
        answer_text = answer.answer_text
        citations = tuple(all_citations.values())

    return ValidatedRagAnswer(
        answer_status=public_status,
        answer_text=answer_text,
        claims=tuple(validated_claims),
        citations=citations,
        limitations=tuple(answer.limitations),
        abstention_reason=answer.abstention_reason,
        validator_status=status,
        validator_issue_codes=tuple(sorted(global_issues)),
        model_answer_status=answer.answer_status,
        publishable=status != ValidatorStatus.REJECTED,
    )


class RagCore:
    """One importable core for local retrieval and closed Responses generation."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        corpus_snapshot_id: str | None = None,
        config: RagCoreConfig | None = None,
        responses_client: ClosedResponsesClient | None = None,
        query_embedding_cache: QueryEmbeddingCache | None = None,
        telemetry_sink: RagTelemetrySink | None = None,
        bridge_catalog: SmPCGuidelineBridgeCatalog | None = None,
    ) -> None:
        self.root = repository_root(root)
        self.corpus_snapshot_id = resolve_snapshot_id(self.root, corpus_snapshot_id)
        self.config = config or RagCoreConfig()
        self.responses_client = responses_client
        self.query_embedding_cache = query_embedding_cache or QueryEmbeddingCache(
            corpus_snapshot_id=self.corpus_snapshot_id, root=self.root
        )
        self.telemetry_sink = telemetry_sink or RagTelemetrySink(
            self.root
            / "outputs/retrieval_phase"
            / self.corpus_snapshot_id
            / "telemetry/closed_rag.jsonl"
        )
        bridge_path = (
            self.root
            / "outputs/retrieval_phase/bridges/smpc_guideline_bridge.jsonl"
        )
        self.bridge_catalog = bridge_catalog or SmPCGuidelineBridgeCatalog.load(
            bridge_path
        )

    def _embedding(
        self,
        normalized: NormalizedQuery,
        mode: RetrievalMode,
        *,
        allow_openai: bool,
    ) -> QueryEmbeddingResult | None:
        if RetrievalChannel.DENSE_EXACT not in _channels(mode):
            return None
        return self.query_embedding_cache.get(
            normalized.cleaned_text, allow_provider_call=allow_openai
        )

    def retrieve(
        self,
        *,
        question: str,
        retrieval_mode: RetrievalMode | str = RetrievalMode.HYBRID_RRF_BRIDGE,
        routing_mode: RoutingMode | str | None = None,
        allow_embedding_api: bool = True,
    ) -> RagRetrievalResult:
        mode = RetrievalMode(retrieval_mode)
        normalization_started = time.perf_counter()
        normalized = normalize_query(question)
        normalization_time_ms = (time.perf_counter() - normalization_started) * 1000
        embedding = self._embedding(normalized, mode, allow_openai=allow_embedding_api)
        started = time.perf_counter()
        with connect(self.root) as connection:
            timed = TimedQueryExecutor(PsycopgQueryExecutor(connection))
            retriever = HybridRetriever(
                timed,
                config=HybridRetrievalConfig(
                    rrf_k=self.config.rrf_k,
                    top_k=self.config.top_k,
                    relation_seed_limit=self.config.relation_seed_limit,
                    relation_limit=self.config.relation_limit,
                    expand_relations=True,
                    enabled_channels=_channels(mode),
                ),
            )
            raw = retriever.search(
                query=normalized.cleaned_text,
                lexical_query=(
                    normalized.lexical_text
                    if mode is RetrievalMode.FTS
                    else normalized.lexical_text.replace(" OR ", " ")
                ),
                corpus_snapshot_id=self.corpus_snapshot_id,
                routing_mode=routing_mode,
                query_embedding=embedding.vector if embedding else None,
            )
        retrieval_wall = (time.perf_counter() - started) * 1000
        classified_database_time = sum(timed.channel_time_ms.values())
        rrf_time_ms = max(retrieval_wall - classified_database_time, 0.0)

        relevant_direct = [
            candidate
            for candidate in raw.direct_candidates
            if _candidate_is_relevant(candidate, self.config)
        ]
        relevant_ids = {candidate.retrieval_unit_id for candidate in relevant_direct}
        relevant_linked = [
            candidate
            for candidate in raw.linked_context
            if relevant_ids.intersection(candidate.seed_retrieval_unit_ids)
        ]

        hits: list[RagHit] = [
            _direct_hit(candidate, rank)
            for rank, candidate in enumerate(relevant_direct, start=1)
        ]
        bridge_time_ms = 0.0
        if mode is RetrievalMode.HYBRID_RRF_BRIDGE and relevant_direct:
            bridge_started = time.perf_counter()
            expansions = self.bridge_catalog.expand_from_smpc_candidates(
                [candidate.to_dict() for candidate in relevant_direct],
                limit=self.config.bridge_limit,
            )
            expansion_ids = [item.retrieval_unit_id for item in expansions]
            catalog = (
                build_database_evidence_package(
                    corpus_snapshot_id=self.corpus_snapshot_id,
                    evidence_ids=expansion_ids,
                    root=self.root,
                    persist=False,
                )[1]
                if expansion_ids
                else {}
            )
            for expansion in expansions:
                if expansion.retrieval_unit_id in relevant_ids:
                    continue
                record = catalog.get(expansion.retrieval_unit_id)
                if record is None:
                    continue
                hits.append(_bridge_hit(expansion, record, len(hits) + 1))
                relevant_ids.add(expansion.retrieval_unit_id)
            bridge_time_ms = (time.perf_counter() - bridge_started) * 1000

        for candidate in relevant_linked:
            if candidate.retrieval_unit_id in relevant_ids:
                continue
            hits.append(_linked_hit(candidate, len(hits) + 1))
            relevant_ids.add(candidate.retrieval_unit_id)

        evidence_ids = tuple(hit.evidence_id for hit in hits[: self.config.max_evidence])
        hits = [
            hit.model_copy(update={"rank": rank})
            for rank, hit in enumerate(
                [hit for hit in hits if hit.evidence_id in evidence_ids], start=1
            )
        ]
        guideline_items = [
            hit
            for hit in hits
            if hit.source_role == "guideline" and hit.source_native_item_number
        ]
        guideline_items = [
            hit.model_copy(update={"rank": rank})
            for rank, hit in enumerate(guideline_items, start=1)
        ]

        failed = any(
            status.status == "failed"
            and status.channel in {channel.value for channel in _channels(mode)}
            for status in raw.channel_status
        )
        skipped_dense = (
            RetrievalChannel.DENSE_EXACT in _channels(mode) and embedding is None
        )
        fallback_complete = not failed and not skipped_dense
        if evidence_ids:
            outcome = "evidence_found"
        elif fallback_complete:
            outcome = "no_evidence_in_snapshot"
        else:
            outcome = "retrieval_failure"

        return RagRetrievalResult(
            corpus_snapshot_id=self.corpus_snapshot_id,
            query_sha256=normalized.query_sha256,
            retrieval_mode=mode,
            routing_mode=raw.routing_mode.value,
            normalized_query_expansions=normalized.applied_expansions,
            retrieval_outcome=outcome,
            retrieval_fallback_complete=fallback_complete,
            hits=tuple(hits),
            guideline_item_ranking=tuple(guideline_items),
            evidence_ids=evidence_ids,
            channel_status=tuple(status.to_dict() for status in raw.channel_status),
            routing_notes=tuple(raw.routing_notes),
            retrieval_time_ms=max(
                0.0,
                retrieval_wall - timed.relation_expansion_time_ms + bridge_time_ms,
            ),
            query_normalization_time_ms=normalization_time_ms,
            exact_search_time_ms=timed.channel_time_ms["exact"],
            fts_time_ms=timed.channel_time_ms["fts"],
            trigram_time_ms=timed.channel_time_ms["trigram"],
            vector_time_ms=timed.channel_time_ms["vector"],
            rrf_time_ms=rrf_time_ms,
            relation_expansion_time_ms=timed.relation_expansion_time_ms + bridge_time_ms,
            evidence_package_time_ms=timed.channel_time_ms["evidence_package"],
            database_time_ms=timed.database_time_ms,
            embedding_time_ms=embedding.latency_ms if embedding else 0.0,
            embedding_cache_hit=embedding.cache_hit if embedding else None,
            embedding_provider_calls=embedding.provider_calls if embedding else 0,
            embedding_tokens=(
                embedding.input_tokens if embedding and embedding.provider_calls else 0
            ),
            embedding_cost_usd=(
                embedding.estimated_cost_usd if embedding and embedding.provider_calls else 0.0
            ),
        )

    @staticmethod
    def _trace_rankings(
        retrieval: RagRetrievalResult | None,
    ) -> tuple[dict[str, tuple[RagCandidateRank, ...]], tuple[RagCandidateRank, ...]]:
        if retrieval is None:
            return {}, ()
        channel_rows: dict[str, list[RagCandidateRank]] = defaultdict(list)
        fused: list[RagCandidateRank] = []
        for hit in retrieval.hits:
            role = hit.evidence_role
            fused.append(
                RagCandidateRank(
                    evidence_id=hit.evidence_id,
                    rank=hit.rank,
                    evidence_role=role,
                )
            )
            for channel, rank in hit.channel_ranks.items():
                channel_rows[channel].append(
                    RagCandidateRank(
                        evidence_id=hit.evidence_id,
                        rank=rank,
                        evidence_role=role,
                    )
                )
            if role == "bridge_context":
                channel_rows["bridge:smPC_to_guideline"].append(
                    RagCandidateRank(
                        evidence_id=hit.evidence_id,
                        rank=len(channel_rows["bridge:smPC_to_guideline"]) + 1,
                        evidence_role=role,
                    )
                )
        return {key: tuple(value) for key, value in sorted(channel_rows.items())}, tuple(fused)

    def _package(
        self, evidence_ids: Sequence[str]
    ) -> tuple[EvidencePackage, dict[str, EvidenceRecord]]:
        if evidence_ids:
            return build_database_evidence_package(
                corpus_snapshot_id=self.corpus_snapshot_id,
                evidence_ids=evidence_ids,
                root=self.root,
                persist=False,
            )
        package = build_evidence_package(
            corpus_snapshot_id=self.corpus_snapshot_id,
            evidence_ids=(),
            evidence_catalog={},
        )
        return package, {}

    def build_evidence_package(
        self, evidence_ids: Sequence[str]
    ) -> tuple[EvidencePackage, dict[str, EvidenceRecord]]:
        """Public finite-package builder for benchmark preflight and future APIs."""

        return self._package(evidence_ids)

    def run(
        self,
        *,
        question: str,
        question_id: str,
        retrieval_mode: RetrievalMode | str = RetrievalMode.HYBRID_RRF_BRIDGE,
        routing_mode: RoutingMode | str | None = None,
        dry_run: bool = False,
        baseline_without_retrieval: bool = False,
        run_id: str | None = None,
    ) -> RagRunResult:
        run_id = run_id or f"rag-{uuid4()}"
        arm = "no_retrieval_context" if baseline_without_retrieval else "closed_corpus_rag"
        retrieval: RagRetrievalResult | None = None
        if not baseline_without_retrieval:
            retrieval = self.retrieve(
                question=question,
                retrieval_mode=retrieval_mode,
                routing_mode=routing_mode,
                allow_embedding_api=not dry_run,
            )
        evidence_ids = retrieval.evidence_ids if retrieval else ()
        package, catalog = self._package(evidence_ids)
        channels, fused = self._trace_rankings(retrieval)
        response_result: ClosedResponseResult | None = None
        validated: ValidatedRagAnswer | None = None
        error: ClosedResponsesError | None = None
        client = self.responses_client
        if not dry_run:
            client = client or ClosedResponsesClient()
            try:
                response_result = client.answer(
                    question=question,
                    package=package,
                    baseline_without_retrieval=baseline_without_retrieval,
                )
                validated = validate_structured_answer(
                    response_result.answer,
                    package=package,
                    evidence_catalog=catalog,
                    retrieval_outcome=(
                        retrieval.retrieval_outcome
                        if retrieval
                        else "retrieval_failure"
                    ),
                    retrieval_fallback_complete=(
                        retrieval.retrieval_fallback_complete if retrieval else False
                    ),
                    baseline_without_retrieval=baseline_without_retrieval,
                )
            except ClosedResponsesError as exc:
                error = exc

        response_usage = (
            response_result.metadata.token_usage if response_result else RagTokenUsage()
        )
        embedding_tokens = retrieval.embedding_tokens if retrieval else 0
        token_usage = response_usage.model_copy(
            update={"embedding_tokens": embedding_tokens}
        )
        embedding_cost = retrieval.embedding_cost_usd if retrieval else 0.0
        response_cost = (
            response_result.metadata.cost.estimated_cost_usd
            if response_result and response_result.metadata.cost.estimated_cost_usd is not None
            else None
        )
        total_cost = (
            response_cost + embedding_cost if response_cost is not None else embedding_cost
        )
        cost = RagCostUsage(
            estimated_cost_usd=total_cost,
            response_cost_usd=response_cost,
            embedding_cost_usd=embedding_cost,
            price_source=(
                response_result.metadata.cost.price_source if response_result else None
            ),
            price_as_of=(
                response_result.metadata.cost.price_as_of if response_result else None
            ),
            estimation_method=(
                "published_response_and_embedding_token_rates"
                if response_result
                else "embedding_rate_only_or_no_api_call"
            ),
        )
        metadata = response_result.metadata if response_result else None
        validator_status = (
            validated.validator_status.value
            if validated
            else ("rejected" if error else "not_run")
        )
        issue_codes = (
            validated.validator_issue_codes
            if validated
            else ((error.error_code,) if error else ())
        )
        trace = make_rag_trace(
            run_id=run_id,
            question_id=question_id,
            arm=arm,
            question_text=question,
            corpus_snapshot_id=self.corpus_snapshot_id,
            retrieval_mode=(
                str(RetrievalMode(retrieval_mode).value)
                if not baseline_without_retrieval
                else "none"
            ),
            model=(
                client.config.model if client is not None else None
            ),
            model_snapshot=metadata.model_snapshot if metadata else None,
            embedding_model=EMBEDDING_MODEL,
            prompt_version=(
                f"{RAG_PROMPT_VERSION}-no-context-baseline"
                if baseline_without_retrieval
                else RAG_PROMPT_VERSION
            ),
            output_schema_version=RAG_OUTPUT_SCHEMA_VERSION,
            reasoning_effort=(
                client.config.reasoning_effort if client is not None else "none"
            ),
            max_output_tokens=(
                client.config.max_output_tokens if client is not None else 700
            ),
            candidates_by_channel=channels,
            rrf_result=fused,
            sent_evidence_ids=package.allowlist_ids,
            retrieval_time_ms=retrieval.retrieval_time_ms if retrieval else 0.0,
            relation_expansion_time_ms=(
                retrieval.relation_expansion_time_ms if retrieval else 0.0
            ),
            database_time_ms=retrieval.database_time_ms if retrieval else 0.0,
            embedding_time_ms=retrieval.embedding_time_ms if retrieval else 0.0,
            embedding_cache_hit=retrieval.embedding_cache_hit if retrieval else None,
            embedding_provider_calls=(
                retrieval.embedding_provider_calls if retrieval else 0
            ),
            token_usage=token_usage,
            cost=cost,
            validator_status=validator_status,  # type: ignore[arg-type]
            validator_issue_codes=issue_codes,
            api_wall_time_ms=(
                metadata.api_wall_time_ms
                if metadata
                else (error.api_wall_time_ms if error else None)
            ),
            openai_processing_ms=metadata.openai_processing_ms if metadata else None,
            x_request_id=metadata.x_request_id if metadata else None,
            http_status=(
                metadata.http_status
                if metadata
                else (error.status_code if error else None)
            ),
            retry_count=(
                metadata.retry_count
                if metadata
                else (error.retry_count if error else 0)
            ),
            retry_statuses=(
                metadata.retry_statuses
                if metadata
                else (error.retry_statuses if error else ())
            ),
            rate_limit_headers=metadata.rate_limit_headers if metadata else {},
            error_code=error.error_code if error else None,
        )
        self.telemetry_sink.append(trace)
        if error is not None:
            raise error
        return RagRunResult(
            run_id=run_id,
            question_id=question_id,
            arm=arm,
            corpus_snapshot_id=self.corpus_snapshot_id,
            retrieval=retrieval,
            evidence_package_id=package.evidence_package_id,
            evidence_allowlist=package.allowlist_ids,
            model_answer=response_result.answer if response_result else None,
            validated_answer=validated,
            telemetry=trace,
            dry_run=dry_run,
            created_at=datetime.now(UTC),
        )


__all__ = [
    "RAG_CORE_SCHEMA_VERSION",
    "RagCore",
    "RagCoreConfig",
    "RagHit",
    "RagRetrievalResult",
    "RagRunResult",
    "RetrievalMode",
    "ValidatedAnswerClaim",
    "ValidatedRagAnswer",
    "resolve_snapshot_id",
    "validate_structured_answer",
]

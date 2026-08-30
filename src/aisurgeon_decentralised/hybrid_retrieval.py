"""Policy-gated hybrid retrieval over the regenerable PostgreSQL index.

All normal retrieval operations in this module call SQL functions from
``db/migrations/0004_eligibility_and_search.sql``.  Those functions use the
``retrieval.eligible_retrieval_units`` security-barrier view.  The Python
layer intentionally never queries ``retrieval.retrieval_unit`` directly.

Raw channel scores are retained only for diagnostics.  Reciprocal Rank
Fusion uses ranks exclusively, so incomparable exact, FTS, trigram, and
vector scores are never added together.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from .retrieval_config import DEFAULT_RRF_K, EMBEDDING_DIMENSION, EMBEDDING_MODEL

GUIDELINE_ROLE = "guideline"
SMPC_ROLE = "smPC"


class RoutingMode(StrEnum):
    """Source routing resolved before executing retrieval channels."""

    GUIDELINE_FIRST = "guideline_first"
    SMPC_FIRST = "smpc_first"
    DUAL_SOURCE = "dual_source"


class RetrievalChannel(StrEnum):
    EXACT = "exact"
    FTS_GERMAN = "fts_german"
    FTS_SIMPLE = "fts_simple"
    TRIGRAM = "trigram"
    DENSE_EXACT = "dense_exact"


ALLOWED_RELATION_TYPES = frozenset(
    {
        "guideline_item_to_rationale",
        "guideline_item_to_comment",
        "guideline_item_to_evidence_grade",
        "guideline_item_to_references",
        "guideline_item_to_tables_figures",
        "product_has_active_substance",
        "product_to_active_substance_context",
        "medicine_to_dosing",
        "medicine_to_warning",
        "medicine_to_contraindication",
        "medicine_to_adverse_reaction",
        "table_to_header_context",
        "table_to_parent_context",
    }
)


SEARCH_EXACT_SQL = """
SELECT retrieval_unit_id, rank, score, match_kind
FROM retrieval.search_exact(%s, %s, %s, %s)
"""

SEARCH_LEXICAL_SQL = """
SELECT retrieval_unit_id, rank, score, configuration
FROM retrieval.search_lexical(%s, %s, %s, %s, %s)
"""

SEARCH_TRIGRAM_SQL = """
SELECT retrieval_unit_id, rank, score
FROM retrieval.search_trigram(%s, %s, %s, %s::real, %s)
"""

SEARCH_VECTOR_SQL = """
SELECT retrieval_unit_id, rank, cosine_distance
FROM retrieval.search_vector_exact(%s, %s::vector, %s, %s, %s)
"""

EXPAND_RELATIONS_SQL = """
SELECT seed_retrieval_unit_id, retrieval_unit_id, relation_type, evidence_role
FROM retrieval.expand_relations(%s, %s::text[], %s)
"""

EVIDENCE_PACKAGE_SQL = """
SELECT *
FROM retrieval.evidence_package_rows(%s, %s::text[])
"""


class QueryExecutor(Protocol):
    """Minimal injectable database interface used by the retriever."""

    def fetch_all(
        self, statement: str, parameters: Sequence[Any]
    ) -> list[Mapping[str, Any]]: ...


class PsycopgQueryExecutor:
    """Adapt a psycopg connection to :class:`QueryExecutor`."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def fetch_all(
        self, statement: str, parameters: Sequence[Any]
    ) -> list[Mapping[str, Any]]:
        # A channel error must not poison later fallback queries.  At top level
        # psycopg creates a short transaction here; inside a caller-owned
        # transaction it creates a savepoint and rolls back only this call.
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.execute(statement, tuple(parameters))
                if cursor.description is None:
                    return []
                columns = [
                    description.name
                    if hasattr(description, "name")
                    else description[0]
                    for description in cursor.description
                ]
                return [
                    dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
                ]


@dataclass(frozen=True)
class HybridRetrievalConfig:
    """Version-neutral technical defaults; none are claimed clinically optimal."""

    rrf_k: int = DEFAULT_RRF_K
    exact_candidates: int = 20
    lexical_candidates: int = 30
    trigram_candidates: int = 20
    dense_candidates: int = 30
    trigram_threshold: float = 0.15
    top_k: int = 10
    relation_seed_limit: int = 5
    relation_limit: int = 30
    embedding_model: str = EMBEDDING_MODEL
    expand_relations: bool = True
    enabled_channels: tuple[RetrievalChannel, ...] = tuple(RetrievalChannel)

    def __post_init__(self) -> None:
        for name in (
            "rrf_k",
            "exact_candidates",
            "lexical_candidates",
            "trigram_candidates",
            "dense_candidates",
            "top_k",
            "relation_seed_limit",
            "relation_limit",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "exact_candidates",
            "lexical_candidates",
            "trigram_candidates",
            "dense_candidates",
            "top_k",
            "relation_limit",
        ):
            if getattr(self, name) > 1000:
                raise ValueError(f"{name} must not exceed the SQL safety cap of 1000")
        if not 0.0 <= self.trigram_threshold <= 1.0:
            raise ValueError("trigram_threshold must be between 0 and 1")
        if not self.embedding_model.strip():
            raise ValueError("embedding_model must not be empty")
        if not self.enabled_channels:
            raise ValueError("at least one retrieval channel must be enabled")
        if len(self.enabled_channels) != len(set(self.enabled_channels)):
            raise ValueError("enabled retrieval channels must be unique")
        if any(not isinstance(channel, RetrievalChannel) for channel in self.enabled_channels):
            raise ValueError("enabled_channels must contain RetrievalChannel values")


@dataclass(frozen=True)
class ChannelHit:
    retrieval_unit_id: str
    rank: int
    channel: str
    source_role: str
    raw_score: float | None = None
    raw_score_kind: str | None = None


@dataclass(frozen=True)
class ChannelStatus:
    channel: str
    source_role: str
    status: str
    result_count: int = 0
    error_type: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "source_role": self.source_role,
            "status": self.status,
            "result_count": self.result_count,
            "error_type": self.error_type,
            "detail": self.detail,
        }


@dataclass
class FusedCandidate:
    retrieval_unit_id: str
    rrf_score: float
    best_channel_rank: int
    source_roles: tuple[str, ...]
    channel_ranks: dict[str, int] = field(default_factory=dict)
    raw_scores: dict[str, float | None] = field(default_factory=dict)
    final_rank: int | None = None


@dataclass(frozen=True)
class EvidenceCandidate:
    retrieval_unit_id: str
    final_rank: int
    evidence_role: str
    rrf_score: float | None
    channel_ranks: Mapping[str, int]
    raw_scores: Mapping[str, float | None]
    relation_types: tuple[str, ...]
    seed_retrieval_unit_ids: tuple[str, ...]
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieval_unit_id": self.retrieval_unit_id,
            "final_rank": self.final_rank,
            "evidence_role": self.evidence_role,
            "rrf_score": self.rrf_score,
            "channel_ranks": dict(self.channel_ranks),
            # Raw scores are diagnostic only and are never used by RRF.
            "raw_scores": dict(self.raw_scores),
            "relation_types": list(self.relation_types),
            "seed_retrieval_unit_ids": list(self.seed_retrieval_unit_ids),
            **dict(self.metadata),
        }


@dataclass(frozen=True)
class HybridSearchResult:
    corpus_snapshot_id: str
    query_sha256: str
    routing_mode: RoutingMode
    retrieval_outcome: str
    direct_candidates: tuple[EvidenceCandidate, ...]
    linked_context: tuple[EvidenceCandidate, ...]
    evidence_allowlist: tuple[str, ...]
    channel_status: tuple[ChannelStatus, ...]
    routing_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_snapshot_id": self.corpus_snapshot_id,
            "query_sha256": self.query_sha256,
            "routing_mode": self.routing_mode.value,
            "retrieval_outcome": self.retrieval_outcome,
            "direct_candidates": [item.to_dict() for item in self.direct_candidates],
            "linked_context": [item.to_dict() for item in self.linked_context],
            "evidence_allowlist": list(self.evidence_allowlist),
            "channel_status": [item.to_dict() for item in self.channel_status],
            "routing_notes": list(self.routing_notes),
        }


class RetrievalFailure(RuntimeError):
    """Raised only when every attempted normal retrieval channel failed."""

    def __init__(self, message: str, statuses: Sequence[ChannelStatus]) -> None:
        super().__init__(message)
        self.statuses = tuple(statuses)


def infer_routing_mode(query: str) -> RoutingMode:
    """Deterministically route common query intents without an LLM classifier."""

    normalized = " ".join(query.casefold().split())
    dual_patterns = (
        r"leitlinie.*(?:fachinformation|smpc)",
        r"(?:fachinformation|smpc).*leitlinie",
        r"\b(?:unterschied|abweichung|abweichend|konflikt|widerspruch|versus|vs\.?)\b",
        r"\bmehrquellen",
    )
    if any(re.search(pattern, normalized) for pattern in dual_patterns):
        return RoutingMode.DUAL_SOURCE

    smpc_terms = (
        "dosis",
        "dosierung",
        "zubereitung",
        "gegenanzeige",
        "kontraindikation",
        "nebenwirkung",
        "warnhinweis",
        "wechselwirkung",
        "applikationsweg",
        "intervall",
        "zugelassen",
        "fachinformation",
        "smpc",
    )
    if any(term in normalized for term in smpc_terms):
        return RoutingMode.SMPC_FIRST

    guideline_terms = (
        "leitlinie",
        "empfehlung",
        "statement",
        "evidenzgrad",
        "empfehlungsgrad",
        "konsensstärke",
        "rationale",
        "begründung",
    )
    if any(term in normalized for term in guideline_terms):
        return RoutingMode.GUIDELINE_FIRST
    return RoutingMode.DUAL_SOURCE


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[ChannelHit]], *, k: int = DEFAULT_RRF_K
) -> list[FusedCandidate]:
    """Fuse ranked lists with RRF using ranks, never raw channel scores."""

    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("RRF k must be a positive integer")

    contributions: dict[str, float] = defaultdict(float)
    channel_ranks: dict[str, dict[str, int]] = defaultdict(dict)
    raw_scores: dict[str, dict[str, float | None]] = defaultdict(dict)
    roles: dict[str, set[str]] = defaultdict(set)

    for channel_name in sorted(rankings):
        best_by_unit: dict[str, ChannelHit] = {}
        for hit in rankings[channel_name]:
            if not hit.retrieval_unit_id or hit.rank <= 0:
                raise ValueError(f"invalid hit in channel {channel_name}")
            previous = best_by_unit.get(hit.retrieval_unit_id)
            if previous is None or hit.rank < previous.rank:
                best_by_unit[hit.retrieval_unit_id] = hit
        for unit_id, hit in best_by_unit.items():
            contributions[unit_id] += 1.0 / (k + hit.rank)
            channel_ranks[unit_id][channel_name] = hit.rank
            raw_scores[unit_id][channel_name] = hit.raw_score
            roles[unit_id].add(hit.source_role)

    fused = [
        FusedCandidate(
            retrieval_unit_id=unit_id,
            rrf_score=score,
            best_channel_rank=min(channel_ranks[unit_id].values()),
            source_roles=tuple(sorted(roles[unit_id])),
            channel_ranks=dict(sorted(channel_ranks[unit_id].items())),
            raw_scores=dict(sorted(raw_scores[unit_id].items())),
        )
        for unit_id, score in contributions.items()
    ]
    fused.sort(
        key=lambda item: (
            -item.rrf_score,
            item.best_channel_rank,
            item.retrieval_unit_id,
        )
    )
    for rank, item in enumerate(fused, start=1):
        item.final_rank = rank
    return fused


def _vector_literal(values: Sequence[float]) -> str:
    if len(values) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"query embedding dimension {len(values)} does not match {EMBEDDING_DIMENSION}"
        )
    normalized: list[str] = []
    norm_squared = 0.0
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("query embedding contains a non-finite value")
        norm_squared += number * number
        normalized.append(format(number, ".9g"))
    if norm_squared == 0.0:
        raise ValueError("query embedding must not be the zero vector")
    return "[" + ",".join(normalized) + "]"


def _safe_error_type(exc: BaseException) -> str:
    return type(exc).__name__[:100]


def _public_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "corpus_snapshot_id",
        "evidence_span_id",
        "source_version_id",
        "source_document_id",
        "document_kind",
        "source_status",
        "document_component",
        "source_role",
        "source_authority",
        "source_sha256",
        "text_sha256",
        "exact_source_text",
        "chapter_path",
        "source_native_item_type",
        "source_native_item_number",
        "printed_source_item_number",
        "pdf_page_index",
        "pdf_pages_1based",
        "printed_page_label",
        "table_id",
        "row_header_path",
        "column_header_path",
        "exact_table_cell_text",
        "product_ids",
        "active_substance_ids",
        "product_names",
        "active_substance_names",
        "strength",
        "pharmaceutical_form",
        "route",
        "dose_value",
        "dose_unit",
        "frequency",
        "population",
        "qa_status",
        "qa_flags",
        "conflict_status",
        "citation_label",
        "source_file_name",
        "extraction_pipeline_version",
    )
    result = {field_name: row.get(field_name) for field_name in fields}
    raw = row.get("raw_v1") or {}
    result["evidence_metadata"] = raw.get("evidence_metadata") or {}
    return result


class HybridRetriever:
    """Execute policy-gated hybrid retrieval with deterministic fallback."""

    def __init__(
        self,
        executor: QueryExecutor,
        *,
        config: HybridRetrievalConfig | None = None,
    ) -> None:
        self.executor = executor
        self.config = config or HybridRetrievalConfig()

    def _query_channel(
        self,
        *,
        channel: RetrievalChannel,
        snapshot_id: str,
        query: str,
        source_role: str,
        vector_literal: str | None,
    ) -> tuple[list[ChannelHit], ChannelStatus]:
        channel_key = f"{channel.value}:{source_role}"
        if channel is RetrievalChannel.DENSE_EXACT and vector_literal is None:
            return [], ChannelStatus(
                channel=channel.value,
                source_role=source_role,
                status="skipped",
                detail="query_embedding_not_supplied",
            )

        if channel is RetrievalChannel.EXACT:
            statement = SEARCH_EXACT_SQL
            parameters: Sequence[Any] = (
                snapshot_id,
                query,
                self.config.exact_candidates,
                source_role,
            )
            score_field = "score"
            score_kind = "exact_match_score"
        elif channel is RetrievalChannel.FTS_GERMAN:
            statement = SEARCH_LEXICAL_SQL
            parameters = (
                snapshot_id,
                query,
                "german",
                self.config.lexical_candidates,
                source_role,
            )
            score_field = "score"
            score_kind = "ts_rank_cd_german"
        elif channel is RetrievalChannel.FTS_SIMPLE:
            statement = SEARCH_LEXICAL_SQL
            parameters = (
                snapshot_id,
                query,
                "simple",
                self.config.lexical_candidates,
                source_role,
            )
            score_field = "score"
            score_kind = "ts_rank_cd_simple"
        elif channel is RetrievalChannel.TRIGRAM:
            statement = SEARCH_TRIGRAM_SQL
            parameters = (
                snapshot_id,
                query,
                self.config.trigram_candidates,
                self.config.trigram_threshold,
                source_role,
            )
            score_field = "score"
            score_kind = "trigram_similarity"
        else:
            statement = SEARCH_VECTOR_SQL
            parameters = (
                snapshot_id,
                vector_literal,
                self.config.embedding_model,
                self.config.dense_candidates,
                source_role,
            )
            score_field = "cosine_distance"
            score_kind = "cosine_distance"

        try:
            rows = self.executor.fetch_all(statement, parameters)
            hits: list[ChannelHit] = []
            for row in rows:
                unit_id = str(row["retrieval_unit_id"])
                rank = int(row["rank"])
                score_value = row.get(score_field)
                score = None if score_value is None else float(score_value)
                hits.append(
                    ChannelHit(
                        retrieval_unit_id=unit_id,
                        rank=rank,
                        channel=channel_key,
                        source_role=source_role,
                        raw_score=score,
                        raw_score_kind=score_kind,
                    )
                )
            return hits, ChannelStatus(
                channel=channel.value,
                source_role=source_role,
                status="ok" if hits else "empty",
                result_count=len(hits),
            )
        except Exception as exc:  # channel isolation is the intentional fallback boundary
            return [], ChannelStatus(
                channel=channel.value,
                source_role=source_role,
                status="failed",
                error_type=_safe_error_type(exc),
            )

    def _query_role(
        self,
        *,
        snapshot_id: str,
        query: str,
        lexical_query: str,
        source_role: str,
        vector_literal: str | None,
    ) -> tuple[dict[str, list[ChannelHit]], list[ChannelStatus]]:
        rankings: dict[str, list[ChannelHit]] = {}
        statuses: list[ChannelStatus] = []
        for channel in self.config.enabled_channels:
            channel_query = (
                lexical_query
                if channel in {RetrievalChannel.FTS_GERMAN, RetrievalChannel.FTS_SIMPLE}
                else query
            )
            hits, status = self._query_channel(
                channel=channel,
                snapshot_id=snapshot_id,
                query=channel_query,
                source_role=source_role,
                vector_literal=vector_literal,
            )
            statuses.append(status)
            if hits:
                rankings[f"{channel.value}:{source_role}"] = hits
        return rankings, statuses

    @staticmethod
    def _sort_for_route(
        fused: list[FusedCandidate], routing_mode: RoutingMode
    ) -> list[FusedCandidate]:
        if routing_mode is RoutingMode.DUAL_SOURCE:
            ordered = sorted(
                fused,
                key=lambda item: (
                    -item.rrf_score,
                    item.best_channel_rank,
                    item.retrieval_unit_id,
                ),
            )
        else:
            primary = (
                GUIDELINE_ROLE
                if routing_mode is RoutingMode.GUIDELINE_FIRST
                else SMPC_ROLE
            )
            ordered = sorted(
                fused,
                key=lambda item: (
                    0 if primary in item.source_roles else 1,
                    -item.rrf_score,
                    item.best_channel_rank,
                    item.retrieval_unit_id,
                ),
            )
        for rank, item in enumerate(ordered, start=1):
            item.final_rank = rank
        return ordered

    @staticmethod
    def _select_top_k(
        ordered: Sequence[FusedCandidate],
        routing_mode: RoutingMode,
        top_k: int,
    ) -> list[FusedCandidate]:
        """Apply source-routing coverage without modifying RRF scores.

        A dual-source request with at least two result slots reserves the
        best-ranked candidate from each available source role. Remaining
        slots follow the global RRF order. This is a routing constraint, not
        a score addition or a claim that either source resolves a conflict.
        """

        if routing_mode is not RoutingMode.DUAL_SOURCE or top_k < 2:
            return list(ordered[:top_k])

        selected_ids: set[str] = set()
        for role in (GUIDELINE_ROLE, SMPC_ROLE):
            candidate = next(
                (item for item in ordered if role in item.source_roles), None
            )
            if candidate is not None:
                selected_ids.add(candidate.retrieval_unit_id)
        for item in ordered:
            if len(selected_ids) >= top_k:
                break
            selected_ids.add(item.retrieval_unit_id)

        selected = [
            item for item in ordered if item.retrieval_unit_id in selected_ids
        ][:top_k]
        for rank, item in enumerate(selected, start=1):
            item.final_rank = rank
        return selected

    def _hydrate(
        self, snapshot_id: str, retrieval_unit_ids: Sequence[str]
    ) -> tuple[dict[str, Mapping[str, Any]], ChannelStatus]:
        if not retrieval_unit_ids:
            return {}, ChannelStatus(
                channel="evidence_package",
                source_role="all",
                status="empty",
            )
        try:
            rows = self.executor.fetch_all(
                EVIDENCE_PACKAGE_SQL, (snapshot_id, list(retrieval_unit_ids))
            )
            result = {str(row["retrieval_unit_id"]): row for row in rows}
            return result, ChannelStatus(
                channel="evidence_package",
                source_role="all",
                status="ok" if result else "empty",
                result_count=len(result),
            )
        except Exception as exc:
            return {}, ChannelStatus(
                channel="evidence_package",
                source_role="all",
                status="failed",
                error_type=_safe_error_type(exc),
            )

    def search(
        self,
        *,
        query: str,
        corpus_snapshot_id: str,
        routing_mode: RoutingMode | str | None = None,
        query_embedding: Sequence[float] | None = None,
        lexical_query: str | None = None,
    ) -> HybridSearchResult:
        clean_query = " ".join(query.split())
        if not clean_query:
            raise ValueError("query must not be empty")
        if not corpus_snapshot_id.strip():
            raise ValueError("corpus_snapshot_id must not be empty")
        clean_lexical_query = " ".join((lexical_query or clean_query).split())
        if not clean_lexical_query:
            raise ValueError("lexical_query must not be empty")
        if routing_mode is None or routing_mode == "auto":
            resolved_route = infer_routing_mode(clean_query)
        else:
            try:
                resolved_route = RoutingMode(str(routing_mode).replace("-", "_"))
            except ValueError as exc:
                raise ValueError(f"unsupported routing mode: {routing_mode}") from exc

        vector_literal = (
            _vector_literal(query_embedding) if query_embedding is not None else None
        )
        primary_role = (
            GUIDELINE_ROLE
            if resolved_route is RoutingMode.GUIDELINE_FIRST
            else SMPC_ROLE
        )
        secondary_role = SMPC_ROLE if primary_role == GUIDELINE_ROLE else GUIDELINE_ROLE
        roles = (
            (GUIDELINE_ROLE, SMPC_ROLE)
            if resolved_route is RoutingMode.DUAL_SOURCE
            else (primary_role, secondary_role)
        )

        rankings: dict[str, list[ChannelHit]] = {}
        statuses: list[ChannelStatus] = []
        first_rankings, first_statuses = self._query_role(
            snapshot_id=corpus_snapshot_id,
            query=clean_query,
            lexical_query=clean_lexical_query,
            source_role=roles[0],
            vector_literal=vector_literal,
        )
        rankings.update(first_rankings)
        statuses.extend(first_statuses)

        first_fused = reciprocal_rank_fusion(first_rankings, k=self.config.rrf_k)
        should_query_second = (
            resolved_route is RoutingMode.DUAL_SOURCE
            or len(first_fused) < self.config.top_k
        )
        if should_query_second:
            second_rankings, second_statuses = self._query_role(
                snapshot_id=corpus_snapshot_id,
                query=clean_query,
                lexical_query=clean_lexical_query,
                source_role=roles[1],
                vector_literal=vector_literal,
            )
            rankings.update(second_rankings)
            statuses.extend(second_statuses)
        else:
            statuses.extend(
                ChannelStatus(
                    channel=channel.value,
                    source_role=roles[1],
                    status="skipped",
                    detail="primary_source_filled_top_k",
                )
                for channel in self.config.enabled_channels
            )

        attempted = [item for item in statuses if item.status != "skipped"]
        if attempted and all(item.status == "failed" for item in attempted):
            raise RetrievalFailure("all attempted retrieval channels failed", statuses)

        fused = self._select_top_k(
            self._sort_for_route(
                reciprocal_rank_fusion(rankings, k=self.config.rrf_k),
                resolved_route,
            ),
            resolved_route,
            self.config.top_k,
        )
        direct_ids = [item.retrieval_unit_id for item in fused]

        relation_rows: list[Mapping[str, Any]] = []
        if self.config.expand_relations and direct_ids:
            seed_ids = direct_ids[: self.config.relation_seed_limit]
            try:
                relation_rows = self.executor.fetch_all(
                    EXPAND_RELATIONS_SQL,
                    (corpus_snapshot_id, seed_ids, self.config.relation_limit),
                )
                statuses.append(
                    ChannelStatus(
                        channel="relation_expansion",
                        source_role="all",
                        status="ok" if relation_rows else "empty",
                        result_count=len(relation_rows),
                    )
                )
            except Exception as exc:
                statuses.append(
                    ChannelStatus(
                        channel="relation_expansion",
                        source_role="all",
                        status="failed",
                        error_type=_safe_error_type(exc),
                    )
                )

        relation_types: dict[str, set[str]] = defaultdict(set)
        relation_seeds: dict[str, set[str]] = defaultdict(set)
        related_ids: list[str] = []
        seen_related = set(direct_ids)
        for row in relation_rows:
            relation_type = str(row.get("relation_type") or "")
            if relation_type not in ALLOWED_RELATION_TYPES:
                continue
            target_id = str(row.get("retrieval_unit_id") or "")
            seed_id = str(row.get("seed_retrieval_unit_id") or "")
            if not target_id or not seed_id:
                continue
            relation_types[target_id].add(relation_type)
            relation_seeds[target_id].add(seed_id)
            if target_id not in seen_related:
                seen_related.add(target_id)
                related_ids.append(target_id)

        requested_package_ids = [*direct_ids, *related_ids]
        hydrated, package_status = self._hydrate(
            corpus_snapshot_id, requested_package_ids
        )
        statuses.append(package_status)
        if package_status.status == "failed":
            raise RetrievalFailure("eligible evidence-package lookup failed", statuses)

        direct_candidates: list[EvidenceCandidate] = []
        for item in fused:
            row = hydrated.get(item.retrieval_unit_id)
            if row is None:
                continue
            direct_candidates.append(
                EvidenceCandidate(
                    retrieval_unit_id=item.retrieval_unit_id,
                    final_rank=len(direct_candidates) + 1,
                    evidence_role="direct",
                    rrf_score=item.rrf_score,
                    channel_ranks=item.channel_ranks,
                    raw_scores=item.raw_scores,
                    relation_types=(),
                    seed_retrieval_unit_ids=(),
                    metadata=_public_metadata(row),
                )
            )

        linked_context: list[EvidenceCandidate] = []
        for target_id in related_ids:
            row = hydrated.get(target_id)
            if row is None:
                continue
            linked_context.append(
                EvidenceCandidate(
                    retrieval_unit_id=target_id,
                    final_rank=len(direct_candidates) + len(linked_context) + 1,
                    evidence_role="linked_context",
                    rrf_score=None,
                    channel_ranks={},
                    raw_scores={},
                    relation_types=tuple(sorted(relation_types[target_id])),
                    seed_retrieval_unit_ids=tuple(sorted(relation_seeds[target_id])),
                    metadata=_public_metadata(row),
                )
            )

        allowlist = tuple(
            item.retrieval_unit_id for item in (*direct_candidates, *linked_context)
        )
        routing_notes: list[str] = []
        package_rows = [item.metadata for item in (*direct_candidates, *linked_context)]
        if any(row.get("source_status") == "consultation_draft" for row in package_rows):
            routing_notes.append("consultation_draft_present_not_treated_as_final")
        roles_present = {row.get("source_role") for row in package_rows}
        if GUIDELINE_ROLE in roles_present and SMPC_ROLE in roles_present:
            routing_notes.append("guideline_and_smpc_evidence_not_silently_reconciled")
        if resolved_route is RoutingMode.DUAL_SOURCE:
            routing_notes.append("dual_source_conflict_assessment_required")

        failed_search_channel = any(
            item.status == "failed"
            and item.channel in {channel.value for channel in RetrievalChannel}
            for item in statuses
        )
        if allowlist:
            retrieval_outcome = "evidence_found"
        elif failed_search_channel:
            # Absence cannot be claimed for the snapshot when a configured
            # channel failed before the complete fallback finished.
            retrieval_outcome = "retrieval_failure"
            routing_notes.append("no_evidence_claim_suppressed_after_channel_failure")
        else:
            retrieval_outcome = "no_evidence_in_snapshot"

        return HybridSearchResult(
            corpus_snapshot_id=corpus_snapshot_id,
            query_sha256=hashlib.sha256(clean_query.encode("utf-8")).hexdigest(),
            routing_mode=resolved_route,
            retrieval_outcome=retrieval_outcome,
            direct_candidates=tuple(direct_candidates),
            linked_context=tuple(linked_context),
            evidence_allowlist=allowlist,
            channel_status=tuple(statuses),
            routing_notes=tuple(routing_notes),
        )


__all__ = [
    "ALLOWED_RELATION_TYPES",
    "ChannelHit",
    "ChannelStatus",
    "EvidenceCandidate",
    "HybridRetrievalConfig",
    "HybridRetriever",
    "HybridSearchResult",
    "PsycopgQueryExecutor",
    "QueryExecutor",
    "RetrievalChannel",
    "RetrievalFailure",
    "RoutingMode",
    "infer_routing_mode",
    "reciprocal_rank_fusion",
]

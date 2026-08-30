"""Separate deterministic provenance validators for RAG and live Web arms.

These validators establish allowlist/source integrity only.  They do not claim
clinical correctness, entailment or applicability; those remain human-rating
outcomes in the prespecified protocol.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

from .evidence_contract import EvidencePackage, render_backend_citation
from .rag_core import RagHit
from .study_responses import StudyStructuredAnswer


class ValidatedStudyClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    claim_text: str
    claim_type: str
    model_support_status: str
    validator_status: Literal["accepted", "downgraded", "rejected"]
    source_refs: tuple[str, ...]
    validated_source_refs: tuple[str, ...]
    issue_codes: tuple[str, ...]


class ValidatedStudyRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recommendation_text: str
    validator_status: Literal["accepted", "rejected"]
    source_refs: tuple[str, ...]
    validated_source_refs: tuple[str, ...]
    issue_codes: tuple[str, ...]


class ValidatedStudyAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer_status: Literal["supported", "partially_supported", "no_validated_evidence"]
    answer_text: str
    claims: tuple[ValidatedStudyClaim, ...]
    recommendations: tuple[ValidatedStudyRecommendation, ...]
    limitations: tuple[str, ...]
    abstention_reason: str | None
    validator_status: Literal["accepted", "downgraded", "rejected"]
    issue_codes: tuple[str, ...]
    rendered_sources: tuple[dict[str, Any], ...]
    provenance_validation_only: Literal[True] = True


def _normalize_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return value.strip()
    scheme = parts.scheme.casefold()
    host = parts.netloc.casefold()
    path = parts.path.rstrip("/") or "/"
    # Web Search annotations can append OpenAI/marketing tracking parameters to
    # the same source URL.  Remove only known tracking keys; retain semantic
    # query parameters because they can identify a distinct source document.
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in {"gclid", "fbclid", "msclkid"}
        ],
        doseq=True,
    )
    return urlunsplit((scheme, host, path, query, ""))


def _numeric_guard(text: str, evidence_text: str) -> tuple[str, ...]:
    issues: set[str] = set()
    folded = evidence_text.casefold()
    for number in re.findall(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])", text):
        variants = {number, number.replace(",", "."), number.replace(".", ",")}
        if not any(value in folded for value in variants):
            issues.add("numeric_value_not_in_cited_source")
    return tuple(sorted(issues))


def validate_rag_answer(
    answer: StudyStructuredAnswer,
    *,
    package: EvidencePackage,
    retrieval_hits: Sequence[RagHit],
) -> ValidatedStudyAnswer:
    """Validate only against the finite local allowlist and backend metadata."""

    allowlist = set(package.allowlist_ids)
    hit_by_id = {hit.evidence_id: hit for hit in retrieval_hits}
    global_issues: set[str] = set()
    rendered: dict[str, dict[str, Any]] = {}
    for evidence_id in package.allowlist_ids:
        row = package.evidence_by_id[evidence_id]
        if not row.is_eligible:
            global_issues.add("policy_ineligible_evidence_in_package")
        if row.exclusion_reason == "hcc_historical_change_table":
            global_issues.add("hcc_history_leakage")
        try:
            rendered[evidence_id] = render_backend_citation(row).model_dump(mode="json")
        except ValueError:
            global_issues.add("invalid_backend_source_locator")

    for hit in retrieval_hits:
        if hit.evidence_role == "bridge_context":
            if any(
                relation != "smpc_product_substance_to_guideline_mention"
                for relation in hit.relation_types
            ):
                global_issues.add("invalid_or_reverse_drug_bridge_relation")
            if not hit.seed_evidence_ids:
                global_issues.add("bridge_context_missing_smpc_seed")

    claims: list[ValidatedStudyClaim] = []
    valid_claim_count = 0
    for claim in answer.claims:
        issues: set[str] = set()
        unknown = [ref for ref in claim.source_refs if ref not in allowlist]
        if unknown:
            issues.add("unknown_or_not_allowlisted_evidence_id")
        if not claim.source_refs and claim.claim_type != "uncertainty":
            issues.add("clinical_claim_missing_source")
        valid_refs = tuple(ref for ref in claim.source_refs if ref in allowlist)
        if valid_refs:
            combined = " ".join(
                package.evidence_by_id[ref].exact_source_text for ref in valid_refs
            )
            issues.update(_numeric_guard(claim.claim_text, combined))
            roles = {
                hit_by_id[ref].evidence_role for ref in valid_refs if ref in hit_by_id
            }
            if roles and roles <= {"linked_context", "bridge_context"}:
                issues.add("relation_context_only_requires_human_support_review")
        status: Literal["accepted", "downgraded", "rejected"]
        hard = {
            "unknown_or_not_allowlisted_evidence_id",
            "clinical_claim_missing_source",
            "numeric_value_not_in_cited_source",
        }
        if issues.intersection(hard):
            status = "rejected"
        elif issues - {"relation_context_only_requires_human_support_review"}:
            status = "downgraded"
            valid_claim_count += 1
        else:
            status = "accepted"
            valid_claim_count += 1
        claims.append(
            ValidatedStudyClaim(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                claim_type=claim.claim_type,
                model_support_status=claim.support_status,
                validator_status=status,
                source_refs=tuple(claim.source_refs),
                validated_source_refs=valid_refs if status != "rejected" else (),
                issue_codes=tuple(sorted(issues)),
            )
        )
        global_issues.update(issues)

    recommendations: list[ValidatedStudyRecommendation] = []
    valid_recommendation_count = 0
    for recommendation in answer.recommendations:
        issues: set[str] = set()
        unknown = [ref for ref in recommendation.source_refs if ref not in allowlist]
        if unknown:
            issues.add("unknown_or_not_allowlisted_evidence_id")
        if not recommendation.source_refs:
            issues.add("recommendation_missing_source")
        valid_refs = tuple(
            ref for ref in recommendation.source_refs if ref in allowlist
        )
        if valid_refs:
            combined = " ".join(
                package.evidence_by_id[ref].exact_source_text for ref in valid_refs
            )
            issues.update(_numeric_guard(recommendation.recommendation_text, combined))
        status: Literal["accepted", "rejected"] = "rejected" if issues else "accepted"
        if status == "accepted":
            valid_recommendation_count += 1
        recommendations.append(
            ValidatedStudyRecommendation(
                recommendation_text=recommendation.recommendation_text,
                validator_status=status,
                source_refs=tuple(recommendation.source_refs),
                validated_source_refs=valid_refs if status == "accepted" else (),
                issue_codes=tuple(sorted(issues)),
            )
        )
        global_issues.update(issues)

    if answer.answer_status == "no_validated_evidence":
        if answer.claims or answer.recommendations:
            global_issues.add("abstention_contains_structured_clinical_assertions")
        if not answer.abstention_reason:
            global_issues.add("abstention_reason_missing")
    elif not answer.claims:
        global_issues.add("non_abstaining_answer_has_no_claims")

    hard_global = {
        "policy_ineligible_evidence_in_package",
        "hcc_history_leakage",
        "invalid_or_reverse_drug_bridge_relation",
        "invalid_backend_source_locator",
        "abstention_contains_structured_clinical_assertions",
        "non_abstaining_answer_has_no_claims",
    }
    if global_issues.intersection(hard_global):
        validator_status: Literal["accepted", "downgraded", "rejected"] = "rejected"
    elif any(row.validator_status == "rejected" for row in claims) or any(
        row.validator_status == "rejected" for row in recommendations
    ):
        validator_status = (
            "downgraded"
            if valid_claim_count + valid_recommendation_count > 0
            else "rejected"
        )
    elif global_issues - {"relation_context_only_requires_human_support_review"}:
        validator_status = "downgraded"
    else:
        validator_status = "accepted"

    if validator_status == "rejected":
        status_out = "no_validated_evidence"
        answer_text = ""
    elif (
        validator_status == "downgraded"
        or answer.answer_status == "partially_supported"
    ):
        status_out = "partially_supported"
        answer_text = answer.answer_text
    else:
        status_out = answer.answer_status
        answer_text = answer.answer_text
    used = {ref for claim in claims for ref in claim.validated_source_refs} | {
        ref
        for recommendation in recommendations
        for ref in recommendation.validated_source_refs
    }
    return ValidatedStudyAnswer(
        answer_status=status_out,
        answer_text=answer_text,
        claims=tuple(claims),
        recommendations=tuple(recommendations),
        limitations=tuple(answer.limitations),
        abstention_reason=answer.abstention_reason,
        validator_status=validator_status,
        issue_codes=tuple(sorted(global_issues)),
        rendered_sources=tuple(
            rendered[ref] for ref in package.allowlist_ids if ref in used
        ),
    )


def validate_web_answer(
    answer: StudyStructuredAnswer,
    *,
    consulted_sources: Sequence[Mapping[str, Any]],
    cited_sources: Sequence[Mapping[str, Any]],
) -> ValidatedStudyAnswer:
    """Validate URLs only against sources actually returned by this Web call."""

    consulted = {
        _normalize_url(str(row.get("url"))): dict(row)
        for row in consulted_sources
        if row.get("url")
    }
    annotated = {
        _normalize_url(str(row.get("url"))) for row in cited_sources if row.get("url")
    }
    global_issues: set[str] = set()
    claims: list[ValidatedStudyClaim] = []
    valid_claim_count = 0
    for claim in answer.claims:
        issues: set[str] = set()
        normalized_refs = tuple(_normalize_url(ref) for ref in claim.source_refs)
        unknown = [ref for ref in normalized_refs if ref not in consulted]
        if unknown:
            issues.add("web_url_not_returned_by_current_search")
        if not normalized_refs and claim.claim_type != "uncertainty":
            issues.add("clinical_claim_missing_web_source")
        if normalized_refs and any(ref not in annotated for ref in normalized_refs):
            issues.add("source_ref_missing_url_citation_annotation")
        valid_refs = tuple(ref for ref in normalized_refs if ref in consulted)
        status: Literal["accepted", "downgraded", "rejected"]
        if issues.intersection(
            {
                "web_url_not_returned_by_current_search",
                "clinical_claim_missing_web_source",
            }
        ):
            status = "rejected"
        elif issues - {"source_ref_missing_url_citation_annotation"}:
            status = "downgraded"
            valid_claim_count += 1
        else:
            status = "accepted"
            valid_claim_count += 1
        claims.append(
            ValidatedStudyClaim(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                claim_type=claim.claim_type,
                model_support_status=claim.support_status,
                validator_status=status,
                source_refs=tuple(claim.source_refs),
                validated_source_refs=valid_refs if status != "rejected" else (),
                issue_codes=tuple(sorted(issues)),
            )
        )
        global_issues.update(issues)

    recommendations: list[ValidatedStudyRecommendation] = []
    valid_recommendations = 0
    for recommendation in answer.recommendations:
        normalized_refs = tuple(
            _normalize_url(ref) for ref in recommendation.source_refs
        )
        issues: set[str] = set()
        if not normalized_refs:
            issues.add("recommendation_missing_web_source")
        if any(ref not in consulted for ref in normalized_refs):
            issues.add("web_url_not_returned_by_current_search")
        if normalized_refs and any(ref not in annotated for ref in normalized_refs):
            issues.add("source_ref_missing_url_citation_annotation")
        valid_refs = tuple(ref for ref in normalized_refs if ref in consulted)
        hard_recommendation_issues = {
            "recommendation_missing_web_source",
            "web_url_not_returned_by_current_search",
        }
        status: Literal["accepted", "rejected"] = (
            "rejected" if issues.intersection(hard_recommendation_issues) else "accepted"
        )
        if status == "accepted":
            valid_recommendations += 1
        recommendations.append(
            ValidatedStudyRecommendation(
                recommendation_text=recommendation.recommendation_text,
                validator_status=status,
                source_refs=tuple(recommendation.source_refs),
                validated_source_refs=valid_refs if status == "accepted" else (),
                issue_codes=tuple(sorted(issues)),
            )
        )
        global_issues.update(issues)

    if answer.answer_status == "no_validated_evidence":
        if answer.claims or answer.recommendations:
            global_issues.add("abstention_contains_structured_clinical_assertions")
        if not answer.abstention_reason:
            global_issues.add("abstention_reason_missing")
    elif not answer.claims:
        global_issues.add("non_abstaining_answer_has_no_claims")
    if not consulted and answer.answer_status != "no_validated_evidence":
        global_issues.add("web_answer_without_returned_search_sources")

    hard_global = {
        "abstention_contains_structured_clinical_assertions",
        "non_abstaining_answer_has_no_claims",
        "web_answer_without_returned_search_sources",
    }
    if global_issues.intersection(hard_global):
        validator_status: Literal["accepted", "downgraded", "rejected"] = "rejected"
    elif any(row.validator_status == "rejected" for row in claims) or any(
        row.validator_status == "rejected" for row in recommendations
    ):
        validator_status = (
            "downgraded"
            if valid_claim_count + valid_recommendations > 0
            else "rejected"
        )
    elif global_issues - {"source_ref_missing_url_citation_annotation"}:
        validator_status = "downgraded"
    else:
        validator_status = "accepted"

    if validator_status == "rejected":
        status_out = "no_validated_evidence"
        answer_text = ""
    elif (
        validator_status == "downgraded"
        or answer.answer_status == "partially_supported"
    ):
        status_out = "partially_supported"
        answer_text = answer.answer_text
    else:
        status_out = answer.answer_status
        answer_text = answer.answer_text
    used = {ref for claim in claims for ref in claim.validated_source_refs} | {
        ref
        for recommendation in recommendations
        for ref in recommendation.validated_source_refs
    }
    rendered = tuple(consulted[ref] for ref in sorted(used))
    return ValidatedStudyAnswer(
        answer_status=status_out,
        answer_text=answer_text,
        claims=tuple(claims),
        recommendations=tuple(recommendations),
        limitations=tuple(answer.limitations),
        abstention_reason=answer.abstention_reason,
        validator_status=validator_status,
        issue_codes=tuple(sorted(global_issues)),
        rendered_sources=rendered,
    )


__all__ = [
    "ValidatedStudyAnswer",
    "ValidatedStudyClaim",
    "ValidatedStudyRecommendation",
    "validate_rag_answer",
    "validate_web_answer",
]

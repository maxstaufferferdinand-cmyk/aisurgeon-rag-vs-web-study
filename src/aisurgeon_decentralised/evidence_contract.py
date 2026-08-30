"""Fail-closed evidence-package and claim-validation contracts.

The module is deliberately independent of a model provider and of PostgreSQL.
Callers populate :class:`EvidenceRecord` instances exclusively from the sealed
corpus snapshot (normally through ``eligible_retrieval_units``).  Source labels
and links are rendered here from backend-owned metadata; they are never trusted
when supplied by a language model.

These validators are technical safeguards for a research prototype.  They are
not a substitute for clinical entailment or applicability review.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PublicSupportLabel(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    NO_VALIDATED_EVIDENCE = "no_validated_evidence"


class EntailmentStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"


class RetrievalOutcome(StrEnum):
    EVIDENCE_FOUND = "evidence_found"
    RETRIEVAL_FAILURE = "retrieval_failure"
    NO_EVIDENCE_IN_SNAPSHOT = "no_evidence_in_snapshot"


class ConflictStatus(StrEnum):
    NONE = "none"
    GUIDELINE_VS_SMPC = "guideline_vs_smpc"
    WITHIN_GUIDELINE = "within_guideline"
    VERSION_CONFLICT = "version_conflict"


class ApplicabilityStatus(StrEnum):
    APPLICABLE = "applicable"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"


class ValidatorStatus(StrEnum):
    ACCEPTED = "accepted"
    DOWNGRADED = "downgraded"
    REJECTED = "rejected"


class EvidenceRole(StrEnum):
    DIRECT = "direct"
    LINKED_CONTEXT = "linked_context"


class IssueAction(StrEnum):
    REJECT = "reject"
    DOWNGRADE = "downgrade"
    NOTE = "note"


class EvidenceContractError(ValueError):
    """Base exception for a package that cannot be built safely."""

    code = "evidence_contract_error"


class UnknownEvidenceError(EvidenceContractError):
    code = "unknown_evidence_id"


class ExcludedEvidenceError(EvidenceContractError):
    code = "excluded_evidence_id"


class SnapshotMismatchError(EvidenceContractError):
    code = "evidence_snapshot_mismatch"


class MissingSourceLocatorError(EvidenceContractError):
    code = "missing_source_location"


class EvidenceRecord(BaseModel):
    """Backend-owned evidence metadata for one retrieval unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    corpus_snapshot_id: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    version_label: str | None = None
    source_status: str = Field(min_length=1)
    source_role: str = Field(min_length=1)
    source_authority: str = Field(min_length=1)
    document_component: str = Field(min_length=1)
    source_file_name: str = Field(min_length=1)
    source_link: str | None = None
    exact_source_text: str = Field(min_length=1)
    pdf_pages_1based: tuple[int, ...] = ()
    printed_page_label: str | None = None
    eligibility_status: str = "eligible"
    retrieval_eligible: bool = True
    answer_eligible: bool = True
    excluded_by_policy: bool = False
    exclusion_reason: str | None = None
    evidence_role: EvidenceRole = EvidenceRole.DIRECT
    dose_value: str | None = None
    dose_unit: str | None = None
    frequency: str | None = None
    route: str | None = None
    population: str | None = None
    negated: bool | None = None

    @field_validator("pdf_pages_1based")
    @classmethod
    def normalize_pages(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(page < 1 for page in value):
            raise ValueError("PDF page numbers must be positive")
        return tuple(sorted(set(value)))

    @property
    def is_eligible(self) -> bool:
        return (
            self.eligibility_status == "eligible"
            and self.retrieval_eligible
            and self.answer_eligible
            and not self.excluded_by_policy
            and self.exclusion_reason != "hcc_historical_change_table"
        )

    @property
    def has_source_locator(self) -> bool:
        return bool(
            self.source_version_id
            and self.document_name
            and self.source_file_name
            and self.source_link
            and self.pdf_pages_1based
        )


class EvidencePackage(BaseModel):
    """Immutable allowlist and its backend-owned evidence rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_package_id: str = Field(min_length=1)
    corpus_snapshot_id: str = Field(min_length=1)
    retrieval_run_id: str | None = None
    created_at: datetime
    allowlist_ids: tuple[str, ...]
    evidence_by_id: dict[str, EvidenceRecord]
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("allowlist_ids")
    @classmethod
    def unique_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evidence allowlist contains duplicate IDs")
        return value

    @model_validator(mode="after")
    def validate_package(self) -> EvidencePackage:
        if set(self.allowlist_ids) != set(self.evidence_by_id):
            raise ValueError("allowlist and evidence rows differ")
        for evidence_id, evidence in self.evidence_by_id.items():
            if evidence.evidence_id != evidence_id:
                raise ValueError("evidence dictionary key does not match evidence_id")
            if evidence.corpus_snapshot_id != self.corpus_snapshot_id:
                raise ValueError("evidence belongs to another corpus snapshot")
            if not evidence.is_eligible:
                raise ValueError("ineligible evidence cannot enter an evidence package")
        _, expected_digest = _package_material(
            self.corpus_snapshot_id,
            [self.evidence_by_id[evidence_id] for evidence_id in self.allowlist_ids],
        )
        if self.package_sha256 != expected_digest:
            raise ValueError("evidence package hash does not match its contents")
        return self

    def contains(self, evidence_id: str) -> bool:
        return evidence_id in self.evidence_by_id


class BackendCitation(BaseModel):
    """Citation fields rendered only from backend snapshot metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    document_name: str
    version_label: str | None
    source_status: str
    source_authority: str
    source_role: str
    document_component: str
    pdf_pages_1based: tuple[int, ...]
    printed_page_label: str | None
    link: str
    label: str


def _citation_link(base: str, first_page: int) -> str:
    separator = "&" if "#" in base else "#"
    return f"{base}{separator}page={first_page}"


def render_backend_citation(evidence: EvidenceRecord) -> BackendCitation:
    """Render a citation without accepting model-supplied source metadata."""

    if not evidence.is_eligible:
        raise ExcludedEvidenceError(
            f"evidence is not eligible for citation: {evidence.evidence_id}"
        )
    if not evidence.has_source_locator:
        raise MissingSourceLocatorError(
            f"evidence has no complete source locator: {evidence.evidence_id}"
        )
    pages = ", ".join(str(page) for page in evidence.pdf_pages_1based)
    version = f" — {evidence.version_label}" if evidence.version_label else ""
    printed = (
        f" (gedruckte Seite {evidence.printed_page_label})"
        if evidence.printed_page_label
        else ""
    )
    status = (
        f" [{evidence.source_status}]"
        if evidence.source_status != "final"
        else ""
    )
    label = f"{evidence.document_name}{version} — PDF-S. {pages}{printed}{status}"
    return BackendCitation(
        evidence_id=evidence.evidence_id,
        document_name=evidence.document_name,
        version_label=evidence.version_label,
        source_status=evidence.source_status,
        source_authority=evidence.source_authority,
        source_role=evidence.source_role,
        document_component=evidence.document_component,
        pdf_pages_1based=evidence.pdf_pages_1based,
        printed_page_label=evidence.printed_page_label,
        link=_citation_link(str(evidence.source_link), evidence.pdf_pages_1based[0]),
        label=label,
    )


def _package_material(
    snapshot_id: str, evidence: Sequence[EvidenceRecord]
) -> tuple[str, str]:
    material = {
        "corpus_snapshot_id": snapshot_id,
        "evidence": [
            {
                "evidence_id": row.evidence_id,
                "source_version_id": row.source_version_id,
                "text_sha256": hashlib.sha256(
                    row.exact_source_text.encode("utf-8")
                ).hexdigest(),
                "pdf_pages_1based": row.pdf_pages_1based,
            }
            for row in evidence
        ],
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return "evidence-package-" + digest[:24], digest


def build_evidence_package(
    *,
    corpus_snapshot_id: str,
    evidence_ids: Sequence[str],
    evidence_catalog: Mapping[str, EvidenceRecord],
    retrieval_run_id: str | None = None,
    evidence_package_id: str | None = None,
    created_at: datetime | None = None,
) -> EvidencePackage:
    """Build an allowlist, rejecting unknown, foreign, or excluded evidence."""

    ordered_ids = tuple(dict.fromkeys(evidence_ids))
    selected: list[EvidenceRecord] = []
    for evidence_id in ordered_ids:
        evidence = evidence_catalog.get(evidence_id)
        if evidence is None:
            raise UnknownEvidenceError(f"unknown evidence ID: {evidence_id}")
        if evidence.corpus_snapshot_id != corpus_snapshot_id:
            raise SnapshotMismatchError(
                f"evidence {evidence_id} belongs to another corpus snapshot"
            )
        if not evidence.is_eligible:
            raise ExcludedEvidenceError(
                f"evidence {evidence_id} is excluded from normal evidence packages"
            )
        selected.append(evidence)
    derived_id, digest = _package_material(corpus_snapshot_id, selected)
    return EvidencePackage(
        evidence_package_id=evidence_package_id or derived_id,
        corpus_snapshot_id=corpus_snapshot_id,
        retrieval_run_id=retrieval_run_id,
        created_at=created_at or datetime.now(UTC),
        allowlist_ids=ordered_ids,
        evidence_by_id={row.evidence_id: row for row in selected},
        package_sha256=digest,
    )


class ClaimDraft(BaseModel):
    """Model/provider-neutral claim submitted to deterministic validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer_claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    proposed_public_label: PublicSupportLabel | None = None
    entailment_status: EntailmentStatus
    retrieval_outcome: RetrievalOutcome
    retrieval_fallback_complete: bool
    conflict_status: ConflictStatus = ConflictStatus.NONE
    applicability_status: ApplicabilityStatus = ApplicabilityStatus.APPLICABLE
    expected_source_status: str | None = None
    dose_value: str | None = None
    dose_unit: str | None = None
    frequency: str | None = None
    route: str | None = None
    population: str | None = None
    negated: bool | None = None

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    action: IssueAction
    field: str | None = None
    evidence_id: str | None = None
    detail: str | None = None


class ValidatedClaim(BaseModel):
    """Result safe for persistence; rejected claims are not public output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer_claim_id: str
    corpus_snapshot_id: str
    evidence_package_id: str
    claim_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_support_label: PublicSupportLabel | None
    entailment_status: EntailmentStatus
    retrieval_outcome: RetrievalOutcome
    conflict_status: ConflictStatus
    applicability_status: ApplicabilityStatus
    validator_status: ValidatorStatus
    validated_evidence_ids: tuple[str, ...]
    citations: tuple[BackendCitation, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def publishable(self) -> bool:
        return (
            self.validator_status != ValidatorStatus.REJECTED
            and self.public_support_label is not None
        )


def _plain_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("‐", "-").replace("‑", "-").replace("–", "-")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .,:;()[]")


def _dose_value(value: str) -> str:
    plain = _plain_text(value).replace(" ", "")
    try:
        return format(Decimal(plain.replace(",", ".")).normalize(), "f")
    except InvalidOperation:
        return plain


def _dose_unit(value: str) -> str:
    return (
        _plain_text(value)
        .replace("μ", "u")
        .replace("µ", "u")
        .replace(" ", "")
    )


_ROUTE_ALIASES = {
    "i.v.": "intravenous",
    "iv": "intravenous",
    "intravenös": "intravenous",
    "intravenöse gabe": "intravenous",
    "intravenöse infusion": "intravenous",
    "p.o.": "oral",
    "per os": "oral",
    "oral": "oral",
    "s.c.": "subcutaneous",
    "subkutan": "subcutaneous",
    "subkutane injektion": "subcutaneous",
}


def _route(value: str) -> str:
    plain = _plain_text(value)
    direct = _ROUTE_ALIASES.get(plain)
    if direct:
        return direct
    abbreviation = plain.replace(".", "").replace(" ", "")
    return {
        "iv": "intravenous",
        "po": "oral",
        "sc": "subcutaneous",
    }.get(abbreviation, plain)


def detect_negation(value: str) -> bool:
    """Conservative lexical polarity signal used only as a technical guard."""

    plain = _plain_text(value)
    return bool(
        re.search(
            r"\b(?:nicht|kein(?:e|en|er|es)?|ohne|kontraindiziert|"
            r"unterbleiben|abzusetzen|abbrechen)\b",
            plain,
        )
    )


def _field_issue(
    *,
    field: str,
    claimed: str | None,
    evidence: Sequence[EvidenceRecord],
    normalizer: Any,
) -> ValidationIssue | None:
    if claimed is None:
        return None
    available = [getattr(row, field) for row in evidence if getattr(row, field) is not None]
    if not available:
        return ValidationIssue(
            code=f"{field}_not_present_in_evidence",
            action=IssueAction.DOWNGRADE,
            field=field,
        )
    expected = normalizer(claimed)
    if any(normalizer(value) == expected for value in available):
        return None
    return ValidationIssue(
        code=f"{field}_mismatch",
        action=IssueAction.REJECT,
        field=field,
    )


def _resolve_evidence(
    claim: ClaimDraft,
    package: EvidencePackage,
    evidence_catalog: Mapping[str, EvidenceRecord],
) -> tuple[list[EvidenceRecord], list[ValidationIssue]]:
    valid: list[EvidenceRecord] = []
    issues: list[ValidationIssue] = []
    for evidence_id in claim.evidence_ids:
        evidence = evidence_catalog.get(evidence_id)
        if evidence is None:
            issues.append(
                ValidationIssue(
                    code="unknown_evidence_id",
                    action=IssueAction.REJECT,
                    evidence_id=evidence_id,
                )
            )
            continue
        if not evidence.is_eligible:
            issues.append(
                ValidationIssue(
                    code="excluded_evidence_id",
                    action=IssueAction.REJECT,
                    evidence_id=evidence_id,
                )
            )
            continue
        if evidence.corpus_snapshot_id != package.corpus_snapshot_id:
            issues.append(
                ValidationIssue(
                    code="evidence_snapshot_mismatch",
                    action=IssueAction.REJECT,
                    evidence_id=evidence_id,
                )
            )
            continue
        if evidence_id not in package.allowlist_ids:
            issues.append(
                ValidationIssue(
                    code="evidence_id_not_in_current_package",
                    action=IssueAction.REJECT,
                    evidence_id=evidence_id,
                )
            )
            continue
        packaged = package.evidence_by_id[evidence_id]
        if evidence != packaged:
            issues.append(
                ValidationIssue(
                    code="evidence_metadata_differs_from_current_package",
                    action=IssueAction.REJECT,
                    evidence_id=evidence_id,
                )
            )
            continue
        valid.append(packaged)
    return valid, issues


def validate_claim(
    claim: ClaimDraft,
    *,
    package: EvidencePackage,
    evidence_catalog: Mapping[str, EvidenceRecord],
) -> ValidatedClaim:
    """Validate a claim and derive its public label fail-closed.

    A rejected claim intentionally has ``public_support_label=None`` and must
    not be rendered.  This avoids misusing ``no_validated_evidence`` for a
    contradicted claim or for a technical retrieval failure.
    """

    evidence, issues = _resolve_evidence(claim, package, evidence_catalog)

    if claim.retrieval_outcome == RetrievalOutcome.RETRIEVAL_FAILURE:
        issues.append(
            ValidationIssue(
                code="retrieval_failure_cannot_support_public_claim",
                action=IssueAction.REJECT,
            )
        )
    if (
        claim.retrieval_outcome == RetrievalOutcome.NO_EVIDENCE_IN_SNAPSHOT
        and not claim.retrieval_fallback_complete
    ):
        issues.append(
            ValidationIssue(
                code="no_evidence_requires_complete_fallback",
                action=IssueAction.REJECT,
            )
        )
    if claim.retrieval_outcome == RetrievalOutcome.EVIDENCE_FOUND and not evidence:
        issues.append(
            ValidationIssue(
                code="no_valid_cited_evidence",
                action=IssueAction.REJECT,
            )
        )
    if (
        claim.retrieval_outcome == RetrievalOutcome.NO_EVIDENCE_IN_SNAPSHOT
        and evidence
    ):
        issues.append(
            ValidationIssue(
                code="no_evidence_outcome_has_citations",
                action=IssueAction.REJECT,
            )
        )
    if claim.retrieval_outcome == RetrievalOutcome.NO_EVIDENCE_IN_SNAPSHOT:
        structured_assertions = (
            claim.dose_value,
            claim.dose_unit,
            claim.frequency,
            claim.route,
            claim.population,
            claim.negated,
        )
        if any(value is not None for value in structured_assertions):
            issues.append(
                ValidationIssue(
                    code="no_evidence_claim_contains_structured_assertion",
                    action=IssueAction.REJECT,
                )
            )
        if claim.entailment_status != EntailmentStatus.INSUFFICIENT:
            issues.append(
                ValidationIssue(
                    code="no_evidence_requires_insufficient_entailment",
                    action=IssueAction.REJECT,
                )
            )

    for row in evidence:
        if not row.has_source_locator:
            issues.append(
                ValidationIssue(
                    code="missing_source_location",
                    action=IssueAction.REJECT,
                    evidence_id=row.evidence_id,
                )
            )
        if claim.expected_source_status and row.source_status != claim.expected_source_status:
            issues.append(
                ValidationIssue(
                    code="source_status_mismatch",
                    action=IssueAction.REJECT,
                    field="source_status",
                    evidence_id=row.evidence_id,
                    detail=(
                        f"expected {claim.expected_source_status}; backend has "
                        f"{row.source_status}"
                    ),
                )
            )

    field_rules = (
        ("dose_value", claim.dose_value, _dose_value),
        ("dose_unit", claim.dose_unit, _dose_unit),
        ("frequency", claim.frequency, _plain_text),
        ("route", claim.route, _route),
        ("population", claim.population, _plain_text),
    )
    for field, claimed, normalizer in field_rules:
        issue = _field_issue(
            field=field, claimed=claimed, evidence=evidence, normalizer=normalizer
        )
        if issue:
            issues.append(issue)

    if claim.negated is not None and evidence:
        polarities = {
            row.negated if row.negated is not None else detect_negation(row.exact_source_text)
            for row in evidence
        }
        if claim.negated not in polarities:
            issues.append(
                ValidationIssue(
                    code="negation_mismatch",
                    action=IssueAction.REJECT,
                    field="negated",
                )
            )

    hard_reject = any(issue.action == IssueAction.REJECT for issue in issues)
    must_downgrade = any(issue.action == IssueAction.DOWNGRADE for issue in issues)
    entailment = claim.entailment_status
    applicability = claim.applicability_status
    if any(issue.code == "population_mismatch" for issue in issues):
        applicability = ApplicabilityStatus.NOT_APPLICABLE
    elif any(issue.code == "population_not_present_in_evidence" for issue in issues):
        applicability = ApplicabilityStatus.UNCERTAIN

    public_label: PublicSupportLabel | None
    validator_status: ValidatorStatus
    contradiction_codes = {
        "dose_value_mismatch",
        "dose_unit_mismatch",
        "frequency_mismatch",
        "route_mismatch",
        "population_mismatch",
        "negation_mismatch",
        "source_status_mismatch",
    }
    has_explicit_contradiction = bool(
        contradiction_codes.intersection(issue.code for issue in issues)
    )
    if hard_reject or entailment == EntailmentStatus.CONTRADICTED:
        validator_status = ValidatorStatus.REJECTED
        public_label = None
        if has_explicit_contradiction:
            entailment = EntailmentStatus.CONTRADICTED
        elif entailment != EntailmentStatus.CONTRADICTED:
            entailment = EntailmentStatus.INSUFFICIENT
    elif claim.retrieval_outcome == RetrievalOutcome.NO_EVIDENCE_IN_SNAPSHOT:
        validator_status = ValidatorStatus.ACCEPTED
        public_label = PublicSupportLabel.NO_VALIDATED_EVIDENCE
        entailment = EntailmentStatus.INSUFFICIENT
    elif entailment == EntailmentStatus.INSUFFICIENT:
        validator_status = ValidatorStatus.REJECTED
        public_label = None
    elif must_downgrade or entailment == EntailmentStatus.PARTIAL:
        validator_status = ValidatorStatus.DOWNGRADED
        public_label = PublicSupportLabel.PARTIALLY_SUPPORTED
        entailment = EntailmentStatus.PARTIAL
    else:
        validator_status = ValidatorStatus.ACCEPTED
        public_label = PublicSupportLabel.SUPPORTED

    if claim.proposed_public_label != public_label:
        issues.append(
            ValidationIssue(
                code="public_label_derived_by_backend",
                action=IssueAction.NOTE,
                detail=(
                    f"proposed={claim.proposed_public_label}; derived={public_label}"
                ),
            )
        )

    citations: list[BackendCitation] = []
    if validator_status != ValidatorStatus.REJECTED:
        for row in evidence:
            citations.append(render_backend_citation(row))

    return ValidatedClaim(
        answer_claim_id=claim.answer_claim_id,
        corpus_snapshot_id=package.corpus_snapshot_id,
        evidence_package_id=package.evidence_package_id,
        claim_text_sha256=hashlib.sha256(claim.claim_text.encode("utf-8")).hexdigest(),
        public_support_label=public_label,
        entailment_status=entailment,
        retrieval_outcome=claim.retrieval_outcome,
        conflict_status=claim.conflict_status,
        applicability_status=applicability,
        validator_status=validator_status,
        validated_evidence_ids=tuple(row.evidence_id for row in evidence),
        citations=tuple(citations),
        issues=tuple(issues),
    )


def validate_claims(
    claims: Iterable[ClaimDraft],
    *,
    package: EvidencePackage,
    evidence_catalog: Mapping[str, EvidenceRecord],
) -> list[ValidatedClaim]:
    return [
        validate_claim(claim, package=package, evidence_catalog=evidence_catalog)
        for claim in claims
    ]

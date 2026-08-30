from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MentionScope = Literal[
    "CLINICAL_CONTENT",
    "REFERENCE_ONLY",
    "HISTORICAL_OR_NEGATIVE",
    "UNCLEAR_SCOPE",
]

EntityType = Literal[
    "ACTIVE_SUBSTANCE",
    "DRUG_PRODUCT",
    "BRAND_NAME",
    "DRUG_CLASS",
    "COMBINATION_REGIMEN",
    "BIOLOGIC",
    "CONTRAST_AGENT",
    "BLOOD_PRODUCT",
    "SUPPLEMENT",
    "OTHER_SUBSTANCE",
    "UNCLEAR",
]

NormalizationStatus = Literal["EXACT", "NORMALIZED", "INFERRED", "UNCLEAR"]
PriorityClass = Literal["PRIORITY_A", "PRIORITY_B", "PRIORITY_C", "MANUAL_REVIEW"]
CitationStatus = Literal["VERIFIED", "UNVERIFIED"]


class GeminiMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_page: int = Field(ge=1)
    printed_page: str | None = None
    section_path: list[str] = Field(default_factory=list)
    mention_scope: MentionScope
    raw_mention: str = Field(min_length=1)
    exact_context_quote: str = Field(min_length=1, max_length=280)
    entity_type: EntityType
    canonical_name_de: str = Field(min_length=1)
    canonical_name_en: str | None = None
    brand_name: str | None = None
    drug_class: str | None = None
    regimen_name: str | None = None
    normalization_status: NormalizationStatus
    source_explicit: bool
    clinical_context: str = Field(min_length=1)
    formal_item_reference: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    needs_manual_review: bool = False

    @field_validator("section_path")
    @classmethod
    def clean_section_path(cls, value: list[str]) -> list[str]:
        return [part.strip() for part in value if part and part.strip()]

    @field_validator(
        "raw_mention",
        "exact_context_quote",
        "canonical_name_de",
        "clinical_context",
    )
    @classmethod
    def no_blank_strings(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("blank value")
        return value


class GeminiPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_page: int = Field(ge=1)
    printed_page: str | None = None
    status: Literal[
        "NO_MEDICATION_MENTION",
        "MEDICATION_MENTION_FOUND",
        "NEEDS_REPAIR",
        "NEEDS_MANUAL_REVIEW",
    ]
    mentions: list[GeminiMention] = Field(default_factory=list)
    notes: str | None = None


class GeminiBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    batch_start_page: int = Field(ge=1)
    batch_end_page: int = Field(ge=1)
    pages: list[GeminiPageResult]


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_filename: str
    source_sha256: str
    pdf_pages: int = Field(ge=1)
    document_status: str


class DrugMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention_id: str
    extraction_run_id: str
    source_id: str
    source_filename: str
    source_sha256: str
    pdf_page: int = Field(ge=1)
    printed_page: str | None
    section_path: list[str]
    mention_scope: MentionScope
    raw_mention: str
    exact_context_quote: str
    entity_type: EntityType
    canonical_name_de: str
    canonical_name_en: str | None
    brand_name: str | None
    drug_class: str | None
    regimen_name: str | None
    normalization_status: NormalizationStatus
    source_explicit: bool
    clinical_context: str
    formal_item_reference: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    needs_manual_review: bool
    citation_verification_status: CitationStatus
    citation_verification_note: str | None = None


class CatalogEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention_id: str
    source_id: str
    source_filename: str
    pdf_page: int
    printed_page: str | None
    mention_scope: MentionScope
    exact_context_quote: str
    raw_mention: str
    clinical_context: str
    formal_item_reference: str | None
    citation_verification_status: CitationStatus


class DrugCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_drug_id: str
    extraction_run_id: str
    priority: PriorityClass
    entity_type: EntityType
    canonical_name_de: str
    canonical_name_en: str | None
    raw_mentions: list[str]
    brand_names: list[str]
    drug_classes: list[str]
    regimen_names: list[str]
    guideline_sources: list[str]
    source_page_counts: dict[str, int]
    exact_pdf_pages_by_source: dict[str, list[int]]
    mention_count_by_source: dict[str, int]
    mention_scopes: list[MentionScope]
    clinical_contexts: list[str]
    representative_quote: str
    evidence: list[CatalogEvidence]
    needs_manual_review: bool
    manual_review_reasons: list[str]
    citation_verification_status: CitationStatus

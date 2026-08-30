"""Versioned structured models for the clinical knowledge-corpus extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "knowledge-corpus-1.0.0"

CoverageStatus = Literal[
    "extracted",
    "blank",
    "front_matter",
    "table_of_contents",
    "references",
    "appendix",
    "label_or_leaflet_noncanonical",
    "unreadable_flagged",
]

RecordType = Literal[
    "document_metadata",
    "chapter_structure",
    "grading_system",
    "formal_item",
    "rationale_block",
    "guideline_reference",
    "table_figure_algorithm",
    "drug_product",
    "active_substance",
    "composition",
    "therapeutic_indication",
    "dosing_rule",
    "preparation_administration",
    "contraindication",
    "warning",
    "interaction",
    "pregnancy_lactation_fertility",
    "adverse_reaction",
    "overdose",
    "pharmacodynamics",
    "pharmacokinetics",
    "excipient",
    "incompatibility",
    "storage_handling",
    "regulatory_metadata",
]

SourceZone = Literal[
    "main_body",
    "foreword_or_preface",
    "table_of_contents",
    "summary",
    "appendix",
    "change_table",
    "historical_table",
    "references",
    "other_secondary_material",
]

CanonicalRole = Literal[
    "primary",
    "secondary_representation",
    "historical_record",
    "historical_secondary",
    "quarantined",
]


class PageAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_page_1based: int = Field(ge=1)
    printed_page_label: str | None = None
    status: CoverageStatus
    reason_de: str = Field(min_length=1)
    section_path: list[str] = Field(default_factory=list)
    relevant_record_types: list[RecordType] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)

    @field_validator("section_path", "review_flags")
    @classmethod
    def clean_list(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class AlgorithmNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    exact_visible_text: str = Field(min_length=1)
    node_type: str | None = None


class AlgorithmEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_node_id: str = Field(min_length=1)
    to_node_id: str = Field(min_length=1)
    exact_visible_label: str | None = None


class ExtractedRecord(BaseModel):
    """One source-grounded record before deterministic provenance enrichment."""

    model_config = ConfigDict(extra="forbid")

    record_type: RecordType
    source_identifier: str | None = None
    title: str | None = None
    section_path: list[str] = Field(default_factory=list)
    pdf_pages_1based: list[int] = Field(min_length=1)
    printed_page_label: str | None = None
    exact_source_text: str = Field(min_length=1)
    semantic_summary_de: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    review_flags: list[str] = Field(default_factory=list)

    # Post-extraction source-zone and retrieval governance.  These fields are
    # optional in immutable Gemini checkpoints and are deterministically filled
    # during compilation.  They keep main-text recommendations distinct from
    # appendices, change tables, summaries, and other secondary renderings.
    source_zone: SourceZone | None = None
    canonical_role: CanonicalRole | None = None
    primary_record_id: str | None = None
    primary_record_ids: list[str] = Field(default_factory=list)
    retrieval_eligible: bool | None = None
    retrieval_exclusion_reason: str | None = None
    embedding_eligible: bool | None = None
    answer_eligible: bool | None = None
    primary_search_eligible: bool | None = None
    status: str | None = None
    exclusion_reason: str | None = None
    secondary_relation_type: str | None = None
    uncertainty_reason: str | None = None

    # Source support for structured fields that are explicit in the same
    # section or on a directly adjacent page, but not repeated in the atomic
    # quote itself.
    supporting_source_text: str | None = None
    supporting_pdf_pages_1based: list[int] = Field(default_factory=list)
    structured_field_provenance: dict[str, str] = Field(default_factory=dict)

    # Raw and search-normalized audit representations are populated only after
    # extraction.  The raw exact_source_text remains untouched.
    exact_source_text_raw_sha256: str | None = None
    normalized_search_text: str | None = None
    normalized_search_text_sha256: str | None = None
    repair_provenance: dict[str, str] = Field(default_factory=dict)

    # Guideline formal items and linkage.
    document_order: int | None = Field(default=None, ge=1)
    source_item_number: str | None = None
    printed_source_item_number: str | None = None
    item_number_status: str | None = None
    item_type: Literal["recommendation", "statement", "consensus_statement", "other"] | None = None
    exact_text_de: str | None = None
    recommendation_grade: str | None = None
    evidence_level: str | None = None
    consensus_strength: str | None = None
    qualifiers: list[str] = Field(default_factory=list)
    population: str | None = None
    intervention: str | None = None
    comparator: str | None = None
    outcome: str | None = None
    setting: str | None = None
    linked_item_numbers: list[str] = Field(default_factory=list)
    explicit_linked_rationale_record_ids: list[str] = Field(default_factory=list)
    linked_reference_labels: list[str] = Field(default_factory=list)
    linked_table_figure_labels: list[str] = Field(default_factory=list)

    # Product and substance identity.
    product_name: str | None = None
    original_product_name: str | None = None
    active_substance_names: list[str] = Field(default_factory=list)
    active_substance_original_names: list[str] = Field(default_factory=list)
    strength: str | None = None
    pharmaceutical_form: str | None = None
    marketing_authorisation_holder: str | None = None
    authorisation_numbers: list[str] = Field(default_factory=list)
    revision_date: str | None = None
    component_name: str | None = None
    component_role: str | None = None
    amount: str | None = None
    amount_unit: str | None = None

    # Atomic dosing and administration fields. Strings preserve ranges and qualifiers verbatim.
    indication: str | None = None
    treatment_context: str | None = None
    dose_value: str | None = None
    dose_unit: str | None = None
    frequency: str | None = None
    route: str | None = None
    duration: str | None = None
    maximum_dose: str | None = None
    loading_dose: str | None = None
    maintenance_dose: str | None = None
    combination_partners: list[str] = Field(default_factory=list)
    renal_adjustment: str | None = None
    hepatic_adjustment: str | None = None
    age_adjustment: str | None = None
    toxicity_adjustment: str | None = None
    interruption_rule: str | None = None
    discontinuation_rule: str | None = None
    preparation_instruction: str | None = None

    # Adverse-reaction and pregnancy/pharmacology qualifiers.
    system_organ_class: str | None = None
    adverse_reaction_term: str | None = None
    frequency_category: str | None = None
    pregnancy_information: str | None = None
    lactation_information: str | None = None
    fertility_information: str | None = None

    # Tables, figures, and algorithms.
    visual_kind: Literal["table", "figure", "algorithm", "other"] | None = None
    caption: str | None = None
    table_rows: list[list[str]] = Field(default_factory=list)
    footnotes: list[str] = Field(default_factory=list)
    algorithm_nodes: list[AlgorithmNode] = Field(default_factory=list)
    algorithm_edges: list[AlgorithmEdge] = Field(default_factory=list)
    semantic_description_de: str | None = None
    local_render_reference: str | None = None

    # Retrieval and controlled linking aids, always source-explicit unless flagged.
    medication_mentions_original: list[str] = Field(default_factory=list)
    normalized_entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    indications: list[str] = Field(default_factory=list)
    populations: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)

    @field_validator(
        "section_path",
        "review_flags",
        "qualifiers",
        "linked_item_numbers",
        "linked_reference_labels",
        "linked_table_figure_labels",
        "active_substance_names",
        "active_substance_original_names",
        "authorisation_numbers",
        "combination_partners",
        "footnotes",
        "medication_mentions_original",
        "normalized_entities",
        "keywords",
        "indications",
        "populations",
        "routes",
        "primary_record_ids",
    )
    @classmethod
    def clean_string_lists(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            cleaned = item.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        return result

    @field_validator("pdf_pages_1based")
    @classmethod
    def unique_positive_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("page numbers must be positive")
        return sorted(set(value))

    @field_validator("supporting_pdf_pages_1based")
    @classmethod
    def unique_supporting_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("supporting page numbers must be positive")
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_record_specific_minimum(self) -> "ExtractedRecord":
        if self.record_type == "formal_item":
            if not self.source_item_number:
                self.review_flags.append("formal_item_number_unclear")
            if not self.item_type:
                raise ValueError("formal_item requires item_type")
            if not self.exact_text_de:
                raise ValueError("formal_item requires exact_text_de")
        if self.record_type == "dosing_rule":
            if not self.product_name and not self.active_substance_names:
                raise ValueError("dosing_rule requires a product or active substance")
            if not any([self.dose_value, self.loading_dose, self.maintenance_dose, self.interruption_rule]):
                self.review_flags.append("dose_value_not_explicit")
        if self.record_type == "adverse_reaction" and not self.adverse_reaction_term:
            raise ValueError("adverse_reaction requires adverse_reaction_term")
        if self.record_type == "table_figure_algorithm" and not self.visual_kind:
            raise ValueError("table_figure_algorithm requires visual_kind")
        return self


class ExtractionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    source_file_name: str = Field(min_length=1)
    document_type: Literal["guideline", "drug_label"]
    task_family: str = Field(min_length=1)
    request_pdf_pages_1based: list[int] = Field(min_length=1)
    owner_pdf_pages_1based: list[int] = Field(min_length=1)
    page_assessments: list[PageAssessment]
    records: list[ExtractedRecord] = Field(default_factory=list)

    @field_validator("request_pdf_pages_1based", "owner_pdf_pages_1based")
    @classmethod
    def normalize_pages(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


class PreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preflight_id: str
    document_type: Literal["guideline", "drug_label"]
    source_id: str
    pages: list[int]
    task_family: str
    passed: bool
    checks: dict[str, bool]
    notes: list[str] = Field(default_factory=list)

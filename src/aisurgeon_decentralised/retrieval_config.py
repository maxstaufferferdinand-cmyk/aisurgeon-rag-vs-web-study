"""Versioned configuration for the retrieval research phase.

Only source-explicit dates are populated.  ``None`` is intentional where the
local PDFs do not establish a safe version date.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RETRIEVAL_SCHEMA_VERSION = "retrieval-provenance-1.0.0"
RETRIEVAL_PIPELINE_VERSION = "retrieval-phase-1.2.0"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536
EMBEDDING_DISTANCE = "cosine"
EMBEDDING_PRICE_USD_PER_MILLION_TOKENS = 0.02
EMBEDDING_PRICE_AS_OF = "2026-08-16"
DEFAULT_RRF_K = 60


@dataclass(frozen=True)
class ComponentRange:
    first_page: int
    last_page: int
    component: str


@dataclass(frozen=True)
class DocumentProfile:
    document_kind: str
    source_status: str
    source_role: str
    source_authority: str
    version_label: str | None
    published_at: str | None
    valid_from: str | None = None
    valid_to: str | None = None
    component_ranges: tuple[ComponentRange, ...] = ()
    qa_flags: tuple[str, ...] = ()


def _guideline(
    *, status: str, version: str, published: str, extra_flags: tuple[str, ...] = ()
) -> DocumentProfile:
    return DocumentProfile(
        document_kind="guideline",
        source_status=status,
        source_role="guideline",
        source_authority="awmf_s3_guideline",
        version_label=version,
        published_at=published,
        component_ranges=(ComponentRange(1, 10000, "guideline"),),
        qa_flags=("published_at_month_precision", *extra_flags),
    )


def _national_smpc(version: str | None, published: str | None) -> DocumentProfile:
    flags: list[str] = []
    if version is None:
        flags.append("source_version_not_safely_reconstructed")
    if published is None:
        flags.append("published_at_not_safely_reconstructed")
    elif len(published) == 7:
        flags.append("published_at_month_precision")
    return DocumentProfile(
        document_kind="medicinal_product_information",
        source_status="current_at_snapshot",
        source_role="smPC",
        source_authority="national_authorised_product_information",
        version_label=version,
        published_at=published,
        component_ranges=(ComponentRange(1, 10000, "smPC"),),
        qa_flags=tuple(flags),
    )


def _epar(
    *, smpc_end: int, annex_end: int, labelling_end: int, total_pages: int
) -> DocumentProfile:
    return DocumentProfile(
        document_kind="medicinal_product_information",
        source_status="current_at_snapshot",
        source_role="mixed_regulatory_document",
        source_authority="ema_authorised_product_information",
        version_label=None,
        published_at=None,
        component_ranges=(
            ComponentRange(1, smpc_end, "smPC"),
            ComponentRange(smpc_end + 1, annex_end, "annex_ii"),
            ComponentRange(annex_end + 1, labelling_end, "labelling"),
            ComponentRange(labelling_end + 1, total_pages, "patient_information"),
        ),
        qa_flags=(
            "source_version_not_safely_reconstructed",
            "published_at_not_safely_reconstructed",
            "component_boundaries_locally_verified_from_pdf_headings",
        ),
    )


DOCUMENT_PROFILES: dict[str, DocumentProfile] = {
    "003-001l_S3_Prophylaxe-venoese-Thromboembolie-VTE_2026-04.pdf": _guideline(
        status="final", version="4.1", published="2026-01"
    ),
    "032-010OLl_Exokrines-Pankreaskarzinom_2025-06.pdf": _guideline(
        status="final", version="3.1", published="2024-09"
    ),
    "S3_LL_HCC_und_BCC_Konsultationsfassung_Langversion_6.01 (1).pdf": _guideline(
        status="consultation_draft",
        version="6.01",
        published="2026-07",
        extra_flags=("not_final_authorised_version_explicit_on_pdf_page_1",),
    ),
    "5-fu-medac-50-mg-ml-injektionsloesung.pdf": _national_smpc(
        "Stand der Information 11.2024", "2024-11"
    ),
    "cisplatin-teva-r-1-mg-ml-konzentrat.pdf": _national_smpc(
        "Version 5; Stand Februar 2025", "2025-02"
    ),
    "abraxane-epar-product-information_de.pdf": _epar(
        smpc_end=30, annex_end=32, labelling_end=40, total_pages=51
    ),
    "eliquis-epar-product-information_de.pdf": _epar(
        smpc_end=124, annex_end=127, labelling_end=151, total_pages=204
    ),
    "enhertu-epar-product-information_de.pdf": _epar(
        smpc_end=40, annex_end=44, labelling_end=49, total_pages=59
    ),
    "keytruda-epar-product-information_de.pdf": _epar(
        smpc_end=347, annex_end=351, labelling_end=363, total_pages=393
    ),
    "lixiana-epar-product-information_de.pdf": _epar(
        smpc_end=36, annex_end=39, labelling_end=63, total_pages=73
    ),
    "plavix-epar-product-information_de.pdf": _epar(
        smpc_end=27, annex_end=29, labelling_end=39, total_pages=56
    ),
    "xarelto-epar-product-information_de.pdf": _epar(
        smpc_end=186, annex_end=190, labelling_end=256, total_pages=347
    ),
}


def repository_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "pyproject.toml").is_file() and (path / "outputs").is_dir():
            return path
    raise RuntimeError("repository root with pyproject.toml and outputs/ not found")


def component_for_pages(profile: DocumentProfile, pages: list[int]) -> tuple[str, list[str]]:
    components = {
        item.component
        for page in pages
        for item in profile.component_ranges
        if item.first_page <= page <= item.last_page
    }
    flags: list[str] = []
    if len(components) != 1:
        flags.append("document_component_unknown_or_cross_boundary")
        return "unknown", flags
    return next(iter(components)), flags


def role_for_component(profile: DocumentProfile, component: str) -> str:
    if profile.document_kind == "guideline":
        return "guideline"
    return {
        "smPC": "smPC",
        "annex_ii": "regulatory_annex",
        "labelling": "labelling",
        "patient_information": "patient_information",
    }.get(component, "unknown")

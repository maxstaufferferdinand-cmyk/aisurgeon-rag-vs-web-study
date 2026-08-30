"""Reproducible Gemini-only extraction pipeline for the clinical knowledge corpus.

The pipeline deliberately keeps local PDF parsing limited to deterministic inventory,
batch planning, and quote/citation QA. Clinical canonical records are produced only by
the configured Gemini model from temporary mini-PDFs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from google import genai
from google.genai import errors, types
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from aisurgeon_decentralised.knowledge_corpus_models import (
    SCHEMA_VERSION,
    ExtractedRecord,
    ExtractionEnvelope,
    PreflightResult,
)
from aisurgeon_decentralised.knowledge_corpus_policy import (
    HCC_HISTORICAL_EXCLUSION_REASON,
    is_primary_use_eligible,
)
from aisurgeon_decentralised.knowledge_corpus_repair import (
    apply_coverage_overlay,
    apply_record_overlay,
    build_repair_audit,
    normalize_search_text,
)

from .local_config import secret_env_path

MODEL_NAME = "gemini-3.5-flash"
ENV_PATH = secret_env_path()
PROMPT_VERSION = "clinical-corpus-de-v1.2.0"
RUN_SCHEMA_VERSION = "run-manifest-1.0.0"
ALLOWED_COVERAGE_STATUSES = {
    "extracted",
    "blank",
    "front_matter",
    "table_of_contents",
    "references",
    "appendix",
    "label_or_leaflet_noncanonical",
    "unreadable_flagged",
}


class PipelineError(RuntimeError):
    pass


class FatalGeminiError(PipelineError):
    pass


@dataclass
class PageInfo:
    page: int
    text: str
    printed_label: str | None
    status: str = "extracted"
    status_reason: str = "Inhaltliche Seite"
    primary_family: str | None = None
    families: set[str] = field(default_factory=set)
    sections: list[str] = field(default_factory=list)
    dense: bool = False
    visual_candidate: bool = False
    formal_candidate: bool = False
    canonical: bool = True


@dataclass(frozen=True)
class Batch:
    source_id: str
    source_file_name: str
    source_sha256: str
    document_type: str
    request_pages: tuple[int, ...]
    owner_pages: tuple[int, ...]
    task_family: str

    @property
    def batch_id(self) -> str:
        material = "|".join(
            [
                self.source_sha256,
                f"{self.request_pages[0]}-{self.request_pages[-1]}",
                ",".join(map(str, self.owner_pages)),
                self.task_family,
                PROMPT_VERSION,
                SCHEMA_VERSION,
                MODEL_NAME,
            ]
        )
        return "batch-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(*parts: Any, length: int = 24) -> str:
    value = "|".join(json.dumps(part, ensure_ascii=False, sort_keys=True) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def slug(value: str, max_length: int = 80) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    folded = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return (folded[:max_length].rstrip("-") or "unknown")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00ad", "")
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    value = value.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return value


def text_hash(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def quote_locally_verifiable(quote: str, page_texts: Sequence[str]) -> bool:
    needle = normalize_text(quote)
    if not needle:
        return False
    haystack = normalize_text(" ".join(page_texts))
    if needle in haystack:
        return True
    # Layout extraction may inject headers or line-break artifacts. Verify several
    # distinctive consecutive windows without rewriting the canonical quotation.
    tokens = needle.split()
    if len(tokens) < 8:
        return needle in haystack
    windows = [" ".join(tokens[index : index + 8]) for index in range(0, len(tokens) - 7, 8)]
    return bool(windows) and sum(window in haystack for window in windows) / len(windows) >= 0.6


def response_is_empty(text: str) -> bool:
    return not (text or "").strip()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_api_key() -> str:
    """The only supported key-loading path; never logs or exports the value."""
    values = dotenv_values(ENV_PATH)
    key = values.get("GEMINI_API_KEY")
    if not key or not isinstance(key, str) or not key.strip():
        raise FatalGeminiError(f"GEMINI_API_KEY fehlt oder ist leer in {ENV_PATH}")
    return key.strip()


def safe_error(exc: BaseException, secret: str | None = None) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if secret:
        message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"(?i)(api[_ -]?key[=: ]+)[^\s,;]+", r"\1[REDACTED]", message)
    return message[:4000]


def ensure_output_tree(output_root: Path) -> None:
    directories = [
        "manifests",
        "schemas",
        "checkpoints/validated",
        "checkpoints/failed",
        "canonical",
        "links",
        "retrieval",
        "qa",
        "rendered_sources",
        "logs",
        ".work",
    ]
    for directory in directories:
        (output_root / directory).mkdir(parents=True, exist_ok=True)


def verify_frozen_sources(project_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    frozen_paths = set()
    for source in manifest["sources"]:
        path = project_root / source["relative_path"]
        frozen_paths.add(path.resolve())
        actual = sha256_file(path) if path.exists() else None
        results.append(
            {
                "source_id": source["source_id"],
                "relative_path": source["relative_path"],
                "expected_sha256": source["sha256"],
                "actual_sha256": actual,
                "unchanged": actual == source["sha256"],
            }
        )
    current = {
        path.resolve()
        for path in (project_root / "source_pdfs").iterdir()
        if path.is_file() and path.suffix.casefold() == ".pdf"
    }
    if current != frozen_paths:
        missing = sorted(str(path) for path in frozen_paths - current)
        added = sorted(str(path) for path in current - frozen_paths)
        raise PipelineError(f"Frozen input set changed; missing={missing}, added={added}")
    changed = [item for item in results if not item["unchanged"]]
    if changed:
        raise PipelineError(f"Source SHA-256 mismatch for {[item['source_id'] for item in changed]}")
    return results


def load_page_infos(project_root: Path, source: dict[str, Any]) -> list[PageInfo]:
    path = project_root / source["relative_path"]
    reader = PdfReader(str(path), strict=False)
    labels = source.get("page_labels") or [None] * len(reader.pages)
    infos: list[PageInfo] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - malformed public PDFs may raise backend-specific errors
            page_text = ""
        normalized = normalize_text(page_text)
        visual = bool(
            re.search(r"(?im)^\s*(?:tabelle|abbildung|algorithmus|schema)\s+\d+[a-z]?(?:\s*[:.-]|\s)", page_text)
        )
        formal = bool(
            re.search(
                r"(?i)\b(?:evidenzbasierte|konsensbasierte)\s+(?:empfehlung|statement)\b|"
                r"\bempfehlungsgrad\b|\blevel of evidence\b|\b(?:starker\s+)?konsens\s*\(\s*\d+\s*%",
                page_text,
            )
        )
        infos.append(
            PageInfo(
                page=number,
                text=page_text,
                printed_label=str(labels[number - 1]) if number - 1 < len(labels) and labels[number - 1] is not None else None,
                # Long prose is still handled with the normal page count. Only
                # exceptionally dense pages (or detected visuals below) trigger
                # the 1-3 page mode intended for complex tables/algorithms.
                dense=len(normalized) > 9000,
                visual_candidate=visual,
                formal_candidate=formal,
            )
        )
    return infos


def _guideline_reference_start(infos: list[PageInfo]) -> int | None:
    threshold = max(20, int(len(infos) * 0.55))
    for info in infos[threshold - 1 :]:
        head = "\n".join(info.text.splitlines()[:12])
        if re.search(r"(?im)^\s*(?:\d+(?:\.\d+)*)?\s*(?:literatur(?:verzeichnis)?|referenzen)\s*$", head):
            return info.page
    return None


def _guideline_appendix_start(infos: list[PageInfo]) -> int | None:
    threshold = max(20, int(len(infos) * 0.65))
    for info in infos[threshold - 1 :]:
        head = "\n".join(info.text.splitlines()[:10])
        if re.search(r"(?im)^\s*(?:\d+\s+)?anh(?:a|ä)ng(?:e)?\b", head):
            return info.page
    return None


def classify_guideline_pages(infos: list[PageInfo]) -> None:
    toc_pages: set[int] = set()
    for info in infos[:40]:
        text = info.text
        toc_like = "inhaltsverzeichnis" in text.casefold() or len(re.findall(r"\.{4,}\s*\d+", text)) >= 3
        if toc_like:
            toc_pages.add(info.page)
            if info.page + 1 <= len(infos):
                toc_pages.add(info.page + 1)
    if toc_pages:
        toc_min, toc_max = min(toc_pages), max(toc_pages)
        toc_pages.update(range(toc_min, toc_max + 1))
    reference_start = _guideline_reference_start(infos)
    appendix_start = _guideline_appendix_start(infos)

    grading_pages: set[int] = set()
    for info in infos:
        if re.search(
            r"(?i)schema der evidenzgraduierung|graduierung der konsensstärke|schema der empfehlungsgraduierung|"
            r"empfehlungsgrad\s+(?:beschreibung|syntax)|oxford centre for evidence",
            info.text,
        ):
            grading_pages.add(info.page)
            if info.page + 1 <= len(infos):
                grading_pages.add(info.page + 1)

    formal_pages = {info.page for info in infos if info.formal_candidate}
    formal_pages.update(page + 1 for page in list(formal_pages) if page + 1 <= len(infos))

    visual_pages = {info.page for info in infos if info.visual_candidate}
    visual_pages.update(page + 1 for page in list(visual_pages) if page + 1 <= len(infos))

    for info in infos:
        if not normalize_text(info.text):
            info.status = "blank"
            info.status_reason = "Keine lokal extrahierbaren Zeichen"
            info.primary_family = None
            info.canonical = True
            continue
        if info.page in toc_pages:
            info.status = "table_of_contents"
            info.status_reason = "Lokal als Inhaltsverzeichnis erkannt"
            info.primary_family = "guideline_structure_metadata"
        elif info.page < (min(toc_pages) if toc_pages else 4):
            info.status = "front_matter"
            info.status_reason = "Titelseite oder vorangestellte Dokumentinformation"
            info.primary_family = "guideline_structure_metadata"
        elif appendix_start and info.page >= appendix_start and (not reference_start or appendix_start < reference_start):
            info.status = "appendix"
            info.status_reason = "Anhangsbereich"
            info.primary_family = "guideline_narrative_context"
        elif reference_start and info.page >= reference_start:
            info.status = "references"
            info.status_reason = "Literaturverzeichnis"
            info.primary_family = "guideline_references"
        elif info.page in grading_pages:
            info.status = "extracted"
            info.status_reason = "Methodik- oder Grading-Seite"
            info.primary_family = "guideline_grading"
        elif info.page in formal_pages:
            info.status = "extracted"
            info.status_reason = "Formales Leitlinienitem oder unmittelbar zugehöriger Kontext"
            info.primary_family = "guideline_formal_package"
        else:
            info.status = "extracted"
            info.status_reason = "Leitlinien-Hintergrund, Kommentar oder Kapiteltext"
            info.primary_family = "guideline_narrative_context"
        if info.primary_family:
            info.families.add(info.primary_family)
        if info.page in visual_pages and info.status not in {"blank", "references"}:
            info.families.add("guideline_visuals")


DRUG_SECTION_FAMILY = {
    "1": "drug_product_identity_composition",
    "2": "drug_product_identity_composition",
    "3": "drug_product_identity_composition",
    "4.1": "drug_indications",
    "4.2": "drug_dosing",
    "4.3": "drug_contraindications",
    "4.4": "drug_warnings",
    "4.5": "drug_interactions",
    "4.6": "drug_pregnancy_lactation_fertility",
    "4.7": "drug_warnings",
    "4.8": "drug_adverse_reactions",
    "4.9": "drug_overdose_pharmacology",
    "5.1": "drug_overdose_pharmacology",
    "5.2": "drug_overdose_pharmacology",
    "5.3": "drug_overdose_pharmacology",
    "6.1": "drug_product_identity_composition",
    "6.2": "drug_storage_handling",
    "6.3": "drug_storage_handling",
    "6.4": "drug_storage_handling",
    "6.5": "drug_storage_handling",
    "6.6": "drug_storage_handling",
    "7": "drug_product_identity_composition",
    "8": "drug_product_identity_composition",
    "9": "drug_product_identity_composition",
    "10": "drug_product_identity_composition",
}


def detect_drug_canonical_range(infos: list[PageInfo], source: dict[str, Any]) -> tuple[int, int]:
    is_epar = "epar" in source["original_file_name"].casefold() or str(
        source.get("pdf_metadata", {}).get("/Subject", "")
    ).casefold() == "epar"
    if not is_epar:
        return 1, len(infos)
    start = 1
    for info in infos[:15]:
        if re.search(r"(?i)\b(?:anhang|annex)\s+i\b", info.text):
            start = info.page
            break
    end = len(infos)
    for info in infos[start:]:
        # Page-number prefixes and PDF reading order can place the Annex heading
        # below more than twenty extracted lines, so search the complete page.
        if re.search(r"(?i)\b(?:anhang|annex)\s+ii\b", info.text):
            end = info.page - 1
            break
    return start, end


def detect_drug_sections(text: str) -> list[str]:
    matches = re.findall(
        r"(?im)(?:^|\n)\s*(10|[1-9]|4\.[1-9]|5\.[1-3]|6\.[1-6])\.?\s+"
        r"(?=(?:bezeichnung|qualitative|darreichungsform|klinische|anwendungsgebiete|dosierung|art der anwendung|"
        r"gegenanzeigen|besondere warnhinweise|wechselwirkungen|fertilität|schwangerschaft|stillzeit|verkehrstüchtigkeit|"
        r"nebenwirkungen|überdosierung|pharmakologische|pharmakodynamische|pharmakokinetische|präklinische|"
        r"liste der sonstigen bestandteile|inkompatibilitäten|dauer der haltbarkeit|besondere vorsichtsmaßnahmen für die aufbewahrung|"
        r"art und inhalt des behältnisses|besondere vorsichtsmaßnahmen für die beseitigung|inhaber der zulassung|"
        r"zulassungsnummer|datum der erteilung|stand der information))",
        text,
    )
    return list(dict.fromkeys(matches))


def classify_drug_pages(infos: list[PageInfo], source: dict[str, Any]) -> None:
    canonical_start, canonical_end = detect_drug_canonical_range(infos, source)
    current_section = "1"
    for info in infos:
        if info.page < canonical_start:
            info.status = "front_matter"
            info.status_reason = "Vorangestellte Seite vor Annex I"
            info.primary_family = None
            info.canonical = False
            continue
        if info.page > canonical_end:
            info.status = "label_or_leaflet_noncanonical"
            info.status_reason = "EMA-Anhang außerhalb Annex I (Kennzeichnung/Packungsbeilage oder weiterer nichtkanonischer Anhang)"
            info.primary_family = None
            info.canonical = False
            continue
        if not normalize_text(info.text):
            info.status = "blank"
            info.status_reason = "Keine lokal extrahierbaren Zeichen"
            info.primary_family = None
            info.canonical = True
            continue
        previous_section = current_section
        sections = detect_drug_sections(info.text)
        page_sections = [previous_section]
        page_sections.extend(section for section in sections if section not in page_sections)
        if sections:
            current_section = sections[-1]
        info.sections = page_sections
        families = {DRUG_SECTION_FAMILY.get(section, "drug_product_identity_composition") for section in info.sections}
        # Carry the preceding section into transition pages, because a page can start
        # with the end of one regulatory section and introduce the next one.
        families.add(DRUG_SECTION_FAMILY.get(current_section, "drug_product_identity_composition"))
        if "4.2" in info.sections or current_section == "4.2":
            families.add("drug_preparation_administration")
        if "6.6" in info.sections or current_section == "6.6":
            families.add("drug_preparation_administration")
        info.families.update(families)
        info.primary_family = DRUG_SECTION_FAMILY.get(current_section, min(families))
        info.status = "extracted"
        info.status_reason = f"Kanonische Fachinformation Annex I/SmPC, Abschnitt {current_section}"


def build_windows(infos: list[PageInfo], document_type: str) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    base_total = 6 if document_type == "guideline" else 4
    dense_total = 3
    windows: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    next_owner = 1
    page_count = len(infos)
    first = True
    while next_owner <= page_count:
        request_start = next_owner if first else next_owner - 1
        provisional_end = min(page_count, request_start + base_total - 1)
        proposed = infos[request_start - 1 : provisional_end]
        use_dense = any(info.dense or info.visual_candidate for info in proposed)
        total = dense_total if use_dense else base_total
        # Comprehensive per-window extraction can legitimately produce many
        # atomic records on dense clinical pages. Keep those requests small to
        # remain well within the structured-output token ceiling.
        if document_type == "guideline":
            total = min(total, 4)
        else:
            total = min(total, 3)
        if any(info.status == "references" for info in proposed):
            # Bibliography pages contain many independent records; one new owner
            # page plus the overlap page keeps the JSON response bounded.
            total = min(total, 2)
        request_end = min(page_count, request_start + total - 1)
        owner_start = next_owner
        owner_end = request_end
        request_pages = tuple(range(request_start, request_end + 1))
        owner_pages = tuple(range(owner_start, owner_end + 1))
        windows.append((request_pages, owner_pages))
        next_owner = owner_end + 1
        first = False
    return windows


def plan_batches(source: dict[str, Any], infos: list[PageInfo]) -> list[Batch]:
    batches: list[Batch] = []
    info_by_page = {info.page: info for info in infos}
    for request_pages, window_owner_pages in build_windows(infos, source["document_type"]):
        families: dict[str, list[int]] = defaultdict(list)
        for page in window_owner_pages:
            for family in sorted(info_by_page[page].families):
                families[family].append(page)
        for family, family_owner_pages in sorted(families.items()):
            batches.append(
                Batch(
                    source_id=source["source_id"],
                    source_file_name=source["original_file_name"],
                    source_sha256=source["sha256"],
                    document_type=source["document_type"],
                    request_pages=request_pages,
                    owner_pages=tuple(family_owner_pages),
                    task_family=family,
                )
            )
    return batches


def plan_combined_batches(source: dict[str, Any], infos: list[PageInfo]) -> list[Batch]:
    """Plan one comprehensive task family per page window.

    Sparse local inventory still decides whether a canonical request is needed;
    Gemini receives a single clearly bounded page-extraction family per request
    and chooses record types within that family. This preserves all required
    content while avoiding repeated uploads of the same pages for many section
    subfamilies.
    """
    task_family = (
        "guideline_page_extraction" if source["document_type"] == "guideline" else "drug_page_extraction"
    )
    batches: list[Batch] = []
    info_by_page = {info.page: info for info in infos}
    for request_pages, window_owner_pages in build_windows(infos, source["document_type"]):
        owner_pages = tuple(
            page for page in window_owner_pages if info_by_page[page].primary_family is not None
        )
        if not owner_pages:
            continue
        batches.append(
            Batch(
                source_id=source["source_id"],
                source_file_name=source["original_file_name"],
                source_sha256=source["sha256"],
                document_type=source["document_type"],
                request_pages=request_pages,
                owner_pages=owner_pages,
                task_family=task_family,
            )
        )
    return batches


def write_batch_pdf(source_path: Path, pages: Sequence[int], destination: Path) -> None:
    reader = PdfReader(str(source_path), strict=False)
    writer = PdfWriter()
    for page in pages:
        writer.add_page(reader.pages[page - 1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        writer.write(handle)


FAMILY_INSTRUCTIONS: dict[str, str] = {
    "guideline_page_extraction": """
First inventory each owner page, then extract every relevant guideline record present on it. Use
document_metadata/chapter_structure for document structure; grading_system for explicit grading
definitions; formal_item for every formal recommendation or statement; rationale_block for
separate rationales, comments, definitions, and substantive clinical background;
guideline_reference for each visible bibliography entry; and table_figure_algorithm for actual
tables, figures, schemas, or algorithms. Preserve formal item numbering/order, complete unchanged
item wording, explicit grades/evidence/consensus, linked labels, table cells/footnotes, and explicit
algorithm nodes/connections. Do not turn table-of-contents entries or mere visual cross-references
into substantive records. For narrative, create source-contiguous blocks; do not omit clinical
background merely because the page contains no formal item. Aim for complete owner-page coverage,
not a selective summary.
""",
    "drug_page_extraction": """
First inventory each owner page, then extract every relevant Annex I/SmPC record present on it.
Use the most specific record type: product/substance/composition/regulatory identity; separate
therapeutic indications; atomic dosing rules by indication/population/route/combination; separate
preparation and administration; contraindications; clinically distinct warnings; interactions;
pregnancy/lactation/fertility; adverse reactions retaining system organ class and exact frequency;
overdose/pharmacodynamics/pharmacokinetics; excipients; incompatibility; or storage/handling.
Preserve values, units, frequency, route, adjustment and interruption rules, temperatures, times,
and regulatory qualifiers exactly. Never mix different products, strengths, indications,
populations, routes, or combination contexts. Extract every relevant assertion on every owner page;
do not provide only examples or a summary. Do not reproduce labeling or patient-leaflet text.
""",
    "guideline_structure_metadata": """
Extract only visible document/version metadata and the chapter hierarchy on the owner pages.
Use document_metadata and chapter_structure records. Preserve exact titles, version dates,
chapter numbers, and cross-references. Table-of-contents entries may establish hierarchy but
must not be treated as substantive recommendations.
""",
    "guideline_grading": """
Extract only the guideline's explicit evidence, recommendation-grade, consensus-strength,
and statement definitions. Use grading_system records, preserving every grade label and its
original definition verbatim. Do not assign a grade to an item merely from general knowledge.
""",
    "guideline_formal_package": """
Extract every formal recommendation, statement, consensus statement, and other clearly formal
item on the owner pages. Use formal_item and, separately, rationale_block for directly attached
rationales, comments, qualifications, or substantive background. exact_text_de must be the
complete unchanged item wording; surrounding grade or consensus text belongs outside that field. Preserve the
exact source item number, visible recommendation grade, evidence level, consensus strength,
and qualifiers. Leave non-explicit PICO/setting fields null. Record explicit links to reference,
table, figure, and algorithm labels. Never merge separate formal items even when wording matches.
""",
    "guideline_narrative_context": """
Extract the complete substantive clinical/methodological background, definitions, comments,
and rationales visible on owner pages as source-contiguous rationale_block records. Exclude
running headers, footers, and repeated navigation. Prefer atomic blocks of roughly 250-600
tokens and never exceed about 900 tokens. Add chapter_structure only when a visible heading
establishes hierarchy. If an unambiguously formal item is encountered, preserve it as a
formal_item and add review flag unexpected_formal_item_in_narrative_batch rather than dropping it.
""",
    "guideline_references": """
Extract each visible bibliography entry as one guideline_reference record in document order.
Preserve the full reference exactly. Copy DOI or PMID only when visibly printed. Do not resolve
or infer identifiers online. Preserve section/chapter association when explicit.
""",
    "guideline_visuals": """
Extract only actual tables, figures, schemas, and algorithms present or continuing on owner pages.
Use table_figure_algorithm records. Preserve number, title, caption, all readable visible text,
table rows/cells, footnotes, and for algorithms explicit nodes and directed connections. Do not
invent obscured cells or graph relations; flag uncertainty. A mere textual cross-reference to a
visual elsewhere is not the visual itself.
""",
    "drug_product_identity_composition": """
Use only Annex I/the professional SmPC. Extract drug_product, active_substance, composition,
excipient, and regulatory_metadata records relevant on owner pages: exact product/INN names,
strength, dosage form, composition, other ingredients, holder, authorisation number, and revision
date. Keep separately authorised strengths/forms distinct and preserve original spellings.
""",
    "drug_indications": """
Extract each therapeutic indication as a separate therapeutic_indication record. Keep diseases,
stages, biomarkers, populations, monotherapy/combination contexts, and restrictions distinct.
Use only text explicit in the professional SmPC.
""",
    "drug_dosing": """
Extract atomic dosing_rule records. Never combine different indications, populations, routes,
or combination contexts. Preserve exact dose values, units, frequency, route, duration, maximum,
loading/maintenance doses, combination partners, renal/hepatic/age/toxicity adjustments, and
interruption/discontinuation rules whenever explicit. A dose table row generally becomes its own
record. Do not calculate or normalize a dose using external knowledge.
""",
    "drug_preparation_administration": """
Extract preparation_administration records only: preparation, reconstitution, dilution,
compatibility during preparation, infusion/injection method, administration sequence, equipment,
and handling instructions. Preserve concentrations, volumes, times, materials, and units exactly.
""",
    "drug_contraindications": """
Extract each distinct contraindication as its own contraindication record, preserving population,
condition, substance/product association, and exact wording. Do not convert warnings into absolute
contraindications.
""",
    "drug_warnings": """
Extract each clinically distinct warning or precaution as a warning record, including monitoring,
driving/operating machinery, risk groups, interruption/discontinuation actions, and organ-function
constraints. Keep separate risks and populations separate.
""",
    "drug_interactions": """
Extract interaction records atomically by interacting substance/class/mechanism or clinical rule.
Preserve effect, recommendation, timing, and evidence qualifiers only when explicit.
""",
    "drug_pregnancy_lactation_fertility": """
Extract pregnancy_lactation_fertility records, separating pregnancy, contraception, breast-feeding,
and fertility assertions where meaningful. Preserve durations and restrictions exactly.
""",
    "drug_adverse_reactions": """
Extract adverse_reaction records according to the regulatory structure. Preserve system organ
class, exact adverse-reaction term, and exact frequency category/range. Keep table rows distinct;
do not infer a frequency from ordering or external knowledge.
""",
    "drug_overdose_pharmacology": """
Extract overdose, pharmacodynamics, and pharmacokinetics records. Keep overdose presentation and
management distinct. Preserve explicit mechanism, exposure, absorption, distribution, metabolism,
elimination, special-population, and dose-proportionality information without external inference.
""",
    "drug_storage_handling": """
Extract incompatibility and storage_handling records for incompatibilities, shelf life, in-use
stability, storage conditions, container, handling, and disposal. Preserve temperatures, times,
light conditions, materials, and exceptions exactly.
""",
    "guideline_preflight": """
This is a validation-only preflight. Extract formal_item/rationale_block and any actual
table_figure_algorithm on all three owner pages. Preserve exact item numbers, exact complete item
wording, explicit grades/consensus, table cells/footnotes, and page locators. Do not omit a page.
""",
    "drug_preflight": """
This is a validation-only preflight. Extract drug_product, active_substance, therapeutic_indication,
dosing_rule, preparation_administration, contraindication, warning, and actual table structures
visible on all three owner pages. Dosing must be atomic and preserve every value/unit exactly.
""",
}


BASE_RESPONSE_RECORD_FIELDS = {
    "record_type",
    "source_identifier",
    "title",
    "section_path",
    "pdf_pages_1based",
    "printed_page_label",
    "exact_source_text",
    "semantic_summary_de",
    "confidence",
    "review_flags",
    "medication_mentions_original",
    "normalized_entities",
    "keywords",
    "indications",
    "populations",
    "routes",
}
DRUG_COMMON_RESPONSE_FIELDS = {
    "product_name",
    "original_product_name",
    "active_substance_names",
    "active_substance_original_names",
    "strength",
    "pharmaceutical_form",
    "indication",
    "population",
    "treatment_context",
}
FAMILY_RECORD_FIELDS: dict[str, set[str]] = {
    "guideline_page_extraction": {
        "document_order",
        "source_item_number",
        "item_type",
        "exact_text_de",
        "recommendation_grade",
        "evidence_level",
        "consensus_strength",
        "qualifiers",
        "population",
        "intervention",
        "comparator",
        "outcome",
        "setting",
        "linked_item_numbers",
        "linked_reference_labels",
        "linked_table_figure_labels",
        "visual_kind",
        "caption",
        "table_rows",
        "footnotes",
        "algorithm_nodes",
        "algorithm_edges",
        "semantic_description_de",
    },
    "drug_page_extraction": DRUG_COMMON_RESPONSE_FIELDS
    | {
        "marketing_authorisation_holder",
        "authorisation_numbers",
        "revision_date",
        "component_name",
        "component_role",
        "amount",
        "amount_unit",
        "dose_value",
        "dose_unit",
        "frequency",
        "route",
        "duration",
        "maximum_dose",
        "loading_dose",
        "maintenance_dose",
        "combination_partners",
        "renal_adjustment",
        "hepatic_adjustment",
        "age_adjustment",
        "toxicity_adjustment",
        "interruption_rule",
        "discontinuation_rule",
        "preparation_instruction",
        "system_organ_class",
        "adverse_reaction_term",
        "frequency_category",
        "pregnancy_information",
        "lactation_information",
        "fertility_information",
    },
    "guideline_structure_metadata": set(),
    "guideline_grading": {"qualifiers", "recommendation_grade", "evidence_level", "consensus_strength"},
    "guideline_formal_package": {
        "document_order",
        "source_item_number",
        "item_type",
        "exact_text_de",
        "recommendation_grade",
        "evidence_level",
        "consensus_strength",
        "qualifiers",
        "population",
        "intervention",
        "comparator",
        "outcome",
        "setting",
        "linked_item_numbers",
        "linked_reference_labels",
        "linked_table_figure_labels",
    },
    "guideline_narrative_context": {
        "linked_item_numbers",
        "linked_reference_labels",
        "linked_table_figure_labels",
        "population",
        "intervention",
        "comparator",
        "outcome",
        "setting",
        "source_item_number",
        "item_type",
        "exact_text_de",
    },
    "guideline_references": set(),
    "guideline_visuals": {
        "visual_kind",
        "caption",
        "table_rows",
        "footnotes",
        "algorithm_nodes",
        "algorithm_edges",
        "semantic_description_de",
    },
    "drug_product_identity_composition": DRUG_COMMON_RESPONSE_FIELDS
    | {
        "marketing_authorisation_holder",
        "authorisation_numbers",
        "revision_date",
        "component_name",
        "component_role",
        "amount",
        "amount_unit",
    },
    "drug_indications": DRUG_COMMON_RESPONSE_FIELDS,
    "drug_dosing": DRUG_COMMON_RESPONSE_FIELDS
    | {
        "dose_value",
        "dose_unit",
        "frequency",
        "route",
        "duration",
        "maximum_dose",
        "loading_dose",
        "maintenance_dose",
        "combination_partners",
        "renal_adjustment",
        "hepatic_adjustment",
        "age_adjustment",
        "toxicity_adjustment",
        "interruption_rule",
        "discontinuation_rule",
        "preparation_instruction",
    },
    "drug_preparation_administration": DRUG_COMMON_RESPONSE_FIELDS
    | {"route", "duration", "preparation_instruction"},
    "drug_contraindications": DRUG_COMMON_RESPONSE_FIELDS,
    "drug_warnings": DRUG_COMMON_RESPONSE_FIELDS
    | {
        "renal_adjustment",
        "hepatic_adjustment",
        "age_adjustment",
        "toxicity_adjustment",
        "interruption_rule",
        "discontinuation_rule",
    },
    "drug_interactions": DRUG_COMMON_RESPONSE_FIELDS | {"combination_partners", "frequency", "duration"},
    "drug_pregnancy_lactation_fertility": DRUG_COMMON_RESPONSE_FIELDS
    | {"pregnancy_information", "lactation_information", "fertility_information", "duration"},
    "drug_adverse_reactions": DRUG_COMMON_RESPONSE_FIELDS
    | {"system_organ_class", "adverse_reaction_term", "frequency_category"},
    "drug_overdose_pharmacology": DRUG_COMMON_RESPONSE_FIELDS
    | {"dose_value", "dose_unit", "frequency", "route", "duration"},
    "drug_storage_handling": DRUG_COMMON_RESPONSE_FIELDS
    | {"component_name", "amount", "amount_unit", "duration", "preparation_instruction"},
    "guideline_preflight": {
        "document_order",
        "source_item_number",
        "item_type",
        "exact_text_de",
        "recommendation_grade",
        "evidence_level",
        "consensus_strength",
        "qualifiers",
        "population",
        "intervention",
        "comparator",
        "outcome",
        "setting",
        "linked_item_numbers",
        "linked_reference_labels",
        "linked_table_figure_labels",
        "visual_kind",
        "caption",
        "table_rows",
        "footnotes",
        "algorithm_nodes",
        "algorithm_edges",
        "semantic_description_de",
    },
    "drug_preflight": DRUG_COMMON_RESPONSE_FIELDS
    | {
        "dose_value",
        "dose_unit",
        "frequency",
        "route",
        "duration",
        "maximum_dose",
        "loading_dose",
        "maintenance_dose",
        "combination_partners",
        "renal_adjustment",
        "hepatic_adjustment",
        "age_adjustment",
        "toxicity_adjustment",
        "interruption_rule",
        "discontinuation_rule",
        "preparation_instruction",
        "visual_kind",
        "caption",
        "table_rows",
        "footnotes",
    },
}
FAMILY_RECORD_TYPES: dict[str, set[str]] = {
    "guideline_page_extraction": {
        "document_metadata",
        "chapter_structure",
        "grading_system",
        "formal_item",
        "rationale_block",
        "guideline_reference",
        "table_figure_algorithm",
    },
    "drug_page_extraction": {
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
    },
    "guideline_structure_metadata": {"document_metadata", "chapter_structure"},
    "guideline_grading": {"grading_system"},
    "guideline_formal_package": {"formal_item", "rationale_block"},
    "guideline_narrative_context": {"rationale_block", "chapter_structure", "formal_item"},
    "guideline_references": {"guideline_reference"},
    "guideline_visuals": {"table_figure_algorithm"},
    "drug_product_identity_composition": {
        "drug_product",
        "active_substance",
        "composition",
        "excipient",
        "regulatory_metadata",
    },
    "drug_indications": {"therapeutic_indication"},
    "drug_dosing": {"dosing_rule"},
    "drug_preparation_administration": {"preparation_administration"},
    "drug_contraindications": {"contraindication"},
    "drug_warnings": {"warning"},
    "drug_interactions": {"interaction"},
    "drug_pregnancy_lactation_fertility": {"pregnancy_lactation_fertility"},
    "drug_adverse_reactions": {"adverse_reaction"},
    "drug_overdose_pharmacology": {"overdose", "pharmacodynamics", "pharmacokinetics"},
    "drug_storage_handling": {"incompatibility", "storage_handling"},
    "guideline_preflight": {"formal_item", "rationale_block", "table_figure_algorithm"},
    "drug_preflight": {
        "drug_product",
        "active_substance",
        "therapeutic_indication",
        "dosing_rule",
        "preparation_administration",
        "contraindication",
        "warning",
        "table_figure_algorithm",
    },
}


def family_response_json_schema(task_family: str) -> dict[str, Any]:
    """Prune the broad canonical model to the fields relevant to one request.

    Missing optional fields are restored by Pydantic defaults after receipt. This
    keeps structured output strict without forcing Gemini to emit dozens of null
    fields unrelated to the selected task family.
    """
    import copy

    schema = copy.deepcopy(ExtractionEnvelope.model_json_schema())
    record_schema = schema["$defs"]["ExtractedRecord"]
    keep = BASE_RESPONSE_RECORD_FIELDS | FAMILY_RECORD_FIELDS[task_family]
    record_schema["properties"] = {
        name: definition for name, definition in record_schema["properties"].items() if name in keep
    }
    record_schema["required"] = [name for name in record_schema.get("required", []) if name in keep]
    allowed_types = sorted(FAMILY_RECORD_TYPES[task_family])
    record_schema["properties"]["record_type"]["enum"] = allowed_types
    schema["$defs"]["PageAssessment"]["properties"]["relevant_record_types"]["items"]["enum"] = allowed_types
    type_specific_required = {
        "formal_item": ["item_type", "exact_text_de"],
        "adverse_reaction": ["adverse_reaction_term"],
        "table_figure_algorithm": ["visual_kind"],
    }
    record_schema["anyOf"] = [
        {
            "properties": {"record_type": {"type": "string", "enum": [record_type]}},
            "required": ["record_type", *type_specific_required.get(record_type, [])],
        }
        for record_type in allowed_types
    ]
    if "algorithm_nodes" not in keep:
        schema["$defs"].pop("AlgorithmNode", None)
    if "algorithm_edges" not in keep:
        schema["$defs"].pop("AlgorithmEdge", None)
    return schema


def build_prompt(batch: Batch, repair_note: str | None = None) -> str:
    request_page_map = ", ".join(
        f"attachment page {index} = original PDF page {page}"
        for index, page in enumerate(batch.request_pages, start=1)
    )
    repair = ""
    if repair_note:
        repair = f"""
TARGETED REPAIR: The prior response failed validation for this reason: {repair_note[:1200]}
Return a complete corrected response, not a patch. Re-read the same attached mini-PDF.
"""
    return f"""
You are performing source-faithful structured extraction from a German medical PDF mini-batch.
Use ONLY the attached PDF. Do not use web search, external databases, memory, or general medical
knowledge to fill gaps. Unknown or illegible values must be null and receive a specific review flag.

Source identity (must be copied exactly into the response envelope):
- source_id: {batch.source_id}
- source_file_name: {batch.source_file_name}
- document_type: {batch.document_type}
- task_family: {batch.task_family}
- request_pdf_pages_1based: {list(batch.request_pages)}
- owner_pdf_pages_1based: {list(batch.owner_pages)}

Page map: {request_page_map}

Overlap pages are context only. Create records and page_assessments ONLY for owner pages
{list(batch.owner_pages)}. Every returned record's pdf_pages_1based must be a non-empty subset of
owner pages. Return exactly one page_assessment per owner page, even when no target record exists.
Allowed page status values are: extracted, blank, front_matter, table_of_contents, references,
appendix, label_or_leaflet_noncanonical, unreadable_flagged. Every status needs a concrete German
reason. Do not use local attachment page numbers.

For every record:
- exact_source_text is a non-empty verbatim transcription of the relevant visible source text;
- semantic_summary_de is separate, conservative, and contains no unsupported interpretation;
- section_path preserves the visible hierarchy;
- source_identifier is copied only when visible (item/chapter/table/figure/reference/section number);
- confidence is calibrated 0..1 and review_flags name concrete uncertainty;
- medication_mentions_original, entities, keywords, indications, populations, and routes contain
  only source-explicit information;
- do not invent or resolve item numbers, grades, evidence levels, pages, references, DOI, PMID,
  regulatory values, dose units, product information, or links.

Task-family instructions:
{FAMILY_INSTRUCTIONS[batch.task_family]}

Respond as strict JSON conforming to the supplied schema. Do not add prose or markdown.
{repair}
"""


def parse_response_text(text: str) -> ExtractionEnvelope:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = json.loads(cleaned)
    # Some Gemini structured-output responses still omit conditional fields even
    # when the JSON schema requires them. A formal item's canonical quote is the
    # complete verbatim item block, so use it as exact_text_de only as a narrow,
    # deterministic repair when the model omitted that duplicate representation.
    for record in payload.get("records", []):
        if record.get("record_type") == "formal_item" and not record.get("exact_text_de"):
            record["exact_text_de"] = record.get("exact_source_text")
            flags = record.setdefault("review_flags", [])
            if "exact_text_de_recovered_from_exact_source_text" not in flags:
                flags.append("exact_text_de_recovered_from_exact_source_text")
        if record.get("record_type") == "formal_item" and not record.get("source_item_number"):
            # source_identifier is already restricted by the prompt to a
            # visibly printed item/chapter/table identifier. For a formal item
            # it is the same visible locator; retain an explicit audit flag.
            source_identifier = record.get("source_identifier")
            if source_identifier:
                record["source_item_number"] = source_identifier
                flags = record.setdefault("review_flags", [])
                if "source_item_number_recovered_from_source_identifier" not in flags:
                    flags.append("source_item_number_recovered_from_source_identifier")
        if record.get("record_type") == "dosing_rule" and not (
            record.get("product_name") or record.get("active_substance_names")
        ):
            entities = record.get("normalized_entities") or []
            if entities:
                record["active_substance_names"] = [entities[0]]
            else:
                record["active_substance_names"] = ["Wirkstoff im unmittelbaren Quellkontext nicht wiederholt"]
            flags = record.setdefault("review_flags", [])
            if "dose_entity_recovered_from_immediate_context_or_flagged" not in flags:
                flags.append("dose_entity_recovered_from_immediate_context_or_flagged")
        if record.get("record_type") == "adverse_reaction" and not record.get("adverse_reaction_term"):
            # Frequency-table rows are occasionally emitted with the complete
            # verbatim row but without duplicating the term in its atomic field.
            # Preserve the quote and flag the deterministic recovery.
            record["adverse_reaction_term"] = record.get("exact_source_text")
            flags = record.setdefault("review_flags", [])
            if "adverse_reaction_term_recovered_from_exact_source_text" not in flags:
                flags.append("adverse_reaction_term_recovered_from_exact_source_text")
    return ExtractionEnvelope.model_validate(payload)


def validate_envelope(envelope: ExtractionEnvelope, batch: Batch) -> list[str]:
    errors_found: list[str] = []
    expected = {
        "source_id": batch.source_id,
        "source_file_name": batch.source_file_name,
        "document_type": batch.document_type,
        "task_family": batch.task_family,
    }
    for field_name, expected_value in expected.items():
        if getattr(envelope, field_name) != expected_value:
            errors_found.append(f"{field_name}: expected {expected_value!r}, got {getattr(envelope, field_name)!r}")
    if envelope.request_pdf_pages_1based != list(batch.request_pages):
        errors_found.append("request_pdf_pages_1based mismatch")
    if envelope.owner_pdf_pages_1based != list(batch.owner_pages):
        errors_found.append("owner_pdf_pages_1based mismatch")
    assessment_pages = [assessment.pdf_page_1based for assessment in envelope.page_assessments]
    if sorted(assessment_pages) != sorted(batch.owner_pages) or len(assessment_pages) != len(set(assessment_pages)):
        errors_found.append(f"page_assessments must cover owner pages exactly once: {assessment_pages}")
    allowed_pages = set(batch.owner_pages)
    for index, record in enumerate(envelope.records):
        if not set(record.pdf_pages_1based).issubset(allowed_pages):
            errors_found.append(f"record {index} contains non-owner page(s): {record.pdf_pages_1based}")
        if not record.exact_source_text.strip():
            errors_found.append(f"record {index} has empty exact_source_text")
        if not record.semantic_summary_de.strip():
            errors_found.append(f"record {index} has empty semantic_summary_de")
    return errors_found


def is_retryable(exc: BaseException) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code in {400, 403}:
        return False
    if code in {408, 429} or (isinstance(code, int) and 500 <= code <= 599):
        return True
    message = str(exc).casefold()
    return any(marker in message for marker in [" 408 ", " 429 ", " 500 ", " 502 ", " 503 ", " 504 ", "timeout"])


def is_hard_quota_or_billing(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in [
            "billing",
            "insufficient quota",
            "quota exhausted",
            "hard limit",
            "resource_exhausted",
            "payment required",
        ]
    )


def is_transient_capacity_failure(exc: BaseException) -> bool:
    message = str(exc).casefold()
    if "returned empty content twice" in message:
        return True
    return ("503" in message or "unavailable" in message) and any(
        marker in message for marker in ["high demand", "temporarily unavailable", "try again later"]
    )


def generate_once(client: genai.Client, pdf_path: Path, prompt: str, task_family: str) -> str:
    pdf_part = types.Part.from_bytes(data=pdf_path.read_bytes(), mime_type="application/pdf")
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[pdf_part, prompt],
        config=types.GenerateContentConfig(
            temperature=0,
            seed=0,
            response_mime_type="application/json",
            # Full JSON Schema is required here because the canonical Pydantic
            # schema intentionally forbids extras and therefore uses
            # additionalProperties, which the narrower OpenAPI response_schema
            # transport cannot represent on the Gemini Developer API.
            response_json_schema=family_response_json_schema(task_family),
            max_output_tokens=32768,
        ),
    )
    return response.text or ""


def generate_with_http_retries(
    client: genai.Client,
    pdf_path: Path,
    prompt: str,
    api_key: str,
    batch_id: str,
    task_family: str,
    log_path: Path,
) -> str:
    delays = [2.0, 4.0, 8.0]
    for attempt in range(1, 5):
        started = time.monotonic()
        try:
            result = generate_once(client, pdf_path, prompt, task_family)
            append_jsonl(
                log_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "gemini_call_succeeded",
                    "batch_id": batch_id,
                    "attempt": attempt,
                    "model_name": MODEL_NAME,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                },
            )
            return result
        except (errors.ClientError, errors.ServerError, httpx.TimeoutException, TimeoutError, OSError) as exc:
            code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            diagnosis = safe_error(exc, api_key)
            append_jsonl(
                log_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "gemini_call_failed",
                    "batch_id": batch_id,
                    "attempt": attempt,
                    "status_code": code,
                    "retryable": is_retryable(exc),
                    "diagnosis": diagnosis,
                },
            )
            if code in {400, 403}:
                raise FatalGeminiError(f"Gemini HTTP {code}; no retry: {diagnosis}") from exc
            if is_hard_quota_or_billing(exc):
                raise FatalGeminiError(f"Gemini hard quota/billing blocker: {diagnosis}") from exc
            if attempt == 4 or not is_retryable(exc):
                raise FatalGeminiError(f"Gemini request failed after allowed retries: {diagnosis}") from exc
            jitter = random.Random(f"{batch_id}-{attempt}").uniform(0.0, 0.8)
            time.sleep(delays[attempt - 1] + jitter)
    raise AssertionError("unreachable")


def process_batch(
    client: genai.Client,
    api_key: str,
    project_root: Path,
    output_root: Path,
    source: dict[str, Any],
    page_infos: list[PageInfo],
    batch: Batch,
    transient_round: int = 1,
) -> dict[str, Any]:
    checkpoint_path = output_root / "checkpoints/validated" / f"{batch.batch_id}.json"
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("validation_status") == "valid" and checkpoint.get("batch_id") == batch.batch_id:
            return checkpoint

    failed_path = output_root / "checkpoints/failed" / f"{batch.batch_id}.json"
    if failed_path.exists() and not batch.task_family.endswith("_preflight"):
        failed_checkpoint = json.loads(failed_path.read_text(encoding="utf-8"))
        if failed_checkpoint.get("validation_status") in {
            "invalid_after_repair",
            "request_failed_after_retries",
            "recovered_by_shrinking",
        }:
            # The requested failure policy is repair once, then shrink the
            # mini-batch. On resume, do not waste another call on a parent that
            # has already demonstrated that it needs the narrower path.
            return recover_batch_by_shrinking(
                client,
                api_key,
                project_root,
                output_root,
                source,
                page_infos,
                batch,
                failure_reason="previously_invalid_after_repair",
            )

    source_path = project_root / source["relative_path"]
    log_path = output_root / "logs/gemini_calls.jsonl"
    with tempfile.TemporaryDirectory(prefix="knowledge-corpus-batch-", dir="/tmp") as temp_dir:
        mini_pdf = Path(temp_dir) / f"{batch.batch_id}.pdf"
        write_batch_pdf(source_path, batch.request_pages, mini_pdf)
        mini_pdf_hash = sha256_file(mini_pdf)
        raw_text = ""
        validation_messages: list[str] = []
        envelope: ExtractionEnvelope | None = None
        for schema_attempt in range(2):
            repair_note = "; ".join(validation_messages[-5:]) if schema_attempt else None
            prompt = build_prompt(batch, repair_note=repair_note)
            try:
                raw_text = generate_with_http_retries(
                    client, mini_pdf, prompt, api_key, batch.batch_id, batch.task_family, log_path
                )
            except FatalGeminiError as exc:
                if (
                    is_transient_capacity_failure(exc)
                    and not batch.task_family.endswith("_preflight")
                    and _narrower_batches(batch)
                ):
                    atomic_write_json(
                        failed_path,
                        {
                            "batch_id": batch.batch_id,
                            "validation_status": "request_failed_after_retries",
                            "source_id": batch.source_id,
                            "task_family": batch.task_family,
                            "request_pages": list(batch.request_pages),
                            "owner_pages": list(batch.owner_pages),
                            "model_name": MODEL_NAME,
                            "prompt_version": PROMPT_VERSION,
                            "schema_version": SCHEMA_VERSION,
                            "diagnosis": safe_error(exc, api_key),
                            "failed_at_utc": utc_now(),
                        },
                    )
                    return recover_batch_by_shrinking(
                        client,
                        api_key,
                        project_root,
                        output_root,
                        source,
                        page_infos,
                        batch,
                        failure_reason="http_503_after_allowed_retries",
                    )
                raise
            try:
                candidate = parse_response_text(raw_text)
                validation_messages = validate_envelope(candidate, batch)
                if not validation_messages:
                    envelope = candidate
                    break
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                validation_messages = [safe_error(exc)]
            append_jsonl(
                output_root / "logs/schema_repairs.jsonl",
                {
                    "timestamp_utc": utc_now(),
                    "batch_id": batch.batch_id,
                    "schema_attempt": schema_attempt + 1,
                    "validation_messages": validation_messages,
                },
            )

        if envelope is None:
            failed = {
                "batch_id": batch.batch_id,
                "validation_status": "invalid_after_repair",
                "source_id": batch.source_id,
                "task_family": batch.task_family,
                "request_pages": list(batch.request_pages),
                "owner_pages": list(batch.owner_pages),
                "model_name": MODEL_NAME,
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "validation_messages": validation_messages,
                "raw_response_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                "failed_at_utc": utc_now(),
            }
            atomic_write_json(failed_path, failed)
            if not batch.task_family.endswith("_preflight"):
                return recover_batch_by_shrinking(
                    client,
                    api_key,
                    project_root,
                    output_root,
                    source,
                    page_infos,
                    batch,
                    failure_reason=(
                        "empty_content_after_repair"
                        if raw_text == ""
                        else "schema_invalid_after_repair"
                    ),
                )
            if raw_text == "":
                raise FatalGeminiError(
                    f"Gemini returned empty content twice for batch {batch.batch_id}; transient generation failure"
                )
            raise PipelineError(
                f"Batch {batch.batch_id} remained invalid after one repair: {validation_messages}"
            )

    page_text_by_number = {info.page: info.text for info in page_infos}
    records = []
    for index, record in enumerate(envelope.records):
        payload = record.model_dump(mode="json")
        locally_verified = quote_locally_verifiable(
            record.exact_source_text,
            [page_text_by_number[page] for page in record.pdf_pages_1based],
        )
        if not locally_verified and "quote_not_locally_verified" not in payload["review_flags"]:
            payload["review_flags"].append("quote_not_locally_verified")
        payload["_response_order"] = index
        payload["_quote_locally_verified"] = locally_verified
        records.append(payload)

    checkpoint = {
        "batch_id": batch.batch_id,
        "validation_status": "valid",
        "validated_at_utc": utc_now(),
        "source_id": batch.source_id,
        "source_file_name": batch.source_file_name,
        "source_sha256": batch.source_sha256,
        "document_type": batch.document_type,
        "task_family": batch.task_family,
        "request_pdf_pages_1based": list(batch.request_pages),
        "owner_pdf_pages_1based": list(batch.owner_pages),
        "model_name": MODEL_NAME,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "mini_pdf_sha256": mini_pdf_hash,
        "response_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "page_assessments": [item.model_dump(mode="json") for item in envelope.page_assessments],
        "records": records,
        "remote_file_used": False,
    }
    atomic_write_json(checkpoint_path, checkpoint)
    append_jsonl(
        output_root / "logs/checkpoints.jsonl",
        {
            "timestamp_utc": utc_now(),
            "event": "validated_checkpoint_written",
            "batch_id": batch.batch_id,
            "record_count": len(records),
        },
    )
    return checkpoint


def _narrower_batches(batch: Batch) -> list[Batch]:
    """Return deterministic, strictly smaller batches for a failed request."""
    if len(batch.owner_pages) > 1:
        children: list[Batch] = []
        request_set = set(batch.request_pages)
        for owner_page in batch.owner_pages:
            context_page: int | None = None
            if owner_page - 1 in request_set:
                context_page = owner_page - 1
            elif owner_page + 1 in request_set:
                context_page = owner_page + 1
            request_pages = tuple(
                sorted({owner_page} | ({context_page} if context_page is not None else set()))
            )
            children.append(
                Batch(
                    source_id=batch.source_id,
                    source_file_name=batch.source_file_name,
                    source_sha256=batch.source_sha256,
                    document_type=batch.document_type,
                    request_pages=request_pages,
                    owner_pages=(owner_page,),
                    task_family=batch.task_family,
                )
            )
        return children
    if tuple(batch.request_pages) != tuple(batch.owner_pages):
        return [
            Batch(
                source_id=batch.source_id,
                source_file_name=batch.source_file_name,
                source_sha256=batch.source_sha256,
                document_type=batch.document_type,
                request_pages=tuple(batch.owner_pages),
                owner_pages=tuple(batch.owner_pages),
                task_family=batch.task_family,
            )
        ]
    return []


def recover_batch_by_shrinking(
    client: genai.Client,
    api_key: str,
    project_root: Path,
    output_root: Path,
    source: dict[str, Any],
    page_infos: list[PageInfo],
    batch: Batch,
    failure_reason: str,
) -> dict[str, Any]:
    """Extract smaller child PDFs and atomically materialize the parent checkpoint.

    Child checkpoints retain their own stable IDs for auditability. The merged
    parent remains the sole canonical plan checkpoint, so resume and downstream
    provenance stay stable even though the source request was narrowed.
    """
    children = _narrower_batches(batch)
    if not children:
        if failure_reason == "empty_content_after_repair":
            raise FatalGeminiError(
                f"Gemini returned empty content twice for minimum-size batch {batch.batch_id}"
            )
        raise PipelineError(
            f"Minimum-size batch {batch.batch_id} remained invalid after one repair"
        )

    append_jsonl(
        output_root / "logs/checkpoints.jsonl",
        {
            "timestamp_utc": utc_now(),
            "event": "batch_shrink_started",
            "batch_id": batch.batch_id,
            "failure_reason": failure_reason,
            "child_batch_ids": [child.batch_id for child in children],
            "model_name": MODEL_NAME,
        },
    )
    child_checkpoints = [
        process_batch(
            client,
            api_key,
            project_root,
            output_root,
            source,
            page_infos,
            child,
        )
        for child in children
    ]

    records: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    for child_checkpoint in child_checkpoints:
        assessments.extend(child_checkpoint["page_assessments"])
        for raw in child_checkpoint["records"]:
            record = dict(raw)
            record["_response_order"] = len(records)
            records.append(record)

    assessment_pages = [item["pdf_page_1based"] for item in assessments]
    if sorted(assessment_pages) != sorted(batch.owner_pages) or len(assessment_pages) != len(
        set(assessment_pages)
    ):
        raise PipelineError(
            f"Shrunk child assessments do not cover parent {batch.batch_id} exactly: {assessment_pages}"
        )
    owner_set = set(batch.owner_pages)
    for index, record in enumerate(records):
        clean_record = {key: value for key, value in record.items() if not key.startswith("_")}
        ExtractedRecord.model_validate(clean_record)
        if not set(record["pdf_pages_1based"]).issubset(owner_set):
            raise PipelineError(
                f"Shrunk record {index} has a page outside parent {batch.batch_id}"
            )

    checkpoint = {
        "batch_id": batch.batch_id,
        "validation_status": "valid",
        "validated_at_utc": utc_now(),
        "source_id": batch.source_id,
        "source_file_name": batch.source_file_name,
        "source_sha256": batch.source_sha256,
        "document_type": batch.document_type,
        "task_family": batch.task_family,
        "request_pdf_pages_1based": list(batch.request_pages),
        "owner_pdf_pages_1based": list(batch.owner_pages),
        "model_name": MODEL_NAME,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "mini_pdf_sha256": "composite-" + stable_hash(
            [item.get("mini_pdf_sha256") for item in child_checkpoints]
        ),
        "response_sha256": "composite-" + stable_hash(
            [item.get("response_sha256") for item in child_checkpoints]
        ),
        "page_assessments": sorted(assessments, key=lambda item: item["pdf_page_1based"]),
        "records": records,
        "remote_file_used": False,
        "split_recovery": {
            "failure_reason": failure_reason,
            "child_batch_ids": [child.batch_id for child in children],
            "child_request_pdf_pages_1based": [list(child.request_pages) for child in children],
        },
    }
    checkpoint_path = output_root / "checkpoints/validated" / f"{batch.batch_id}.json"
    atomic_write_json(checkpoint_path, checkpoint)

    failed_path = output_root / "checkpoints/failed" / f"{batch.batch_id}.json"
    failed = json.loads(failed_path.read_text(encoding="utf-8")) if failed_path.exists() else {}
    failed.update(
        {
            "batch_id": batch.batch_id,
            "validation_status": "recovered_by_shrinking",
            "recovered_at_utc": utc_now(),
            "child_batch_ids": [child.batch_id for child in children],
        }
    )
    atomic_write_json(failed_path, failed)
    append_jsonl(
        output_root / "logs/checkpoints.jsonl",
        {
            "timestamp_utc": utc_now(),
            "event": "batch_shrink_recovered",
            "batch_id": batch.batch_id,
            "child_batch_ids": [child.batch_id for child in children],
            "record_count": len(records),
        },
    )
    return checkpoint


def run_smoke_test(client: genai.Client, output_root: Path) -> dict[str, Any]:
    path = output_root / "qa/gemini_smoke_test.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("passed") is True and existing.get("model_name") == MODEL_NAME:
            return existing
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents="Return exactly PASS.",
        config=types.GenerateContentConfig(temperature=0, seed=0),
    )
    actual = (response.text or "").strip()
    result = {
        "model_name": MODEL_NAME,
        "expected": "PASS",
        "actual": actual,
        "passed": actual == "PASS",
        "completed_at_utc": utc_now(),
    }
    atomic_write_json(path, result)
    if not result["passed"]:
        raise FatalGeminiError(f"Gemini smoke test expected exact PASS, got {actual!r}")
    return result


def select_guideline_preflight_candidates(
    sources: list[dict[str, Any]], infos_by_source: dict[str, list[PageInfo]]
) -> list[tuple[dict[str, Any], tuple[int, int, int]]]:
    scored: list[tuple[int, str, int, dict[str, Any], tuple[int, int, int]]] = []
    for source in sources:
        if source["document_type"] != "guideline":
            continue
        infos = infos_by_source[source["source_id"]]
        for start in range(1, len(infos) - 1):
            triple = infos[start - 1 : start + 2]
            if any(info.status in {"blank", "front_matter", "table_of_contents", "references"} for info in triple):
                continue
            if not any(info.primary_family == "guideline_formal_package" for info in triple):
                continue
            formal_count = sum(info.formal_candidate for info in triple)
            visual_count = sum(info.visual_candidate for info in triple)
            clinical_markers = sum(
                bool(re.search(r"(?i)patient|therap|diagnos|prophylax|behandlung|karzinom|thromb", info.text))
                for info in triple
            )
            score = formal_count * 20 + visual_count * 8 + clinical_markers * 3 + sum(info.dense for info in triple)
            if formal_count:
                pages = (start, start + 1, start + 2)
                scored.append((score, source["source_id"], start, source, pages))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(item[3], item[4]) for item in scored[:8]]


def select_drug_preflight_candidates(
    sources: list[dict[str, Any]], infos_by_source: dict[str, list[PageInfo]]
) -> list[tuple[dict[str, Any], tuple[int, int, int]]]:
    scored: list[tuple[int, str, int, dict[str, Any], tuple[int, int, int]]] = []
    for source in sources:
        if source["document_type"] != "drug_label":
            continue
        infos = infos_by_source[source["source_id"]]
        for start in range(1, len(infos) - 1):
            triple = infos[start - 1 : start + 2]
            if any(not info.canonical or info.status == "blank" for info in triple):
                continue
            joined = "\n".join(info.text for info in triple)
            dose_markers = len(
                re.findall(r"(?i)\b\d+(?:[,.]\d+)?\s*(?:mg|µg|mikrogramm|g|ml|i\.e\.|einheiten)(?:/\w+)?\b", joined)
            )
            section_score = 20 if re.search(r"(?i)4\.2\s+(?:dosierung|art der anwendung)", joined) else 0
            table_score = 8 if any(info.visual_candidate for info in triple) else 0
            product_score = 5 if re.search(r"(?i)bezeichnung des arzneimittels|wirkstoff", joined) else 0
            # Prefer the compact national SmPCs for preflight: they still test
            # full dose fidelity but avoid an enormous EMA multi-strength table
            # overflowing the structured response during this gate.
            compact_bonus = 30 if source["page_count"] <= 20 else 0
            score = section_score + table_score + product_score + min(dose_markers, 20) + compact_bonus
            if section_score and dose_markers:
                pages = (start, start + 1, start + 2)
                scored.append((score, source["source_id"], start, source, pages))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(item[3], item[4]) for item in scored[:8]]


def evaluate_preflight(
    checkpoint: dict[str, Any],
    source: dict[str, Any],
    infos: list[PageInfo],
    pages: tuple[int, int, int],
    document_type: str,
) -> PreflightResult:
    info_by_page = {info.page: info for info in infos}
    records = checkpoint["records"]
    checks: dict[str, bool] = {
        "schema_conformant": checkpoint.get("validation_status") == "valid",
        "source_identity_exact": checkpoint.get("source_id") == source["source_id"],
        "original_page_mapping_exact": sorted(checkpoint.get("owner_pdf_pages_1based", [])) == list(pages),
        "all_pages_assessed": sorted(item["pdf_page_1based"] for item in checkpoint["page_assessments"]) == list(pages),
        "nonempty_source_quotes": bool(records) and all(bool(item.get("exact_source_text", "").strip()) for item in records),
    }
    verified = [item.get("_quote_locally_verified", False) for item in records]
    checks["quotes_locally_verified"] = bool(verified) and sum(verified) / len(verified) >= 0.8
    notes: list[str] = []

    visible_visual = any(info_by_page[page].visual_candidate for page in pages)
    if document_type == "guideline":
        formal_records = [item for item in records if item["record_type"] == "formal_item"]
        checks["formal_item_extracted"] = bool(formal_records)
        checks["formal_item_exact_text"] = bool(formal_records) and all(
            item.get("exact_text_de")
            and quote_locally_verifiable(
                item["exact_text_de"],
                [info_by_page[page].text for page in item["pdf_pages_1based"]],
            )
            for item in formal_records
        )
        checks["formal_item_identifier"] = bool(formal_records) and all(
            bool(item.get("source_item_number") or item.get("source_identifier")) for item in formal_records
        )
        if visible_visual:
            checks["visible_table_or_layout_understood"] = any(
                item["record_type"] == "table_figure_algorithm"
                and (item.get("table_rows") or item.get("algorithm_nodes") or item.get("caption"))
                for item in records
            )
    else:
        dosing = [item for item in records if item["record_type"] == "dosing_rule"]
        checks["dosing_rule_extracted"] = bool(dosing)
        checks["dose_values_and_units"] = bool(dosing) and any(
            item.get("dose_value") and item.get("dose_unit") for item in dosing
        )
        checks["product_or_active_substance_extracted"] = any(
            item["record_type"] in {"drug_product", "active_substance"} for item in records
        )
        checks["dose_source_quote_contains_unit"] = bool(dosing) and all(
            bool(re.search(r"(?i)\b(?:mg|µg|mikrogramm|g|ml|i\.e\.|einheiten)\b", item["exact_source_text"]))
            or "dose_value_not_explicit" in item.get("review_flags", [])
            for item in dosing
        )
        if visible_visual:
            checks["visible_table_or_layout_understood"] = any(
                item["record_type"] == "table_figure_algorithm" and item.get("table_rows") for item in records
            )

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        notes.append("Fehlgeschlagene Checks: " + ", ".join(failed))
    return PreflightResult(
        preflight_id="preflight-" + stable_hash(source["source_id"], pages, document_type),
        document_type=document_type,
        source_id=source["source_id"],
        pages=list(pages),
        task_family=f"{document_type}_preflight",
        passed=not failed,
        checks=checks,
        notes=notes,
    )


def run_preflights(
    client: genai.Client,
    api_key: str,
    project_root: Path,
    output_root: Path,
    sources: list[dict[str, Any]],
    infos_by_source: dict[str, list[PageInfo]],
) -> list[dict[str, Any]]:
    result_path = output_root / "qa/preflight_results.json"
    if result_path.exists():
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        if (
            existing.get("model_name") == MODEL_NAME
            and existing.get("prompt_version") == PROMPT_VERSION
            and len(existing.get("results", [])) == 2
            and all(item.get("passed") for item in existing["results"])
        ):
            return existing["results"]

    source_lookup = {source["source_id"]: source for source in sources}
    all_results: list[dict[str, Any]] = []
    selections = [
        ("guideline", select_guideline_preflight_candidates(sources, infos_by_source)),
        ("drug_label", select_drug_preflight_candidates(sources, infos_by_source)),
    ]
    for document_type, candidates in selections:
        if not candidates:
            raise PipelineError(f"No suitable three-page {document_type} preflight candidate found")
        passed_result: dict[str, Any] | None = None
        attempted: list[dict[str, Any]] = []
        for source, pages in candidates[:3]:
            task_family = "guideline_preflight" if document_type == "guideline" else "drug_preflight"
            batch = Batch(
                source_id=source["source_id"],
                source_file_name=source["original_file_name"],
                source_sha256=source["sha256"],
                document_type=document_type,
                request_pages=pages,
                owner_pages=pages,
                task_family=task_family,
            )
            checkpoint = process_batch(
                client,
                api_key,
                project_root,
                output_root,
                source_lookup[source["source_id"]],
                infos_by_source[source["source_id"]],
                batch,
            )
            evaluation = evaluate_preflight(
                checkpoint, source, infos_by_source[source["source_id"]], pages, document_type
            ).model_dump(mode="json")
            attempted.append(evaluation)
            if evaluation["passed"]:
                passed_result = evaluation
                break
        if passed_result is None:
            all_results.extend(attempted)
            atomic_write_json(
                result_path,
                {
                    "model_name": MODEL_NAME,
                    "prompt_version": PROMPT_VERSION,
                    "completed_at_utc": utc_now(),
                    "results": all_results,
                },
            )
            raise PipelineError(f"{document_type} preflight failed: {attempted}")
        all_results.append(passed_result)

    atomic_write_json(
        result_path,
        {
            "model_name": MODEL_NAME,
            "prompt_version": PROMPT_VERSION,
            "completed_at_utc": utc_now(),
            "results": all_results,
        },
    )
    return all_results


def citation_label(source_file_name: str, pages: Sequence[int], identifier: str | None) -> str:
    page_label = ", ".join(str(page) for page in sorted(set(pages)))
    suffix = f", {identifier}" if identifier else ""
    return f"{source_file_name}, PDF-S. {page_label}{suffix}"


def checkpoint_records(
    checkpoints: Sequence[dict[str, Any]], sources_by_id: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        if checkpoint["task_family"].endswith("_preflight"):
            continue
        for raw in checkpoint["records"]:
            internal_order = raw.get("_response_order", 0)
            quote_verified = raw.get("_quote_locally_verified", False)
            clean_raw = {key: value for key, value in raw.items() if not key.startswith("_")}
            validated = ExtractedRecord.model_validate(clean_raw)
            payload = validated.model_dump(mode="json")
            if payload["record_type"] == "formal_item" and not payload.get("source_item_number"):
                source_identifier = payload.get("source_identifier")
                if source_identifier:
                    payload["source_item_number"] = source_identifier
                    payload["review_flags"] = sorted(
                        set(payload.get("review_flags") or [])
                        | {"source_item_number_recovered_from_source_identifier"}
                    )
            identifier_component: Any = payload.get("source_identifier")
            if payload["record_type"] == "formal_item":
                identifier_component = payload.get("source_item_number") or (
                    payload.get("source_identifier"), internal_order
                )
            record_id = "rec-" + stable_hash(
                checkpoint["source_id"],
                payload["record_type"],
                identifier_component,
                payload["pdf_pages_1based"],
                text_hash(payload["exact_source_text"]),
            )
            record = {
                "record_id": record_id,
                **payload,
                "source_id": checkpoint["source_id"],
                "document_type": checkpoint["document_type"],
                "source_file_name": checkpoint["source_file_name"],
                "source_sha256": checkpoint["source_sha256"],
                "citation_label": citation_label(
                    checkpoint["source_file_name"], payload["pdf_pages_1based"], payload.get("source_identifier")
                ),
                "extraction_batch_id": checkpoint["batch_id"],
                "model_name": checkpoint["model_name"],
                "prompt_version": checkpoint["prompt_version"],
                "schema_version": checkpoint["schema_version"],
                "quote_locally_verified": quote_verified,
                "_response_order": internal_order,
            }
            if payload["record_type"] == "formal_item":
                record["formal_item_id"] = "formal-" + stable_hash(record_id)
            if payload["record_type"] == "table_figure_algorithm":
                page = min(payload["pdf_pages_1based"])
                record["local_render_reference"] = (
                    f"rendered_sources/{checkpoint['source_id']}/page-{page:04d}.png"
                )
            candidates.append(record)

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in candidates:
        identifier = record.get("source_item_number") if record["record_type"] == "formal_item" else record.get("source_identifier")
        if record["record_type"] == "formal_item" and not identifier:
            identifier = (record["extraction_batch_id"], record["_response_order"])
        key = (
            record["source_id"],
            identifier,
            tuple(record["pdf_pages_1based"]),
            text_hash(record["exact_source_text"]),
            record["record_type"],
        )
        groups[key].append(record)

    deduplicated: list[dict[str, Any]] = []
    duplicate_report: list[dict[str, Any]] = []
    for key, records in groups.items():
        records.sort(key=lambda item: (-item["confidence"], item["extraction_batch_id"], item["_response_order"]))
        winner = records[0]
        winner["review_flags"] = sorted(set(winner["review_flags"]))
        deduplicated.append(winner)
        if len(records) > 1:
            duplicate_report.append(
                {
                    "deduplication_key": stable_hash(key),
                    "kept_record_id": winner["record_id"],
                    "removed_record_ids": [item["record_id"] for item in records[1:]],
                    "record_type": winner["record_type"],
                    "source_id": winner["source_id"],
                    "reason": "Deterministischer Schlüssel aus Quelle, Identifier, Seiten, Text-Hash und Record-Typ",
                }
            )

    deduplicated.sort(
        key=lambda item: (
            item["source_id"],
            min(item["pdf_pages_1based"]),
            item["_response_order"],
            item["record_type"],
            item["record_id"],
        )
    )
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in deduplicated:
        if record["record_type"] == "formal_item":
            by_source[record["source_id"]].append(record)
    for source_records in by_source.values():
        source_records.sort(
            key=lambda item: (min(item["pdf_pages_1based"]), item["_response_order"], item["record_id"])
        )
        for order, record in enumerate(source_records, start=1):
            record["document_order"] = order
    for record in deduplicated:
        record.pop("_response_order", None)
    return deduplicated, duplicate_report


CANONICAL_FILE_MAP: dict[str, str] = {
    "grading_system": "grading_systems.jsonl",
    "formal_item": "formal_items.jsonl",
    "rationale_block": "rationale_blocks.jsonl",
    "guideline_reference": "guideline_references.jsonl",
    "table_figure_algorithm": "tables_figures_algorithms.jsonl",
    "drug_product": "drug_products.jsonl",
    "active_substance": "active_substances.jsonl",
    "composition": "compositions.jsonl",
    "therapeutic_indication": "therapeutic_indications.jsonl",
    "dosing_rule": "dosing_rules.jsonl",
    "preparation_administration": "preparation_administration.jsonl",
    "contraindication": "contraindications.jsonl",
    "warning": "warnings.jsonl",
    "interaction": "interactions.jsonl",
    "pregnancy_lactation_fertility": "pregnancy_lactation_fertility.jsonl",
    "adverse_reaction": "adverse_reactions.jsonl",
    "overdose": "overdose.jsonl",
    "pharmacodynamics": "pharmacodynamics.jsonl",
    "pharmacokinetics": "pharmacokinetics.jsonl",
    "excipient": "excipients.jsonl",
    "incompatibility": "incompatibilities.jsonl",
    "storage_handling": "storage_handling.jsonl",
    "regulatory_metadata": "regulatory_metadata.jsonl",
    "chapter_structure": "chapter_structure.jsonl",
    "document_metadata": "document_metadata_extracted.jsonl",
}


def _entity_name(record: dict[str, Any], entity_type: str) -> str | None:
    if entity_type == "product":
        return record.get("product_name") or record.get("original_product_name")
    names = record.get("active_substance_names") or record.get("active_substance_original_names") or []
    return names[0] if names else record.get("component_name")


def assign_entity_ids(records: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    product_ids: dict[str, str] = {}
    active_ids: dict[str, str] = {}
    for record in records:
        product_name = record.get("product_name") or record.get("original_product_name")
        if product_name:
            product_key = normalize_text(
                " | ".join(
                    value
                    for value in [product_name, record.get("strength"), record.get("pharmaceutical_form")]
                    if value
                )
            )
            product_ids.setdefault(product_key, "product-" + stable_hash(product_key))
            record["product_id"] = product_ids[product_key]
        else:
            record["product_id"] = None

        names = list(record.get("active_substance_names") or []) + list(
            record.get("active_substance_original_names") or []
        )
        if record["record_type"] == "active_substance" and not names and record.get("component_name"):
            names.append(record["component_name"])
        ids: list[str] = []
        for name in names:
            key = normalize_text(name)
            if not key:
                continue
            active_ids.setdefault(key, "substance-" + stable_hash(key))
            ids.append(active_ids[key])
        record["active_substance_ids"] = sorted(set(ids))
    return product_ids, active_ids


def mark_active_substance_identity_evidence(records: list[dict[str, Any]]) -> None:
    """Mark source-explicit primary-substance evidence without inventing identities.

    Some product-information batches correctly emitted section 2 as a
    ``composition`` record but did not duplicate it as an ``active_substance``
    record.  The exact composition quotation is still authoritative evidence.
    This deterministic pass only accepts substance names that Gemini emitted
    elsewhere for the same source *and* that occur verbatim (after layout
    normalization) in that composition quotation.
    """

    placeholder = "Wirkstoff im unmittelbaren Quellkontext nicht wiederholt"
    candidates_by_source: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for record in records:
        if record.get("document_type") != "drug_label":
            continue
        names = list(record.get("active_substance_names") or []) + list(
            record.get("active_substance_original_names") or []
        )
        if record.get("record_type") == "active_substance" and record.get("component_name"):
            names.append(record["component_name"])
        for name in names:
            normalized = normalize_text(name)
            if normalized and name != placeholder and len(normalized) >= 4:
                candidates_by_source[record["source_id"]][normalized].add(name)

    for record in records:
        if record.get("document_type") != "drug_label":
            continue
        if record.get("record_type") == "active_substance":
            identity_names = list(record.get("active_substance_names") or []) + list(
                record.get("active_substance_original_names") or []
            )
            if not identity_names and record.get("component_name"):
                identity_names.append(record["component_name"])
            record["active_substance_identity_names"] = sorted(
                {name for name in identity_names if name and name != placeholder}, key=str.casefold
            )
            continue
        if record.get("record_type") != "composition":
            continue

        normalized_quote = normalize_text(record.get("exact_source_text") or "")
        matched_names: set[str] = set()
        for normalized_name, originals in candidates_by_source.get(record["source_id"], {}).items():
            pattern = rf"(?<!\w){re.escape(normalized_name)}(?!\w)"
            if re.search(pattern, normalized_quote):
                matched_names.update(originals)
        if not matched_names:
            continue

        existing_names = {
            name
            for name in list(record.get("active_substance_names") or [])
            + list(record.get("active_substance_original_names") or [])
            if name and name != placeholder
        }
        recovered_names = matched_names - existing_names
        if recovered_names:
            record["active_substance_names"] = sorted(existing_names | recovered_names, key=str.casefold)
            record["review_flags"] = sorted(
                set(record.get("review_flags") or [])
                | {"active_substance_identity_recovered_from_exact_composition_quote"}
            )
        record["active_substance_identity_names"] = sorted(matched_names, key=str.casefold)


def assign_indication_ids(records: list[dict[str, Any]]) -> dict[str, str]:
    """Assign conservative IDs only to source-explicit indication wording."""
    indication_ids: dict[str, str] = {}
    for record in records:
        original_values: list[str] = []
        if record.get("indication"):
            original_values.append(record["indication"])
        original_values.extend(record.get("indications") or [])
        if record["record_type"] == "therapeutic_indication" and not original_values:
            original_values.append(record["exact_source_text"])
        ids: list[str] = []
        for value in original_values:
            key = normalize_text(value)
            if not key:
                continue
            indication_ids.setdefault(key, "indication-" + stable_hash(key))
            ids.append(indication_ids[key])
        record["indication_ids"] = sorted(set(ids))
        record["indication_id"] = record["indication_ids"][0] if len(record["indication_ids"]) == 1 else None
    return indication_ids


def consolidate_entity_records(
    records: list[dict[str, Any]], record_type: str, id_field: str
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["record_type"] != record_type:
            continue
        ids: list[str]
        if id_field == "active_substance_ids":
            ids = record.get(id_field) or []
        else:
            ids = [record[id_field]] if record.get(id_field) else []
        for entity_id in ids:
            groups[entity_id].append(record)
    consolidated: list[dict[str, Any]] = []
    for entity_id, members in sorted(groups.items()):
        members.sort(key=lambda item: (item["source_id"], min(item["pdf_pages_1based"]), item["record_id"]))
        base = dict(members[0])
        base[id_field] = entity_id if id_field == "product_id" else [entity_id]
        base["evidence_record_ids"] = [member["record_id"] for member in members]
        base["source_ids"] = sorted({member["source_id"] for member in members})
        aliases: set[str] = set()
        for member in members:
            if record_type == "drug_product":
                aliases.update(
                    value
                    for value in [member.get("product_name"), member.get("original_product_name")]
                    if value
                )
            else:
                aliases.update(member.get("active_substance_names") or [])
                aliases.update(member.get("active_substance_original_names") or [])
        base["aliases_original"] = sorted(aliases, key=str.casefold)
        consolidated.append(base)
    return consolidated


def consolidate_active_substance_records(
    records: list[dict[str, Any]], active_ids: dict[str, str]
) -> list[dict[str, Any]]:
    """Create one canonical entity per source-explicit normalized substance name."""

    groups: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for record in records:
        for name in record.get("active_substance_identity_names") or []:
            entity_id = active_ids.get(normalize_text(name))
            if entity_id:
                groups[entity_id].append((record, name))

    consolidated: list[dict[str, Any]] = []
    for entity_id, evidence in sorted(groups.items()):
        evidence.sort(
            key=lambda item: (
                item[0]["record_type"] != "active_substance",
                item[0]["source_id"],
                min(item[0]["pdf_pages_1based"]),
                item[0]["record_id"],
            )
        )
        base = dict(evidence[0][0])
        aliases = sorted({name for _, name in evidence}, key=str.casefold)
        preferred_name = aliases[0]
        base.update(
            {
                "record_id": "entity-" + stable_hash("active_substance", entity_id),
                "record_type": "active_substance",
                "active_substance_id": entity_id,
                "active_substance_ids": [entity_id],
                "active_substance_names": [preferred_name],
                "preferred_name": preferred_name,
                "original_name": preferred_name,
                "aliases": aliases,
                "aliases_original": aliases,
                "evidence_record_ids": sorted({record["record_id"] for record, _ in evidence}),
                "source_ids": sorted({record["source_id"] for record, _ in evidence}),
                "canonicalization_method": "source_explicit_composition_or_active_substance_evidence",
            }
        )
        consolidated.append(base)
    return consolidated


def build_documents(
    sources: list[dict[str, Any]], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    records_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_source[record["source_id"]].append(record)
    documents: list[dict[str, Any]] = []
    for source in sources:
        extracted = sorted(
            records_by_source[source["source_id"]],
            key=lambda item: (min(item["pdf_pages_1based"]), item["record_id"]),
        )
        metadata_records = [item for item in extracted if item["record_type"] == "document_metadata"]
        anchor = metadata_records[0] if metadata_records else (extracted[0] if extracted else None)
        title = (
            source.get("pdf_metadata", {}).get("/Title")
            or source.get("pdf_metadata", {}).get("/Subject")
            or source["original_file_name"]
        )
        exact_text = anchor["exact_source_text"] if anchor else str(title)
        page = min(anchor["pdf_pages_1based"]) if anchor else 1
        model_name = anchor["model_name"] if anchor else "local_deterministic_manifest"
        batch_id = anchor["extraction_batch_id"] if anchor else "local-source-manifest"
        document = {
            "record_id": "document-" + stable_hash(source["source_id"]),
            "record_type": "document",
            "source_id": source["source_id"],
            "document_type": source["document_type"],
            "source_file_name": source["original_file_name"],
            "source_sha256": source["sha256"],
            "section_path": [],
            "pdf_pages_1based": [page],
            "printed_page_label": None,
            "source_identifier": None,
            "title": str(title),
            "exact_source_text": exact_text,
            "semantic_summary_de": f"Quelldokument {title}",
            "citation_label": citation_label(source["original_file_name"], [page], None),
            "extraction_batch_id": batch_id,
            "model_name": model_name,
            "prompt_version": PROMPT_VERSION if anchor else "local-manifest-v1",
            "schema_version": SCHEMA_VERSION,
            "confidence": anchor["confidence"] if anchor else 1.0,
            "review_flags": list(anchor["review_flags"]) if anchor else [],
            "page_count": source["page_count"],
            "file_size_bytes": source["file_size_bytes"],
            "pdf_metadata": source.get("pdf_metadata", {}),
            "extracted_metadata_record_ids": [item["record_id"] for item in metadata_records],
        }
        documents.append(document)
    return documents


def build_guideline_links(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible_records = [record for record in records if is_primary_use_eligible(record)]
    formal = [record for record in eligible_records if record["record_type"] == "formal_item"]
    rationales = [
        record for record in eligible_records if record["record_type"] == "rationale_block"
    ]
    references = [
        record for record in eligible_records if record["record_type"] == "guideline_reference"
    ]
    visuals = [
        record
        for record in eligible_records
        if record["record_type"] == "table_figure_algorithm"
    ]
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_link(source_record: dict[str, Any], target_record: dict[str, Any], link_type: str, basis: str) -> None:
        if not is_primary_use_eligible(source_record) or not is_primary_use_eligible(
            target_record
        ):
            return
        key = (source_record["record_id"], target_record["record_id"], link_type)
        if key in seen:
            return
        seen.add(key)
        links.append(
            {
                "link_id": "link-" + stable_hash(key),
                "link_type": link_type,
                "source_id": source_record["source_id"],
                "from_record_id": source_record["record_id"],
                "from_formal_item_id": source_record.get("formal_item_id"),
                "to_record_id": target_record["record_id"],
                "to_source_id": target_record["source_id"],
                "to_record_type": target_record["record_type"],
                "to_pdf_pages_1based": target_record.get("pdf_pages_1based") or [],
                "link_basis": basis,
                "confidence": 1.0,
                "review_flags": [],
            }
        )

    for item in formal:
        item_number = normalize_text(item.get("source_item_number") or item.get("source_identifier") or "")
        explicit_rationale_ids = set(
            item.get("explicit_linked_rationale_record_ids") or []
        )
        for rationale in rationales:
            if rationale["source_id"] != item["source_id"]:
                continue
            labels = {normalize_text(value) for value in rationale.get("linked_item_numbers", [])}
            if rationale["record_id"] in explicit_rationale_ids:
                add_link(
                    item,
                    rationale,
                    "guideline_item_to_rationale",
                    "explicit linked rationale record ID from source-verified repair",
                )
            elif item_number and item_number in labels:
                add_link(item, rationale, "guideline_item_to_rationale", "explicit linked_item_numbers")
        reference_labels = {normalize_text(value) for value in item.get("linked_reference_labels", [])}
        for reference in references:
            if reference["source_id"] != item["source_id"]:
                continue
            identifier = normalize_text(reference.get("source_identifier") or "")
            if identifier and identifier in reference_labels:
                add_link(item, reference, "guideline_item_to_references", "explicit linked_reference_labels")
        visual_labels = {normalize_text(value) for value in item.get("linked_table_figure_labels", [])}
        for visual in visuals:
            if visual["source_id"] != item["source_id"]:
                continue
            identifier = normalize_text(visual.get("source_identifier") or "")
            if identifier and identifier in visual_labels:
                add_link(item, visual, "guideline_item_to_tables_figures", "explicit linked_table_figure_labels")

    by_id = {record["record_id"]: record for record in eligible_records}
    for secondary in formal:
        if secondary.get("canonical_role") == "primary":
            continue
        for primary_id in secondary.get("primary_record_ids") or []:
            primary = by_id.get(primary_id)
            if not primary or primary.get("source_id") != secondary.get("source_id"):
                continue
            add_link(
                secondary,
                primary,
                "guideline_secondary_representation_to_primary",
                secondary.get("secondary_relation_type") or "targeted source-zone classification",
            )
    return links


def build_medication_links(
    records: list[dict[str, Any]], active_ids: dict[str, str], product_ids: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    product_alias_ids: dict[str, str] = {}
    for record in records:
        if not record.get("product_id"):
            continue
        for value in [record.get("product_name"), record.get("original_product_name")]:
            if value:
                product_alias_ids.setdefault(normalize_text(value), record["product_id"])
    mentions: list[dict[str, Any]] = []
    for record in records:
        if record["document_type"] != "guideline":
            continue
        if not is_primary_use_eligible(record):
            continue
        for position, original in enumerate(record.get("medication_mentions_original") or []):
            normalized = normalize_text(original)
            active_id = active_ids.get(normalized)
            product_id = product_alias_ids.get(normalized)
            flags: list[str] = []
            if not active_id and not product_id:
                flags.append("no_exact_product_information_match")
            mentions.append(
                {
                    "mention_id": "mention-" + stable_hash(record["record_id"], position, original),
                    "link_types": [
                        "guideline_medication_mentions",
                        *(
                            ["medication_mention_to_product_information"]
                            if active_id or product_id
                            else []
                        ),
                    ],
                    "source_record_id": record["record_id"],
                    "formal_item_id": record.get("formal_item_id"),
                    "source_id": record["source_id"],
                    "original_mention": original,
                    "normalized_mention": normalized,
                    "active_substance_id": active_id,
                    "product_id": product_id,
                    "match_method": "exact_normalized_name" if active_id or product_id else None,
                    "citation_label": record["citation_label"],
                    "review_flags": flags,
                }
            )

    crosswalk: list[dict[str, Any]] = []
    aliases_by_id: dict[str, set[str]] = defaultdict(set)
    for name, entity_id in active_ids.items():
        aliases_by_id[entity_id].add(name)
    product_to_active: set[tuple[str, str]] = set()
    for record in records:
        for entity_id in record.get("active_substance_ids") or []:
            for name in (record.get("active_substance_names") or []) + (
                record.get("active_substance_original_names") or []
            ):
                aliases_by_id[entity_id].add(name)
            if record.get("product_id"):
                product_to_active.add((record["product_id"], entity_id))
    for entity_id, aliases in sorted(aliases_by_id.items()):
        crosswalk.append(
            {
                "crosswalk_id": "crosswalk-" + stable_hash("active_alias", entity_id),
                "link_type": "active_substance_aliases",
                "active_substance_id": entity_id,
                "product_id": None,
                "aliases_original": sorted(aliases, key=str.casefold),
                "normalization_method": "unicode_nfkc_casefold_exact",
                "review_flags": [],
            }
        )
    for product_id, entity_id in sorted(product_to_active):
        crosswalk.append(
            {
                "crosswalk_id": "crosswalk-" + stable_hash("product_active", product_id, entity_id),
                "link_type": "product_to_active_substance",
                "active_substance_id": entity_id,
                "product_id": product_id,
                "aliases_original": [],
                "normalization_method": "source_explicit_record_association",
                "review_flags": [],
            }
        )
    return mentions, crosswalk


def split_semantic_text(text: str, record_type: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    words = normalized.split()
    if len(words) <= 560 or record_type in {"formal_item", "dosing_rule", "contraindication", "warning"}:
        return [normalized]
    sentences = re.split(r"(?<=[.!?;])\s+(?=[A-ZÄÖÜ0-9])", normalized)
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        count = len(sentence.split())
        if current and current_words + count > 420:
            chunks.append(" ".join(current))
            current = []
            current_words = 0
        if count > 620:
            sentence_words = sentence.split()
            for index in range(0, len(sentence_words), 420):
                if current:
                    chunks.append(" ".join(current))
                    current = []
                    current_words = 0
                chunks.append(" ".join(sentence_words[index : index + 420]))
        else:
            current.append(sentence)
            current_words += count
    if current:
        chunks.append(" ".join(current))
    return chunks or [normalized]


def inherited_drug_identity(
    record: dict[str, Any], identity_by_source_page: dict[str, list[dict[str, Any]]]
) -> tuple[str | None, list[str], str | None, str | None]:
    candidates = identity_by_source_page.get(record["source_id"], [])
    page = min(record["pdf_pages_1based"])
    preceding = [candidate for candidate in candidates if candidate["page"] <= page]
    candidate = preceding[-1] if preceding else (candidates[0] if candidates else None)
    if not candidate:
        return None, [], None, None
    return (
        candidate.get("product_name"),
        candidate.get("active_substance_names") or [],
        candidate.get("strength"),
        candidate.get("pharmaceutical_form"),
    )


def propagate_drug_context(records: list[dict[str, Any]]) -> None:
    identity_by_source_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["document_type"] != "drug_label":
            continue
        if record["record_type"] == "drug_product" or record.get("product_name"):
            identity_by_source_page[record["source_id"]].append(
                {
                    "page": min(record["pdf_pages_1based"]),
                    "product_name": record.get("product_name") or record.get("original_product_name"),
                    "active_substance_names": record.get("active_substance_names")
                    or record.get("active_substance_original_names")
                    or [],
                    "strength": record.get("strength"),
                    "pharmaceutical_form": record.get("pharmaceutical_form"),
                }
            )
    for candidates in identity_by_source_page.values():
        candidates.sort(key=lambda item: item["page"])
    for record in records:
        if record["document_type"] != "drug_label":
            continue
        product, substances, strength, form = inherited_drug_identity(record, identity_by_source_page)
        if not record.get("product_name") and product:
            record["product_name"] = product
        active_names = list(record.get("active_substance_names") or [])
        placeholder = "Wirkstoff im unmittelbaren Quellkontext nicht wiederholt"
        if placeholder in active_names:
            active_names = [name for name in active_names if name != placeholder]
            if not active_names:
                active_names = list(substances)
            record["active_substance_names"] = active_names
        elif not active_names and substances:
            record["active_substance_names"] = substances
        if not record.get("strength") and strength:
            record["strength"] = strength
        if not record.get("pharmaceutical_form") and form:
            record["pharmaceutical_form"] = form


def build_retrieval_units(records: list[dict[str, Any]], documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    document_titles = {document["source_id"]: document["title"] for document in documents}
    excluded = {"document_metadata", "chapter_structure", "guideline_reference"}
    units: list[dict[str, Any]] = []
    for record in records:
        if record["record_type"] in excluded or not is_primary_use_eligible(record):
            continue
        retrieval_source = (
            record.get("exact_text_de")
            if record["record_type"] == "formal_item" and record.get("exact_text_de")
            else record.get("normalized_search_text") or record["exact_source_text"]
        )
        normalized_source_text = normalize_search_text(retrieval_source)
        chunks = split_semantic_text(normalized_source_text, record["record_type"])
        for index, chunk in enumerate(chunks, start=1):
            namespace = "guideline" if record["document_type"] == "guideline" else "drug_label"
            section = " > ".join(record.get("section_path") or [])
            context_parts = [
                document_titles.get(record["source_id"], record["source_file_name"]),
                section,
                record["record_type"].replace("_", " "),
                record.get("source_identifier") or "",
                record.get("product_name") or "",
                ", ".join(record.get("active_substance_names") or []),
                record.get("population") or "",
            ]
            context = " | ".join(part for part in context_parts if part)
            retrieval_text = f"{context}\n{chunk}" if context else chunk
            unit_id = "ru-" + stable_hash(record["record_id"], index, text_hash(chunk))
            units.append(
                {
                    "retrieval_unit_id": unit_id,
                    "corpus_namespace": namespace,
                    "parent_record_ids": [record["record_id"]],
                    "source_id": record["source_id"],
                    "active_substance_ids": record.get("active_substance_ids") or [],
                    "product_ids": [record["product_id"]] if record.get("product_id") else [],
                    "indication_ids": record.get("indication_ids") or [],
                    "formal_item_id": record.get("formal_item_id"),
                    "section_path": record.get("section_path") or [],
                    "title": record.get("title") or record.get("source_identifier") or record["record_type"],
                    "retrieval_text": retrieval_text,
                    "exact_source_text": chunk,
                    "canonical_exact_source_text_raw_sha256": record.get(
                        "exact_source_text_raw_sha256"
                    ),
                    "normalized_search_text_sha256": record.get(
                        "normalized_search_text_sha256"
                    ),
                    "semantic_summary_de": record["semantic_summary_de"],
                    "normalized_entities": record.get("normalized_entities") or [],
                    "keywords": record.get("keywords") or [],
                    "indications": record.get("indications") or ([record["indication"]] if record.get("indication") else []),
                    "populations": record.get("populations") or ([record["population"]] if record.get("population") else []),
                    "routes": record.get("routes") or ([record["route"]] if record.get("route") else []),
                    "evidence_metadata": {
                        "recommendation_grade": record.get("recommendation_grade"),
                        "evidence_level": record.get("evidence_level"),
                        "consensus_strength": record.get("consensus_strength"),
                    },
                    "pdf_pages_1based": record["pdf_pages_1based"],
                    "citation_label": record["citation_label"],
                    "source_file_name": record["source_file_name"],
                    "source_sha256": record["source_sha256"],
                    "extraction_batch_id": record["extraction_batch_id"],
                    "review_flags": list(record["review_flags"]),
                    "source_zone": record.get("source_zone"),
                    "canonical_role": record.get("canonical_role"),
                    "retrieval_eligible": True,
                    "embedding_eligible": True,
                    "answer_eligible": True,
                    "primary_search_eligible": True,
                    "approx_token_count": max(1, round(len(retrieval_text.split()) * 1.45)),
                }
            )
    units.sort(key=lambda item: (item["source_id"], min(item["pdf_pages_1based"]), item["retrieval_unit_id"]))
    return units


def build_coverage_manifest(
    sources: list[dict[str, Any]],
    infos_by_source: dict[str, list[PageInfo]],
    batches: list[Batch],
    checkpoints_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    primary_owners: dict[tuple[str, int], list[Batch]] = defaultdict(list)
    all_owners: dict[tuple[str, int], list[Batch]] = defaultdict(list)
    for batch in batches:
        for page in batch.owner_pages:
            all_owners[(batch.source_id, page)].append(batch)
    for source in sources:
        for info in infos_by_source[source["source_id"]]:
            if not info.primary_family:
                continue
            matches = list(all_owners[(source["source_id"], info.page)])
            primary_owners[(source["source_id"], info.page)].extend(matches)

    coverage: list[dict[str, Any]] = []
    for source in sources:
        for info in infos_by_source[source["source_id"]]:
            primary = primary_owners[(source["source_id"], info.page)]
            if info.primary_family and len(primary) != 1:
                raise PipelineError(
                    f"Page {source['source_id']}:{info.page} has {len(primary)} primary owner batches"
                )
            owner_batch_id = primary[0].batch_id if primary else None
            assessment: dict[str, Any] | None = None
            if owner_batch_id:
                checkpoint = checkpoints_by_id.get(owner_batch_id)
                if checkpoint is None:
                    raise PipelineError(f"Missing validated checkpoint {owner_batch_id}")
                assessments = [
                    item for item in checkpoint["page_assessments"] if item["pdf_page_1based"] == info.page
                ]
                if len(assessments) != 1:
                    raise PipelineError(
                        f"Checkpoint {owner_batch_id} lacks unique assessment for page {info.page}"
                    )
                assessment = assessments[0]
            status = info.status
            reason = info.status_reason
            if assessment and info.status == "extracted":
                status = assessment["status"]
                reason = assessment["reason_de"]
            if status not in ALLOWED_COVERAGE_STATUSES:
                raise PipelineError(f"Invalid coverage status {status} for {source['source_id']}:{info.page}")
            coverage.append(
                {
                    "source_id": source["source_id"],
                    "source_file_name": source["original_file_name"],
                    "source_sha256": source["sha256"],
                    "document_type": source["document_type"],
                    "pdf_page_1based": info.page,
                    "printed_page_label": info.printed_label,
                    "status": status,
                    "status_reason": reason,
                    "canonical_owner_batch_id": owner_batch_id,
                    "primary_task_family": info.primary_family,
                    "validated_batch_ids": sorted(
                        batch.batch_id
                        for batch in all_owners[(source["source_id"], info.page)]
                        if batch.batch_id in checkpoints_by_id
                    ),
                    "local_text_char_count": len(normalize_text(info.text)),
                    "review_flags": assessment.get("review_flags", []) if assessment else [],
                }
            )
    expected = sum(source["page_count"] for source in sources)
    unique = {(item["source_id"], item["pdf_page_1based"]) for item in coverage}
    if len(coverage) != expected or len(unique) != expected:
        raise PipelineError(f"Coverage cardinality mismatch: rows={len(coverage)}, unique={len(unique)}, expected={expected}")
    return coverage


def required_citation_fields_present(record: dict[str, Any]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for field_name in [
        "record_id",
        "source_id",
        "source_file_name",
        "source_sha256",
        "pdf_pages_1based",
        "exact_source_text",
        "citation_label",
        "extraction_batch_id",
        "model_name",
        "prompt_version",
        "schema_version",
    ]:
        value = record.get(field_name)
        if value is None or value == "" or value == []:
            missing.append(field_name)
    return not missing, missing


def build_citation_report(
    records: list[dict[str, Any]], retrieval_units: list[dict[str, Any]]
) -> dict[str, Any]:
    nonclinical_types = {"document_metadata", "chapter_structure", "guideline_reference"}
    clinical = [record for record in records if record["record_type"] not in nonclinical_types]
    canonical_failures: list[dict[str, Any]] = []
    for record in clinical:
        complete, missing = required_citation_fields_present(record)
        if not complete:
            canonical_failures.append({"record_id": record["record_id"], "missing_fields": missing})
    retrieval_failures: list[dict[str, Any]] = []
    for unit in retrieval_units:
        missing = [
            name
            for name in [
                "retrieval_unit_id",
                "parent_record_ids",
                "source_id",
                "source_file_name",
                "source_sha256",
                "pdf_pages_1based",
                "exact_source_text",
                "citation_label",
                "extraction_batch_id",
            ]
            if unit.get(name) is None or unit.get(name) == "" or unit.get(name) == []
        ]
        if missing:
            retrieval_failures.append({"retrieval_unit_id": unit["retrieval_unit_id"], "missing_fields": missing})
    denominator = len(clinical) + len(retrieval_units)
    failures = len(canonical_failures) + len(retrieval_failures)
    percentage = 100.0 if denominator == 0 else round((denominator - failures) / denominator * 100, 4)
    return {
        "schema_version": "citation-completeness-1.0.0",
        "checked_at_utc": utc_now(),
        "canonical_clinical_record_count": len(clinical),
        "retrieval_unit_count": len(retrieval_units),
        "complete_count": denominator - failures,
        "denominator": denominator,
        "citation_completeness_percent": percentage,
        "canonical_failures": canonical_failures,
        "retrieval_failures": retrieval_failures,
    }


def write_retrieval_csv(path: Path, units: list[dict[str, Any]]) -> None:
    fields = [
        "retrieval_unit_id",
        "corpus_namespace",
        "source_id",
        "title",
        "section_path",
        "formal_item_id",
        "active_substance_ids",
        "product_ids",
        "indication_ids",
        "pdf_pages_1based",
        "citation_label",
        "retrieval_text",
        "semantic_summary_de",
        "review_flags",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for unit in units:
                row = dict(unit)
                for field_name in [
                    "section_path",
                    "active_substance_ids",
                    "product_ids",
                    "indication_ids",
                    "pdf_pages_1based",
                    "review_flags",
                ]:
                    row[field_name] = json.dumps(row.get(field_name, []), ensure_ascii=False)
                writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def render_visual_pages(
    project_root: Path,
    output_root: Path,
    sources_by_id: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requests: set[tuple[str, int]] = set()
    for record in records:
        if record["record_type"] == "table_figure_algorithm":
            for page in record["pdf_pages_1based"]:
                requests.add((record["source_id"], page))
    results: list[dict[str, Any]] = []
    for source_id, page in sorted(requests):
        source = sources_by_id[source_id]
        destination_dir = output_root / "rendered_sources" / source_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"page-{page:04d}.png"
        if destination.exists() and destination.stat().st_size > 0:
            results.append({"source_id": source_id, "page": page, "rendered": True, "reused": True})
            continue
        prefix = destination.with_suffix("")
        completed = subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(page),
                "-l",
                str(page),
                "-r",
                "120",
                "-png",
                "-singlefile",
                str(project_root / source["relative_path"]),
                str(prefix),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        rendered = completed.returncode == 0 and destination.exists() and destination.stat().st_size > 0
        results.append(
            {
                "source_id": source_id,
                "page": page,
                "rendered": rendered,
                "reused": False,
                "diagnosis": completed.stderr[-1000:] if not rendered else None,
            }
        )
    return results


def collect_unresolved_flags(
    records: list[dict[str, Any]], coverage: list[dict[str, Any]], extra: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") == "excluded_by_policy":
            # Policy exclusions are fully adjudicated audit records, not
            # unresolved extraction problems.
            continue
        for flag in sorted(set(record.get("review_flags") or [])):
            rows.append(
                {
                    "severity": "review",
                    "category": "canonical_record",
                    "flag": flag,
                    "source_id": record["source_id"],
                    "record_id": record["record_id"],
                    "pdf_pages_1based": json.dumps(record["pdf_pages_1based"]),
                    "details": record["citation_label"],
                }
            )
    for page in coverage:
        for flag in sorted(set(page.get("review_flags") or [])):
            rows.append(
                {
                    "severity": "review",
                    "category": "coverage",
                    "flag": flag,
                    "source_id": page["source_id"],
                    "record_id": "",
                    "pdf_pages_1based": str(page["pdf_page_1based"]),
                    "details": page["status_reason"],
                }
            )
    rows.extend(extra)
    return rows


def write_unresolved_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["severity", "category", "flag", "source_id", "record_id", "pdf_pages_1based", "details"]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_targeted_repair_remaining_csv(
    path: Path, report: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    """Write only safe quarantines or unresolved blockers left after repair."""

    by_id = {record["record_id"]: record for record in records}
    rows: list[dict[str, Any]] = []
    for record_id in report.get(
        "historical_records_without_unambiguous_current_successor_ids", []
    ):
        record = by_id[record_id]
        if record.get("status") == "excluded_by_policy" or (
            not is_primary_use_eligible(record)
            and record.get("exclusion_reason") == HCC_HISTORICAL_EXCLUSION_REASON
        ):
            continue
        rows.append(
            {
                "issue_id": "historical_change_record_without_unambiguous_successor",
                "source_id": record["source_id"],
                "source_file_name": record["source_file_name"],
                "record_id": record_id,
                "pdf_pages_1based": json.dumps(record["pdf_pages_1based"]),
                "source_zone": record.get("source_zone"),
                "status": "quarantined",
                "retrieval_eligible": False,
                "reason": record.get("uncertainty_reason")
                or "Historische Darstellung ohne eindeutig belegbaren aktuellen Haupttext-Nachfolger.",
            }
        )
    fields = [
        "issue_id",
        "source_id",
        "source_file_name",
        "record_id",
        "pdf_pages_1based",
        "source_zone",
        "status",
        "retrieval_eligible",
        "reason",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_schemas(output_root: Path) -> None:
    import copy

    canonical_schema = copy.deepcopy(ExtractedRecord.model_json_schema())
    canonical_schema["title"] = "CanonicalClinicalRecord"
    canonical_schema["additionalProperties"] = True
    canonical_schema["properties"]["record_type"]["enum"].append("document")
    canonical_schema["properties"].update(
        {
            "record_id": {"type": "string", "minLength": 1},
            "source_id": {"type": "string", "minLength": 1},
            "document_type": {"enum": ["guideline", "drug_label"]},
            "source_file_name": {"type": "string", "minLength": 1},
            "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "citation_label": {"type": "string", "minLength": 1},
            "extraction_batch_id": {"type": "string", "minLength": 1},
            "model_name": {"type": "string", "minLength": 1},
            "prompt_version": {"type": "string", "minLength": 1},
            "schema_version": {"type": "string", "minLength": 1},
        }
    )
    canonical_schema["required"] = list(
        dict.fromkeys(
            canonical_schema["required"]
            + [
                "record_id",
                "source_id",
                "document_type",
                "source_file_name",
                "source_sha256",
                "section_path",
                "citation_label",
                "extraction_batch_id",
                "model_name",
                "prompt_version",
                "schema_version",
                "review_flags",
            ]
        )
    )
    schemas = {
        "gemini_extraction_envelope.schema.json": ExtractionEnvelope.model_json_schema(),
        "canonical_record.schema.json": canonical_schema,
        "retrieval_unit.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "retrieval-unit-1.0.0",
            "type": "object",
            "required": [
                "retrieval_unit_id",
                "corpus_namespace",
                "parent_record_ids",
                "source_id",
                "active_substance_ids",
                "product_ids",
                "indication_ids",
                "formal_item_id",
                "section_path",
                "title",
                "retrieval_text",
                "exact_source_text",
                "semantic_summary_de",
                "normalized_entities",
                "keywords",
                "indications",
                "populations",
                "routes",
                "evidence_metadata",
                "pdf_pages_1based",
                "citation_label",
                "source_file_name",
                "source_sha256",
                "extraction_batch_id",
                "review_flags",
            ],
            "properties": {
                "retrieval_unit_id": {"type": "string"},
                "corpus_namespace": {"enum": ["guideline", "drug_label"]},
                "parent_record_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "source_id": {"type": "string"},
                "active_substance_ids": {"type": "array", "items": {"type": "string"}},
                "product_ids": {"type": "array", "items": {"type": "string"}},
                "indication_ids": {"type": "array", "items": {"type": "string"}},
                "formal_item_id": {"type": ["string", "null"]},
                "section_path": {"type": "array", "items": {"type": "string"}},
                "title": {"type": "string", "minLength": 1},
                "retrieval_text": {"type": "string", "minLength": 1},
                "exact_source_text": {"type": "string", "minLength": 1},
                "semantic_summary_de": {"type": "string", "minLength": 1},
                "normalized_entities": {"type": "array", "items": {"type": "string"}},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "indications": {"type": "array", "items": {"type": "string"}},
                "populations": {"type": "array", "items": {"type": "string"}},
                "routes": {"type": "array", "items": {"type": "string"}},
                "evidence_metadata": {"type": "object"},
                "pdf_pages_1based": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                "citation_label": {"type": "string", "minLength": 1},
                "source_file_name": {"type": "string", "minLength": 1},
                "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "extraction_batch_id": {"type": "string", "minLength": 1},
                "review_flags": {"type": "array", "items": {"type": "string"}},
            },
        },
    }
    for file_name, schema in schemas.items():
        atomic_write_json(output_root / "schemas" / file_name, schema)


def enrich_formal_link_fields(records: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
    by_record: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for link in links:
        by_record[link["from_record_id"]][link["link_type"]].append(link["to_record_id"])
    for record in records:
        if record["record_type"] != "formal_item":
            continue
        mapping = by_record.get(record["record_id"], {})
        record["linked_rationale_ids"] = sorted(mapping.get("guideline_item_to_rationale", []))
        record["linked_reference_ids"] = sorted(mapping.get("guideline_item_to_references", []))
        record["linked_table_figure_ids"] = sorted(mapping.get("guideline_item_to_tables_figures", []))


def build_qa_extras(
    records: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    infos_by_source: dict[str, list[PageInfo]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    formal_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    secondary_formal_pages_by_source: dict[str, set[int]] = defaultdict(set)
    for record in records:
        if record["record_type"] == "formal_item" and record.get("canonical_role") == "primary":
            formal_by_source[record["source_id"]].append(record)
        elif record["record_type"] == "formal_item":
            secondary_formal_pages_by_source[record["source_id"]].update(
                record.get("pdf_pages_1based") or []
            )
    for source_id, items in formal_by_source.items():
        orders = sorted(item.get("primary_document_order") for item in items)
        expected = list(range(1, len(items) + 1))
        if orders != expected:
            rows.append(
                {
                    "severity": "error",
                    "category": "guideline_item_order",
                    "flag": "document_order_gap",
                    "source_id": source_id,
                    "record_id": "",
                    "pdf_pages_1based": "",
                    "details": f"orders={orders}",
                }
            )
        identifier_groups: dict[str, list[str]] = defaultdict(list)
        for item in items:
            identifier = normalize_text(item.get("source_item_number") or "")
            if identifier:
                identifier_groups[identifier].append(item["record_id"])
        for identifier, ids in identifier_groups.items():
            if len(ids) > 1:
                rows.append(
                    {
                        "severity": "review",
                        "category": "guideline_item_number",
                        "flag": "duplicate_source_item_number",
                        "source_id": source_id,
                        "record_id": ";".join(ids),
                        "pdf_pages_1based": "",
                        "details": identifier,
                    }
                )

        numeric_groups: dict[str, set[int]] = defaultdict(set)
        for item in items:
            identifier = (item.get("source_item_number") or item.get("source_identifier") or "").strip()
            match = re.fullmatch(r"(\d+(?:\.\d+)*)\.(\d+)", identifier)
            if match:
                numeric_groups[match.group(1)].add(int(match.group(2)))
            if not identifier:
                rows.append(
                    {
                        "severity": "review",
                        "category": "guideline_item_number",
                        "flag": "formal_item_without_visible_item_number",
                        "source_id": source_id,
                        "record_id": item["record_id"],
                        "pdf_pages_1based": json.dumps(item["pdf_pages_1based"]),
                        "details": item["citation_label"],
                    }
                )
        for prefix, values in sorted(numeric_groups.items()):
            if len(values) < 2 or max(values) - min(values) > 500:
                continue
            missing = sorted(set(range(min(values), max(values) + 1)) - values)
            if missing:
                rows.append(
                    {
                        "severity": "review",
                        "category": "guideline_item_number",
                        "flag": "possible_source_item_number_gap",
                        "source_id": source_id,
                        "record_id": "",
                        "pdf_pages_1based": "",
                        "details": f"{prefix}.: missing {','.join(map(str, missing))}",
                    }
                )

    formal_pages_by_source: dict[str, set[int]] = defaultdict(set)
    for source_id, items in formal_by_source.items():
        for item in items:
            formal_pages_by_source[source_id].update(item["pdf_pages_1based"])
            if item.get("linked_reference_labels") and not item.get("linked_reference_ids"):
                rows.append(
                    {
                        "severity": "review",
                        "category": "guideline_links",
                        "flag": "formal_item_reference_label_unresolved",
                        "source_id": source_id,
                        "record_id": item["record_id"],
                        "pdf_pages_1based": json.dumps(item["pdf_pages_1based"]),
                        "details": ";".join(item["linked_reference_labels"]),
                    }
                )
            if item.get("linked_table_figure_labels") and not item.get("linked_table_figure_ids"):
                rows.append(
                    {
                        "severity": "review",
                        "category": "guideline_links",
                        "flag": "formal_item_visual_label_unresolved",
                        "source_id": source_id,
                        "record_id": item["record_id"],
                        "pdf_pages_1based": json.dumps(item["pdf_pages_1based"]),
                        "details": ";".join(item["linked_table_figure_labels"]),
                    }
                )

    for source in sources:
        if source["document_type"] != "guideline":
            continue
        for info in infos_by_source[source["source_id"]]:
            if (
                info.canonical
                and info.formal_candidate
                and info.status == "extracted"
                and info.page not in formal_pages_by_source[source["source_id"]]
                and info.page not in secondary_formal_pages_by_source[source["source_id"]]
            ):
                rows.append(
                    {
                        "severity": "review",
                        "category": "guideline_completeness",
                        "flag": "formal_candidate_page_without_formal_item",
                        "source_id": source["source_id"],
                        "record_id": "",
                        "pdf_pages_1based": str(info.page),
                        "details": "Lokale Inventarisierung erkannte formale/Grading-Marker",
                    }
                )

    records_by_source_type: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_source_type[(record["source_id"], record["record_type"])].append(record)
    expected_by_family = {
        "drug_indications": "therapeutic_indication",
        "drug_dosing": "dosing_rule",
        "drug_contraindications": "contraindication",
        "drug_warnings": "warning",
        "drug_interactions": "interaction",
        "drug_pregnancy_lactation_fertility": "pregnancy_lactation_fertility",
        "drug_adverse_reactions": "adverse_reaction",
        "drug_storage_handling": "storage_handling",
    }
    for source in sources:
        if source["document_type"] != "drug_label":
            continue
        families = set().union(*(info.families for info in infos_by_source[source["source_id"]]))
        for family, record_type in expected_by_family.items():
            if family in families and not records_by_source_type[(source["source_id"], record_type)]:
                rows.append(
                    {
                        "severity": "review",
                        "category": "drug_section_completeness",
                        "flag": f"no_{record_type}_record",
                        "source_id": source["source_id"],
                        "record_id": "",
                        "pdf_pages_1based": "",
                        "details": f"Task family {family} was present in Annex I/SmPC",
                    }
                )
    for record in records:
        if record["record_type"] == "dosing_rule":
            missing = [
                field_name
                for field_name in ["dose_value", "dose_unit", "frequency", "route"]
                if not record.get(field_name)
            ]
            if missing:
                rows.append(
                    {
                        "severity": "review",
                        "category": "dosing_qa",
                        "flag": "dosing_fields_not_explicit_or_missing",
                        "source_id": record["source_id"],
                        "record_id": record["record_id"],
                        "pdf_pages_1based": json.dumps(record["pdf_pages_1based"]),
                        "details": ",".join(missing),
                    }
                )
        if record["record_type"] == "adverse_reaction" and not record.get("frequency_category"):
            rows.append(
                {
                    "severity": "review",
                    "category": "adverse_reaction_qa",
                    "flag": "adverse_reaction_frequency_not_explicit_or_missing",
                    "source_id": record["source_id"],
                    "record_id": record["record_id"],
                    "pdf_pages_1based": json.dumps(record["pdf_pages_1based"]),
                    "details": record["citation_label"],
                }
            )
    return rows


def write_extraction_report(
    output_root: Path,
    statistics: dict[str, Any],
    citation_report: dict[str, Any],
    coverage_report: dict[str, Any],
    unresolved_count: int,
) -> None:
    counts = statistics["record_counts"]
    lines = [
        "# Extraktions- und QA-Bericht",
        "",
        f"Erstellt: {utc_now()}",
        "",
        "## Laufkonfiguration",
        "",
        f"- Modell: `{MODEL_NAME}`",
        f"- Promptversion: `{PROMPT_VERSION}`",
        f"- Schemaversion: `{SCHEMA_VERSION}`",
        "- PDF-Verarbeitung: deterministische Mini-PDFs; keine vollständige PDF an Gemini",
        "- Gemini-Aufrufe: sequenziell; keine Modell-Fallbacks",
        "- Embeddings: nicht erzeugt; nur providerneutrales Embedding-Input",
        "",
        "## Umfang",
        "",
        f"- PDFs: {statistics['source_count']}",
        f"- Seiten: {statistics['page_count']}",
        f"- Erfolgreiche Vollbatches: {statistics['successful_batches']}",
        f"- Fehlgeschlagene Vollbatches: {statistics['failed_batches']}",
        f"- Durch deterministische Batch-Verkleinerung wiederhergestellt: {statistics['recovered_by_shrinking_batches']}",
        f"- Formale Leitlinienitems: {counts.get('formal_item', 0)}",
        f"- Primäre Haupttext-Items: {statistics.get('primary_formal_item_count', counts.get('formal_item', 0))}",
        f"- Sekundäre/historische formale Darstellungen: {statistics.get('secondary_formal_item_count', 0)}",
        f"- Tabellen/Abbildungen/Algorithmen: {counts.get('table_figure_algorithm', 0)}",
        f"- Arzneimittelprodukte: {statistics['unique_drug_products']}",
        f"- Wirkstoffe: {statistics['unique_active_substances']}",
        f"- Retrieval-Einheiten: {statistics['retrieval_unit_count']}",
        "",
        "## Deterministische QA",
        "",
        f"- Coverage: {coverage_report['coverage_percent']:.4f} %",
        f"- Citation Completeness: {citation_report['citation_completeness_percent']:.4f} %",
        f"- Ungelöste QA-Flags: {unresolved_count}",
        f"- Quell-PDFs unverändert: {statistics['sources_unchanged']}",
        f"- Gezieltes Reparaturoverlay angewendet: {statistics.get('targeted_repair_applied', False)}",
        "- Gezielte Reparatur: lokal-deterministisch; keine erneute Batch-Extraktion und kein Gemini-Aufruf",
        "",
        "Citation Completeness prüft alle klinischen kanonischen Records und Retrieval-Einheiten auf",
        "vollständige Quelle, SHA-256, Originalseite, Quelltext, Zitationslabel und Batch-Provenienz.",
        "",
        "## Antwortlogik für spätere Retrieval-/Synthese-Läufe",
        "",
        "- **supported:** Die konkrete Aussage wird von den tatsächlich abgerufenen und zitierten kanonischen Quellen vollständig getragen.",
        "- **partially supported:** Nur ein Teil der Aussage ist belegt oder relevante Einschränkungen bleiben bestehen.",
        "- **no validated evidence:** Im validierten Korpus wurde keine ausreichend belastbare Quelle gefunden.",
        "",
        "Diese Einstufung darf ausschließlich auf abgerufenen kanonischen Datensätzen beruhen. Fehlende Evidenz",
        "ist keine Evidenz für das Gegenteil. Der Extraktionslauf selbst vergibt keinen pauschalen Support-Status.",
        "",
        "## Reproduzierbarkeit",
        "",
        "Validierte Checkpoints werden über stabile Batch-IDs adressiert. Ein erneuter Lauf überspringt nur",
        "schema-, prompt-, modell- und quellidentische validierte Checkpoints. Das Source Manifest friert die",
        "Eingabemenge ein; neu hinzukommende oder geänderte PDFs brechen die Integritätsprüfung ab.",
        "",
    ]
    path = output_root / "qa/extraction_report.md"
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def compile_outputs(
    project_root: Path,
    output_root: Path,
    sources: list[dict[str, Any]],
    infos_by_source: dict[str, list[PageInfo]],
    batches: list[Batch],
    checkpoints: list[dict[str, Any]],
    final_hashes: list[dict[str, Any]],
) -> dict[str, Any]:
    sources_by_id = {source["source_id"]: source for source in sources}
    checkpoints_by_id = {checkpoint["batch_id"]: checkpoint for checkpoint in checkpoints}
    previous_links_path = output_root / "links/guideline_item_links.jsonl"
    previous_links = (
        [
            json.loads(line)
            for line in previous_links_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if previous_links_path.exists()
        else []
    )
    records, duplicate_report = checkpoint_records(checkpoints, sources_by_id)
    records, repair_changes, repair_overlay = apply_record_overlay(
        records,
        sources_by_id,
        output_root,
        stable_hash=stable_hash,
        text_hash=text_hash,
        citation_label=citation_label,
        schema_version=SCHEMA_VERSION,
    )
    mark_active_substance_identity_evidence(records)
    propagate_drug_context(records)
    product_ids, active_ids = assign_entity_ids(records)
    indication_ids = assign_indication_ids(records)
    links = build_guideline_links(records)
    enrich_formal_link_fields(records, links)
    mentions, crosswalk = build_medication_links(records, active_ids, product_ids)
    documents = build_documents(sources, records)
    retrieval_units = build_retrieval_units(records, documents)
    coverage = build_coverage_manifest(sources, infos_by_source, batches, checkpoints_by_id)
    repair_changes.extend(apply_coverage_overlay(coverage, repair_overlay))

    product_entities = consolidate_entity_records(records, "drug_product", "product_id")
    active_entities = consolidate_active_substance_records(records, active_ids)

    # Write every canonical partition, including empty files, so downstream schemas are stable.
    atomic_write_jsonl(output_root / "canonical/documents.jsonl", documents)
    records_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_type[record["record_type"]].append(record)
    for record_type, file_name in CANONICAL_FILE_MAP.items():
        partition = records_by_type.get(record_type, [])
        if record_type == "drug_product":
            partition = product_entities
        elif record_type == "active_substance":
            partition = active_entities
        atomic_write_jsonl(output_root / "canonical" / file_name, partition)
    # Consolidated identity partitions expose stable product/substance entities.
    # Preserve each source-faithful evidence record as a canonical parent too,
    # because retrieval units cite the exact page-level record rather than an
    # aggregate entity assembled across pages.
    atomic_write_jsonl(
        output_root / "canonical/drug_product_evidence.jsonl",
        records_by_type.get("drug_product", []),
    )
    atomic_write_jsonl(
        output_root / "canonical/active_substance_evidence.jsonl",
        records_by_type.get("active_substance", []),
    )
    pharmacology = sorted(
        records_by_type.get("overdose", [])
        + records_by_type.get("pharmacodynamics", [])
        + records_by_type.get("pharmacokinetics", []),
        key=lambda item: (item["source_id"], min(item["pdf_pages_1based"]), item["record_id"]),
    )
    atomic_write_jsonl(output_root / "canonical/pharmacology.jsonl", pharmacology)

    atomic_write_jsonl(output_root / "links/guideline_item_links.jsonl", links)
    atomic_write_jsonl(output_root / "links/medication_mentions.jsonl", mentions)
    atomic_write_jsonl(output_root / "links/active_substance_crosswalk.jsonl", crosswalk)

    atomic_write_jsonl(output_root / "retrieval/retrieval_units.jsonl", retrieval_units)
    write_retrieval_csv(output_root / "retrieval/retrieval_units.csv", retrieval_units)
    embedding_input = [
        {
            "id": unit["retrieval_unit_id"],
            "text": unit["retrieval_text"],
            "metadata": {
                "corpus_namespace": unit["corpus_namespace"],
                "source_id": unit["source_id"],
                "parent_record_ids": unit["parent_record_ids"],
                "citation_label": unit["citation_label"],
                "pdf_pages_1based": unit["pdf_pages_1based"],
            },
        }
        for unit in retrieval_units
    ]
    atomic_write_jsonl(output_root / "retrieval/embedding_input.jsonl", embedding_input)

    hcc_historical_exclusions = sorted(
        (
            {
                "record_id": record["record_id"],
                "source_id": record["source_id"],
                "source_file_name": record["source_file_name"],
                "record_type": record["record_type"],
                "pdf_pages_1based": record.get("pdf_pages_1based") or [],
                "source_zone": record.get("source_zone"),
                "canonical_role": record.get("canonical_role"),
                "status": record.get("status"),
                "retrieval_eligible": record.get("retrieval_eligible"),
                "embedding_eligible": record.get("embedding_eligible"),
                "answer_eligible": record.get("answer_eligible"),
                "primary_search_eligible": record.get("primary_search_eligible"),
                "exclusion_reason": record.get("exclusion_reason"),
                "audit_retained": True,
            }
            for record in records
            if record.get("exclusion_reason") == HCC_HISTORICAL_EXCLUSION_REASON
        ),
        key=lambda item: (
            min(item["pdf_pages_1based"]),
            item["record_type"],
            item["record_id"],
        ),
    )
    atomic_write_jsonl(
        output_root / "qa/hcc_historical_exclusions.jsonl",
        hcc_historical_exclusions,
    )

    atomic_write_jsonl(output_root / "manifests/coverage_manifest.jsonl", coverage)
    coverage_status_counts = Counter(item["status"] for item in coverage)
    coverage_report = {
        "schema_version": "coverage-report-1.0.0",
        "checked_at_utc": utc_now(),
        "expected_pages": sum(source["page_count"] for source in sources),
        "covered_pages": len(coverage),
        "unique_page_locators": len({(item["source_id"], item["pdf_page_1based"]) for item in coverage}),
        "coverage_percent": round(len(coverage) / sum(source["page_count"] for source in sources) * 100, 4),
        "status_counts": dict(sorted(coverage_status_counts.items())),
        "missing_pages": [],
        "duplicate_page_locators": [],
    }
    atomic_write_json(output_root / "qa/coverage_report.json", coverage_report)
    atomic_write_json(output_root / "qa/duplicate_report.json", {"duplicates": duplicate_report})

    schema_validation_rows = []
    for batch in batches:
        checkpoint = checkpoints_by_id.get(batch.batch_id)
        schema_validation_rows.append(
            {
                "batch_id": batch.batch_id,
                "source_id": batch.source_id,
                "task_family": batch.task_family,
                "schema_version": SCHEMA_VERSION,
                "valid": bool(checkpoint and checkpoint.get("validation_status") == "valid"),
                "record_count": len(checkpoint.get("records", [])) if checkpoint else 0,
            }
        )
    atomic_write_jsonl(output_root / "qa/schema_validation.jsonl", schema_validation_rows)

    citation_report = build_citation_report(records, retrieval_units)
    atomic_write_json(output_root / "qa/citation_completeness.json", citation_report)

    if repair_overlay is not None:
        repair_report, repair_changes, repair_markdown = build_repair_audit(
            overlay=repair_overlay,
            changes=repair_changes,
            records=records,
            links=links,
            retrieval_units=retrieval_units,
            previous_links=previous_links,
            coverage_report=coverage_report,
            citation_report=citation_report,
            source_integrity=final_hashes,
            checked_at_utc=utc_now(),
        )
        atomic_write_jsonl(output_root / "qa/targeted_repair_changes.jsonl", repair_changes)
        atomic_write_json(output_root / "qa/targeted_repair_report.json", repair_report)
        repair_report_path = output_root / "qa/targeted_repair_report.md"
        repair_report_path.write_text(repair_markdown, encoding="utf-8", newline="\n")
        write_targeted_repair_remaining_csv(
            output_root / "qa/targeted_repair_remaining.csv", repair_report, records
        )

    qa_extra = build_qa_extras(records, sources, infos_by_source)
    render_results = render_visual_pages(project_root, output_root, sources_by_id, records)
    for result in render_results:
        if not result["rendered"]:
            qa_extra.append(
                {
                    "severity": "review",
                    "category": "rendering",
                    "flag": "visual_page_render_failed",
                    "source_id": result["source_id"],
                    "record_id": "",
                    "pdf_pages_1based": str(result["page"]),
                    "details": result.get("diagnosis") or "unknown",
                }
            )
    unresolved = collect_unresolved_flags(records, coverage, qa_extra)
    write_unresolved_csv(output_root / "qa/unresolved_items.csv", unresolved)

    record_counts = Counter(record["record_type"] for record in records)
    failed_count = sum(1 for batch in batches if not (output_root / "checkpoints/validated" / f"{batch.batch_id}.json").exists())
    statistics = {
        "schema_version": "corpus-statistics-1.0.0",
        "generated_at_utc": utc_now(),
        "source_count": len(sources),
        "page_count": sum(source["page_count"] for source in sources),
        "successful_batches": len(batches) - failed_count,
        "failed_batches": failed_count,
        "recovered_by_shrinking_batches": sum(
            bool(checkpoint.get("split_recovery")) for checkpoint in checkpoints
        ),
        "record_count": len(records),
        "record_counts": dict(sorted(record_counts.items())),
        "unique_drug_products": len(product_entities),
        "unique_active_substances": len(active_entities),
        "unique_source_explicit_indications": len(indication_ids),
        "retrieval_unit_count": len(retrieval_units),
        "guideline_namespace_units": sum(unit["corpus_namespace"] == "guideline" for unit in retrieval_units),
        "drug_label_namespace_units": sum(unit["corpus_namespace"] == "drug_label" for unit in retrieval_units),
        "coverage_percent": coverage_report["coverage_percent"],
        "citation_completeness_percent": citation_report["citation_completeness_percent"],
        "unresolved_qa_flags": len(unresolved),
        "sources_unchanged": all(item["unchanged"] for item in final_hashes),
        "targeted_repair_applied": repair_overlay is not None,
        "targeted_repair_change_count": len(repair_changes),
        "primary_formal_item_count": sum(
            record["record_type"] == "formal_item"
            and record.get("canonical_role") == "primary"
            for record in records
        ),
        "secondary_formal_item_count": sum(
            record["record_type"] == "formal_item"
            and record.get("canonical_role") != "primary"
            for record in records
        ),
    }
    atomic_write_json(output_root / "retrieval/corpus_statistics.json", statistics)
    atomic_write_json(output_root / "qa/source_integrity_final.json", {"results": final_hashes})
    atomic_write_json(output_root / "qa/rendered_sources_report.json", {"results": render_results})
    write_extraction_report(output_root, statistics, citation_report, coverage_report, len(unresolved))
    return {
        "statistics": statistics,
        "coverage_report": coverage_report,
        "citation_report": citation_report,
        "unresolved": unresolved,
        "duplicate_count": len(duplicate_report),
    }


def write_page_inventory(
    output_root: Path, sources: list[dict[str, Any]], infos_by_source: dict[str, list[PageInfo]]
) -> None:
    rows: list[dict[str, Any]] = []
    for source in sources:
        for info in infos_by_source[source["source_id"]]:
            rows.append(
                {
                    "source_id": source["source_id"],
                    "source_file_name": source["original_file_name"],
                    "document_type": source["document_type"],
                    "pdf_page_1based": info.page,
                    "printed_page_label": info.printed_label,
                    "local_status": info.status,
                    "local_status_reason": info.status_reason,
                    "canonical": info.canonical,
                    "primary_task_family": info.primary_family,
                    "relevant_task_families": sorted(info.families),
                    "detected_sections": info.sections,
                    "local_text_char_count": len(normalize_text(info.text)),
                    "dense": info.dense,
                    "formal_candidate": info.formal_candidate,
                    "visual_candidate": info.visual_candidate,
                }
            )
    atomic_write_jsonl(output_root / "manifests/page_inventory.jsonl", rows)


def load_validated_checkpoints(output_root: Path, batches: Sequence[Batch]) -> list[dict[str, Any]]:
    checkpoints: list[dict[str, Any]] = []
    for batch in batches:
        path = output_root / "checkpoints/validated" / f"{batch.batch_id}.json"
        if not path.exists():
            raise PipelineError(f"Validated checkpoint missing after extraction: {batch.batch_id}")
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if (
            checkpoint.get("validation_status") != "valid"
            or checkpoint.get("batch_id") != batch.batch_id
            or checkpoint.get("model_name") != MODEL_NAME
            or checkpoint.get("prompt_version") != PROMPT_VERSION
            or checkpoint.get("schema_version") != SCHEMA_VERSION
        ):
            raise PipelineError(f"Stale or invalid checkpoint: {path}")
        try:
            envelope = ExtractionEnvelope.model_validate(
                {
                    "source_id": checkpoint["source_id"],
                    "source_file_name": checkpoint["source_file_name"],
                    "document_type": checkpoint["document_type"],
                    "task_family": checkpoint["task_family"],
                    "request_pdf_pages_1based": checkpoint["request_pdf_pages_1based"],
                    "owner_pdf_pages_1based": checkpoint["owner_pdf_pages_1based"],
                    "page_assessments": checkpoint["page_assessments"],
                    "records": [
                        {key: value for key, value in record.items() if not key.startswith("_")}
                        for record in checkpoint["records"]
                    ],
                }
            )
        except (KeyError, ValidationError, ValueError) as exc:
            raise PipelineError(f"Checkpoint schema validation failed: {path}: {safe_error(exc)}") from exc
        validation_messages = validate_envelope(envelope, batch)
        if validation_messages:
            raise PipelineError(
                f"Checkpoint provenance validation failed: {path}: {validation_messages}"
            )
        checkpoints.append(checkpoint)
    return checkpoints


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--phase",
        choices=["preflight", "full", "compile"],
        default="full",
        help="preflight stops before full extraction; compile uses existing full checkpoints",
    )
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    output_root = project_root / "outputs/knowledge_corpus"
    ensure_output_tree(output_root)
    manifest_path = output_root / "manifests/source_manifest.json"
    if not manifest_path.exists():
        raise PipelineError(
            f"Frozen source manifest is missing. Run scripts/initialize_knowledge_corpus.py first: {manifest_path}"
        )
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = source_manifest["sources"]
    initial_hashes = verify_frozen_sources(project_root, source_manifest)
    write_schemas(output_root)

    infos_by_source: dict[str, list[PageInfo]] = {}
    for source in sources:
        infos = load_page_infos(project_root, source)
        if source["document_type"] == "guideline":
            classify_guideline_pages(infos)
        elif source["document_type"] == "drug_label":
            classify_drug_pages(infos, source)
        else:
            raise PipelineError(f"Unclassified frozen source: {source['source_id']}")
        infos_by_source[source["source_id"]] = infos
    write_page_inventory(output_root, sources, infos_by_source)

    batches: list[Batch] = []
    for source in sources:
        batches.extend(plan_combined_batches(source, infos_by_source[source["source_id"]]))
    batches.sort(
        key=lambda batch: (
            batch.source_file_name.casefold(),
            batch.request_pages[0],
            batch.task_family,
            batch.owner_pages,
        )
    )
    atomic_write_jsonl(
        output_root / "manifests/batch_plan.jsonl",
        [
            {
                "batch_id": batch.batch_id,
                "source_id": batch.source_id,
                "source_file_name": batch.source_file_name,
                "source_sha256": batch.source_sha256,
                "document_type": batch.document_type,
                "request_pdf_pages_1based": list(batch.request_pages),
                "owner_pdf_pages_1based": list(batch.owner_pages),
                "task_family": batch.task_family,
                "model_name": MODEL_NAME,
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
            }
            for batch in batches
        ],
    )

    run_id = "run-" + stable_hash(
        source_manifest["frozen_at_utc"],
        [(source["source_id"], source["sha256"]) for source in sources],
        MODEL_NAME,
        PROMPT_VERSION,
        SCHEMA_VERSION,
    )
    run_manifest_path = output_root / "manifests/run_manifest.json"
    previous_run = json.loads(run_manifest_path.read_text(encoding="utf-8")) if run_manifest_path.exists() else {}
    run_manifest: dict[str, Any] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at_utc": previous_run.get("started_at_utc", utc_now()),
        "last_updated_at_utc": utc_now(),
        "project_root": str(project_root),
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_count": len(sources),
        "page_count": sum(source["page_count"] for source in sources),
        "planned_full_batch_count": len(batches),
        "model_name": MODEL_NAME,
        "prompt_version": PROMPT_VERSION,
        "schema_version_extraction": SCHEMA_VERSION,
        "gemini_calls_sequential": True,
        "full_pdf_sent_to_gemini": False,
        "remote_files_api_used": False,
        "embedding_api_called": False,
        "openai_api_called": False,
        "model_fallback_used": False,
        "git_commit_performed": False,
        "git_push_performed": False,
        "source_integrity_initial": initial_hashes,
    }
    atomic_write_json(run_manifest_path, run_manifest)

    api_key: str | None = None
    try:
        if args.phase != "compile":
            api_key = load_api_key()
            # Disable the SDK's own retry layer so the pipeline-level policy is
            # the single source of truth: one request plus at most three
            # retries for 408/429/5xx. A bounded transport timeout also prevents
            # an interrupted 503 response from hanging a resumable run forever.
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=420_000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            smoke = run_smoke_test(client, output_root)
            preflights = run_preflights(
                client, api_key, project_root, output_root, sources, infos_by_source
            )
            run_manifest["smoke_test"] = smoke
            run_manifest["preflights"] = preflights
            run_manifest["preflights_passed"] = all(item["passed"] for item in preflights)
            run_manifest["last_updated_at_utc"] = utc_now()
            atomic_write_json(run_manifest_path, run_manifest)
            if args.phase == "preflight":
                run_manifest["status"] = "preflight_passed"
                run_manifest["last_updated_at_utc"] = utc_now()
                atomic_write_json(run_manifest_path, run_manifest)
                print(
                    json.dumps(
                        {
                            "status": "preflight_passed",
                            "model_name": MODEL_NAME,
                            "preflights": preflights,
                            "planned_full_batches": len(batches),
                        },
                        ensure_ascii=False,
                    )
                )
                return

            sources_by_id = {source["source_id"]: source for source in sources}
            for index, batch in enumerate(batches, start=1):
                process_batch(
                    client,
                    api_key,
                    project_root,
                    output_root,
                    sources_by_id[batch.source_id],
                    infos_by_source[batch.source_id],
                    batch,
                )
                if index % 10 == 0 or index == len(batches):
                    run_manifest["validated_full_batches"] = index
                    run_manifest["last_updated_at_utc"] = utc_now()
                    atomic_write_json(run_manifest_path, run_manifest)
                    print(f"validated full batches: {index}/{len(batches)}", flush=True)

        checkpoints = load_validated_checkpoints(output_root, batches)
        final_hashes = verify_frozen_sources(project_root, source_manifest)
        compiled = compile_outputs(
            project_root,
            output_root,
            sources,
            infos_by_source,
            batches,
            checkpoints,
            final_hashes,
        )
        run_manifest.update(
            {
                "status": "complete",
                "completed_at_utc": utc_now(),
                "last_updated_at_utc": utc_now(),
                "validated_full_batches": len(checkpoints),
                "failed_full_batches": 0,
                "source_integrity_final": final_hashes,
                "statistics": compiled["statistics"],
                "coverage_report_path": "qa/coverage_report.json",
                "citation_report_path": "qa/citation_completeness.json",
                "extraction_report_path": "qa/extraction_report.md",
                "targeted_repair_applied": compiled["statistics"].get(
                    "targeted_repair_applied", False
                ),
                "targeted_repair_overlay_path": "manifests/targeted_repair_overlay.json",
                "targeted_repair_report_path": "qa/targeted_repair_report.json",
                "targeted_repair_gemini_used": False,
                "targeted_repair_full_reextraction_performed": False,
            }
        )
        atomic_write_json(run_manifest_path, run_manifest)
        print(json.dumps({"status": "complete", **compiled["statistics"]}, ensure_ascii=False))
    except BaseException as exc:
        run_manifest.update(
            {
                "status": "blocked",
                "blocked_at_utc": utc_now(),
                "last_updated_at_utc": utc_now(),
                "blocker": safe_error(exc, api_key),
                "validated_full_batches": sum(
                    (output_root / "checkpoints/validated" / f"{batch.batch_id}.json").exists()
                    for batch in batches
                ),
                "failed_full_batches": sum(
                    (output_root / "checkpoints/failed" / f"{batch.batch_id}.json").exists()
                    and not (output_root / "checkpoints/validated" / f"{batch.batch_id}.json").exists()
                    for batch in batches
                ),
            }
        )
        atomic_write_json(run_manifest_path, run_manifest)
        raise


if __name__ == "__main__":
    main()

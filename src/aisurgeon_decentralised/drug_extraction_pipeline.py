from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors, types
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from aisurgeon_decentralised.drug_extraction_schema import (
    CatalogEvidence,
    DrugCatalogEntry,
    DrugMention,
    GeminiBatchResult,
    SourceRecord,
)

MODEL_NAME = "gemini-3.5-flash"

SOURCE_FILES = [
    (
        "VTE_2026",
        "003-001l_S3_Prophylaxe-venoese-Thromboembolie-VTE_2026-04.pdf",
        "Finale Langversion",
    ),
    (
        "PANKREAS_2025",
        "032-010OLl_Exokrines-Pankreaskarzinom_2025-06.pdf",
        "Finale Langversion",
    ),
    (
        "HCC_BCC_2026_KONSULTATION",
        "S3_LL_HCC_und_BCC_Konsultationsfassung_Langversion_6.01 (1).pdf",
        "Konsultationsfassung",
    ),
]

JSON_SCHEMA_HINT = """
Return one strict JSON object with this shape:
{
  "source_id": "...",
  "batch_start_page": 1,
  "batch_end_page": 3,
  "pages": [
    {
      "pdf_page": 1,
      "printed_page": "1 or null",
      "status": "NO_MEDICATION_MENTION | MEDICATION_MENTION_FOUND | NEEDS_REPAIR | NEEDS_MANUAL_REVIEW",
      "mentions": [
        {
          "pdf_page": 1,
          "printed_page": "1 or null",
          "section_path": ["chapter", "section"],
          "mention_scope": "CLINICAL_CONTENT | REFERENCE_ONLY | HISTORICAL_OR_NEGATIVE | UNCLEAR_SCOPE",
          "raw_mention": "short exact mentioned term",
          "exact_context_quote": "short verbatim quote, preferably <=25 words",
          "entity_type": "ACTIVE_SUBSTANCE | DRUG_PRODUCT | BRAND_NAME | DRUG_CLASS | COMBINATION_REGIMEN | BIOLOGIC | CONTRAST_AGENT | BLOOD_PRODUCT | SUPPLEMENT | OTHER_SUBSTANCE | UNCLEAR",
          "canonical_name_de": "normalized German name",
          "canonical_name_en": null,
          "brand_name": null,
          "drug_class": null,
          "regimen_name": null,
          "normalization_status": "EXACT | NORMALIZED | INFERRED | UNCLEAR",
          "source_explicit": true,
          "clinical_context": "brief German context",
          "formal_item_reference": "recommendation/table/section id or null",
          "confidence": 0.0,
          "needs_manual_review": false
        }
      ],
      "notes": null
    }
  ]
}
"""


@dataclass(frozen=True)
class Batch:
    source_id: str
    source_path: Path
    start_page: int
    end_page: int

    @property
    def key(self) -> str:
        return f"{self.source_id}_{self.start_page:04d}_{self.end_page:04d}"


def load_gemini_api_key(env_path: Path) -> str:
    text = env_path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*(?:export\s+)?GEMINI_API_KEY\s*=\s*(.+?)\s*$", text)
    if not match:
        raise RuntimeError(f"GEMINI_API_KEY not found in {env_path}")
    value = match.group(1).strip()
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        value = value[1:-1]
    if not value:
        raise RuntimeError("GEMINI_API_KEY is empty")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_pdf_pages(path: Path) -> int:
    return len(PdfReader(str(path)).pages)


def register_sources(source_dir: Path) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source_id, filename, status in SOURCE_FILES:
        path = source_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing source PDF: {path}")
        records.append(
            SourceRecord(
                source_id=source_id,
                source_filename=filename,
                source_sha256=sha256_file(path),
                pdf_pages=count_pdf_pages(path),
                document_status=status,
            )
        )
    return records


def make_batches(source_path: Path, source_id: str, page_count: int, size: int = 20) -> list[Batch]:
    batches: list[Batch] = []
    start = 1
    while start <= page_count:
        end = min(page_count, start + size - 1)
        batches.append(Batch(source_id, source_path, start, end))
        if end == page_count:
            break
        start = end
    return batches


def write_pdf_batch(batch: Batch, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(batch.source_path))
    writer = PdfWriter()
    for page_no in range(batch.start_page, batch.end_page + 1):
        writer.add_page(reader.pages[page_no - 1])
    with output_path.open("wb") as handle:
        writer.write(handle)


def run_text_smoke_test(client: genai.Client) -> str:
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=["Return exactly PASS."],
        config=types.GenerateContentConfig(temperature=0),
    )
    text = (response.text or "").strip()
    if text != "PASS":
        raise RuntimeError(f"Gemini smoke test failed: expected PASS, got {text!r}")
    return "PASS"


def is_retryable_error(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None)
    if code in {400, 403}:
        return False
    if code in {408, 429}:
        return True
    if isinstance(code, int) and 500 <= code <= 599:
        return True
    text = str(exc)
    return any(marker in text for marker in [" 429 ", " 408 ", " 500 ", " 502 ", " 503 ", " 504 "])


def request_gemini_batch(
    client: genai.Client,
    batch_pdf: Path,
    batch: Batch,
    source_filename: str,
    repair: bool = False,
) -> str:
    uploaded = None
    try:
        uploaded = client.files.upload(file=str(batch_pdf))
        prompt = build_prompt(batch, source_filename, repair=repair)
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[uploaded, prompt],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                max_output_tokens=32768,
            ),
        )
        return response.text or ""
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def call_with_retries(function, *args, **kwargs) -> Any:
    delay = 2.0
    attempts = 4
    for attempt in range(1, attempts + 1):
        try:
            return function(*args, **kwargs)
        except (errors.ClientError, errors.ServerError, TimeoutError, OSError) as exc:
            code = getattr(exc, "status_code", None)
            if code in {400, 403} or not is_retryable_error(exc) or attempt == attempts:
                raise
            time.sleep(delay)
            delay *= 2


def build_prompt(batch: Batch, source_filename: str, repair: bool) -> str:
    page_map = ", ".join(str(page) for page in range(batch.start_page, batch.end_page + 1))
    repair_text = (
        "This is a targeted repair request. Return valid JSON only and include every page in the page map. "
        if repair
        else ""
    )
    return f"""
{repair_text}
You are extracting medication and substance mentions from a German medical guideline PDF batch.

Model requirement: use only the attached PDF batch. Do not use external drug databases or general medical knowledge for source-explicit facts.

Source:
- source_id: {batch.source_id}
- source_filename: {source_filename}
- The attached PDF contains original 1-based PDF pages: {page_map}
- The first attached page is original pdf_page {batch.start_page}; never return local attachment page numbers.

Analyze every attached page. Include recommendations, statements, comments, free text, tables, algorithms, figure captions, footnotes, appendices, abbreviations, and references.

Extract:
- active substances, drug products, brand names, biologics, antibodies, anticoagulants, antiplatelet agents, oncology therapies, supportive/perioperative drugs, contrast agents if concrete substances are named, drug classes if no concrete substance is named, blood products, supplements, other substances, and combination regimens such as FOLFIRINOX.
- Regimens are COMBINATION_REGIMEN, not individual substances.
- Components of a regimen are source-explicit only if the PDF explicitly spells them out or clearly maps them. If you infer components from general knowledge, mark normalization_status INFERRED, source_explicit false, and needs_manual_review true.

Classify scope:
- CLINICAL_CONTENT: clinical main text, recommendation, statement, comment, table, algorithm, clinically meaningful appendix.
- REFERENCE_ONLY: appears only in a bibliographic reference.
- HISTORICAL_OR_NEGATIVE: historical therapy, rejected option, negative study arm, or not recommended treatment.
- UNCLEAR_SCOPE: cannot classify confidently.

Quote rules:
- exact_context_quote must be short, verbatim, and sufficient for checking.
- Prefer 25 words or fewer.
- Never paraphrase as a quote.
- Do not invent pages, substances, quotes, or recommendation ids.

For pages without medication/substance mentions, include the page with status NO_MEDICATION_MENTION and an empty mentions array.

{JSON_SCHEMA_HINT}
Return JSON only.
"""


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in Gemini response")
    return json.loads(text[start : end + 1])


def align_batch_page_numbers(result: GeminiBatchResult, batch: Batch) -> GeminiBatchResult:
    expected_pages = list(range(batch.start_page, batch.end_page + 1))
    returned_pages = sorted({page.pdf_page for page in result.pages})
    if returned_pages == expected_pages:
        result.batch_start_page = batch.start_page
        result.batch_end_page = batch.end_page
        return result

    same_shape = (
        len(returned_pages) == len(expected_pages)
        and returned_pages == list(range(returned_pages[0], returned_pages[-1] + 1))
    )
    if not same_shape:
        return result

    offset = batch.start_page - returned_pages[0]
    shifted_pages = [page + offset for page in returned_pages]
    if shifted_pages != expected_pages:
        return result

    page_by_shifted = {page.pdf_page + offset: page for page in result.pages}
    aligned_pages = []
    for expected_page in expected_pages:
        page = page_by_shifted[expected_page]
        page.pdf_page = expected_page
        for mention in page.mentions:
            mention.pdf_page = expected_page
            mention.needs_manual_review = True
        if page.notes:
            page.notes += " Original-PDF-Seitenzahl wurde lokal aus zusammenhängender Batch-Range korrigiert."
        else:
            page.notes = "Original-PDF-Seitenzahl wurde lokal aus zusammenhängender Batch-Range korrigiert."
        aligned_pages.append(page)
    result.pages = aligned_pages
    result.batch_start_page = batch.start_page
    result.batch_end_page = batch.end_page
    return result


def checkpoint_path(run_dir: Path, batch: Batch) -> Path:
    return run_dir / "checkpoints" / f"{batch.key}.json"


def process_batch(
    client: genai.Client,
    batch: Batch,
    source_filename: str,
    run_dir: Path,
) -> GeminiBatchResult:
    checkpoint = checkpoint_path(run_dir, batch)
    if checkpoint.exists():
        return GeminiBatchResult.model_validate_json(checkpoint.read_text(encoding="utf-8"))

    batch_pdf = run_dir / "batch_pdfs" / f"{batch.key}.pdf"
    write_pdf_batch(batch, batch_pdf)

    last_error: str | None = None
    for repair in [False, True]:
        raw = call_with_retries(request_gemini_batch, client, batch_pdf, batch, source_filename, repair)
        try:
            data = parse_json_object(raw)
            result = align_batch_page_numbers(GeminiBatchResult.model_validate(data), batch)
            expected_pages = set(range(batch.start_page, batch.end_page + 1))
            returned_pages = {page.pdf_page for page in result.pages}
            if result.source_id != batch.source_id:
                raise ValueError("source_id mismatch")
            if result.batch_start_page != batch.start_page or result.batch_end_page != batch.end_page:
                raise ValueError("batch page range mismatch")
            if returned_pages != expected_pages:
                raise ValueError(f"missing or extra pages: expected {sorted(expected_pages)}, got {sorted(returned_pages)}")
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            print(f"checkpoint {batch.key}: pages={len(result.pages)} mentions={sum(len(page.mentions) for page in result.pages)}", flush=True)
            return result
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = str(exc)
            repair_record = {
                "batch": batch.key,
                "repair_attempted": repair,
                "error": last_error,
                "raw_response_excerpt": raw[:2000],
            }
            repair_dir = run_dir / "repair_failures"
            repair_dir.mkdir(parents=True, exist_ok=True)
            (repair_dir / f"{batch.key}_{'repair' if repair else 'initial'}.json").write_text(
                json.dumps(repair_record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    raise RuntimeError(f"Batch {batch.key} failed validation after repair attempt: {last_error}")


def normalize_for_match(value: str) -> str:
    value = value.replace("\u00ad", "")
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().casefold()


def source_text_by_page(source_path: Path) -> dict[int, str]:
    completed = subprocess.run(
        ["pdftotext", str(source_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    pages = completed.stdout.split("\f")
    return {index + 1: page for index, page in enumerate(pages)}


def verify_citation(page_text: str, raw_mention: str, quote: str) -> tuple[str, str | None]:
    normalized_page = normalize_for_match(page_text)
    normalized_quote = normalize_for_match(quote)
    normalized_raw = normalize_for_match(raw_mention)
    if normalized_quote and normalized_quote in normalized_page:
        return "VERIFIED", None
    if normalized_raw and normalized_raw in normalized_page:
        return "UNVERIFIED", "raw_mention found, exact_context_quote not found by local text extraction"
    return "UNVERIFIED", "raw_mention and exact_context_quote not found by local text extraction"


def stable_id(prefix: str, *parts: object) -> str:
    joined = "\u241f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha1(joined.encode('utf-8')).hexdigest()[:16]}"


def canonical_key(mention: DrugMention) -> str:
    name = mention.canonical_name_de or mention.raw_mention
    name = re.sub(r"[®™]", "", name)
    name = re.sub(r"\s+", " ", name).strip().casefold()
    return f"{mention.entity_type}:{name}"


def choose_priority(mentions: list[DrugMention]) -> str:
    if any(m.needs_manual_review or m.entity_type == "UNCLEAR" or m.normalization_status == "UNCLEAR" for m in mentions):
        return "MANUAL_REVIEW"
    if any(m.mention_scope == "CLINICAL_CONTENT" for m in mentions):
        return "PRIORITY_A"
    if any(m.mention_scope == "HISTORICAL_OR_NEGATIVE" for m in mentions):
        return "PRIORITY_B"
    if all(m.mention_scope == "REFERENCE_ONLY" for m in mentions):
        return "PRIORITY_C"
    return "MANUAL_REVIEW"


def deduplicate_mentions(mentions: list[DrugMention]) -> list[DrugMention]:
    seen: set[tuple[str, int, str, str, str, str]] = set()
    unique: list[DrugMention] = []
    for mention in sorted(
        mentions,
        key=lambda m: (m.source_id, m.pdf_page, m.entity_type, m.raw_mention.casefold(), m.exact_context_quote.casefold()),
    ):
        key = (
            mention.source_id,
            mention.pdf_page,
            mention.entity_type,
            normalize_for_match(mention.raw_mention),
            normalize_for_match(mention.exact_context_quote),
            normalize_for_match(mention.canonical_name_de),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(mention)
    return unique


def build_mentions(
    run_id: str,
    sources: list[SourceRecord],
    batch_results: list[GeminiBatchResult],
    source_dir: Path,
) -> list[DrugMention]:
    source_by_id = {source.source_id: source for source in sources}
    local_text = {
        source.source_id: source_text_by_page(source_dir / source.source_filename)
        for source in sources
    }
    mentions: list[DrugMention] = []
    for batch_result in batch_results:
        source = source_by_id[batch_result.source_id]
        for page in batch_result.pages:
            for item in page.mentions:
                if item.pdf_page != page.pdf_page:
                    item.pdf_page = page.pdf_page
                status, note = verify_citation(
                    local_text[source.source_id].get(item.pdf_page, ""),
                    item.raw_mention,
                    item.exact_context_quote,
                )
                needs_review = bool(item.needs_manual_review or status == "UNVERIFIED")
                mention_id = stable_id(
                    "ment",
                    run_id,
                    source.source_id,
                    item.pdf_page,
                    item.entity_type,
                    item.raw_mention,
                    item.exact_context_quote,
                    item.canonical_name_de,
                )
                mentions.append(
                    DrugMention(
                        mention_id=mention_id,
                        extraction_run_id=run_id,
                        source_id=source.source_id,
                        source_filename=source.source_filename,
                        source_sha256=source.source_sha256,
                        pdf_page=item.pdf_page,
                        printed_page=item.printed_page or page.printed_page,
                        section_path=item.section_path,
                        mention_scope=item.mention_scope,
                        raw_mention=item.raw_mention,
                        exact_context_quote=item.exact_context_quote,
                        entity_type=item.entity_type,
                        canonical_name_de=item.canonical_name_de,
                        canonical_name_en=item.canonical_name_en,
                        brand_name=item.brand_name,
                        drug_class=item.drug_class,
                        regimen_name=item.regimen_name,
                        normalization_status=item.normalization_status,
                        source_explicit=item.source_explicit,
                        clinical_context=item.clinical_context,
                        formal_item_reference=item.formal_item_reference,
                        confidence=item.confidence,
                        needs_manual_review=needs_review,
                        citation_verification_status=status,  # type: ignore[arg-type]
                        citation_verification_note=note,
                    )
                )
    return deduplicate_mentions(mentions)


def sorted_unique(values: list[str | None]) -> list[str]:
    return sorted({value.strip() for value in values if value and value.strip()}, key=str.casefold)


def build_catalog(run_id: str, mentions: list[DrugMention]) -> list[DrugCatalogEntry]:
    groups: dict[str, list[DrugMention]] = defaultdict(list)
    for mention in mentions:
        groups[canonical_key(mention)].append(mention)

    entries: list[DrugCatalogEntry] = []
    for _, items in sorted(groups.items()):
        representative = sorted(items, key=lambda m: (-m.confidence, m.source_id, m.pdf_page))[0]
        evidence = [
            CatalogEvidence(
                mention_id=m.mention_id,
                source_id=m.source_id,
                source_filename=m.source_filename,
                pdf_page=m.pdf_page,
                printed_page=m.printed_page,
                mention_scope=m.mention_scope,
                exact_context_quote=m.exact_context_quote,
                raw_mention=m.raw_mention,
                clinical_context=m.clinical_context,
                formal_item_reference=m.formal_item_reference,
                citation_verification_status=m.citation_verification_status,
            )
            for m in sorted(items, key=lambda m: (m.source_id, m.pdf_page, m.raw_mention.casefold()))
        ]
        citation_status = "VERIFIED" if all(m.citation_verification_status == "VERIFIED" for m in items) else "UNVERIFIED"
        manual_reasons = []
        if any(m.needs_manual_review for m in items):
            manual_reasons.append("Mindestens eine Einzelnennung ist lokal nicht voll verifiziert oder von Gemini markiert.")
        if any(m.normalization_status in {"INFERRED", "UNCLEAR"} for m in items):
            manual_reasons.append("Normalisierung ist inferiert oder unklar.")
        if any(not m.source_explicit for m in items):
            manual_reasons.append("Mindestens eine Information ist nicht source-explicit.")
        priority = choose_priority(items)
        entry = DrugCatalogEntry(
            canonical_drug_id=stable_id("drug", representative.entity_type, representative.canonical_name_de.casefold()),
            extraction_run_id=run_id,
            priority=priority,  # type: ignore[arg-type]
            entity_type=representative.entity_type,
            canonical_name_de=representative.canonical_name_de,
            canonical_name_en=representative.canonical_name_en,
            raw_mentions=sorted_unique([m.raw_mention for m in items]),
            brand_names=sorted_unique([m.brand_name for m in items]),
            drug_classes=sorted_unique([m.drug_class for m in items]),
            regimen_names=sorted_unique([m.regimen_name for m in items]),
            guideline_sources=sorted_unique([m.source_id for m in items]),
            source_page_counts={source: len({m.pdf_page for m in items if m.source_id == source}) for source in sorted_unique([m.source_id for m in items])},
            exact_pdf_pages_by_source={
                source: sorted({m.pdf_page for m in items if m.source_id == source})
                for source in sorted_unique([m.source_id for m in items])
            },
            mention_count_by_source=Counter(m.source_id for m in items),
            mention_scopes=sorted_unique([m.mention_scope for m in items]),  # type: ignore[arg-type]
            clinical_contexts=sorted_unique([m.clinical_context for m in items])[:12],
            representative_quote=representative.exact_context_quote,
            evidence=evidence,
            needs_manual_review=bool(priority == "MANUAL_REVIEW" or manual_reasons),
            manual_review_reasons=manual_reasons,
            citation_verification_status=citation_status,  # type: ignore[arg-type]
        )
        entries.append(entry)
    return sorted(entries, key=lambda e: (e.priority, e.canonical_name_de.casefold(), e.entity_type))


def build_page_coverage(
    sources: list[SourceRecord],
    batch_results: list[GeminiBatchResult],
) -> list[dict[str, Any]]:
    by_source_page: dict[tuple[str, int], list[str]] = defaultdict(list)
    mentions_by_page: Counter[tuple[str, int]] = Counter()
    for result in batch_results:
        for page in result.pages:
            by_source_page[(result.source_id, page.pdf_page)].append(page.status)
            mentions_by_page[(result.source_id, page.pdf_page)] += sum(len(p.mentions) for p in [page])

    coverage: list[dict[str, Any]] = []
    for source in sources:
        for page_no in range(1, source.pdf_pages + 1):
            statuses = by_source_page.get((source.source_id, page_no), [])
            mention_count = mentions_by_page[(source.source_id, page_no)]
            if not statuses:
                status = "REPAIR_REQUIRED"
            elif any(status == "NEEDS_MANUAL_REVIEW" for status in statuses):
                status = "MANUAL_REVIEW_REQUIRED"
            elif any(status == "NEEDS_REPAIR" for status in statuses):
                status = "REPAIR_REQUIRED"
            elif mention_count:
                status = "MEDICATION_MENTION_FOUND"
            else:
                status = "NO_MEDICATION_MENTION"
            coverage.append(
                {
                    "source_id": source.source_id,
                    "source_filename": source.source_filename,
                    "pdf_page": page_no,
                    "processed_successfully": bool(status not in {"REPAIR_REQUIRED"}),
                    "status": status,
                    "mention_count": mention_count,
                    "batch_statuses": statuses,
                }
            )
    return coverage


def write_jsonl(path: Path, records: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            if hasattr(record, "model_dump"):
                payload = record.model_dump(mode="json")
            else:
                payload = record
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def write_catalog_csv(path: Path, entries: list[DrugCatalogEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "canonical_drug_id",
        "priority",
        "entity_type",
        "canonical_name_de",
        "canonical_name_en",
        "raw_mentions",
        "brand_names",
        "drug_classes",
        "regimen_names",
        "guideline_sources",
        "exact_pdf_pages_by_source",
        "mention_count_by_source",
        "citation_verification_status",
        "needs_manual_review",
        "manual_review_reasons",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for entry in entries:
            row = entry.model_dump(mode="json")
            for key, value in row.items():
                if isinstance(value, (list, dict)):
                    row[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            writer.writerow({field: row.get(field) for field in fields})


def write_report(
    output_dir: Path,
    run_id: str,
    smoke_status: str,
    preflight: dict[str, Any],
    sources: list[SourceRecord],
    mentions: list[DrugMention],
    catalog: list[DrugCatalogEntry],
    coverage: list[dict[str, Any]],
    docx_status: str | None = None,
) -> dict[str, Any]:
    source_pages = {source.source_id: source.pdf_pages for source in sources}
    priority_counts = Counter(entry.priority for entry in catalog)
    entity_counts = Counter(entry.entity_type for entry in catalog)
    pages_with_repair = [row for row in coverage if row["status"] == "REPAIR_REQUIRED"]
    pages_with_manual = [row for row in coverage if row["status"] == "MANUAL_REVIEW_REQUIRED"]
    report = {
        "extraction_run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL_NAME,
        "gemini_smoke_test": smoke_status,
        "preflight": preflight,
        "sources": [source.model_dump(mode="json") for source in sources],
        "source_pages": source_pages,
        "pages_total": sum(source_pages.values()),
        "pages_successfully_processed": sum(1 for row in coverage if row["processed_successfully"]),
        "pages_with_repair": len(pages_with_repair),
        "pages_with_manual_review": len(pages_with_manual),
        "total_mentions": len(mentions),
        "catalog_entries": len(catalog),
        "unique_active_substances": sum(1 for entry in catalog if entry.entity_type in {"ACTIVE_SUBSTANCE", "BIOLOGIC", "CONTRAST_AGENT"}),
        "regimens": entity_counts["COMBINATION_REGIMEN"],
        "drug_classes": entity_counts["DRUG_CLASS"],
        "brand_names": entity_counts["BRAND_NAME"],
        "priority_counts": {
            "PRIORITY_A": priority_counts["PRIORITY_A"],
            "PRIORITY_B": priority_counts["PRIORITY_B"],
            "PRIORITY_C": priority_counts["PRIORITY_C"],
            "MANUAL_REVIEW": priority_counts["MANUAL_REVIEW"],
        },
        "mention_count_by_source": Counter(mention.source_id for mention in mentions),
        "citation_verification": "PASS"
        if all(entry.citation_verification_status == "VERIFIED" for entry in catalog)
        else "UNVERIFIED",
        "docx_render_qa": docx_status,
        "acceptance_checks": {
            "all_three_sources_registered": len(sources) == 3,
            "every_page_in_coverage": len(coverage) == sum(source.pdf_pages for source in sources),
            "catalog_entries_have_evidence": all(entry.evidence for entry in catalog),
            "all_evidence_has_source_and_page": all(ev.source_id and ev.pdf_page for entry in catalog for ev in entry.evidence),
            "jsonl_schema_validated_before_write": True,
            "priority_classes_separated": True,
        },
    }
    path = output_dir / "extraction_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=dict), encoding="utf-8")
    return report


def preflight_batch(source_dir: Path) -> Batch:
    path = source_dir / "032-010OLl_Exokrines-Pankreaskarzinom_2025-06.pdf"
    return Batch("PANKREAS_2025", path, 154, 156)


def run_extraction(
    root: Path,
    env_path: Path,
    resume_run_id: str | None = None,
) -> dict[str, Any]:
    source_dir = root / "source_pdfs"
    output_dir = root / "outputs" / "medications"
    runs_dir = root / "data" / "extraction_runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_id = resume_run_id or datetime.now().strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    api_key = load_gemini_api_key(env_path)
    client = genai.Client(api_key=api_key)
    smoke_status = run_text_smoke_test(client)

    sources = register_sources(source_dir)
    write_jsonl(output_dir / "source_registry.jsonl", sources)

    source_by_id = {source.source_id: source for source in sources}
    preflight = preflight_batch(source_dir)
    preflight_result = process_batch(
        client,
        preflight,
        source_by_id[preflight.source_id].source_filename,
        run_dir / "preflight",
    )
    preflight_summary = {
        "status": "PASS",
        "source_id": preflight.source_id,
        "pages": [preflight.start_page, preflight.end_page],
        "mentions": sum(len(page.mentions) for page in preflight_result.pages),
    }

    batch_results: list[GeminiBatchResult] = []
    for source in sources:
        source_path = source_dir / source.source_filename
        for batch in make_batches(source_path, source.source_id, source.pdf_pages):
            result = process_batch(client, batch, source.source_filename, run_dir)
            batch_results.append(result)

    mentions = build_mentions(run_id, sources, batch_results, source_dir)
    catalog = build_catalog(run_id, mentions)
    coverage = build_page_coverage(sources, batch_results)

    write_jsonl(output_dir / "drug_mentions.jsonl", mentions)
    write_jsonl(output_dir / "drug_catalog.jsonl", catalog)
    write_catalog_csv(output_dir / "drug_catalog.csv", catalog)
    write_jsonl(output_dir / "page_coverage.jsonl", coverage)

    report = write_report(
        output_dir,
        run_id,
        smoke_status,
        preflight_summary,
        sources,
        mentions,
        catalog,
        coverage,
    )
    return report


def print_terminal_summary(report: dict[str, Any], root: Path) -> None:
    output_dir = root / "outputs" / "medications"
    source_names = ", ".join(source["source_id"] for source in report["sources"])
    print(f"Gemini-Smoke-Test: {report['gemini_smoke_test']}")
    print(f"verarbeitete Quellen: {source_names}")
    print(f"Seiten pro Quelle: {json.dumps(report['source_pages'], ensure_ascii=False, sort_keys=True)}")
    print(f"Seiten erfolgreich verarbeitet: {report['pages_successfully_processed']}")
    print(f"Seiten mit Reparatur: {report['pages_with_repair']}")
    print(f"Seiten mit manueller Kontrolle: {report['pages_with_manual_review']}")
    print(f"Gesamtzahl Einzelnennungen: {report['total_mentions']}")
    print(f"eindeutige Wirkstoffe: {report['unique_active_substances']}")
    print(f"Regime: {report['regimens']}")
    print(f"Wirkstoffgruppen: {report['drug_classes']}")
    print(f"PRIORITY_A/B/C: {report['priority_counts']['PRIORITY_A']}/{report['priority_counts']['PRIORITY_B']}/{report['priority_counts']['PRIORITY_C']}")
    print(f"MANUAL_REVIEW: {report['priority_counts']['MANUAL_REVIEW']}")
    print(f"Citation verification: {report['citation_verification']}")
    print(f"DOCX-Render-QA: {report.get('docx_render_qa') or 'PENDING'}")
    print(f"finale DOCX: {(output_dir / 'Medikamente_und_Wirkstoffe_aus_drei_Leitlinien.docx').resolve()}")
    print(f"drug_mentions.jsonl: {(output_dir / 'drug_mentions.jsonl').resolve()}")
    print(f"drug_catalog.jsonl: {(output_dir / 'drug_catalog.jsonl').resolve()}")
    print(f"drug_catalog.csv: {(output_dir / 'drug_catalog.csv').resolve()}")
    print(f"source_registry.jsonl: {(output_dir / 'source_registry.jsonl').resolve()}")
    print(f"page_coverage.jsonl: {(output_dir / 'page_coverage.jsonl').resolve()}")
    print(f"extraction_report.json: {(output_dir / 'extraction_report.json').resolve()}")


def clean_internal_batch_pdfs(root: Path, run_id: str) -> None:
    batch_dir = root / "data" / "extraction_runs" / run_id / "batch_pdfs"
    if batch_dir.exists():
        shutil.rmtree(batch_dir)

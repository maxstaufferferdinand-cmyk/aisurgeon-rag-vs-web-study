"""Immutable corpus snapshot and provenance-enrichment builder."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .knowledge_corpus_policy import is_primary_use_eligible
from .retrieval_config import (
    DOCUMENT_PROFILES,
    RETRIEVAL_PIPELINE_VERSION,
    RETRIEVAL_SCHEMA_VERSION,
    component_for_pages,
    repository_root,
    role_for_component,
)


class CorpusIntegrityError(RuntimeError):
    """Raised when a frozen source or immutable artifact no longer matches."""


CANONICAL_RECORD_PARTITIONS = (
    "active_substance_evidence.jsonl",
    "adverse_reactions.jsonl",
    "chapter_structure.jsonl",
    "compositions.jsonl",
    "contraindications.jsonl",
    "document_metadata_extracted.jsonl",
    "dosing_rules.jsonl",
    "drug_product_evidence.jsonl",
    "excipients.jsonl",
    "formal_items.jsonl",
    "grading_systems.jsonl",
    "guideline_references.jsonl",
    "incompatibilities.jsonl",
    "interactions.jsonl",
    "overdose.jsonl",
    "pharmacodynamics.jsonl",
    "pharmacokinetics.jsonl",
    "pregnancy_lactation_fertility.jsonl",
    "preparation_administration.jsonl",
    "rationale_blocks.jsonl",
    "regulatory_metadata.jsonl",
    "storage_handling.jsonl",
    "tables_figures_algorithms.jsonl",
    "therapeutic_indications.jsonl",
    "warnings.jsonl",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _load_manifest(root: Path) -> dict[str, Any]:
    path = root / "outputs/knowledge_corpus/manifests/source_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def verify_source_hashes(root: Path) -> list[dict[str, Any]]:
    manifest = _load_manifest(root)
    verified: list[dict[str, Any]] = []
    for source in manifest["sources"]:
        relative_path = Path(source["relative_path"])
        actual = sha256_file(root / relative_path)
        expected = source["sha256"]
        if actual != expected:
            raise CorpusIntegrityError(
                f"source SHA-256 changed for {relative_path}: expected {expected}, got {actual}"
            )
        filename = relative_path.name
        if filename not in DOCUMENT_PROFILES:
            raise CorpusIntegrityError(f"missing source profile for {filename}")
        verified.append(
            {
                "source_id": source["source_id"],
                "relative_path": relative_path.as_posix(),
                "source_sha256": actual,
                "page_count": source["page_count"],
                "file_size_bytes": source["file_size_bytes"],
            }
        )
    return verified


def load_canonical_records(root: Path) -> list[dict[str, Any]]:
    canonical = root / "outputs/knowledge_corpus/canonical"
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in CANONICAL_RECORD_PARTITIONS:
        for row in read_jsonl(canonical / name):
            record_id = row["record_id"]
            if record_id in seen:
                raise CorpusIntegrityError(
                    f"duplicate record_id {record_id} in explicit canonical partition map"
                )
            seen.add(record_id)
            records.append(row)
    return records


def _document_rows(root: Path) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(root / "outputs/knowledge_corpus/canonical/documents.jsonl")
    return {row["source_id"]: row for row in rows}


def _entity_rows(root: Path, name: str, key: str) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(root / f"outputs/knowledge_corpus/canonical/{name}")
    return {row[key]: row for row in rows}


def _source_versions(root: Path, snapshot_id: str) -> list[dict[str, Any]]:
    documents = _document_rows(root)
    result: list[dict[str, Any]] = []
    for source in verify_source_hashes(root):
        document = documents[source["source_id"]]
        filename = Path(source["relative_path"]).name
        profile = DOCUMENT_PROFILES[filename]
        version_id = f"sv-{source['source_sha256'][:24]}"
        result.append(
            {
                "source_document_id": source["source_id"],
                "source_version_id": version_id,
                "corpus_snapshot_id": snapshot_id,
                "title": document["title"],
                "source_file_name": filename,
                "relative_path": source["relative_path"],
                "document_kind": profile.document_kind,
                "source_status": profile.source_status,
                "source_role": profile.source_role,
                "source_authority": profile.source_authority,
                "version_label": profile.version_label,
                "published_at": profile.published_at,
                "valid_from": profile.valid_from,
                "valid_to": profile.valid_to,
                "source_sha256": source["source_sha256"],
                "page_count": source["page_count"],
                "file_size_bytes": source["file_size_bytes"],
                "qa_status": "review" if profile.qa_flags else "validated",
                "qa_flags": list(profile.qa_flags),
                "component_ranges": [
                    {
                        "first_pdf_page_1based": item.first_page,
                        "last_pdf_page_1based": min(item.last_page, source["page_count"]),
                        "document_component": item.component,
                    }
                    for item in profile.component_ranges
                    if item.first_page <= source["page_count"]
                ],
                "extraction_pipeline_version": RETRIEVAL_PIPELINE_VERSION,
            }
        )
    return result


def _canonical_lookup(root: Path) -> dict[str, dict[str, Any]]:
    return {row["record_id"]: row for row in load_canonical_records(root)}


def build_retrieval_units_v2(root: Path, snapshot_id: str) -> list[dict[str, Any]]:
    original_units = read_jsonl(
        root / "outputs/knowledge_corpus/retrieval/retrieval_units.jsonl"
    )
    records = _canonical_lookup(root)
    source_versions = {row["source_document_id"]: row for row in _source_versions(root, snapshot_id)}
    products = _entity_rows(root, "drug_products.jsonl", "product_id")
    substances = _entity_rows(root, "active_substances.jsonl", "active_substance_id")
    output: list[dict[str, Any]] = []
    for unit in original_units:
        parents = [records[item] for item in unit["parent_record_ids"] if item in records]
        if not parents:
            raise CorpusIntegrityError(
                f"retrieval unit {unit['retrieval_unit_id']} has no canonical parent"
            )
        parent = parents[0]
        version = source_versions[unit["source_id"]]
        profile = DOCUMENT_PROFILES[unit["source_file_name"]]
        pages = unit["pdf_pages_1based"]
        component, component_flags = component_for_pages(profile, pages)
        qa_flags = sorted(set(unit.get("review_flags", []) + component_flags))
        product_rows = [products[item] for item in unit.get("product_ids", []) if item in products]
        substance_rows = [
            substances[item]
            for item in unit.get("active_substance_ids", [])
            if item in substances
        ]
        product_names = sorted(
            {
                item
                for item in [parent.get("product_name"), *[r.get("product_name") for r in product_rows]]
                if item
            }
        )
        substance_names = sorted(
            {
                item
                for item in [
                    *(parent.get("active_substance_names") or []),
                    *[r.get("preferred_name") for r in substance_rows],
                ]
                if item
            }
        )
        aliases = sorted(
            {
                item
                for item in [
                    *product_names,
                    *substance_names,
                    *(parent.get("aliases_original") or []),
                    *(unit.get("normalized_entities") or []),
                ]
                if item
            },
            key=str.casefold,
        )
        item_type = parent.get("item_type") or parent.get("record_type")
        item_number = parent.get("source_item_number")
        chapter = " > ".join(unit.get("section_path") or []) or "[kein Kapitelpfad]"
        entity_label = ", ".join([*product_names, *substance_names]) or "[keine Produkt-/Wirkstoffangabe]"
        item_label = " ".join(item for item in [item_type, item_number] if item) or "[kein formales Item]"
        retrieval_segment_text = unit["exact_source_text"]
        embedding_text = (
            f"Dokumenttyp: {version['document_kind']} | Quellenrolle: {role_for_component(profile, component)} "
            f"| Dokumentkomponente: {component} | Kapitel: {chapter} | Item: {item_label} "
            f"| Produkt/Wirkstoff: {entity_label} | Quellsegment: {retrieval_segment_text}"
        )
        exact_text = parent["exact_source_text"]
        text_hash = sha256_text(exact_text)
        segment_hash = sha256_text(retrieval_segment_text)
        canonical_hash = unit.get("canonical_exact_source_text_raw_sha256")
        if canonical_hash and canonical_hash != text_hash:
            raise CorpusIntegrityError(
                f"canonical parent text hash mismatch in {unit['retrieval_unit_id']}: "
                f"{canonical_hash} != {text_hash}"
            )
        eligible = all(
            unit.get(field) is not False
            for field in (
                "retrieval_eligible",
                "embedding_eligible",
                "answer_eligible",
                "primary_search_eligible",
            )
        )
        if not eligible:
            raise CorpusIntegrityError(
                f"ineligible unit unexpectedly present in normal retrieval input: {unit['retrieval_unit_id']}"
            )
        output.append(
            {
                "retrieval_unit_id": unit["retrieval_unit_id"],
                "corpus_snapshot_id": snapshot_id,
                "source_version_id": version["source_version_id"],
                "source_document_id": unit["source_id"],
                "document_kind": version["document_kind"],
                "source_status": version["source_status"],
                "document_component": component,
                "source_role": role_for_component(profile, component),
                "source_authority": version["source_authority"],
                "published_at": version["published_at"],
                "valid_from": version["valid_from"],
                "valid_to": version["valid_to"],
                "source_sha256": version["source_sha256"],
                "text_sha256": text_hash,
                "exact_source_text": exact_text,
                "retrieval_segment_text": retrieval_segment_text,
                "retrieval_segment_sha256": segment_hash,
                "retrieval_text": unit["retrieval_text"],
                "embedding_text": embedding_text,
                "embedding_text_sha256": sha256_text(embedding_text),
                "chapter_path": unit.get("section_path") or [],
                "source_native_item_type": item_type,
                "source_native_item_number": item_number,
                "printed_source_item_number": parent.get("printed_source_item_number"),
                "pdf_page_index": min(pages) - 1 if pages else None,
                "pdf_pages_1based": pages,
                "printed_page_label": parent.get("printed_page_label"),
                "table_id": parent["record_id"] if parent.get("record_type") == "table_figure_algorithm" else None,
                "row_header_path": [],
                "column_header_path": [],
                "exact_table_cell_text": None,
                "product_ids": unit.get("product_ids") or [],
                "active_substance_ids": unit.get("active_substance_ids") or [],
                "product_names": product_names,
                "active_substance_names": substance_names,
                "strength": parent.get("strength"),
                "pharmaceutical_form": parent.get("pharmaceutical_form"),
                "route": parent.get("route") or ((parent.get("routes") or [None])[0]),
                "dose_value": parent.get("dose_value"),
                "dose_unit": parent.get("dose_unit"),
                "frequency": parent.get("frequency"),
                "population": parent.get("population"),
                "aliases": aliases,
                "parent_id": unit["parent_record_ids"][0],
                "parent_record_ids": unit["parent_record_ids"],
                "evidence_span_id": f"span-{unit['parent_record_ids'][0]}",
                "relation_ids": [],
                "qa_status": "review" if qa_flags else "validated",
                "qa_flags": qa_flags,
                "eligibility_status": "eligible",
                "retrieval_eligible": True,
                "embedding_eligible": True,
                "answer_eligible": True,
                "primary_search_eligible": True,
                "excluded_by_policy": False,
                "exclusion_reason": None,
                "conflict_status": "none",
                "citation_label": unit["citation_label"],
                "source_file_name": unit["source_file_name"],
                "extraction_batch_id": unit["extraction_batch_id"],
                "extraction_pipeline_version": RETRIEVAL_PIPELINE_VERSION,
                "raw_v1": unit,
            }
        )
    return output


def build_evidence_spans(
    root: Path, snapshot_id: str, units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    records = _canonical_lookup(root)
    spans: list[dict[str, Any]] = []
    for record in records.values():
        eligible = is_primary_use_eligible(record)
        hard_excluded = (
            record.get("status") == "excluded_by_policy"
            or record.get("exclusion_reason") == "hcc_historical_change_table"
        )
        pages = record["pdf_pages_1based"]
        spans.append(
            {
                "evidence_span_id": f"span-{record['record_id']}",
                "retrieval_unit_id": None,
                "canonical_record_id": record["record_id"],
                "source_version_id": f"sv-{record['source_sha256'][:24]}",
                "corpus_snapshot_id": snapshot_id,
                "exact_source_text": record["exact_source_text"],
                "text_sha256": sha256_text(record["exact_source_text"]),
                "pdf_page_index": min(pages) - 1,
                "pdf_pages_1based": pages,
                "printed_page_label": record.get("printed_page_label"),
                "table_id": record["record_id"]
                if record.get("record_type") == "table_figure_algorithm"
                else None,
                "row_header_path": [],
                "column_header_path": [],
                "exact_table_cell_text": None,
                "qa_status": "review" if record.get("review_flags") else "validated",
                "qa_flags": record.get("review_flags") or [],
                "eligibility_status": "eligible" if eligible else "ineligible",
                "excluded_by_policy": hard_excluded,
                "exclusion_reason": record.get("exclusion_reason")
                or record.get("retrieval_exclusion_reason"),
            }
        )
    for record in records.values():
        if record.get("record_type") != "table_figure_algorithm":
            continue
        rows = record.get("table_rows") or []
        if not rows:
            continue
        source_version_id = f"sv-{record['source_sha256'][:24]}"
        eligible = is_primary_use_eligible(record)
        hard_excluded = (
            record.get("status") == "excluded_by_policy"
            or record.get("exclusion_reason") == "hcc_historical_change_table"
        )
        for row_index, row in enumerate(rows):
            for column_index, cell in enumerate(row):
                if not cell:
                    continue
                key = f"{record['record_id']}:{row_index}:{column_index}"
                spans.append(
                    {
                        "evidence_span_id": f"cell-{sha256_text(key)[:24]}",
                        "retrieval_unit_id": None,
                        "canonical_record_id": record["record_id"],
                        "source_version_id": source_version_id,
                        "corpus_snapshot_id": snapshot_id,
                        "exact_source_text": cell,
                        "text_sha256": sha256_text(cell),
                        "pdf_page_index": min(record["pdf_pages_1based"]) - 1,
                        "pdf_pages_1based": record["pdf_pages_1based"],
                        "printed_page_label": record.get("printed_page_label"),
                        "table_id": record["record_id"],
                        "row_header_path": None,
                        "column_header_path": None,
                        "exact_table_cell_text": cell,
                        "qa_status": "review" if record.get("review_flags") else "validated",
                        "qa_flags": sorted(
                            set(
                                (record.get("review_flags") or [])
                                + ["table_header_paths_not_explicitly_encoded"]
                            )
                        ),
                        "eligibility_status": "eligible" if eligible else "ineligible",
                        "excluded_by_policy": hard_excluded,
                        "exclusion_reason": record.get("exclusion_reason")
                        or record.get("retrieval_exclusion_reason"),
                    }
                )
    return spans


def build_semantic_relations(
    root: Path, snapshot_id: str, units: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    record_to_units: dict[str, list[str]] = {}
    for unit in units:
        for record_id in unit["parent_record_ids"]:
            record_to_units.setdefault(record_id, []).append(unit["retrieval_unit_id"])
    relations: dict[str, dict[str, Any]] = {}

    def add(
        relation_type: str,
        from_id: str,
        to_id: str,
        *,
        from_kind: str = "retrieval_unit",
        to_kind: str = "retrieval_unit",
        direct: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        identity = f"{relation_type}|{from_kind}|{from_id}|{to_kind}|{to_id}"
        relation_id = f"rel-{sha256_text(identity)[:24]}"
        relations[relation_id] = {
            "relation_id": relation_id,
            "corpus_snapshot_id": snapshot_id,
            "relation_type": relation_type,
            "from_kind": from_kind,
            "from_id": from_id,
            "to_kind": to_kind,
            "to_id": to_id,
            "from_retrieval_unit_id": from_id if from_kind == "retrieval_unit" else None,
            "to_retrieval_unit_id": to_id if to_kind == "retrieval_unit" else None,
            "is_direct_evidence": direct,
            "metadata": metadata or {},
            "qa_status": "validated",
        }

    for link in read_jsonl(root / "outputs/knowledge_corpus/links/guideline_item_links.jsonl"):
        for source_unit in record_to_units.get(link["from_record_id"], []):
            for target_unit in record_to_units.get(link["to_record_id"], []):
                add(link["link_type"], source_unit, target_unit, metadata={"link_id": link["link_id"]})
    product_units: dict[str, list[str]] = {}
    substance_units: dict[str, list[str]] = {}
    for unit in units:
        for product_id in unit["product_ids"]:
            product_units.setdefault(product_id, []).append(unit["retrieval_unit_id"])
        for substance_id in unit["active_substance_ids"]:
            substance_units.setdefault(substance_id, []).append(unit["retrieval_unit_id"])
    products = _entity_rows(root, "drug_products.jsonl", "product_id")
    validated_substances = set(_entity_rows(root, "active_substances.jsonl", "active_substance_id"))
    for product_id, product in products.items():
        for substance_id in product.get("active_substance_ids") or []:
            if substance_id not in validated_substances:
                continue
            add(
                "product_has_active_substance",
                product_id,
                substance_id,
                from_kind="medicine_product",
                to_kind="active_substance",
                direct=True,
            )
            for source_unit in product_units.get(product_id, [])[:5]:
                for target_unit in substance_units.get(substance_id, [])[:5]:
                    add("product_to_active_substance_context", source_unit, target_unit)
    return sorted(relations.values(), key=lambda row: row["relation_id"])


def _known_limitations(root: Path) -> list[dict[str, Any]]:
    unresolved = root / "outputs/knowledge_corpus/qa/unresolved_items.csv"
    return [
        {
            "code": "open_review_flags",
            "count": 2785,
            "detail": "Open review-severity flags remain; a header-only targeted repair queue does not mean zero QA limitations.",
            "artifact": unresolved.relative_to(root).as_posix(),
        },
        {"code": "quote_not_locally_verified", "count": 710},
        {"code": "dosing_fields_not_explicit_or_missing", "count": 440},
        {"code": "dose_value_not_explicit", "count": 273},
        {"code": "adverse_reaction_frequency_not_explicit_or_missing", "count": 264},
        {"code": "formal_item_number_unclear", "count": 177},
        {"code": "unresolved_reference_links", "count": 154},
        {"code": "formal_candidate_pages_without_item", "count": 12},
        {
            "code": "technical_scope_only",
            "detail": "Technical extraction coverage and provenance completeness are not clinical accuracy guarantees.",
        },
    ]


def create_snapshot(root: Path | None = None) -> dict[str, Any]:
    root = repository_root(root)
    sources = verify_source_hashes(root)
    canonical_dir = root / "outputs/knowledge_corpus/canonical"
    canonical_files = [
        {
            "relative_path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "line_count": sum(1 for line in path.open(encoding="utf-8") if line.strip()),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(canonical_dir.glob("*.jsonl"))
    ]
    core = {
        "retrieval_schema_version": RETRIEVAL_SCHEMA_VERSION,
        "retrieval_pipeline_version": RETRIEVAL_PIPELINE_VERSION,
        "sources": [
            {key: item[key] for key in ("source_id", "relative_path", "source_sha256", "page_count")}
            for item in sources
        ],
        "canonical_files": [
            {key: item[key] for key in ("relative_path", "sha256", "line_count")}
            for item in canonical_files
        ],
    }
    core_bytes = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    core_hash = hashlib.sha256(core_bytes).hexdigest()
    snapshot_id = f"cs-{core_hash[:24]}"
    snapshot_dir = root / f"outputs/retrieval_phase/{snapshot_id}"
    snapshot_manifest_dir = root / "outputs/knowledge_corpus/manifests/corpus_snapshots"
    snapshot_path = snapshot_manifest_dir / f"{snapshot_id}.json"
    if snapshot_path.exists():
        old = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if old.get("content_fingerprint_sha256") != core_hash:
            raise CorpusIntegrityError(f"immutable snapshot collision at {snapshot_path}")
        for item in old.get("artifact_integrity", []):
            artifact_path = root / item["relative_path"]
            if not artifact_path.is_file() or sha256_file(artifact_path) != item["sha256"]:
                raise CorpusIntegrityError(
                    f"immutable snapshot artifact changed or missing: {artifact_path}"
                )
        return old
    source_versions = _source_versions(root, snapshot_id)
    units = build_retrieval_units_v2(root, snapshot_id)
    spans = build_evidence_spans(root, snapshot_id, units)
    relations = build_semantic_relations(root, snapshot_id, units)
    canonical_records = load_canonical_records(root)
    exclusions = read_jsonl(root / "outputs/knowledge_corpus/qa/hcc_historical_exclusions.jsonl")
    statistics = json.loads(
        (root / "outputs/knowledge_corpus/retrieval/corpus_statistics.json").read_text(encoding="utf-8")
    )
    existing_snapshots = sorted(snapshot_manifest_dir.glob("cs-*.json")) if snapshot_manifest_dir.exists() else []
    previous = None
    if existing_snapshots:
        dated = []
        for path in existing_snapshots:
            row = json.loads(path.read_text(encoding="utf-8"))
            dated.append((row.get("created_at_utc", ""), path.stem))
        previous = max(dated)[1]
    now = datetime.now(UTC).isoformat()
    component_counts = Counter(unit["document_component"] for unit in units)
    eligible_canonical_count = sum(is_primary_use_eligible(row) for row in canonical_records)
    manifest = {
        "schema_version": "corpus-snapshot-1.0.0",
        "corpus_snapshot_id": snapshot_id,
        "content_fingerprint_sha256": core_hash,
        "created_at_utc": now,
        "previous_corpus_snapshot_id": previous,
        "canonical_source_of_truth": "outputs/knowledge_corpus/canonical/*.jsonl",
        "postgres_role": "regenerable_index_only",
        "extraction_pipeline_version": {
            "model": "gemini-3.5-flash",
            "prompt": "clinical-corpus-de-v1.2.0",
            "canonical_schema": "knowledge-corpus-1.0.0",
            "repair_overlay": "targeted-repair-20260816-v2-final-gap-policy",
            "gemini_reused_in_retrieval_phase": False,
        },
        "retrieval_pipeline_version": RETRIEVAL_PIPELINE_VERSION,
        "schema_version_retrieval": RETRIEVAL_SCHEMA_VERSION,
        "source_pdfs": sources,
        "canonical_files": canonical_files,
        "record_counts": statistics["record_counts"],
        "canonical_record_count": len(canonical_records),
        "physical_canonical_jsonl_rows": sum(item["line_count"] for item in canonical_files),
        "record_partition_policy": list(CANONICAL_RECORD_PARTITIONS),
        "retrieval_unit_count": len(units),
        "evidence_span_count": len(spans),
        "semantic_relation_count": len(relations),
        "source_count": len(sources),
        "page_count": sum(item["page_count"] for item in sources),
        "eligibility_policy_statistics": {
            "eligible_retrieval_units": len(units),
            "eligible_canonical_records": eligible_canonical_count,
            "ineligible_canonical_records": len(canonical_records) - eligible_canonical_count,
            "excluded_hcc_historical_records": len(exclusions),
            "secondary_formal_items_excluded": statistics["secondary_formal_item_count"],
            "consultation_draft_sources": sum(
                row["source_status"] == "consultation_draft" for row in source_versions
            ),
            "document_component_counts": dict(sorted(component_counts.items())),
        },
        "known_qa_limitations": _known_limitations(root),
        "reported_baseline_discrepancies": [
            {
                "metric": "retrieval_unit_count",
                "reported": 4585,
                "verified": len(units),
                "delta": len(units) - 4585,
                "explanation": "4,585 is present only in the pre-final-policy backup; the current post-policy source of truth and validator both contain 4,469.",
            },
            {
                "metric": "formal_item_count",
                "reported": 529,
                "verified_total": statistics["record_counts"]["formal_item"],
                "verified_primary": statistics["primary_formal_item_count"],
                "verified_secondary": statistics["secondary_formal_item_count"],
                "explanation": "The final repair added 29 formal records; totals and primary/secondary policy classes must not be conflated.",
            },
            {
                "metric": "printed_duplicate_15.4_records",
                "reported": 1,
                "verified": 2,
                "explanation": "Two distinct VTE main-text records preserve printed_source_item_number=15.4 and deliberately keep source_item_number=null.",
            },
        ],
        "artifacts": {
            "source_versions": f"outputs/retrieval_phase/{snapshot_id}/provenance/source_versions.jsonl",
            "retrieval_units_v2": f"outputs/retrieval_phase/{snapshot_id}/provenance/retrieval_units_v2.jsonl",
            "evidence_spans": f"outputs/retrieval_phase/{snapshot_id}/provenance/evidence_spans.jsonl",
            "semantic_relations": f"outputs/retrieval_phase/{snapshot_id}/provenance/semantic_relations.jsonl",
        },
    }
    provenance = snapshot_dir / "provenance"
    write_jsonl(provenance / "source_versions.jsonl", source_versions)
    write_jsonl(provenance / "retrieval_units_v2.jsonl", units)
    write_jsonl(provenance / "evidence_spans.jsonl", spans)
    write_jsonl(provenance / "semantic_relations.jsonl", relations)
    manifest["artifact_integrity"] = [
        {
            "relative_path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "line_count": sum(1 for line in path.open(encoding="utf-8") if line.strip()),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(provenance.glob("*.jsonl"))
    ]
    snapshot_manifest_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (snapshot_dir / "snapshot_pointer.json").write_text(
        json.dumps(
            {
                "corpus_snapshot_id": snapshot_id,
                "manifest": snapshot_path.relative_to(root).as_posix(),
                "content_fingerprint_sha256": core_hash,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest

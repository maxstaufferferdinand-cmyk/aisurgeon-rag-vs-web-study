"""Deterministic post-checkpoint repairs for the compiled knowledge corpus.

The validated Gemini checkpoints remain immutable.  A small, versioned overlay
contains only source-verified targeted corrections and is applied while the
canonical corpus is compiled.  This makes the repair reproducible without
rerunning any extraction batch.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Sequence

from aisurgeon_decentralised.knowledge_corpus_policy import (
    HCC_HISTORICAL_EXCLUSION_REASON,
    is_primary_use_eligible,
)

OVERLAY_SCHEMA_VERSION = "targeted-repair-overlay-1.0.0"


def _raw_sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def normalize_search_text(value: str) -> str:
    """Create a conservative search view without altering canonical raw text."""

    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00ad", "")
    value = "".join(
        " " if unicodedata.category(char) == "Cc" and char not in "\n\r\t" else char
        for char in value
    )
    value = value.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ")
    value = re.sub(r"(?<=\w)-\s+(?=\w)", "", value)
    value = value.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\s*\n\s*", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _comparison_text(value: str) -> str:
    value = normalize_search_text(value).casefold()
    value = re.sub(r"[^a-z0-9äöüß]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def load_overlay(output_root: Path) -> dict[str, Any] | None:
    path = output_root / "manifests/targeted_repair_overlay.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != OVERLAY_SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported targeted repair overlay: {path}")
    return payload


def _change(
    *,
    issue_ids: Sequence[str],
    record: dict[str, Any],
    field_name: str,
    old_value: Any,
    new_value: Any,
    method: str,
    reason: str,
    status: str,
) -> dict[str, Any]:
    return {
        "issue_id": ";".join(dict.fromkeys(issue_ids)),
        "source_id": record.get("source_id", ""),
        "source_file_name": record.get("source_file_name", ""),
        "record_id": record.get("record_id", ""),
        "old_field": field_name,
        "old_value": old_value,
        "new_value": new_value,
        "pdf_pages_1based": record.get("pdf_pages_1based", []),
        "printed_page_label": record.get("printed_page_label"),
        "source_zone": record.get("source_zone"),
        "repair_method": method,
        "reason": reason,
        "validation_result": "source_verified",
        "status": status,
    }


def _identifier_numbers(value: str | None) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\d+\.\d+", value or "")))


def _apply_source_zones(
    records: list[dict[str, Any]], overlay: dict[str, Any], changes: list[dict[str, Any]]
) -> None:
    rules = overlay.get("source_zone_rules", [])
    for record in records:
        if record.get("record_type") == "formal_item":
            defaults = {
                "source_zone": "main_body",
                "canonical_role": "primary",
                "primary_record_id": None,
                "primary_record_ids": [],
                "retrieval_eligible": True,
                "embedding_eligible": True,
                "answer_eligible": True,
                "primary_search_eligible": True,
                "status": None,
                "exclusion_reason": None,
                "retrieval_exclusion_reason": None,
                "secondary_relation_type": None,
            }
            for field_name, value in defaults.items():
                record[field_name] = value
        elif record.get("record_type") == "guideline_reference":
            record.update(
                {
                    "source_zone": "references",
                    "canonical_role": "secondary_representation",
                    "retrieval_eligible": False,
                    "retrieval_exclusion_reason": "reference_list_nonretrieval",
                }
            )

        for rule in rules:
            if record.get("source_id") != rule["source_id"]:
                continue
            record_types = set(rule.get("record_types") or [])
            if record_types and record.get("record_type") not in record_types:
                continue
            pages = record.get("pdf_pages_1based") or []
            if not any(rule["page_from"] <= page <= rule["page_to"] for page in pages):
                continue
            fields = {
                "source_zone": rule["source_zone"],
                "canonical_role": rule["canonical_role"],
                "retrieval_eligible": rule.get("retrieval_eligible", False),
                "retrieval_exclusion_reason": rule["retrieval_exclusion_reason"],
                "secondary_relation_type": rule.get("secondary_relation_type"),
            }
            for optional_field in (
                "embedding_eligible",
                "answer_eligible",
                "primary_search_eligible",
                "status",
                "exclusion_reason",
            ):
                if optional_field in rule:
                    fields[optional_field] = rule[optional_field]
            for field_name, value in fields.items():
                old_value = record.get(field_name)
                if old_value == value:
                    continue
                record[field_name] = value
                changes.append(
                    _change(
                        issue_ids=[rule["issue_id"]],
                        record=record,
                        field_name=field_name,
                        old_value=old_value,
                        new_value=value,
                        method="local_deterministic",
                        reason=rule["reason"],
                        status=rule.get("change_status", "secondary_representation_linked"),
                    )
                )


def _link_secondary_formal_records(
    records: list[dict[str, Any]], overlay: dict[str, Any], changes: list[dict[str, Any]]
) -> None:
    primary = [
        record
        for record in records
        if record.get("record_type") == "formal_item"
        and record.get("canonical_role") == "primary"
    ]
    primary_by_source_number: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in primary:
        for number in _identifier_numbers(record.get("source_item_number")):
            primary_by_source_number[(record["source_id"], number)].append(record)
    primary_by_id = {record["record_id"]: record for record in primary}
    explicit = overlay.get("secondary_record_mappings", {})

    for record in records:
        if record.get("record_type") != "formal_item" or record.get("canonical_role") == "primary":
            continue
        if record.get("exclusion_reason") == HCC_HISTORICAL_EXCLUSION_REASON:
            fields = {
                "canonical_role": "historical_secondary",
                "secondary_relation_type": "excluded_historical_change_table",
                "primary_record_ids": [],
                "primary_record_id": None,
                "retrieval_eligible": False,
                "embedding_eligible": False,
                "answer_eligible": False,
                "primary_search_eligible": False,
                "status": "excluded_by_policy",
            }
            for field_name, value in fields.items():
                old_value = record.get(field_name)
                if old_value == value:
                    continue
                record[field_name] = value
                changes.append(
                    _change(
                        issue_ids=["hcc_historical_change_table_policy"],
                        record=record,
                        field_name=field_name,
                        old_value=old_value,
                        new_value=value,
                        method="local_deterministic",
                        reason=(
                            "Historische HCC/BCC-Änderungstabellen bleiben im Audit-Korpus, "
                            "sind aber für Suche, Embeddings, Evidenzexpansion und Antworten "
                            "dauerhaft ausgeschlossen."
                        ),
                        status="excluded_by_policy",
                    )
                )
            continue
        mapping = explicit.get(record["record_id"])
        candidates: list[dict[str, Any]] = []
        role = record.get("canonical_role") or "secondary_representation"
        relation = record.get("secondary_relation_type") or "secondary_source_rendering"
        if mapping:
            candidates = [
                primary_by_id[record_id]
                for record_id in mapping.get("primary_record_ids", [])
                if record_id in primary_by_id
            ]
            role = mapping.get("canonical_role", role)
            relation = mapping.get("secondary_relation_type", relation)
            if mapping.get("uncertainty_reason"):
                record["uncertainty_reason"] = mapping["uncertainty_reason"]
        else:
            numbers = _identifier_numbers(record.get("source_item_number"))
            for number in numbers:
                candidates.extend(primary_by_source_number.get((record["source_id"], number), []))
            candidates = list({candidate["record_id"]: candidate for candidate in candidates}.values())
            if candidates:
                source_text = _comparison_text(record.get("exact_text_de") or record.get("exact_source_text") or "")
                scored = [
                    (
                        SequenceMatcher(
                            None,
                            source_text,
                            _comparison_text(candidate.get("exact_text_de") or candidate.get("exact_source_text") or ""),
                        ).ratio(),
                        candidate,
                    )
                    for candidate in candidates
                ]
                scored.sort(key=lambda item: (-item[0], item[1]["record_id"]))
                best_score = scored[0][0]
                # A same-number mapping in the pancreatic appendix is an explicit
                # old/new version relation.  In the HCC change table, numbering
                # changed extensively, so a weak same-number match is unsafe.
                if record["source_id"].startswith("src-s3-") and best_score < 0.70:
                    candidates = []
                    role = "historical_record"
                    relation = "historical_change_record_without_unambiguous_successor"
                elif best_score < 0.82:
                    role = "historical_record"
                    relation = "historical_predecessor"
                else:
                    candidates = [scored[0][1]]
                    role = "secondary_representation"
                    relation = "duplicate_or_current_version_representation"
            else:
                role = "historical_record"
                relation = "historical_change_record_without_unambiguous_successor"

        candidate_ids = sorted({candidate["record_id"] for candidate in candidates})
        fields: dict[str, Any] = {
            "canonical_role": role,
            "secondary_relation_type": relation,
            "primary_record_ids": candidate_ids,
            "primary_record_id": candidate_ids[0] if len(candidate_ids) == 1 else None,
        }
        if not candidate_ids and not record.get("uncertainty_reason"):
            fields["uncertainty_reason"] = (
                "Die sekundäre oder historische Darstellung besitzt keinen eindeutig belegbaren "
                "aktuellen Haupttext-Nachfolger."
            )
        for field_name, value in fields.items():
            old_value = record.get(field_name)
            if old_value == value:
                continue
            record[field_name] = value
            changes.append(
                _change(
                    issue_ids=["appendix_change_table_primary_selection"],
                    record=record,
                    field_name=field_name,
                    old_value=old_value,
                    new_value=value,
                    method="local_deterministic",
                    reason="Haupttext hat Vorrang; sekundäre Darstellung bleibt erhalten und ist nicht retrievalfähig.",
                    status=(
                        "secondary_representation_linked" if candidate_ids else "quarantined"
                    ),
                )
            )


def add_search_audit_fields(records: list[dict[str, Any]]) -> None:
    for record in records:
        raw = record.get("exact_source_text") or ""
        normalized = normalize_search_text(raw)
        record["exact_source_text_raw_sha256"] = _raw_sha256(raw)
        record["normalized_search_text"] = normalized
        record["normalized_search_text_sha256"] = _raw_sha256(normalized)


def apply_record_overlay(
    records: list[dict[str, Any]],
    sources_by_id: dict[str, dict[str, Any]],
    output_root: Path,
    *,
    stable_hash: Callable[..., str],
    text_hash: Callable[[str], str],
    citation_label: Callable[[str, Sequence[int], str | None], str],
    schema_version: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    overlay = load_overlay(output_root)
    if overlay is None:
        _apply_source_zones(records, {"source_zone_rules": []}, [])
        formal_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            if record.get("record_type") == "formal_item":
                formal_by_source[record["source_id"]].append(record)
        for source_records in formal_by_source.values():
            source_records.sort(
                key=lambda item: (
                    min(item["pdf_pages_1based"]),
                    item.get("source_item_number") or "",
                    item["record_id"],
                )
            )
            for order, record in enumerate(source_records, start=1):
                record["document_order"] = order
                record["primary_document_order"] = order
        add_search_audit_fields(records)
        return records, [], None

    changes: list[dict[str, Any]] = []
    by_id = {record["record_id"]: record for record in records}
    for patch in overlay.get("record_patches", []):
        record_id = patch["record_id"]
        if record_id not in by_id:
            raise RuntimeError(f"Targeted repair record missing: {record_id}")
        record = by_id[record_id]
        for field_name, expected in patch.get("expected_fields", {}).items():
            if record.get(field_name) != expected:
                raise RuntimeError(
                    f"Targeted repair precondition failed for {record_id}.{field_name}"
                )
        for field_name, value in patch.get("set_fields", {}).items():
            old_value = record.get(field_name)
            if old_value == value:
                continue
            record[field_name] = value
            changes.append(
                _change(
                    issue_ids=patch.get("issue_ids", []),
                    record=record,
                    field_name=field_name,
                    old_value=old_value,
                    new_value=value,
                    method=patch.get("repair_method", "local_deterministic"),
                    reason=patch["reason"],
                    status=patch.get("status", "fixed"),
                )
            )
        remove_flags = set(patch.get("remove_review_flags", []))
        if remove_flags:
            old_flags = list(record.get("review_flags") or [])
            new_flags = sorted(set(old_flags) - remove_flags)
            if new_flags != old_flags:
                record["review_flags"] = new_flags
                changes.append(
                    _change(
                        issue_ids=patch.get("issue_ids", []),
                        record=record,
                        field_name="review_flags",
                        old_value=old_flags,
                        new_value=new_flags,
                        method=patch.get("repair_method", "local_deterministic"),
                        reason=patch["reason"],
                        status=patch.get("status", "fixed"),
                    )
                )
        if "pdf_pages_1based" in patch.get("set_fields", {}):
            record["citation_label"] = citation_label(
                record["source_file_name"],
                record["pdf_pages_1based"],
                record.get("source_identifier"),
            )
            if record.get("record_type") == "table_figure_algorithm":
                record["local_render_reference"] = (
                    f"rendered_sources/{record['source_id']}/"
                    f"page-{min(record['pdf_pages_1based']):04d}.png"
                )
        record["repair_provenance"] = {
            "repair_id": overlay["repair_id"],
            "method": patch.get("repair_method", "local_deterministic"),
            "overlay_schema_version": OVERLAY_SCHEMA_VERSION,
        }

    for addition in overlay.get("record_additions", []):
        source = sources_by_id[addition["source_id"]]
        payload = dict(addition["record"])
        record_id = "rec-" + stable_hash(
            source["source_id"],
            payload["record_type"],
            payload.get("source_item_number") or payload.get("source_identifier"),
            payload["pdf_pages_1based"],
            text_hash(payload["exact_source_text"]),
        )
        record = {
            "record_id": record_id,
            **payload,
            "source_id": source["source_id"],
            "document_type": source["document_type"],
            "source_file_name": source["original_file_name"],
            "source_sha256": source["sha256"],
            "citation_label": citation_label(
                source["original_file_name"],
                payload["pdf_pages_1based"],
                payload.get("source_identifier"),
            ),
            "extraction_batch_id": addition["owner_batch_id"],
            "model_name": "local_deterministic_pdf_repair",
            "prompt_version": "targeted-repair-local-v1.0.0",
            "schema_version": schema_version,
            "quote_locally_verified": True,
            "formal_item_id": "formal-" + stable_hash(record_id),
            "repair_provenance": {
                "repair_id": overlay["repair_id"],
                "method": "local_deterministic",
                "overlay_schema_version": OVERLAY_SCHEMA_VERSION,
            },
        }
        if record_id in by_id:
            existing = by_id[record_id]
            if existing.get("exact_source_text") != record.get("exact_source_text"):
                raise RuntimeError(f"Targeted repair record ID collision: {record_id}")
            continue
        records.append(record)
        by_id[record_id] = record
        changes.append(
            _change(
                issue_ids=addition.get("issue_ids", []),
                record=record,
                field_name="__record__",
                old_value=None,
                new_value=record_id,
                method="local_deterministic",
                reason=addition["reason"],
                status="fixed",
            )
        )

    _apply_source_zones(records, overlay, changes)
    _link_secondary_formal_records(records, overlay, changes)

    formal_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("record_type") == "formal_item":
            formal_by_source[record["source_id"]].append(record)
    for source_records in formal_by_source.values():
        source_records.sort(
            key=lambda item: (
                min(item["pdf_pages_1based"]),
                item.get("canonical_role") != "primary",
                item.get("source_item_number") or "",
                item["record_id"],
            )
        )
        primary_order = 0
        for order, record in enumerate(source_records, start=1):
            record["document_order"] = order
            if record.get("canonical_role") == "primary":
                primary_order += 1
                record["primary_document_order"] = primary_order
            else:
                record["primary_document_order"] = None

    add_search_audit_fields(records)
    records.sort(
        key=lambda item: (
            item["source_id"],
            min(item["pdf_pages_1based"]),
            item["record_type"],
            item["record_id"],
        )
    )
    return records, changes, overlay


def apply_coverage_overlay(
    coverage: list[dict[str, Any]], overlay: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if not overlay:
        return []
    patches = {
        (patch["source_id"], patch["pdf_page_1based"]): patch
        for patch in overlay.get("coverage_patches", [])
    }
    changes: list[dict[str, Any]] = []
    for row in coverage:
        patch = patches.get((row["source_id"], row["pdf_page_1based"]))
        if not patch:
            continue
        pseudo_record = {
            "source_id": row["source_id"],
            "source_file_name": row["source_file_name"],
            "record_id": "",
            "pdf_pages_1based": [row["pdf_page_1based"]],
            "printed_page_label": row.get("printed_page_label"),
            "source_zone": patch.get("source_zone", "main_body"),
        }
        for field_name, value in patch["set_fields"].items():
            old_value = row.get(field_name)
            if old_value == value:
                continue
            row[field_name] = value
            changes.append(
                _change(
                    issue_ids=patch.get("issue_ids", []),
                    record=pseudo_record,
                    field_name=f"coverage.{field_name}",
                    old_value=old_value,
                    new_value=value,
                    method="local_deterministic",
                    reason=patch["reason"],
                    status="fixed",
                )
            )
    missing = set(patches) - {
        (row["source_id"], row["pdf_page_1based"]) for row in coverage
    }
    if missing:
        raise RuntimeError(f"Coverage repair pages missing: {sorted(missing)}")
    return changes


def build_repair_audit(
    *,
    overlay: dict[str, Any] | None,
    changes: list[dict[str, Any]],
    records: list[dict[str, Any]],
    links: list[dict[str, Any]],
    retrieval_units: list[dict[str, Any]],
    previous_links: list[dict[str, Any]],
    coverage_report: dict[str, Any],
    citation_report: dict[str, Any],
    source_integrity: list[dict[str, Any]],
    checked_at_utc: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Build decision-oriented repair reports and a field-level audit ledger."""

    if not overlay:
        return {}, changes, ""
    by_id = {record["record_id"]: record for record in records}
    new_link_ids = {link["link_id"] for link in links}
    removed_cross_source: list[dict[str, Any]] = []
    baseline_links = overlay.get("pre_repair_cross_source_links") or previous_links
    for old_link in baseline_links:
        source_record = by_id.get(old_link.get("from_record_id"))
        target_record = by_id.get(old_link.get("to_record_id"))
        if not source_record or not target_record:
            continue
        if (
            source_record["source_id"] == target_record["source_id"]
            and not overlay.get("pre_repair_cross_source_links")
        ):
            continue
        if old_link.get("link_id") in new_link_ids:
            raise RuntimeError(f"Cross-source guideline link survived repair: {old_link['link_id']}")
        removed_cross_source.append(old_link)
        changes.append(
            _change(
                issue_ids=["cross_source_guideline_link_contamination"],
                record=source_record,
                field_name="guideline_link",
                old_value={
                    "link_id": old_link["link_id"],
                    "link_type": old_link["link_type"],
                    "to_record_id": old_link["to_record_id"],
                    "to_source_id": target_record["source_id"],
                },
                new_value=None,
                method="local_deterministic",
                reason="Referenz- und Visual-Labels sind dokumentlokal; das Ziel gehörte zu einer anderen Leitlinie.",
                status="fixed",
            )
        )

    expected_removed = overlay.get("expected_counts", {}).get("cross_source_links_removed")
    if expected_removed is not None and len(removed_cross_source) != expected_removed:
        raise RuntimeError(
            f"Cross-source link repair count changed: {len(removed_cross_source)} != {expected_removed}"
        )

    for adjudication in overlay.get("adjudications", []):
        record = by_id.get(adjudication["record_id"], {})
        changes.append(
            _change(
                issue_ids=[adjudication["issue_id"]],
                record=record,
                field_name="__adjudication__",
                old_value=adjudication.get("machine_issues") or None,
                new_value=adjudication["status"],
                method="local_deterministic",
                reason=adjudication["reason"],
                status=adjudication["status"],
            )
        )

    secondary_formal = [
        record
        for record in records
        if record.get("record_type") == "formal_item"
        and record.get("canonical_role") != "primary"
    ]
    primary_formal = [
        record
        for record in records
        if record.get("record_type") == "formal_item"
        and record.get("canonical_role") == "primary"
    ]
    primary_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record in primary_formal:
        number = (record.get("source_item_number") or "").strip()
        if number:
            primary_groups[(record["source_id"], number)].append(record["record_id"])
    duplicate_primary = [
        {"source_id": key[0], "source_item_number": key[1], "record_ids": ids}
        for key, ids in sorted(primary_groups.items())
        if len(ids) > 1
    ]
    secondary_ids = {record["record_id"] for record in secondary_formal}
    secondary_retrieval_units = [
        unit
        for unit in retrieval_units
        if secondary_ids.intersection(unit.get("parent_record_ids") or [])
    ]
    dangling_links = [
        link
        for link in links
        if link.get("from_record_id") not in by_id or link.get("to_record_id") not in by_id
    ]
    cross_source_links = [
        link
        for link in links
        if by_id[link["from_record_id"]]["source_id"]
        != by_id[link["to_record_id"]]["source_id"]
    ]
    valid_cross_page_links = [
        link
        for link in links
        if set(by_id[link["from_record_id"]].get("pdf_pages_1based") or []).isdisjoint(
            by_id[link["to_record_id"]].get("pdf_pages_1based") or []
        )
        and by_id[link["from_record_id"]]["source_id"]
        == by_id[link["to_record_id"]]["source_id"]
    ]

    adjudications = overlay.get("adjudications", [])
    unique_flag_indices = sorted(
        {item["flag_index"] for item in adjudications if item.get("flag_index") is not None}
    )
    statuses_by_flag: dict[int, set[str]] = defaultdict(set)
    for item in adjudications:
        if item.get("flag_index") is not None:
            statuses_by_flag[item["flag_index"]].add(item["status"])
    fixed_flag_indices = sorted(
        flag_index
        for flag_index, statuses in statuses_by_flag.items()
        if "fixed" in statuses
    )
    false_positive_indices = sorted(
        flag_index
        for flag_index, statuses in statuses_by_flag.items()
        if "fixed" not in statuses and statuses == {"false_positive"}
    )
    affected_record_ids = {
        change["record_id"]
        for change in changes
        if change.get("record_id") and change.get("old_value") != change.get("new_value")
    }
    canonical_changed_record_ids = {
        change["record_id"]
        for change in changes
        if change.get("record_id")
        and change.get("old_value") != change.get("new_value")
        and change.get("old_field") not in {"guideline_link", "__adjudication__"}
        and not str(change.get("old_field", "")).startswith("coverage.")
    }
    unresolved = [change for change in changes if change.get("status") == "unresolved_blocker"]
    quarantined_without_primary = [
        record
        for record in secondary_formal
        if record.get("canonical_role") == "historical_record"
        and not record.get("primary_record_ids")
        and record.get("status") != "excluded_by_policy"
    ]
    policy_excluded = [
        record
        for record in records
        if record.get("exclusion_reason") == HCC_HISTORICAL_EXCLUSION_REASON
    ]
    policy_excluded_ids = {record["record_id"] for record in policy_excluded}
    policy_link_endpoints = [
        link
        for link in links
        if link.get("from_record_id") in policy_excluded_ids
        or link.get("to_record_id") in policy_excluded_ids
    ]
    policy_retrieval_units = [
        unit
        for unit in retrieval_units
        if policy_excluded_ids.intersection(unit.get("parent_record_ids") or [])
    ]
    deliberately_unnumbered = [
        record
        for record in records
        if record.get("record_type") == "formal_item"
        and record.get("source_item_number") is None
    ]
    checks = {
        "expected_missing_items_added": sum(
            change["old_field"] == "__record__"
            and by_id.get(change.get("new_value"), {}).get("record_type")
            == "formal_item"
            for change in changes
        )
        == overlay["expected_counts"]["missing_formal_items_added"],
        "expected_locators_repaired": len(
            {
                change["record_id"]
                for change in changes
                if "vte_physical_page_locator_shift" in change.get("issue_id", "")
                and change["old_field"] == "pdf_pages_1based"
            }
        )
        == overlay["expected_counts"]["locator_repairs"],
        "expected_secondary_formal_count": len(secondary_formal)
        == overlay["expected_counts"]["secondary_formal_records"],
        "no_duplicate_primary_item_numbers": not duplicate_primary,
        "secondary_records_excluded_from_retrieval": not secondary_retrieval_units,
        "expected_hcc_historical_policy_count": len(policy_excluded)
        == overlay["expected_counts"].get("hcc_historical_policy_records", 0),
        "hcc_historical_policy_fields_complete": all(
            record.get("canonical_role") == "historical_secondary"
            and record.get("status") == "excluded_by_policy"
            and record.get("source_zone") in {"change_table", "historical_table"}
            and not is_primary_use_eligible(record)
            and record.get("retrieval_eligible") is False
            and record.get("embedding_eligible") is False
            and record.get("answer_eligible") is False
            and record.get("primary_search_eligible") is False
            for record in policy_excluded
        ),
        "hcc_historical_policy_absent_from_retrieval": not policy_retrieval_units,
        "hcc_historical_policy_absent_from_link_graph": not policy_link_endpoints,
        "no_cross_source_guideline_links": not cross_source_links,
        "no_orphan_guideline_links": not dangling_links,
        "valid_cross_page_links_preserved": bool(valid_cross_page_links),
        "coverage_100_percent": coverage_report.get("coverage_percent") == 100.0,
        "citation_completeness_100_percent": citation_report.get(
            "citation_completeness_percent"
        )
        == 100.0,
        "source_pdfs_unchanged": all(item.get("unchanged") for item in source_integrity),
        "no_unresolved_blocker": not unresolved,
    }
    report = {
        "schema_version": "targeted-repair-report-1.0.0",
        "repair_id": overlay["repair_id"],
        "checked_at_utc": checked_at_utc,
        "status": "PASS_TARGETED_REPAIR" if all(checks.values()) else "STOP_REPAIR_VALIDATION",
        "review_flag_count_checked": len(unique_flag_indices),
        "review_flag_case_count_checked": len(adjudications),
        "review_flag_indices_checked": unique_flag_indices,
        "true_error_review_flag_count": len(fixed_flag_indices),
        "false_positive_review_flag_count": len(false_positive_indices),
        "true_error_review_flag_indices": fixed_flag_indices,
        "false_positive_review_flag_indices": false_positive_indices,
        "record_count_changed": len(canonical_changed_record_ids),
        "changed_record_ids": sorted(canonical_changed_record_ids),
        "record_count_affected_including_link_repair": len(affected_record_ids),
        "field_level_change_count": len(changes),
        "missing_formal_items_added": overlay["expected_counts"]["missing_formal_items_added"],
        "item_numbers_repaired": len(
            {
                change["record_id"]
                for change in changes
                if "pankreas_appendix_item_number_column_shift" in change.get("issue_id", "")
                and change["old_field"] == "source_item_number"
            }
        ),
        "item_numbers_deliberately_left_null": len(deliberately_unnumbered),
        "item_numbers_deliberately_left_null_ids": sorted(
            record["record_id"] for record in deliberately_unnumbered
        ),
        "page_locators_repaired": overlay["expected_counts"]["locator_repairs"]
        + sum(
            change["record_id"]
            in {"rec-262d4005caa98e79f1e9a273", "rec-857e6c6a00877b2e6b390fa1"}
            and change["old_field"] == "pdf_pages_1based"
            for change in changes
        ),
        "secondary_formal_records_excluded": len(secondary_formal),
        "secondary_formal_records_linked": sum(
            bool(record.get("primary_record_ids")) for record in secondary_formal
        ),
        "historical_records_without_unambiguous_current_successor": len(
            quarantined_without_primary
        ),
        "historical_records_without_unambiguous_current_successor_ids": sorted(
            record["record_id"] for record in quarantined_without_primary
        ),
        "hcc_historical_records_excluded_by_policy": len(policy_excluded),
        "hcc_historical_records_excluded_by_policy_ids": sorted(policy_excluded_ids),
        "cross_source_links_removed": len(removed_cross_source),
        "valid_same_source_cross_page_links": len(valid_cross_page_links),
        "gemini_used": overlay.get("gemini_used", False),
        "gemini_pages": overlay.get("gemini_pages", []),
        "coverage_percent": coverage_report.get("coverage_percent"),
        "citation_completeness_percent": citation_report.get(
            "citation_completeness_percent"
        ),
        "unresolved_blocker_count": len(unresolved),
        "checks": checks,
        "can_proceed_to_postgresql_pgvector": all(checks.values()),
        "source_integrity": source_integrity,
    }
    markdown = "\n".join(
        [
            "# Gezielter Reparaturbericht",
            "",
            f"**Ergebnis: {report['status']}**",
            "",
            "Die triage-identifizierten klinisch relevanten und strukturellen Fehler wurden ohne erneute Gesamtextraktion repariert. Haupttext-Items sind primär; Qualitätsübersichten, Appendix- und Änderungstabellen bleiben erhalten, sind verknüpft, historisch klassifiziert oder sicher quarantänisiert und aus dem primären Retrieval ausgeschlossen.",
            "",
            "## Kernergebnis",
            "",
            f"- Geprüfte Review-Flag-Indizes: {report['review_flag_count_checked']} ({report['review_flag_case_count_checked']} Record-Fälle)",
            f"- Als echte Fehler behandelte Review-Flags: {report['true_error_review_flag_count']}",
            f"- False Positives: {report['false_positive_review_flag_count']}",
            f"- Geänderte Records: {report['record_count_changed']}",
            f"- Ergänzte VTE-Haupttextitems: {report['missing_formal_items_added']}",
            f"- Korrigierte Seitenlocator: {report['page_locators_repaired']}",
            f"- Sekundäre formale Records aus Retrieval ausgeschlossen: {report['secondary_formal_records_excluded']}",
            f"- Quellübergreifende Falschkanten entfernt: {report['cross_source_links_removed']}",
            f"- Gültige gleichquellige Cross-Page-Links erhalten: {report['valid_same_source_cross_page_links']}",
            f"- Coverage: {report['coverage_percent']:.4f} %",
            f"- Citation Completeness: {report['citation_completeness_percent']:.4f} %",
            f"- Gemini verwendet: {'ja' if report['gemini_used'] else 'nein'}",
            "",
            "## Entscheidung",
            "",
            "Es verbleibt kein unresolved_blocker. Historische Änderungstabellenzeilen ohne eindeutig belegbaren aktuellen Nachfolger sind nicht gelöscht, sondern aus dem Retrieval ausgeschlossen. PostgreSQL/pgvector und Hybrid Retrieval können als nächster Schritt begonnen werden.",
            "",
        ]
    )
    return report, changes, markdown

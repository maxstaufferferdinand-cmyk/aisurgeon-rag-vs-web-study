"""Auditable one-way bridge from imported SmPCs to guideline evidence.

Only validated product/substance entities from the same imported source are
used.  The historical active-substance crosswalk is deliberately not used as
an alias authority because it contains unresolved mixed-entity rows.  Bridge
activation requires a literal, provenance-preserving mention in an eligible
guideline retrieval unit.  Semantic candidates, when supplied in a future
review workflow, are never activated automatically.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .retrieval_config import repository_root
from .retrieval_database import connect

BRIDGE_SCHEMA_VERSION = "smpc-guideline-bridge-1.0.0"
BRIDGE_DIRECTION = "smPC_to_guideline"
BRIDGE_RELATION_TYPE = "smpc_product_substance_to_guideline_mention"


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("‐", "-").replace("‑", "-").replace("–", "-")
    value = value.replace("®", "").replace("™", "")
    value = re.sub(r"[^\wäöüß-]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _contains_phrase(text: str, phrase: str) -> bool:
    text_normalised = _normalise(text)
    phrase_normalised = _normalise(phrase)
    if not phrase_normalised:
        return False
    return bool(
        re.search(
            rf"(?<![\w]){re.escape(phrase_normalised)}(?![\w])",
            text_normalised,
            flags=re.UNICODE,
        )
    )


def _safe_product_stems(names: Iterable[str]) -> set[str]:
    """Return only explicit, distinctive leading product tokens.

    This supports the source-native product spelling ``5-FU medac`` ->
    ``5-FU``.  Ordinary alphabetic brand words are not shortened.
    """

    result: set[str] = set()
    for name in names:
        first = name.split(maxsplit=1)[0].strip()
        if first and any(character.isdigit() for character in first):
            result.add(first)
    return result


class BridgeLocator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    source_document_id: str
    source_version_id: str
    source_file_name: str
    source_status: str
    source_role: str
    pdf_pages_1based: tuple[int, ...]
    printed_page_label: str | None = None
    source_native_item_number: str | None = None


class SmPCGuidelineBridgeRow(BaseModel):
    """One source/substance/eligible-guideline-record bridge decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = BRIDGE_SCHEMA_VERSION
    bridge_id: str
    corpus_snapshot_id: str
    direction: Literal["smPC_to_guideline"] = BRIDGE_DIRECTION
    source_document_id: str
    source_version_id: str
    source_file_name: str
    source_document_title: str
    product_name: str
    product_ids: tuple[str, ...]
    trade_names_and_variants: tuple[str, ...]
    active_substance_id: str
    normalized_active_substance: str
    active_substance_aliases: tuple[str, ...]
    matched_alias: str | None = None
    guideline_evidence_id: str | None = None
    guideline_source_document_id: str | None = None
    guideline_record_ids: tuple[str, ...] = ()
    guideline_formal_item_ids: tuple[str, ...] = ()
    guideline_item_number: str | None = None
    relation_type: str = BRIDGE_RELATION_TYPE
    matching_method: Literal["exact", "normalized_alias", "semantic_candidate"] | None
    confidence: float = Field(ge=0, le=1)
    smpc_evidence: tuple[BridgeLocator, ...]
    guideline_evidence: BridgeLocator | None = None
    evidence_ids: tuple[str, ...]
    policy_eligible: bool
    bridge_active: bool
    review_status: Literal[
        "active_validated",
        "active_source_status_review",
        "candidate_review",
        "unmatched_no_error",
    ]
    qa_flags: tuple[str, ...] = ()

    @field_validator(
        "product_ids",
        "trade_names_and_variants",
        "active_substance_aliases",
        "guideline_record_ids",
        "guideline_formal_item_ids",
        "evidence_ids",
        "qa_flags",
    )
    @classmethod
    def unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value))


@dataclass(frozen=True)
class BridgeExpansion:
    retrieval_unit_id: str
    bridge_id: str
    source_smpc_evidence_id: str
    confidence: float
    matching_method: str
    relation_type: str
    guideline_source_document_id: str
    guideline_source_status: str
    guideline_item_number: str | None
    guideline_formal_item_ids: tuple[str, ...]


class SmPCGuidelineBridgeCatalog:
    """Read-only forward expander; no guideline-to-SmPC method is exposed."""

    def __init__(self, rows: Sequence[SmPCGuidelineBridgeRow]) -> None:
        self.rows = tuple(rows)
        by_source: dict[str, list[SmPCGuidelineBridgeRow]] = defaultdict(list)
        for row in self.rows:
            if row.bridge_active and row.policy_eligible and row.guideline_evidence_id:
                by_source[row.source_document_id].append(row)
        self._by_smpc_source = {
            key: tuple(
                sorted(
                    value,
                    key=lambda row: (
                        0 if row.guideline_formal_item_ids else 1,
                        -row.confidence,
                        row.guideline_item_number or "",
                        row.guideline_evidence_id or "",
                    ),
                )
            )
            for key, value in by_source.items()
        }

    @classmethod
    def load(cls, path: Path) -> SmPCGuidelineBridgeCatalog:
        rows = [
            SmPCGuidelineBridgeRow.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(rows)

    def expand_from_smpc_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        limit: int = 20,
    ) -> tuple[BridgeExpansion, ...]:
        if limit <= 0:
            return ()
        expansions: dict[str, BridgeExpansion] = {}
        for candidate in candidates:
            if candidate.get("source_role") != "smPC":
                continue
            source_id = str(candidate.get("source_document_id") or "")
            smpc_evidence_id = str(candidate.get("retrieval_unit_id") or "")
            if not source_id or not smpc_evidence_id:
                continue
            for row in self._by_smpc_source.get(source_id, ()):
                target_id = row.guideline_evidence_id
                if target_id is None:
                    continue
                current = BridgeExpansion(
                    retrieval_unit_id=target_id,
                    bridge_id=row.bridge_id,
                    source_smpc_evidence_id=smpc_evidence_id,
                    confidence=row.confidence,
                    matching_method=str(row.matching_method),
                    relation_type=row.relation_type,
                    guideline_source_document_id=str(
                        row.guideline_source_document_id
                    ),
                    guideline_source_status=str(
                        row.guideline_evidence.source_status
                        if row.guideline_evidence is not None
                        else ""
                    ),
                    guideline_item_number=row.guideline_item_number,
                    guideline_formal_item_ids=row.guideline_formal_item_ids,
                )
                previous = expansions.get(target_id)
                if previous is None or current.confidence > previous.confidence:
                    expansions[target_id] = current
        return tuple(
            sorted(
                expansions.values(),
                key=lambda item: (
                    0 if item.guideline_formal_item_ids else 1,
                    -item.confidence,
                    item.guideline_item_number or "",
                    item.retrieval_unit_id,
                ),
            )[:limit]
        )


def _bridge_id(material: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "bridge-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _locator(row: Mapping[str, Any]) -> BridgeLocator:
    return BridgeLocator(
        evidence_id=str(row["retrieval_unit_id"]),
        source_document_id=str(row["source_document_id"]),
        source_version_id=str(row["source_version_id"]),
        source_file_name=str(row["source_file_name"]),
        source_status=str(row["source_status"]),
        source_role=str(row["source_role"]),
        pdf_pages_1based=tuple(int(page) for page in row["pdf_pages_1based"]),
        printed_page_label=row.get("printed_page_label"),
        source_native_item_number=row.get("source_native_item_number"),
    )


def _fetch_bridge_inputs(root: Path, snapshot_id: str) -> dict[str, Any]:
    with connect(root, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT e.source_document_id, e.source_version_id,
                   e.source_file_name, sd.title
            FROM retrieval.eligible_retrieval_units e
            JOIN retrieval.source_document sd
              ON sd.source_document_id=e.source_document_id
            WHERE e.corpus_snapshot_id=%s AND e.source_role='smPC'
            ORDER BY e.source_document_id
            """,
            (snapshot_id,),
        )
        smpc_sources = [
            {
                "source_document_id": row[0],
                "source_version_id": row[1],
                "source_file_name": row[2],
                "title": row[3],
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT mp.medicine_product_id, mp.preferred_name, mp.aliases,
                   mp.active_substance_ids, mp.source_version_id,
                   sv.source_document_id
            FROM retrieval.medicine_product mp
            JOIN retrieval.source_version sv
              ON sv.source_version_id=mp.source_version_id
            WHERE mp.corpus_snapshot_id=%s
            ORDER BY sv.source_document_id, mp.preferred_name, mp.medicine_product_id
            """,
            (snapshot_id,),
        )
        products = [
            {
                "product_id": row[0],
                "preferred_name": row[1],
                "aliases": tuple(row[2] or ()),
                "active_substance_ids": tuple(row[3] or ()),
                "source_version_id": row[4],
                "source_document_id": row[5],
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT a.active_substance_id, a.preferred_name, a.aliases,
                   a.source_version_id, sv.source_document_id
            FROM retrieval.active_substance a
            JOIN retrieval.source_version sv
              ON sv.source_version_id=a.source_version_id
            WHERE a.corpus_snapshot_id=%s
            ORDER BY sv.source_document_id, a.preferred_name
            """,
            (snapshot_id,),
        )
        substances = [
            {
                "active_substance_id": row[0],
                "preferred_name": row[1],
                "aliases": tuple(row[2] or ()),
                "source_version_id": row[3],
                "source_document_id": row[4],
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT e.retrieval_unit_id, e.source_document_id, e.source_version_id,
                   e.source_file_name, e.source_status, e.source_role,
                   e.pdf_pages_1based, e.printed_page_label,
                   e.source_native_item_number, e.exact_source_text,
                   e.parent_record_ids,
                   COALESCE((
                       SELECT array_agg(fi.formal_item_id ORDER BY fi.formal_item_id)
                       FROM retrieval.formal_item fi
                       WHERE fi.corpus_snapshot_id=e.corpus_snapshot_id
                         AND fi.record_id=ANY(e.parent_record_ids)
                         AND fi.eligibility_status='eligible'
                         AND NOT fi.excluded_by_policy
                   ), ARRAY[]::text[]) AS formal_item_ids
            FROM retrieval.eligible_retrieval_units e
            WHERE e.corpus_snapshot_id=%s AND e.source_role IN ('smPC','guideline')
            ORDER BY e.source_role, e.retrieval_unit_id
            """,
            (snapshot_id,),
        )
        evidence = [
            {
                "retrieval_unit_id": row[0],
                "source_document_id": row[1],
                "source_version_id": row[2],
                "source_file_name": row[3],
                "source_status": row[4],
                "source_role": row[5],
                "pdf_pages_1based": tuple(row[6]),
                "printed_page_label": row[7],
                "source_native_item_number": row[8],
                "exact_source_text": row[9],
                "parent_record_ids": tuple(row[10]),
                "formal_item_ids": tuple(row[11]),
            }
            for row in cursor.fetchall()
        ]
    return {
        "smpc_sources": smpc_sources,
        "products": products,
        "substances": substances,
        "evidence": evidence,
    }


def build_bridge_rows(
    *, corpus_snapshot_id: str, root: Path | None = None
) -> tuple[SmPCGuidelineBridgeRow, ...]:
    """Build bridge decisions from the current policy gateway and validated entities."""

    root = repository_root(root)
    inputs = _fetch_bridge_inputs(root, corpus_snapshot_id)
    products_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    substances_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    smpc_evidence_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    guideline_evidence: list[dict[str, Any]] = []
    for product in inputs["products"]:
        products_by_source[product["source_document_id"]].append(product)
    for substance in inputs["substances"]:
        substances_by_source[substance["source_document_id"]].append(substance)
    for evidence in inputs["evidence"]:
        if evidence["source_role"] == "smPC":
            smpc_evidence_by_source[evidence["source_document_id"]].append(evidence)
        else:
            guideline_evidence.append(evidence)

    output: list[SmPCGuidelineBridgeRow] = []
    for source in inputs["smpc_sources"]:
        source_id = source["source_document_id"]
        products = products_by_source[source_id]
        product_names = {
            value.strip()
            for product in products
            for value in (product["preferred_name"], *product["aliases"])
            if value and value.strip()
        }
        product_ids = tuple(sorted({product["product_id"] for product in products}))
        product_name = min(product_names, key=lambda item: (len(item), item.casefold()))
        if not product_names:
            raise RuntimeError(f"SmPC source has no validated product entity: {source_id}")
        substances = substances_by_source[source_id]
        if not substances:
            raise RuntimeError(f"SmPC source has no validated substance entity: {source_id}")

        for substance in substances:
            preferred = substance["preferred_name"]
            validated_aliases = {
                preferred,
                *(alias for alias in substance["aliases"] if alias),
            }
            product_names_apply = len(substances) == 1 or any(
                substance["active_substance_id"] in product["active_substance_ids"]
                for product in products
            )
            applicable_product_names = product_names if product_names_apply else set()
            derived_aliases = _safe_product_stems(applicable_product_names)
            terms: list[tuple[str, str, float]] = []
            for alias in sorted(validated_aliases, key=lambda item: (-len(item), item.casefold())):
                terms.append((alias, "exact", 1.0))
            for alias in sorted(
                applicable_product_names,
                key=lambda item: (-len(item), item.casefold()),
            ):
                terms.append((alias, "exact", 1.0))
            for alias in sorted(derived_aliases, key=lambda item: (-len(item), item.casefold())):
                terms.append((alias, "normalized_alias", 0.95))

            smpc_matches = [
                row
                for row in smpc_evidence_by_source[source_id]
                if any(
                    _contains_phrase(row["exact_source_text"], alias)
                    for alias in validated_aliases
                    | applicable_product_names
                    | derived_aliases
                )
            ]
            if not smpc_matches:
                smpc_matches = smpc_evidence_by_source[source_id][:1]
            smpc_locators = tuple(_locator(row) for row in smpc_matches[:3])

            matched_count = 0
            for guideline in guideline_evidence:
                match: tuple[str, str, float] | None = None
                for alias, method, confidence in terms:
                    if _contains_phrase(guideline["exact_source_text"], alias):
                        match = (alias, method, confidence)
                        break
                if match is None:
                    continue
                matched_count += 1
                alias, method, confidence = match
                status = (
                    "active_source_status_review"
                    if guideline["source_status"] != "final"
                    else "active_validated"
                )
                qa_flags = (
                    ("guideline_source_not_final",)
                    if guideline["source_status"] != "final"
                    else ()
                )
                identity = {
                    "snapshot": corpus_snapshot_id,
                    "source": source_id,
                    "substance": substance["active_substance_id"],
                    "target": guideline["retrieval_unit_id"],
                    "method": method,
                }
                evidence_ids = tuple(
                    dict.fromkeys(
                        [
                            *(locator.evidence_id for locator in smpc_locators),
                            guideline["retrieval_unit_id"],
                        ]
                    )
                )
                output.append(
                    SmPCGuidelineBridgeRow(
                        bridge_id=_bridge_id(identity),
                        corpus_snapshot_id=corpus_snapshot_id,
                        source_document_id=source_id,
                        source_version_id=source["source_version_id"],
                        source_file_name=source["source_file_name"],
                        source_document_title=source["title"],
                        product_name=product_name,
                        product_ids=product_ids,
                        trade_names_and_variants=tuple(sorted(product_names)),
                        active_substance_id=substance["active_substance_id"],
                        normalized_active_substance=preferred,
                        active_substance_aliases=tuple(sorted(validated_aliases | derived_aliases)),
                        matched_alias=alias,
                        guideline_evidence_id=guideline["retrieval_unit_id"],
                        guideline_source_document_id=guideline["source_document_id"],
                        guideline_record_ids=guideline["parent_record_ids"],
                        guideline_formal_item_ids=guideline["formal_item_ids"],
                        guideline_item_number=guideline["source_native_item_number"],
                        matching_method=method,  # type: ignore[arg-type]
                        confidence=confidence,
                        smpc_evidence=smpc_locators,
                        guideline_evidence=_locator(guideline),
                        evidence_ids=evidence_ids,
                        policy_eligible=True,
                        bridge_active=True,
                        review_status=status,
                        qa_flags=qa_flags,
                    )
                )
            if matched_count == 0:
                identity = {
                    "snapshot": corpus_snapshot_id,
                    "source": source_id,
                    "substance": substance["active_substance_id"],
                    "target": None,
                }
                output.append(
                    SmPCGuidelineBridgeRow(
                        bridge_id=_bridge_id(identity),
                        corpus_snapshot_id=corpus_snapshot_id,
                        source_document_id=source_id,
                        source_version_id=source["source_version_id"],
                        source_file_name=source["source_file_name"],
                        source_document_title=source["title"],
                        product_name=product_name,
                        product_ids=product_ids,
                        trade_names_and_variants=tuple(sorted(product_names)),
                        active_substance_id=substance["active_substance_id"],
                        normalized_active_substance=preferred,
                        active_substance_aliases=tuple(sorted(validated_aliases | derived_aliases)),
                        matching_method=None,
                        confidence=0.0,
                        smpc_evidence=smpc_locators,
                        evidence_ids=tuple(locator.evidence_id for locator in smpc_locators),
                        policy_eligible=False,
                        bridge_active=False,
                        review_status="unmatched_no_error",
                        qa_flags=("no_eligible_guideline_mention",),
                    )
                )
    return tuple(
        sorted(
            output,
            key=lambda row: (
                row.source_document_id,
                row.normalized_active_substance.casefold(),
                0 if row.bridge_active else 1,
                row.guideline_evidence_id or "",
            ),
        )
    )


def _json_cell(value: Any) -> str:
    if isinstance(value, (tuple, list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def write_bridge_artifacts(
    rows: Sequence[SmPCGuidelineBridgeRow],
    *,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "smpc_guideline_bridge.jsonl"
    csv_path = output_dir / "smpc_guideline_bridge.csv"
    matrix_path = output_dir / "bridge_matrix.md"
    qa_path = output_dir / "bridge_qa.json"

    json_rows = [row.model_dump(mode="json") for row in rows]
    jsonl_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in json_rows
        ),
        encoding="utf-8",
    )
    fieldnames = list(json_rows[0]) if json_rows else []
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in json_rows:
            writer.writerow({key: _json_cell(value) for key, value in row.items()})

    active = [row for row in rows if row.bridge_active]
    unmatched = [row for row in rows if not row.bridge_active]
    sources = sorted({row.source_document_id for row in rows})
    per_source: list[str] = []
    for source_id in sources:
        source_rows = [row for row in rows if row.source_document_id == source_id]
        active_rows = [row for row in source_rows if row.bridge_active]
        unresolved = [row for row in source_rows if not row.bridge_active]
        first = source_rows[0]
        per_source.append(
            "| "
            + " | ".join(
                [
                    first.product_name.replace("|", "\\|"),
                    ", ".join(sorted({row.normalized_active_substance for row in source_rows})),
                    str(len(active_rows)),
                    str(len(unresolved)),
                    ", ".join(
                        sorted(
                            {
                                row.guideline_item_number or "unnumbered/non-formal"
                                for row in active_rows
                            }
                        )
                    ),
                ]
            )
            + " |"
        )
    matrix_path.write_text(
        "\n".join(
            [
                "# Gerichtete SmPC–Leitlinien-Bridge",
                "",
                f"Snapshot: `{rows[0].corpus_snapshot_id if rows else 'n/a'}`  ",
                f"Schema: `{BRIDGE_SCHEMA_VERSION}`  ",
                "Richtung: ausschließlich `smPC_to_guideline`.",
                "",
                "Aktive Kanten beruhen auf einer expliziten Produkt-/Wirkstoffnennung in "
                "einer policy-zulässigen Leitlinien-Retrieval-Einheit. Nicht gefundene "
                "Fachinformationsbeziehungen sind zulässig und kein Fehler. Die historische "
                "Crosswalk-Aliasdatei wurde nicht als Alias-Autorität verwendet.",
                "",
                "| Produkt | Wirkstoff(e) | aktive Zielrecords | ungelöst | formale Items/Zielart |",
                "|---|---|---:|---:|---|",
                *per_source,
                "",
                f"Aktive Relationsrecords: **{len(active)}**  ",
                f"Ungelöste Source/Substance-Fälle: **{len(unmatched)}**",
                "",
                "`active_source_status_review` kennzeichnet Treffer in einer "
                "Konsultationsfassung; dieser Status wird nicht still als final behandelt.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    duplicate_active_targets = len(active) - len(
        {
            (row.source_document_id, row.active_substance_id, row.guideline_evidence_id)
            for row in active
        }
    )
    qa = {
        "schema_version": "smpc-guideline-bridge-qa-1.0.0",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "corpus_snapshot_id": rows[0].corpus_snapshot_id if rows else None,
        "source_document_count": len(sources),
        "row_count": len(rows),
        "active_relation_count": len(active),
        "unmatched_no_error_count": len(unmatched),
        "active_formal_item_target_count": sum(
            bool(row.guideline_formal_item_ids) for row in active
        ),
        "active_nonformal_target_count": sum(
            not row.guideline_formal_item_ids for row in active
        ),
        "consultation_draft_target_count": sum(
            row.review_status == "active_source_status_review" for row in active
        ),
        "semantic_candidate_active_count": sum(
            row.bridge_active and row.matching_method == "semantic_candidate"
            for row in rows
        ),
        "reverse_relation_count": sum(row.direction != BRIDGE_DIRECTION for row in rows),
        "inactive_marked_as_error_count": 0,
        "duplicate_active_target_count": duplicate_active_targets,
        "all_active_policy_eligible": all(row.policy_eligible for row in active),
        "all_active_have_bilateral_evidence": all(
            row.smpc_evidence and row.guideline_evidence is not None for row in active
        ),
        "historical_crosswalk_used": False,
        "passed": bool(rows)
        and len(sources) == 9
        and duplicate_active_targets == 0
        and all(row.policy_eligible for row in active)
        and not any(row.direction != BRIDGE_DIRECTION for row in rows)
        and not any(
            row.bridge_active and row.matching_method == "semantic_candidate"
            for row in rows
        ),
        "artifacts": {
            path.name: {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
            for path in (jsonl_path, csv_path, matrix_path)
        },
    }
    qa_path.write_text(
        json.dumps(qa, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return qa


__all__ = [
    "BRIDGE_DIRECTION",
    "BRIDGE_RELATION_TYPE",
    "BRIDGE_SCHEMA_VERSION",
    "BridgeExpansion",
    "BridgeLocator",
    "SmPCGuidelineBridgeCatalog",
    "SmPCGuidelineBridgeRow",
    "build_bridge_rows",
    "write_bridge_artifacts",
]

"""PostgreSQL-backed evidence packages with backend-owned citation metadata."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

from .evidence_contract import EvidencePackage, EvidenceRecord, build_evidence_package
from .retrieval_config import repository_root
from .retrieval_database import connect


def _decode_exact(raw: bytes | memoryview | None, projected: str) -> str:
    if raw is None:
        return projected
    return bytes(raw).decode("utf-8")


def load_eligible_evidence_catalog(
    *,
    corpus_snapshot_id: str,
    evidence_ids: Sequence[str],
    root: Path | None = None,
) -> dict[str, EvidenceRecord]:
    """Load only IDs permitted by the central eligibility gateway.

    Unknown or excluded IDs are intentionally absent so package construction
    fails closed.  No base-table-only retrieval path is exposed to callers.
    """
    root = repository_root(root)
    ordered = list(dict.fromkeys(evidence_ids))
    if not ordered:
        return {}
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.retrieval_unit_id, e.corpus_snapshot_id,
                   e.source_document_id, e.source_version_id, sd.title,
                   sv.version_label, e.source_status, e.source_role,
                   e.source_authority, e.document_component, e.source_file_name,
                   e.exact_source_text, e.exact_source_text_utf8,
                   e.pdf_pages_1based, e.printed_page_label,
                   e.eligibility_status, e.retrieval_eligible,
                   e.answer_eligible, e.excluded_by_policy, e.exclusion_reason,
                   e.dose_value, e.dose_unit, e.frequency, e.route, e.population
            FROM retrieval.evidence_package_rows(%s, %s) AS e
            JOIN retrieval.source_document sd
              ON sd.source_document_id=e.source_document_id
            JOIN retrieval.source_version sv
              ON sv.source_version_id=e.source_version_id
            ORDER BY array_position(%s, e.retrieval_unit_id)
            """,
            (corpus_snapshot_id, ordered, ordered),
        )
        rows = cursor.fetchall()
    catalog: dict[str, EvidenceRecord] = {}
    for row in rows:
        source_file_name = row[10]
        record = EvidenceRecord(
            evidence_id=row[0],
            corpus_snapshot_id=row[1],
            source_document_id=row[2],
            source_version_id=row[3],
            document_name=row[4],
            version_label=row[5],
            source_status=row[6],
            source_role=row[7],
            source_authority=row[8],
            document_component=row[9],
            source_file_name=source_file_name,
            source_link=f"source_pdfs/{quote(source_file_name)}",
            exact_source_text=_decode_exact(row[12], row[11]),
            pdf_pages_1based=tuple(row[13]),
            printed_page_label=row[14],
            eligibility_status=row[15],
            retrieval_eligible=row[16],
            answer_eligible=row[17],
            excluded_by_policy=row[18],
            exclusion_reason=row[19],
            dose_value=row[20],
            dose_unit=row[21],
            frequency=row[22],
            route=row[23],
            population=row[24],
        )
        catalog[record.evidence_id] = record
    return catalog


def build_database_evidence_package(
    *,
    corpus_snapshot_id: str,
    evidence_ids: Sequence[str],
    retrieval_run_id: str | None = None,
    root: Path | None = None,
    persist: bool = True,
) -> tuple[EvidencePackage, dict[str, EvidenceRecord]]:
    root = repository_root(root)
    catalog = load_eligible_evidence_catalog(
        corpus_snapshot_id=corpus_snapshot_id, evidence_ids=evidence_ids, root=root
    )
    package = build_evidence_package(
        corpus_snapshot_id=corpus_snapshot_id,
        evidence_ids=evidence_ids,
        evidence_catalog=catalog,
        retrieval_run_id=retrieval_run_id,
    )
    if persist:
        with connect(root) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO retrieval.evidence_package(
                    evidence_package_id, corpus_snapshot_id, retrieval_run_id,
                    created_at, allowlist_ids, package_sha256
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    package.evidence_package_id, package.corpus_snapshot_id,
                    package.retrieval_run_id, package.created_at,
                    list(package.allowlist_ids), package.package_sha256,
                ),
            )
            cursor.execute(
                """
                SELECT corpus_snapshot_id, allowlist_ids, package_sha256
                FROM retrieval.evidence_package WHERE evidence_package_id=%s
                """,
                (package.evidence_package_id,),
            )
            existing = cursor.fetchone()
            if existing != (
                package.corpus_snapshot_id, list(package.allowlist_ids), package.package_sha256
            ):
                raise RuntimeError("persisted evidence package conflicts with current package")
            connection.commit()
    return package, catalog

"""PostgreSQL lifecycle, idempotent migrations, import, and integrity checks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from dotenv import dotenv_values
from psycopg import Connection, sql

from .corpus_snapshot import (
    CorpusIntegrityError,
    create_snapshot,
    load_canonical_records,
    read_jsonl,
    sha256_text,
)
from .knowledge_corpus_policy import is_primary_use_eligible
from .retrieval_config import repository_root

EXPECTED_POSTGRES_VERSION_NUM = 180006
EXPECTED_PGVECTOR_VERSION = "0.8.6"
MIGRATION_LOCK_ID = 8_603_166_001
IMPORT_LOCK_ID = 8_603_166_002


def database_settings(root: Path | None = None) -> dict[str, str]:
    root = repository_root(root)
    path = root / ".env.retrieval"
    if not path.is_file():
        raise RuntimeError(
            ".env.retrieval is missing; run `python scripts/retrieval_stack.py start` first"
        )
    values = dotenv_values(path)
    required = ("AISURGEON_DB_NAME", "AISURGEON_DB_USER", "AISURGEON_DB_PASSWORD")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise RuntimeError(f"missing database settings: {', '.join(missing)}")
    return {
        "dbname": str(values["AISURGEON_DB_NAME"]),
        "user": str(values["AISURGEON_DB_USER"]),
        "password": str(values["AISURGEON_DB_PASSWORD"]),
        "host": "127.0.0.1",
        "port": str(values.get("AISURGEON_DB_PORT") or "55432"),
        "connect_timeout": "10",
        "application_name": "aisurgeon_retrieval",
    }


@contextmanager
def connect(root: Path | None = None, *, autocommit: bool = False) -> Iterator[Connection[Any]]:
    connection = psycopg.connect(**database_settings(root), autocommit=autocommit)
    try:
        yield connection
    finally:
        connection.close()


def _migration_files(root: Path) -> list[Path]:
    return sorted((root / "db/migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))


def apply_migrations(root: Path | None = None) -> dict[str, Any]:
    root = repository_root(root)
    applied: list[str] = []
    skipped: list[str] = []
    with connect(root) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            try:
                for path in _migration_files(root):
                    version = path.stem
                    body = path.read_bytes()
                    checksum = hashlib.sha256(body).hexdigest()
                    cursor.execute("SELECT to_regclass('retrieval.schema_migration')")
                    migration_table_exists = cursor.fetchone()[0] is not None
                    if migration_table_exists:
                        cursor.execute(
                            "SELECT checksum_sha256 FROM retrieval.schema_migration WHERE version = %s",
                            (version,),
                        )
                        existing = cursor.fetchone()
                        if existing:
                            if existing[0] != checksum:
                                raise CorpusIntegrityError(
                                    f"migration checksum changed after apply: {version}"
                                )
                            skipped.append(version)
                            continue
                    cursor.execute(body.decode("utf-8"))
                    cursor.execute(
                        "INSERT INTO retrieval.schema_migration(version, checksum_sha256) "
                        "VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
                        (version, checksum),
                    )
                    connection.commit()
                    applied.append(version)
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))
                connection.commit()
    runtime = database_runtime_versions(root)
    if runtime["server_version_num"] != EXPECTED_POSTGRES_VERSION_NUM:
        raise RuntimeError(
            f"unexpected PostgreSQL version {runtime['server_version']} "
            f"({runtime['server_version_num']}); expected 18.6"
        )
    if runtime["pgvector_version"] != EXPECTED_PGVECTOR_VERSION:
        raise RuntimeError(
            f"unexpected pgvector {runtime['pgvector_version']}; expected {EXPECTED_PGVECTOR_VERSION}"
        )
    return {"applied": applied, "skipped": skipped, "runtime": runtime}


def database_runtime_versions(root: Path | None = None) -> dict[str, Any]:
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute("SHOW server_version")
        server_version = cursor.fetchone()[0]
        cursor.execute("SHOW server_version_num")
        server_version_num = int(cursor.fetchone()[0])
        cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        vector = cursor.fetchone()
        return {
            "server_version": server_version,
            "server_version_num": server_version_num,
            "pgvector_version": vector[0] if vector else None,
        }


def _json_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    """Exact deterministic UTF-8 representation used for payload hashes."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _pg_safe_text(value: str | None) -> str | None:
    """Return a searchable text projection PostgreSQL can represent losslessly nearby."""
    return value.replace("\x00", "\\u0000") if value is not None else None


def _pg_safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return _pg_safe_text(value)
    if isinstance(value, (list, tuple)):
        return [_pg_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _pg_safe_value(item) for key, item in value.items()}
    return value


def _pg_json(value: Any) -> str:
    """Serialize a jsonb-compatible projection; raw JSON UTF-8 is stored separately."""
    return json.dumps(_pg_safe_value(value), ensure_ascii=False)


def _execute_many(cursor: Any, statement: str, rows: Sequence[tuple[Any, ...]]) -> None:
    if rows:
        cursor.executemany(statement, rows)


def _snapshot_paths(root: Path, snapshot: dict[str, Any]) -> dict[str, Path]:
    return {name: root / relative for name, relative in snapshot["artifacts"].items()}


def _insert_snapshot(cursor: Any, snapshot: dict[str, Any]) -> None:
    cursor.execute(
        "SELECT content_fingerprint_sha256 FROM retrieval.corpus_snapshot "
        "WHERE corpus_snapshot_id = %s",
        (snapshot["corpus_snapshot_id"],),
    )
    existing = cursor.fetchone()
    if existing and existing[0] != snapshot["content_fingerprint_sha256"]:
        raise CorpusIntegrityError("database snapshot ID collision")
    previous = snapshot.get("previous_corpus_snapshot_id")
    if previous:
        cursor.execute(
            "SELECT 1 FROM retrieval.corpus_snapshot WHERE corpus_snapshot_id = %s", (previous,)
        )
        if cursor.fetchone() is None:
            previous = None
    cursor.execute(
        """
        INSERT INTO retrieval.corpus_snapshot(
            corpus_snapshot_id, content_fingerprint_sha256, schema_version,
            extraction_pipeline_version, retrieval_pipeline_version, created_at,
            previous_corpus_snapshot_id, snapshot_status, manifest
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'building', %s)
        ON CONFLICT (corpus_snapshot_id) DO NOTHING
        """,
        (
            snapshot["corpus_snapshot_id"],
            snapshot["content_fingerprint_sha256"],
            snapshot["schema_version_retrieval"],
            json.dumps(snapshot["extraction_pipeline_version"]),
            snapshot["retrieval_pipeline_version"],
            snapshot["created_at_utc"],
            previous,
            json.dumps(snapshot),
        ),
    )


def _insert_sources(cursor: Any, source_versions: list[dict[str, Any]], snapshot_id: str) -> None:
    for row in source_versions:
        cursor.execute(
            """
            INSERT INTO retrieval.source_document(
                source_document_id, title, document_kind, source_authority
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (source_document_id) DO NOTHING
            """,
            (
                row["source_document_id"], row["title"], row["document_kind"], row["source_authority"]
            ),
        )
        cursor.execute(
            """
            INSERT INTO retrieval.source_version(
                source_version_id, source_document_id, source_file_name, relative_path,
                source_status, source_role, source_authority, version_label, published_at,
                valid_from, valid_to, source_sha256, page_count, file_size_bytes,
                component_ranges, qa_status, qa_flags, extraction_pipeline_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            ) ON CONFLICT (source_version_id) DO NOTHING
            """,
            (
                row["source_version_id"], row["source_document_id"], row["source_file_name"],
                row["relative_path"], row["source_status"], row["source_role"],
                row["source_authority"], row["version_label"], row["published_at"],
                row["valid_from"], row["valid_to"], row["source_sha256"], row["page_count"],
                row["file_size_bytes"], json.dumps(row["component_ranges"]), row["qa_status"],
                json.dumps(row["qa_flags"]), row["extraction_pipeline_version"],
            ),
        )
        cursor.execute(
            "INSERT INTO retrieval.corpus_snapshot_source(corpus_snapshot_id, source_version_id) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (snapshot_id, row["source_version_id"]),
        )


def _insert_artifacts(cursor: Any, root: Path, snapshot: dict[str, Any]) -> None:
    snapshot_id = snapshot["corpus_snapshot_id"]
    entries: list[tuple[Any, ...]] = []
    for item in snapshot["canonical_files"]:
        entries.append(
            (
                snapshot_id, item["relative_path"], "canonical_jsonl", item["sha256"],
                item["line_count"], item["size_bytes"],
            )
        )
    for item in snapshot["source_pdfs"]:
        entries.append(
            (
                snapshot_id, item["relative_path"], "source_pdf", item["source_sha256"],
                None, item["file_size_bytes"],
            )
        )
    for item in snapshot.get("artifact_integrity", []):
        entries.append(
            (
                snapshot_id, item["relative_path"], "retrieval_provenance", item["sha256"],
                item["line_count"], item["size_bytes"],
            )
        )
    _execute_many(
        cursor,
        """
        INSERT INTO retrieval.corpus_artifact(
            corpus_snapshot_id, relative_path, artifact_kind, sha256, row_count, size_bytes
        ) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """,
        entries,
    )


def _insert_canonical_records(
    cursor: Any, root: Path, snapshot_id: str, records: list[dict[str, Any]]
) -> None:
    rows: list[tuple[Any, ...]] = []
    for record in records:
        exact = record["exact_source_text"]
        text_hash = sha256_text(exact)
        expected_hash = record.get("exact_source_text_raw_sha256")
        if expected_hash and expected_hash != text_hash:
            raise CorpusIntegrityError(f"canonical text hash mismatch: {record['record_id']}")
        hard_excluded = (
            record.get("status") == "excluded_by_policy"
            or record.get("exclusion_reason") == "hcc_historical_change_table"
        )
        eligible = is_primary_use_eligible(record)
        rows.append(
            (
                snapshot_id,
                record["record_id"],
                record["record_type"],
                f"sv-{record['source_sha256'][:24]}",
                _pg_safe_text(exact),
                exact.encode("utf-8"),
                text_hash,
                _json_hash(record),
                _canonical_json_bytes(record),
                record["pdf_pages_1based"],
                record.get("printed_page_label"),
                "eligible" if eligible else "ineligible",
                hard_excluded,
                record.get("exclusion_reason") or record.get("retrieval_exclusion_reason"),
                "review" if record.get("review_flags") else "validated",
                _pg_json(record.get("review_flags") or []),
                _pg_json(record),
            )
        )
    _execute_many(
        cursor,
        """
        INSERT INTO retrieval.canonical_record(
            corpus_snapshot_id, record_id, record_type, source_version_id,
            exact_source_text, exact_source_text_utf8, text_sha256, payload_sha256,
            payload_utf8, pdf_pages_1based,
            printed_page_label, eligibility_status, excluded_by_policy, exclusion_reason,
            qa_status, qa_flags, payload
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT DO NOTHING
        """,
        rows,
    )


def _insert_evidence_spans(cursor: Any, spans: list[dict[str, Any]]) -> None:
    rows = [
        (
            row["corpus_snapshot_id"], row["evidence_span_id"], row["retrieval_unit_id"],
            row["canonical_record_id"], row["source_version_id"],
            _pg_safe_text(row["exact_source_text"]), row["exact_source_text"].encode("utf-8"),
            row["text_sha256"], row["pdf_page_index"], row["pdf_pages_1based"],
            _pg_safe_text(row["printed_page_label"]), row["table_id"],
            _pg_safe_value(row["row_header_path"]), _pg_safe_value(row["column_header_path"]),
            _pg_safe_text(row["exact_table_cell_text"]),
            row["exact_table_cell_text"].encode("utf-8")
            if row["exact_table_cell_text"] is not None else None,
            row["qa_status"],
            _pg_json(row["qa_flags"]), row["eligibility_status"], row["excluded_by_policy"],
            row["exclusion_reason"],
        )
        for row in spans
    ]
    _execute_many(
        cursor,
        """
        INSERT INTO retrieval.evidence_span(
            corpus_snapshot_id, evidence_span_id, retrieval_unit_id, canonical_record_id,
            source_version_id, exact_source_text, exact_source_text_utf8, text_sha256, pdf_page_index,
            pdf_pages_1based, printed_page_label, table_id, row_header_path,
            column_header_path, exact_table_cell_text, exact_table_cell_text_utf8,
            qa_status, qa_flags,
            eligibility_status, excluded_by_policy, exclusion_reason
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s
        ) ON CONFLICT DO NOTHING
        """,
        rows,
    )


def _simple_search_text(unit: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "source_native_item_number", "printed_source_item_number", "strength",
        "dose_value", "dose_unit", "frequency", "route", "source_file_name",
    ):
        if unit.get(key):
            values.append(str(unit[key]))
    for key in ("product_names", "active_substance_names", "aliases", "chapter_path"):
        values.extend(str(value) for value in unit.get(key) or [] if value)
    return " | ".join(dict.fromkeys(values))


def _insert_retrieval_units(cursor: Any, units: list[dict[str, Any]]) -> None:
    rows = []
    for row in units:
        rows.append(
            (
                row["corpus_snapshot_id"], row["retrieval_unit_id"], row["evidence_span_id"],
                row["source_version_id"], row["source_document_id"], row["document_kind"],
                row["source_status"], row["document_component"], row["source_role"],
                row["source_authority"], row["source_sha256"], row["text_sha256"],
                _pg_safe_text(row["exact_source_text"]), row["exact_source_text"].encode("utf-8"),
                _pg_safe_text(row["retrieval_segment_text"]),
                row["retrieval_segment_text"].encode("utf-8"),
                row["retrieval_segment_sha256"], _pg_safe_text(row["retrieval_text"]),
                row["retrieval_text"].encode("utf-8"), _pg_safe_text(row["embedding_text"]),
                row["embedding_text"].encode("utf-8"), row["embedding_text_sha256"],
                _pg_safe_value(row["chapter_path"]), _pg_safe_text(row["source_native_item_type"]),
                _pg_safe_text(row["source_native_item_number"]),
                _pg_safe_text(row["printed_source_item_number"]), row["pdf_page_index"],
                row["pdf_pages_1based"], _pg_safe_text(row["printed_page_label"]), row["table_id"],
                _pg_safe_value(row["row_header_path"]), _pg_safe_value(row["column_header_path"]),
                _pg_safe_text(row["exact_table_cell_text"]),
                row["exact_table_cell_text"].encode("utf-8")
                if row["exact_table_cell_text"] is not None else None,
                _pg_safe_value(row["product_ids"]), _pg_safe_value(row["active_substance_ids"]),
                _pg_safe_value(row["product_names"]), _pg_safe_value(row["active_substance_names"]),
                _pg_safe_text(row["strength"]), _pg_safe_text(row["pharmaceutical_form"]),
                _pg_safe_text(row["route"]), _pg_safe_text(row["dose_value"]),
                _pg_safe_text(row["dose_unit"]), _pg_safe_text(row["frequency"]),
                _pg_safe_text(row["population"]), _pg_safe_value(row["aliases"]),
                _pg_safe_text(" | ".join(row["aliases"])),
                _pg_safe_text(_simple_search_text(row)), row["parent_id"],
                row["parent_record_ids"], row["relation_ids"], row["qa_status"],
                _pg_json(row["qa_flags"]), row["eligibility_status"], row["retrieval_eligible"],
                row["embedding_eligible"], row["answer_eligible"],
                row["primary_search_eligible"], row["excluded_by_policy"],
                row["exclusion_reason"], row["conflict_status"], row["citation_label"],
                row["source_file_name"], row["extraction_batch_id"],
                row["extraction_pipeline_version"], _pg_json(row["raw_v1"]),
            )
        )
    placeholders = ", ".join(["%s"] * 66)
    statement = f"""
        INSERT INTO retrieval.retrieval_unit(
            corpus_snapshot_id, retrieval_unit_id, evidence_span_id, source_version_id,
            source_document_id, document_kind, source_status, document_component,
            source_role, source_authority, source_sha256, text_sha256, exact_source_text,
            exact_source_text_utf8, retrieval_segment_text, retrieval_segment_text_utf8,
            retrieval_segment_sha256, retrieval_text, retrieval_text_utf8, embedding_text,
            embedding_text_utf8, embedding_text_sha256, chapter_path, source_native_item_type,
            source_native_item_number, printed_source_item_number, pdf_page_index,
            pdf_pages_1based, printed_page_label, table_id, row_header_path,
            column_header_path, exact_table_cell_text, exact_table_cell_text_utf8,
            product_ids, active_substance_ids,
            product_names, active_substance_names, strength, pharmaceutical_form, route,
            dose_value, dose_unit, frequency, population, aliases, aliases_text,
            simple_search_text, parent_id, parent_record_ids, relation_ids, qa_status,
            qa_flags, eligibility_status, retrieval_eligible, embedding_eligible,
            answer_eligible, primary_search_eligible, excluded_by_policy, exclusion_reason,
            conflict_status, citation_label, source_file_name, extraction_batch_id,
            extraction_pipeline_version, raw_v1
        ) VALUES ({placeholders}) ON CONFLICT DO NOTHING
    """
    if rows and len(rows[0]) != 66:
        raise AssertionError(f"retrieval unit insert has {len(rows[0])} values, expected 66")
    _execute_many(cursor, statement, rows)


def _insert_formal_items(
    cursor: Any, snapshot_id: str, records: list[dict[str, Any]]
) -> None:
    rows = []
    for row in records:
        if row["record_type"] != "formal_item":
            continue
        rows.append(
            (
                snapshot_id, row["formal_item_id"], row["record_id"],
                f"sv-{row['source_sha256'][:24]}", row["item_type"],
                row.get("source_item_number"), row.get("printed_source_item_number"),
                _pg_safe_text(row["exact_text_de"]), row["exact_text_de"].encode("utf-8"),
                _pg_safe_text(row.get("recommendation_grade")),
                _pg_safe_text(row.get("evidence_level")),
                _pg_safe_text(row.get("consensus_strength")),
                _pg_safe_value(row.get("section_path") or []),
                row["pdf_pages_1based"], "review" if row.get("review_flags") else "validated",
                "eligible" if is_primary_use_eligible(row) else "ineligible",
                row.get("status") == "excluded_by_policy", row.get("exclusion_reason"),
                _canonical_json_bytes(row), _pg_json(row),
            )
        )
    _execute_many(
        cursor,
        """
        INSERT INTO retrieval.formal_item(
            corpus_snapshot_id, formal_item_id, record_id, source_version_id,
            source_native_item_type, source_native_item_number, printed_source_item_number,
            exact_text, exact_text_utf8, recommendation_grade, evidence_level, consensus_strength,
            chapter_path, pdf_pages_1based, qa_status, eligibility_status,
            excluded_by_policy, exclusion_reason, payload_utf8, payload
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT DO NOTHING
        """,
        rows,
    )


def _insert_entities(cursor: Any, root: Path, snapshot_id: str, units: list[dict[str, Any]]) -> None:
    products = read_jsonl(root / "outputs/knowledge_corpus/canonical/drug_products.jsonl")
    substances = read_jsonl(root / "outputs/knowledge_corpus/canonical/active_substances.jsonl")
    product_rows = [
        (
            snapshot_id, row["product_id"], row["product_name"], row.get("aliases_original") or [],
            row.get("active_substance_ids") or [], row.get("strength"),
            row.get("pharmaceutical_form"), row.get("route"),
            f"sv-{row['source_sha256'][:24]}",
            "review" if row.get("review_flags") else "validated",
            _canonical_json_bytes(row), _pg_json(row),
        )
        for row in products
    ]
    _execute_many(
        cursor,
        """
        INSERT INTO retrieval.medicine_product(
            corpus_snapshot_id, medicine_product_id, preferred_name, aliases,
            active_substance_ids, strength, pharmaceutical_form, route,
            source_version_id, qa_status, payload_utf8, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        product_rows,
    )
    substance_rows = [
        (
            snapshot_id, row["active_substance_id"], row["preferred_name"],
            row.get("aliases") or [], f"sv-{row['source_sha256'][:24]}",
            "review" if row.get("review_flags") else "validated",
            _canonical_json_bytes(row), _pg_json(row),
        )
        for row in substances
    ]
    _execute_many(
        cursor,
        """
        INSERT INTO retrieval.active_substance(
            corpus_snapshot_id, active_substance_id, preferred_name, aliases,
            source_version_id, qa_status, payload_utf8, payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """,
        substance_rows,
    )
    valid_products = {row["product_id"] for row in products}
    valid_substances = {row["active_substance_id"] for row in substances}
    referenced_products = {item for unit in units for item in unit["product_ids"]}
    referenced_substances = {item for unit in units for item in unit["active_substance_ids"]}
    reference_rows = []
    for entity_id in sorted(referenced_products | valid_products):
        reference_rows.append(
            (
                snapshot_id, entity_id, "medicine_product",
                "resolved" if entity_id in valid_products else "unresolved",
                entity_id if entity_id in valid_products else None, [entity_id],
                json.dumps([] if entity_id in valid_products else ["no_validated_product_entity"]),
            )
        )
    for entity_id in sorted(referenced_substances | valid_substances):
        reference_rows.append(
            (
                snapshot_id, entity_id, "active_substance",
                "resolved" if entity_id in valid_substances else "unresolved",
                entity_id if entity_id in valid_substances else None, [entity_id],
                json.dumps([] if entity_id in valid_substances else ["no_validated_substance_entity"]),
            )
        )
    _execute_many(
        cursor,
        """
        INSERT INTO retrieval.entity_reference(
            corpus_snapshot_id, entity_reference_id, entity_kind, resolution_status,
            resolved_entity_id, source_identifiers, qa_flags
        ) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """,
        reference_rows,
    )


def _insert_relations(cursor: Any, relations: list[dict[str, Any]]) -> None:
    rows = [
        (
            row["corpus_snapshot_id"], row["relation_id"], row["relation_type"],
            row["from_kind"], row["from_id"], row["to_kind"], row["to_id"],
            row["from_retrieval_unit_id"], row["to_retrieval_unit_id"],
            row["is_direct_evidence"], row["qa_status"], _pg_json(row["metadata"]),
        )
        for row in relations
    ]
    _execute_many(
        cursor,
        """
        INSERT INTO retrieval.semantic_relation(
            corpus_snapshot_id, relation_id, relation_type, from_kind, from_id,
            to_kind, to_id, from_retrieval_unit_id, to_retrieval_unit_id,
            is_direct_evidence, qa_status, metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        rows,
    )


def import_snapshot(root: Path | None = None) -> dict[str, Any]:
    root = repository_root(root)
    snapshot = create_snapshot(root)
    snapshot_id = snapshot["corpus_snapshot_id"]
    paths = _snapshot_paths(root, snapshot)
    source_versions = read_jsonl(paths["source_versions"])
    units = read_jsonl(paths["retrieval_units_v2"])
    spans = read_jsonl(paths["evidence_spans"])
    relations = read_jsonl(paths["semantic_relations"])
    records = load_canonical_records(root)
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (IMPORT_LOCK_ID,))
        _insert_snapshot(cursor, snapshot)
        _insert_sources(cursor, source_versions, snapshot_id)
        _insert_artifacts(cursor, root, snapshot)
        _insert_canonical_records(cursor, root, snapshot_id, records)
        _insert_evidence_spans(cursor, spans)
        _insert_retrieval_units(cursor, units)
        _insert_formal_items(cursor, snapshot_id, records)
        _insert_entities(cursor, root, snapshot_id, units)
        _insert_relations(cursor, relations)
        connection.commit()
    preseal = validate_database_import(root, snapshot_id, require_sealed=False)
    if not preseal["passed"]:
        raise CorpusIntegrityError("pre-seal database validation failed")
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE retrieval.corpus_snapshot SET snapshot_status = 'sealed', "
            "sealed_at = coalesce(sealed_at, clock_timestamp()) WHERE corpus_snapshot_id = %s",
            (snapshot_id,),
        )
        connection.commit()
    return validate_database_import(root, snapshot_id, require_sealed=True)


def _count(cursor: Any, table: str, snapshot_id: str) -> int:
    cursor.execute(
        sql.SQL("SELECT count(*) FROM {} WHERE corpus_snapshot_id = %s").format(
            sql.Identifier("retrieval", table)
        ),
        (snapshot_id,),
    )
    return int(cursor.fetchone()[0])


def validate_database_import(
    root: Path | None, snapshot_id: str, *, require_sealed: bool = True
) -> dict[str, Any]:
    root = repository_root(root)
    manifest_path = root / f"outputs/knowledge_corpus/manifests/corpus_snapshots/{snapshot_id}.json"
    snapshot = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "sources": snapshot["source_count"],
        "canonical_records": snapshot["canonical_record_count"],
        "evidence_spans": snapshot["evidence_span_count"],
        "retrieval_units": snapshot["retrieval_unit_count"],
        "formal_items": snapshot["record_counts"]["formal_item"],
        "medicine_products": 28,
        "active_substances": 10,
        "semantic_relations": snapshot["semantic_relation_count"],
    }
    checks: dict[str, bool] = {}
    counts: dict[str, int] = {}
    with connect(root) as connection, connection.cursor() as cursor:
        counts["sources"] = _count(cursor, "corpus_snapshot_source", snapshot_id)
        counts["canonical_records"] = _count(cursor, "canonical_record", snapshot_id)
        counts["evidence_spans"] = _count(cursor, "evidence_span", snapshot_id)
        counts["retrieval_units"] = _count(cursor, "retrieval_unit", snapshot_id)
        counts["formal_items"] = _count(cursor, "formal_item", snapshot_id)
        counts["medicine_products"] = _count(cursor, "medicine_product", snapshot_id)
        counts["active_substances"] = _count(cursor, "active_substance", snapshot_id)
        counts["semantic_relations"] = _count(cursor, "semantic_relation", snapshot_id)
        checks["record_counts"] = counts == expected
        cursor.execute(
            "SELECT count(*) FROM retrieval.canonical_record WHERE corpus_snapshot_id=%s "
            "AND (exact_source_text_utf8 IS NULL OR payload_utf8 IS NULL "
            "OR encode(digest(exact_source_text_utf8, 'sha256'), 'hex') <> text_sha256 "
            "OR encode(digest(payload_utf8, 'sha256'), 'hex') <> payload_sha256)",
            (snapshot_id,),
        )
        checks["canonical_text_hashes"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.retrieval_unit WHERE corpus_snapshot_id=%s "
            "AND (exact_source_text_utf8 IS NULL OR retrieval_segment_text_utf8 IS NULL "
            "OR embedding_text_utf8 IS NULL "
            "OR encode(digest(exact_source_text_utf8, 'sha256'), 'hex') <> text_sha256 "
            "OR encode(digest(retrieval_segment_text_utf8, 'sha256'), 'hex') <> retrieval_segment_sha256 "
            "OR encode(digest(embedding_text_utf8, 'sha256'), 'hex') <> embedding_text_sha256)",
            (snapshot_id,),
        )
        checks["retrieval_text_hashes"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.evidence_span WHERE corpus_snapshot_id=%s "
            "AND (exact_source_text_utf8 IS NULL OR "
            "encode(digest(exact_source_text_utf8, 'sha256'), 'hex') <> text_sha256 "
            "OR (exact_table_cell_text IS NOT NULL AND exact_table_cell_text_utf8 IS NULL))",
            (snapshot_id,),
        )
        checks["evidence_span_text_hashes"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.canonical_record WHERE corpus_snapshot_id=%s "
            "AND position(decode('00', 'hex') in exact_source_text_utf8) > 0",
            (snapshot_id,),
        )
        checks["legacy_nul_bytes_preserved"] = cursor.fetchone()[0] == 218
        cursor.execute(
            "SELECT count(*) FROM retrieval.formal_item WHERE corpus_snapshot_id=%s "
            "AND (exact_text_utf8 IS NULL OR payload_utf8 IS NULL)",
            (snapshot_id,),
        )
        checks["formal_item_lossless_payloads"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.medicine_product WHERE corpus_snapshot_id=%s "
            "AND payload_utf8 IS NULL",
            (snapshot_id,),
        )
        product_payload_missing = cursor.fetchone()[0]
        cursor.execute(
            "SELECT count(*) FROM retrieval.active_substance WHERE corpus_snapshot_id=%s "
            "AND payload_utf8 IS NULL",
            (snapshot_id,),
        )
        checks["entity_lossless_payloads"] = product_payload_missing == 0 and cursor.fetchone()[0] == 0
        cursor.execute(
            """
            SELECT count(*) FROM retrieval.canonical_record cr
            JOIN retrieval.source_version sv ON sv.source_version_id=cr.source_version_id
            WHERE cr.corpus_snapshot_id=%s
              AND EXISTS (
                  SELECT 1 FROM unnest(cr.pdf_pages_1based) p
                  WHERE p < 1 OR p > sv.page_count
              )
            """,
            (snapshot_id,),
        )
        checks["source_locators"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.canonical_record WHERE corpus_snapshot_id=%s "
            "AND excluded_by_policy AND exclusion_reason='hcc_historical_change_table'",
            (snapshot_id,),
        )
        checks["hcc_exclusion_count"] = cursor.fetchone()[0] == 99
        cursor.execute(
            "SELECT count(*) FROM retrieval.source_version sv "
            "JOIN retrieval.corpus_snapshot_source css USING (source_version_id) "
            "WHERE css.corpus_snapshot_id=%s "
            "AND sv.source_file_name ILIKE '%%HCC%%BCC%%Konsultationsfassung%%' "
            "AND sv.source_status='consultation_draft'",
            (snapshot_id,),
        )
        checks["consultation_draft_status"] = cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT count(*) FROM retrieval.retrieval_unit WHERE corpus_snapshot_id=%s "
            "AND (excluded_by_policy OR exclusion_reason='hcc_historical_change_table')",
            (snapshot_id,),
        )
        checks["no_excluded_base_retrieval_units"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.retrieval_unit WHERE corpus_snapshot_id=%s "
            "AND document_kind='medicinal_product_information' "
            "AND (document_component <> 'smPC' OR source_role <> 'smPC')",
            (snapshot_id,),
        )
        checks["retrieved_drug_records_are_smpc"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT snapshot_status FROM retrieval.corpus_snapshot WHERE corpus_snapshot_id=%s",
            (snapshot_id,),
        )
        status = cursor.fetchone()[0]
        checks["snapshot_status"] = status == "sealed" if require_sealed else status in {"building", "sealed"}
        if require_sealed:
            cursor.execute(
                "SELECT count(*) FROM retrieval.eligible_retrieval_units WHERE corpus_snapshot_id=%s",
                (snapshot_id,),
            )
            checks["eligible_view_count"] = cursor.fetchone()[0] == snapshot["retrieval_unit_count"]
            cursor.execute(
                "SELECT count(*) FROM retrieval.eligible_retrieval_units "
                "WHERE corpus_snapshot_id=%s AND (excluded_by_policy OR exclusion_reason='hcc_historical_change_table')",
                (snapshot_id,),
            )
            checks["eligible_view_no_policy_leakage"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='retrieval' "
            "AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%')"
        )
        checks["no_approximate_vector_index"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.source_version sv "
            "JOIN retrieval.corpus_snapshot_source css ON css.source_version_id=sv.source_version_id "
            "WHERE css.corpus_snapshot_id=%s AND sv.source_sha256 NOT IN "
            "(SELECT sha256 FROM retrieval.corpus_artifact WHERE corpus_snapshot_id=%s AND artifact_kind='source_pdf')",
            (snapshot_id, snapshot_id),
        )
        checks["source_hashes"] = cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM retrieval.semantic_relation WHERE corpus_snapshot_id=%s "
            "AND ((from_retrieval_unit_id IS NOT NULL AND from_retrieval_unit_id NOT IN "
            "(SELECT retrieval_unit_id FROM retrieval.retrieval_unit WHERE corpus_snapshot_id=%s)) "
            "OR (to_retrieval_unit_id IS NOT NULL AND to_retrieval_unit_id NOT IN "
            "(SELECT retrieval_unit_id FROM retrieval.retrieval_unit WHERE corpus_snapshot_id=%s)))",
            (snapshot_id, snapshot_id, snapshot_id),
        )
        checks["relation_integrity"] = cursor.fetchone()[0] == 0
        cursor.execute(
            """
            SELECT entity_kind, resolution_status, count(*)
            FROM retrieval.entity_reference WHERE corpus_snapshot_id=%s
            GROUP BY entity_kind, resolution_status
            """,
            (snapshot_id,),
        )
        entity_counts = {(kind, status): int(count) for kind, status, count in cursor.fetchall()}
        checks["entity_resolution_is_conservative"] = entity_counts == {
            ("medicine_product", "resolved"): 28,
            ("medicine_product", "unresolved"): 39,
            ("active_substance", "resolved"): 10,
            ("active_substance", "unresolved"): 30,
        }
        safe_functions = (
            "search_exact", "search_lexical", "search_trigram", "search_vector_exact",
            "expand_relations", "evidence_package_rows",
        )
        cursor.execute(
            """
            SELECT p.proname, pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='retrieval' AND p.proname = ANY(%s)
            """,
            (list(safe_functions),),
        )
        definitions = {name: definition for name, definition in cursor.fetchall()}
        checks["all_normal_functions_use_eligibility_gateway"] = (
            set(definitions) == set(safe_functions)
            and all("eligible_retrieval_units" in definition for definition in definitions.values())
        )
    return {
        "schema_version": "database-import-validation-1.0.0",
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "corpus_snapshot_id": snapshot_id,
        "expected_counts": expected,
        "database_counts": counts,
        "checks": checks,
        "passed": all(checks.values()),
    }


def snapshot_table_counts(root: Path | None, snapshot_id: str) -> dict[str, int]:
    names = (
        "corpus_snapshot_source", "canonical_record", "evidence_span", "retrieval_unit",
        "formal_item", "medicine_product", "active_substance", "entity_reference",
        "semantic_relation", "retrieval_embedding",
    )
    with connect(root) as connection, connection.cursor() as cursor:
        return {name: _count(cursor, name, snapshot_id) for name in names}

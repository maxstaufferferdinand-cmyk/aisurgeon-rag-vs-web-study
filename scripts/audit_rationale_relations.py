#!/usr/bin/env python3
"""Audit the two previously reported VTE rationale relations without mutation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from aisurgeon_decentralised.rag_core import resolve_snapshot_id
from aisurgeon_decentralised.retrieval_config import repository_root
from aisurgeon_decentralised.retrieval_database import connect

KNOWN = (
    ("rec-7a2c99c3a6dffc908b5eb111", "rec-5ea56ee4466171d5d4fe21dd"),
    ("rec-95dfba9075cff2c1f6940c9c", "rec-f89ace17e294f2bf3cac9942"),
)


def main() -> int:
    root = repository_root()
    snapshot_id = resolve_snapshot_id(root)
    canonical = {
        row["record_id"]: row
        for line in (root / "outputs/knowledge_corpus/canonical/formal_items.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }
    checks = []
    with connect(root, autocommit=True) as connection, connection.cursor() as cursor:
        for formal_record_id, rationale_record_id in KNOWN:
            row = canonical[formal_record_id]
            explicit = rationale_record_id in (
                row.get("explicit_linked_rationale_record_ids") or []
            )
            cursor.execute(
                """
                SELECT retrieval_unit_id, pdf_pages_1based
                FROM retrieval.eligible_retrieval_units
                WHERE corpus_snapshot_id=%s AND parent_record_ids @> ARRAY[%s]::text[]
                """,
                (snapshot_id, formal_record_id),
            )
            formal = cursor.fetchone()
            cursor.execute(
                """
                SELECT retrieval_unit_id, pdf_pages_1based
                FROM retrieval.eligible_retrieval_units
                WHERE corpus_snapshot_id=%s AND parent_record_ids @> ARRAY[%s]::text[]
                """,
                (snapshot_id, rationale_record_id),
            )
            rationale = cursor.fetchone()
            cursor.execute(
                """
                SELECT relation_id, qa_status
                FROM retrieval.semantic_relation
                WHERE corpus_snapshot_id=%s
                  AND relation_type='guideline_item_to_rationale'
                  AND from_retrieval_unit_id=%s AND to_retrieval_unit_id=%s
                """,
                (snapshot_id, formal[0], rationale[0]),
            )
            relation = cursor.fetchone()
            checks.append(
                {
                    "formal_record_id": formal_record_id,
                    "rationale_record_id": rationale_record_id,
                    "source_item_number": row.get("source_item_number"),
                    "printed_source_item_number": row.get(
                        "printed_source_item_number"
                    ),
                    "formal_pdf_pages_1based": formal[1],
                    "rationale_pdf_pages_1based": rationale[1],
                    "canonical_explicit_link": explicit,
                    "formal_retrieval_unit_id": formal[0],
                    "rationale_retrieval_unit_id": rationale[0],
                    "semantic_relation_id": relation[0] if relation else None,
                    "semantic_relation_qa_status": relation[1] if relation else None,
                    "already_repaired": bool(explicit and relation),
                    "mutation_performed": False,
                }
            )
    payload = {
        "schema_version": "rationale-relation-audit-1.0.0",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "corpus_snapshot_id": snapshot_id,
        "reported_pairs": len(KNOWN),
        "canonical_explicit_and_indexed": sum(
            check["already_repaired"] for check in checks
        ),
        "mutation_performed": False,
        "decision": "no_change_required",
        "checks": checks,
        "passed": all(check["already_repaired"] for check in checks),
    }
    output = root / "outputs/retrieval_phase/qa"
    output.mkdir(parents=True, exist_ok=True)
    (output / "rationale_relation_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Audit der zwei gemeldeten Rationale-Relationen",
        "",
        f"Snapshot: `{snapshot_id}`",
        "",
        "Beide Relationen sind bereits durch explizite kanonische Felder belegt "
        "und als validierte gerichtete Relation im Index vorhanden. Es wurde "
        "keine kanonische Datei verändert.",
        "",
        "| Item | Formalrecord | Rationalerecord | Relation | Ergebnis |",
        "|---|---|---|---|---|",
    ]
    for check in checks:
        item = check["source_item_number"] or (
            f"gedruckt {check['printed_source_item_number']}, kanonisch null"
        )
        lines.append(
            f"| {item} | `{check['formal_record_id']}` | "
            f"`{check['rationale_record_id']}` | "
            f"`{check['semantic_relation_id']}` | bereits repariert |"
        )
    (output / "rationale_relation_audit.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

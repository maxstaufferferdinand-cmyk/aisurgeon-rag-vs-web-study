"""Deterministic eligibility policy for search, answers, and evidence expansion."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

HCC_HISTORICAL_EXCLUSION_REASON = "hcc_historical_change_table"


def is_primary_use_eligible(record: Mapping[str, Any]) -> bool:
    """Return whether a record may enter normal search or answer evidence."""

    if record.get("status") == "excluded_by_policy":
        return False
    if record.get("exclusion_reason") == HCC_HISTORICAL_EXCLUSION_REASON:
        return False
    return all(
        record.get(field) is not False
        for field in (
            "retrieval_eligible",
            "embedding_eligible",
            "answer_eligible",
            "primary_search_eligible",
        )
    )


def filter_normal_search_records(
    records: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Apply the same hard policy gate expected by a future normal search path."""

    return [record for record in records if is_primary_use_eligible(record)]


def build_answer_evidence_package(
    seed_record_ids: Sequence[str],
    records_by_id: Mapping[str, Mapping[str, Any]],
    links: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Expand links without ever traversing into policy-excluded records.

    This is intentionally provider-neutral.  A future GPT or other synthesis
    layer can use it as the mandatory evidence gate.
    """

    adjacency: dict[str, set[str]] = {}
    for link in links:
        source_id = str(link.get("from_record_id") or "")
        target_id = str(link.get("to_record_id") or "")
        source = records_by_id.get(source_id)
        target = records_by_id.get(target_id)
        if not source or not target:
            continue
        if not is_primary_use_eligible(source) or not is_primary_use_eligible(target):
            continue
        adjacency.setdefault(source_id, set()).add(target_id)

    visited: set[str] = set()
    queue = [
        record_id
        for record_id in seed_record_ids
        if record_id in records_by_id
        and is_primary_use_eligible(records_by_id[record_id])
    ]
    while queue:
        record_id = queue.pop(0)
        if record_id in visited:
            continue
        visited.add(record_id)
        queue.extend(sorted(adjacency.get(record_id, set()) - visited))
    return [records_by_id[record_id] for record_id in sorted(visited)]

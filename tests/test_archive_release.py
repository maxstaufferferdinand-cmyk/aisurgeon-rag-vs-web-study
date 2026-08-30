from __future__ import annotations

import json
from pathlib import Path

from aisurgeon_decentralised.archive_release import (
    SECRET_PATTERNS,
    SNAPSHOT_ID,
    archive_allowlist,
    pseudonymize,
    sanitize_string,
)

ROOT = Path(__file__).resolve().parents[1]


def test_secret_patterns_detect_representative_values() -> None:
    assert SECRET_PATTERNS["github_token"].search("ghp_" + "a" * 30)
    dsn = "postgresql" + "://" + "user" + ":" + "secret" + "@db/test"
    assert SECRET_PATTERNS["credentialed_dsn"].search(dsn)


def test_sanitizer_removes_machine_specific_paths() -> None:
    example_home = "/" + "home" + "/" + "example" + "/.config"
    value = sanitize_string(f"{ROOT}/outputs and {example_home}", ROOT)
    assert str(ROOT) not in value
    assert example_home not in value
    assert "${PROJECT_ROOT}" in value
    assert "${USER_HOME}" in value


def test_operational_identifier_pseudonymization_is_deterministic() -> None:
    first = pseudonymize("request-123", "attempt")
    assert first == pseudonymize("request-123", "attempt")
    assert first != pseudonymize("request-124", "attempt")
    assert "request-123" not in str(first)


def test_repository_allowlist_has_no_forbidden_primary_data_when_built() -> None:
    if not (ROOT / "archive/repository_allowlist.txt").is_file():
        return
    paths = archive_allowlist(ROOT)
    assert not any(path.lower().endswith(".pdf") for path in paths)
    assert not any(path.startswith("outputs/knowledge_corpus/canonical/") for path in paths)
    assert not any("/query_embeddings/" in path or "/full/ecp-" in path for path in paths)
    assert "outputs/study_phase2/results/api_attempts.jsonl" not in paths


def test_archive_core_counts_when_built() -> None:
    manifest = ROOT / "archive/corpus/source_manifest.json"
    results = ROOT / "archive/study_phase2/results/study_results_redacted.jsonl"
    if not manifest.is_file() or not results.is_file():
        return
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["corpus_snapshot_id"] == SNAPSHOT_ID
    assert payload["source_count"] == 12
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 800
    assert len({row["run_id"] for row in rows}) == 800

#!/usr/bin/env python3
"""Destructively rebuild only the named regenerable retrieval database volume."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aisurgeon_decentralised.corpus_snapshot import create_snapshot

ROOT = Path(__file__).resolve().parents[1]
PINNED_IMAGE = (
    "pgvector/pgvector:0.8.6-pg18-trixie@"
    "sha256:78bf48b801e792f99e3ac62b5036fd3876e9be48afda16c1e331af1c75ceb2ff"
)


def _run(arguments: list[str]) -> tuple[dict[str, Any] | None, float]:
    started = time.perf_counter()
    result = subprocess.run(
        arguments, cwd=ROOT, text=True, capture_output=True, check=False
    )
    duration = time.perf_counter() - started
    if result.returncode != 0:
        # These project commands never print secrets; still keep the error bounded.
        detail = (result.stderr or result.stdout)[-2000:]
        raise RuntimeError(f"command failed ({arguments[1]}): {detail}")
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else None
    return payload, duration


def _source_hashes(snapshot: dict[str, Any]) -> dict[str, str]:
    return {item["relative_path"]: item["source_sha256"] for item in snapshot["source_pdfs"]}


def _docker_executable() -> str:
    candidates: list[Path] = []
    for name in ("docker", "docker.exe"):
        value = shutil.which(name)
        if value:
            candidates.append(Path(value))
    candidates.extend(
        Path("/mnt/c/Users").glob(
            "*/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            probe = subprocess.run(
                [str(candidate), "version", "--format", "{{.Client.Version}}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if probe.returncode == 0:
                return str(candidate)
    raise RuntimeError("no working Docker CLI found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--yes-really-reset",
        action="store_true",
        help="required: removes only Docker volume aisurgeon_retrieval_pgdata",
    )
    args = parser.parse_args()
    if not args.yes_really_reset:
        parser.error("rebuild requires --yes-really-reset")
    before = create_snapshot(ROOT)
    steps: dict[str, Any] = {}
    _, steps["reset_seconds"] = _run(
        [sys.executable, "scripts/retrieval_stack.py", "reset", "--yes-really-reset"]
    )
    _, steps["start_seconds"] = _run(
        [sys.executable, "scripts/retrieval_stack.py", "start"]
    )
    first_migration, steps["first_migration_seconds"] = _run(
        [sys.executable, "scripts/migrate_retrieval_db.py"]
    )
    second_migration, steps["second_migration_seconds"] = _run(
        [sys.executable, "scripts/migrate_retrieval_db.py"]
    )
    imported, steps["import_seconds"] = _run(
        [sys.executable, "scripts/import_corpus_snapshot.py", "--verify-idempotent"]
    )
    embedded, steps["embedding_restore_seconds"] = _run(
        [
            sys.executable, "scripts/embed_retrieval_units.py", "--full", "--resume",
            "--batch-size", "64",
        ]
    )
    restored, steps["structured_restore_seconds"] = _run(
        [
            sys.executable, "scripts/openai_structured_output_smoke.py",
            "--restore-from-report",
        ]
    )
    semantic, steps["semantic_resume_seconds"] = _run(
        [sys.executable, "scripts/run_semantic_retrieval_smoke.py"]
    )
    validated, steps["validation_seconds"] = _run(
        [sys.executable, "scripts/validate_retrieval_layer.py"]
    )
    after = create_snapshot(ROOT)
    docker = _docker_executable()
    compose_base = [
        docker, "compose", "--env-file", ".env.retrieval", "-f", "docker-compose.yml"
    ]
    container = subprocess.run(
        [*compose_base, "ps", "-q", "postgres"], cwd=ROOT, text=True,
        capture_output=True, check=True,
    ).stdout.strip()
    image_id = subprocess.run(
        [docker, "inspect", "--format", "{{.Image}}", container], cwd=ROOT,
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    source_unchanged = _source_hashes(before) == _source_hashes(after)
    report = {
        "schema_version": "retrieval-database-rebuild-1.0.0",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "corpus_snapshot_id": after["corpus_snapshot_id"],
        "destructive_scope": "named_regenerable_volume_aisurgeon_retrieval_pgdata_only",
        "pinned_image": PINNED_IMAGE,
        "runtime_image_id": image_id,
        "source_pdf_hashes_unchanged": source_unchanged,
        "first_migration_applied": first_migration["applied"] if first_migration else None,
        "second_migration_applied": second_migration["applied"] if second_migration else None,
        "migration_idempotent": bool(second_migration and not second_migration["applied"]),
        "import_idempotent": bool(imported and imported["idempotent"]),
        "database_counts": imported["counts_after_second_import"] if imported else None,
        "embedding_restore": {
            key: embedded[key]
            for key in (
                "complete", "checkpointed_embedding_count", "database_embedding_count",
                "new_embedding_count", "resume_skipped_count", "provider_calls_this_run",
                "input_tokens", "estimated_cost_usd",
            )
        } if embedded else None,
        "structured_contract_restore": restored,
        "semantic_checkpoint_resume": {
            key: semantic[key]
            for key in ("passed", "expected_rank_at_20", "provider_calls_this_run")
        } if semantic else None,
        "final_validation": {
            "passed": validated["passed"],
            "passed_check_count": validated["passed_check_count"],
            "total_check_count": validated["total_check_count"],
        } if validated else None,
        "step_durations_seconds": steps,
    }
    report["passed"] = bool(
        source_unchanged
        and report["migration_idempotent"]
        and report["import_idempotent"]
        and report["embedding_restore"]["complete"]
        and report["embedding_restore"]["provider_calls_this_run"] == 0
        and report["structured_contract_restore"]["api_calls"] == 0
        and report["semantic_checkpoint_resume"]["provider_calls_this_run"] == 0
        and report["final_validation"]["passed"]
    )
    output = ROOT / "outputs/retrieval_phase" / after["corpus_snapshot_id"] / "qa"
    output.mkdir(parents=True, exist_ok=True)
    (output / "database_rebuild.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

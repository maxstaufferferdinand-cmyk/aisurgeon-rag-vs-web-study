#!/usr/bin/env python3
"""Finalize the targeted-repair report with backup and file-hash evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aisurgeon_decentralised.knowledge_corpus_pipeline import atomic_write_json, utc_now

MARKER = "\n## Wiederherstellbarkeit und Abschlussvalidierung\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output_root = project_root / "outputs/knowledge_corpus"
    backup_dirs = sorted(
        (output_root / "qa/backups").glob("targeted_repair_*/backup_manifest.json")
    )
    if not backup_dirs:
        raise RuntimeError("Targeted-repair backup manifest is missing")
    backup_manifest_path = backup_dirs[-1]
    backup_manifest = json.loads(backup_manifest_path.read_text(encoding="utf-8"))

    file_audit: list[dict[str, Any]] = []
    for item in backup_manifest["files"]:
        path = project_root / item["relative_path"]
        after_hash = sha256_file(path) if path.exists() else None
        file_audit.append(
            {
                "relative_path": item["relative_path"],
                "sha256_before": item["sha256_before"],
                "sha256_after": after_hash,
                "changed": after_hash != item["sha256_before"],
                "exists_after": path.exists(),
            }
        )

    validation_path = output_root / "qa/final_validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    report_path = output_root / "qa/targeted_repair_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.update(
        {
            "audit_finalized_at_utc": utc_now(),
            "backup_manifest_path": str(backup_manifest_path.relative_to(project_root)),
            "backed_up_file_count": backup_manifest["file_count"],
            "backed_up_files_changed_count": sum(row["changed"] for row in file_audit),
            "file_sha256_before_after": file_audit,
            "final_validation_path": str(validation_path.relative_to(project_root)),
            "final_validation_passed": validation.get("passed") is True,
            "final_validation_check_count": len(validation.get("checks") or {}),
            "git_commit_performed": False,
            "git_push_performed": False,
        }
    )
    if not report["final_validation_passed"]:
        report["status"] = "STOP_REPAIR_VALIDATION"
        report["can_proceed_to_postgresql_pgvector"] = False
    atomic_write_json(report_path, report)

    markdown_path = output_root / "qa/targeted_repair_report.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    if MARKER in markdown:
        markdown = markdown.split(MARKER, 1)[0].rstrip() + "\n"
    markdown += MARKER
    markdown += (
        f"\n- Backup-Manifest: `{report['backup_manifest_path']}`"
        f"\n- Gesicherte Dateien: {report['backed_up_file_count']}"
        f"\n- Dateien mit dokumentierter SHA-256-Änderung: "
        f"{report['backed_up_files_changed_count']}"
        f"\n- Abschlussvalidator: {report['final_validation_check_count']}/"
        f"{report['final_validation_check_count']} bestanden"
        "\n- Git-Commit/Git-Push: nicht durchgeführt\n"
    )
    markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "report": str(report_path),
                "backup_manifest": str(backup_manifest_path),
                "file_audit_count": len(file_audit),
                "changed_file_count": report["backed_up_files_changed_count"],
                "validation_passed": report["final_validation_passed"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

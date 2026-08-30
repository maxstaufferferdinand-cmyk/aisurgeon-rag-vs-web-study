#!/usr/bin/env python3
"""Freeze the PDF input set and collect deterministic source metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader

SCHEMA_VERSION = "source-manifest-1.0.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)


def stable_source_id(path: Path, digest: str) -> str:
    stem = path.stem.casefold()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    stem = stem[:64].rstrip("-") or "source"
    return f"src-{stem}-{digest[:12]}"


def classify_document(path: Path, metadata: dict[str, Any], first_text: str) -> str:
    haystack = " ".join(
        [path.name, str(metadata.get("/Title", "")), str(metadata.get("/Subject", "")), first_text[:8000]]
    ).casefold()
    guideline_markers = ("leitlinie", "langversion", "konsultationsfassung", "s3-ll", "s3_ll")
    label_markers = (
        "fachinformation",
        "zusammenfassung der merkmale des arzneimittels",
        "product information",
        "epar",
        "annex i",
        "anhang i",
    )
    guideline_score = sum(marker in haystack for marker in guideline_markers)
    label_score = sum(marker in haystack for marker in label_markers)
    if guideline_score > label_score:
        return "guideline"
    if label_score > guideline_score:
        return "drug_label"
    return "unclassified"


def density_record(page_number: int, text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    chars = len(normalized)
    words = len(normalized.split()) if normalized else 0
    if chars == 0:
        density = "none"
    elif chars < 250:
        density = "low"
    elif chars < 1800:
        density = "medium"
    elif chars < 4500:
        density = "high"
    else:
        density = "very_high"
    return {
        "pdf_page_1based": page_number,
        "text_char_count": chars,
        "word_count": words,
        "text_density": density,
    }


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_manifest(project_root: Path, source_dir: Path) -> dict[str, Any]:
    paths = sorted(
        (path for path in source_dir.iterdir() if path.is_file() and path.suffix.casefold() == ".pdf"),
        key=lambda path: path.name.casefold(),
    )
    if not paths:
        raise RuntimeError(f"No PDF sources found in {source_dir}")

    sources: list[dict[str, Any]] = []
    for path in paths:
        digest = sha256_file(path)
        reader = PdfReader(str(path), strict=False)
        encrypted = bool(reader.is_encrypted)
        decrypt_status = "not_encrypted"
        if encrypted:
            try:
                decrypt_result = reader.decrypt("")
            except Exception as exc:  # pragma: no cover - blocker path
                raise RuntimeError(f"Encrypted PDF cannot be opened: {path.name}: {exc}") from exc
            if not decrypt_result:
                raise RuntimeError(f"Encrypted PDF requires a password: {path.name}")
            decrypt_status = "empty_password_opened"

        metadata = json_safe(dict(reader.metadata or {}))
        page_densities: list[dict[str, Any]] = []
        preview_text: list[str] = []
        extraction_errors: list[dict[str, Any]] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                text = ""
                extraction_errors.append(
                    {
                        "pdf_page_1based": page_number,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            page_densities.append(density_record(page_number, text))
            if page_number <= 5:
                preview_text.append(text)

        try:
            page_labels = list(reader.page_labels)
        except Exception:
            page_labels = []
        if len(page_labels) != len(reader.pages):
            page_labels = [None] * len(reader.pages)

        sources.append(
            {
                "source_id": stable_source_id(path, digest),
                "original_file_name": path.name,
                "absolute_path": str(path.resolve()),
                "relative_path": path.resolve().relative_to(project_root.resolve()).as_posix(),
                "sha256": digest,
                "file_size_bytes": path.stat().st_size,
                "page_count": len(reader.pages),
                "pdf_metadata": metadata,
                "is_encrypted": encrypted,
                "decryption_status": decrypt_status,
                "document_type": classify_document(path, metadata, "\n".join(preview_text)),
                "page_labels": page_labels,
                "page_text_density": page_densities,
                "local_text_extraction_errors": extraction_errors,
            }
        )

    if any(source["document_type"] == "unclassified" for source in sources):
        names = [source["original_file_name"] for source in sources if source["document_type"] == "unclassified"]
        raise RuntimeError(f"Unclassified PDF source(s): {names}")

    return {
        "schema_version": SCHEMA_VERSION,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root.resolve()),
        "source_directory": str(source_dir.resolve()),
        "discovery_rule": "direct children with case-insensitive .pdf suffix, sorted by casefolded filename",
        "source_count": len(sources),
        "total_pages": sum(source["page_count"] for source in sources),
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    source_dir = (args.source_dir or project_root / "source_pdfs").resolve()
    output = (args.output or project_root / "outputs/knowledge_corpus/manifests/source_manifest.json").resolve()

    fresh = build_manifest(project_root, source_dir)
    if output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        frozen_signature = [(item["relative_path"], item["sha256"]) for item in existing.get("sources", [])]
        fresh_signature = [(item["relative_path"], item["sha256"]) for item in fresh["sources"]]
        if frozen_signature != fresh_signature:
            raise RuntimeError("Existing frozen source manifest differs from current PDF input set")
        print(f"Source manifest already frozen and unchanged: {output}")
        return

    atomic_write_json(output, fresh)
    print(f"Frozen {fresh['source_count']} PDFs / {fresh['total_pages']} pages: {output}")


if __name__ == "__main__":
    main()

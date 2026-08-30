"""Official-source model/snapshot verification immediately before study freeze."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .study_phase2 import utc_now

OFFICIAL_URLS = {
    "gpt55": "https://developers.openai.com/api/docs/models/gpt-5.5",
    "gpt56_sol": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
    "pricing": "https://developers.openai.com/api/docs/pricing",
}
EXPECTED_GPT55_SNAPSHOT = "gpt-5.5-2026-04-23"


def _fetch(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "AISurgeon-research-reproducibility/1.0"},
    )
    with urlopen(request, timeout=45) as response:
        return response.read()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def verify_official_model_availability(*, root: Path) -> dict[str, Any]:
    pages = {name: _fetch(url) for name, url in OFFICIAL_URLS.items()}
    gpt55_text = pages["gpt55"].decode("utf-8", errors="ignore")
    gpt56_text = pages["gpt56_sol"].decode("utf-8", errors="ignore")
    gpt55_snapshots = sorted(set(re.findall(r"gpt-5\.5-\d{4}-\d{2}-\d{2}", gpt55_text)))
    gpt56_snapshots = sorted(
        set(re.findall(r"gpt-5\.6-sol-\d{4}-\d{2}-\d{2}", gpt56_text))
    )
    issues = []
    if gpt55_snapshots != [EXPECTED_GPT55_SNAPSHOT]:
        issues.append("gpt55_dated_snapshot_changed_or_ambiguous")
    if gpt56_snapshots:
        issues.append("dated_gpt56_sol_snapshot_now_available")
    report = {
        "schema_version": "official-model-availability-verification-1.0.0",
        "verified_at_utc": utc_now(),
        "official_sources_only": True,
        "urls": OFFICIAL_URLS,
        "page_sha256": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in pages.items()
        },
        "detected_gpt55_dated_snapshots": gpt55_snapshots,
        "frozen_gpt55_snapshot": EXPECTED_GPT55_SNAPSHOT,
        "detected_gpt56_sol_dated_snapshots": gpt56_snapshots,
        "frozen_gpt56_sol_request": "gpt-5.6-sol",
        "gpt56_sol_alias_limitation": not gpt56_snapshots,
        "pricing_page_retrieved": bool(pages["pricing"]),
        "issues": issues,
        "status": "verified" if not issues else "MODEL_SNAPSHOT_REVIEW_REQUIRED",
    }
    path = root / (
        "outputs/study_phase2/manifest/model_availability_verification.json"
    )
    _write_json(path, report)
    return report


def require_recent_model_verification(*, root: Path) -> dict[str, Any]:
    path = root / (
        "outputs/study_phase2/manifest/model_availability_verification.json"
    )
    if not path.is_file():
        raise RuntimeError(
            "official model verification is missing; run "
            "scripts/verify_openai_study_models.py immediately before freeze"
        )
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("status") != "verified":
        raise RuntimeError(str(report.get("status") or "model verification failed"))
    verified = datetime.fromisoformat(str(report["verified_at_utc"]))
    if datetime.now(UTC) - verified > timedelta(hours=24):
        raise RuntimeError(
            "official model verification is older than 24 hours; refresh before freeze"
        )
    return report


__all__ = [
    "EXPECTED_GPT55_SNAPSHOT",
    "OFFICIAL_URLS",
    "require_recent_model_verification",
    "verify_official_model_availability",
]

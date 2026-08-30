#!/usr/bin/env python3
"""Verify model/snapshot availability from the three allowed official pages."""

from __future__ import annotations

import json
from pathlib import Path

from aisurgeon_decentralised.study_model_verification import (
    verify_official_model_availability,
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = verify_official_model_availability(root=root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "verified" else 5


if __name__ == "__main__":
    raise SystemExit(main())

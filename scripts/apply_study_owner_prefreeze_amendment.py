#!/usr/bin/env python3
"""Apply the owner-approved, versioned Phase-2 pre-freeze amendment."""

from __future__ import annotations

import json
from pathlib import Path

from aisurgeon_decentralised.study_amendment import (
    apply_study_owner_prefreeze_amendment,
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = apply_study_owner_prefreeze_amendment(root=root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

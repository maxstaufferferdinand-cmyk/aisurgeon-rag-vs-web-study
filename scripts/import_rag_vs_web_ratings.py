#!/usr/bin/env python3
"""Import completed blinded clinical ratings and citation audit."""

from __future__ import annotations

import json
from pathlib import Path

from aisurgeon_decentralised.study_ratings import import_human_ratings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = import_human_ratings(root=root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

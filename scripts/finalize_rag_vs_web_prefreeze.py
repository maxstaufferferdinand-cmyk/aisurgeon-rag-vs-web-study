#!/usr/bin/env python3
"""Create deterministic Phase-2 pre-freeze metrics and study exports."""

from __future__ import annotations

import json
from pathlib import Path

from aisurgeon_decentralised.study_analysis import build_prefreeze_artifacts


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = build_prefreeze_artifacts(root=root)
    print(
        json.dumps(
            {
                "status": report["status"],
                "questions": report["question_summary"]["total"],
                "coverage": report["question_summary"]["coverage"],
                "pilot_responses": report["pilot"]["recorded_responses"],
                "conservative_total_projection_usd": report["cost_projection"][
                    "conservative_total_projection_usd"
                ],
                "cost_limit_usd": report["cost_limit_usd"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

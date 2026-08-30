#!/usr/bin/env python3
"""Build all deterministic technical outputs after the 800-cell run."""

from __future__ import annotations

import json
from pathlib import Path

from aisurgeon_decentralised.study_finalization import finalize_main_study


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = finalize_main_study(root=root)
    print(
        json.dumps(
            {
                "status": report["status"],
                "execution": report["execution"],
                "external_api_cost_usd": report[
                    "external_api_cost_usd_including_preparation_and_pilot"
                ],
                "clinical_rating_status": report["clinical_rating_status"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

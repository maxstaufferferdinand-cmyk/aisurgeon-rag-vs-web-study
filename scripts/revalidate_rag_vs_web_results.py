#!/usr/bin/env python3
"""Re-apply local provenance validators without making paid API calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.study_postprocess import revalidate_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        choices=("pilot", "main"),
        default="pilot",
        help="Persisted result set to validate in place.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.dataset == "pilot":
        results = root / (
            "outputs/study_phase2/pilot/development_cost_pilot_results.jsonl"
        )
        report = root / (
            "outputs/study_phase2/pilot/deterministic_revalidation.json"
        )
    else:
        results = root / "outputs/study_phase2/results/study_results.jsonl"
        report = root / "outputs/study_phase2/qa/main_deterministic_revalidation.json"
    if not results.is_file():
        raise SystemExit(f"result file does not exist: {results}")
    summary = revalidate_results(
        root=root,
        results_path=results,
        report_path=report,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

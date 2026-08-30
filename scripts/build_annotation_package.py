#!/usr/bin/env python3
"""Build the deterministic, unlabelled human-annotation package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.retrieval_evaluation import (
    DEFAULT_SAMPLING_SEED,
    build_annotation_package,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/retrieval_phase/evaluation"),
    )
    parser.add_argument("--snapshot-manifest", type=Path)
    parser.add_argument("--retrieval-units", type=Path)
    parser.add_argument("--hcc-exclusions", type=Path)
    parser.add_argument("--development-count", type=int, default=50)
    parser.add_argument("--test-count", type=int, default=250)
    parser.add_argument("--sampling-seed", default=DEFAULT_SAMPLING_SEED)
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = project_root / output_dir
    manifest = build_annotation_package(
        project_root=project_root,
        output_dir=output_dir,
        snapshot_manifest_path=args.snapshot_manifest,
        retrieval_units_path=args.retrieval_units,
        hcc_exclusions_path=args.hcc_exclusions,
        development_count=args.development_count,
        test_count=args.test_count,
        sampling_seed=args.sampling_seed,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

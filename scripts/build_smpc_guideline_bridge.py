#!/usr/bin/env python3
"""Build the audited one-way SmPC-to-guideline bridge artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.corpus_snapshot import create_snapshot
from aisurgeon_decentralised.retrieval_config import repository_root
from aisurgeon_decentralised.smpc_guideline_bridge import (
    build_bridge_rows,
    write_bridge_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/retrieval_phase/bridges"),
    )
    args = parser.parse_args()
    root = repository_root()
    snapshot_id = create_snapshot(root)["corpus_snapshot_id"]
    rows = build_bridge_rows(corpus_snapshot_id=snapshot_id, root=root)
    qa = write_bridge_artifacts(rows, output_dir=root / args.output_dir)
    print(json.dumps(qa, ensure_ascii=False, indent=2, sort_keys=True))
    if not qa["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

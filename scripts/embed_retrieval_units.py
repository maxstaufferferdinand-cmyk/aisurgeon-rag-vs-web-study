#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from aisurgeon_decentralised.retrieval_embeddings import (
    run_embedding_smoke,
    run_full_embeddings,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate sequential OpenAI embedding checkpoints and import them"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true", help="embed three public units")
    mode.add_argument("--full", action="store_true", help="resume the complete active corpus")
    parser.add_argument("--resume", action="store_true", help="document explicit resume intent")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    result = (
        run_embedding_smoke()
        if args.smoke
        else run_full_embeddings(batch_size=args.batch_size)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("passed", result.get("complete", False)):
        raise SystemExit(1)

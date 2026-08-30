#!/usr/bin/env python3
"""Calculate predeclared retrieval, citation, answer, policy and load metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.retrieval_evaluation import evaluate_records, read_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Adjudicated result JSONL")
    parser.add_argument("--output", type=Path, help="Metrics JSON; stdout when omitted")
    parser.add_argument("--k-values", default="5,10,20")
    args = parser.parse_args()
    k_values = tuple(int(value) for value in args.k_values.split(",") if value.strip())
    report = evaluate_records(read_jsonl(args.input), k_values=k_values)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()

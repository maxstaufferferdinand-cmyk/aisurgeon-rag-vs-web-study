#!/usr/bin/env python3
"""Run interactive or one-shot policy-gated closed-corpus RAG queries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aisurgeon_decentralised.rag_core import RagCore, RetrievalMode
from aisurgeon_decentralised.rag_exports import (
    csv_export_row,
    json_export_row,
    write_csv,
    write_jsonl,
)
from aisurgeon_decentralised.rag_responses import ClosedResponsesError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Closed, policy-gated RAG query CLI (no web or provider tools)."
    )
    parser.add_argument("--question")
    parser.add_argument("--question-id", default="interactive-001")
    parser.add_argument("--snapshot-id")
    parser.add_argument(
        "--retrieval-mode",
        choices=tuple(mode.value for mode in RetrievalMode),
        default=RetrievalMode.HYBRID_RRF_BRIDGE.value,
    )
    parser.add_argument(
        "--routing",
        choices=("auto", "guideline_first", "smpc_first", "dual_source"),
        default="auto",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--baseline-without-retrieval", action="store_true")
    parser.add_argument("--output-jsonl", type=Path)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument(
        "--include-question-in-study-export",
        action="store_true",
        help="explicit opt-in; operational telemetry still stores only the hash",
    )
    return parser


def _questions(args: argparse.Namespace):
    if args.question is not None:
        yield args.question_id, args.question
        return
    counter = 1
    while True:
        try:
            value = input("Klinische Frage (leer zum Beenden): ").strip()
        except EOFError:
            return
        if not value:
            return
        yield f"interactive-{counter:03d}", value
        counter += 1


def main() -> int:
    args = _parser().parse_args()
    try:
        core = RagCore(corpus_snapshot_id=args.snapshot_id)
        had_question = False
        for question_id, question in _questions(args):
            had_question = True
            result = core.run(
                question=question,
                question_id=question_id,
                retrieval_mode=args.retrieval_mode,
                routing_mode=args.routing,
                dry_run=args.dry_run,
                baseline_without_retrieval=args.baseline_without_retrieval,
            )
            export = json_export_row(
                result,
                question=question,
                include_question=args.include_question_in_study_export,
            )
            print(json.dumps(export, ensure_ascii=False, indent=2))
            if args.output_jsonl:
                write_jsonl([export], path=args.output_jsonl, append=True)
            if args.output_csv:
                write_csv([csv_export_row(result)], path=args.output_csv, append=True)
        if not had_question:
            return 0
        return 0
    except ClosedResponsesError as exc:
        payload = {
            "error": "closed_responses_error",
            "status_code": exc.status_code,
            "retry_count": exc.retry_count,
            "error_code": exc.error_code,
            "telemetry_written": True,
        }
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"error": type(exc).__name__, "message": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

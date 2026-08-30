#!/usr/bin/env python3
"""Prepare the prespecified study without bypassing the human freeze."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.study_exports import (
    build_question_review_workbook,
    build_study_workbooks,
    export_planned_results,
    validate_study_workbooks,
)
from aisurgeon_decentralised.study_phase2 import StudyQuestion, read_jsonl
from aisurgeon_decentralised.study_preparation import prepare_study


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--audit-retrieval",
        action="store_true",
        help="Run FTS, exact vector, hybrid RRF and bridge checks for all 100 drafts.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    summary = prepare_study(root=root, run_retrieval_audit=args.audit_retrieval)
    candidate_path = root / "outputs/study_phase2/questions/question_candidates.jsonl"
    questions = tuple(
        StudyQuestion.model_validate(row) for row in read_jsonl(candidate_path)
    )
    review_path = root / "outputs/study_phase2/questions/question_freeze_review.xlsx"
    build_question_review_workbook(questions=questions, path=review_path)
    export_planned_results(root=root, questions=questions)
    build_study_workbooks(root=root, questions=questions)
    excel_qa = validate_study_workbooks(root=root)
    qa_path = root / "outputs/study_phase2/qa/excel_integrity.json"
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(
        json.dumps(excel_qa, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary.update(
        {
            "review_workbook": str(review_path),
            "excel_integrity": excel_qa["status"],
            "candidate_rows": len(read_jsonl(candidate_path)),
        }
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

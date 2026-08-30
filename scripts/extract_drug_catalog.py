from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.drug_extraction_pipeline import (
    clean_internal_batch_pdfs,
    print_terminal_summary,
    run_extraction,
)
from aisurgeon_decentralised.local_config import secret_env_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract a source-linked medication catalog from guideline PDFs.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--env-file",
        type=Path,
        default=secret_env_path(),
    )
    parser.add_argument("--resume-run-id", default=None)
    args = parser.parse_args()

    report = run_extraction(args.root, args.env_file, args.resume_run_id)
    clean_internal_batch_pdfs(args.root, report["extraction_run_id"])
    print_terminal_summary(report, args.root)
    print("raw_report_json:")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.drug_docx import (
    build_docx,
    render_docx_for_qa,
    update_report_docx_status,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and render-QA the medication catalog DOCX.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    output_dir = args.root / "outputs" / "medications"
    docx_path = build_docx(output_dir)
    qa_result = render_docx_for_qa(docx_path, output_dir / "_docx_render_qa")
    update_report_docx_status(output_dir, qa_result)
    print(json.dumps({"docx_path": str(docx_path.resolve()), "qa": qa_result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

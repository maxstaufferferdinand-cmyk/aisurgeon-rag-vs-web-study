#!/usr/bin/env python3
"""Run the repository-wide archive security scan without exposing matches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.archive_release import build_security_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    report = build_security_report(root, root / "archive")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

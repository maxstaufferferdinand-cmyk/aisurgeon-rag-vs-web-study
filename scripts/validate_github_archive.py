#!/usr/bin/env python3
"""Fail closed when the curated GitHub archive violates release policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.archive_release import validate_archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(validate_archive(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

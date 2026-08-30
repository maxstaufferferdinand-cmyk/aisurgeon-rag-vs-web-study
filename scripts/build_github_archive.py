#!/usr/bin/env python3
"""Create new redacted GitHub archive artifacts without changing history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aisurgeon_decentralised.archive_release import build_archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(build_archive(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

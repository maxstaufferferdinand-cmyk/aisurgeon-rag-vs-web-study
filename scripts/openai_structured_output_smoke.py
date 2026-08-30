#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from aisurgeon_decentralised.structured_output_smoke import (
    restore_structured_smoke_persistence,
    run_structured_output_smoke,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-from-report", action="store_true")
    args = parser.parse_args()
    result = (
        restore_structured_smoke_persistence()
        if args.restore_from_report else run_structured_output_smoke()
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("passed", result.get("restored", False)):
        raise SystemExit(1)

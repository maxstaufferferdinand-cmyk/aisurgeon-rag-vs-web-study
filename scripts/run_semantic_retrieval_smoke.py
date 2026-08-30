#!/usr/bin/env python3
from __future__ import annotations

import json

from aisurgeon_decentralised.retrieval_semantic_smoke import run_semantic_smoke

if __name__ == "__main__":
    result = run_semantic_smoke()
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)

#!/usr/bin/env python3
from __future__ import annotations

import json

from aisurgeon_decentralised.retrieval_validation import (
    validate_retrieval_layer,
    write_validation_reports,
)

if __name__ == "__main__":
    result = validate_retrieval_layer()
    write_validation_reports(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)

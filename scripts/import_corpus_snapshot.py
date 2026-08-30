#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from aisurgeon_decentralised.corpus_snapshot import create_snapshot
from aisurgeon_decentralised.retrieval_database import (
    import_snapshot,
    snapshot_table_counts,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-idempotent", action="store_true")
    args = parser.parse_args()
    first = import_snapshot()
    result: dict[str, object] = {"first_import": first}
    if args.verify_idempotent:
        snapshot_id = create_snapshot()["corpus_snapshot_id"]
        before = snapshot_table_counts(None, snapshot_id)
        second = import_snapshot()
        after = snapshot_table_counts(None, snapshot_id)
        result.update(
            {
                "second_import": second,
                "counts_before_second_import": before,
                "counts_after_second_import": after,
                "idempotent": before == after and second["passed"],
            }
        )
        if not result["idempotent"]:
            raise SystemExit(1)
    print(json.dumps(result, indent=2, sort_keys=True))

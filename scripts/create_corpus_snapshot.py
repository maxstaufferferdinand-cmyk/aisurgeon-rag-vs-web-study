#!/usr/bin/env python3
"""Create or verify the immutable retrieval corpus snapshot."""

from __future__ import annotations

import json

from aisurgeon_decentralised.corpus_snapshot import create_snapshot

if __name__ == "__main__":
    snapshot = create_snapshot()
    print(
        json.dumps(
            {
                "corpus_snapshot_id": snapshot["corpus_snapshot_id"],
                "source_count": snapshot["source_count"],
                "page_count": snapshot["page_count"],
                "canonical_record_count": snapshot["canonical_record_count"],
                "retrieval_unit_count": snapshot["retrieval_unit_count"],
                "excluded_hcc_historical_records": snapshot[
                    "eligibility_policy_statistics"
                ]["excluded_hcc_historical_records"],
            },
            indent=2,
            sort_keys=True,
        )
    )

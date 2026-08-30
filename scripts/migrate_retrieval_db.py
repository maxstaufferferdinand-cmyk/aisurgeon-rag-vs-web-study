#!/usr/bin/env python3
from __future__ import annotations

import json

from aisurgeon_decentralised.retrieval_database import apply_migrations

if __name__ == "__main__":
    print(json.dumps(apply_migrations(), indent=2, sort_keys=True))

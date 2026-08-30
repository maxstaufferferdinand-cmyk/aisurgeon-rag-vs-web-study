from __future__ import annotations

import pytest

from aisurgeon_decentralised.retrieval_database import (
    connect,
    validate_database_import,
)
from aisurgeon_decentralised.retrieval_embeddings import validate_embedding_baseline
from aisurgeon_decentralised.retrieval_validation import validate_retrieval_layer

SNAPSHOT_ID = "cs-f61b3d4e90089c1b890c23cb"


def _database_available() -> bool:
    try:
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0] == 1
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(), reason="local retrieval PostgreSQL stack is not running"
)


def test_live_database_import_integrity() -> None:
    report = validate_database_import(None, SNAPSHOT_ID)
    assert report["passed"] is True
    assert all(report["checks"].values())


def test_live_embedding_baseline_integrity() -> None:
    report = validate_embedding_baseline(None, snapshot_id=SNAPSHOT_ID)
    assert report["passed"] is True
    assert report["measurements"]["count"] == 4469
    assert report["measurements"]["approximate_vector_indexes"] == 0


def test_live_end_to_end_retrieval_validator() -> None:
    report = validate_retrieval_layer()
    assert report["passed"] is True
    assert report["passed_check_count"] == report["total_check_count"]
    assert report["total_check_count"] >= 37

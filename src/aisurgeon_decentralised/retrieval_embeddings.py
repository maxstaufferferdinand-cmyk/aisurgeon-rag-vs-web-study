"""Sequential, checkpointed embedding generation and exact pgvector persistence."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from dotenv import dotenv_values

from .corpus_snapshot import (
    CorpusIntegrityError,
    create_snapshot,
    read_jsonl,
    sha256_file,
)
from .local_config import secret_env_path
from .retrieval_config import (
    EMBEDDING_DIMENSION,
    EMBEDDING_DISTANCE,
    EMBEDDING_MODEL,
    EMBEDDING_PRICE_AS_OF,
    EMBEDDING_PRICE_USD_PER_MILLION_TOKENS,
    repository_root,
)
from .retrieval_database import connect

OPENAI_ENV_PATH = secret_env_path()
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
FATAL_HTTP_STATUSES = {400, 401, 403}


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    usage: dict[str, int | float | str | None]


class EmbeddingProvider(Protocol):
    model: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class OpenAIEmbeddingProvider:
    """OpenAI provider with SDK retries disabled and explicit bounded backoff."""

    def __init__(
        self,
        *,
        model: str = EMBEDDING_MODEL,
        dimension: int = EMBEDDING_DIMENSION,
        max_attempts: int = 5,
    ) -> None:
        from openai import OpenAI

        values = dotenv_values(OPENAI_ENV_PATH)
        api_key = values.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(f"OPENAI_API_KEY missing in {OPENAI_ENV_PATH}")
        self.model = model
        self.dimension = dimension
        self.max_attempts = max_attempts
        self._client = OpenAI(api_key=str(api_key), max_retries=0, timeout=90.0)
        self.call_count = 0

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch([], {"prompt_tokens": 0, "total_tokens": 0})
        for attempt in range(1, self.max_attempts + 1):
            self.call_count += 1
            try:
                response = self._client.embeddings.create(
                    model=self.model,
                    input=list(texts),
                    dimensions=self.dimension,
                    encoding_format="float",
                )
                ordered = sorted(response.data, key=lambda item: item.index)
                usage_obj = response.usage
                usage = {
                    "prompt_tokens": getattr(usage_obj, "prompt_tokens", None),
                    "total_tokens": getattr(usage_obj, "total_tokens", None),
                }
                return EmbeddingBatch(
                    vectors=[list(item.embedding) for item in ordered], usage=usage
                )
            except Exception as exc:  # SDK exception classes vary across patch releases
                status = getattr(exc, "status_code", None)
                if status in FATAL_HTTP_STATUSES:
                    raise RuntimeError(
                        f"fatal OpenAI embedding HTTP {status}; no automatic retry"
                    ) from exc
                retryable = status in RETRYABLE_HTTP_STATUSES or (
                    isinstance(status, int) and status >= 500
                )
                if not retryable or attempt >= self.max_attempts:
                    raise
                time.sleep(min(2 ** (attempt - 1), 30))
        raise AssertionError("unreachable")


class DeterministicFakeEmbeddingProvider:
    """Offline provider used only by automated tests, never as the baseline."""

    model = "deterministic-test-provider"

    def __init__(self, dimension: int = EMBEDDING_DIMENSION) -> None:
        self.dimension = dimension
        self.call_count = 0

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.call_count += 1
        vectors: list[list[float]] = []
        for text in texts:
            seed = hashlib.shake_256(text.encode("utf-8")).digest(self.dimension * 2)
            values = [int.from_bytes(seed[i : i + 2], "big") - 32768 for i in range(0, len(seed), 2)]
            norm = math.sqrt(sum(value * value for value in values))
            vectors.append([value / norm for value in values])
        return EmbeddingBatch(vectors, {"prompt_tokens": None, "total_tokens": None})


def _validate_vector(vector: Sequence[float], dimension: int) -> float:
    if len(vector) != dimension:
        raise CorpusIntegrityError(
            f"embedding dimension {len(vector)} does not match expected {dimension}"
        )
    if not all(math.isfinite(value) for value in vector):
        raise CorpusIntegrityError("embedding contains non-finite values")
    norm = math.sqrt(sum(float(value) ** 2 for value in vector))
    if not 0.99 <= norm <= 1.01:
        raise CorpusIntegrityError(f"embedding norm {norm:.8f} is not approximately one")
    return norm


def _checkpoint_id(snapshot_id: str, model: str, units: Sequence[dict[str, Any]]) -> str:
    payload = "\n".join(
        f"{row['retrieval_unit_id']}:{row['embedding_text_sha256']}:{row['text_sha256']}"
        for row in units
    )
    digest = hashlib.sha256(f"{snapshot_id}|{model}|{payload}".encode()).hexdigest()
    return f"ecp-{digest[:24]}"


def _checkpoint_root(root: Path, snapshot_id: str, model: str) -> Path:
    return root / "outputs/retrieval_phase" / snapshot_id / "embeddings" / model


def _write_gzip_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    os.replace(temporary, path)


def _load_checkpoint(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _validated_checkpoint_records(
    root: Path,
    snapshot_id: str,
    model: str,
    dimension: int,
    unit_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    base = _checkpoint_root(root, snapshot_id, model)
    for path in sorted(base.glob("**/*.json.gz")):
        payload = _load_checkpoint(path)
        if payload.get("corpus_snapshot_id") != snapshot_id:
            raise CorpusIntegrityError(f"checkpoint snapshot mismatch: {path}")
        if payload.get("model") != model or payload.get("dimension") != dimension:
            raise CorpusIntegrityError(f"checkpoint model/dimension mismatch: {path}")
        for record in payload.get("records", []):
            unit_id = record["retrieval_unit_id"]
            unit = unit_by_id.get(unit_id)
            if unit is None:
                raise CorpusIntegrityError(f"unknown retrieval unit in checkpoint: {unit_id}")
            if (
                record["embedding_text_sha256"] != unit["embedding_text_sha256"]
                or record["source_text_sha256"] != unit["text_sha256"]
            ):
                raise CorpusIntegrityError(f"checkpoint hash mismatch: {unit_id}")
            _validate_vector(record["embedding"], dimension)
            previous = records.get(unit_id)
            if previous and previous != record:
                raise CorpusIntegrityError(f"conflicting checkpoints for {unit_id}")
            records[unit_id] = record
    return records


@contextmanager
def _embedding_process_lock(root: Path) -> Iterator[None]:
    directory = root / ".retrieval-locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "embedding-api.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"another embedding process owns {path}") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"


def import_embedding_records(
    root: Path, snapshot_id: str, model: str, records: Sequence[dict[str, Any]]
) -> dict[str, int]:
    inserted = 0
    with connect(root) as connection, connection.cursor() as cursor:
        for record in records:
            cursor.execute(
                """
                INSERT INTO retrieval.retrieval_embedding(
                    corpus_snapshot_id, retrieval_unit_id, model, dimension,
                    distance_metric, embedding, embedding_text_sha256,
                    source_text_sha256, created_at, batch_id, checkpoint_id,
                    input_tokens, api_usage, estimated_cost_usd, price_as_of
                ) VALUES (
                    %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                ) ON CONFLICT DO NOTHING
                """,
                (
                    snapshot_id, record["retrieval_unit_id"], model, record["dimension"],
                    EMBEDDING_DISTANCE, _vector_literal(record["embedding"]),
                    record["embedding_text_sha256"], record["source_text_sha256"],
                    record["created_at_utc"], record["batch_id"], record["checkpoint_id"],
                    record.get("input_tokens"), json.dumps(record["api_usage"]),
                    record.get("estimated_cost_usd"), EMBEDDING_PRICE_AS_OF,
                ),
            )
            inserted += cursor.rowcount
            cursor.execute(
                """
                SELECT dimension, embedding_text_sha256, source_text_sha256,
                       vector_dims(embedding), distance_metric
                FROM retrieval.retrieval_embedding
                WHERE corpus_snapshot_id=%s AND retrieval_unit_id=%s AND model=%s
                """,
                (snapshot_id, record["retrieval_unit_id"], model),
            )
            existing = cursor.fetchone()
            expected = (
                record["dimension"], record["embedding_text_sha256"],
                record["source_text_sha256"], record["dimension"], EMBEDDING_DISTANCE,
            )
            if existing != expected:
                raise CorpusIntegrityError(
                    f"database embedding metadata conflict: {record['retrieval_unit_id']}"
                )
        connection.commit()
    return {"submitted": len(records), "inserted": inserted}


def _units_for_snapshot(root: Path, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    path = root / snapshot["artifacts"]["retrieval_units_v2"]
    units = read_jsonl(path)
    if len(units) != snapshot["retrieval_unit_count"]:
        raise CorpusIntegrityError("snapshot retrieval unit count changed")
    if not all(
        unit["embedding_eligible"] and unit["eligibility_status"] == "eligible"
        and not unit["excluded_by_policy"]
        for unit in units
    ):
        raise CorpusIntegrityError("non-eligible unit reached embedding input")
    return units


def _select_smoke_units(units: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    predicates = (
        lambda row: row["source_role"] == "guideline" and row["source_status"] == "final",
        lambda row: row["source_role"] == "smPC",
        lambda row: row["source_status"] == "consultation_draft",
    )
    for predicate in predicates:
        selected.append(next(row for row in units if predicate(row)))
    if len({row["retrieval_unit_id"] for row in selected}) != 3:
        raise CorpusIntegrityError("smoke sample did not select three distinct public units")
    return selected


def _create_batch_checkpoint(
    root: Path,
    snapshot_id: str,
    provider: EmbeddingProvider,
    units: Sequence[dict[str, Any]],
    *,
    batch_id: str,
    subdirectory: str,
) -> list[dict[str, Any]]:
    checkpoint_id = _checkpoint_id(snapshot_id, provider.model, units)
    path = _checkpoint_root(root, snapshot_id, provider.model) / subdirectory / f"{checkpoint_id}.json.gz"
    if path.exists():
        raise CorpusIntegrityError(f"unexpected existing unindexed checkpoint: {path}")
    result = provider.embed([row["embedding_text"] for row in units])
    if len(result.vectors) != len(units):
        raise CorpusIntegrityError("provider returned a different number of embeddings")
    prompt_tokens = result.usage.get("prompt_tokens")
    total_cost = (
        float(prompt_tokens) * EMBEDDING_PRICE_USD_PER_MILLION_TOKENS / 1_000_000
        if isinstance(prompt_tokens, int) else None
    )
    created_at = datetime.now(UTC).isoformat()
    records: list[dict[str, Any]] = []
    for unit, vector in zip(units, result.vectors, strict=True):
        norm = _validate_vector(vector, provider.dimension)
        records.append(
            {
                "retrieval_unit_id": unit["retrieval_unit_id"],
                "model": provider.model,
                "dimension": provider.dimension,
                "distance_metric": EMBEDDING_DISTANCE,
                "embedding_text_sha256": unit["embedding_text_sha256"],
                "source_text_sha256": unit["text_sha256"],
                "created_at_utc": created_at,
                "batch_id": batch_id,
                "checkpoint_id": checkpoint_id,
                "input_tokens": None,
                "api_usage": {
                    **result.usage,
                    "scope": "sequential_batch",
                    "record_count": len(units),
                },
                "estimated_cost_usd": total_cost / len(units) if total_cost is not None else None,
                "vector_l2_norm": norm,
                "embedding": vector,
            }
        )
    payload = {
        "schema_version": "embedding-checkpoint-1.0.0",
        "corpus_snapshot_id": snapshot_id,
        "model": provider.model,
        "dimension": provider.dimension,
        "distance_metric": EMBEDDING_DISTANCE,
        "checkpoint_id": checkpoint_id,
        "batch_id": batch_id,
        "created_at_utc": created_at,
        "api_usage": result.usage,
        "price_usd_per_million_input_tokens": EMBEDDING_PRICE_USD_PER_MILLION_TOKENS,
        "price_as_of": EMBEDDING_PRICE_AS_OF,
        "records": records,
    }
    _write_gzip_json_atomic(path, payload)
    # Validate the file immediately before it is considered resumable.
    roundtrip = _load_checkpoint(path)
    if roundtrip["checkpoint_id"] != checkpoint_id or len(roundtrip["records"]) != len(records):
        raise CorpusIntegrityError(f"checkpoint round-trip failed: {path}")
    return records


def _database_embedding_count(root: Path, snapshot_id: str, model: str) -> int:
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM retrieval.retrieval_embedding "
            "WHERE corpus_snapshot_id=%s AND model=%s",
            (snapshot_id, model),
        )
        return int(cursor.fetchone()[0])


def _similarity_self_check(
    root: Path, snapshot_id: str, model: str, records: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    with connect(root) as connection, connection.cursor() as cursor:
        for record in records:
            cursor.execute(
                "SELECT retrieval_unit_id, rank, cosine_distance "
                "FROM retrieval.search_vector_exact(%s, %s::vector, %s, 1, NULL)",
                (snapshot_id, _vector_literal(record["embedding"]), model),
            )
            result = cursor.fetchone()
            checks.append(
                {
                    "query_retrieval_unit_id": record["retrieval_unit_id"],
                    "top_retrieval_unit_id": result[0] if result else None,
                    "rank": result[1] if result else None,
                    "cosine_distance": result[2] if result else None,
                    "self_top_1": bool(result and result[0] == record["retrieval_unit_id"]),
                }
            )
    return checks


def _embedding_summary(
    root: Path,
    snapshot: dict[str, Any],
    provider: EmbeddingProvider,
    unit_by_id: dict[str, dict[str, Any]],
    *,
    mode: str,
    provider_calls_this_run: int | None,
    smoke_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records = _validated_checkpoint_records(
        root, snapshot["corpus_snapshot_id"], provider.model, provider.dimension, unit_by_id
    )
    # Count each API batch once; batch usage is repeated on record rows by design.
    usage_by_checkpoint: dict[str, dict[str, Any]] = {}
    for record in records.values():
        usage_by_checkpoint.setdefault(record["checkpoint_id"], record["api_usage"])
    prompt_tokens = sum(
        int(usage.get("prompt_tokens") or 0) for usage in usage_by_checkpoint.values()
    )
    cost = prompt_tokens * EMBEDDING_PRICE_USD_PER_MILLION_TOKENS / 1_000_000
    checkpoint_manifest: list[dict[str, Any]] = []
    base = _checkpoint_root(root, snapshot["corpus_snapshot_id"], provider.model)
    for path in sorted(base.glob("**/*.json.gz")):
        payload = _load_checkpoint(path)
        checkpoint_manifest.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "checkpoint_id": payload["checkpoint_id"],
                "batch_id": payload["batch_id"],
                "record_count": len(payload["records"]),
                "input_tokens": payload["api_usage"].get("prompt_tokens"),
                "size_bytes": path.stat().st_size,
            }
        )
    return {
        "schema_version": "embedding-run-summary-1.0.0",
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "mode": mode,
        "corpus_snapshot_id": snapshot["corpus_snapshot_id"],
        "model": provider.model,
        "dimension": provider.dimension,
        "distance_metric": EMBEDDING_DISTANCE,
        "checkpointed_embedding_count": len(records),
        "database_embedding_count": _database_embedding_count(
            root, snapshot["corpus_snapshot_id"], provider.model
        ),
        "expected_embedding_count": snapshot["retrieval_unit_count"],
        "checkpoint_count": len(usage_by_checkpoint),
        "input_tokens": prompt_tokens,
        "estimated_cost_usd": cost,
        "price_usd_per_million_input_tokens": EMBEDDING_PRICE_USD_PER_MILLION_TOKENS,
        "price_as_of": EMBEDDING_PRICE_AS_OF,
        "provider_calls_this_run": provider_calls_this_run,
        "smoke_similarity_checks": smoke_checks or [],
        "checkpoint_manifest": checkpoint_manifest,
    }


def validate_embedding_baseline(
    root: Path | None = None,
    *,
    snapshot_id: str | None = None,
    model: str = EMBEDDING_MODEL,
) -> dict[str, Any]:
    """Validate count, hashes, dimensions, normalisation and policy isolation."""
    root = repository_root(root)
    snapshot = create_snapshot(root)
    snapshot_id = snapshot_id or snapshot["corpus_snapshot_id"]
    checks: dict[str, bool] = {}
    measurements: dict[str, Any] = {}
    with connect(root) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*), min(vector_dims(re.embedding)), max(vector_dims(re.embedding)),
                   min(vector_norm(re.embedding)), max(vector_norm(re.embedding)),
                   count(*) FILTER (WHERE re.embedding_text_sha256 <> ru.embedding_text_sha256),
                   count(*) FILTER (WHERE re.source_text_sha256 <> ru.text_sha256),
                   count(*) FILTER (WHERE ru.excluded_by_policy OR ru.eligibility_status <> 'eligible')
            FROM retrieval.retrieval_embedding re
            JOIN retrieval.retrieval_unit ru
              ON ru.corpus_snapshot_id=re.corpus_snapshot_id
             AND ru.retrieval_unit_id=re.retrieval_unit_id
            WHERE re.corpus_snapshot_id=%s AND re.model=%s
            """,
            (snapshot_id, model),
        )
        row = cursor.fetchone()
        measurements.update(
            {
                "count": int(row[0]),
                "min_dimension": int(row[1]) if row[1] is not None else None,
                "max_dimension": int(row[2]) if row[2] is not None else None,
                "min_l2_norm": float(row[3]) if row[3] is not None else None,
                "max_l2_norm": float(row[4]) if row[4] is not None else None,
                "embedding_text_hash_mismatches": int(row[5]),
                "source_text_hash_mismatches": int(row[6]),
                "ineligible_or_excluded_embeddings": int(row[7]),
            }
        )
        checks["complete_count"] = measurements["count"] == snapshot["retrieval_unit_count"]
        checks["dimension_1536"] = (
            measurements["min_dimension"] == EMBEDDING_DIMENSION
            and measurements["max_dimension"] == EMBEDDING_DIMENSION
        )
        checks["normalised_vectors"] = (
            measurements["min_l2_norm"] is not None
            and measurements["max_l2_norm"] is not None
            and 0.99 <= measurements["min_l2_norm"] <= 1.01
            and 0.99 <= measurements["max_l2_norm"] <= 1.01
        )
        checks["embedding_text_hashes"] = measurements["embedding_text_hash_mismatches"] == 0
        checks["source_text_hashes"] = measurements["source_text_hash_mismatches"] == 0
        checks["no_policy_leakage"] = measurements["ineligible_or_excluded_embeddings"] == 0
        cursor.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='retrieval' "
            "AND (indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%')"
        )
        measurements["approximate_vector_indexes"] = int(cursor.fetchone()[0])
        checks["exact_baseline_no_ann_index"] = measurements["approximate_vector_indexes"] == 0
    return {
        "schema_version": "embedding-validation-1.0.0",
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "corpus_snapshot_id": snapshot_id,
        "model": model,
        "measurements": measurements,
        "checks": checks,
        "passed": all(checks.values()),
    }


def run_embedding_smoke(
    root: Path | None = None, *, provider: EmbeddingProvider | None = None
) -> dict[str, Any]:
    root = repository_root(root)
    snapshot = create_snapshot(root)
    units = _units_for_snapshot(root, snapshot)
    unit_by_id = {row["retrieval_unit_id"]: row for row in units}
    provider = provider or OpenAIEmbeddingProvider()
    if provider.dimension != EMBEDDING_DIMENSION:
        raise CorpusIntegrityError("baseline provider dimension must be 1536")
    with _embedding_process_lock(root):
        existing = _validated_checkpoint_records(
            root, snapshot["corpus_snapshot_id"], provider.model, provider.dimension, unit_by_id
        )
        selected = _select_smoke_units(units)
        missing = [row for row in selected if row["retrieval_unit_id"] not in existing]
        records = []
        if missing:
            records = _create_batch_checkpoint(
                root, snapshot["corpus_snapshot_id"], provider, missing,
                batch_id="smoke-000", subdirectory="smoke",
            )
        all_records = _validated_checkpoint_records(
            root, snapshot["corpus_snapshot_id"], provider.model, provider.dimension, unit_by_id
        )
        smoke_records = [all_records[row["retrieval_unit_id"]] for row in selected]
        first_import = import_embedding_records(
            root, snapshot["corpus_snapshot_id"], provider.model, smoke_records
        )
        second_import = import_embedding_records(
            root, snapshot["corpus_snapshot_id"], provider.model, smoke_records
        )
        checks = _similarity_self_check(
            root, snapshot["corpus_snapshot_id"], provider.model, smoke_records
        )
        summary = _embedding_summary(
            root, snapshot, provider, unit_by_id, mode="smoke",
            provider_calls_this_run=getattr(provider, "call_count", None), smoke_checks=checks,
        )
        summary.update(
            {
                "smoke_unit_ids": [row["retrieval_unit_id"] for row in selected],
                "new_records": len(records),
                "first_import": first_import,
                "second_import": second_import,
                "persistence_idempotent": second_import["inserted"] == 0,
                "passed": len(smoke_records) == 3
                and all(item["self_top_1"] for item in checks)
                and second_import["inserted"] == 0,
            }
        )
        path = _checkpoint_root(root, snapshot["corpus_snapshot_id"], provider.model) / "smoke_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary


def run_full_embeddings(
    root: Path | None = None,
    *,
    provider: EmbeddingProvider | None = None,
    batch_size: int = 64,
) -> dict[str, Any]:
    root = repository_root(root)
    snapshot = create_snapshot(root)
    units = _units_for_snapshot(root, snapshot)
    unit_by_id = {row["retrieval_unit_id"]: row for row in units}
    provider = provider or OpenAIEmbeddingProvider()
    if provider.model != EMBEDDING_MODEL or provider.dimension != EMBEDDING_DIMENSION:
        raise CorpusIntegrityError("full baseline requires text-embedding-3-small at 1536 dimensions")
    if not 1 <= batch_size <= 256:
        raise ValueError("batch_size must be between 1 and 256")
    with _embedding_process_lock(root):
        existing = _validated_checkpoint_records(
            root, snapshot["corpus_snapshot_id"], provider.model, provider.dimension, unit_by_id
        )
        pending = [row for row in units if row["retrieval_unit_id"] not in existing]
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset : offset + batch_size]
            batch_id = f"full-{offset // batch_size:05d}"
            records = _create_batch_checkpoint(
                root, snapshot["corpus_snapshot_id"], provider, batch,
                batch_id=batch_id, subdirectory="full",
            )
            import_embedding_records(root, snapshot["corpus_snapshot_id"], provider.model, records)
        all_records = _validated_checkpoint_records(
            root, snapshot["corpus_snapshot_id"], provider.model, provider.dimension, unit_by_id
        )
        import_embedding_records(
            root, snapshot["corpus_snapshot_id"], provider.model, list(all_records.values())
        )
        summary = _embedding_summary(
            root, snapshot, provider, unit_by_id, mode="full",
            provider_calls_this_run=getattr(provider, "call_count", None),
        )
        summary["resume_skipped_count"] = len(units) - len(pending)
        summary["new_embedding_count"] = len(pending)
        summary["complete"] = (
            summary["checkpointed_embedding_count"] == len(units)
            and summary["database_embedding_count"] == len(units)
        )
        base = _checkpoint_root(root, snapshot["corpus_snapshot_id"], provider.model)
        validation = validate_embedding_baseline(
            root, snapshot_id=snapshot["corpus_snapshot_id"], model=provider.model
        )
        summary["database_validation_passed"] = validation["passed"]
        (base / "embedding_validation.json").write_text(
            json.dumps(validation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        path = base / "full_report.json"
        path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not summary["complete"] or not validation["passed"]:
            raise CorpusIntegrityError("full embedding baseline is incomplete")
        return summary

CREATE INDEX IF NOT EXISTS retrieval_unit_item_number_idx
    ON retrieval.retrieval_unit (corpus_snapshot_id, source_native_item_number)
    WHERE source_native_item_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS retrieval_unit_source_route_idx
    ON retrieval.retrieval_unit (corpus_snapshot_id, source_role, source_status, document_component);

CREATE INDEX IF NOT EXISTS retrieval_unit_german_fts_idx
    ON retrieval.retrieval_unit USING gin (search_german);

CREATE INDEX IF NOT EXISTS retrieval_unit_simple_fts_idx
    ON retrieval.retrieval_unit USING gin (search_simple);

CREATE INDEX IF NOT EXISTS retrieval_unit_alias_trgm_idx
    ON retrieval.retrieval_unit USING gin (aliases_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS retrieval_unit_simple_trgm_idx
    ON retrieval.retrieval_unit USING gin (simple_search_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS retrieval_unit_product_ids_idx
    ON retrieval.retrieval_unit USING gin (product_ids);

CREATE INDEX IF NOT EXISTS retrieval_unit_substance_ids_idx
    ON retrieval.retrieval_unit USING gin (active_substance_ids);

CREATE INDEX IF NOT EXISTS semantic_relation_from_idx
    ON retrieval.semantic_relation (corpus_snapshot_id, from_retrieval_unit_id, relation_type);

CREATE INDEX IF NOT EXISTS semantic_relation_to_idx
    ON retrieval.semantic_relation (corpus_snapshot_id, to_retrieval_unit_id, relation_type);

-- Intentionally no HNSW or IVFFlat index: exact pgvector scan is the baseline.

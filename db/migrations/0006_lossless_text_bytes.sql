-- PostgreSQL text/jsonb reject U+0000. The legacy extractor retained those
-- source bytes in a small number of public-document records. Searchable text
-- escapes U+0000 visibly; bytea retains exact UTF-8 for hashes/provenance.
ALTER TABLE retrieval.canonical_record
    ADD COLUMN IF NOT EXISTS exact_source_text_utf8 bytea,
    ADD COLUMN IF NOT EXISTS payload_utf8 bytea;

ALTER TABLE retrieval.evidence_span
    ADD COLUMN IF NOT EXISTS exact_source_text_utf8 bytea,
    ADD COLUMN IF NOT EXISTS exact_table_cell_text_utf8 bytea;

ALTER TABLE retrieval.retrieval_unit
    ADD COLUMN IF NOT EXISTS exact_source_text_utf8 bytea,
    ADD COLUMN IF NOT EXISTS retrieval_segment_text_utf8 bytea,
    ADD COLUMN IF NOT EXISTS retrieval_text_utf8 bytea,
    ADD COLUMN IF NOT EXISTS embedding_text_utf8 bytea,
    ADD COLUMN IF NOT EXISTS exact_table_cell_text_utf8 bytea;

ALTER TABLE retrieval.formal_item
    ADD COLUMN IF NOT EXISTS exact_text_utf8 bytea,
    ADD COLUMN IF NOT EXISTS payload_utf8 bytea;

ALTER TABLE retrieval.medicine_product
    ADD COLUMN IF NOT EXISTS payload_utf8 bytea;

ALTER TABLE retrieval.active_substance
    ADD COLUMN IF NOT EXISTS payload_utf8 bytea;

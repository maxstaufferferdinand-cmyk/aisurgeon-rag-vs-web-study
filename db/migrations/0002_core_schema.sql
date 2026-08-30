CREATE TABLE IF NOT EXISTS retrieval.corpus_snapshot (
    corpus_snapshot_id text PRIMARY KEY,
    content_fingerprint_sha256 text NOT NULL UNIQUE CHECK (content_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
    schema_version text NOT NULL,
    extraction_pipeline_version jsonb NOT NULL,
    retrieval_pipeline_version text NOT NULL,
    created_at timestamptz NOT NULL,
    previous_corpus_snapshot_id text NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    snapshot_status text NOT NULL CHECK (snapshot_status IN ('building', 'sealed', 'invalid')),
    manifest jsonb NOT NULL,
    sealed_at timestamptz NULL
);

CREATE TABLE IF NOT EXISTS retrieval.source_document (
    source_document_id text PRIMARY KEY,
    title text NOT NULL,
    document_kind text NOT NULL,
    source_authority text NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval.source_version (
    source_version_id text PRIMARY KEY,
    source_document_id text NOT NULL REFERENCES retrieval.source_document(source_document_id),
    source_file_name text NOT NULL,
    relative_path text NOT NULL,
    source_status text NOT NULL,
    source_role text NOT NULL,
    source_authority text NOT NULL,
    version_label text NULL,
    published_at text NULL,
    valid_from text NULL,
    valid_to text NULL,
    source_sha256 text NOT NULL UNIQUE CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    page_count integer NOT NULL CHECK (page_count > 0),
    file_size_bytes bigint NOT NULL CHECK (file_size_bytes > 0),
    component_ranges jsonb NOT NULL,
    qa_status text NOT NULL CHECK (qa_status IN ('validated', 'review', 'rejected')),
    qa_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    extraction_pipeline_version text NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval.corpus_snapshot_source (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    source_version_id text NOT NULL REFERENCES retrieval.source_version(source_version_id),
    PRIMARY KEY (corpus_snapshot_id, source_version_id)
);

CREATE TABLE IF NOT EXISTS retrieval.corpus_artifact (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    relative_path text NOT NULL,
    artifact_kind text NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    row_count bigint NULL CHECK (row_count IS NULL OR row_count >= 0),
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    PRIMARY KEY (corpus_snapshot_id, relative_path)
);

CREATE TABLE IF NOT EXISTS retrieval.canonical_record (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    record_id text NOT NULL,
    record_type text NOT NULL,
    source_version_id text NOT NULL REFERENCES retrieval.source_version(source_version_id),
    exact_source_text text NOT NULL,
    text_sha256 text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    pdf_pages_1based integer[] NOT NULL CHECK (cardinality(pdf_pages_1based) > 0),
    printed_page_label text NULL,
    eligibility_status text NOT NULL CHECK (eligibility_status IN ('eligible', 'ineligible', 'review')),
    excluded_by_policy boolean NOT NULL DEFAULT false,
    exclusion_reason text NULL,
    qa_status text NOT NULL CHECK (qa_status IN ('validated', 'review', 'rejected')),
    qa_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload jsonb NOT NULL,
    PRIMARY KEY (corpus_snapshot_id, record_id)
);

CREATE TABLE IF NOT EXISTS retrieval.evidence_span (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    evidence_span_id text NOT NULL,
    retrieval_unit_id text NULL,
    canonical_record_id text NOT NULL,
    source_version_id text NOT NULL REFERENCES retrieval.source_version(source_version_id),
    exact_source_text text NOT NULL,
    text_sha256 text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    pdf_page_index integer NULL CHECK (pdf_page_index IS NULL OR pdf_page_index >= 0),
    pdf_pages_1based integer[] NOT NULL CHECK (cardinality(pdf_pages_1based) > 0),
    printed_page_label text NULL,
    table_id text NULL,
    row_header_path text[] NULL,
    column_header_path text[] NULL,
    exact_table_cell_text text NULL,
    qa_status text NOT NULL CHECK (qa_status IN ('validated', 'review', 'rejected')),
    qa_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    eligibility_status text NOT NULL CHECK (eligibility_status IN ('eligible', 'ineligible', 'review')),
    excluded_by_policy boolean NOT NULL DEFAULT false,
    exclusion_reason text NULL,
    PRIMARY KEY (corpus_snapshot_id, evidence_span_id),
    FOREIGN KEY (corpus_snapshot_id, canonical_record_id)
        REFERENCES retrieval.canonical_record(corpus_snapshot_id, record_id)
);

CREATE TABLE IF NOT EXISTS retrieval.retrieval_unit (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    retrieval_unit_id text NOT NULL,
    evidence_span_id text NOT NULL,
    source_version_id text NOT NULL REFERENCES retrieval.source_version(source_version_id),
    source_document_id text NOT NULL REFERENCES retrieval.source_document(source_document_id),
    document_kind text NOT NULL,
    source_status text NOT NULL,
    document_component text NOT NULL,
    source_role text NOT NULL,
    source_authority text NOT NULL,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    text_sha256 text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    exact_source_text text NOT NULL,
    retrieval_segment_text text NOT NULL,
    retrieval_segment_sha256 text NOT NULL CHECK (retrieval_segment_sha256 ~ '^[0-9a-f]{64}$'),
    retrieval_text text NOT NULL,
    embedding_text text NOT NULL,
    embedding_text_sha256 text NOT NULL CHECK (embedding_text_sha256 ~ '^[0-9a-f]{64}$'),
    chapter_path text[] NOT NULL DEFAULT '{}',
    source_native_item_type text NULL,
    source_native_item_number text NULL,
    printed_source_item_number text NULL,
    pdf_page_index integer NULL CHECK (pdf_page_index IS NULL OR pdf_page_index >= 0),
    pdf_pages_1based integer[] NOT NULL CHECK (cardinality(pdf_pages_1based) > 0),
    printed_page_label text NULL,
    table_id text NULL,
    row_header_path text[] NULL,
    column_header_path text[] NULL,
    exact_table_cell_text text NULL,
    product_ids text[] NOT NULL DEFAULT '{}',
    active_substance_ids text[] NOT NULL DEFAULT '{}',
    product_names text[] NOT NULL DEFAULT '{}',
    active_substance_names text[] NOT NULL DEFAULT '{}',
    strength text NULL,
    pharmaceutical_form text NULL,
    route text NULL,
    dose_value text NULL,
    dose_unit text NULL,
    frequency text NULL,
    population text NULL,
    aliases text[] NOT NULL DEFAULT '{}',
    aliases_text text NOT NULL DEFAULT '',
    simple_search_text text NOT NULL DEFAULT '',
    parent_id text NOT NULL,
    parent_record_ids text[] NOT NULL CHECK (cardinality(parent_record_ids) > 0),
    relation_ids text[] NOT NULL DEFAULT '{}',
    qa_status text NOT NULL CHECK (qa_status IN ('validated', 'review', 'rejected')),
    qa_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    eligibility_status text NOT NULL CHECK (eligibility_status IN ('eligible', 'ineligible', 'review')),
    retrieval_eligible boolean NOT NULL,
    embedding_eligible boolean NOT NULL,
    answer_eligible boolean NOT NULL,
    primary_search_eligible boolean NOT NULL,
    excluded_by_policy boolean NOT NULL DEFAULT false,
    exclusion_reason text NULL,
    conflict_status text NOT NULL CHECK (conflict_status IN ('none', 'guideline_vs_smpc', 'within_guideline', 'version_conflict')),
    citation_label text NOT NULL,
    source_file_name text NOT NULL,
    extraction_batch_id text NOT NULL,
    extraction_pipeline_version text NOT NULL,
    raw_v1 jsonb NOT NULL,
    search_german tsvector GENERATED ALWAYS AS (
        to_tsvector('german', coalesce(retrieval_text, '') || ' ' || coalesce(retrieval_segment_text, ''))
    ) STORED,
    search_simple tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(simple_search_text, '') || ' ' || coalesce(aliases_text, ''))
    ) STORED,
    PRIMARY KEY (corpus_snapshot_id, retrieval_unit_id),
    FOREIGN KEY (corpus_snapshot_id, evidence_span_id)
        REFERENCES retrieval.evidence_span(corpus_snapshot_id, evidence_span_id)
);

CREATE TABLE IF NOT EXISTS retrieval.formal_item (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    formal_item_id text NOT NULL,
    record_id text NOT NULL,
    source_version_id text NOT NULL REFERENCES retrieval.source_version(source_version_id),
    source_native_item_type text NOT NULL,
    source_native_item_number text NULL,
    printed_source_item_number text NULL,
    exact_text text NOT NULL,
    recommendation_grade text NULL,
    evidence_level text NULL,
    consensus_strength text NULL,
    chapter_path text[] NOT NULL DEFAULT '{}',
    pdf_pages_1based integer[] NOT NULL,
    qa_status text NOT NULL,
    eligibility_status text NOT NULL,
    excluded_by_policy boolean NOT NULL DEFAULT false,
    exclusion_reason text NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (corpus_snapshot_id, formal_item_id),
    FOREIGN KEY (corpus_snapshot_id, record_id)
        REFERENCES retrieval.canonical_record(corpus_snapshot_id, record_id)
);

CREATE TABLE IF NOT EXISTS retrieval.medicine_product (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    medicine_product_id text NOT NULL,
    preferred_name text NOT NULL,
    aliases text[] NOT NULL DEFAULT '{}',
    active_substance_ids text[] NOT NULL DEFAULT '{}',
    strength text NULL,
    pharmaceutical_form text NULL,
    route text NULL,
    source_version_id text NOT NULL REFERENCES retrieval.source_version(source_version_id),
    qa_status text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (corpus_snapshot_id, medicine_product_id)
);

CREATE TABLE IF NOT EXISTS retrieval.active_substance (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    active_substance_id text NOT NULL,
    preferred_name text NOT NULL,
    aliases text[] NOT NULL DEFAULT '{}',
    source_version_id text NOT NULL REFERENCES retrieval.source_version(source_version_id),
    qa_status text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (corpus_snapshot_id, active_substance_id)
);

CREATE TABLE IF NOT EXISTS retrieval.entity_reference (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    entity_reference_id text NOT NULL,
    entity_kind text NOT NULL CHECK (entity_kind IN ('medicine_product', 'active_substance')),
    resolution_status text NOT NULL CHECK (resolution_status IN ('resolved', 'unresolved')),
    resolved_entity_id text NULL,
    source_identifiers text[] NOT NULL DEFAULT '{}',
    qa_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (corpus_snapshot_id, entity_reference_id)
);

CREATE TABLE IF NOT EXISTS retrieval.semantic_relation (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    relation_id text NOT NULL,
    relation_type text NOT NULL,
    from_kind text NOT NULL,
    from_id text NOT NULL,
    to_kind text NOT NULL,
    to_id text NOT NULL,
    from_retrieval_unit_id text NULL,
    to_retrieval_unit_id text NULL,
    is_direct_evidence boolean NOT NULL DEFAULT false,
    qa_status text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (corpus_snapshot_id, relation_id),
    FOREIGN KEY (corpus_snapshot_id, from_retrieval_unit_id)
        REFERENCES retrieval.retrieval_unit(corpus_snapshot_id, retrieval_unit_id),
    FOREIGN KEY (corpus_snapshot_id, to_retrieval_unit_id)
        REFERENCES retrieval.retrieval_unit(corpus_snapshot_id, retrieval_unit_id)
);

CREATE TABLE IF NOT EXISTS retrieval.retrieval_embedding (
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    retrieval_unit_id text NOT NULL,
    model text NOT NULL,
    dimension integer NOT NULL CHECK (dimension = 1536),
    distance_metric text NOT NULL CHECK (distance_metric = 'cosine'),
    embedding vector(1536) NOT NULL,
    embedding_text_sha256 text NOT NULL CHECK (embedding_text_sha256 ~ '^[0-9a-f]{64}$'),
    source_text_sha256 text NOT NULL CHECK (source_text_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    batch_id text NOT NULL,
    checkpoint_id text NOT NULL,
    input_tokens integer NULL CHECK (input_tokens IS NULL OR input_tokens >= 0),
    api_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    estimated_cost_usd numeric(14, 8) NULL CHECK (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0),
    price_as_of date NULL,
    PRIMARY KEY (corpus_snapshot_id, retrieval_unit_id, model),
    FOREIGN KEY (corpus_snapshot_id, retrieval_unit_id)
        REFERENCES retrieval.retrieval_unit(corpus_snapshot_id, retrieval_unit_id),
    CHECK (vector_dims(embedding) = dimension)
);

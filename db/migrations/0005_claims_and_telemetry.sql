CREATE TABLE IF NOT EXISTS retrieval.retrieval_run (
    retrieval_run_id text PRIMARY KEY,
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    trace_id text NOT NULL UNIQUE,
    started_at timestamptz NOT NULL,
    completed_at timestamptz NULL,
    routing_mode text NOT NULL CHECK (routing_mode IN ('guideline_first', 'smpc_first', 'dual_source')),
    rrf_k integer NOT NULL CHECK (rrf_k > 0),
    retrieval_outcome text NULL CHECK (
        retrieval_outcome IS NULL OR retrieval_outcome IN (
            'evidence_found', 'retrieval_failure', 'no_evidence_in_snapshot'
        )
    ),
    channel_status jsonb NOT NULL DEFAULT '{}'::jsonb,
    config jsonb NOT NULL,
    error_status jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS retrieval.retrieval_candidate (
    retrieval_run_id text NOT NULL REFERENCES retrieval.retrieval_run(retrieval_run_id),
    retrieval_unit_id text NOT NULL,
    channel text NOT NULL,
    channel_rank integer NOT NULL CHECK (channel_rank > 0),
    raw_score double precision NULL,
    rrf_score double precision NULL,
    final_rank integer NULL CHECK (final_rank IS NULL OR final_rank > 0),
    evidence_role text NOT NULL CHECK (evidence_role IN ('direct', 'linked_context')),
    PRIMARY KEY (retrieval_run_id, retrieval_unit_id, channel)
);

CREATE TABLE IF NOT EXISTS retrieval.evidence_package (
    evidence_package_id text PRIMARY KEY,
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    retrieval_run_id text NULL REFERENCES retrieval.retrieval_run(retrieval_run_id),
    created_at timestamptz NOT NULL,
    allowlist_ids text[] NOT NULL,
    package_sha256 text NOT NULL CHECK (package_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS retrieval.answer_claim (
    answer_claim_id text PRIMARY KEY,
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    evidence_package_id text NOT NULL REFERENCES retrieval.evidence_package(evidence_package_id),
    claim_text_sha256 text NOT NULL CHECK (claim_text_sha256 ~ '^[0-9a-f]{64}$'),
    public_support_label text NOT NULL CHECK (
        public_support_label IN ('supported', 'partially_supported', 'no_validated_evidence')
    ),
    entailment_status text NOT NULL CHECK (
        entailment_status IN ('supported', 'partial', 'contradicted', 'insufficient')
    ),
    retrieval_outcome text NOT NULL CHECK (
        retrieval_outcome IN ('evidence_found', 'retrieval_failure', 'no_evidence_in_snapshot')
    ),
    conflict_status text NOT NULL CHECK (
        conflict_status IN ('none', 'guideline_vs_smpc', 'within_guideline', 'version_conflict')
    ),
    applicability_status text NOT NULL CHECK (
        applicability_status IN ('applicable', 'uncertain', 'not_applicable')
    ),
    validator_status text NOT NULL CHECK (validator_status IN ('accepted', 'downgraded', 'rejected')),
    validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval.claim_evidence (
    answer_claim_id text NOT NULL REFERENCES retrieval.answer_claim(answer_claim_id),
    corpus_snapshot_id text NOT NULL,
    retrieval_unit_id text NOT NULL,
    evidence_role text NOT NULL CHECK (evidence_role IN ('direct', 'linked_context')),
    entailment_status text NOT NULL CHECK (
        entailment_status IN ('supported', 'partial', 'contradicted', 'insufficient')
    ),
    PRIMARY KEY (answer_claim_id, retrieval_unit_id),
    FOREIGN KEY (corpus_snapshot_id, retrieval_unit_id)
        REFERENCES retrieval.retrieval_unit(corpus_snapshot_id, retrieval_unit_id)
);

CREATE TABLE IF NOT EXISTS retrieval.retrieval_trace (
    trace_id text PRIMARY KEY,
    corpus_snapshot_id text NOT NULL REFERENCES retrieval.corpus_snapshot(corpus_snapshot_id),
    schema_version text NOT NULL,
    prompt_version text NULL,
    model text NULL,
    embedding_model text NOT NULL,
    query_sha256 text NOT NULL CHECK (query_sha256 ~ '^[0-9a-f]{64}$'),
    query_text_redacted text NULL,
    answer_text_redacted text NULL,
    full_text_logging_opt_in boolean NOT NULL DEFAULT false,
    channel_candidates jsonb NOT NULL,
    rrf_result jsonb NOT NULL,
    sent_evidence_ids text[] NOT NULL DEFAULT '{}',
    token_usage jsonb NOT NULL,
    cost jsonb NOT NULL,
    latency_ms jsonb NOT NULL,
    retry_status jsonb NOT NULL,
    error_status jsonb NOT NULL,
    database_time_ms double precision NULL,
    local_infrastructure jsonb NOT NULL,
    validator_status jsonb NOT NULL,
    created_at timestamptz NOT NULL
);

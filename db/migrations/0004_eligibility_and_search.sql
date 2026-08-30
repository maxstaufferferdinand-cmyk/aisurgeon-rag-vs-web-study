CREATE OR REPLACE VIEW retrieval.eligible_retrieval_units
WITH (security_barrier = true) AS
SELECT ru.*
FROM retrieval.retrieval_unit AS ru
JOIN retrieval.corpus_snapshot AS cs
  ON cs.corpus_snapshot_id = ru.corpus_snapshot_id
JOIN retrieval.evidence_span AS es
  ON es.corpus_snapshot_id = ru.corpus_snapshot_id
 AND es.evidence_span_id = ru.evidence_span_id
WHERE cs.snapshot_status = 'sealed'
  AND ru.eligibility_status = 'eligible'
  AND ru.retrieval_eligible
  AND ru.embedding_eligible
  AND ru.answer_eligible
  AND ru.primary_search_eligible
  AND NOT ru.excluded_by_policy
  AND coalesce(ru.exclusion_reason, '') <> 'hcc_historical_change_table'
  AND es.eligibility_status = 'eligible'
  AND NOT es.excluded_by_policy
  AND coalesce(es.exclusion_reason, '') <> 'hcc_historical_change_table';

CREATE OR REPLACE FUNCTION retrieval.search_exact(
    p_snapshot_id text,
    p_query text,
    p_limit integer DEFAULT 20,
    p_source_role text DEFAULT NULL
)
RETURNS TABLE (retrieval_unit_id text, rank integer, score double precision, match_kind text)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT e.retrieval_unit_id,
           row_number() OVER (
               ORDER BY
                   CASE
                       WHEN e.source_native_item_number = p_query THEN 0
                       WHEN EXISTS (SELECT 1 FROM unnest(e.aliases) a WHERE lower(a) = lower(p_query)) THEN 1
                       WHEN lower(e.simple_search_text) = lower(p_query) THEN 2
                       ELSE 3
                   END,
                   e.retrieval_unit_id
           )::integer AS rank,
           CASE
               WHEN e.source_native_item_number = p_query THEN 1.0
               WHEN EXISTS (SELECT 1 FROM unnest(e.aliases) a WHERE lower(a) = lower(p_query)) THEN 0.95
               WHEN e.simple_search_text ILIKE '%' || p_query || '%' THEN 0.8
               ELSE 0.5
           END::double precision AS score,
           CASE
               WHEN e.source_native_item_number = p_query THEN 'item_number'
               WHEN EXISTS (SELECT 1 FROM unnest(e.aliases) a WHERE lower(a) = lower(p_query)) THEN 'alias'
               ELSE 'structured_text'
           END AS match_kind
    FROM retrieval.eligible_retrieval_units e
    WHERE e.corpus_snapshot_id = p_snapshot_id
      AND (p_source_role IS NULL OR e.source_role = p_source_role)
      AND (
          e.source_native_item_number = p_query
          OR EXISTS (SELECT 1 FROM unnest(e.aliases) a WHERE lower(a) = lower(p_query))
          OR e.simple_search_text ILIKE '%' || p_query || '%'
      )
    ORDER BY rank, e.retrieval_unit_id
    LIMIT greatest(0, least(p_limit, 1000));
$$;

CREATE OR REPLACE FUNCTION retrieval.search_lexical(
    p_snapshot_id text,
    p_query text,
    p_configuration text DEFAULT 'german',
    p_limit integer DEFAULT 30,
    p_source_role text DEFAULT NULL
)
RETURNS TABLE (retrieval_unit_id text, rank integer, score double precision, configuration text)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    WITH candidates AS (
        SELECT e.retrieval_unit_id,
               CASE
                   WHEN p_configuration = 'simple' THEN
                       ts_rank_cd(e.search_simple, websearch_to_tsquery('simple', p_query))
                   ELSE
                       ts_rank_cd(e.search_german, websearch_to_tsquery('german', p_query))
               END::double precision AS lexical_score
        FROM retrieval.eligible_retrieval_units e
        WHERE e.corpus_snapshot_id = p_snapshot_id
          AND (p_source_role IS NULL OR e.source_role = p_source_role)
          AND CASE
              WHEN p_configuration = 'simple' THEN e.search_simple @@ websearch_to_tsquery('simple', p_query)
              ELSE e.search_german @@ websearch_to_tsquery('german', p_query)
          END
    )
    SELECT c.retrieval_unit_id,
           row_number() OVER (ORDER BY c.lexical_score DESC, c.retrieval_unit_id)::integer,
           c.lexical_score,
           CASE WHEN p_configuration = 'simple' THEN 'simple' ELSE 'german' END
    FROM candidates c
    ORDER BY c.lexical_score DESC, c.retrieval_unit_id
    LIMIT greatest(0, least(p_limit, 1000));
$$;

CREATE OR REPLACE FUNCTION retrieval.search_trigram(
    p_snapshot_id text,
    p_query text,
    p_limit integer DEFAULT 20,
    p_threshold real DEFAULT 0.15,
    p_source_role text DEFAULT NULL
)
RETURNS TABLE (retrieval_unit_id text, rank integer, score double precision)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    WITH candidates AS (
        SELECT e.retrieval_unit_id,
               greatest(similarity(e.aliases_text, p_query), similarity(e.simple_search_text, p_query))::double precision AS s
        FROM retrieval.eligible_retrieval_units e
        WHERE e.corpus_snapshot_id = p_snapshot_id
          AND (p_source_role IS NULL OR e.source_role = p_source_role)
    )
    SELECT c.retrieval_unit_id,
           row_number() OVER (ORDER BY c.s DESC, c.retrieval_unit_id)::integer,
           c.s
    FROM candidates c
    WHERE c.s >= p_threshold
    ORDER BY c.s DESC, c.retrieval_unit_id
    LIMIT greatest(0, least(p_limit, 1000));
$$;

CREATE OR REPLACE FUNCTION retrieval.search_vector_exact(
    p_snapshot_id text,
    p_query vector(1536),
    p_model text DEFAULT 'text-embedding-3-small',
    p_limit integer DEFAULT 30,
    p_source_role text DEFAULT NULL
)
RETURNS TABLE (retrieval_unit_id text, rank integer, cosine_distance double precision)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT e.retrieval_unit_id,
           row_number() OVER (ORDER BY re.embedding <=> p_query, e.retrieval_unit_id)::integer,
           (re.embedding <=> p_query)::double precision
    FROM retrieval.eligible_retrieval_units e
    JOIN retrieval.retrieval_embedding re
      ON re.corpus_snapshot_id = e.corpus_snapshot_id
     AND re.retrieval_unit_id = e.retrieval_unit_id
    WHERE e.corpus_snapshot_id = p_snapshot_id
      AND re.model = p_model
      AND (p_source_role IS NULL OR e.source_role = p_source_role)
    ORDER BY re.embedding <=> p_query, e.retrieval_unit_id
    LIMIT greatest(0, least(p_limit, 1000));
$$;

CREATE OR REPLACE FUNCTION retrieval.expand_relations(
    p_snapshot_id text,
    p_seed_ids text[],
    p_limit integer DEFAULT 100
)
RETURNS TABLE (
    seed_retrieval_unit_id text,
    retrieval_unit_id text,
    relation_type text,
    evidence_role text
)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT rel.from_retrieval_unit_id,
           target.retrieval_unit_id,
           rel.relation_type,
           'linked_context'::text
    FROM retrieval.semantic_relation rel
    JOIN retrieval.eligible_retrieval_units target
      ON target.corpus_snapshot_id = rel.corpus_snapshot_id
     AND target.retrieval_unit_id = rel.to_retrieval_unit_id
    JOIN retrieval.eligible_retrieval_units seed
      ON seed.corpus_snapshot_id = rel.corpus_snapshot_id
     AND seed.retrieval_unit_id = rel.from_retrieval_unit_id
    WHERE rel.corpus_snapshot_id = p_snapshot_id
      AND rel.from_retrieval_unit_id = ANY(p_seed_ids)
    ORDER BY array_position(p_seed_ids, rel.from_retrieval_unit_id), rel.relation_id
    LIMIT greatest(0, least(p_limit, 1000));
$$;

CREATE OR REPLACE FUNCTION retrieval.evidence_package_rows(
    p_snapshot_id text,
    p_retrieval_unit_ids text[]
)
RETURNS SETOF retrieval.eligible_retrieval_units
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT e.*
    FROM retrieval.eligible_retrieval_units e
    WHERE e.corpus_snapshot_id = p_snapshot_id
      AND e.retrieval_unit_id = ANY(p_retrieval_unit_ids)
    ORDER BY array_position(p_retrieval_unit_ids, e.retrieval_unit_id);
$$;

-- Expand only already-selected seeds and only through eligible rows. Besides
-- explicit edges, medicinal safety context is derived conservatively from a
-- shared source product identifier; no entity mapping is invented here.
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
    WITH explicit_forward AS (
        SELECT rel.from_retrieval_unit_id AS seed_id,
               target.retrieval_unit_id AS target_id,
               rel.relation_type AS typed_relation,
               0 AS relation_priority
        FROM retrieval.semantic_relation rel
        JOIN retrieval.eligible_retrieval_units target
          ON target.corpus_snapshot_id = rel.corpus_snapshot_id
         AND target.retrieval_unit_id = rel.to_retrieval_unit_id
        JOIN retrieval.eligible_retrieval_units seed
          ON seed.corpus_snapshot_id = rel.corpus_snapshot_id
         AND seed.retrieval_unit_id = rel.from_retrieval_unit_id
        WHERE rel.corpus_snapshot_id = p_snapshot_id
          AND rel.from_retrieval_unit_id = ANY(p_seed_ids)
    ), table_parent_reverse AS (
        SELECT rel.to_retrieval_unit_id AS seed_id,
               parent.retrieval_unit_id AS target_id,
               'table_to_parent_context'::text AS typed_relation,
               1 AS relation_priority
        FROM retrieval.semantic_relation rel
        JOIN retrieval.eligible_retrieval_units parent
          ON parent.corpus_snapshot_id = rel.corpus_snapshot_id
         AND parent.retrieval_unit_id = rel.from_retrieval_unit_id
        JOIN retrieval.eligible_retrieval_units table_unit
          ON table_unit.corpus_snapshot_id = rel.corpus_snapshot_id
         AND table_unit.retrieval_unit_id = rel.to_retrieval_unit_id
        WHERE rel.corpus_snapshot_id = p_snapshot_id
          AND rel.relation_type = 'guideline_item_to_tables_figures'
          AND rel.to_retrieval_unit_id = ANY(p_seed_ids)
    ), medicinal_context AS (
        SELECT seed.retrieval_unit_id AS seed_id,
               target.retrieval_unit_id AS target_id,
               CASE target.source_native_item_type
                   WHEN 'dosing_rule' THEN 'medicine_to_dosing'
                   WHEN 'warning' THEN 'medicine_to_warning'
                   WHEN 'contraindication' THEN 'medicine_to_contraindication'
                   WHEN 'adverse_reaction' THEN 'medicine_to_adverse_reaction'
               END AS typed_relation,
               2 AS relation_priority
        FROM retrieval.eligible_retrieval_units seed
        JOIN retrieval.eligible_retrieval_units target
          ON target.corpus_snapshot_id = seed.corpus_snapshot_id
         AND target.retrieval_unit_id <> seed.retrieval_unit_id
         AND target.product_ids && seed.product_ids
        WHERE seed.corpus_snapshot_id = p_snapshot_id
          AND seed.retrieval_unit_id = ANY(p_seed_ids)
          AND cardinality(seed.product_ids) > 0
          AND target.source_native_item_type IN (
              'dosing_rule', 'warning', 'contraindication', 'adverse_reaction'
          )
    ), combined AS (
        SELECT * FROM explicit_forward
        UNION ALL
        SELECT * FROM table_parent_reverse
        UNION ALL
        SELECT * FROM medicinal_context
    ), deduplicated AS (
        SELECT seed_id, target_id, typed_relation, min(relation_priority) AS relation_priority
        FROM combined
        WHERE seed_id IS NOT NULL AND target_id IS NOT NULL AND typed_relation IS NOT NULL
        GROUP BY seed_id, target_id, typed_relation
    )
    SELECT seed_id, target_id, typed_relation, 'linked_context'::text
    FROM deduplicated
    ORDER BY array_position(p_seed_ids, seed_id), relation_priority, typed_relation, target_id
    LIMIT greatest(0, least(p_limit, 1000));
$$;

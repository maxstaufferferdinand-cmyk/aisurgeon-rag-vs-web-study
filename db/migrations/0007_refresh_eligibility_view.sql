-- Refresh SELECT ru.* after migration 0006 appended raw UTF-8 provenance
-- columns.  The predicates remain fail-closed and every normal search function
-- continues to depend on this security-barrier view.
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

"""Shared SQLite eligibility for operational payload cleanup and inspection."""

_OWNER_SEED = """
    SELECT DISTINCT number, term, term FROM analysis_memo
    WHERE (:cutoff IS NULL OR last_used_at < :cutoff) AND term IS NOT NULL AND number IS NOT NULL
"""

_OWNER_EDGES = """
    UNION
    SELECT o.number, o.origin, g.to_term FROM owners o
    JOIN government_owner_moves g ON g.number=o.number AND g.from_term=o.term
    UNION
    SELECT o.number, o.origin, g.from_term FROM owners o
    JOIN government_owner_moves g ON g.number=o.number AND g.to_term=o.term
"""

_PENDING_MEMO_KEYS = """
    SELECT 'intent', json_extract(meta_json,'$.memo_key') FROM llm_batch_intents
    UNION ALL
    SELECT 'intent', json_extract(meta_json,'$.delivery_key') FROM llm_batch_intents
    UNION ALL
    SELECT 'item', json_extract(meta_json,'$.memo_key') FROM llm_batch_items
    WHERE consumed_at IS NULL OR accounted_at IS NULL
    UNION ALL
    SELECT 'item', json_extract(meta_json,'$.delivery_key') FROM llm_batch_items
    WHERE consumed_at IS NULL OR accounted_at IS NULL
    UNION ALL
    SELECT 'review', json_extract(work.value,'$.memo_key')
    FROM bills b JOIN json_each(b.review_work_json) work
    WHERE work.type='object' AND json_type(work.value,'$.context')='object'
"""

_MEMO_ROWS = """
SELECT m.*,
  b.term IS NOT NULL AS has_bill,
  b.status IN ('analyzed', 'linked', 'skipped_prefilter',
               'skipped_text_prefilter', 'skipped_closed') AS settled,
  b.awaiting_batch_since IS NOT NULL OR b.ready_analysis_json IS NOT NULL
    OR b.review_work_json IS NOT NULL AS bill_work,
  EXISTS (SELECT 1 FROM owners o JOIN document_work w ON (w.term,w.number)=(o.term,o.number)
    WHERE (o.origin,o.number)=(m.term,m.number) AND w.state!='complete') AS document_work,
  EXISTS (SELECT 1 FROM owners o JOIN publications p ON (p.term,p.number)=(o.term,o.number)
    WHERE (o.origin,o.number)=(m.term,m.number)
      AND p.status NOT IN ('sent','skipped')) AS publication,
  EXISTS (SELECT 1 FROM owners o JOIN llm_batch_intents i
    ON (json_extract(i.request_json,'$.term'),json_extract(i.request_json,'$.number'))
       =(o.term,o.number)
    WHERE (o.origin,o.number)=(m.term,m.number)) AS intent,
  EXISTS (SELECT 1 FROM owners o JOIN llm_batch_items i ON (i.term,i.number)=(o.term,o.number)
    WHERE (o.origin,o.number)=(m.term,m.number)
    AND (i.consumed_at IS NULL OR i.accounted_at IS NULL)) AS batch_item,
  COALESCE(m.key IN (SELECT key FROM pending_memo_keys WHERE source='intent'), 0) AS intent_key_ref,
  COALESCE(m.key IN (SELECT key FROM pending_memo_keys WHERE source='item'), 0)
    AS batch_item_key_ref,
  COALESCE(m.key IN (SELECT key FROM pending_memo_keys WHERE source='review'), 0) AS review_key_ref
FROM analysis_memo m
LEFT JOIN terminal t ON (t.origin,t.number)=(m.term,m.number)
LEFT JOIN bills b ON (b.term,b.number)=(t.term,t.number)
"""


def _owner_chain_cte(seed: str, *, owner_moves: bool) -> str:
    edges = _OWNER_EDGES if owner_moves else ""
    terminal = (
        "AND NOT EXISTS (SELECT 1 FROM government_owner_moves g"
        " WHERE (g.number,g.from_term)=(o.number,o.term))"
        if owner_moves
        else ""
    )
    return f"""
    WITH RECURSIVE owners(number, origin, term) AS ({seed}{edges}),
    terminal AS (
      SELECT o.* FROM owners o JOIN bills b ON (b.term,b.number)=(o.term,o.number)
      WHERE NOT EXISTS (SELECT 1 FROM owners other JOIN bills live
          ON (live.term,live.number)=(other.term,other.number)
          WHERE (other.origin,other.number)=(o.origin,o.number) AND other.term!=o.term)
      {terminal}
    )
    """


def memo_retention_cte(*, owner_moves: bool) -> str:
    return _owner_chain_cte(_OWNER_SEED, owner_moves=owner_moves) + (
        f", pending_memo_keys(source, key) AS ({_PENDING_MEMO_KEYS})"
        f", memo_retention AS ({_MEMO_ROWS})"
    )


_BATCH_CLEANUP_GATES = (
    ("consumed", "i.consumed_at IS NOT NULL"),
    ("accounted", "i.accounted_at IS NOT NULL"),
    (
        "provider_forgotten",
        "i.batch_id IN (SELECT batch_id FROM llm_batches WHERE forgotten_at IS NOT NULL)",
    ),
    (
        "settled_owner",
        """
  EXISTS (
    SELECT 1 FROM bills b
    WHERE b.number=i.number
      AND (b.term=i.term OR (
        i.call_kind='name'
        AND EXISTS (
          SELECT 1 FROM terminal t
          WHERE (t.origin,t.number,t.term)=(i.term,i.number,b.term) AND t.term>t.origin
        )
      ))
      AND b.status IN ('analyzed', 'linked', 'skipped_prefilter',
                       'skipped_text_prefilter', 'skipped_closed')
      AND b.awaiting_batch_since IS NULL AND b.ready_analysis_json IS NULL
  )
        """,
    ),
)

_BATCH_PAYLOAD = """
length(json_extract(meta_json, '$.text')) > 0
   OR json_type(meta_json, '$.located') = 'object'
   OR json_type(meta_json, '$.review_context') = 'object'
   OR length(json_extract(meta_json, '$.explanation.text')) > 0
   OR json_type(meta_json, '$.explanation.located') = 'object'
   OR json_type(result_json, '$.answer') = 'object'
   OR json_type(result_json, '$.analysis') = 'object'
"""


def batch_payload_cleanup_filter() -> str:
    return " AND ".join(f"({predicate})" for _, predicate in _BATCH_CLEANUP_GATES) + (
        f" AND ({_BATCH_PAYLOAD})"
    )


def batch_payload_retention_query() -> str:
    flags = ", ".join(f"({predicate}) AS {name}" for name, predicate in _BATCH_CLEANUP_GATES)
    return batch_retention_cte() + (
        f"SELECT i.call_kind, {flags} FROM llm_batch_items i WHERE ({_BATCH_PAYLOAD})"
    )


def batch_retention_cte() -> str:
    return _owner_chain_cte(
        """
        SELECT DISTINCT i.number, i.term, i.term FROM llm_batch_items i
        WHERE i.call_kind='name' AND i.consumed_at IS NOT NULL AND i.accounted_at IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM bills b WHERE (b.term,b.number)=(i.term,i.number))
        """,
        owner_moves=True,
    )

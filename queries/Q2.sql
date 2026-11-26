-- Only reachable accounts are returned, not paths. Any bounded walk to a
-- non-seed account contains a simple path no longer than that walk.
-- UNION deduplicates (account, depth), including parallel transfers.
WITH RECURSIVE walk(dst, depth) AS (
  SELECT %(seed)s::bigint, 0
  UNION
  SELECT t.dst_key, w.depth + 1
  FROM walk w JOIN transfers t ON t.src_key = w.dst
  WHERE w.depth < %(k)s
    AND t.ts >= %(t0)s
    AND t.ts < %(t1)s
    AND t.dst_key <> %(seed)s
    AND t.dst_key <> t.src_key
)
SELECT DISTINCT dst
FROM walk
WHERE depth > 0

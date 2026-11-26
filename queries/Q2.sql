WITH RECURSIVE walk(dst, path, depth) AS (
  SELECT %(seed)s::bigint, ARRAY[%(seed)s::bigint], 0
  UNION ALL
  SELECT t.dst_key, w.path || t.dst_key, w.depth + 1
  FROM walk w JOIN transfers t ON t.src_key = w.dst
  WHERE w.depth < %(k)s AND NOT t.dst_key = ANY(w.path)
    AND t.ts >= %(t0)s AND t.ts < %(t1)s
)
SELECT DISTINCT dst FROM walk WHERE depth > 0

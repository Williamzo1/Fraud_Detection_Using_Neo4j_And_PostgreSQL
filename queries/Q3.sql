-- Each alias can use its own index access; keep time predicates local to it.
-- Do not DISTINCT: parallel transfers are different transaction combinations.
SELECT x.txn_id, y.txn_id, z.txn_id
FROM transfers x
JOIN transfers y
  ON y.src_key = x.dst_key
 AND y.ts >= %(t0)s
 AND y.ts < %(t1)s
 AND y.dst_key > x.src_key
 AND y.dst_key <> x.dst_key
JOIN transfers z
  ON z.src_key = y.dst_key
 AND z.dst_key = x.src_key
 AND z.ts >= %(t0)s
 AND z.ts < %(t1)s
WHERE x.src_key < x.dst_key
  AND x.ts >= %(t0)s
  AND x.ts < %(t1)s

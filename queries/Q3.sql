SELECT x.txn_id, y.txn_id, z.txn_id
FROM transfers x JOIN transfers y ON x.dst_key = y.src_key
JOIN transfers z ON z.src_key = y.dst_key AND z.dst_key = x.src_key
WHERE x.src_key < x.dst_key AND x.src_key < y.dst_key AND x.dst_key <> y.dst_key
  AND x.ts >= %(t0)s AND x.ts < %(t1)s
  AND y.ts >= %(t0)s AND y.ts < %(t1)s
  AND z.ts >= %(t0)s AND z.ts < %(t1)s

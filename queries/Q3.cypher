// Filter each relationship independently, before joining the cycle branches.
// The smallest account key removes rotations but preserves direction and
// every combination of parallel transfers.
MATCH (a:Account)-[x:TRANSFER]->(b:Account)-[y:TRANSFER]->(c:Account)-[z:TRANSFER]->(a)
WHERE x.ts >= localdatetime($t0)
  AND x.ts < localdatetime($t1)
  AND y.ts >= localdatetime($t0)
  AND y.ts < localdatetime($t1)
  AND z.ts >= localdatetime($t0)
  AND z.ts < localdatetime($t1)
  AND a.acc_key < b.acc_key
  AND a.acc_key < c.acc_key
  AND b <> c
RETURN x.txn_id, y.txn_id, z.txn_id

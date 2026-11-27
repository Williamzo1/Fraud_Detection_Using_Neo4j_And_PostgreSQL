// Filter each relationship independently, before joining the cycle branches.
// The smallest account key removes rotations but preserves direction and
// every combination of parallel transfers.
// Omit redundant :Account labels (every node in the graph is an Account,
// matching Q3.sql scanning transfers without joining accounts).
WITH localdatetime($t0) AS t0, localdatetime($t1) AS t1
MATCH (a)-[x:TRANSFER]->(b)-[y:TRANSFER]->(c)-[z:TRANSFER]->(a)
USING INDEX x:TRANSFER(ts)
WHERE x.ts >= t0
  AND x.ts < t1
  AND y.ts >= t0
  AND y.ts < t1
  AND z.ts >= t0
  AND z.ts < t1
  AND a.acc_key < b.acc_key
  AND a.acc_key < c.acc_key
  AND b <> c
RETURN x.txn_id, y.txn_id, z.txn_id

MATCH (a:Account)-[x:TRANSFER]->(b:Account)-[y:TRANSFER]->(c:Account)-[z:TRANSFER]->(a)
WHERE a.acc_key < b.acc_key AND a.acc_key < c.acc_key AND b <> c
  AND all(r IN [x,y,z] WHERE r.ts >= localdatetime($t0) AND r.ts < localdatetime($t1))
RETURN x.txn_id, y.txn_id, z.txn_id

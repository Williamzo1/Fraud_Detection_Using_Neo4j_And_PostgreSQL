MATCH p=(a:Account {acc_key: $seed})-[:TRANSFER*1..{K}]->(b:Account)
WHERE all(r IN relationships(p) WHERE r.ts >= localdatetime($t0) AND r.ts < localdatetime($t1))
  AND all(n IN nodes(p) WHERE single(m IN nodes(p) WHERE m = n))
RETURN DISTINCT b.acc_key

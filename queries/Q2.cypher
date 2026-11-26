// Only endpoint reachability matters. Removing cycles from a walk cannot
// increase its length, so excluding the seed preserves the simple-path result.
// Avoid materializing paths so the planner can use BFS pruning expansion.
MATCH (a:Account {acc_key: $seed})
MATCH (a)-[rs:TRANSFER*1..{K}]->(b:Account)
WHERE b <> a
  AND all(r IN rs WHERE r.ts >= localdatetime($t0)
                       AND r.ts < localdatetime($t1))
RETURN DISTINCT b.acc_key

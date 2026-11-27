// Only endpoint reachability matters. Removing cycles from a walk cannot
// increase its length, so excluding the seed preserves the simple-path result.
// Use a quantified path pattern so time filtering is pushed into each hop.
WITH localdatetime($t0) AS t0, localdatetime($t1) AS t1
MATCH (a:Account {acc_key: $seed})
MATCH (a) (()-[r:TRANSFER WHERE r.ts >= t0 AND r.ts < t1]->(:Account)){1,{K}} (b:Account)
WHERE b <> a
RETURN DISTINCT b.acc_key

MATCH ()-[r:TRANSFER]->()
WHERE r.ts >= localdatetime($t0) AND r.ts < localdatetime($t1)
  AND r.currency = $currency AND r.amount_paid > $threshold
RETURN r.txn_id

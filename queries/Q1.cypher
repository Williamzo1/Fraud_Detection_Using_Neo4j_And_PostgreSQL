// Equality on the leading composite-index property, then the time range.
WITH localdatetime($t0) AS t0, localdatetime($t1) AS t1
MATCH ()-[r:TRANSFER {currency: $currency}]->()
WHERE r.ts >= t0
  AND r.ts < t1
  AND r.amount_paid > $threshold
RETURN r.txn_id

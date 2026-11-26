// Equality on the leading composite-index property, then the time range.
MATCH ()-[r:TRANSFER {currency: $currency}]->()
WHERE r.ts >= localdatetime($t0)
  AND r.ts < localdatetime($t1)
  AND r.amount_paid > $threshold
RETURN r.txn_id

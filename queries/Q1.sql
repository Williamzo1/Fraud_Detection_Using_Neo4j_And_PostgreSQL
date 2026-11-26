-- Keep predicates on the indexed columns; do not cast/transform ts or amount_paid.
SELECT txn_id
FROM transfers
WHERE currency = %(currency)s
  AND ts >= %(t0)s
  AND ts < %(t1)s
  AND amount_paid > %(threshold)s

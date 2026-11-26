SELECT txn_id FROM transfers
WHERE ts >= %(t0)s AND ts < %(t1)s AND currency = %(currency)s AND amount_paid > %(threshold)s

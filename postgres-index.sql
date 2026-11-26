\set ON_ERROR_STOP on
BEGIN;
ALTER TABLE accounts ADD PRIMARY KEY (acc_key), ADD UNIQUE (bank_id, account_id);
ALTER TABLE transfers ADD PRIMARY KEY (txn_id);
ALTER TABLE transfers
    ADD FOREIGN KEY (src_key) REFERENCES accounts(acc_key),
    ADD FOREIGN KEY (dst_key) REFERENCES accounts(acc_key);
CREATE INDEX transfers_src_ts ON transfers (src_key, ts);
CREATE INDEX transfers_currency_ts_amt ON transfers (currency, ts, amount_paid);
ANALYZE accounts;
ANALYZE transfers;
COMMIT;

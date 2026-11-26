\set ON_ERROR_STOP on
BEGIN;
ALTER TABLE accounts ADD PRIMARY KEY (acc_key), ADD UNIQUE (bank_id, account_id);
ALTER TABLE transfers ADD PRIMARY KEY (txn_id);
ALTER TABLE transfers
    ADD FOREIGN KEY (src_key) REFERENCES accounts(acc_key),
    ADD FOREIGN KEY (dst_key) REFERENCES accounts(acc_key);
-- Q2 expansion and Q3's second hop; INCLUDE also covers returned edge data.
CREATE INDEX transfers_src_ts ON transfers (src_key, ts) INCLUDE (dst_key, txn_id);
-- Q1: equality before range predicates, with txn_id available in the index.
CREATE INDEX transfers_currency_ts_amt ON transfers (currency, ts, amount_paid) INCLUDE (txn_id);
-- Q3: scan only canonical first edges in the requested time window.
CREATE INDEX transfers_cycle_start ON transfers (ts, src_key, dst_key) INCLUDE (txn_id)
    WHERE src_key < dst_key;
-- Q3: seek a closing edge by both endpoints before applying its time range.
CREATE INDEX transfers_cycle_close ON transfers (src_key, dst_key, ts) INCLUDE (txn_id);
COMMIT;
-- COPY produced a read-only benchmark dataset. Populate the visibility map
-- so covering indexes can actually use index-only scans.
-- Disable parallel VACUUM so Docker's default 64 MiB /dev/shm is sufficient.
VACUUM (ANALYZE, PARALLEL 0) accounts;
VACUUM (ANALYZE, PARALLEL 0) transfers;

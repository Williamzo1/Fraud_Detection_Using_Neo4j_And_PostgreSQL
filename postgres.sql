\set ON_ERROR_STOP on
BEGIN;
CREATE TABLE accounts (
    acc_key bigint,
    bank_id text NOT NULL,
    account_id text NOT NULL,
    bank_name text,
    entity_id text,
    entity_name text
);
CREATE TABLE transfers (
    src_key bigint NOT NULL,
    dst_key bigint NOT NULL,
    txn_id text,
    ts timestamp NOT NULL,
    amount_paid double precision NOT NULL,
    currency text NOT NULL,
    amount_received double precision NOT NULL,
    recv_currency text NOT NULL,
    payment_format text NOT NULL,
    is_laundering boolean NOT NULL
);
\copy accounts FROM '/aml-data/accounts.csv' WITH (FORMAT csv, HEADER true)
\copy transfers FROM '/aml-data/transfers.csv' WITH (FORMAT csv, HEADER true)
COMMIT;

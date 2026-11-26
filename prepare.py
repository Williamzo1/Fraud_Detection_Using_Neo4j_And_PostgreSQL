import argparse
import csv
import json
import hashlib
import tempfile
from datetime import datetime
import math
import re
from pathlib import Path

ACCOUNT_HEADER = "bank_name bank_id account_id entity_id entity_name".split()
ACCOUNT_FIELDS = "acc_key bank_id account_id bank_name entity_id entity_name".split()
TRANSFER_HEADER = (
    "ts src_bank src_account dst_bank dst_account amount_received "
    "receiving_currency amount_paid payment_currency payment_format is_laundering"
).split()
FIELDS = "txn_id ts amount_paid currency amount_received recv_currency payment_format is_laundering".split()
TYPES = "string localdatetime double string double string string boolean".split()


AMOUNT_LITERAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
AMOUNT_FORMAT = "source-unit-binary64"


def normalize_bank_id(bank):
    if bank.isascii() and bank.isdigit():
        return bank.lstrip("0") or "0"
    return bank


def money(value):
    if not AMOUNT_LITERAL.fullmatch(value) or not math.isfinite(float(value)):
        raise ValueError(f"Invalid finite amount: {value!r}")
    return value


def check_amount_format(manifest):
    if manifest.get("amount_format") != AMOUNT_FORMAT:
        raise ValueError("Re-run prepare.py into a new directory to use source currency units.")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare(account_source, transfer_source, output):
    if output.exists():
        raise FileExistsError(output)
    accounts_by_id = {}
    labels = {"true": 0, "false": 0}

    count = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        stage = Path(temporary) / "prepared"
        stage.mkdir()
        with (
            account_source.open(encoding="utf-8-sig", newline="") as account_input,
            transfer_source.open(encoding="utf-8-sig", newline="") as transfer_input,
            (stage / "accounts.csv").open("w", encoding="utf-8", newline="") as accounts,
            (stage / "nodes.csv").open("w", encoding="utf-8", newline="") as nodes,
            (stage / "transfers.csv").open("w", encoding="utf-8", newline="") as transfers,
            (stage / "edges.csv").open("w", encoding="utf-8", newline="") as edges,
        ):
            account_rows = csv.reader(account_input, strict=True)
            source_rows = csv.reader(transfer_input, strict=True)
            if next(account_rows, None) != ACCOUNT_HEADER:
                raise ValueError(f"{account_source}: expected columns {ACCOUNT_HEADER}")
            if next(source_rows, None) != TRANSFER_HEADER:
                raise ValueError(f"{transfer_source}: expected columns {TRANSFER_HEADER}")
            account_sql, account_graph, transfer_sql, transfer_graph = [
                csv.writer(file, lineterminator="\n")
                for file in (accounts, nodes, transfers, edges)
            ]
            account_sql.writerow(ACCOUNT_FIELDS)
            account_graph.writerow((":ID(Account)", "acc_key:long",
                                    *(f"{name}:string" for name in ACCOUNT_FIELDS[1:])))
            transfer_sql.writerow(("src_key", "dst_key", *FIELDS))
            transfer_graph.writerow((":START_ID(Account)", ":END_ID(Account)",
                                     *(f"{name}:{kind}" for name, kind in zip(FIELDS, TYPES))))

            for row in account_rows:
                try:
                    name, bank, account, entity_id, entity_name = (value.strip() for value in row)
                    if not bank or not account:
                        raise ValueError("Missing bank_id or account_id.")
                    identity = (bank, account)
                    metadata = (name, entity_id, entity_name)
                    existing = accounts_by_id.get(identity)
                    if existing is not None:
                        if existing[1:] != metadata:
                            raise ValueError(f"Conflicting metadata for account {identity!r}.")
                        continue
                    acc_key = len(accounts_by_id) + 1
                    accounts_by_id[identity] = (acc_key, *metadata)
                    record = (acc_key, bank, account, *metadata)
                    account_sql.writerow(record)
                    account_graph.writerow((acc_key, *record))
                except ValueError as error:
                    raise ValueError(f"{account_source}:{account_rows.line_num}: {error}") from error

            for count, row in enumerate(source_rows, 1):
                try:
                    ts, sb, sa, db, da, received, rc, paid, pc, fmt, label = (
                        field.strip() for field in row
                    )
                    if not all((sb, sa, db, da, rc, pc, fmt)) or label not in {"0", "1"}:
                        raise ValueError("Missing required field or invalid label.")
                    sb = normalize_bank_id(sb)
                    db = normalize_bank_id(db)
                    timestamp = datetime.strptime(ts, "%Y/%m/%d %H:%M").isoformat()
                    paid, received = money(paid), money(received)
                    src_key = accounts_by_id[(sb, sa)][0]
                    dst_key = accounts_by_id[(db, da)][0]
                except KeyError as error:
                    raise ValueError(
                        f"{transfer_source}:{source_rows.line_num}: "
                        f"account {error.args[0]!r} is missing from {account_source}"
                    ) from error
                except (ValueError, ArithmeticError) as error:
                    raise ValueError(f"{transfer_source}:{source_rows.line_num}: {error}") from error
                record = (src_key, dst_key, f"tx-{count:012d}", timestamp,
                          paid, pc, received, rc, fmt, "true" if label == "1" else "false")
                labels[record[-1]] += 1
                transfer_sql.writerow(record)
                transfer_graph.writerow(record)
        if count == 0:
            raise ValueError("No transactions found.")
        manifest = {"accounts": len(accounts_by_id), "transfers": count, "amount_format": AMOUNT_FORMAT, "amount_scale": 1}
        manifest.update(is_laundering=labels,
                        transaction_bank_id_normalization="strip-leading-zeros",
                        source_sha256={"accounts": sha256(account_source), "transfers": sha256(transfer_source)})
        (stage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        stage.rename(output)
        print(manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("accounts", type=Path, nargs="?", default=Path("HI-Small_accounts_renamed.csv"))
    parser.add_argument("transfers", type=Path, nargs="?", default=Path("HI-Small_Trans_renamed.csv"))
    parser.add_argument("output", type=Path, nargs="?", default=Path("prepared"))
    args = parser.parse_args()
    prepare(args.accounts, args.transfers, args.output)

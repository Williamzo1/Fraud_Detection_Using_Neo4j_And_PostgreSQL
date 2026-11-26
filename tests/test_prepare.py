import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from prepare import prepare


class TransactionBankNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.accounts = self.root / "accounts.csv"
        self.transfers = self.root / "transactions.csv"
        self.output = self.root / "prepared"

    def write_sources(self, accounts, transfers):
        with self.accounts.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["bank_name", "bank_id", "account_id", "entity_id", "entity_name"])
            for index, (bank, account) in enumerate(accounts, 1):
                writer.writerow([f"Bank {index}", bank, account, str(index), f"Entity {index}"])
        with self.transfers.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([
                "ts", "src_bank", "src_account", "dst_bank", "dst_account",
                "amount_received", "receiving_currency", "amount_paid",
                "payment_currency", "payment_format", "is_laundering",
            ])
            for src_bank, src_account, dst_bank, dst_account in transfers:
                writer.writerow([
                    "2022/09/01 00:20", src_bank, src_account, dst_bank, dst_account,
                    "10.2500", "USD", "10.2500", "USD", "Wire", "0",
                ])

    def run_prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(self.accounts, self.transfers, self.output)

    def read_output(self, name):
        with (self.output / name).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_normalizes_both_banks_and_preserves_account_numbers(self):
        self.write_sources(
            [("10", "000123"), ("3208", "000456"), ("0", "000789")],
            [
                ("010", "000123", "003208", "000456"),
                ("3208", "000456", "000", "000789"),
                ("00", "000789", "10", "000123"),
            ],
        )
        source_bytes = (self.accounts.read_bytes(), self.transfers.read_bytes())
        self.run_prepare()

        accounts = self.read_output("accounts.csv")
        self.assertEqual([row["bank_id"] for row in accounts], ["10", "3208", "0"])
        self.assertEqual([row["account_id"] for row in accounts], ["000123", "000456", "000789"])
        transfers = self.read_output("transfers.csv")
        edges = self.read_output("edges.csv")
        self.assertEqual(
            [(row["src_key"], row["dst_key"]) for row in transfers],
            [("1", "2"), ("2", "3"), ("3", "1")],
        )
        self.assertEqual(
            [(row[":START_ID(Account)"], row[":END_ID(Account)"]) for row in edges],
            [("1", "2"), ("2", "3"), ("3", "1")],
        )
        self.assertTrue(all(row["amount_paid"] == "10.2500" for row in transfers))
        self.assertEqual(source_bytes, (self.accounts.read_bytes(), self.transfers.read_bytes()))
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["transfers"], 3)
        self.assertEqual(manifest["transaction_bank_id_normalization"], "strip-leading-zeros")

    def test_preserves_bank_ids_that_are_not_ascii_numbers(self):
        self.write_sources(
            [("00ABC", "001"), ("０１", "002")],
            [("00ABC", "001", "０１", "002")],
        )
        self.run_prepare()
        self.assertEqual(
            [row["bank_id"] for row in self.read_output("accounts.csv")],
            ["00ABC", "０１"],
        )
        row, = self.read_output("transfers.csv")
        self.assertEqual((row["src_key"], row["dst_key"]), ("1", "2"))

    def test_same_account_number_at_different_banks_stays_distinct(self):
        self.write_sources(
            [("1", "001"), ("2", "001")],
            [("0001", "001", "0002", "001")],
        )
        self.run_prepare()
        self.assertEqual(
            [row["bank_id"] for row in self.read_output("accounts.csv")],
            ["1", "2"],
        )
        row, = self.read_output("transfers.csv")
        self.assertEqual((row["src_key"], row["dst_key"]), ("1", "2"))

    def test_normalization_does_not_hide_missing_accounts(self):
        self.write_sources(
            [("10", "001")],
            [("010", "001", "099", "001")],
        )
        with self.assertRaisesRegex(ValueError, r"transactions.csv:2: account \('99', '001'\) is missing"):
            self.run_prepare()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

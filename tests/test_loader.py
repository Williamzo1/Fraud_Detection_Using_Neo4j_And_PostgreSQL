import csv
from contextlib import ExitStack
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import db
import loader
from prepare import AMOUNT_FORMAT


class EnvironmentTests(unittest.TestCase):
    def test_postgres_connection_reads_unexported_dotenv_port(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("PG_PORT=15432\nPOSTGRES_PASSWORD=test-password\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch("db.psycopg.connect") as connect:
                db.load_environment(env_file)
                connection = db.Database("postgres", 1000)
                self.assertEqual(connect.call_args.kwargs["port"], "15432")
                self.assertEqual(connect.call_args.kwargs["password"], "test-password")
                connection.close()

    def test_exported_port_takes_precedence_over_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("PG_PORT=15432\n", encoding="utf-8")
            with patch.dict(os.environ, {"PG_PORT": "15433"}, clear=True):
                db.load_environment(env_file)
                self.assertEqual(os.environ["PG_PORT"], "15433")


class FinalizeImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source = root / "prepared"
        self.out = root / "recovery-test"
        self.source.mkdir()
        self.out.mkdir()
        self.counts = dict(accounts=2, transfers=3, is_laundering={"true": 1, "false": 2})
        self.manifest = dict(self.counts, amount_format=AMOUNT_FORMAT)
        (self.source / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        for name in ("accounts.csv", "transfers.csv"):
            (self.source / name).write_text("existing data\n", encoding="utf-8")
        self.write_timings(("bulk", "indexes"))
        self.indexes = [[name, "index definition"] for name in (
            "accounts_pkey", "accounts_bank_id_account_id_key", "transfers_pkey",
            "transfers_src_ts", "transfers_currency_ts_amt", "transfers_cycle_start",
            "transfers_cycle_close")]
        self.constraints = [(name, True) for name in (
            "accounts_pkey", "accounts_bank_id_account_id_key", "transfers_pkey",
            "transfers_src_key_fkey", "transfers_dst_key_fkey")]
        self.database = SimpleNamespace(pg=object(), counts=Mock(return_value=self.counts),
                                        rows=Mock(side_effect=self.rows), close=Mock())
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.compose = self.patches.enter_context(patch("loader.compose", return_value="123\tdata"))
        self.patches.enter_context(patch("loader.paths", return_value=(self.source, self.out)))
        self.patches.enter_context(patch("loader.Database", return_value=self.database))
        self.patches.enter_context(patch("loader.service_info", return_value={"volume": "test-volume"}))

    def write_timings(self, phases):
        with (self.out / "import_timings.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("experiment_id", "dbms", "phase", "elapsed_ms"))
            for phase in phases:
                writer.writerow((self.out.name, "postgres", phase, 1.0))

    def rows(self, statement):
        if "pg_database_size" in statement:
            return iter([(123,)])
        if statement == "SHOW server_version":
            return iter([("16.8",)])
        if "pg_indexes" in statement:
            return iter(self.indexes)
        if "pg_constraint" in statement:
            return iter(self.constraints)
        raise AssertionError(f"Unexpected SQL: {statement}")

    def test_finalize_reconciles_and_records_without_reloading(self):
        before = (self.out / "import_timings.csv").read_bytes()
        loader.load("postgres", finalize_only=True)
        receipt = json.loads((self.out / "import-postgres.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["counts"], self.counts)
        self.assertEqual(receipt["manifest"], self.manifest)
        self.assertFalse(any("psql" in call.args for call in self.compose.call_args_list))
        self.assertEqual((self.out / "import_timings.csv").read_bytes(), before)
        self.assertEqual((self.source / "accounts.csv").read_text(), "existing data\n")

    def test_finalize_rejects_incomplete_import(self):
        self.write_timings(("bulk",))
        with self.assertRaisesRegex(ValueError, "both bulk and indexes"):
            loader.load("postgres", finalize_only=True)
        self.compose.assert_not_called()

    def test_finalize_rejects_mismatched_counts(self):
        self.database.counts.return_value = dict(self.counts, transfers=1)
        with self.assertRaisesRegex(ValueError, "Import mismatch"):
            loader.load("postgres", finalize_only=True)
        self.assertFalse((self.out / "import-postgres.json").exists())

    def test_finalize_rejects_missing_index(self):
        self.indexes.pop()
        with self.assertRaisesRegex(ValueError, "missing PostgreSQL indexes"):
            loader.load("postgres", finalize_only=True)
        self.assertFalse((self.out / "import-postgres.json").exists())

    def test_finalize_rejects_unvalidated_foreign_key(self):
        self.constraints[-1] = ("transfers_dst_key_fkey", False)
        with self.assertRaisesRegex(ValueError, "unvalidated constraints"):
            loader.load("postgres", finalize_only=True)
        self.assertFalse((self.out / "import-postgres.json").exists())


if __name__ == "__main__":
    unittest.main()

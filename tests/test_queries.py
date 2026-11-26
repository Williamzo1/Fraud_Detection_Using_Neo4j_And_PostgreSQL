"""Query contracts: stdlib SQL checks plus opt-in PostgreSQL/Neo4j integration.

Native tests use PostgreSQL TEMP tables and rollback-only, uniquely labelled
Neo4j data. Set QUERY_TEST_POSTGRES_DSN and/or QUERY_TEST_NEO4J_URI (with
QUERY_TEST_NEO4J_PASSWORD) to run them. They do not touch benchmark artifacts.
"""
import os
import random
import re
import sqlite3
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
T0 = "2022-09-01T00:00:00"
T1 = "2022-09-01T01:00:00"


def edge(source, destination, timestamp="2022-09-01T00:30:00", amount=11.0, currency="USD"):
    return dict(src_key=source, dst_key=destination, ts=timestamp,
                amount_paid=amount, currency=currency)


def eligible(edges):
    return [row for row in edges if T0 <= row["ts"] < T1]


def simple_path_endpoints(edges, seed, k):
    adjacency = defaultdict(list)
    for row in eligible(edges):
        adjacency[row["src_key"]].append(row["dst_key"])
    found = set()

    def visit(node, path):
        if len(path) - 1 == k:
            return
        for destination in adjacency[node]:
            if destination not in path:
                found.add(destination)
                visit(destination, path + (destination,))

    visit(seed, (seed,))
    return Counter((node,) for node in found)


def triangle_transactions(edges):
    # Enumerate all orientations, then remove only rotations of each cycle.
    rows = eligible(edges)
    cycles = set()
    for x in rows:
        for y in rows:
            if x["dst_key"] != y["src_key"]:
                continue
            for z in rows:
                nodes = (x["src_key"], y["src_key"], z["src_key"])
                if len(set(nodes)) != 3:
                    continue
                if y["dst_key"] == z["src_key"] and z["dst_key"] == x["src_key"]:
                    start = nodes.index(min(nodes))
                    ids = (x["txn_id"], y["txn_id"], z["txn_id"])
                    cycles.add(ids[start:] + ids[:start])
    return Counter(cycles)


class QueryContract:
    def load(self, rows):
        self.edges = [dict(row, txn_id=f"tx-{index:04d}") for index, row in enumerate(rows)]
        self.replace_data(self.edges)

    def query(self, query_id, **overrides):
        params = dict(t0=T0, t1=T1, currency="USD", threshold=10.0, seed=1, k=3)
        params.update(overrides)
        return Counter(tuple(row) for row in self.execute_query(query_id, params))

    def test_q1_currency_amount_and_half_open_time_window(self):
        self.load([
            edge(1, 2, T0, 10.01),
            edge(1, 2, amount=10.0),
            edge(1, 2, T1, 99.0),
            edge(1, 2, amount=99.0, currency="EUR"),
            edge(1, 2, "2022-08-31T23:59:59", 99.0),
            edge(1, 2, amount=10.01),
        ])
        self.assertEqual(self.query("Q1"), Counter({("tx-0000",): 1, ("tx-0005",): 1}))

    def test_q2_cycles_parallel_edges_and_reconverging_paths(self):
        self.load([
            edge(1, 1), edge(1, 2), edge(1, 2), edge(2, 1),
            edge(2, 3), edge(3, 2), edge(3, 4), edge(4, 2),
            edge(1, 5, T1), edge(3, 6, T0), edge(2, 6),
            edge(6, 4, "2022-09-01T00:01:00"),
        ])
        for seed in (1, 2, 5):
            for k in range(1, 7):
                with self.subTest(seed=seed, k=k):
                    self.assertEqual(self.query("Q2", seed=seed, k=k),
                                     simple_path_endpoints(self.edges, seed, k))

    def test_q2_bigint_account_keys(self):
        seed = 2 ** 40
        self.load([edge(seed, seed + 1), edge(seed + 1, seed + 2), edge(seed + 2, seed)])
        self.assertEqual(self.query("Q2", seed=seed, k=6),
                         Counter({(seed + 1,): 1, (seed + 2,): 1}))

    def test_q3_parallel_transactions_both_directions_and_unordered_times(self):
        self.load([
            edge(1, 2), edge(1, 2, T0),
            edge(2, 3, "2022-09-01T00:10:00"),
            edge(3, 1, "2022-09-01T00:20:00"),
            edge(1, 3), edge(3, 2), edge(2, 1), edge(2, 1),
            edge(1, 1), edge(2, 2), edge(3, 3),
            edge(1, 2, T1),
        ])
        expected = Counter({
            ("tx-0000", "tx-0002", "tx-0003"): 1,
            ("tx-0001", "tx-0002", "tx-0003"): 1,
            ("tx-0004", "tx-0005", "tx-0006"): 1,
            ("tx-0004", "tx-0005", "tx-0007"): 1,
        })
        self.assertEqual(self.query("Q3"), expected)

    def test_random_graphs_against_independent_search(self):
        rng = random.Random(20261127)
        for graph in range(30):
            self.load([
                edge(rng.randrange(1, 7), rng.randrange(1, 7),
                     rng.choice([T0, "2022-09-01T00:20:00", T1]),
                     rng.choice([9.0, 10.0, 10.25]), rng.choice(["USD", "EUR"]))
                for _ in range(18)
            ])
            with self.subTest(graph=graph, query="Q1"):
                expected = Counter((row["txn_id"],) for row in eligible(self.edges)
                                   if row["currency"] == "USD" and row["amount_paid"] > 10.0)
                self.assertEqual(self.query("Q1"), expected)
            for seed in (1, 3):
                for k in (1, 3, 6):
                    with self.subTest(graph=graph, query="Q2", seed=seed, k=k):
                        self.assertEqual(self.query("Q2", seed=seed, k=k),
                                         simple_path_endpoints(self.edges, seed, k))
            with self.subTest(graph=graph, query="Q3"):
                self.assertEqual(self.query("Q3"), triangle_transactions(self.edges))


class SQLQueryTests(QueryContract, unittest.TestCase):
    """Execute actual SQL templates with SQLite's compatible relational subset."""
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE transfers (src_key INTEGER, dst_key INTEGER, "
                                "txn_id TEXT, ts TEXT, amount_paid REAL, currency TEXT)")

    def replace_data(self, edges):
        self.connection.execute("DELETE FROM transfers")
        self.connection.executemany(
            "INSERT INTO transfers VALUES (:src_key,:dst_key,:txn_id,:ts,:amount_paid,:currency)", edges)

    def execute_query(self, query_id, params):
        statement = (ROOT / "queries" / f"{query_id}.sql").read_text(encoding="utf-8")
        statement = re.sub(r"%\((\w+)\)s", r":\1", statement).replace("::bigint", "")
        return self.connection.execute(statement, params).fetchall()


@unittest.skipUnless(os.getenv("QUERY_TEST_POSTGRES_DSN"), "Set QUERY_TEST_POSTGRES_DSN for native tests")
class PostgreSQLQueryTests(QueryContract, unittest.TestCase):
    def setUp(self):
        import psycopg
        self.connection = psycopg.connect(os.environ["QUERY_TEST_POSTGRES_DSN"])
        self.addCleanup(self.connection.close)
        self.connection.execute("SET LOCAL statement_timeout='10s'")
        self.connection.execute("CREATE TEMP TABLE transfers (src_key bigint, dst_key bigint, "
                                "txn_id text, ts timestamp, amount_paid double precision, currency text)")
        indexes = (ROOT / "postgres-index.sql").read_text(encoding="utf-8")
        for statement in re.findall(r"CREATE INDEX\b.*?;", indexes, re.S):
            self.connection.execute(statement)

    def replace_data(self, edges):
        self.connection.execute("TRUNCATE transfers")
        with self.connection.cursor() as cursor:
            cursor.executemany("INSERT INTO transfers VALUES (%(src_key)s,%(dst_key)s,%(txn_id)s,"
                               "%(ts)s,%(amount_paid)s,%(currency)s)", edges)

    def execute_query(self, query_id, params):
        statement = (ROOT / "queries" / f"{query_id}.sql").read_text(encoding="utf-8")
        return self.connection.execute(statement, params).fetchall()


@unittest.skipUnless(os.getenv("QUERY_TEST_NEO4J_URI"), "Set QUERY_TEST_NEO4J_URI for native tests")
class Neo4jQueryTests(QueryContract, unittest.TestCase):
    def setUp(self):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(os.environ["QUERY_TEST_NEO4J_URI"],
                                          auth=(os.getenv("QUERY_TEST_NEO4J_USER", "neo4j"),
                                                os.environ["QUERY_TEST_NEO4J_PASSWORD"]))
        self.addCleanup(self.driver.close)
        self.session = self.driver.session(database=os.getenv("QUERY_TEST_NEO4J_DATABASE", "neo4j"))
        self.addCleanup(self.session.close)
        self.transaction = self.session.begin_transaction(timeout=120)
        self.addCleanup(self.transaction.rollback)
        suffix = uuid4().hex
        self.label = "QueryCheckAccount_" + suffix
        self.relationship = "QueryCheckTransfer_" + suffix

    def replace_data(self, edges):
        self.transaction.run(f"MATCH (n:{self.label}) DETACH DELETE n").consume()
        nodes = sorted(set(range(1, 7)) | {row[key] for row in edges for key in ("src_key", "dst_key")})
        self.transaction.run(f"UNWIND $nodes AS key CREATE (:{self.label} {{acc_key:key}})", nodes=nodes).consume()
        self.transaction.run(
            f"UNWIND $edges AS row MATCH (a:{self.label} {{acc_key:row.src_key}}), "
            f"(b:{self.label} {{acc_key:row.dst_key}}) "
            f"CREATE (a)-[:{self.relationship} {{txn_id:row.txn_id,ts:localdatetime(row.ts),"
            "amount_paid:row.amount_paid,currency:row.currency}]->(b)", edges=edges).consume()

    def execute_query(self, query_id, params):
        statement = (ROOT / "queries" / f"{query_id}.cypher").read_text(encoding="utf-8")
        statement = statement.replace(":Account", ":" + self.label)
        statement = statement.replace(":TRANSFER", ":" + self.relationship)
        statement = statement.replace("{K}", str(params["k"]))
        return [tuple(row) for row in self.transaction.run(statement, params)]


if __name__ == "__main__":
    unittest.main()

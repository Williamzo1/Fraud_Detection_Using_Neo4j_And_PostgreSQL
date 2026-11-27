"""Database access and artifact conventions shared by the loaders and runner."""
import csv
import json
import os
import re
import subprocess
from pathlib import Path

import psycopg
from neo4j import GraphDatabase, Query
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
SYSTEMS = ("postgres", "neo4j")


def load_environment(path=None):
    # Match Compose's precedence: exported variables override the repo .env.
    return load_dotenv(ROOT / ".env" if path is None else path, override=False)


load_environment()


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def compose(*args, capture=False):
    return subprocess.run(["docker", "compose", *args], check=True, cwd=ROOT,
                          capture_output=capture, text=True, encoding="utf-8").stdout


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def append_csv(path, record):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(record))
        if new:
            writer.writeheader()
        writer.writerow(record)


def paths():
    exp = identifier(os.environ["EXPERIMENT_ID"])
    prepared = ROOT / os.getenv("PREPARED_DIR", "prepared")
    results = ROOT / "results" / exp
    results.mkdir(parents=True, exist_ok=True)
    return prepared, results


def service_info(system):
    cid = compose("ps", "-q", system, capture=True).strip()
    if not cid:
        raise ValueError(f"Start {system} first")
    data = json.loads(subprocess.check_output(["docker", "inspect", cid], encoding="utf-8"))[0]
    mount = next(m for m in data["Mounts"] if m["Destination"] in ("/data", "/var/lib/postgresql/data"))
    return dict(image=data["Image"], volume=mount["Name"], nano_cpus=data["HostConfig"]["NanoCpus"],
                memory_bytes=data["HostConfig"]["Memory"])


class Database:
    def __init__(self, system, timeout):
        self.system = system
        self.timeout = timeout
        self.pg = self.driver = self.session = None
        if system == "postgres":
            self.pg = psycopg.connect(host="127.0.0.1", port=os.getenv("PG_PORT", "5432"),
                                      dbname="aml", user="aml", password=os.environ["POSTGRES_PASSWORD"],
                                      options=f"-c statement_timeout={int(timeout)}", autocommit=True,
                                      prepare_threshold=None)
        else:
            port = os.getenv("BOLT_PORT", "7687")
            self.driver = GraphDatabase.driver(f"bolt://127.0.0.1:{port}",
                                               auth=("neo4j", os.environ["NEO4J_PASSWORD"]))
            self.session = self.driver.session(database="neo4j")
            self.session.run("RETURN 1").consume()

    def rows(self, statement, params=None):
        if self.pg:
            with self.pg.cursor() as cursor:
                yield from cursor.stream(statement, params or {})
        else:
            result = self.session.run(Query(statement, timeout=self.timeout / 1000), params or {})
            yield from result
            result.consume()

    def measure_server(self, statement, params=None):
        """Execute statement and return server-side planning/execution timing."""
        if self.pg:
            doc = self.pg.execute(
                "EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, FORMAT JSON)\n" + statement,
                params or {},
            ).fetchone()[0][0]
            plan_ms = float(doc.get("Planning Time", 0.0))
            exec_ms = float(doc.get("Execution Time", 0.0))
            rows = int(doc.get("Plan", {}).get("Actual Rows", 0))
            return dict(
                server_ms=plan_ms + exec_ms,
                plan_or_avail_ms=plan_ms,
                exec_or_consumed_ms=exec_ms,
                row_count=rows,
            )
        result = self.session.run(Query(statement, timeout=self.timeout / 1000), params or {})
        count = sum(1 for _ in result)
        summary = result.consume()
        avail_ms = float(summary.result_available_after or 0.0)
        consumed_ms = float(summary.result_consumed_after or 0.0)
        return dict(
            server_ms=avail_ms + consumed_ms,
            plan_or_avail_ms=avail_ms,
            exec_or_consumed_ms=consumed_ms,
            row_count=count,
        )

    def close(self):
        if self.session:
            self.session.close()
        if self.driver:
            self.driver.close()
        if self.pg:
            self.pg.close()

    def counts(self):
        if self.pg:
            account = "SELECT count(*) FROM accounts"
            transfers = "SELECT is_laundering,count(*) FROM transfers GROUP BY 1"
        else:
            account = "MATCH (a:Account) RETURN count(a)"
            transfers = "MATCH ()-[r:TRANSFER]->() RETURN r.is_laundering, count(*)"
        labels = {"false": 0, "true": 0}
        for label, count in self.rows(transfers):
            labels[str(label).lower()] = count
        return dict(accounts=list(self.rows(account))[0][0], transfers=sum(labels.values()),
                    is_laundering=labels)


def reconcile(db, manifest):
    actual = db.counts()
    expected = {key: manifest[key] for key in actual}
    if actual != expected:
        raise ValueError(f"Import mismatch: expected {expected}, found {actual}")
    return actual

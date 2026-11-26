import argparse
import csv
import time
from contextlib import closing

from prepare import check_amount_format
from db import (Database, SYSTEMS, append_csv, compose, paths, read_json,
                reconcile, save_json, service_info)


def check_completed_phases(out, system):
    timings = out / "import_timings.csv"
    if not timings.is_file():
        raise ValueError("Cannot finalize: import_timings.csv is missing.")
    with timings.open(encoding="utf-8", newline="") as stream:
        phases = {row["phase"] for row in csv.DictReader(stream)
                  if row["dbms"] == system and row["experiment_id"] == out.name}
    if not {"bulk", "indexes"}.issubset(phases):
        raise ValueError("Cannot finalize: both bulk and indexes must have completed in this experiment.")


def check_postgres_schema(db, indexes):
    required_indexes = {"accounts_pkey", "accounts_bank_id_account_id_key", "transfers_pkey",
                        "transfers_src_ts", "transfers_currency_ts_amt",
                        "transfers_cycle_start", "transfers_cycle_close"}
    missing = required_indexes - {name for name, definition in indexes}
    if missing:
        raise ValueError(f"Cannot finalize: missing PostgreSQL indexes: {sorted(missing)}")
    constraints = dict(db.rows(
        "SELECT conname,convalidated FROM pg_constraint "
        "WHERE conrelid IN ('public.accounts'::regclass,'public.transfers'::regclass) "
        "AND contype IN ('p','u','f')"))
    required_constraints = {"accounts_pkey", "accounts_bank_id_account_id_key", "transfers_pkey",
                            "transfers_src_key_fkey", "transfers_dst_key_fkey"}
    invalid = {name for name in required_constraints if not constraints.get(name)}
    if invalid:
        raise ValueError(f"Cannot finalize: missing or unvalidated constraints: {sorted(invalid)}")


def load(system, finalize_only=False):
    if finalize_only and system != "postgres":
        raise ValueError("--finalize-only currently supports PostgreSQL only.")
    source, out = paths()
    manifest = read_json(source / "manifest.json")
    check_amount_format(manifest)
    names = ("accounts", "transfers") if system == "postgres" else ("nodes", "edges")
    for name in names:
        if not (source / f"{name}.csv").is_file():
            raise FileNotFoundError(source / f"{name}.csv")
    if (out / f"import-{system}.json").exists():
        raise ValueError("Import already recorded. Use a new EXPERIMENT_ID and COMPOSE_PROJECT_NAME.")
    if finalize_only:
        check_completed_phases(out, system)
    def timed(phase, action):
        start = time.perf_counter()
        action()
        append_csv(out / "import_timings.csv", dict(experiment_id=out.name, dbms=system,
                   phase=phase, elapsed_ms=(time.perf_counter()-start)*1000))

    def up():
        compose("up", "-d", "--wait", system)

    def inside(*args):
        return compose("exec", "-T", system, *args, capture=True).strip()

    if finalize_only:
        up()
    elif system == "postgres":
        up()
        def sql(file):
            compose("exec", "-T", system, "psql", "-X", "-U", "aml", "-d", "aml", "-f", file)
        timed("bulk", lambda: sql("/aml-postgres.sql"))
        timed("indexes", lambda: sql("/aml-index.sql"))
    else:
        run = ["run", "--rm", "-T", "--no-deps"]
        compose(*run, "--entrypoint", "sh", system, "-ec",
                'if test -e /data/databases/neo4j; then echo "Existing store: choose a new COMPOSE_PROJECT_NAME" >&2; exit 1; fi')
        compose("stop", system)
        timed("bulk", lambda: compose(*run, system, "neo4j-admin", "database", "import", "full", "neo4j",
              "--id-type=integer", "--bad-tolerance=0", "--overwrite-destination=false",
              "--max-off-heap-memory=2G", "--report-file=/data/aml-import.report",
              "--nodes=Account=/aml-data/nodes.csv", "--relationships=TRANSFER=/aml-data/edges.csv"))
        up()
        with closing(Database(system, 900000)) as db:
            def indexes():
                for stmt in ("CREATE CONSTRAINT account_key FOR (a:Account) REQUIRE a.acc_key IS UNIQUE",
                             "CREATE INDEX account_identity FOR (a:Account) ON (a.bank_id,a.account_id)",
                             "CREATE RANGE INDEX transfer_currency_ts_amount FOR ()-[r:TRANSFER]-() ON (r.currency,r.ts,r.amount_paid)",
                             "CREATE RANGE INDEX transfer_ts FOR ()-[r:TRANSFER]-() ON (r.ts)",
                             "CALL db.awaitIndexes(600)"):
                    list(db.rows(stmt))
            timed("indexes", indexes)

    with closing(Database(system, 900000)) as db:
        counts = reconcile(db, manifest)
        if db.pg:
            size = list(db.rows("SELECT pg_database_size(current_database())"))[0][0]
            method, mount = "pg_database_size(aml)", "/var/lib/postgresql/data"
            version = list(db.rows("SHOW server_version"))[0][0]
            indexes = [list(r) for r in db.rows("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1")]
            if finalize_only:
                check_postgres_schema(db, indexes)
        else:
            size = int(inside("du", "-sb", "/data/databases/neo4j").split()[0])
            method, mount = "du -sb /data/databases/neo4j", "/data"
            version = list(db.rows("CALL dbms.components() YIELD versions RETURN versions[0]"))[0][0]
            indexes = [list(r) for r in db.rows("SHOW INDEXES YIELD name,state,properties RETURN name,state,properties ORDER BY name")]
        for scope, size, method in [("database", size, method),
                                    ("volume", int(inside("du", "-sb", mount).split()[0]), f"du -sb {mount}")]:
            append_csv(out / "storage.csv", dict(experiment_id=out.name, dbms=system, scope=scope,
                                                  bytes=size, method=method))
        save_json(out / f"import-{system}.json", dict(manifest=manifest, counts=counts, version=version,
                                                      indexes=indexes, service=service_info(system)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("system", choices=SYSTEMS)
    parser.add_argument("--finalize-only", action="store_true",
                        help="PostgreSQL only: reconcile and record a completed bulk/index import without reloading data.")
    args = parser.parse_args()
    load(args.system, args.finalize_only)

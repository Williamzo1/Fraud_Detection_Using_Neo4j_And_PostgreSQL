"""Run from the project root in the activated benchmark environment."""
import argparse
import csv
import hashlib
import json
import math
import random
import tempfile
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from neo4j import Query
from db import (ROOT, SYSTEMS, Database, append_csv, compose, identifier, paths,
                read_json, reconcile, save_json, service_info)
from prepare import check_amount_format, sha256


def load_cases(file, manifest, out):
    check_amount_format(manifest)
    config = read_json(file)
    cases = {}
    for c in config["cases"]:
        cid, q = identifier(c["case_id"]), c["query_id"]
        if q not in ("Q1", "Q2", "Q3") or cid in cases:
            raise ValueError(f"Invalid or duplicate case {cid}")
        lo, hi = (datetime.fromisoformat(c[key]) for key in ("t0", "t1"))
        if lo.tzinfo or hi.tzinfo or lo >= hi:
            raise ValueError(f"Invalid time window in {cid}")
        if q == "Q2":
            if type(c["k"]) is not int or not 1 <= c["k"] <= config["max_k"]:
                raise ValueError(f"Invalid k in {cid}")
            if len(c["seed"]) != 2 or not all(isinstance(v, str) for v in c["seed"]):
                raise ValueError(f"Use two string IDs for seed in {cid}")
        if q == "Q1" and (type(c["threshold"]) not in (int, float) or not math.isfinite(c["threshold"]) or not isinstance(c["currency"], str)):
            raise ValueError(f"Invalid Q1 parameters in {cid}")
        cases[cid] = c
    if not cases:
        raise ValueError("No cases configured")
    signature = dict(config=config, manifest=manifest,
                     queries={p.name: sha256(p) for p in sorted((ROOT / "queries").glob("*"))})
    snapshot = out / "experiment.json"
    if snapshot.exists() and read_json(snapshot) != signature:
        raise ValueError("Data, queries or cases changed. Choose a new experiment.")
    save_json(snapshot, signature)
    return cases


def compile_cases(db, cases):
    prepared = {}
    seeds = {}
    for cid, c in cases.items():
        q = c["query_id"]
        params = {k: v for k, v in c.items() if k not in ("case_id", "query_id")}
        ext = "sql" if db.pg else "cypher"
        statement = (ROOT / "queries" / f"{q}.{ext}").read_text(encoding="utf-8")
        if q == "Q1":
            params["threshold"] = float(params["threshold"])
        if db.pg:
            for key in ("t0", "t1"):
                params[key] = datetime.fromisoformat(params[key])
        if q == "Q2":
            bank, account = c["seed"]
            if (bank, account) not in seeds:
                lookup = ("SELECT acc_key FROM accounts WHERE bank_id=%(bank)s AND account_id=%(account)s"
                          if db.pg else "MATCH (a:Account {bank_id:$bank,account_id:$account}) RETURN a.acc_key")
                keys = list(db.rows(lookup, dict(bank=bank, account=account)))
                if len(keys) != 1:
                    raise ValueError(f"{cid}: seed {(bank, account)} has {len(keys)} matches")
                seeds[bank, account] = keys[0][0]
            params["seed"] = seeds[bank, account]
            statement = statement.replace("{K}", str(c["k"]))
        prepared[cid] = statement, params
    return prepared


def canonical(rows, q, path):
    # SQLite sorts on disk. Duplicate JSON lines represent separate result rows.
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        with closing(sqlite3.connect(str(Path(tmp) / "sort.db"))) as sort:
            sort.execute("PRAGMA temp_store=FILE")
            sort.execute("CREATE TABLE records(line TEXT NOT NULL)")
            def lines():
                for row in rows:
                    values = [int(row[0])] if q == "Q2" else [str(v) for v in row]
                    yield (json.dumps(values, separators=(",", ":")),)
            sort.executemany("INSERT INTO records VALUES (?)", lines())
            digest, count = hashlib.sha256(), 0
            staged = Path(tmp) / "sorted.jsonl"
            with staged.open("wb") as stream:
                for (line,) in sort.execute("SELECT line FROM records ORDER BY line COLLATE BINARY"):
                    data = (line + "\n").encode()
                    stream.write(data)
                    digest.update(data)
                    count += 1
            staged.replace(path)
    return dict(rows=count, sha256=digest.hexdigest())


def differences(left, right):
    sample = []
    with left.open("rb") as a, right.open("rb") as b:
        x, y = a.readline(), b.readline()
        while (x or y) and len(sample) < 20:
            if x == y:
                x, y = a.readline(), b.readline()
            elif x and (not y or x < y):
                sample.append(["postgres", json.loads(x)])
                x = a.readline()
            else:
                sample.append(["neo4j", json.loads(y)])
                y = b.readline()
    return sample


def verify(db, prepared, cases, out):
    root = out / "verify"
    folder = root / db.system
    folder.mkdir(parents=True, exist_ok=True)
    mismatch = False
    for cid, (stmt, params) in prepared.items():
        dest = folder / (cid + ".jsonl")
        report = root / (cid + ".json")
        state = read_json(report) if report.exists() else {}
        state.pop(db.system, None)
        state.update(status="pending", differences=[])
        save_json(report, state)
        state[db.system] = canonical(db.rows(stmt, params), cases[cid]["query_id"], dest)
        if all(s in state for s in SYSTEMS):
            same = state["postgres"] == state["neo4j"]
            state["status"] = "match" if same else "mismatch"
            if not same:
                state["differences"] = differences(*(root / s / dest.name for s in SYSTEMS))
                mismatch = True
        save_json(report, state)
    if mismatch:
        raise ValueError("Different results. See verify/<case_id>.json")


def benchmark(db, prepared, cases, out, args):
    runfile = out / "runs.csv"
    if runfile.exists():
        with runfile.open(newline="") as f:
            if any(r["dbms"] == db.system for r in csv.DictReader(f)):
                raise ValueError("Runs already exist for this DBMS and experiment")
    for cid in cases:
        if read_json(out / "verify" / (cid + ".json"))["status"] != "match":
            raise ValueError(f"Verify {cid} on both databases before benchmarking")
    rng = random.Random(args.seed)
    save_json(out / f"settings-{db.system}.json", dict(warmup=args.warmup, repetitions=args.repetitions,
              seed=args.seed, timeout_ms=args.timeout_ms, resolved_seeds={
                  cid:p["seed"] for cid, (_,p) in prepared.items() if "seed" in p}))
    order = 0
    try:
        for phase, n in (("warmup", args.warmup), ("measure", args.repetitions)):
            for rep in range(1, n+1):
                ids = list(cases)
                rng.shuffle(ids)
                for cid in ids:
                    stmt, params = prepared[cid]
                    status, count, error = "ok", 0, ""
                    start = time.perf_counter_ns()
                    try:
                        for _ in db.rows(stmt, params):
                            count += 1
                    except Exception as exc:
                        error = str(exc)
                        code = str(getattr(exc, "code", "") or getattr(exc, "sqlstate", ""))
                        status = "timeout" if code == "57014" or "timedout" in code.lower() or "timeout" in error.lower() else "error"
                    elapsed = (time.perf_counter_ns()-start)/1e6
                    order += 1
                    append_csv(runfile, dict(experiment_id=out.name, dbms=db.system, query_id=cases[cid]["query_id"],
                          case_id=cid, phase=phase, repetition=rep, latency_ms=elapsed, row_count=count,
                          status=status, timeout_ms=args.timeout_ms, error_message=error, run_order=order))
                    if status != "ok":
                        system = db.system
                        db.close()
                        db = Database(system, args.timeout_ms)
    finally:
        db.close()


def plans(db, prepared, out):
    for cid, (statement, params) in prepared.items():
        if db.pg:
            plan = db.pg.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+statement, params).fetchone()[0]
        else:
            res = db.session.run(Query("PROFILE "+statement, timeout=db.timeout/1000), params)
            for _ in res:
                pass
            plan = res.consume().profile
        save_json(out / "plans" / db.system / (cid + ".json"), plan)


def percentile(sorted_values, fraction):
    if not sorted_values:
        return None
    pos = (len(sorted_values)-1)*fraction
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (pos-lo)*(sorted_values[hi]-sorted_values[lo])


def summarize(out):
    config = read_json(out / "experiment.json")["config"]
    with (out / "runs.csv").open(newline="", encoding="utf-8") as stream:
        runs = [r for r in csv.DictReader(stream) if r["phase"] == "measure"]
    summary, sweep = [], []
    for c in config["cases"]:
        cid = c["case_id"]
        status = read_json(out / "verify" / (cid+".json"))["status"]
        pair = []
        for system in SYSTEMS:
            group = [r for r in runs if r["case_id"] == cid and r["dbms"] == system]
            values = sorted(float(r["latency_ms"]) for r in group if r["status"] == "ok")
            row = dict(experiment_id=out.name, dbms=system, query_id=c["query_id"], case_id=cid,
                       p50_ms=percentile(values,.5), p95_ms=percentile(values,.95),
                       iqr_ms=percentile(values,.75)-percentile(values,.25) if values else None,
                       measured=len(group), completed=len(values), timeouts=sum(r["status"]=="timeout" for r in group),
                       errors=sum(r["status"]=="error" for r in group), verification=status)
            pair.append(row)
        a,b = (r["p50_ms"] for r in pair)
        for row in pair:
            row["p50_postgres_over_neo4j"] = a/b if status=="match" and a is not None and b else None
            summary.append(row)
            if c["query_id"]=="Q2":
                sweep.append(dict(**row, k=c["k"], bank_id=c["seed"][0], account_id=c["seed"][1], t0=c["t0"], t1=c["t1"]))
    for name, records in (("summary.csv",summary),("q2_by_k.csv",sweep)):
        if records:
            with (out/name).open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f,fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["verify","benchmark","collect-plans","summarize"])
    parser.add_argument("--dbms", choices=SYSTEMS)
    parser.add_argument("--cases", type=Path, default=ROOT/"cases.json")
    parser.add_argument("--timeout-ms", type=int, default=120000)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    source, out = paths()
    if args.command == "summarize":
        return summarize(out)
    system = args.dbms
    if not system or args.timeout_ms < 1 or args.warmup < 0 or args.repetitions < 1:
        parser.error("Specify --dbms, positive timeout/repetitions and nonnegative warmup")
    if args.command == "benchmark" and compose("ps", "--services", "--status", "running", capture=True).split() != [system]:
        raise ValueError("Run only the measured service in this Compose project")
    manifest = read_json(source/"manifest.json")
    receipt = read_json(out/f"import-{system}.json")
    if receipt["manifest"] != manifest or receipt["service"] != service_info(system):
        raise ValueError("Prepared dataset or Docker configuration differs from import")
    cases = load_cases(args.cases, manifest, out)
    with closing(Database(system, 900000)) as preflight:
        reconcile(preflight, manifest)
        prepared = compile_cases(preflight, cases)
    with closing(Database(system, args.timeout_ms)) as db:
        if args.command == "verify":
            verify(db, prepared, cases, out)
        elif args.command == "collect-plans":
            plans(db, prepared, out)
        else:
            benchmark(db, prepared, cases, out, args)


if __name__ == "__main__":
    main()

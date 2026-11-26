"""Check both driver outputs against the known fixture answers after verify."""
import json
import sys
from pathlib import Path


def tx(n):
    return f"tx-{n:012d}"


expected = {
    "filter": [[tx(1)], [tx(2)], [tx(8)]],
    "cycles": [[tx(1), tx(3), tx(4)], [tx(2), tx(3), tx(4)], [tx(5), tx(6), tx(7)]],
    **{f"trace-{k}": [[2], [3]] for k in (1, 2, 3)},
}
root = Path(sys.argv[1])
for system in ("postgres", "neo4j"):
    receipt = json.loads((root / f"import-{system}.json").read_text(encoding="utf-8"))
    if receipt["counts"] != {"accounts": 4, "transfers": 9, "is_laundering": {"true": 3, "false": 6}}:
        raise ValueError(f"{system}: unexpected counts")
    for case, rows in expected.items():
        path = root / "verify" / system / (case + ".jsonl")
        actual = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if sorted(actual) != sorted(rows):
            raise ValueError(f"{system}/{case}: expected {rows}, got {actual}")
print("Both databases match the expected fixture IDs for all five cases.")

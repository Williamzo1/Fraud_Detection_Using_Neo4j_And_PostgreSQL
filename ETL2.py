"""Rename IBM AML account columns without converting their values."""

import argparse
import csv
from pathlib import Path


COLUMN_MAPPING = {
    "Bank Name": "bank_name",
    "Bank ID": "bank_id",
    "Account Number": "account_id",
    "Entity ID": "entity_id",
    "Entity Name": "entity_name",
}


def rename_columns(input_file: Path, output_file: Path) -> None:
    if input_file.resolve() == output_file.resolve():
        raise ValueError("Input and output must be different files.")

    with input_file.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        header = next(reader, None)
        if header != list(COLUMN_MAPPING):
            raise ValueError(f"Unexpected CSV header: {header!r}")

        # Exclusive creation prevents overwriting an existing file.
        with output_file.open("x", encoding="utf-8", newline="") as target:
            writer = csv.writer(target)
            writer.writerow([COLUMN_MAPPING[name] for name in header])
            writer.writerows(reader)

    print(f"Saved: {output_file.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", nargs="?", type=Path, default=Path("HI-Small_accounts.csv")
    )
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    output_file = args.output or args.input.with_name(
        args.input.stem + "_renamed.csv"
    )
    rename_columns(args.input, output_file)


if __name__ == "__main__":
    main()

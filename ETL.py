import csv
from pathlib import Path

input_file = Path("HI-Small_Trans.csv")
output_file = Path("HI-Small_Trans_renamed.csv")

original_columns = [
    "Timestamp", "From Bank", "Account", "To Bank", "Account",
    "Amount Received", "Receiving Currency",
    "Amount Paid", "Payment Currency",
    "Payment Format", "Is Laundering",
]

new_columns = [
    "ts", "src_bank", "src_account", "dst_bank", "dst_account",
    "amount_received", "receiving_currency",
    "amount_paid", "payment_currency",
    "payment_format", "is_laundering",
]

with input_file.open(
    "r", encoding="utf-8-sig", newline=""
) as source:
    reader = csv.reader(source)
    header = next(reader)

    # Kiểm tra thứ tự cột trước khi đổi tên.
    if header != original_columns:
        raise ValueError(
            f"Cấu trúc cột khác dự kiến. Header thực tế: {header}"
        )

    with output_file.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        writer = csv.writer(target)
        writer.writerow(new_columns)

        for row in reader:
            writer.writerow(row)

print(f"Đã lưu: {output_file.resolve()}")
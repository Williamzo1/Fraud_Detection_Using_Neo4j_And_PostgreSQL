# So sánh PostgreSQL và Neo4j trên IBM AML-Data

Pipeline nạp cùng một tập giao dịch vào PostgreSQL 16.8 và Neo4j Community 5.26.5, đối chiếu ID kết quả Q1–Q3, rồi đo từng hệ riêng. Chạy các khối lệnh dưới đây trong cùng một terminal WSL2 tại thư mục repo. Cần Python 3.10+, Docker Desktop với WSL integration, Compose v2 và các cổng 5432, 7474, 7687 còn trống.

## 1. Chuẩn bị môi trường

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
# Sửa hai mật khẩu trong .env (ít nhất 8 ký tự, dùng chữ và số).
set -a
source .env
set +a
docker info >/dev/null
docker compose config --quiet
```

DB_CPUS và DB_MEMORY áp dụng cho **mỗi** service. Mặc định 4 CPU và 4 GiB. PG_PORT, BOLT_PORT, HTTP_PORT có thể export nếu cổng mặc định đang dùng. Khi đổi ngân sách RAM, điều chỉnh cả heap/page cache và bộ nhớ offline importer cho phù hợp. Các lệnh runner đọc biến môi trường đã export.

## 2. Chọn dữ liệu và tạo CSV

Để kiểm tra cài đặt bằng fixture:

```bash
export EXPERIMENT_ID=fixture-01 COMPOSE_PROJECT_NAME=aml-fixture-01
export PREPARED_DIR=./prepared-fixture-01 CASES=fixtures/cases.json
python prepare.py fixtures/accounts.csv fixtures/transfers.csv "$PREPARED_DIR"
```

Cho HI-Small dùng project, kết quả và thư mục dữ liệu riêng. Hai input mặc định là HI-Small_accounts_renamed.csv và HI-Small_Trans_renamed.csv. ETL.py và ETL2.py chỉ cần khi đổi tên cột từ file gốc.

```bash
export EXPERIMENT_ID=hi-small-01 COMPOSE_PROJECT_NAME=aml-hi-small-01
export PREPARED_DIR=./prepared CASES=cases.json
python prepare.py --allow-bank-padding
# Hoặc truyền đường dẫn (và thư mục đầu ra) trực tiếp:
# python prepare.py /data/accounts_renamed.csv /data/transfers_renamed.csv "$PREPARED_DIR"
```

**Khóa ngân hàng trên HI-Small trong workspace.** Accounts dùng mã ngân hàng `10` nơi transfers ghi `010`. Mặc định loader so khóa chuỗi chính xác. Nếu xác nhận hai file dùng quy ước đệm số 0 khác nhau, chạy `python prepare.py --allow-bank-padding`. Tùy chọn này ưu tiên khóa chính xác, chỉ tra cứu bổ sung khi mã ngân hàng số có đúng một tài khoản tương ứng sau khi bỏ số 0 đệm. ID trong bảng accounts vẫn giữ nguyên.

prepare.py đọc giao dịch từng dòng và giữ bản đồ tài khoản trong RAM. Khóa gồm cả bank_id và account_id. Mỗi giao dịch có ID theo vị trí bản ghi. Giữ cả self-transfer, cạnh song song và tài khoản cô lập. manifest.json ghi tổng số tài khoản, giao dịch, hai nhóm nhãn, đơn vị tiền gốc (amount_scale = 1), chế độ đối chiếu và SHA-256 hai file nguồn. Bốn CSV sinh ra được mount read-only tại /aml-data.

## 3. Nạp và đối soát

```bash
bash import-postgres.sh
docker compose stop postgres
bash import-neo4j.sh
```

Mỗi script khởi động service cần thiết, đợi sẵn sàng và gọi loader.py. PostgreSQL dùng hai transaction: tạo bảng và COPY, sau đó PK/UNIQUE/FK, index truy vấn và ANALYZE. Nếu bảng đã tồn tại, CREATE TABLE dừng trước khi nạp. Neo4j kiểm tra store trước khi dừng service, bulk import offline, khởi động rồi tạo constraint và relationship range index, đợi ONLINE. Báo cáo importer ở /data/aml-import.report trong volume. Hai bộ nạp kiểm tra số lượng thực tế theo manifest, chỉ ghi biên nhận import khi khớp.

Khi cần nạp lại, dừng service của project cũ rồi đặt **cả** COMPOSE_PROJECT_NAME và EXPERIMENT_ID thành tên mới. Có thể dùng lại PREPARED_DIR đã hoàn tất. Nếu một bước đã commit trước khi bước kế tiếp lỗi, volume cũ vẫn giữ dữ liệu để chẩn đoán.

## 4. Kiểm chứng ID, benchmark và lấy plan

Sau bước nạp, Neo4j đang chạy. Các lệnh sau chỉ chạy một DBMS tại một thời điểm.

```bash
python runner.py verify --dbms neo4j --cases "$CASES"
docker compose stop neo4j
docker compose up -d --wait postgres
python runner.py verify --dbms postgres --cases "$CASES"

# Riêng với fixture, kiểm tra thêm bằng đáp án viết độc lập.
# python fixtures/check_results.py "results/$EXPERIMENT_ID"

python runner.py benchmark --dbms postgres --cases "$CASES"
python runner.py collect-plans --dbms postgres --cases "$CASES"
docker compose stop postgres
docker compose up -d --wait neo4j
python runner.py benchmark --dbms neo4j --cases "$CASES"
python runner.py collect-plans --dbms neo4j --cases "$CASES"
docker compose stop neo4j
python runner.py summarize
```

Mặc định mỗi case chạy 5 warm-up và 30 lượt đo. Để smoke test nhanh, thêm `--warmup 1 --repetitions 3` vào cả hai lệnh benchmark. Đặt `--seed 42 --timeout-ms 120000` để đổi hạt giống xáo trộn hoặc timeout phía DBMS. Mỗi repetition xáo thứ tự case. Truy vấn chạy trên kết nối đã mở, đồng hồ đơn điệu tính từ lúc gửi tới khi driver đọc hết kết quả, gồm truyền dữ liệu và đếm hàng tại client. Dùng lại kết nối giữa các lượt thành công và mở kết nối mới ngoài phép đo sau lỗi. Timeout phía server có thể được phát hiện muộn hơn thời hạn cấu hình. Các lượt đo dùng cache của phiên đã warm-up.

**Case và ngữ nghĩa.** Sửa cases.json trước verify. Chọn t0, t1 theo giờ nguồn, t0 bao gồm và t1 loại trừ. Q1 lọc amount_paid > threshold theo payment_currency. Nhập ngưỡng bằng đơn vị tiền gốc, ví dụ 1000 hoặc 1000.25 cho US Dollar. CSV đầu ra giữ nguyên cách viết amount_paid và amount_received. PostgreSQL dùng double precision và Neo4j dùng double nên cả hai so sánh theo cùng độ chính xác dấu phẩy động 64 bit. Các giá trị thập phân cực gần nhau có thể được lưu thành cùng một số. Với dữ liệu đã nạp theo schema cũ, chạy lại prepare vào thư mục mới và import bằng project Compose cùng EXPERIMENT_ID mới. Q2 dùng seed là cặp chuỗi [bank_id, account_id] từ accounts.csv, runner tra khóa trong DBMS đang đo. Dùng cùng seed, cửa sổ và thay k trong các case trace. Q2 trả tập tài khoản đến được theo đường có hướng từ 1 đến k cạnh, không lặp nút. Q3 lấy ba tài khoản phân biệt, bắt đầu ở khóa nhỏ nhất, giữ chiều ngược và từng tổ hợp cạnh. Q2/Q3 cho phép timestamp theo mọi thứ tự trong cửa sổ. Nhãn is_laundering chỉ dùng đối soát.

## 5. Đọc kết quả

Mỗi experiment có thư mục results/<EXPERIMENT_ID>/.

| File | Nội dung |
|---|---|
| experiment.json, import-{dbms}.json | Case, hash truy vấn/nguồn, đối soát, phiên bản, index thực, image ID, named volume, giới hạn CPU/RAM thực tế |
| verify/{dbms}/{case}.jsonl | Toàn bộ ID chuẩn hóa thành mảng JSON, sắp xếp theo byte ngoài RAM bằng SQLite, giữ số lần xuất hiện |
| verify/{case}.json | Số dòng, SHA-256 từng hệ, trạng thái pending/match/mismatch và tối đa 20 dòng lệch |
| settings-{dbms}.json, runs.csv | Tham số đo, acc_key đã ánh xạ và thứ tự thực tế. Mỗi dòng là một lượt, gồm cả timeout và lỗi |
| summary.csv | p50, p95, IQR (ms), số lượt đo/thành công/timeout/lỗi, trạng thái đối soát và tỷ số p50 PostgreSQL / Neo4j |
| q2_by_k.csv | Bảng vẽ p50 theo k, DBMS, seed và cửa sổ |
| plans/{dbms}/{case}.json | EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) hoặc cây PROFILE, thu ở lượt riêng |
| import_timings.csv | Millisecond theo bulk/index. PostgreSQL bulk gồm exec psql, DDL bảng và COPY; indexes gồm constraint, index, ANALYZE và commit. Neo4j bulk gồm tạo/chạy container offline và ghi store; indexes gồm DDL và thời gian chờ index. Khởi động service, hậu kiểm và đo dung lượng nằm ngoài hai pha |
| storage.csv | Byte sau tạo index. PostgreSQL dùng pg_database_size(aml). Neo4j dùng du -sb trên thư mục store neo4j gồm index. Phạm vi volume dùng du -sb trên mount /data hoặc PGDATA, gồm cả file ngoài database như log giao dịch. Đây là kích thước biểu kiến, khác dung lượng vật lý của Docker Desktop |

Phân vị nội suy tuyến tính tại vị trí (n−1)×p của các lượt measure thành công, IQR = p75−p25. Case thiếu p50 hợp lệ ở một hệ hoặc lệch đầu ra sẽ để trống tỷ số. Tỷ số lớn hơn 1 nghĩa là PostgreSQL có p50 cao hơn trong case đó. Dùng số đo fixture để kiểm tra pipeline. Dùng thí nghiệm HI-Small đã đối soát để kết luận hiệu năng trên HI-Small.

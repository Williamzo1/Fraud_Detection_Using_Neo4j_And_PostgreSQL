# So sánh PostgreSQL và Neo4j trên IBM AML-Data

Pipeline nạp cùng một tập giao dịch vào PostgreSQL 16.8 và Neo4j Community 5.26.5, đối chiếu ID kết quả Q1–Q3, rồi đo từng hệ riêng. Các lệnh dưới đây dùng Bash trong Ubuntu WSL2 tại thư mục repo, ví dụ `~/IMB_AML`. Cần Python 3.10+, Docker Desktop đang chạy với WSL integration, Compose v2 và các cổng 5432, 7474, 7687 còn trống. Dùng cùng một terminal cho các bước; nếu mở terminal mới, kích hoạt lại virtual environment, nạp `.env` và các biến ở bước 2.

## 1. Chuẩn bị môi trường

```bash
cd ~/IMB_AML  # Thay bằng đường dẫn repo của bạn trong WSL.
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
fi
```

Sửa hai mật khẩu trong `.env` bằng trình soạn thảo (ít nhất 8 ký tự, dùng chữ và số), rồi nạp vào terminal:

```bash
set -a
source .env
set +a
timeout 15s docker version
timeout 15s docker info
docker compose version
```

Chỉ tiếp tục khi Docker trả kết quả thành công và `docker version` hiện cả Client và Server. Nếu lệnh hết 15 giây mà chưa phản hồi, xem bước 6 trước khi import. Tạo virtual environment trong WSL; không dùng lại virtual environment tạo trên Windows. Docker Compose và các chương trình Python đều đọc `.env` trong thư mục repo; biến đã export trong terminal được ưu tiên. Bước `set -a`, `source .env`, `set +a` còn giúp các lệnh Bash sử dụng chung các biến này. Cài lại `pip install -r requirements.txt` khi cập nhật code để có thư viện `python-dotenv`.

`DB_CPUS` và `DB_MEMORY` áp dụng cho **mỗi** service (mặc định `1` CPU và `4g`). `NEO4J_HEAP` (mặc định `1G`) và `NEO4J_PAGECACHE` (mặc định `1800M` để chứa vừa database ~`1.72 GB` trong container `4g`) cấu hình bộ nhớ cho Neo4j; `PG_PORT`, `BOLT_PORT`, `HTTP_PORT` có thể export nếu cổng mặc định đang dùng. Khi đổi ngân sách RAM (ví dụ nâng lên `8g`), điều chỉnh cả `NEO4J_HEAP`, `NEO4J_PAGECACHE` và bộ nhớ offline importer cho phù hợp. Các lệnh runner đọc biến môi trường đã export.

## 2. Chuẩn bị dữ liệu HI-Small và tạo CSV

Chuẩn bị HI-Small một lần để nạp vào cả PostgreSQL và Neo4j. Hai hệ dùng chung thư mục PREPARED_DIR và bộ truy vấn cases.json. Hai input là HI-Small_accounts_renamed.csv và HI-Small_Trans_renamed.csv. ETL.py và ETL2.py chỉ cần khi đổi tên cột từ file gốc.

```bash
export EXPERIMENT_ID=hi-small-01 COMPOSE_PROJECT_NAME=aml-hi-small-01
export PREPARED_DIR=./prepared CASES=cases.json
docker compose config --quiet
```

Chọn tên experiment/project chưa từng dùng để import. Hai tên trên là ví dụ cho lần đầu; cách nạp lại ở cuối bước 3.

**Nếu thư mục dữ liệu chưa tồn tại**, chạy:

```bash
python prepare.py HI-Small_accounts_renamed.csv HI-Small_Trans_renamed.csv "$PREPARED_DIR"
# Nếu dữ liệu nằm ngoài repo, thay đường dẫn hai file đầu vào:
# python prepare.py /data/accounts_renamed.csv /data/transfers_renamed.csv "$PREPARED_DIR"
```

**Nếu đã có dữ liệu chuẩn bị**, bỏ qua lệnh prepare và kiểm tra manifest:

```bash
python -m json.tool "$PREPARED_DIR/manifest.json"
```

Manifest cần có `amount_format: "source-unit-binary64"` và `transaction_bank_id_normalization: "strip-leading-zeros"` để tương ứng với cách chuẩn bị hiện tại. Nếu thiếu trường hoặc muốn chuẩn bị lại, chọn thư mục đầu ra mới, ví dụ `export PREPARED_DIR=./prepared-v2`, rồi chạy lệnh prepare phía trên. Nếu không đọc được manifest, xử lý lỗi truy cập trước khi import. prepare.py từ chối ghi đè thư mục đã tồn tại và không tự đọc PREPARED_DIR; đường dẫn đầu ra được lấy từ argument thứ ba. Sau khi đổi thư mục, chạy lại `docker compose config --quiet`.

prepare.py tự bỏ số `0` đệm đầu trong `src_bank` và `dst_bank`, ví dụ `010` → `10`, `001` → `1`, `000` → `0`. Mã tài khoản và hai CSV nguồn được giữ nguyên. Chương trình đọc giao dịch từng dòng và giữ bản đồ tài khoản trong RAM. Khóa gồm cả bank_id và account_id. Mỗi giao dịch có ID theo vị trí bản ghi. Giữ cả self-transfer, cạnh song song và tài khoản cô lập. manifest.json ghi tổng số tài khoản, giao dịch, hai nhóm nhãn, đơn vị tiền gốc (amount_scale = 1), chế độ chuẩn hóa và SHA-256 hai file nguồn. Bốn CSV sinh ra được mount read-only tại /aml-data.

## 3. Nạp và đối soát

```bash
bash import-postgres.sh
```

Đợi lệnh kết thúc thành công và kiểm tra biên nhận trước khi chuyển hệ:

```bash
python -m json.tool "results/$EXPERIMENT_ID/import-postgres.json"
```

Khi biên nhận PostgreSQL đọc được và số lượng khớp manifest, chuyển sang Neo4j:

```bash
docker compose stop postgres
bash import-neo4j.sh
```

Sau khi import Neo4j thành công, kiểm tra biên nhận tương tự:

```bash
python -m json.tool "results/$EXPERIMENT_ID/import-neo4j.json"
```

PostgreSQL in thông báo như `BEGIN`, `CREATE TABLE`, `COPY ...`, `CREATE INDEX`, `COMMIT` khi từng câu lệnh hoàn tất. Trong lúc COPY hoặc tạo index, có thể chưa có dòng mới; loader hiện không in phần trăm tiến độ. Biên nhận `import-{dbms}.json` được ghi sau khi hoàn tất nạp, tạo index và hậu kiểm. Nếu lệnh import hoặc kiểm tra biên nhận lỗi, xử lý lỗi trước khi chạy lệnh tiếp theo.

Mỗi script khởi động service cần thiết, đợi sẵn sàng và gọi loader.py. PostgreSQL dùng hai transaction: tạo bảng và COPY, sau đó PK/UNIQUE/FK, index truy vấn và ANALYZE. Nếu bảng đã tồn tại, CREATE TABLE dừng trước khi nạp. Neo4j kiểm tra store trước khi dừng service, bulk import offline, khởi động rồi tạo constraint và relationship range index, đợi ONLINE. Báo cáo importer ở /data/aml-import.report trong volume. Hai bộ nạp kiểm tra số lượng thực tế theo manifest, chỉ ghi biên nhận import khi khớp.

Khi cần nạp lại, chạy `docker compose stop` khi COMPOSE_PROJECT_NAME vẫn trỏ tới project cũ, rồi đặt **cả** COMPOSE_PROJECT_NAME và EXPERIMENT_ID thành tên mới, ví dụ `export EXPERIMENT_ID=hi-small-02 COMPOSE_PROJECT_NAME=aml-hi-small-02`. Có thể dùng lại PREPARED_DIR đã hoàn tất nếu nguồn và cách chuẩn bị không thay đổi. Nếu một bước đã commit trước khi bước kế tiếp lỗi, volume cũ vẫn giữ dữ liệu để chẩn đoán. Loader từ chối import lặp khi đã có biên nhận; PostgreSQL từ chối tạo bảng đã tồn tại và Neo4j từ chối import vào store đã có database.

## 4. Kiểm chứng ID, benchmark và lấy plan

Sau bước nạp, Neo4j đang chạy. Các lệnh sau chỉ chạy một DBMS tại một thời điểm.

```bash
(
set -e  # Dừng khối lệnh nếu verify hoặc khởi động service lỗi.
python runner.py verify --dbms neo4j --cases "$CASES" --timeout-ms 900000
docker compose stop neo4j
docker compose up -d --wait postgres
python runner.py verify --dbms postgres --cases "$CASES" --timeout-ms 900000
)
```

Khối verify trên cho phép mỗi truy vấn chạy tối đa 900.000 ms (15 phút); mặc định runner là 120.000 ms (2 phút). Sau lần verify đầu tiên, báo cáo có thể là `pending`. Sau khi verify cả hai hệ, mọi báo cáo `results/$EXPERIMENT_ID/verify/<case>.json` phải có trạng thái `match` mới benchmark được. Dừng tại đây nếu có lỗi hoặc `mismatch`; không chạy tiếp các khối bên dưới. Sửa cases.json trước verify; `currency` phải đúng chuỗi trong CSV (ví dụ `US Dollar`), seed Q2 dùng đúng cặp chuỗi `[bank_id, account_id]` trong dữ liệu accounts.

PostgreSQL đang chạy sau đoạn trên. Đo lần lượt từng hệ:

```bash
(
set -e
python runner.py benchmark --dbms postgres --cases "$CASES" --timeout-ms 900000
python runner.py collect-plans --dbms postgres --cases "$CASES" --timeout-ms 900000
docker compose stop postgres
docker compose up -d --wait neo4j
python runner.py benchmark --dbms neo4j --cases "$CASES" --timeout-ms 900000
python runner.py collect-plans --dbms neo4j --cases "$CASES" --timeout-ms 900000
docker compose stop neo4j
python runner.py summarize
)
```

Runner yêu cầu chỉ service đang đo chạy trong project Compose hiện tại và từ chối benchmark lần nữa cho cùng DBMS trong cùng experiment. `summarize` đọc các file kết quả, không cần database đang chạy.

Các lệnh verify, benchmark và collect-plans phía trên cùng đặt timeout 900.000 ms mỗi truy vấn; mặc định runner khi không truyền tùy chọn vẫn là 120.000 ms. Giữ cùng timeout cho hai hệ khi benchmark. Mỗi case được chạy 5 warm-up và 15 lượt đo, nên thời gian toàn bộ thí nghiệm phụ thuộc độ khó của từng seed và cửa sổ thời gian. Không thay đổi case hoặc truy vấn trong experiment đã có `experiment.json`; tối ưu câu truy vấn cần một experiment mới.

Mặc định mỗi case chạy 5 warm-up và 15 lượt đo. Để smoke test nhanh, thêm `--warmup 1 --repetitions 3` vào cả hai lệnh benchmark. Đặt `--seed 42 --timeout-ms 120000` để đổi hạt giống xáo trộn hoặc timeout phía DBMS. Mỗi repetition xáo thứ tự case. Truy vấn chạy trên kết nối đã mở và chỉ đo thời gian xử lý phía server (`EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, FORMAT JSON)` gồm `Planning Time + Execution Time` với PostgreSQL; `result_available_after + result_consumed_after` từ `ResultSummary` với Neo4j). Dùng lại kết nối giữa các lượt thành công và mở kết nối mới ngoài phép đo sau lỗi. Timeout phía server có thể được phát hiện muộn hơn thời hạn cấu hình. Các lượt đo dùng cache của phiên đã warm-up.

**Case và ngữ nghĩa.** Sửa cases.json trước verify. Chọn t0, t1 theo giờ nguồn, t0 bao gồm và t1 loại trừ. Q1 lọc amount_paid > threshold theo payment_currency. Nhập ngưỡng bằng đơn vị tiền gốc, ví dụ 1000 hoặc 1000.25 cho US Dollar. CSV đầu ra giữ nguyên cách viết amount_paid và amount_received. PostgreSQL dùng double precision và Neo4j dùng double nên cả hai so sánh theo cùng độ chính xác dấu phẩy động 64 bit. Các giá trị thập phân cực gần nhau có thể được lưu thành cùng một số. Với dữ liệu đã nạp theo schema cũ, chạy lại prepare vào thư mục mới và import bằng project Compose cùng EXPERIMENT_ID mới. Q2 dùng seed là cặp chuỗi [bank_id, account_id] từ accounts.csv, runner tra khóa trong DBMS đang đo. Bộ `cases.json` mặc định gồm 28 case (4 Q1, 20 Q2 phân tầng theo 10 seed bậc thưa/trung bình/hub phủ đều 4 cửa sổ 1h/12h/24h/48h với các độ sâu k=1,2,3,6,12, và 4 Q3). Dùng cùng seed, cửa sổ và thay k trong các case trace. Q2 trả tập tài khoản khác seed đến được theo ít nhất một đường có hướng không lặp nút dài từ 1 đến k cạnh; không trả từng đường đi. Q3 lấy ba tài khoản phân biệt, bắt đầu ở khóa nhỏ nhất, giữ chiều ngược và từng tổ hợp cạnh. Q2/Q3 cho phép timestamp theo mọi thứ tự trong cửa sổ. Nhãn is_laundering chỉ dùng đối soát.

## 5. Đọc kết quả

Mỗi experiment có thư mục results/<EXPERIMENT_ID>/.

| File                                | Nội dung                                                                                                                                                                                                                                                                                       |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| experiment.json, import-{dbms}.json | Case, hash truy vấn/nguồn, đối soát, phiên bản, index thực, image ID, named volume, giới hạn CPU/RAM thực tế                                                                                                                                                                                   |
| verify/{dbms}/{case}.jsonl          | Toàn bộ ID chuẩn hóa thành mảng JSON, sắp xếp theo byte ngoài RAM bằng SQLite, giữ số lần xuất hiện                                                                                                                                                                                            |
| verify/{case}.json                  | Số dòng, SHA-256 từng hệ, trạng thái pending/match/mismatch và tối đa 20 dòng lệch                                                                                                                                                                                                             |
| settings-{dbms}.json, runs.csv      | Tham số đo, acc_key đã ánh xạ và thứ tự thực tế. Mỗi dòng là một lượt (`timing_scope=server`, tách `server_plan_or_avail_ms` và `server_exec_or_consumed_ms`), gồm cả timeout và lỗi                                                                                                     |
| summary.csv                         | p50, p95, IQR (ms) theo `timing_scope`, số lượt đo/thành công/timeout/lỗi, trạng thái đối soát, tỷ số p50 PostgreSQL / Neo4j và chỉ số từ `plans/` (`plan_db_hits`, `plan_memory_bytes`, `plan_temp_written_blocks`)                                                                       |
| q2_by_k.csv                         | Bảng vẽ p50 theo k, DBMS, seed và cửa sổ                                                                                                                                                                                                                                                       |
| plans/{dbms}/{case}.json            | EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) hoặc cây PROFILE, thu ở lượt riêng                                                                                                                                                                                                                     |
| import_timings.csv                  | Millisecond theo bulk/index. PostgreSQL bulk gồm exec psql, DDL bảng và COPY; indexes gồm constraint, index, ANALYZE và commit. Neo4j bulk gồm tạo/chạy container offline và ghi store; indexes gồm DDL và thời gian chờ index. Khởi động service, hậu kiểm và đo dung lượng nằm ngoài hai pha |
| storage.csv                         | Byte sau tạo index. PostgreSQL dùng pg_database_size(aml). Neo4j dùng du -sb trên thư mục store neo4j gồm index. Phạm vi volume dùng du -sb trên mount /data hoặc PGDATA, gồm cả file ngoài database như log giao dịch. Đây là kích thước biểu kiến, khác dung lượng vật lý của Docker Desktop |

Phân vị nội suy tuyến tính tại vị trí (n−1)×p của các lượt measure thành công, IQR = p75−p25. Case thiếu p50 hợp lệ ở một hệ hoặc lệch đầu ra sẽ để trống tỷ số. Tỷ số lớn hơn 1 nghĩa là PostgreSQL có p50 cao hơn trong case đó. Dùng thí nghiệm HI-Small đã đối soát để kết luận hiệu năng trên HI-Small.

## 6. Khi import chạy lâu hoặc chưa có đầu ra

Trong một terminal WSL khác, kiểm tra Docker trước:

```bash
timeout 15s docker version
timeout 15s docker ps -a
```

Nếu các lệnh này cũng không phản hồi, xử lý kết nối Docker trong WSL trước khi kiểm tra tốc độ COPY. Mở Docker Desktop → Troubleshoot → Restart Docker Desktop, chờ khởi động xong rồi chạy lại hai lệnh kiểm tra. Tham khảo [hướng dẫn xử lý sự cố Docker Desktop](https://docs.docker.com/desktop/troubleshoot-and-support/troubleshoot/).

Nếu Docker phản hồi, tại thư mục repo trong terminal kiểm tra, nạp `.env` và đặt COMPOSE_PROJECT_NAME **giống terminal đang import** (thay tên ví dụ dưới đây nếu bạn đã đổi):

```bash
cd ~/IMB_AML  # Thay bằng đường dẫn repo của bạn trong WSL.
set -a
source .env
set +a
export COMPOSE_PROJECT_NAME=aml-hi-small-01
docker compose ps -a
docker compose logs --tail 50 postgres
docker compose top postgres
```

Nếu cần tách riêng bước khởi động để chẩn đoán trước khi bắt đầu import PostgreSQL, dùng:

```bash
docker compose up -d --wait --wait-timeout 120 postgres
```

Lệnh này chỉ khởi động PostgreSQL và chờ service sẵn sàng; sau khi thành công mới chạy `bash import-postgres.sh`. Script vẫn gọi bước khởi động trước khi nạp. Theo [tài liệu Compose](https://docs.docker.com/reference/cli/docker/compose/up/), `--wait-timeout` giới hạn thời gian chờ service sẵn sàng; không đặt timeout cho COPY hoặc tạo index. Chỉ dùng lệnh chẩn đoán này cho PostgreSQL: Neo4j cần store chưa có database trước lần bulk import đầu tiên.

Khi PostgreSQL đã sẵn sàng, kiểm tra câu SQL đang chạy:

```bash
docker compose exec -T postgres psql -X -U aml -d aml -c "SELECT pid, state, wait_event_type, wait_event, now() - query_start AS elapsed, left(query, 200) AS query FROM pg_stat_activity WHERE datname = 'aml' AND pid <> pg_backend_pid() AND state <> 'idle';"
```

Đầu ra giúp phân biệt COPY, tạo index, ANALYZE hoặc chờ khóa/I/O; PID của chính lệnh kiểm tra được loại khỏi kết quả. Giữ nguyên tiến trình import đang chạy khi kiểm tra; tránh chạy thêm một lệnh import đồng thời.

Nếu PostgreSQL báo không bind được cổng 5432, chọn một cổng host còn trống, ví dụ 15432. Thêm hoặc sửa `PG_PORT=15432` trong `.env`, rồi nạp lại trong terminal hiện tại trước khi import:

```bash
set -a
source .env
set +a
export PG_PORT=15432
docker compose up -d --wait --wait-timeout 120 postgres
```

Giữ cùng PG_PORT cho loader và runner. Cổng PostgreSQL bên trong container vẫn là 5432. Nếu chưa có `results/$EXPERIMENT_ID/import-postgres.json`, hoàn tất `bash import-postgres.sh` trước khi verify PostgreSQL. File này được loader tạo sau khi nạp và hậu kiểm thành công; không tự tạo biên nhận để bỏ qua bước import.

Nếu log đã có COPY, COMMIT, toàn bộ index và VACUUM nhưng lỗi ở bước kết nối/hậu kiểm cuối, sửa `.env` và biến đã export trước. Không chạy lại import thông thường vì các bảng đã tồn tại. Với cùng EXPERIMENT_ID, COMPOSE_PROJECT_NAME và PREPARED_DIR của lần nạp đó, dùng:

```bash
python loader.py postgres --finalize-only
```

Chế độ này chỉ hoàn tất hậu kiểm và ghi biên nhận, không CREATE/COPY lại dữ liệu. Nó yêu cầu cả pha bulk và indexes đã được ghi trong `import_timings.csv`, đối chiếu số lượng với manifest, kiểm tra đủ index và các constraint đã được validate. Nếu các điều kiện không đạt, lệnh dừng và không tạo biên nhận. `--finalize-only` hiện chỉ hỗ trợ PostgreSQL.

## 7. Truy vấn và index tối ưu

Q1 giữ nguyên các bộ lọc và ID giao dịch đầu ra. PostgreSQL dùng index `(currency, ts, amount_paid) INCLUDE (txn_id)`. Neo4j dùng range index trên cùng ba thuộc tính; điều kiện ngưỡng tiền có thể vẫn là Filter sau index seek. Không thêm LIMIT, lấy mẫu hoặc đổi sang COUNT trong truy vấn được đo.

Q2 chỉ cần tập tài khoản đích. Mọi đường đi có vòng lặp đến một tài khoản khác seed đều rút được thành đường không lặp nút dài không hơn đường ban đầu; vì vậy tập tài khoản đến được trong tối đa k bước không đổi khi bỏ việc lưu từng đường. PostgreSQL dùng `UNION` trên `(dst, depth)` để loại các trạng thái trùng, thay vì `UNION ALL` kèm mảng path. Neo4j dùng `RETURN DISTINCT` trên nút đích, loại seed và để planner dùng BFS pruning. Các cạnh vẫn được lọc thời gian trong quá trình mở rộng. Tối ưu này dựa vào việc chỉ trả tập tài khoản và không yêu cầu timestamp tăng dần; không áp dụng nguyên xi nếu sau này cần trả từng đường, số đường hoặc điều kiện phụ thuộc lịch sử đường đi.

Q3 Cypher đặt điều kiện thời gian riêng cho từng cạnh, để planner lọc trước khi ghép các nhánh. Loader tạo thêm range index `TRANSFER(ts)` và index `Account(bank_id, account_id)` cho bước tra seed. PostgreSQL giữ các phép join theo cạnh, bổ sung partial covering index cho cạnh đầu có `src_key < dst_key`, index `(src_key, dst_key, ts)` cho cạnh đóng chu trình, và covering index `(src_key, ts)` cho bước mở rộng. Sau COPY và tạo index, `VACUUM (ANALYZE, PARALLEL 0)` cập nhật visibility map phục vụ index-only scan và tránh vượt shared memory mặc định của Docker. Q3 giữ nguyên mọi tổ hợp giao dịch song song và hai hướng chu trình; không dùng DISTINCT để làm mất kết quả.

Đổi query hoặc index phải dùng experiment và project Compose mới, ví dụ `hi-small-opt-01` / `aml-hi-small-opt-01`, rồi import và verify lại trước khi benchmark. Có thể dùng lại các CSV đã prepare. Không tiếp tục benchmark bản tối ưu vào `runs.csv` của bản cũ. Nếu chỉ dừng benchmark giữa chừng, các dòng đã ghi vẫn được giữ nhưng chưa phải thí nghiệm đủ số lượt.

Kiểm thử ngữ nghĩa bằng thư viện chuẩn:

```bash
python -m unittest discover -s tests -v
```

`tests/test_queries.py` kiểm tra biên thời gian, ngưỡng tiền, seed, khóa bigint, cạnh song song, vòng lặp, chu trình hai chiều và 30 đồ thị ngẫu nhiên với bộ tìm đường độc lập. Có thể chạy lại chính các contract này trên PostgreSQL và Neo4j bằng các biến `QUERY_TEST_POSTGRES_DSN`, `QUERY_TEST_NEO4J_URI`, `QUERY_TEST_NEO4J_PASSWORD` (và user/database tùy chọn). PostgreSQL dùng bảng TEMP; Neo4j dùng nhãn/type riêng và rollback dữ liệu sau mỗi test. Chạy kiểm thử tích hợp khi hai hệ đều sẵn sàng và không có benchmark đang đo.

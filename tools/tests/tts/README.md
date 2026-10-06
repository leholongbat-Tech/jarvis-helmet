# Bộ đo TTS (`tools/tests/tts`)

Đo chuỗi TTS của Jarvis qua **tts-router** (SV4 §2 và tiêu chí nghiệm thu 1–2): thời gian tới byte âm thanh đầu tiên (TTFA),
tổng thời gian, lưu WAV để nghe, tự kiểm tra âm thanh có tiếng và độ dài hợp lý. Có thêm chế độ quét giọng để chọn giọng nam
trầm. Kiến trúc TTS, biến môi trường và cách đổi giọng: `server/tts/README.md`.

| File | Nội dung |
|---|---|
| `bench.py` | Script đo (cần `httpx`, `numpy` – đều có trong `.venv` của repo; Python ≥ 3.9) |
| `sentences.txt` | 20 câu thử, chia nhóm |
| `out/` | Thư mục WAV mặc định (tạo khi chạy; **không commit** – thư mục này chưa có trong `.gitignore`) |

## 1. Chuẩn bị

Router không mở cổng ra host trong cấu hình thường. Chạy stack kèm file debug để có `127.0.0.1:8880` (chạy bench trên chính
máy server, hoặc trên máy dev với Docker Desktop):

```bash
cd server
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d
# Docker Desktop: docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.debug.yml up -d
curl -s http://127.0.0.1:8880/health
```

Cổng khác: đặt `DEBUG_TTS_PORT` trong `server/.env` và truyền `--url http://127.0.0.1:<cổng>`. Trên máy không có `.venv` của repo:
`pip install httpx numpy` rồi chạy bằng Python bất kỳ ≥ 3.9.

## 2. `sentences.txt`

- Mỗi dòng một câu, dòng trống bị bỏ qua. Dòng bắt đầu bằng `#` là chú thích.
- Dòng `# @<nhóm>` đặt tên nhóm cho các câu phía sau (cột `grp` trong bảng kết quả).
- Câu được đánh số 1..N theo thứ tự (dùng cho `--only`).

| Nhóm | Câu số | Nội dung |
|---|---|---|
| `vi` | 1–4 | Tiếng Việt thuần ("Vâng, thưa ngài." …) |
| `mixed` | 5–9 | Trộn Việt–Anh, viết tắt (combat mode, WiFi, USB, LED, OK, J.A.R.V.I.S, AI, HUD) |
| `en` | 10–13 | Tiếng Anh thuần (đi Kokoro nếu bật `TTS_ENGLISH_ENGINE=kokoro`) |
| `numbers` | 14–19 | Giờ, ngày, điện áp, dòng, %, mAh, °C, km/h, MB/GB, kHz |
| `long40` | 20 | Câu dài 40 từ |

Có thể dùng file câu khác với `--sentences <file>` (cùng định dạng).

## 3. Cách chạy

```bash
# Toàn bộ 20 câu (giọng = TTS_VOICE của router), WAV vào tools/tests/tts/out/
.venv/Scripts/python tools/tests/tts/bench.py --url http://127.0.0.1:8880

# Vài câu, mỗi câu 3 lần (trung vị trên mọi lần), ghi JSON
.venv/Scripts/python tools/tests/tts/bench.py --only 1,5,14,20 --repeat 3 --json out.json

# In văn bản router đã chuẩn hóa (kiểm tra cách đọc số/đơn vị)
.venv/Scripts/python tools/tests/tts/bench.py --show-text

# Thử một giọng cụ thể
.venv/Scripts/python tools/tests/tts/bench.py --voice "Thiền Tâm Đức" --out tools/tests/tts/out/thien_tam_duc

# Quét giọng nam của VieNeu, xếp theo cao độ
.venv/Scripts/python tools/tests/tts/bench.py --voices-scan
```

(Trên Linux/macOS dùng `.venv/bin/python`.)

Tham số (đã đối chiếu `--help`):

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--url` | `http://127.0.0.1:8880` | Địa chỉ gốc của tts-router |
| `--sentences` | `sentences.txt` cạnh script | File câu thử |
| `--voice` | không gửi (router dùng `TTS_VOICE`) | Giọng yêu cầu |
| `--rate` | 24000 | `sample_rate` yêu cầu (đúng tần số mũ nhận) |
| `--only` | – | Danh sách số câu, ví dụ `1,5,20` |
| `--limit` | 0 (= tất cả) | Chỉ N câu đầu |
| `--repeat` | 1 | Số lần chạy mỗi câu; WAV có hậu tố `_r1`, `_r2`… |
| `--warmup` | 1 | Số request "Xin chào." không tính giờ trước khi đo |
| `--out` | `tools/tests/tts/out` | Thư mục WAV |
| `--json` | – | Ghi toàn bộ kết quả ra file JSON |
| `--show-text` | tắt | In văn bản đã chuẩn hóa của từng câu |
| `--ttfa-target` | 300 | Ngưỡng TTFA (ms) của tiêu chí SV4 |
| `--short-words` | 15 | Câu "ngắn" = ≤ N từ (đếm trên câu gốc) |
| `--timeout` | 60 | Timeout mỗi request (giây) |
| `--voices-scan` | tắt | Chế độ quét giọng (mục 5) |
| `--gender` | `male` | Lọc giọng khi quét: `male` \| `female` \| `all` |
| `--scan-text` | "Vâng, thưa ngài. Hệ thống đã sẵn sàng, tôi luôn ở đây để phục vụ ngài." | Câu dùng khi quét |

## 4. Đọc kết quả

Đầu tiên script gọi `/health` của router và in kết quả (không gọi được → thoát mã 2). Mỗi câu gửi
`POST /v1/audio/speech` với `response_format: pcm`, `stream: true`, đo:

| Cột | Ý nghĩa |
|---|---|
| `words` | Số từ của câu gốc |
| `TTFA ms` | Từ lúc gửi request tới chunk âm thanh đầu tiên (gồm chuẩn hóa ở router, TTFA của VieNeu/Kokoro, mạng) |
| `total ms` | Tới khi nhận hết âm thanh |
| `audio s` | Độ dài âm thanh nhận được |
| `RTF` | `total / audio`; > 1 nghĩa là tổng hợp chậm hơn thời gian thực (mũ sẽ phải chờ giữa chừng) |
| `dBFS` | Mức RMS của cả đoạn |
| `route` | Header `X-TTS-Route` của router: `vieneu`, `kokoro`, `vieneu+kokoro`… |
| `ok` | `ok` hoặc `FAIL` kèm lý do ở dòng `->` |

Lý do `FAIL`: `request failed` (lỗi mạng), `HTTP <mã>`, `no audio`, `silent` (RMS < −45 dBFS hoặc < 20 % khung 20 ms có tiếng
trên −40 dBFS), `duration ...` (ngoài khoảng 0,12–1,0 giây cho mỗi từ **sau chuẩn hóa** – bắt lỗi đọc thiếu hoặc lặp).

Cuối bảng:

```
requests=20 ok=20  median TTFA=... ms (p90 ... ms)  median total=... ms  median RTF=...
SV4 criterion 1: TTFA <= 300 ms for sentences <= 15 words: N/M
WAV files: tools/tests/tts/out
```

- WAV: `<out>/<số câu 2 chữ số>_<đầu câu>.wav`, 16 bit mono, tần số `--rate`. Nghe từng file để chấm tiêu chí 2 (đọc đúng
  20/20 câu, không sai số/đơn vị, từ tiếng Anh nghe hiểu được); dùng `--show-text` để xem router đã đọc số thành chữ ra sao.
- `--json`: `{"summary": {...}, "results": [{index, group, text, words, status, ttfa_ms, total_ms, audio_s, rtf, rms_dbfs,
  peak_dbfs, voiced_ratio, route, normalized, spoken_words, wav, error, checks}, ...]}` – dán `summary` vào `docs/test-report.md`.
- Mã thoát: 0 khi mọi câu `ok`; 1 khi có câu `FAIL`; 2 khi router không trả lời. Tiêu chí TTFA chỉ được in ra, **không** ảnh
  hưởng mã thoát.
- Chỉ dùng số đo trên **server thật** (GPU thật, stack đầy đủ) để đánh giá SV4: GPU yếu hoặc đang bị service khác dùng cho kết quả
  chậm hơn nhiều (RTF > 1). Mốc tham khảo (không phải nghiệm thu): máy dev GTX 1660 SUPER 6 GB, VieNeu chạy một mình
  (`VIENEU_MAX_STREAMS=2`), câu 1, 5, 14, 20, giọng "Xuân Vĩnh" – TTFA trung vị ≈ 160 ms, RTF ≈ 0,75, 4/4 `ok` (2026-10-06).

## 5. Quét giọng (`--voices-scan`)

Lấy danh sách `GET /v1/voices` của router, giữ giọng `engine = vieneu` đúng `--gender` (mặc định `male`, giọng clone cần được
tạo với `--gender male`), đọc `--scan-text` bằng từng giọng, lưu `<out>/voices/<tên giọng>.wav` và in bảng xếp theo **cao độ
trung vị F0** tăng dần (tự tương quan, khung 40 ms; không đo được thì để `-`):

```
 F0 Hz  voice             TTFA ms  audio s  description / wav
   104  Xuân Vĩnh             ...     ...   Nam · Bắc · Phong cách tự nhiên  tools/tests/tts/out/voices/Xuân_Vĩnh.wav
   ...
Lower F0 = deeper voice. Listen to the WAVs, then set TTS_VOICE in server/.env.
```

F0 thấp = giọng trầm, nhưng VieNeu lấy mẫu ngẫu nhiên nên mỗi lần lệch vài Hz: luôn nghe WAV (tốt nhất qua loa trong mũ) trước khi
chọn. Cách đặt giọng đã chọn và clone giọng: `server/tts/README.md` mục 6.

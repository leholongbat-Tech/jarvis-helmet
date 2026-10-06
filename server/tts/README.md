# TTS – tổng hợp giọng nói (`server/tts`)

Chuỗi TTS của Jarvis (SV4, quyết định D18 trong hợp đồng triển khai):

```
xiaozhi-server ── provider jarvis_stream ──► tts-router :8880 ──► vieneu :8000   (VieNeu-TTS v3 Turbo, GPU, mặc định)
 (Opus 24 kHz xuống mũ)   POST /v1/audio/speech    │  chuẩn hóa văn bản      └─► kokoro :8880 (tùy chọn, profile english-tts,
                           PCM s16le 24 kHz         │  định tuyến từng câu         chỉ cho câu thuần tiếng Anh)
                                                    └─ trả PCM ngay khi có chunk đầu tiên
```

| Thành phần | Đường dẫn | Vai trò |
|---|---|---|
| Image VieNeu | `server/tts/vieneu/` | API kiểu OpenAI `POST /v1/audio/speech` (streaming), build từ VieNeu-TTS tag `app-v0.18.9` |
| tts-router | `server/tts/router/` | FastAPI: bỏ markdown/emoji, tách câu, đọc số/đơn vị/viết tắt tiếng Việt, chọn engine cho từng câu, stream PCM |
| Provider xiaozhi | `server/xiaozhi/providers/tts/jarvis_stream.py` | Tách câu theo luồng chữ của LLM, gọi router từng đoạn, mã hóa Opus 60 ms gửi xuống mũ |
| Bộ đo | `tools/tests/tts/` | `bench.py` (TTFA, tổng thời gian, lưu WAV, quét giọng) + `sentences.txt` (20 câu) – xem `tools/tests/tts/README.md` |
| Test | `server/tests/test_tts_router.py`, `test_normalize_vi.py`, `test_jarvis_stream_segmentation.py`, `test_tts_provider_runtime.py` | Mục 8 |

Không service nào trong số này mở cổng ra host: chỉ xiaozhi publish cổng (D21). Muốn gọi router từ máy host để thử,
dùng `docker-compose.debug.yml` (mở `127.0.0.1:${DEBUG_TTS_PORT:-8880}`).

---

## 1. Image VieNeu (`server/tts/vieneu`)

### 1.1 Nguồn và cách build

`server/tts/vieneu/Dockerfile` làm lại đúng `docker/Dockerfile.gpu`, target `prod`, của VieNeu-TTS tag **`app-v0.18.9`**
(commit `85344322b7258b4e25479b692e8e3396baf9db34`): CUDA 12.8 runtime trên Ubuntu 24.04, Python 3.12, dependency cài bằng
`uv sync --frozen --no-dev --extra cuda --no-install-project --no-build` (torch 2.8.0+cu128, chạy được từ sm_75 – GTX 16xx –
tới sm_120 – RTX 50xx), app chạy thẳng từ cây mã nguồn, user không phải root `app` (uid 1000).

Các điểm khác upstream (đánh dấu `jarvis:` trong Dockerfile):
- Mã nguồn được `git clone --branch app-v0.18.9` trong stage riêng và **kiểm tra đúng commit** (sai commit → build lỗi).
- Base image và `uv` ghim theo digest (base CUDA 12.8.2 giống `server/asr`).
- Thư mục cache HuggingFace và `/voices` tạo sẵn, thuộc user `app` (volume mới tạo sẽ kế thừa quyền).
- Lệnh mặc định: `python -m apps.openai_speech` trên `0.0.0.0:8000`, có healthcheck.
- Wrapper `/opt/jarvis/jarvis_entrypoint.sh` nạp file giọng đã clone (mục 6.3); script `clone_voice.py`, `healthcheck.py` ở `/opt/jarvis/`.

```bash
# Từ thư mục gốc repo
docker build -t jarvis/vieneu:dev server/tts/vieneu
# hoặc qua compose (thư mục server/)
docker compose build vieneu
```

- Build arg `APT_CHECK_DATE` (mặc định `true`): chỉ đặt `--build-arg APT_CHECK_DATE=false` khi đồng hồ máy build bị lệch
  làm apt báo Release file "not valid yet" (chữ ký vẫn được kiểm tra).
- Image khá nặng (bản build trên máy dev: khoảng 17,5 GB). Lần chạy đầu tải model + codec (~0,6 GB) về cache HF rồi warm-up
  trước khi mở cổng, nên healthcheck có `start_period` dài (600 s trong Dockerfile, 900 s trong compose).

### 1.2 Biến môi trường của container `vieneu`

Container `vieneu` **không** nhận `env_file` (không có khóa API nào bên trong), chỉ nhận các biến ghi rõ trong compose.

| Biến | Giá trị trong image / compose | Ý nghĩa |
|---|---|---|
| `HOST`, `PORT` | `0.0.0.0`, `8000` | Địa chỉ lắng nghe (upstream mặc định chỉ loopback) |
| `VIENEU_MAX_STREAMS` | 4 (compose: `${VIENEU_MAX_STREAMS:-4}`; `docker-compose.local.yml`: 2) | Số luồng tổng hợp chạy song song trên GPU |
| `VIENEU_QUEUE` | 8 (compose: `${VIENEU_QUEUE:-8}`) | Số request được xếp hàng chờ; đầy → HTTP 429 |
| `VIENEU_QUEUE_TIMEOUT` | 10 | Thời gian chờ slot tối đa (giây), quá → 429 |
| `VIENEU_WATERMARK` | 0 | Tắt watermark (bản cài `cuda` không có thư viện watermark) |
| `VIENEU_VOICES_FILE` | `/voices/voices_v3_turbo.json` | File giọng tùy chỉnh (mục 6.3); không có file → dùng giọng có sẵn |
| `HF_HUB_OFFLINE` | compose: `${TTS_HF_OFFLINE:-0}` | Đặt `TTS_HF_OFFLINE=1` trong `server/.env` **sau khi** cache đã có model (VieNeu không ghim được revision model, codec tải remote code) |
| `VIENEU_DEVICE` | `auto` (upstream); `docker-compose.local.yml`: `${LOCAL_VIENEU_DEVICE:-auto}` | `auto` \| `cuda` \| `cpu` |
| `VIENEU_BACKEND`, `VIENEU_PRECISION`, `VIENEU_ONNX_DIR`, `VIENEU_API_KEY` | không đặt | Biến upstream (`apps/openai_speech.py`); `VIENEU_API_KEY` bật xác thực Bearer, compose không truyền biến này (mạng nội bộ) |

Volume: `vieneu_hf_cache` → `/home/app/.cache/huggingface` (volume riêng, không dùng chung `hf_cache` của các service khác, để
thư mục luôn thuộc user `app` uid 1000 của VieNeu) và `vieneu_voices` → `/voices`. Tên thật của volume có tiền tố project:
`jarvis_vieneu_hf_cache`, `jarvis_vieneu_voices`.

### 1.3 Chạy riêng để thử

```bash
docker run --rm --gpus all -p 127.0.0.1:18000:8000 \
    -v jarvis_vieneu_hf_cache:/home/app/.cache/huggingface jarvis/vieneu:dev
curl -s http://127.0.0.1:18000/health      # {"status":"ok","backend":...,"max_streams":4,...}
curl -s http://127.0.0.1:18000/v1/voices
```

- Trên server (Docker Engine dùng CDI, D20) thay `--gpus all` bằng `--device nvidia.com/gpu=all`.
- Lệnh trên dùng lại cache model trong volume của compose (đã có sau lần `docker compose up` đầu tiên). Chưa chạy compose lần nào thì
  đặt tên volume khác: volume do `docker run` tạo trước sẽ làm compose cảnh báo "already exists but was not created by Docker Compose".
- Git Bash trên Windows: đặt `MSYS_NO_PATHCONV=1` trước các lệnh `docker run` có đường dẫn trong container.
- Cổng 8000 của host server đã thuộc dự án khác (D21): luôn publish sang cổng khác (ví dụ 18000) và chỉ trên `127.0.0.1`.
- `/health` trả 503 khi bộ lập lịch stream trên GPU đã chết: VieNeu không tự hồi phục, phải khởi động lại container (mục 9).

---

## 2. tts-router (`server/tts/router`)

FastAPI chạy trên `:8880` (`python -m tts_router`), image `jarvis/tts-router:dev` (Python 3.12, user không phải root).
Healthcheck gọi `/health` (chỉ 200 khi VieNeu trả lời).

### 2.1 Xử lý một request

1. **Bỏ markup** (`strip_markup`): code block, link (giữ chữ), URL, tiêu đề/gạch đầu dòng markdown, `**đậm**`, bảng (dấu `|`
   thành dấu phẩy), emoji và ký tự điều khiển. Xuống dòng được giữ (là ranh giới câu).
2. **Tách câu** (`textsplit.split_sentences`): ranh giới là xuống dòng, dấu câu CJK, hoặc chuỗi `. ! ? ; : …` theo sau là khoảng
   trắng. Không tách trong số thập phân/giờ (`5.02`, `3,5`, `14:32`), sau chữ viết tắt (`TP.`, `Mr.`, `v.v.`…), sau chữ tắt dạng
   `J.A.R.V.I.S.` khi từ tiếp theo viết thường, sau chữ cái đầu tên viết hoa, hoặc số thứ tự đầu dòng (`1. `). Dấu `:` và `;`
   ASCII chỉ tách câu khi phần đã đọc có chữ tiếng Việt có dấu: `Sir, the battery level is: 85%.` vẫn là một câu tiếng Anh
   (không đổi giọng giữa câu, số đọc bằng tiếng Anh), còn `Lưu ý: pin yếu` vẫn tách.
3. **Nhận ngôn ngữ từng câu** (mục 2.3) rồi **chuẩn hóa** câu đó bằng `Normalizer.vi()` hoặc `Normalizer.en()` (mục 2.2).
   Câu không còn chữ/số nào để đọc bị bỏ.
4. **Gom nhóm**: các câu liền nhau cùng engine được ghép thành một request tới backend.
5. **Stream**: mở stream tới backend của nhóm đầu. Lỗi xảy ra *trước* byte âm thanh đầu tiên được trả về thành mã HTTP lỗi
   (mục 2.5). Sau đó mỗi chunk PCM nhận được được chuyển tiếp ngay (căn theo mẫu 16 bit, resample nếu cần). Giữa hai nhóm
   (hai request khác nhau) chèn `TTS_SENTENCE_GAP_MS` (120 ms) im lặng. Nhóm sau bị lỗi thì bỏ qua và ghi log, phần còn lại vẫn đọc.
6. Client ngắt kết nối (ví dụ mũ bị ngắt lời) → router đóng stream tới backend để VieNeu dừng tổng hợp.

Mỗi request ghi một dòng log, ví dụ:
`tts-1a2b3c4d done: route=vieneu ttfb=95ms total=1830ms audio=2960ms rate=24000 errors=0`.

### 2.2 Chuẩn hóa văn bản tiếng Việt

Ví dụ lấy đúng từ `normalize_vi.py` (đã chạy kiểm tra):

| Đầu vào | Router gửi cho VieNeu |
|---|---|
| `Bây giờ là 14:32, thứ Hai ngày 06/10/2026.` | `Bây giờ là mười bốn giờ ba mươi hai phút, thứ Hai ngày sáu tháng mười năm hai nghìn không trăm hai mươi sáu.` |
| `Điện áp 5,02 V, dòng 1.250 mA.` | `Điện áp năm phẩy không hai vôn, dòng một nghìn hai trăm năm mươi mi li ampe.` |
| `Pin còn 85%, khoảng 2500 mAh.` | `Pin còn tám mươi lăm phần trăm, khoảng hai nghìn năm trăm mi li ampe giờ.` |
| `Nhiệt độ 31°C, tốc độ 45 km/h.` | `Nhiệt độ ba mươi mốt độ xê, tốc độ bốn mươi lăm ki lô mét trên giờ.` |
| `Phòng 105, giá 1005 đồng, 20-30% pin.` | `Phòng một trăm linh năm, giá một nghìn không trăm linh năm đồng, hai mươi đến ba mươi phần trăm pin.` |
| `Kết nối WiFi ổn định, USB và LED OK.` | `Kết nối wai phai ổn định, u ét bê và lét ô kê.` |
| `Xin chào, tôi là J.A.R.V.I.S, trợ lý AI của ngài.` | `Xin chào, tôi là Giắc vít, trợ lý ây ai của ngài.` |
| `Gọi 0912345678 lúc 7h30.` | `Gọi không chín một hai ba bốn năm sáu bảy tám lúc bảy giờ ba mươi phút.` |
| `Firmware ESP32 v2.5 chạy tốt.` | `Firmware ESP ba hai v hai chấm năm chạy tốt.` |
| `**Đã mở** mặt nạ 😀 nhé! Xem https://example.com` | `Đã mở mặt nạ nhé! Xem` |

Quy tắc chính:
- Đọc số kiểu miền Bắc: 21 "hai mươi mốt", 24 "hai mươi tư", 25 "hai mươi lăm", 105 "một trăm linh năm".
  `TTS_NUM_ZERO_TENS=lẻ` → "một trăm lẻ năm"; `TTS_NUM_THOUSAND=ngàn` → "một ngàn".
- Dấu phân cách: một dấu `.` hoặc `,` theo sau **đúng 3 chữ số** là phân cách hàng nghìn (`1.250`, `1,000`); còn lại là dấu
  thập phân (`5.02`, `3,5`). Số bắt đầu bằng `0` đọc từng chữ số (số điện thoại, mã). Số điện thoại viết theo nhóm
  (`0912 345 678`, `0912.345.678`, `028 3822 1234`, `1900 6060`) đọc từng chữ số, ngắt nghỉ giữa các nhóm; khoảng trắng
  làm dấu phân cách hàng nghìn (`1 000 000`) được bỏ trước khi đọc ("một triệu").
- Đơn vị sau số (V, mV, A, mA, mAh, W, kWh, °C, %, km/h, m/s, km, cm, mm, kg, g, GB, MB, Hz, kHz, ms, dB, đ/VNĐ, USD, `$`…),
  ngày (`06/10/2026`, `2026-10-06`, `6/10`, `10/2026`, `tháng 3/2026`, `Q1/2025` / `quý 1/2025` → "quý một năm …"; năm
  ngoài 1900–2100 là tỉ lệ: `1/1000` → "một trên một nghìn"), giờ (`14:32`, `7h30`), khoảng (`20-30%`, `1900-1945` →
  "đến"), dấu `+ × = /` giữa hai số, số âm, `thứ 2`/`hạng 1`, `tháng 4` → "tháng tư". `x/y` là điểm số cạnh "điểm",
  "chấm", "được", "đạt", "sao" (không tính "thời điểm", "địa điểm", "sao?"), phân số thường gặp (`1/2`, `3/4`…) sau "từ",
  "tới"… là ngày trừ khi đi sau động từ hay trước danh từ chỉ lượng ("chiếm tới 3/4", "sau 1/4 thế kỷ"). Sau các từ như
  phòng, tầng, lớp, căn, quốc lộ, mạng… chữ `A` hoặc `g` là ký hiệu chứ không phải ampe/gam: `phòng 12A` → "phòng mười hai
  A", `mạng 4g` → "mạng bốn G"; các đơn vị khác vẫn đọc là đơn vị ("quạt bàn 40W", "giá phòng 500.000đ"), và `dòng 2A`
  vẫn là "hai ampe".
- Viết tắt có sẵn (khớp nguyên từ, phân biệt hoa thường): `Jarvis`/`JARVIS`/`J.A.R.V.I.S` → "Giắc vít", `AI`, `WiFi`/`Wi-Fi`,
  `USB`, `OK`/`okay`, `LED`, `HUD`, `GPS`, `SOS`, `TV`, `GPU`, `CPU`, `PTT`, `Bluetooth`, `v.v.`, `TP.`.
- **Thêm/ghi đè viết tắt**: file JSON `{"vi": {"ESP": "i ét pi"}, "en": {"ESP": "E S P"}}` (hoặc phẳng `{"ESP": "i ét pi"}` cho
  tiếng Việt), đặt đường dẫn **trong container** vào `TTS_ABBREV_FILE`. Compose không mount sẵn file nào, nên cần thêm volume cho
  `tts-router` trong một file override riêng, ví dụ:

  ```yaml
  # my-override.yml (không có trong repo)
  services:
    tts-router:
      volumes:
        - ./tts/abbreviations.json:/etc/tts/abbreviations.json:ro
      environment:
        TTS_ABBREV_FILE: /etc/tts/abbreviations.json
  ```

  File lỗi chỉ ghi log `cannot load TTS_ABBREV_FILE ...` và router chạy tiếp với bảng mặc định.
- Muốn biết router đã đọc câu thành gì: header `X-TTS-Normalized` của response (mục 2.5) hoặc `bench.py --show-text`.

### 2.3 Định tuyến ngôn ngữ

Một câu là **thuần tiếng Anh** khi: không có chữ cái tiếng Việt có dấu, có ít nhất `TTS_ENGLISH_MIN_WORDS` (2) từ "giống tiếng
Anh", và số từ tiếng Anh ≥ `TTS_ENGLISH_MIN_RATIO` (0,6) × (từ Anh + từ Việt không dấu). Chữ viết tắt in hoa 2–5 ký tự, tên
`Jarvis` và chữ cái đơn được coi là trung tính.

| Câu | Kết quả |
|---|---|
| `Good evening, sir. All systems are online.` | 2 câu tiếng Anh |
| `Stealth mode engaged.` | tiếng Anh |
| `Đã kích hoạt combat mode, thưa ngài.` | tiếng Việt (có dấu) → luôn VieNeu |
| `HUD online` | tiếng Việt (`HUD` trung tính, chỉ 1 từ tiếng Anh) |

- Câu tiếng Anh dùng `Normalizer.en()` (số, đơn vị, giờ, ngày đọc bằng tiếng Anh) và được gửi tới **Kokoro chỉ khi**
  `TTS_ENGLISH_ENGINE=kokoro` **và** Kokoro đang healthy; ngược lại vẫn gửi VieNeu (VieNeu đọc được tiếng Anh, giọng lơ lớ hơn).
- Câu không có chữ cái nào (ví dụ `42.` hay `85%.` đứng riêng) theo ngôn ngữ và engine của câu liền trước trong cùng request.
- Kokoro lỗi lúc mở stream, hoặc trả 200 rồi đứt kết nối / không có âm thanh (ví dụ hết VRAM khi dùng chung GPU, trong khi
  `/health` vẫn 200) → router đánh dấu Kokoro down và, nếu nhóm đó chưa phát ra âm thanh nào, đọc lại nhóm đó bằng VieNeu.
  Nhóm đã phát được một phần thì không đọc lại (tránh nghe hai lần). Vòng kiểm tra sức khỏe (`TTS_HEALTH_INTERVAL`, 10 s)
  bật lại Kokoro khi nó trả lời.
- Trường mở rộng `language` trong request (`auto` mặc định, `vi`, `en`) ép ngôn ngữ cho mọi câu.

### 2.4 Âm thanh: PCM 24 kHz streaming

- Đầu ra: PCM s16le mono, `audio/pcm` (mặc định) hoặc `audio/wav`, tần số `sample_rate` của request (mặc định
  `TTS_SAMPLE_RATE` = 24000; cho phép 8000–48000). Xiaozhi gửi Opus 24 kHz xuống mũ (hello patch D3), nên mọi chặng đều 24 kHz.
- VieNeu sinh 48 kHz và tự resample (soxr) sang 48/24/16/8 kHz: router xin VieNeu đúng tần số nếu thuộc danh sách này, tần số
  khác thì xin 48 kHz rồi tự resample bằng soxr HQ theo từng chunk. Kokoro luôn ra 24 kHz.
- `stream: true` (mặc định): chunk được chuyển tiếp ngay khi tới; với `wav` header ghi kích thước "không xác định"
  (0xFFFFFFFF) để trình phát chạy ngay. `stream: false`: trả trọn file, WAV có kích thước chính xác.
- `speed`: **VieNeu bỏ qua tốc độ** (v3 Turbo không có điều khiển tốc độ; router trả header `X-TTS-Ignored: speed`). Chỉ Kokoro
  dùng `speed` (request hoặc `TTS_SPEED`). Tốc độ 1.05 trong kế hoạch SV4 vì vậy không áp dụng được cho giọng tiếng Việt.

### 2.5 API

`POST /v1/audio/speech` (JSON):

| Trường | Mặc định | Ghi chú |
|---|---|---|
| `input` | bắt buộc | 1–20 000 ký tự trong schema; vượt `TTS_MAX_INPUT_CHARS` (5000) → 400 |
| `voice` | `TTS_VOICE` | Tên giọng VieNeu (đúng dấu, router chuẩn hóa NFC) hoặc giọng Kokoro (`bm_george`, `bm_george(2)+bm_lewis(1)`) – mục 2.6 |
| `response_format` | `pcm` | `pcm` \| `wav`; giá trị khác → 400 |
| `sample_rate` | `TTS_SAMPLE_RATE` (24000) | 8000–48000 |
| `stream` | `true` | |
| `speed` | `TTS_SPEED` (1.0) | 0.25–4.0, chỉ Kokoro |
| `language` | `auto` | `auto` \| `vi` \| `en` (mở rộng của router) |
| `model` | – | Nhận cho tương thích, không dùng (model VieNeu lấy từ `VIENEU_MODEL`) |

Header trả về: `X-Request-Id`, `X-Sample-Rate`, `X-TTS-Route` (ví dụ `vieneu`, `vieneu+kokoro`, `none` khi không có gì để đọc –
khi đó body rỗng, hoặc chỉ header WAV), `X-TTS-Normalized` (văn bản đã chuẩn hóa, URL-encode, tối đa 1500 ký tự, các nhóm ngăn
bằng ` | `), `X-TTS-Ignored: speed`.

Mã lỗi (body dạng OpenAI `{"error": {"message": ...}}`): 400 tham số sai; 503 backend không kết nối được; 429 backend bận
(`Retry-After: 1`); lỗi 4xx của backend chuyển nguyên mã; lỗi 5xx của backend → 502.

Các endpoint khác:
- `GET /health`: kiểm tra backend ngay lúc gọi. `status` = `ok`, `degraded` (cần Kokoro – `TTS_ENGLISH_ENGINE=kokoro` – nhưng
  Kokoro down, câu tiếng Anh tạm đọc bằng VieNeu) hoặc `error` (VieNeu down). HTTP 200 khi VieNeu healthy, 503 khi không.
- `GET /livez`: chỉ kiểm tra tiến trình router còn sống.
- `GET /v1/voices`: gộp giọng VieNeu và Kokoro (`id`, `name`, `engine`, `gender`, `description`, `featured`, `aliases`) cùng
  `default_voice`, `english_voice`, `english_engine`; backend lỗi được liệt kê trong `errors`. Cache 60 s khi không lỗi.

Thử nhanh từ máy host (chạy compose kèm `docker-compose.debug.yml`):

```bash
curl -s http://127.0.0.1:8880/health
curl -s http://127.0.0.1:8880/v1/voices
cat > thu.json <<'EOF'
{"input": "Vâng, thưa ngài. Điện áp hiện tại là 5,02 V.", "response_format": "wav", "stream": false}
EOF
curl -s -X POST http://127.0.0.1:8880/v1/audio/speech -H 'Content-Type: application/json' \
     --data-binary @thu.json -o thu.wav -D -
```

Gửi JSON bằng file UTF-8 (`--data-binary @file`): trên Windows (Git Bash, PowerShell) curl nhận tham số dòng lệnh qua bảng mã ANSI nên
chữ tiếng Việt viết thẳng trong `-d '...'` bị hỏng và router trả 400 `There was an error parsing the body` (đã gặp khi kiểm tra). `-D -`
in header trả về (`X-TTS-Route`, `X-TTS-Normalized`…).

### 2.6 Chọn giọng trong router

- `voice` rỗng, `default`, `auto`, `none`, hoặc là tên giọng Kokoro → dùng `TTS_VOICE` cho VieNeu. Tên được chuẩn hóa Unicode NFC.
- VieNeu trả 400 "unknown voice" → router thử lại với `TTS_VOICE`, rồi không gửi `voice` (giọng mặc định của VieNeu, `Hải Đăng`
  hoặc `default_voice` trong file giọng), và ghi cảnh báo `vieneu: voice 'X' rejected, spoke with 'Y' instead`. Mũ vẫn nói,
  nhưng phải sửa tên giọng.
- Nhóm tiếng Anh gửi Kokoro dùng `voice` của request nếu nó có dạng giọng Kokoro, ngược lại dùng `TTS_ENGLISH_VOICE`.

### 2.7 Biến môi trường của router

Router nạp toàn bộ `server/.env` (`env_file`), nên mọi biến dưới đây đặt được trong `server/.env`. Giá trị rỗng = mặc định.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `VIENEU_URL` | `http://vieneu:8000` (compose đặt sẵn) | Địa chỉ VieNeu |
| `VIENEU_API_KEY` | rỗng | Gửi `Authorization: Bearer` tới VieNeu (chỉ cần nếu VieNeu bật `VIENEU_API_KEY`) |
| `VIENEU_MODEL` | `vieneu-v3-turbo` | Trường `model` gửi VieNeu |
| `KOKORO_URL` | rỗng = tắt Kokoro (compose đặt `http://kokoro:8880`) | Địa chỉ Kokoro |
| `TTS_VOICE` | `Hải Đăng` | Giọng VieNeu mặc định (đúng dấu). `server/.env.example` đặt `Thiền Tâm Đức` |
| `TTS_ENGLISH_ENGINE` | `vieneu` | `vieneu` \| `kokoro` (giá trị khác coi như `vieneu`) |
| `TTS_ENGLISH_VOICE` | `bm_george` | Giọng Kokoro router yêu cầu cho câu tiếng Anh |
| `TTS_SPEED` | 1.0 | Tốc độ mặc định (chỉ Kokoro) |
| `TTS_SAMPLE_RATE` | 24000 | Tần số khi request không ghi `sample_rate` |
| `TTS_CONNECT_TIMEOUT` | 3 | Giây, kết nối tới backend |
| `TTS_READ_TIMEOUT` | 20 | Giây, chờ byte âm thanh đầu tiên và khoảng cách giữa các chunk |
| `TTS_HEALTH_TIMEOUT` | 2 | Giây, mỗi lần kiểm tra sức khỏe backend |
| `TTS_HEALTH_INTERVAL` | 10 | Giây, chu kỳ kiểm tra sức khỏe nền (tối thiểu 1) |
| `TTS_SENTENCE_GAP_MS` | 120 | Im lặng chèn giữa hai nhóm câu đọc bằng hai request |
| `TTS_ENGLISH_MIN_WORDS` | 2 | Số từ tiếng Anh tối thiểu để coi là câu tiếng Anh |
| `TTS_ENGLISH_MIN_RATIO` | 0.6 | Tỉ lệ từ tiếng Anh tối thiểu |
| `TTS_NUM_ZERO_TENS` | `linh` | Cách đọc hàng chục 0 (`linh` \| `lẻ`) |
| `TTS_NUM_THOUSAND` | `nghìn` | `nghìn` \| `ngàn` |
| `TTS_ABBREV_FILE` | rỗng | File JSON viết tắt bổ sung (mục 2.2) |
| `TTS_MAX_INPUT_CHARS` | 5000 | Độ dài `input` tối đa |
| `LOG_LEVEL` | `info` | Mức log |
| `HOST`, `PORT` | `0.0.0.0`, `8880` | Địa chỉ lắng nghe |

---

## 3. Provider xiaozhi `jarvis_stream`

`server/xiaozhi/Dockerfile` chép file vào image thành `core/providers/tts/jarvis_stream.py` (factory TTS của xiaozhi nạp
`core/providers/tts/<type>.py`). Khối cấu hình trong `server/xiaozhi/data/.config.yaml.tmpl`:

```yaml
selected_module:
  TTS: JarvisTTS
TTS:
  JarvisTTS:
    type: jarvis_stream
    api_url: http://tts-router:8880/v1/audio/speech
    api_key: "none"
    voice: "${TTS_VOICE}"      # bắt buộc, không được rỗng (entrypoint báo lỗi nếu thiếu)
    sample_rate: 24000
    output_dir: tmp/tts
```

| Khóa | Mặc định | Ý nghĩa |
|---|---|---|
| `api_url` | `http://tts-router:8880/v1/audio/speech` | Router |
| `voice` (`private_voice` nếu có thì ưu tiên) | `default` | `default` = không gửi `voice`, router dùng `TTS_VOICE` |
| `sample_rate` | 24000 | Phải là tần số Opus (8000/12000/16000/24000/48000). Tần số thật lấy theo kết nối (`conn.sample_rate`); khác cấu hình thì ghi cảnh báo một lần và dùng tần số kết nối |
| `timeout` | `tts_timeout` (15 s trong template) | Read timeout (giây) |
| `connect_timeout` | 3 | Giây |
| `retries` | 1 | Số lần thử lại **trước** byte âm thanh đầu tiên (lỗi mạng, 5xx, 429) |
| `max_segment_seconds` | 60 | Một đoạn đọc quá lâu bị cắt (log `truncated`) |
| `first_segment_min_chars` | 8 | Độ dài tối thiểu để đoạn đầu được cắt ở dấu phẩy |
| `max_segment_chars` | 220 | Đoạn dài không có dấu câu bị cắt ở dấu phẩy/khoảng trắng gần nhất |
| `model`, `api_key`, `speed`, `correct_words`, `output_dir` | – | `model` gửi kèm (router không dùng); `api_key` → Bearer nếu khác `none`; `speed` chỉ có tác dụng với Kokoro; `correct_words` là danh sách "từ\|thay bằng" của upstream |

Hoạt động:
- **Tách đoạn theo luồng chữ của LLM**: cắt ở `. ! ? ; : …` + khoảng trắng, xuống dòng, dấu câu CJK; đoạn **đầu tiên** của lượt
  trả lời còn được cắt ở dấu phẩy đầu tiên (đoạn ≥ `first_segment_min_chars` ký tự và có ít nhất một chữ cái ngoài ASCII, tức chữ
  tiếng Việt có dấu – đoạn thuần ASCII như "Stealth mode engaged," có thể là tiếng Anh nằm giữa câu Việt nên không bị tách riêng;
  dấu phẩy ASCII phải có khoảng trắng phía sau, nên không cắt trong `3,5` hay `1,000`) để có âm thanh sớm. Không bao giờ cắt trong
  `5.02`, `14:32`, `J.A.R.V.I.S`. Dấu chấm câu ở cuối buffer chỉ được tính khi chắc chắn (`5.` có thể thành `5.02`).
  Upstream bỏ qua dấu `.` ASCII; provider này thì không (D18).
- Mỗi đoạn là một request streaming tới router (`response_format: pcm`, `sample_rate` = tần số kết nối, `stream: true`); PCM
  nhận được được mã hóa ngay thành **khung Opus 60 ms** và đưa vào hàng gửi của xiaozhi.
- **Ngắt lời / lượt cũ**: khi `conn.client_abort` bật hoặc `sentence_id` đổi, provider bỏ tin nhắn, đóng stream HTTP giữa chừng
  (router/VieNeu dừng theo) và xóa phần PCM còn dở.
- **Lỗi**: thử lại trước byte đầu tiên; đã có âm thanh mà stream đứt thì không đọc lại; hết lần thử → bỏ đoạn
  (`jarvis_stream: TTS failed, segment skipped ...`) nhưng chữ của đoạn vẫn được gửi lên màn hình mũ.
- **Log**: mỗi đoạn một dòng `jarvis_stream: segment chars=.. ttfb_ms=.. total_ms=.. audio_ms=.. text='...'`; byte PCM đầu
  tiên của mỗi lượt ghi `[LAT] sid=<session> stage=tts_first_audio t_ms=<epoch ms> seg_chars=.. ttfb_ms=..` (định dạng `[LAT]`
  dùng chung với `tools/tests/e2e/latency.py`).
- `text_to_speak()` (không streaming, `stream: false`) dùng cho cache câu trả lời wake word của xiaozhi; trả WAV.

---

## 4. Biến trong `server/.env` liên quan TTS

| Biến | Dùng bởi | Ghi chú |
|---|---|---|
| `TTS_VOICE` | xiaozhi (template, **bắt buộc**), tts-router | Giọng tiếng Việt. Mẫu: `Thiền Tâm Đức` |
| `TTS_ENGLISH_ENGINE` | tts-router | `vieneu` (mặc định) \| `kokoro` (cần profile `english-tts`) |
| `TTS_ENGLISH_VOICE` | tts-router, compose → `DEFAULT_VOICE` của container kokoro | Giọng Kokoro cho câu tiếng Anh (mặc định `bm_george`); một biến dùng cho cả router lẫn warm-up của Kokoro |
| `VIENEU_MAX_STREAMS`, `VIENEU_QUEUE` | compose → vieneu | Mục 1.2 |
| `TTS_HF_OFFLINE` | compose → `JARVIS_HF_OFFLINE` của vieneu | `auto` (mặc định): tự chuyển offline khi model và codec đã có trong cache; `1`: luôn offline; `0`: luôn online (chỉ dùng khi cần tải model mới) |
| `TTS_*` khác, `VIENEU_URL`… | tts-router | Mục 2.7 |

Đổi biến xong, tạo lại container để nạp giá trị mới (xiaozhi render cấu hình lúc khởi động, router đọc biến lúc khởi động):

```bash
cd server
docker compose up -d --force-recreate tts-router xiaozhi
```

---

## 5. Kiến trúc tóm tắt theo yêu cầu SV4

| Yêu cầu SV4 | Cách đáp ứng |
|---|---|
| Độ trễ thấp, đọc theo từng câu | Provider tách đoạn theo luồng chữ LLM, đoạn đầu cắt ở dấu phẩy; router chuyển tiếp chunk ngay; VieNeu streaming |
| Chuẩn hóa số, đơn vị, viết tắt, bỏ emoji | `normalize_vi.py` (mục 2.2) |
| Câu thuần tiếng Anh giọng chuẩn (phương án B) | Kokoro `bm_george`, profile `english-tts` (mục 7) |
| Giọng nam trầm | Chọn preset bằng `bench.py --voices-scan` (mục 6.2) hoặc clone giọng có quyền dùng (mục 6.3) |

---

## 6. Giọng đọc

### 6.1 Giọng nam có sẵn (VieNeu v3 Turbo, 14 giọng nam / 25 giọng)

Metadata của VieNeu chỉ có giới tính, vùng miền, phong cách – **không có thuộc tính "trầm"**, phải nghe thử. Hai cột F0 là cao độ
trung vị đo bằng `bench.py --voices-scan` (hàm `median_f0`, câu mẫu mặc định) ở **hai lần quét** khác nhau (ngày 2026-10-06); VieNeu
lấy mẫu ngẫu nhiên nên kết quả lệch giữa các lần (có giọng lệch tới ~15 Hz). F0 thấp hơn = giọng trầm hơn. Bảng xếp theo trung bình.

| Giọng (viết đúng dấu) | Vùng · phong cách | Bí danh | F0 lần 1 (Hz) | F0 lần 2 (Hz) |
|---|---|---|---|---|
| `Đức Trí` | Nam (miền Nam) · đọc truyện | | 101 | 100 |
| `Xuân Vĩnh` | Bắc · tự nhiên | | 104 | 105 |
| `Phạm Tuyên` | Bắc · tự nhiên | | 110 | 115 |
| `Thiền Tâm Đức` | Bắc · kể chuyện | | 112 | 114 |
| `Thiện Minh` | Bắc · kể chuyện | `Anh Khôi` | 113 | 115 |
| `Adam` | Nam · tự nhiên | | 108 | 125 |
| `Thái Sơn` | Nam · kể chuyện | | 131 | 130 |
| `Hải Đăng` (mặc định của VieNeu) | Bắc · tự nhiên | `Minh Quân Pro`, `Minh Quân` | 146 | 136 |
| `Quang Sơn` | Trung · tự nhiên | | 145 | 144 |
| `Minh Triết` | Nam · tin tức | | 147 | 146 |
| `Minh Đức` | Bắc · tin tức | | 151 | 155 |
| `Thanh Bình` | Bắc · kể chuyện | | 154 | 158 |
| `Quốc Tuấn` | Bắc · tự nhiên | `Mạnh Dũng` | 158 | 157 |
| `Adam bựa` | Bắc · tự nhiên | | 162 | 168 |

Giọng nữ: Trúc Ly, Mai Anh, Thùy Dung, Ngọc Huyền, Ngọc Trân, Ngọc Linh, Đoan Trang, Thục Đoan, Mỹ Duyên, Quỳnh Anh, Kim Thanh.
Tên giọng so khớp **chính xác** (bí danh cũng được chấp nhận): sai một dấu là VieNeu từ chối (mục 2.6).

### 6.2 Nghe thử và chọn giọng nam trầm

1. Chạy stack kèm file debug để mở router trên `127.0.0.1:8880`:

   ```bash
   cd server
   docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d
   # Docker Desktop: thêm -f docker-compose.local.yml
   ```

2. Quét mọi giọng nam: tổng hợp cùng một câu bằng từng giọng, xếp theo F0 tăng dần, lưu WAV vào `tools/tests/tts/out/voices/`:

   ```bash
   .venv/Scripts/python tools/tests/tts/bench.py --voices-scan
   # câu khác: --scan-text "Thưa ngài, mặt nạ đã mở."   cả giọng nữ: --gender all
   ```

3. Nghe các WAV, ưu tiên nhóm F0 thấp; với quản gia nói giọng Bắc, các ứng viên thường là `Xuân Vĩnh`, `Phạm Tuyên`,
   `Thiền Tâm Đức`, `Thiện Minh` (`Đức Trí`, `Adam` trầm nhưng giọng miền Nam). Nghe qua loa trong mũ trước khi chốt (SV4 tiêu chí 3).
4. Thử ứng viên trên bộ 20 câu (số, đơn vị, câu trộn Anh–Việt):

   ```bash
   .venv/Scripts/python tools/tests/tts/bench.py --voice "Xuân Vĩnh" --out tools/tests/tts/out/xuan_vinh
   ```

5. Ghi vào `server/.env`: `TTS_VOICE=Xuân Vĩnh` (không cần dấu nháy, giữ đúng dấu), rồi
   `docker compose up -d --force-recreate tts-router xiaozhi`. Kiểm tra `curl -s http://127.0.0.1:8880/health` (trường `voice`).

Không chỉnh được tốc độ đọc của VieNeu: nếu muốn quản gia nói gọn hơn, chọn giọng phong cách "tin tức" hoặc "tự nhiên" thay
vì "kể chuyện".

### 6.3 Clone giọng và lưu bền

Chỉ clone giọng bạn **có quyền dùng** (giọng của chính bạn hoặc người đã đồng ý). Không clone giọng diễn viên/người thật khi chưa
được phép.

**Vì sao cần script riêng:** `POST /v1/voices` của VieNeu chỉ đăng ký giọng trong bộ nhớ tiến trình (mất khi khởi động lại) và
từ chối (409) tên trùng giọng có sẵn. `clone_voice.py` dùng SDK (`add_voice` + `save_voices`) để ghi **file giọng đầy đủ** (giọng
có sẵn + giọng đã cài từ `VIENEU_VOICES_FILE` + giọng mới). Mỗi lần container khởi động, `jarvis_entrypoint.sh`:
- có file `VIENEU_VOICES_FILE` hợp lệ (có `presets` không rỗng) → chép đè lên file giọng của package, log
  `jarvis: voices loaded from /voices/voices_v3_turbo.json`;
- file lỗi → cảnh báo `jarvis: WARNING invalid voices file ...` và dùng giọng có sẵn;
- không có file → khôi phục giọng có sẵn.

**Chuẩn bị clip mẫu:** giọng nói sạch, một người, không nhạc nền, định dạng wav/mp3/flac/ogg/m4a. VieNeu cắt khoảng lặng hai đầu
rồi **chỉ dùng tối đa 8 giây đầu** (`_MAX_REF_SECONDS = 8.0`), nên clip 5–8 giây là đủ (`--help` của script ghi 3–30 s: clip dài
hơn vẫn nhận nhưng phần sau 8 s bị bỏ; kế hoạch SV4 ghi 10–30 s là chưa đúng với VieNeu).

**Các bước với stack compose** (project `jarvis`):

```bash
cd server
docker compose stop vieneu                       # GPU nhỏ: nhường VRAM cho bước clone
mkdir -p ref && cp /duong/dan/clip_mau.wav ref/  # clip nằm trên máy host

# Ghi thẳng vào volume giọng của compose (Docker Desktop: --gpus all; server CDI: --device nvidia.com/gpu=all)
docker run --rm --gpus all \
  -v jarvis_vieneu_hf_cache:/home/app/.cache/huggingface \
  -v jarvis_vieneu_voices:/voices \
  -v "$PWD/ref:/ref:ro" \
  jarvis/vieneu:dev python /opt/jarvis/clone_voice.py \
    --name "Jarvis Bat" --ref /ref/clip_mau.wav --gender male \
    --description "Giong quan gia" --sample "Vâng, thưa ngài."

docker compose start vieneu                      # entrypoint cài file giọng mới
```

Trong Git Bash trên Windows, đặt `MSYS_NO_PATHCONV=1` trước lệnh `docker run` để Git Bash không đổi các đường dẫn `/voices`, `/ref`.

Tham số của `clone_voice.py` (đã đối chiếu `--help`):

| Tham số | Ý nghĩa |
|---|---|
| `--name` (bắt buộc) | Tên giọng dùng làm `voice` (1–64 ký tự). Trùng tên đã có → thay thế (`note: replacing existing voice`) |
| `--ref` (bắt buộc) | Đường dẫn clip mẫu **trong container** |
| `--out` | File giọng ghi ra, mặc định `/voices/voices_v3_turbo.json` (đúng `VIENEU_VOICES_FILE`) |
| `--description` | Mô tả hiện trong `GET /v1/voices` |
| `--gender` | Chỉ là metadata; đặt `male` để `bench.py --voices-scan` (lọc giọng nam) quét cả giọng này |
| `--no-denoise` | Clip đã sạch, bỏ bước khử nhiễu |
| `--default` | Đặt làm giọng mặc định của VieNeu (chỉ dùng khi request không có `voice`; mũ luôn gửi `TTS_VOICE`) |
| `--device` | `auto` (mặc định) \| `cuda` \| `cpu` |
| `--sample` | Đọc thử câu này, lưu `<thư mục của --out>/<name>.sample.wav` để nghe ngay |

Sau đó: kiểm tra giọng mới có trong `curl -s http://127.0.0.1:8880/v1/voices`, nghe bằng
`bench.py --voice "Jarvis Bat" --only 1,5,14`, rồi đặt `TTS_VOICE=Jarvis Bat` trong `server/.env` và tạo lại `tts-router`, `xiaozhi`.

Sao lưu: file giọng là bản duy nhất chứa embedding của giọng clone. Lấy ra máy host:
`docker compose cp vieneu:/voices/voices_v3_turbo.json ./voices_backup.json`. Đưa lại:
`docker compose cp ./voices_backup.json vieneu:/voices/voices_v3_turbo.json`, rồi trả quyền cho user `app` (file chép vào thuộc
root, `clone_voice.py` lần sau sẽ không ghi đè được): `docker compose exec -u root vieneu chown app:app /voices/voices_v3_turbo.json`,
cuối cùng `docker compose restart vieneu`. File này chứa đặc trưng giọng nói của một người: không commit vào repo.

---

## 7. Giọng tiếng Anh bằng Kokoro (profile `english-tts`)

Dùng khi câu trả lời **toàn tiếng Anh** mà VieNeu đọc lơ lớ. Câu tiếng Việt và câu trộn vẫn đi VieNeu.

1. Trong `server/.env`:

   ```
   TTS_ENGLISH_ENGINE=kokoro
   # tùy chọn (mặc định bm_george); trộn giọng: bm_george(2)+bm_lewis(1)
   TTS_ENGLISH_VOICE=bm_george
   ```

2. Chạy kèm profile (mọi lệnh compose sau đó cũng cần `--profile english-tts` để thấy service `kokoro`):

   ```bash
   cd server
   docker compose --profile english-tts up -d
   docker compose --profile english-tts up -d --force-recreate tts-router
   ```

3. Kiểm tra: `/health` của router có `"english_engine": "kokoro"` và `backends.kokoro.healthy: true` (nếu Kokoro chưa lên:
   `status: "degraded"`, câu tiếng Anh tạm đọc bằng VieNeu). Chạy `bench.py --only 10,11,12,13`: cột `route` phải là `kokoro`.

Ghi chú: image `ghcr.io/remsky/kokoro-fastapi-gpu:v0.9.0-cu128` (model đóng sẵn trong image, ra 24 kHz), cần thêm khoảng 3 GB
VRAM; giọng Anh-Anh nam: `bm_george`, `bm_fable`, `bm_lewis`, `bm_daniel`. Kokoro có tôn trọng `speed`. Kokoro lỗi trước khi
phát ra âm thanh (kể cả khi đã trả 200) → nhóm câu đó được đọc bằng VieNeu; lỗi khi đã phát một phần → phần còn lại bị mất,
Kokoro bị đánh dấu down tới lần kiểm tra sức khỏe sau (mục 2.3). Tắt: đặt lại `TTS_ENGLISH_ENGINE=vieneu`, tạo lại `tts-router`, rồi
`docker compose --profile english-tts stop kokoro`.

---

## 8. Kiểm thử

Unit test (không cần GPU, chạy trên máy dev):

```bash
.venv/Scripts/python -m pytest server/tests/test_tts_router.py server/tests/test_normalize_vi.py \
    server/tests/test_jarvis_stream_segmentation.py server/tests/test_tts_provider_runtime.py -q
```

`test_tts_provider_runtime.py` tự bỏ qua ngoài image xiaozhi. Chạy nó **trong** image (Python 3.10, provider thật, encoder Opus
thật, router giả phát tone; thêm `-e JARVIS_TTS_URL=...` để đi qua router thật):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint python -w /opt/xiaozhi-esp32-server \
    -v "$PWD/server/tests/test_tts_provider_runtime.py:/tmp/test_tts_provider_runtime.py:ro" \
    jarvis/xiaozhi:dev /tmp/test_tts_provider_runtime.py
```

Đo hiệu năng và nghe đánh giá: `tools/tests/tts/README.md`. Đối chiếu tiêu chí nghiệm thu SV4:

| Tiêu chí SV4 | Cách kiểm |
|---|---|
| 1. TTFA ≤ 300 ms với câu ≤ 15 từ | `bench.py` in dòng `SV4 criterion 1: ... N/M` (đo trên server thật, GPU thật) |
| 2. Đọc đúng 20/20 câu, không sai số/đơn vị, từ tiếng Anh nghe hiểu được | Nghe các WAV của `bench.py`, xem `--show-text` |
| 3. Nghe qua loa trong mũ rõ, không rè, âm lượng ổn ở 70% | Thử trên mũ thật |
| 4. Một giờ hội thoại: VRAM ổn định | `nvidia-smi` theo thời gian khi chạy hội thoại thật / `tools/tests/e2e` |

---

## 9. Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp / cách xử lý |
|---|---|
| `vieneu` mãi `starting`/`unhealthy` lần đầu | Đang tải model (~0,6 GB) và warm-up: xem `docker compose logs -f vieneu`, chờ dòng `✅ ready in ...s: backend=... max_streams=...`. Lỗi tải HF: kiểm tra mạng; `TTS_HF_OFFLINE=1` khi cache còn trống sẽ làm tải thất bại |
| `Permission denied` khi tải model | Volume cache thuộc root (dùng chung với service khác). Dùng volume riêng `vieneu_hf_cache` như compose (uid 1000) |
| Router `/health` 503, xiaozhi không lên (`depends_on` chờ router healthy) | VieNeu chưa sẵn sàng hoặc đã chết. `docker compose ps`, `docker compose logs vieneu tts-router` |
| VieNeu `/health` 503 "stream scheduler is down", mọi request 503 | Bộ lập lịch GPU chết, không tự hồi phục. Docker **không** tự khởi động lại container unhealthy: `docker compose restart vieneu` |
| Log router `vieneu: voice 'X' rejected, spoke with 'Y' instead` | `TTS_VOICE` sai tên (thiếu/sai dấu, khoảng trắng). So với `GET /v1/voices`, sửa `server/.env`, tạo lại `tts-router` + `xiaozhi` |
| HTTP 429 / log `server busy` | Nhiều request đồng thời hơn `VIENEU_MAX_STREAMS` + `VIENEU_QUEUE`. Tăng `VIENEU_MAX_STREAMS` (tốn thêm VRAM) |
| Âm thanh trên mũ bị ngắt quãng, TTFA cao | Xem dòng log VieNeu `spk-... done: ttfa=... rtf=...`: `rtf` > 1 nghĩa là tổng hợp chậm hơn thời gian thực (GPU yếu, GPU bị chia sẻ, hoặc đang chạy CPU). GPU "ngủ" sau vài giây rảnh làm request kế tiếp chậm thêm 100–300 ms; có thể bật persistence mode `nvidia-smi -pm 1` trên host (máy dùng chung: thống nhất trước) |
| Đọc sai số/viết tắt | Xem `X-TTS-Normalized` / `bench.py --show-text`; thêm viết tắt qua `TTS_ABBREV_FILE` (mục 2.2) |
| Câu tiếng Anh vẫn giọng VieNeu | `TTS_ENGLISH_ENGINE` chưa là `kokoro`, Kokoro chưa healthy (`/health` = `degraded`), hoặc câu có chữ tiếng Việt / chưa đủ 2 từ tiếng Anh (mục 2.3) |
| Đổi giọng tiếng Anh mà không thấy khác | Đặt `TTS_ENGLISH_VOICE` rồi tạo lại container `tts-router` và `kokoro` (mục 4) |
| Mũ hiện chữ nhưng không có tiếng | Log xiaozhi `jarvis_stream: TTS failed, segment skipped`: kiểm tra router từ trong container xiaozhi: `docker compose exec xiaozhi python3 -c "import urllib.request as u; print(u.urlopen('http://tts-router:8880/health').read())"` |
| Log `connection output rate ... != configured sample_rate` | Tần số Opus của kết nối khác `sample_rate` cấu hình; kiểm tra `xiaozhi.audio_params.sample_rate: 24000` trong template và bản vá hello (D3) |
| Mũ nói giọng cũ sau khi clone | Container `vieneu` chưa khởi động lại, hoặc file giọng lỗi (log `invalid voices file`) |

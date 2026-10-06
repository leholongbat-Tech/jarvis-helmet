# Công cụ thử end-to-end (`tools/tests/e2e`) – IT1

Bộ công cụ cho module IT1 (tích hợp & nghiệm thu): giả lập mũ để chạy kịch bản tự động với server thật, đo độ trễ từ log
xiaozhi, theo dõi heap của mũ thật, chuẩn bị file âm thanh. Quy trình từng kịch bản E1–E14 (phần tự động, phần cần mũ thật, tiêu
chí đạt, bằng chứng): **`scenarios.md`**. Ghi kết quả vào **`docs/test-report.md`**.

| File | Vai trò |
|---|---|
| `device_sim.py` | Giả lập mũ: OTA, WebSocket giao thức v1, MCP server với đúng danh sách tool của firmware, Opus 16 kHz lên / giải mã Opus xuống, chạy kịch bản E1–E8, E11–E13 và tự chấm |
| `latency.py` | Đọc các dòng `[LAT]` trong log xiaozhi, tính trung vị/p90 từng chặng và so với mục tiêu IT1 |
| `heap_trend.py` | Đọc log serial của mũ thật: xu hướng heap, phát hiện reset (E13, E14, FW7 §5.4) |
| `make_wavs.py` | Tạo WAV cho kịch bản bằng tts-router, chuyển file ghi âm sang WAV 16 kHz, phát WAV lặp lại qua loa (E13) |
| `scenarios.md` | Quy trình E1–E14 và cách đo các chỉ tiêu IT1 §3 |
| `requirements.txt` | Gói Python các công cụ trên cần |
| `test_*.py`, `conftest.py` | Unit test (không cần server) |
| `audio/` | WAV đầu vào cho `device_sim.py` (`<ID>.wav`, `<ID>_<n>.wav`), tạo bởi `make_wavs.py` |
| `out/` | Kết quả mỗi lần chạy `device_sim.py` |

`audio/` và `out/` chưa có trong `.gitignore`: **không commit** (có thể chứa giọng thật, file lớn). Chỉ đưa số liệu vào
`docs/test-report.md`.

---

## 1. Cài đặt và unit test

```bash
.venv/Scripts/python -m pip install -r tools/tests/e2e/requirements.txt   # .venv của repo đã có sẵn
.venv/Scripts/python -m pytest tools/tests/e2e -q
```

(Linux/macOS: `.venv/bin/python`.) Gói cần: `websockets`; `numpy` + `av` (PyAV) chỉ cần khi dùng âm thanh (chế độ chèn văn bản
chạy được không có chúng); `PyYAML` cho `--expect-task` và cho bộ kiểm tra chuỗi `server/tasks/taskslib.py`. `latency.py`,
`heap_trend.py` chỉ dùng thư viện chuẩn. Unit test kiểm tra giả lập so với mã firmware (JSON của tool MCP, phân trang
`tools/list`, OTA, header WebSocket…) và chạy CLI đầu-cuối với một server giả trong tiến trình.

---

## 2. `device_sim.py` – giả lập mũ

### 2.1 Giả lập được gì, không giả lập được gì

| Có | Không (cần mũ thật – xem `scenarios.md`) |
|---|---|
| `POST` OTA với header + JSON system-info như firmware (`main/ota.cc`) → nhận URL WebSocket và token | Âm học thật: mic INMP441, loa trong mũ, tiếng vọng |
| WebSocket v1: `Authorization: Bearer`, `Protocol-Version`, `Device-Id`, `Client-Id` (+ header Cloudflare Access), `hello` của thiết bị | Wake word trên thiết bị: giả lập gửi chữ wake word bằng `listen`/`detect` như firmware khi đã nhận wake word |
| MCP server: `initialize`, `tools/list` (trang 8000 byte, `nextCursor`), `tools/call` giống `main/mcp_server.cc`, đủ tool Jarvis + tool upstream với đúng mô tả và schema của firmware (đọc từ `tools/tests/llm/device_tools.json`), lỗi của callback chép nguyên văn từ firmware; kiểm tra `self.sequence.run` bằng `taskslib.py` (hoặc bộ kiểm tra có sẵn: cùng luật, câu báo lỗi của từng bước có thể khác firmware) | Servo, đèn, HUD thật (chỉ kiểm tra font HUD có đủ glyph cho chữ hiển thị) |
| Trạng thái mũ mô phỏng theo thời gian (mặt nạ chạy, đèn, output, chuỗi đã lưu) cho `get_state`/`get_status` | Wi-Fi, chuyển mạng, nguồn, heap của ESP32 |
| Âm thanh: WAV 16 kHz mono → Opus 60 ms theo thời gian thực; giải mã Opus server gửi xuống thành WAV | |
| Đo từ lúc nói xong tới: `stt`, khung audio đầu tiên, lệnh MCP đầu tiên, `tts stop`; đo thời gian dừng sau `abort` | |

### 2.2 Kết nối tới server

| Cách | Tham số |
|---|---|
| Qua OTA ở LAN (giống mũ) | `--ota-url http://<LAN_IP>:8003/xiaozhi/ota/` (OTA trả `PUBLIC_WS_URL` + token) |
| WebSocket trực tiếp | `--ws-url ws://<LAN_IP>:8100/xiaozhi/v1/` kèm `--auth-key <XIAOZHI_AUTH_KEY>` (tự tính token như server) hoặc `--token <token>` (`--token ''` = không gửi) |
| Qua Cloudflare (P8) | `--ota-url https://jarvis.<domain>/xiaozhi/ota/` và biến môi trường `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` (thêm header vào OTA và WebSocket) |

Không ghi khóa (`XIAOZHI_AUTH_KEY`, token Cloudflare) vào file hay script trong repo; đặt bằng biến môi trường của phiên terminal.
Giả lập dùng MAC `02:4a:48:4d:00:01` (đổi bằng `--mac`, `--uuid`), khác MAC của mũ thật nên chạy song song được.

### 2.3 Đầu vào

- **Chèn văn bản** (mặc định khi không có WAV): gửi `listen`/`detect` với câu lệnh, bỏ qua VAD và ASR → đo đường xử lý của server
  từ chữ tới câu trả lời. `--text "..."` thay câu của kịch bản.
- **Âm thanh**: `--wav <file>` (lặp lại được), `--wav-dir <thư mục>` (quay vòng), hoặc tự lấy `audio/<ID>.wav`, `audio/<ID>_<n>.wav`
  khi có (`--input auto`); `--input audio` bắt buộc dùng WAV, `--input text` bắt buộc dùng chữ. File không phải WAV 16 bit được
  chuyển bằng PyAV. Điểm kết thúc lời nói trong WAV lấy theo năng lượng, hoặc `--speech-end-ms`.
- **Wake word**: mặc định gửi trước chữ wake word của kịch bản (`Jarvis`, E12 là `Quản gia`); đổi bằng `--wake`, bỏ bằng `--no-wake`.
- **Chế độ nghe** `--mode`: `auto` (VAD của server cắt câu; sau WAV gửi thêm tối đa `--tail-ms` = 4000 ms im lặng), `manual` (giống
  nhấn giữ PTT: gửi `listen stop` ngay sau khung cuối, trễ thêm `--ptt-release-ms`), `realtime`.
- **Nhiều lượt**: `--repeat N`, `--pause-ms` (mặc định 1000) giữa các lượt, `--reconnect` mở WebSocket mới mỗi lượt.
- **Ngắt lời**: `--abort-after-ms` gửi `abort` sau khung audio trả lời đầu tiên (rồi `listen start` như bấm PTT, D8);
  `--abort-reason wake_word_detected` gửi kèm lý do wake word (E8).

### 2.4 Kịch bản có sẵn (`--scenario`)

| ID | Câu (sau wake word) | Kiểm tra bắt buộc | Kiểm tra mềm (chỉ cảnh báo) |
|---|---|---|---|
| E1 | mấy giờ rồi? | `reply`, `no_device_action` | `glyphs`, `reply_has_hour` |
| E2 | what's the weather like? | `reply` | `glyphs`, `reply_english` |
| E3 | open mask rồi bật đèn mắt lên | `actions:mask_open,eyes_on` | `glyphs` |
| E4 | mở mặt nạ (`eos_to_first_mcp_ms` bỏ qua `self.sequence.save` lúc kết nối và các tool chỉ đọc) | `actions:mask_open`, `max:eos_to_first_mcp_ms:800` | `glyphs` |
| E5 | bật party mode | `tool:self.sequence.run`, `task_steps` | `glyphs` |
| E6 | hỏi chuyên gia: vì sao bầu trời có màu xanh? (timeout lượt 60 s) | `reply`, `no_device_action` | `glyphs` |
| E7 | kể cho tôi nghe một câu chuyện thật dài về Iron Man (`abort` sau 1500 ms) | `max:abort_to_tts_stop_ms:300`, `max:abort_to_last_audio_ms:500` | `glyphs` |
| E8 | như E7, `abort` lý do `wake_word_detected` | `max:abort_to_tts_stop_ms:300` | `glyphs` |
| E11 | hãy đọc nguyên văn câu này: Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài! | `reply`, `glyphs`, `vi_diacritics` | – |
| E12 | close the mask (wake word `Quản gia`) | `wake_word_silent`, `actions:mask_close` | `glyphs` |
| E13 | mở mặt nạ / đóng mặt nạ / bật đèn mắt / tắt đèn mắt, 50 lượt | `all_completed`, `each_turn_responds` | – |

Mọi lần chạy còn có kiểm tra `no_errors` (không lỗi giao thức/lượt). E9, E10, E14 không giả lập được (mạng, nút bấm, nguồn).
`--list-scenarios` in bảng này từ mã.

Ý nghĩa các kiểm tra:

| Kiểm tra | Đạt khi |
|---|---|
| `reply` | Mỗi lượt có câu trả lời (audio hoặc câu TTS) |
| `no_device_action` / `no_mcp` | Không có hành động lên mũ / không có lệnh MCP nào |
| `actions:a,b` | Các hành động xuất hiện **đúng thứ tự** (có thể xen hành động khác). Tên: `mask_open`, `mask_close`, `mask_pos_<n>`, `eyes_on`, `eyes_off`, `eyes_blink`, `output_on`, `output_off`, `display`, `servo`, `sound` (suy ra từ tool gọi tới, kể cả bước trong `self.sequence.run` và chuỗi đã lưu) |
| `tool:<tên>` | Tool đó được gọi trong mọi lượt |
| `max:<chỉ số>:<ms>` | Chỉ số của mọi lượt có giá trị và ≤ ngưỡng |
| `task_steps` | Bước của `self.sequence.run` trùng bước của task trong `tasks.yaml` (hoặc nằm liền trong chuỗi đã gộp, D4). Bỏ qua nếu không có `--expect-task` |
| `all_completed`, `each_turn_responds` | Đủ số lượt hoàn tất không lỗi; mỗi lượt có lệnh MCP hoặc audio |
| `glyphs` | Mọi chữ hiển thị đều có trong dải ký tự font HUD (D10) và ở dạng NFC |
| `vi_diacritics` | Câu trả lời có chữ tiếng Việt có dấu |
| `reply_has_hour` | Câu trả lời có số giờ hiện tại (múi giờ `--tz-offset`, mặc định 7) |
| `reply_english` | Câu trả lời gần như toàn chữ ASCII, không có dấu tiếng Việt |

Thêm kiểm tra cho lần chạy tùy ý: `--expect-tool <tool>` (lặp lại được), `--expect-actions mask_open,eyes_on`, `--expect-no-mcp`,
`--expect-reply`, `--expect-task <task_id>` (+ `--tasks-file`, mặc định `server/tasks/tasks.yaml`), `--max-eos-to-mcp-ms`,
`--max-eos-to-audio-ms`.

### 2.5 Chỉ số đo (mỗi lượt, mili giây)

Mốc 0 = **lúc nói xong**: với WAV là điểm kết thúc lời nói trong file (đã phát theo thời gian thực); với chèn văn bản là lúc gửi
`listen`/`detect`.

| Chỉ số | Từ lúc nói xong tới |
|---|---|
| `eos_to_stt_ms` | Server gửi kết quả nhận dạng (`stt`) |
| `eos_to_tts_start_ms` | `tts start` |
| `eos_to_first_audio_ms` | Khung Opus trả lời đầu tiên tới giả lập |
| `eos_to_first_mcp_ms` | Lệnh `tools/call` đầu tiên tới giả lập |
| `eos_to_tts_stop_ms` | `tts stop` |
| `abort_to_tts_stop_ms` | (E7/E8) từ lúc gửi `abort` tới `tts stop` |
| `abort_to_last_audio_ms` | (E7/E8) từ `abort` tới khung audio cuối cùng còn nhận được |

Ở chế độ `auto`, các chỉ số tính từ lúc nói xong nên **gồm cả khoảng im lặng VAD chờ** (`min_silence_duration_ms: 700` trong cấu hình
xiaozhi); chế độ `manual` (PTT) thì không.

### 2.6 Kết quả

- **stdout**: tóm tắt JSON. **stderr**: log từng bước, rồi từng kiểm tra `[PASS]` / `[WARN]` (kiểm tra mềm) / `[FAIL]` và dòng
  `RESULT: PASS|FAIL`. `--quiet` chỉ in JSON.
- **Thư mục kết quả** (mặc định `tools/tests/e2e/out/<YYYYMMDD-HHMMSS>_<ID kịch bản|custom>/`, đổi bằng `--out-dir`, tắt bằng
  `--no-save`): `summary.json` và `turn<NNN>_reply.wav` (audio server gửi xuống, đã giải mã).
- `summary.json`: `scenario`, `device`, `validator`, `ota` (status, thời gian, `ws_url`, có token không, cảnh báo), `ws_url`,
  `connections` (session id, hello của server, dòng thời gian, các request MCP, cảnh báo/lỗi giao thức, lý do đóng), `turns`
  (đầu vào, `stt`, câu trả lời, `mcp_calls`, `actions`, `metrics`, lỗi/cảnh báo, đường dẫn WAV), `mcp_calls`, `stats`
  (n/median/p90/min/max của `eos_to_stt_ms`, `eos_to_first_audio_ms`, `eos_to_first_mcp_ms`, `eos_to_tts_stop_ms`), `assertions`, `pass`.
- **Mã thoát**: 0 đạt; 1 có kiểm tra bắt buộc không đạt; 2 sai tham số; 3 lỗi kết nối/giao thức (OTA, WebSocket…).

Session id trong `connections[].session_id` dùng để lọc log server: `latency.py --sid <session_id>`.

### 2.7 Ví dụ

```bash
SIM=".venv/Scripts/python tools/tests/e2e/device_sim.py"
OTA=http://192.168.1.27:8003/xiaozhi/ota/

$SIM --ota-url $OTA --scenario E1                               # chèn văn bản
$SIM --ota-url $OTA --scenario E3 --input audio                 # dùng audio/E3.wav
$SIM --ota-url $OTA --scenario E4 --input audio --mode manual   # giống PTT
$SIM --ota-url $OTA --scenario E5 --expect-task party_mode      # sau khi bật party_mode trong tasks.yaml
$SIM --ota-url $OTA --scenario E13 --repeat 50 --pause-ms 2000
$SIM --ota-url $OTA --text "kể một câu chuyện cười ngắn" --expect-reply --expect-no-mcp
$SIM --ws-url ws://192.168.1.27:8100/xiaozhi/v1/ --auth-key "$XIAOZHI_AUTH_KEY" --scenario E2

# Qua Cloudflare (khóa chỉ nằm trong biến môi trường của phiên)
CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... $SIM --ota-url https://jarvis.<domain>/xiaozhi/ota/ --scenario E1

# Không cần server
$SIM --list-scenarios
$SIM --dump-tools [--with-user-tools]      # các trang tools/list firmware sẽ gửi
```

---

## 3. `latency.py` – độ trễ từ log xiaozhi

Bản vá của SV-C ghi mỗi mốc một dòng ở mức INFO (hợp đồng §4.10):

```
[LAT] sid=<session_id> stage=<stage> t_ms=<epoch ms> [key=value ...]
```

`stage`: `vad_end` (`src=vad`: VAD của server cắt câu, kèm `speech_end_ms=` = lúc thực sự hết tiếng; `src=listen_stop`: mũ báo hết
câu khi thả PTT, mốc chính là lúc nhận `listen stop`), `asr_done` (`text_len=`, `dur_ms=`), `llm_first_token`, `llm_done`,
`tts_first_audio`, `first_packet_sent`, `quick_phrase` (`task=`), `mcp_call` (`tool=`, `ms=` round-trip; `t_ms` = lúc gửi lệnh).

**Lấy log** (log level `INFO`, biến `XIAOZHI_LOG_LEVEL`): xiaozhi ghi file `tmp/server.log` trong container (volume `xiaozhi_tmp`):

```bash
cd server
docker compose cp xiaozhi:/opt/xiaozhi-esp32-server/tmp/server.log ./server.log
# hoặc từ stdout của container (mã màu ANSI được bỏ tự động)
docker compose logs --no-color --since 30m xiaozhi > xiaozhi.log
```

**Chạy**:

```bash
.venv/Scripts/python tools/tests/e2e/latency.py server/server.log --turns
.venv/Scripts/python tools/tests/e2e/latency.py xiaozhi.log --since "2026-10-06 14:00" --kind voice --check
.venv/Scripts/python tools/tests/e2e/latency.py xiaozhi.log --network 4g --csv lat.csv
```

| Tham số | Ý nghĩa |
|---|---|
| `logs` (≥ 1) | File log hoặc glob (`server.log*`), `-` = stdin |
| `--sid` | Chỉ các session id này (khớp tiền tố, lặp lại được) |
| `--since`, `--until` | Epoch ms/s hoặc `YYYY-MM-DD HH:MM[:SS]` (giờ máy) |
| `--last N` | Chỉ N lượt nói cuối |
| `--kind` | `voice` (có `vad_end`), `text` (chèn văn bản), `quick` (khớp nhanh); lặp lại được |
| `--vad-offset-ms` | Chỉ cho log cũ có dòng `vad_end` (VAD) thiếu `speech_end_ms`: lúc nói xong = `vad_end` − offset. Không áp cho lượt PTT (`src=listen_stop`: nhả nút chính là lúc nói xong); log hiện tại luôn có `speech_end_ms` nên không cần |
| `--mcp-t-is-end` | `t_ms` của `mcp_call` là lúc nhận trả lời (mặc định: lúc gửi) |
| `--network` | `lan` (mục tiêu nói xong → audio 1500 ms) hoặc `4g` (2500 ms) |
| `--csv` | Ghi mỗi lượt một dòng (UTF-8 có BOM, mở được bằng Excel) |
| `--json` | In JSON thay cho bảng |
| `--turns` | In thêm bảng từng lượt |
| `--check` | Thoát mã 1 khi trung vị của một chỉ số có mục tiêu không đạt |

Các chỉ số (trung vị, p90, min, max; cột KQ = ĐẠT/KHÔNG ĐẠT theo trung vị):

| Chỉ số | Chặng | Mục tiêu |
|---|---|---|
| `vad_wait` | lúc hết tiếng → `vad_end` | – |
| `asr` | `vad_end` → `asr_done` | 300 ms (SV2 §4.3, tham khảo) |
| `llm_ttft` | `asr_done` → `llm_first_token` | 400 ms (SV3 §4.1) |
| `llm_total` | `asr_done` → `llm_done` | – |
| `tts_first` | `llm_first_token` → `tts_first_audio` | – |
| `send_first` | `tts_first_audio` → `first_packet_sent` | – |
| `server_total` | `vad_end` → `first_packet_sent` | – |
| `eos_to_audio` | lúc nói xong → gói audio đầu tiên | 1500 ms LAN / 2500 ms 4G (IT1 §3) |
| `asr_to_quick` | `asr_done` → `quick_phrase` | – |
| `eos_to_mcp` | lúc nói xong → lệnh MCP đầu tiên (mọi lượt) | – |
| `eos_to_mcp_quick` | như trên, chỉ lượt khớp nhanh | 800 ms (IT1 §3, SV6 §5.2) |
| `mcp_rtt` | round-trip mọi lệnh MCP | 50 ms (FW4 §4.4) |

Các dòng được nhóm theo `sid` rồi thành lượt nói; nhóm không có mốc mở lượt (ví dụ đồng bộ chuỗi ngay sau khi kết nối) được báo là
`background`. Mã thoát: 0; 1 khi `--check` có chỉ số trượt; 2 sai tham số; 3 không có dòng `[LAT]` nào (bản vá chưa áp dụng hoặc
log level không phải INFO).

`eos_to_audio` của server đo tới lúc **gửi** gói đầu tiên: cộng thêm thời gian mạng + bộ đệm phát của mũ mới là lúc nghe thấy.
Đối chiếu bằng video (IT1 §1) – `scenarios.md` mục "Chỉ tiêu tổng hợp".

---

## 4. `heap_trend.py` – heap và reset của mũ thật

Firmware in `SystemInfo: free sram: <n> minimal sram: <m>` mỗi 10 giây; script cũng nhận `"free_heap":<n>` (JSON của
`self.system.get_status`) nếu dòng log có tiền tố thời gian ESP (`(<ms>)`). Banner khởi động `rst:0x..` hoặc thời gian log quay
lui = một lần reset. Phân tích đoạn khởi động **cuối cùng**: độ dốc (byte/giờ, bình phương tối thiểu), mức giảm giữa 10 % mẫu đầu và
10 % mẫu cuối, `minimal sram` thấp nhất.

```bash
# Ghi log serial (ESP-IDF cài trên máy; container Docker không truy cập được cổng COM)
idf.py -p COM5 monitor | tee helmet.log        # hoặc terminal serial bất kỳ ghi ra file
.venv/Scripts/python tools/tests/e2e/heap_trend.py helmet.log
.venv/Scripts/python tools/tests/e2e/heap_trend.py helmet.log --json --allow-reboot
```

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `log` | – | File log serial, `-` = stdin |
| `--max-drop` | 4096 | Mức giảm cho phép (byte) giữa 10 % đầu và 10 % cuối |
| `--max-slope` | 2048 | Tốc độ giảm cho phép (byte/giờ) trước khi coi là rò rỉ |
| `--min-free` | 30720 | `minimal sram` thấp nhất chấp nhận được (FW7 §5.4: 30 KB) |
| `--min-samples` | 6 | Số mẫu tối thiểu (10 s/mẫu) |
| `--allow-reboot` | tắt | Không đánh trượt khi có reset |
| `--json` | tắt | In JSON |

Kiểm tra: `enough_samples`, `no_steady_decrease` (trượt khi **cả** mức giảm > `--max-drop` **và** độ dốc < −`--max-slope`),
`no_reset`, `minimal_sram`. In `KẾT QUẢ: ĐẠT|KHÔNG ĐẠT`; mã thoát 0 đạt, 1 không đạt, 2 sai tham số, 3 không có mẫu heap.

---

## 5. `make_wavs.py` – chuẩn bị âm thanh

```bash
# Tổng hợp câu của mọi kịch bản qua tts-router (chạy compose kèm docker-compose.debug.yml) vào tools/tests/e2e/audio/
.venv/Scripts/python tools/tests/e2e/make_wavs.py tts --tts-url http://127.0.0.1:8880/v1/audio/speech
.venv/Scripts/python tools/tests/e2e/make_wavs.py tts --tts-url http://127.0.0.1:8880/v1/audio/speech --scenario E4 --scenario E13
.venv/Scripts/python tools/tests/e2e/make_wavs.py tts --tts-url ... --text "Quản gia, close the mask" --name E12

# Chuyển file ghi âm giọng thật (m4a, mp3, ogg, flac, wav...) sang WAV 16 kHz mono (in vị trí lời nói)
.venv/Scripts/python tools/tests/e2e/make_wavs.py convert ghi_am/E13_1.m4a ghi_am/E13_2.m4a

# Phát lặp qua loa máy tính đặt cạnh mũ (E13 với mũ thật): 50 lần, cách nhau 8 giây
.venv/Scripts/python tools/tests/e2e/make_wavs.py play tools/tests/e2e/audio/E13_1.wav tools/tests/e2e/audio/E13_2.wav --repeat 50 --interval 8
```

| Lệnh | Tham số |
|---|---|
| `tts` | `--tts-url` (bắt buộc), `--voice` (mặc định `TTS_VOICE` của server), `--model` (`tts-1`), `--api-key`, `--scenario` (lặp lại được), `--text` + `--name`, `--timeout` (60), `--out` (mặc định `tools/tests/e2e/audio`) |
| `convert` | `files...`, `--out` |
| `play` | `files...`, `--repeat` (50), `--interval` (8 giây); Windows dùng `winsound`, Linux/macOS dùng `aplay`/`paplay`/`afplay` |

Tên file theo quy ước của `device_sim.py`: `<ID>.wav` cho kịch bản một câu, `<ID>_<n>.wav` cho câu thứ n (E13 có 4 câu). WAV được
đệm 300 ms im lặng hai đầu. Câu wake word không nằm trong file (giả lập gửi wake word bằng chữ). Giọng TTS không thay được giọng
thật: IT1 yêu cầu E13 dùng **giọng thật ghi âm** (`convert`).

---

## 6. Quy trình gợi ý

1. Mỗi giai đoạn P1–P8: chạy các kịch bản liên quan (`scenarios.md`, bảng cuối), ghi vào bảng của giai đoạn trong `docs/test-report.md`.
2. Lượt nghiệm thu cuối (sau HW4): toàn bộ E1–E14 trên mũ hoàn chỉnh + các chỉ tiêu IT1 §3.
3. Lỗi phát hiện: ghi vào mục "Nhật ký lỗi" của `docs/test-report.md` và tạo issue gắn nhãn module (`SV3`, `FW5`…).

---

## 7. Xử lý sự cố

| Thông báo / hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `OTA HTTP 302` (hoặc 401) qua Cloudflare, thoát mã 3 | Thiếu hoặc sai `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` (Access chuyển hướng tới trang đăng nhập; 401 khi bật "401 Response for Service Auth policies") |
| `OTA request ... failed`, `OTA HTTP <mã>` | Sai địa chỉ/cổng OTA (`XIAOZHI_OTA_PORT`), server chưa healthy, firewall |
| `OTA returned an 'mqtt' section` / `no 'websocket' section` | Cấu hình server: firmware sẽ chuyển sang MQTT – kiểm tra `server.websocket` (`PUBLIC_WS_URL`) |
| `give --ota-url or --ws-url` | Thiếu địa chỉ server |
| Kết nối bị đóng ngay sau `hello` | Token sai/hết hạn (`XIAOZHI_AUTH_KEY` khác giữa máy chạy `--auth-key` và server), hoặc server không chấp nhận thiết bị |
| Cảnh báo `MCP tools/list not finished ... after hello` | Server khởi tạo MCP chậm: tăng `--mcp-wait` (mặc định 8 s) |
| Cảnh báo `server did not react within 4000 ms of trailing silence` | VAD server không thấy hết câu: WAV quá nhỏ/ồn, hoặc tăng `--tail-ms`; thử `--mode manual` |
| Cảnh báo `... ms of speech not sent: the device was in Speaking state` | Server đã trả lời wake word (lẽ ra im lặng): kiểm tra `enable_greeting: false` và bản vá `wake_no_echo` trong image xiaozhi (`server/xiaozhi/patches/README.md`), hoặc wake word không có trong `wakeup_words` nên bị coi là câu hỏi (E12 báo `wake_word_silent` sai) |
| `PyAV is required for audio` | `pip install -r tools/tests/e2e/requirements.txt` |
| `task 'party_mode' not found` | Chưa bỏ comment `party_mode` trong `server/tasks/tasks.yaml` (hoặc `--tasks-file` trỏ sai) |
| `latency.py: no [LAT] lines found` (mã 3) | Bản vá `[LAT]` chưa có trong image xiaozhi, log level khác INFO, hoặc file log sai |
| `heap_trend.py: no 'free sram' samples found` (mã 3) | Log không phải log serial của mũ, hoặc thiếu đoạn chạy đủ lâu |

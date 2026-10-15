# Kịch bản nghiệm thu E1–E14 (IT1)

Quy trình từng bước cho các kịch bản của `docs/modules/IT1-tich-hop-nghiem-thu.md` §2 và cách đo các chỉ tiêu §3. Với mỗi kịch bản:
phần **tự động** chạy bằng `device_sim.py` (giả lập mũ, chạy với server thật), phần **cần mũ thật**, **tiêu chí đạt** và **bằng chứng**
cần lưu. Công cụ: `README.md` cùng thư mục. Ghi kết quả vào bảng E1–E14 của `docs/test-report.md`.

`device_sim.py` kiểm tra được phần server (ASR khi dùng WAV, LLM, tool MCP, TTS, độ trễ phía server, giao thức). Nó **không** thay
được mũ thật ở: âm học (mic, loa, tiếng vọng), nhận wake word trên ESP32, servo/đèn/HUD thật, nút bấm, Wi-Fi, nguồn. Một kịch bản
chỉ "đạt" khi cả hai phần đều đạt (trừ kịch bản chỉ có một phần).

---

## 0. Chuẩn bị chung

**Server** (`server/`, xem `server/.env.example`): stack chạy, mọi service healthy (`docker compose ps`). Để gọi trực tiếp tts-router /
ASR / LiteLLM từ máy host khi cần: thêm `-f docker-compose.debug.yml`.

**Biến dùng trong các lệnh dưới** (Git Bash; thay IP LAN của server):

```bash
SIM=".venv/Scripts/python tools/tests/e2e/device_sim.py"
OTA=http://192.168.1.27:8003/xiaozhi/ota/
```

**File âm thanh** (tùy chọn, cho các lệnh `--input audio`): `make_wavs.py tts --tts-url http://127.0.0.1:8880/v1/audio/speech`
(giọng TTS), hoặc ghi giọng thật rồi `make_wavs.py convert`. Đặt tên `audio/<ID>.wav`, E13 là `audio/E13_1.wav` … `E13_4.wav`.

**Mũ**: firmware build và nạp theo `firmware/main/boards/jarvis-helmet/README.md` (biến thể wake word theo `docs/wakeword-report.md`),
OTA URL trỏ tới server (P2–P7: `http://<IP-LAN>:8003/xiaozhi/ota/`; P8: `https://jarvis.<domain>/xiaozhi/ota/` + token Cloudflare
trong phân vùng `jsecrets`). Log serial khi cần: `idf.py -p COM5 monitor | tee <tên>.log`.

**Đo bằng video**: điện thoại quay cận mũ (thấy servo/đèn/HUD, nghe được tiếng); dùng chế độ nhiều khung hình/giây nếu có, đếm khung để
đo thời gian. **Độ trễ phía server**: `latency.py` trên log xiaozhi (README mục 3).

**Bằng chứng**: lưu ngoài repo (ví dụ thư mục theo ngày), ghi tên file vào cột "Bằng chứng" của `docs/test-report.md`. Với
`device_sim.py`: thư mục `tools/tests/e2e/out/<lần chạy>/` (`summary.json`, `turn<NNN>_reply.wav`).

---

## E1 – "Jarvis, mấy giờ rồi?"

- **Kỳ vọng (IT1)**: trả lời tiếng Việt, đúng giờ, giọng quản gia.
- **Chuẩn bị**: `TZ=Asia/Ho_Chi_Minh` và `server.timezone_offset: 7` (mặc định của template); giọng đã chọn (`TTS_VOICE`).
- **Tự động**:
  ```bash
  $SIM --ota-url $OTA --scenario E1                  # chèn văn bản
  $SIM --ota-url $OTA --scenario E1 --input audio    # audio/E1.wav, đi qua VAD + ASR
  ```
  Bắt buộc: `reply` (có câu trả lời), `no_device_action` (không động vào phần cứng). Cảnh báo: `reply_has_hour` (câu trả lời có số
  giờ hiện tại; LLM viết giờ bằng chữ thì chỉ là WARN – kiểm tra bằng tai), `glyphs`.
- **Mũ thật**: nói "Jarvis", rồi "mấy giờ rồi?" (hoặc liền một hơi "Jarvis, mấy giờ rồi?"). Nghe: tiếng Việt, giờ khớp đồng hồ điện
  thoại, lời gọn kiểu quản gia ("thưa ngài"), giọng nam trầm đã chọn.
- **Đạt khi**: phần tự động PASS; trên mũ trả lời đúng giờ (lệch ≤ 1 phút), đúng ngôn ngữ và phong cách.
- **Bằng chứng**: `summary.json`, `turn001_reply.wav`; video/ghi âm trên mũ.

## E2 – "Jarvis, what's the weather like?"

- **Kỳ vọng (IT1)**: trả lời tiếng Anh, hoặc lịch sự báo là không có dữ liệu (server không có plugin thời tiết).
- **Tự động**: `$SIM --ota-url $OTA --scenario E2` (thêm `--input audio` để thử ASR tiếng Anh). Bắt buộc: `reply`. Cảnh báo:
  `reply_english`.
- **Mũ thật**: nói câu tiếng Anh. Nghe câu trả lời tiếng Anh; không bịa số liệu thời tiết. Nếu bật Kokoro (`TTS_ENGLISH_ENGINE=kokoro`,
  profile `english-tts`), câu thuần tiếng Anh dùng giọng Anh-Anh (`server/tts/README.md` mục 7).
- **Đạt khi**: trả lời tiếng Anh, lịch sự, không bịa dữ liệu.
- **Bằng chứng**: `summary.json` (trường `reply_text`), WAV trả lời.

## E3 – "Jarvis, open mask rồi bật đèn mắt lên"

- **Kỳ vọng (IT1)**: gọi 2 tác vụ đúng thứ tự; mặt nạ mở, đèn sáng; HUD cập nhật.
- **Tự động**: `$SIM --ota-url $OTA --scenario E3` (và `--input audio`). Bắt buộc: `actions:mask_open,eyes_on` – hai hành động xuất
  hiện đúng thứ tự (server gộp các tác vụ của một lượt thành **một** `self.sequence.run`, D4; xem `mcp_calls` trong `summary.json`).
- **Mũ thật**: mặt nạ đang đóng, đèn tắt. Nói câu lệnh. Quan sát: mặt nạ mở **rồi** đèn mắt sáng; HUD đổi trạng thái/hiện thông tin.
- **Đạt khi**: đúng thứ tự, đủ hai hành động, không có hành động thừa gây hại.
- **Bằng chứng**: `summary.json`, video trên mũ; dòng log server `[helmet_tasks] merged ... task call(s) into one sequence` (nếu LLM gọi
  task) hoặc dòng `[LAT] ... stage=mcp_call`.

## E4 – "Mở mặt nạ" (khớp nhanh)

- **Kỳ vọng (IT1)**: servo chạy ≤ 800 ms sau khi nói xong (LAN).
- **Tự động**:
  ```bash
  $SIM --ota-url $OTA --scenario E4                                 # chỉ đường server (không VAD/ASR)
  $SIM --ota-url $OTA --scenario E4 --input audio --mode manual     # như PTT: gồm ASR
  $SIM --ota-url $OTA --scenario E4 --input audio                   # tự động theo VAD: gồm cả 700 ms im lặng VAD
  ```
  Bắt buộc: `actions:mask_open`, `max:eos_to_first_mcp_ms:800`. Nhớ ghi **chế độ** đã đo: ở chế độ `auto`, thời gian tính từ lúc nói
  xong gồm khoảng im lặng VAD chờ (`min_silence_duration_ms: 700` của cấu hình xiaozhi), nên khó đạt 800 ms; ở chế độ PTT thì không.
- **Mũ thật** (LAN): mặt nạ đóng. Lặp ≥ 5 lần mỗi chế độ: (a) nhấn giữ PTT, nói "mở mặt nạ", thả tay; (b) "Jarvis, mở mặt nạ" liền
  một hơi. Đóng lại giữa các lần (nút mặt nạ). Quay video, đếm từ lúc dứt tiếng tới lúc servo bắt đầu chạy. Đồng thời lấy log server:
  `latency.py <log> --kind quick --turns` → chỉ số `eos_to_mcp_quick` (lúc nói xong → gửi lệnh MCP) và `mcp_rtt`.
- **Đạt khi**: trung vị ≤ 800 ms (video), cùng chiều với `eos_to_mcp_quick`. Không đạt ở chế độ wake word vì thời gian chờ VAD: ghi rõ
  vào nhật ký lỗi để quyết định (giảm `min_silence_duration_ms` hay chấp nhận đo bằng PTT).
- **Bằng chứng**: video, bảng `latency.py`, `summary.json`.

## E5 – Thêm `party_mode` vào YAML rồi ra lệnh

- **Kỳ vọng (IT1)**: chạy được, không khởi động lại gì.
- **Chuẩn bị**: ghi lại thời gian chạy của container (`docker compose ps` cột STATUS "Up …").
- **Các bước**:
  1. Trên server, mở `server/tasks/tasks.yaml`, bỏ dấu `#` ở khối ví dụ `party_mode` (cuối file; giữ đúng thụt lề), lưu. Thư mục
     `server/tasks` được mount vào container (chỉ đọc), plugin `helmet_tasks` tự nạp lại khi `mtime` của file đổi, kiểm tra ở mỗi câu nói.
  2. (Khuyên dùng) Kiểm tra file trước: `.venv/Scripts/python tools/tasks_cli.py validate --file server/tasks/tasks.yaml`.
  3. **Không** khởi động lại container nào. Ra lệnh:
     ```bash
     $SIM --ota-url $OTA --scenario E5 --expect-task party_mode
     ```
     Bắt buộc: `tool:self.sequence.run`, `task_steps` (bước gửi xuống trùng bước của `party_mode` trong `tasks.yaml` máy chạy giả lập –
     phải là cùng một file với server).
  4. Trên mũ thật: "Jarvis, bật party mode" → mặt nạ mở, đèn mắt "thở", HUD hiện thẻ "PARTY MODE".
- **Đạt khi**: tác vụ chạy ở lượt hội thoại ngay sau khi lưu file; `docker compose ps` cho thấy không container nào khởi động lại.
- **Bằng chứng**: log server `[helmet_tasks] loaded <n> task(s) from ...` sau khi sửa, `summary.json`, video.
- **Thêm (SV6 tiêu chí 5)**: cố ý làm hỏng YAML (ví dụ thụt lề sai), nói một câu: plugin giữ bảng cũ và log lỗi kèm số dòng. Sửa lại.
  Sau khi thử xong có thể comment lại `party_mode`.

## E6 – "Jarvis, hỏi chuyên gia: …"

- **Kỳ vọng (IT1)**: gọi `ask_expert`, trả lời ngắn gọn.
- **Chuẩn bị**: `jarvis-cloud` dùng được (khóa API trong `server/.env`, còn ngân sách `CLOUD_MONTHLY_BUDGET_USD`).
- **Tự động**: `$SIM --ota-url $OTA --scenario E6` (timeout lượt 60 s). Bắt buộc: `reply`, `no_device_action`. `ask_expert` là plugin
  phía server nên giả lập không thấy trực tiếp: xác nhận bằng log xiaozhi dòng `ask_expert: <ms> ms, <n> chars, model=jarvis-cloud`
  (lỗi: `ask_expert: timeout ...` / `ask_expert: HTTP ...`).
- **Mũ thật**: hỏi một câu kiến thức ("Jarvis, hỏi chuyên gia: vì sao bầu trời có màu xanh?"). Nghe câu trả lời ngắn, đúng ngôn ngữ đã hỏi.
- **Đạt khi**: có dòng log `ask_expert` thành công, câu trả lời ngắn gọn, đúng ngôn ngữ.
- **Bằng chứng**: `summary.json`, trích log xiaozhi (và log chi phí cloud của LiteLLM).

## E7 – Bấm PTT trong lúc Jarvis đang nói

- **Kỳ vọng (IT1)**: dừng nói ≤ 300 ms, chuyển sang nghe.
- **Tự động (phần server)**: `$SIM --ota-url $OTA --scenario E7` – khi câu trả lời dài đang phát 1,5 s, giả lập gửi `abort` rồi
  `listen start` (như bấm PTT, D8). Bắt buộc: `max:abort_to_tts_stop_ms:300` (server xác nhận dừng), `max:abort_to_last_audio_ms:500`
  (không còn audio tới sau 500 ms).
- **Mũ thật**: yêu cầu một câu trả lời dài ("kể cho tôi nghe một câu chuyện thật dài về Iron Man"). Khi đang nói, **bấm nhanh** PTT.
  Firmware hủy và xả âm thanh đang phát ngay rồi chuyển sang nghe (LED đỏ / HUD trạng thái nghe). Quay video, đếm từ lúc nhấn nút
  tới lúc tắt tiếng. Lặp ≥ 5 lần.
- **Đạt khi**: phần tự động PASS; trên mũ tiếng dừng ≤ 300 ms (trung vị) và mũ nghe được câu tiếp theo.
- **Bằng chứng**: `summary.json` (`abort_to_*`), video.

## E8 – Gọi "Jarvis" trong lúc Jarvis đang nói

- **Kỳ vọng (IT1)**: ngắt lời được nếu có khử vọng; nếu không thì ghi là giới hạn.
- **Tự động (phần server)**: `$SIM --ota-url $OTA --scenario E8` – `abort` với lý do `wake_word_detected`. Bắt buộc:
  `max:abort_to_tts_stop_ms:300`.
- **Mũ thật**: firmware mặc định `CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n`: **tắt nhận wake word khi đang nói** (mic và loa cùng trong mũ,
  không có AEC; AEC phía server v0.9.7 chỉ chạy qua MQTT, D12). Kết quả mong đợi với bản mặc định: gọi "Jarvis" không ngắt lời →
  ghi **"Giới hạn đã chấp nhận (D12) – dùng PTT"**.
- **Thử nghiệm tùy chọn**: build với `CONFIG_JARVIS_WAKE_WORD_BARGE_IN=y` (thêm vào `sdkconfig_append` của biến thể trong `config.json`),
  thử 10 lần gọi "Jarvis" khi đang nói (số lần ngắt được) và để mũ đọc câu trả lời dài 10 phút không ai nói (số lần tự kích). Ghi kết quả
  vào `docs/wakeword-report.md` mục ngắt lời.
- **Bằng chứng**: `summary.json`; ghi chú cấu hình firmware đã thử.

## E9 – Tắt server, dùng nút mặt nạ (offline)

- **Kỳ vọng (IT1)**: mặt nạ và đèn vẫn chạy; HUD hiện OFFLINE. Không giả lập được (chỉ mũ thật).
- **Các bước**:
  1. Mũ đang chạy bình thường. Dừng server: `cd server && docker compose stop xiaozhi` (hoặc rút mạng máy server).
  2. Bấm PTT một lần: mũ thử kết nối và thất bại (âm báo lỗi, chữ "Không thể kết nối đến dịch vụ…") → biểu tượng **OFFLINE** bật.
     (WebSocket chỉ kết nối khi bắt đầu hội thoại, nên trước lần thử này mũ chưa biết server đã tắt.) Cách khác: tắt Wi-Fi → OFFLINE
     bật ngay.
  3. Nút mặt nạ: bấm 1 lần → mở (chuỗi `open_mask`), bấm lần nữa → đóng; nhấn giữ ≥ 800 ms → đèn mắt `power_up` / `fade_out`; bấm đúp
     khi mặt nạ đang chạy → dừng tại chỗ.
  4. Bật lại server (`docker compose start xiaozhi`), bấm PTT: hội thoại chạy lại, OFFLINE tắt khi mũ vào trạng thái nghe/nói.
- **Đạt khi**: mọi thao tác nút mặt nạ chạy khi không có server/mạng; OFFLINE hiển thị đúng lúc và tắt khi có kết nối lại.
- **Bằng chứng**: video, ảnh HUD có OFFLINE.

## E10 – Tắt Wi-Fi nhà khi hotspot đang bật

- **Kỳ vọng (IT1)**: tự chuyển sang hotspot ≤ 30 giây, dùng tiếp qua Cloudflare.
- **Chuẩn bị**:
  - Mũ đã lưu cả Wi-Fi nhà và hotspot điện thoại (board README mục 5; iPhone bật "Tối đa hóa tương thích", Android 2.4 GHz).
  - OTA URL của mũ là địa chỉ Cloudflare `https://jarvis.<domain>/xiaozhi/ota/`, server có `PUBLIC_WS_URL=wss://jarvis.<domain>/xiaozhi/v1/`,
    token Cloudflare đã nạp vào mũ (board README mục 7). Với OTA URL LAN, sang mạng 4G mũ sẽ không tới được server.
  - Firmware mặc định `CONFIG_JARVIS_WIFI_FAST_RESCAN=y` (quét lại 3 → 30 giây).
- **Tự động (đường Cloudflare)**: từ laptop nối hotspot (4G):
  ```bash
  CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... $SIM --ota-url https://jarvis.<domain>/xiaozhi/ota/ --scenario E1
  ```
  Xác nhận OTA + WebSocket qua tunnel, xác thực Access và hội thoại trọn vẹn. Chạy thêm một lần **không** đặt hai biến trên: phải bị
  chặn (`OTA HTTP 302`, hoặc 401 nếu bật "401 Response for Service Auth policies" trong Cloudflare; thoát mã 3).
- **Mũ thật**: mũ đang ở Wi-Fi nhà, hotspot bật. Tắt Wi-Fi nhà (tắt router/AP), bấm đồng hồ. Theo dõi HUD (biểu tượng Wi-Fi, OFFLINE)
  và log serial (thời điểm mất kết nối, thời điểm kết nối hotspot). Khi đã nối hotspot, nói "Jarvis, mấy giờ rồi?".
- **Đạt khi**: kết nối lại ≤ 30 giây; hội thoại qua Cloudflare chạy; độ trễ nói xong → tiếng trả lời ≤ 2,5 s qua 4G
  (`latency.py <log> --network 4g`, đối chiếu video).
- **Bằng chứng**: log serial, video có đồng hồ, bảng `latency.py`.

## E11 – Câu hiển thị đủ dấu tiếng Việt

- **Kỳ vọng (IT1)**: không thiếu ký tự.
- **Tự động**: `$SIM --ota-url $OTA --scenario E11`. Bắt buộc: `reply`, `glyphs` (mọi chữ server gửi để hiển thị đều nằm trong dải ký
  tự của font HUD và ở dạng NFC), `vi_diacritics`.
- **Mũ thật**:
  1. Hiển thị cố định đúng câu kiểm tra của FW6 qua server MCP cục bộ của mũ (cùng mạng LAN):
     ```bash
     .venv/Scripts/python tools/servo_calibrate.py --host <IP-mũ> --call self.display.show_info \
         '{"title": "Kiểm tra dấu", "text": "Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài!", "seconds": 10}'
     ```
     Chụp ảnh HUD: đủ dấu, không có ô vuông thay ký tự.
  2. Nói câu của kịch bản E11, chụp phụ đề trên HUD.
- **Đạt khi**: phần tự động PASS; ảnh HUD không thiếu ký tự nào.
- **Bằng chứng**: ảnh HUD (cả hai bước), `summary.json`.

## E12 – "Hàu ơi, close the mask"

- **Kỳ vọng (IT1)**: wake word tiếng Việt (tên chính thức "Hàu", D34) hoạt động, mặt nạ đóng.
- **Tự động**: `$SIM --ota-url $OTA --scenario E12` – giả lập gửi chữ wake word "Hàu ơi" rồi câu lệnh. Bắt buộc:
  `wake_word_silent` (sau wake word – 800 ms khi chèn chữ, hoặc tới lúc nói xong câu lệnh khi phát WAV – server không gửi
  `stt`, câu đáp, âm thanh hay emotion nào: nó nhận "Hàu ơi" là wake word trong `wakeup_words`, chứ không coi là câu hỏi) và
  `actions:mask_close`.
- **Mũ thật**: cần biến thể `jarvis-helmet-mww` (B2, model microWakeWord "Hàu ơi" đã train, D33); phương án A
  (`wn9_jarvis_tts`) chỉ có "Jarvis", B1 (`jarvis-helmet-multinet`) chỉ có "jia wei si" + "guan jia" ("Quản gia", tên cũ).
  Mặt nạ mở sẵn; nói "Hàu ơi", rồi "close the mask" (hoặc liền một hơi); server nhận "Hàuơi" trong `wakeup_words`.
- **Đạt khi**: mũ thức dậy với "Hàu ơi" và mặt nạ đóng. Độ nhạy (≥ 18/20) và kích nhầm đo theo `docs/wakeword-report.md`.
- **Bằng chứng**: `summary.json`, video, biến thể firmware đã dùng.

## E13 – 50 lệnh liên tiếp

- **Kỳ vọng (IT1)**: không treo; heap của thiết bị không giảm dần (log heap).
- **Tự động (phía server)**:
  ```bash
  $SIM --ota-url $OTA --scenario E13                              # 50 lượt, quay vòng 4 lệnh, một kết nối
  $SIM --ota-url $OTA --scenario E13 --input audio --pause-ms 2000 # audio/E13_1..4.wav
  ```
  Bắt buộc: `all_completed` (đủ 50 lượt, không lỗi), `each_turn_responds` (mỗi lượt có lệnh MCP hoặc audio). `latency.py` trên log của
  khoảng thời gian đó cho thống kê độ trễ.
- **Mũ thật** (IT1: phát file âm thanh): ghi giọng thật 4 lệnh ("mở mặt nạ", "đóng mặt nạ", "bật đèn mắt", "tắt đèn mắt"),
  `make_wavs.py convert ...`. Mũ cắm USB vào laptop và ghi log serial (`idf.py -p COM5 monitor | tee e13.log`). Nói "Jarvis" để mở phiên
  (ở chế độ tự động mũ vẫn nghe sau mỗi câu trả lời; phiên tự đóng sau 120 s im lặng), rồi đặt loa máy tính cạnh mũ:
  ```bash
  .venv/Scripts/python tools/tests/e2e/make_wavs.py play tools/tests/e2e/audio/E13_1.wav tools/tests/e2e/audio/E13_2.wav \
      tools/tests/e2e/audio/E13_3.wav tools/tests/e2e/audio/E13_4.wav --repeat 50 --interval 8
  .venv/Scripts/python tools/tests/e2e/heap_trend.py e13.log
  ```
  `--interval` phải dài hơn câu trả lời + chuyển động (lệnh tới lúc mũ còn đang nói sẽ bị bỏ qua). Đếm số lệnh được thực hiện (video
  hoặc `latency.py <log> --turns`). Sau lệnh cuối, để mũ chờ **ít nhất 2 phút** rồi mới dừng ghi log và chạy `heap_trend.py`
  (cần mẫu heap sau khi chạy xong). Firmware in heap mỗi 10 s ở mọi trạng thái; nếu log có khoảng trống > 30 s giữa hai mẫu thì
  `heap_trend.py` báo `no_sample_gaps` KHÔNG ĐẠT (log không đo được, ví dụ firmware cũ hoặc mất dữ liệu serial).
- **Đạt khi**: phần tự động PASS; mũ thực hiện đủ 50 lệnh, không treo/không reset; `heap_trend.py` ĐẠT (`no_steady_decrease`,
  `no_reset`, `minimal_sram` ≥ 30 KB).
- **Bằng chứng**: `summary.json`, `e13.log` + kết quả `heap_trend.py --json`, video.

## E14 – Đội mũ dùng liên tục 2 giờ bằng sạc dự phòng

- **Kỳ vọng (IT1)**: không reset, sạc dự phòng không tự tắt, VSENSE ≥ 4,6 V. Không giả lập được.
- **Chuẩn bị**: sạc dự phòng đầy (dòng ra ≥ 2,4 A); mũ lắp hoàn chỉnh. **Không** cắm USB laptop vào mũ trong lúc thử (cáp USB cũng cấp
  nguồn cho board, kết quả về sạc dự phòng sẽ sai). Laptop chỉ cần cùng mạng Wi-Fi để đọc trạng thái qua server MCP cục bộ.
- **Các bước**:
  1. Lấy trạng thái định kỳ (mỗi 5 phút) – JSON có `uptime_s`, `vbus_mv`, `free_heap`, `rssi`:
     ```bash
     while true; do date; .venv/Scripts/python tools/servo_calibrate.py --host <IP-mũ> --call self.system.get_status; sleep 300; done | tee e14_status.log
     ```
  2. Dùng mũ như thật trong 2 giờ: mỗi 5–10 phút một lượt hội thoại, mở/đóng mặt nạ, bật/tắt đèn; đội mũ, đi lại.
  3. Theo dõi HUD (cảnh báo điện áp thấp khi dưới 4600 mV) và đèn của sạc dự phòng.
- **Đạt khi**: `uptime_s` tăng liên tục tới ≈ 7200 (không reset); mọi mẫu `vbus_mv` ≥ 4600; sạc dự phòng không tắt; không vấp tiếng.
- **Bằng chứng**: `e14_status.log`, ảnh HUD đầu/cuối, ghi chú model sạc dự phòng.

---

## Chỉ tiêu tổng hợp (IT1 §3)

| Chỉ tiêu | Mục tiêu | Cách đo |
|---|---|---|
| Nói xong → tiếng trả lời đầu tiên (LAN, trung vị) | ≤ 1,5 s | Mũ thật ở LAN, ≥ 20 lượt nói bằng giọng: `latency.py <log> --kind voice` (chỉ số `eos_to_audio`). Đối chiếu bằng tai: quay video 5 câu, đo từ lúc nói xong tới lúc nghe tiếng trả lời (thời gian mạng + bộ đệm phát của mũ nằm ngoài log server) |
| Như trên, qua 4G | ≤ 2,5 s | Như trên với mũ nối hotspot qua Cloudflare: `latency.py <log> --kind voice --network 4g` + video |
| Khớp nhanh: nói xong → servo chạy (LAN) | ≤ 0,8 s | Kịch bản E4 (video + `eos_to_mcp_quick`) |
| Từ khóa lệnh nhận đúng (ASR) | ≥ 95 % | `tools/tests/asr/RESULTS.md` trên **bộ thu âm thật** (`tools/tests/asr/README.md`) |
| Gọi đúng tool (LLM) | ≥ 95 % | `tools/tests/llm/run_tool_calls.py --env-file server/.env` (LiteLLM qua `docker-compose.debug.yml`, mặc định `http://127.0.0.1:4000/v1`) |
| Wake word nhận đúng (yên tĩnh) | ≥ 18/20 mỗi từ | `docs/wakeword-report.md` |
| Kích nhầm wake word | ≤ 1 lần/giờ | `docs/wakeword-report.md` |
| Pin | ≥ 2 giờ không sự cố | E14 |

`eos_to_audio` của `latency.py` lấy lúc nói xong từ `speech_end_ms` của dòng `vad_end` (VAD server); lượt PTT dùng lúc thả nút.

---

## Kịch bản theo giai đoạn (gợi ý cho các đợt thử con)

IT1 §4: mỗi giai đoạn kết thúc bằng một đợt thử con chỉ gồm các kịch bản liên quan; đợt nghiệm thu cuối chạy toàn bộ E1–E14.

| Giai đoạn | Kịch bản / bài thử |
|---|---|
| P1 Phần cứng breadboard | Tiêu chí HW2, FW1 (firmware gốc + board), FW1 #5 chuyển hotspot (như E10, chưa qua Cloudflare) |
| P2 Server AI | E1, E2, E6; `device_sim.py` E1/E2/E6/E11 (phần server); `latency.py` (LAN); ASR A/B, bench TTS, bộ thử LLM |
| P3 Cơ cấu chấp hành | E3 (tool trực tiếp hoặc tác vụ), tiêu chí FW2–FW4, HW1, HW3; E13 phía server |
| P4 Tác vụ tùy biến + nút bấm | E3, E4, E5, E7, E9 |
| P5 Giao diện | E11 (ảnh HUD), tiêu chí FW6 |
| P6 Lắp ráp | Tiêu chí HW4, lặp lại bài thử HW1/HW3; E13 (mũ thật), E14 |
| P7 Wake word | E12, E8, chỉ tiêu wake word (`docs/wakeword-report.md`) |
| P8 Ngoài trời & nghiệm thu | E10, chỉ tiêu 4G, rồi toàn bộ E1–E14 + mọi chỉ tiêu IT1 §3 |

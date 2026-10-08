# Báo cáo wake word "Jarvis" + "Quản gia" (FW7)

Mẫu báo cáo so sánh các phương án wake word của `docs/modules/FW7-wake-word.md` (quyết định D11, D12 trong hợp đồng triển khai):
cách build từng biến thể, phương pháp đo (FW7 §3), bảng kết quả để điền cho A, B1, B2, tiêu chí chọn, pipeline train cho B2 và giới
hạn ngắt lời bằng wake word. Kết luận ngắn đưa vào `docs/test-report.md` (bảng P7).

---

## 0. Kết luận (điền sau khi đo)

| Mục | Giá trị |
|---|---|
| Phương án chọn | A / B1 / B2 |
| Biến thể build (`--name`) và option đã chỉnh | … |
| Lý do | … |
| Ngày, commit firmware | … |
| `CONFIG_JARVIS_WAKE_WORD_BARGE_IN` | n (mặc định) / y – lý do |
| Giới hạn còn lại | … |

---

## 1. Các phương án và cách build

| Phương án | Từ đánh thức | Cơ chế trên ESP32-S3 | Biến thể `config.json` (`--name`) |
|---|---|---|---|
| **A** (mốc so sánh, dự phòng) | "Jarvis" | WakeNet9 `wn9_jarvis_tts` chạy trong AFE | `jarvis-helmet` (LCD 7 chân: `jarvis-helmet-7pin`) |
| **B1** (không cần train) | "jia wei si" (贾维斯 = Jarvis) + "guan jia" (管家 = quản gia) | MultiNet7 tiếng Trung lượng tử hóa, lệnh khai báo bằng pinyin | `jarvis-helmet-multinet` |
| **B2** (cần train) | WakeNet "Jarvis" + microWakeWord "Quản gia" | `wn9_jarvis_tts` + model microWakeWord (TFLite Micro) chạy song song trên cùng đầu ra AFE | `jarvis-helmet-mww` |

Lệnh build (Git Bash, từ thư mục gốc repo; xem thêm `firmware/main/boards/jarvis-helmet/README.md` mục 1–3):

```bash
# A
tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet --language vi-VN --wake-word wn9_jarvis_tts
# B1 – KHÔNG truyền --wake-word (wake word do sdkconfig_append của biến thể quyết định)
tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet-multinet --language vi-VN
# B2 – cần model đã train (mục 2), nếu không bộ microWakeWord tự tắt và firmware chạy như A
tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet-mww --language vi-VN --wake-word wn9_jarvis_tts

# Lấy file .bin về máy: FW_OUT=out/fw ; build sạch: FW_CLEAN=1 ; build song song nhiều biến thể: FW_VOLUME=jarvis-fw-<tên>
FW_OUT=out/fw FW_VOLUME=jarvis-fw-multinet tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet-multinet --language vi-VN
```

Cấu hình riêng của từng biến thể (`firmware/main/boards/jarvis-helmet/config.json`, ngoài các dòng chung: LCD, bảng phân vùng riêng,
`CONFIG_HTTPD_WS_SUPPORT=y`):

| Biến thể | `sdkconfig_append` riêng |
|---|---|
| `jarvis-helmet` | – (wake word chọn bằng `--wake-word wn9_jarvis_tts`) |
| `jarvis-helmet-multinet` | `CONFIG_USE_CUSTOM_WAKE_WORD=y`, `CONFIG_USE_AFE_WAKE_WORD=n`, `CONFIG_SR_MN_CN_MULTINET7_QUANT=y`, `CONFIG_CUSTOM_WAKE_WORD="jia wei si;guan jia"`, `CONFIG_CUSTOM_WAKE_WORD_DISPLAY="Jarvis;Quản gia"` |
| `jarvis-helmet-mww` | `CONFIG_JARVIS_MICRO_WAKE_WORD=y` |

Ghi chú khi build và nạp:
- `scripts/build.py` **tạo lại `sdkconfig` mỗi lần build**: option muốn giữ (ngưỡng, `CONFIG_JARVIS_WAKE_WORD_BARGE_IN`…) phải thêm vào
  `sdkconfig_append` của biến thể trong `config.json`, không chỉnh bằng menuconfig.
- Model WakeNet/MultiNet nằm trong phân vùng `assets`: khi đổi biến thể hoặc wake word phải **nạp đầy đủ** (`idf.py flash`, hoặc lệnh
  `esptool write-flash` có `generated_assets.bin` ở 0x800000 trong board README), `app-flash` không đủ. Model microWakeWord (B2) nằm
  trong ảnh app.
- Mọi biến thể dùng chung `type` = `jarvis-helmet`, nên OTA tự host gửi cùng một firmware cho mọi biến thể: nạp các bản thử bằng cáp,
  sau khi chọn chỉ giữ một biến thể.
- **B1 – ngưỡng**: `CONFIG_CUSTOM_WAKE_WORD_THRESHOLD` (1–99, mặc định 20, càng nhỏ càng nhạy) chưa đặt trong `config.json`, nên dùng
  mặc định. Muốn chỉnh: thêm `"CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=<n>"` vào `sdkconfig_append` của `jarvis-helmet-multinet`.
- **B1 – nhiều lệnh**: bản vá `scripts/build_default_assets.py` (hàm `build_custom_wake_word_commands`, xem `firmware/PATCHES.md`) tách
  `CONFIG_CUSTOM_WAKE_WORD` theo dấu `;` thành nhiều lệnh MultiNet trong `index.json` của phân vùng assets; chữ hiển thị
  (`CONFIG_CUSTOM_WAKE_WORD_DISPLAY`, cũng tách theo `;`) ghép theo vị trí, thiếu thì lặp chữ cuối – tiện để thêm **biến thể phát âm**
  cho cùng một từ, ví dụ `"jia wei si;guan jia;<pinyin khác>"` với `"Jarvis;Quản gia"` (lệnh thứ ba cũng hiện "Quản gia"; phải thử
  xem MultiNet có nhận pinyin đó không). Lệnh dài quá 63 byte bị cảnh báo. Test: `tools/wakeword/tests/test_build_default_assets_multinet.py`.
- **B2 – ngưỡng**: `CONFIG_JARVIS_MWW_CUTOFF_PCT` (50–100, mặc định 97) và `CONFIG_JARVIS_MWW_SLIDING_WINDOW` (1–20, mặc định 5).
  Firmware lấy trung bình `WINDOW` xác suất gần nhất (thang 0–255) so với `CUTOFF_PCT × 255 / 100`, sau mỗi lần nhận nghỉ 1 giây; sau
  mỗi lần `Start()` có 1 giây khởi động trước khi được phép nhận. `export/tflite_to_c.py` in sẵn hai dòng Kconfig gợi ý theo manifest
  của trainer.
- Chữ wake word gửi lên server: A "Jarvis"; B1 theo chữ hiển thị ("Jarvis", "Quản gia"); B2 "Jarvis" (WakeNet) hoặc "Quản gia"
  (microWakeWord). Server so khớp sau khi bỏ dấu cách (`wakeup_words`: "Jarvis", "quảngia", "Hàuơi"…) và trả câu đáp tiếng Việt có sẵn (D15).

Xác nhận trên thiết bị (log serial):
- AFE khởi tạo: `Initialized FD AFE, detector: WakeNet, ...` (A, B2) hoặc `detector: MultiNet` (B1); `none` = không có model wake word.
- Mỗi lần nhận: `Wake word detected: <chữ wake word>` (Application).
- B2: `MicroWakeWord: Ready for "Quản gia": model <n> B, stride <s>, cutoff <c>/255 (<pct>%), window <w>, refractory 1000 ms`; với file
  placeholder: `No model linked in (mww_model_data.c is the placeholder), disabled`. Khi nhận: `Detected "Quản gia" (mean <m>/255, cutoff
  <c>, window <w>)`. Mỗi 10 giây: `<n> inferences, avg <us> us, peak probability <p>/255` (lần đầu ở INFO, sau đó ở DEBUG:
  `esp_log_level_set("MicroWakeWord", ESP_LOG_DEBUG)`); `Dropped <n> B of audio (inference too slow?)` nghĩa là CPU không theo kịp.

---

## 2. Pipeline train cho B2 (`tools/wakeword/`)

Chi tiết từng script: `tools/wakeword/README.md`. Tóm tắt luồng (thư mục `out/` tính từ thư mục đang đứng, không commit):

| Bước | Script | Kết quả |
|---|---|---|
| 1. Câu dương bằng TTS | `gen_positives.py --api-url <tts-router hoặc vieneu>/v1/audio/speech --count 3000` | `out/positives/` (WAV 16 kHz mono + `manifest.jsonl`), mọi giọng VieNeu, đổi tốc độ/nhịp/cao độ (VieNeu bỏ qua `speed`); tùy chọn thêm giọng Piper vi_VN |
| 2. Câu âm gần âm | `gen_negatives.py --api-url ... --per-phrase 6` | `out/negatives/` từ `negatives_vi.txt` (quản lý, gia đình, quán gà, hoàng gia…); từ chối câu chứa "quản gia" |
| 3. Giọng của chính mình | `record_personal.py --speaker <tên> --count 80 --accents bac,nam` | `out/personal/` – quan trọng nhất (trainer nhân trọng số 3); nên ghi thêm bằng mic INMP441 của mũ (mục 7) |
| 4. Tăng cường | `augment.py --in out/personal --out out/personal_aug --variants 3 --keep-original` | Vọng vỏ mũ, nhiễu (SNR 3–20 dB), lọc như mic MEMS, đổi âm lượng |
| 5. Train (trên server GPU) | `train.sh --data-dir /mnt/data1/mww --positives out/positives,out/positives_aug,out/personal,out/personal_aug --negatives out/negatives` | `out/model/quan_gia.tflite` + `quan_gia.json` (manifest có `probability_cutoff`, `sliding_window_size`) |
| 6. Chọn ngưỡng (offline) | `eval_wakeword.py --model out/model/quan_gia.tflite --manifest out/model/quan_gia.json --positives <thư mục> --negatives <ghi âm dài> --cutoffs 90:99:1 --windows 3:8:1 --csv out/eval.csv` | Bảng recall và số lần kích nhầm/giờ cho từng cặp (ngưỡng, cửa sổ), cùng thuật toán với firmware |
| 7. Đưa vào firmware | `export/tflite_to_c.py --model out/model/quan_gia.tflite --manifest out/model/quan_gia.json` | Ghi đè `firmware/main/boards/jarvis-helmet/mww_model_data.c`, in dòng Kconfig gợi ý; `--placeholder` khôi phục file rỗng |
| 8. Build, nạp, đo | Biến thể `jarvis-helmet-mww` (mục 1), đo theo mục 3 | Bảng 4.3 |

Lưu ý:
- `train.sh`: GPU `--gpu cdi` (mặc định, server dùng CDI) hoặc `--gpu all` (Docker Desktop); image mặc định
  `ghcr.io/tatertotterson/microwakeword:v32-blackwell` (ghim digest); GPU không phải RTX 50: `--image ghcr.io/tatertotterson/microwakeword:v32`.
  Lần đầu tải nhiều GB dữ liệu. `--dry-run` chỉ in kế hoạch.
- **Giấy phép**: image Blackwell dùng bản TensorFlow không chính thức theo CC BY-NC 4.0, một số bộ dữ liệu âm trainer tải về cũng chỉ
  dùng phi thương mại → model chỉ dùng phi thương mại; không commit model khi chưa rõ giấy phép dữ liệu (repo chỉ có placeholder kích thước 0).
- `eval_wakeword.py` cần `pymicro-features` và `ai-edge-litert` (không có bản Windows): trên Windows chạy trong Docker (lệnh mẫu trong
  `tools/wakeword/README.md`). Mặc định `--min-recall` 0,9 (18/20) và `--max-fa-per-hour` 1 (tiêu chí FW7); mỗi clip dương được thêm
  1,5 s yên lặng phía trước (`--lead-ms`).
- Trên Windows, `gen_positives.py`, `gen_negatives.py`, `record_personal.py` hiện lỗi `UnicodeEncodeError` khi in `--help` trong console
  mã hóa cp1252 (chữ tiếng Việt trong trợ giúp): đặt `PYTHONIOENCODING=utf-8` (hoặc `PYTHONUTF8=1`) trước lệnh.
- Mã C++ trên thiết bị tự viết trên thư viện Apache-2.0 (`esp-tflite-micro`, `esp-micro-speech-features`), không chép mã ESPHome (GPLv3).
  Các component chỉ được thêm vào build khi bật `CONFIG_JARVIS_MICRO_WAKE_WORD` (`main/idf_component.yml`).
- Kiểm tra công cụ (không cần GPU/server): `.venv/Scripts/python -m pytest tools/wakeword/tests -q`.

---

## 3. Phương pháp đo (FW7 §3)

Bộ thử: **20 lần gọi mỗi từ × 3 điều kiện** + **1 giờ podcast/TV tiếng Việt** để đếm kích nhầm. Tiêu chí FW7 §5 tính "ở khoảng cách
đội mũ".

### 3.1 Chuẩn bị

- Nạp đúng biến thể (mục 1), ghi lại commit, ngưỡng (`CUSTOM_WAKE_WORD_THRESHOLD` hoặc `MWW_CUTOFF_PCT`/`MWW_SLIDING_WINDOW`) vào bảng.
- Ghi log serial suốt buổi đo: `idf.py -p COM5 monitor | tee ww_<phương án>_<điều kiện>.log`.
- Chỉ đo khi mũ ở trạng thái **chờ (Idle)**: wake word chỉ bật ở Idle (tắt khi đang nghe; khi đang nói tắt nếu
  `CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n`). Model wake word chỉ được nạp sau khi Wi-Fi kết nối lần đầu kể từ lúc khởi động.
- Người nói: ít nhất người dùng chính; với "Quản gia" đọc xen kẽ giọng Bắc ("kwản za") và giọng Nam ("wảng da"/"quảng ya"); thêm người
  thứ hai nếu có. Ghi rõ người/giọng trong bảng.
- Ghi mức ồn (ứng dụng đo âm thanh trên điện thoại, dBA) cho từng điều kiện.

### 3.2 Độ nhạy (20 lần mỗi từ, mỗi điều kiện)

| Mã điều kiện | Mô tả | Tiêu chí |
|---|---|---|
| Q-W | Phòng yên tĩnh, người dùng **đội mũ**, nói bình thường | ≥ 18/20 |
| M-W | Như Q-W + nhạc nền (loa cách 1–2 m, mức nghe bình thường) | ≥ 15/20 |
| Q-30 | Phòng yên tĩnh, mũ trên bàn, người nói cách 30 cm (tham khảo, giống HW2 #2) | tham khảo |

Mỗi lần thử: mũ ở Idle → nói từ đánh thức một lần → **nhận** nếu trong 2 giây có dòng log `Wake word detected: <từ>` (và mũ phản hồi:
câu đáp, HUD/LED chuyển sang nghe) → **bấm nhanh PTT** để kết thúc phiên, chờ về Idle → lần tiếp theo. Thay đổi nhẹ độ to, tốc độ. Đếm
bằng log: `grep -c "Wake word detected: Quản gia" ww_B1_Q-W.log` (đối chiếu với số lần đã nói).

### 3.3 Kích nhầm (≥ 1 giờ)

- Phát podcast/TV tiếng Việt (giọng nói liên tục) qua loa cách mũ 1–2 m; mũ ở Idle (trên bàn hoặc trên đầu người/manocanh), không ai gọi.
- Mỗi lần mũ tự thức: ghi thời điểm và từ (log `Wake word detected: ...`), bấm PTT kết thúc phiên ngay.
- Kích nhầm/giờ = số lần ÷ số giờ mũ ở Idle. Tính riêng cho "Jarvis" và "Quản gia". Tùy chọn: thêm 1 giờ nhạc.
- Với B2 có thể thu chính đoạn âm thanh đó bằng mic của mũ (mục 7) để chạy lại `eval_wakeword.py --negatives` với ngưỡng khác mà không
  phải phát lại.

### 3.4 Độ trễ phản hồi (FW7 #3)

Quay video 10 lần gọi mỗi từ: từ lúc dứt tiếng wake word tới lúc mũ phản hồi (âm báo/câu đáp, HUD/LED đổi trạng thái). Tiêu chí:
trung vị < 500 ms.

### 3.5 Tài nguyên (FW7 #4)

Bật đủ thành phần của phương án, dùng ≥ 10 phút (có hội thoại, TTS đang phát, HUD chạy): nghe có vấp tiếng không; log serial qua
`tools/tests/e2e/heap_trend.py <log>` → `minimal_sram` ≥ 30 KB (`--min-free 30720` mặc định). B2: thời gian suy luận trung bình (log
mỗi 10 s) và không có cảnh báo `Dropped ... B of audio`.

### 3.6 B2: chọn ngưỡng offline trước khi nạp

Chạy `eval_wakeword.py` (mục 2, bước 6) trên clip dương thật (yên tĩnh, có nhạc – thư mục riêng) và ghi âm âm tính dài. Chọn cặp
(ngưỡng, cửa sổ) có dấu `*` (đạt recall ≥ 90 % và ≤ 1 lần/giờ), ưu tiên kích nhầm thấp; ghi hai dòng Kconfig vào `sdkconfig_append` của
`jarvis-helmet-mww`, build lại, rồi đo trên thiết bị theo 3.2–3.5.

---

## 4. Kết quả (điền)

### 4.1 Phương án A – WakeNet "Jarvis"

| Cấu hình | Giá trị |
|---|---|
| Biến thể / commit | `jarvis-helmet` / … |
| Ngày đo, người nói | … |

| Từ | Điều kiện | Người nói / giọng | Mức ồn (dBA) | Nhận / 20 | Tiêu chí | KQ |
|---|---|---|---|---|---|---|
| Jarvis | Q-W | | | | ≥ 18 | |
| Jarvis | M-W | | | | ≥ 15 | |
| Jarvis | Q-30 | | | | tham khảo | |
| Quản gia | – | – | – | không hỗ trợ | – | N/A |

| Kích nhầm | Nguồn âm | Thời lượng Idle | Số lần | Lần/giờ | Tiêu chí | KQ |
|---|---|---|---|---|---|---|
| Jarvis | Podcast/TV … | … | | | ≤ 1 | |

| Chỉ số khác | Kết quả | Tiêu chí | KQ |
|---|---|---|---|
| Độ trễ phản hồi (trung vị 10 lần) | … ms | < 500 ms | |
| `minimal_sram` thấp nhất | … B | ≥ 30 KB | |
| Vấp tiếng | có / không | không | |

### 4.2 Phương án B1 – MultiNet "jia wei si" + "guan jia"

| Cấu hình | Giá trị |
|---|---|
| Biến thể / commit | `jarvis-helmet-multinet` / … |
| `CONFIG_CUSTOM_WAKE_WORD` / `_DISPLAY` | `"jia wei si;guan jia"` / `"Jarvis;Quản gia"` (ghi lại nếu đã thêm biến thể phát âm) |
| `CONFIG_CUSTOM_WAKE_WORD_THRESHOLD` | 20 (mặc định) / … |

| Từ | Điều kiện | Người nói / giọng | Mức ồn (dBA) | Nhận / 20 | Tiêu chí | KQ |
|---|---|---|---|---|---|---|
| Jarvis ("jia wei si") | Q-W | | | | ≥ 18 | |
| Jarvis ("jia wei si") | M-W | | | | ≥ 15 | |
| Jarvis ("jia wei si") | Q-30 | | | | tham khảo | |
| Quản gia ("guan jia") | Q-W, giọng Bắc | | | | ≥ 18 | |
| Quản gia ("guan jia") | Q-W, giọng Nam | | | | ≥ 18 | |
| Quản gia ("guan jia") | M-W | | | | ≥ 15 | |
| Quản gia ("guan jia") | Q-30 | | | | tham khảo | |

| Kích nhầm | Nguồn âm | Thời lượng Idle | Số lần | Lần/giờ | Tiêu chí | KQ |
|---|---|---|---|---|---|---|
| Jarvis | Podcast/TV … | … | | | ≤ 1 | |
| Quản gia | Podcast/TV … | … | | | ≤ 1 | |

| Chỉ số khác | Kết quả | Tiêu chí | KQ |
|---|---|---|---|
| Độ trễ phản hồi Jarvis / Quản gia | … / … ms | < 500 ms | |
| `minimal_sram` thấp nhất | … B | ≥ 30 KB | |
| Vấp tiếng | có / không | không | |
| So với A: "Jarvis" Q-W / M-W | … / … (A: … / …) | không kém A đáng kể | |

Nếu chỉnh ngưỡng: lặp bảng cho từng giá trị ngưỡng đã thử (ghi cả số nhận và số kích nhầm).

### 4.3 Phương án B2 – WakeNet "Jarvis" + microWakeWord "Hàu ơi" (D33)

Người dùng chọn "Hàu ơi" làm tên tiếng Việt (thay "Quản gia"); các bảng dưới dành cho từ này.

| Cấu hình | Giá trị |
|---|---|
| Biến thể / commit | `jarvis-helmet-mww` / model không commit (giấy phép dataset), build từ bản sao firmware |
| Model (`hau_oi.tflite`), ngày train, số bước | 137.960 B, SHA-256 `a4b71ecb…`, 8/10/2026, 40.000 bước trên 12 nhân CPU batai (35 phút) |
| Dữ liệu train | Chỉ TTS: VieNeu 25 giọng (7.500 clip làm bộ TTS của trainer + 3.000 + 3.000 tăng cường vọng mũ + 3.000 "Hàu ơi" nối lệnh); âm: 1.184 câu VieNeu gần âm/thường ngày + 3.355 câu FLEURS vi train/dev (giọng thật) + dataset âm của trainer; không có giọng thật của người dùng |
| `CONFIG_JARVIS_MWW_CUTOFF_PCT` / `_SLIDING_WINDOW` | 95 / 5 (trainer hiệu chỉnh: 95 / 5) |
| Log `Ready for "Hàu ơi"` | model 137.960 B, stride 2, arena 67.520 B trong PSRAM (49.212 B dùng), ngưỡng 242/255 |

Đánh giá offline (`eval_wakeword.py`, thuật toán của firmware; dương: clip VieNeu mới `--seed 99` không dùng để train;
kích nhầm: 3,18 giờ FLEURS vi_vn test, giọng người thật đọc tin tức):

| Model | Ngưỡng (%) | Cửa sổ | Recall "Hàu ơi" (300) | Recall "Hàu ơi, <lệnh>" (200) | Kích nhầm/giờ | Đạt (`*`) |
|---|---|---|---|---|---|---|
| v1 (chưa có âm FLEURS, chưa có mẫu nối lệnh) | 97 | 5 | 98,0% | ~63% | 3,46 | không |
| v2 | 95 | 5 | 97,7% | 76,0% | 0,63 | kích nhầm đạt; recall gộp 89,2% |
| v2 | 97 | 5 | 97,0% | 72,0% | 0,31 | |
| v2 | 99 | 5 | – | – | 0 | recall gộp 79,4% |

"hầu ơi" / "hậu ơi" / "hào ơi" (120 clip) đánh thức v2 ở 94% (không có trong mẫu âm, xem `tools/wakeword/README.md` mục 9).
Kích nhầm còn lại của v2 trên FLEURS: "…về đích ở vị trí thứ mười sáu…", "…Hội Đồng Bầu Cử…".

Trên thiết bị:

| Từ | Điều kiện | Người nói / giọng | Mức ồn (dBA) | Nhận / 20 | Tiêu chí | KQ |
|---|---|---|---|---|---|---|
| Jarvis (WakeNet) | Q-W | | | | ≥ 18 | |
| Jarvis (WakeNet) | M-W | | | | ≥ 15 | |
| Quản gia (mWW) | Q-W, giọng Bắc | | | | ≥ 18 | |
| Quản gia (mWW) | Q-W, giọng Nam | | | | ≥ 18 | |
| Quản gia (mWW) | M-W | | | | ≥ 15 | |
| Quản gia (mWW) | Q-30 | | | | tham khảo | |

| Kích nhầm | Nguồn âm | Thời lượng Idle | Số lần | Lần/giờ | Tiêu chí | KQ |
|---|---|---|---|---|---|---|
| Jarvis | Podcast/TV … | … | | | ≤ 1 | |
| Quản gia | Podcast/TV … | … | | | ≤ 1 | |

| Chỉ số khác | Kết quả | Tiêu chí | KQ |
|---|---|---|---|
| Độ trễ phản hồi Jarvis / Hàu ơi | … / … ms | < 500 ms | |
| Suy luận microWakeWord trung bình | 7.090–7.122 µs / lần, 49 lần/s (board thử, 8/10) | không có `Dropped ... B of audio` | đạt (không có) |
| `minimal_sram` thấp nhất | 33.468 B (board thử, 76 phút chạy) | ≥ 30 KB | đạt |
| Kích nhầm khi để chờ trong văn phòng có người nói chuyện | 0 lần / 76 phút (v1, 97/5) | ≤ 1 / giờ | tham khảo |
| Vấp tiếng | có / không | không | |

### 4.4 Ngắt lời bằng wake word (mục 6)

| Phương án | `BARGE_IN` | Gọi wake word khi đang nói: ngắt được / 10 | Tự kích do loa trong 10 phút TTS | Kết luận |
|---|---|---|---|---|
| … | n (mặc định) | (không áp dụng: wake word tắt khi đang nói) | 0 (theo thiết kế) | Dùng PTT |
| … | y | | | |

---

## 5. Tiêu chí chọn phương án

1. **Bắt buộc** (FW7 §5): cả hai từ ≥ 18/20 ở Q-W và ≥ 15/20 ở M-W; kích nhầm ≤ 1 lần/giờ mỗi từ; độ trễ phản hồi < 500 ms; không vấp
   tiếng, `minimal_sram` ≥ 30 KB. Kết quả bằng **giọng của người dùng chính** có trọng số cao nhất.
2. A không có "Quản gia" nên chỉ là mốc so sánh và phương án dự phòng.
3. Khi nhiều phương án cùng đạt: ưu tiên **B1** (không cần train, không vướng giấy phép dữ liệu/trainer, ít tài nguyên hơn), trừ khi
   "Jarvis" của B1 kém rõ rệt so với WakeNet của A – khi đó chọn **B2** (giữ WakeNet "Jarvis").
4. Nếu không phương án nào đạt đủ: chọn phương án tốt nhất cho "Jarvis" (thường là A) và ghi "Quản gia" là giới hạn (gọi bằng PTT), hoặc
   chấp nhận B1/B2 với ngưỡng đã chỉnh nếu kích nhầm đạt nhưng độ nhạy hơi thấp – ghi rõ quyết định và số liệu ở mục 0.
5. Ghi quyết định vào mục 0, `docs/test-report.md` (P7) và chỉ giữ một biến thể build cho mũ.

---

## 6. Ngắt lời bằng wake word (barge-in) – giới hạn đã biết

- Mic INMP441 và amply MAX98357A chạy I2S simplex, không có tín hiệu tham chiếu nên **không khử vọng (AEC) được trên thiết bị**: khi mũ
  đang nói, mic thu cả tiếng loa trong mũ và giọng TTS có thể tự kích wake word.
- AEC phía server của xiaozhi-esp32-server v0.9.7 chỉ chạy qua MQTT, mũ dùng WebSocket → không dùng được (D12). Vì vậy ngắt lời bằng giọng
  nói được ghi là **giới hạn**; cách ngắt lời tin cậy là **bấm PTT** (D8, kịch bản E7).
- Firmware: `CONFIG_JARVIS_WAKE_WORD_BARGE_IN` (menu "Jarvis Helmet", **mặc định n**). Với `n`, bản vá có điều kiện trong
  `main/application.cc` (`firmware/PATCHES.md`) **tắt nhận wake word trong trạng thái Speaking**. Đặt `y` để trở lại hành vi upstream:
  bộ nhận wake word của AFE (WakeNet, MultiNet, và microWakeWord – bộ này theo `EnableWakeWordDetection`) vẫn chạy khi đang nói.
- Prompt của Jarvis (`server/xiaozhi/prompts/jarvis-prompt.txt`, quy tắc 5) yêu cầu LLM không bao giờ nói "Jarvis", "quản gia" hay
  "hàu ơi" trong câu trả lời, để giảm tự kích khi bật barge-in.
- Thử barge-in (kịch bản E8, `tools/tests/e2e/scenarios.md`): thêm `"CONFIG_JARVIS_WAKE_WORD_BARGE_IN=y"` vào `sdkconfig_append` của biến
  thể, build và nạp lại; gọi wake word 10 lần khi mũ đang đọc câu trả lời dài (số lần ngắt được), rồi để mũ đọc câu trả lời dài 10 phút
  không ai nói (số lần tự kích). Điền bảng 4.4. Chỉ giữ `y` khi tự kích ≈ 0 và ngắt lời ổn định; nếu không, giữ `n`.

---

## 7. Thu âm bằng mic thật của mũ (audio debugger upstream)

Dùng để ghi mẫu "Quản gia" cho B2 (thư mục `personal_samples`) và ghi âm âm tính dài cho `eval_wakeword.py`, đúng đường âm thanh của mũ.

1. Tạm thêm vào `sdkconfig_append` của biến thể đang dùng (đừng commit):
   `"CONFIG_USE_AUDIO_DEBUGGER=y"` và `"CONFIG_AUDIO_DEBUG_UDP_SERVER=\"<IP-laptop>:8000\""`, build và nạp.
2. Trên laptop cùng mạng (mở cổng UDP 8000 trong firewall):
   ```bash
   python firmware/scripts/audio_debug_server.py --samplerate 16000 --channels 1
   ```
   Script nhận gói UDP và ghi liên tục vào `16000_1.wav` trong thư mục hiện tại (mic của mũ là 16 kHz, một kênh); Ctrl+C để dừng.
3. Firmware gửi âm thanh thô của mic (trước AFE) mỗi khi đang đọc mic (chờ wake word hoặc đang nghe). Đội mũ, đọc "Quản gia" nhiều lần
   (giọng Bắc/Nam, to/nhỏ, nhanh/chậm), rồi cắt thành từng clip WAV 16 kHz mono (ví dụ bằng Audacity), đưa vào thư mục riêng (ví dụ
   `out/personal_helmet/`) và thêm vào `--positives` của `train.sh`.
4. Build lại không có hai dòng trên khi xong (audio debugger gửi âm thanh liên tục qua Wi-Fi, ai bắt được gói UDP đều nghe được).

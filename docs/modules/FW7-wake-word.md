# FW7 – Wake word "Jarvis" + "Quản gia"

| | |
|---|---|
| **Mục tiêu** | Đánh thức trợ lý bằng "Jarvis" và "Quản gia", xử lý ngay trên ESP32, ít kích nhầm |
| **Phụ thuộc** | FW1. SV4 (VieNeu-TTS) dùng để tạo dữ liệu train. Server RTX 5090 dùng để train |
| **Kết quả giao nộp** | Cấu hình wake word đã chọn; nếu chọn phương án B2 thì thêm `micro_wake_word.{h,cc}` + model `.tflite`; thư mục `tools/wakeword/` (script train); báo cáo so sánh các phương án |

## 1. Bối cảnh kỹ thuật
- ESP-SR có sẵn model **`wn9_jarvis_tts`** cho "Jarvis" (WakeNet9, chạy trong AFE).
- "Quản gia" **không có model sẵn**. Espressif có dịch vụ train WakeNet riêng nhưng phải trả phí.
- Kconfig của xiaozhi chỉ cho chọn **một** cơ chế: WakeNet (`USE_AFE_WAKE_WORD`) **hoặc** MultiNet tùy biến (`USE_CUSTOM_WAKE_WORD`).
- Firmware có interface `WakeWord` (`main/audio/wake_word.h`: `Initialize`, `Feed`, `OnWakeWordDetected`, …) để viết thêm bộ nhận dạng.

## 2. Các phương án, làm lần lượt

### A. Chỉ "Jarvis" (làm ngay ở P1)
- Dùng `--wake-word wn9_jarvis_tts`. Là mốc chuẩn để so sánh và dự phòng cho các phương án sau.

### B1. MultiNet tùy biến, không cần train (thử trước)
- Chọn `USE_CUSTOM_WAKE_WORD`, model MultiNet tiếng Trung, khai báo 2 lệnh bằng pinyin:
  - `jia wei si` (贾维斯: tên tiếng Trung của Jarvis)
  - `guan jia` (管家: chữ Hán của "quản gia")
- Khai báo trong `CONFIG_CUSTOM_WAKE_WORD`, hoặc trong `index.json` của phân vùng assets (`multinet_model.commands`).
- Chỉnh `CONFIG_CUSTOM_WAKE_WORD_THRESHOLD`.
- Rủi ro: cách đọc "quản gia" theo giọng Bắc ("kwản za") hoặc giọng Nam ("wảng da") khác với "guǎn jiā". Phải thử bằng chính giọng người dùng.

### B2. WakeNet "Jarvis" + microWakeWord "Quản gia" (nếu B1 không đạt)
- **Train** trên server 5090:
  - Công cụ: `TaterTotterson/microWakeWord-Trainer-Nvidia-Docker` (image `:blackwell`) hoặc `OHF-Voice/micro-wake-word`.
  - Dữ liệu dương: 2.000–5.000 câu "quản gia" sinh bằng VieNeu-TTS (nhiều giọng, nhiều tốc độ) + Piper vi_VN + **50–100 lần tự ghi âm** (cả giọng Bắc và Nam), có trộn nhiễu và tiếng vọng mũ.
  - Dữ liệu âm: tiếng Việt nói thường, đặc biệt các từ gần âm ("quản lý", "gia đình", "quán gà"…), nhạc, tiếng ồn.
  - Script và cấu hình lưu ở `tools/wakeword/`.
- **Chạy trên thiết bị:**
  - Viết class `MicroWakeWord` theo interface `WakeWord`, dùng `espressif/esp-tflite-micro`. Tham khảo `0xD34D/micro_wake_word_standalone` và `micro_wake_word` của ESPHome.
  - Phần trích đặc trưng giống micro_speech: 40 đặc trưng, cửa sổ 30ms, bước 10ms. Tensor arena đặt trong PSRAM.
  - Viết `CompositeWakeWord` chạy song song WakeNet (AFE) và microWakeWord trên cùng luồng audio đã khử ồn. Phát hiện được một trong hai thì đánh thức.
  - Ghim phiên bản `esp-nn`: bản mới cần tensor arena lớn hơn.
- Theo dõi CPU và RAM: AFE + WakeNet + microWakeWord + Opus phải chạy song song mà không vấp tiếng.

### Ngắt lời bằng wake word
- Khi đang phát TTS, mic thu cả tiếng loa trong mũ. Thiết bị không có tín hiệu tham chiếu để khử vọng (INMP441 + MAX98357 chạy simplex).
- Thử `USE_SERVER_AEC` (ghi là "Unstable") kết hợp khử vọng phía server (SV5).
- Nếu không đạt: chỉ ngắt lời bằng nút PTT (FW5) và **ghi rõ giới hạn này** trong tài liệu.

## 3. Cách đo
Bộ thử: 20 lần gọi mỗi từ × 3 khoảng cách/điều kiện (yên tĩnh, có nhạc nền, đang đội mũ) + 1 giờ podcast/TV tiếng Việt để đếm số lần kích nhầm.

## 4. Công việc
- [x] Build phương án A (biến thể `jarvis-helmet`); [ ] đo trên mũ thật, lấy làm mốc
- [x] Build B1 (biến thể `jarvis-helmet-multinet`); [ ] thử bằng giọng người dùng, ghi kết quả
- [x] Dựng pipeline train (B2) và bộ nhận trên thiết bị (biến thể `jarvis-helmet-mww`); [ ] train model "Quản gia" trên 5090, đưa vào firmware
- [x] Ngắt lời bằng wake word: khử vọng phía server không dùng được với WebSocket → mặc định tắt khi đang nói (`CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n`), đã ghi giới hạn
- [x] Mẫu báo cáo so sánh (`docs/wakeword-report.md`); [ ] điền số đo, chọn phương án cuối cùng

## 5. Tiêu chí nghiệm thu
1. Mỗi từ nhận đúng ≥18/20 lần trong phòng yên tĩnh, ≥15/20 khi có nhạc nền, ở khoảng cách đội mũ.
2. Kích nhầm ≤1 lần mỗi giờ khi phát podcast/TV.
3. Độ trễ từ lúc nói xong wake word đến khi thiết bị phản hồi (âm báo/HUD) dưới 500ms.
4. Không vấp audio, RAM còn trống ≥30KB internal sau khi bật đủ các thành phần.

## Ghi chú triển khai (10/2026)

**Trạng thái:** 3 phương án đều build được (D11); cách đo, bảng kết quả và tiêu chí chọn ở
[`docs/wakeword-report.md`](../wakeword-report.md). **Chưa đo trên mũ thật, chưa train model "Quản gia".**

| Kế hoạch | Thực tế | Lý do |
|---|---|---|
| B1 khai báo 2 lệnh | Biến thể `jarvis-helmet-multinet` (MultiNet7 tiếng Trung, lệnh `jia wei si;guan jia`); vá nhỏ `scripts/build_default_assets.py` để dấu `;` sinh nhiều lệnh | Upstream chỉ nhận một lệnh |
| B2 `CompositeWakeWord` chạy song song | `MicroWakeWord` (lớp riêng theo interface `WakeWord`, mã tự viết trên thư viện Apache-2.0, không dùng mã GPL của ESPHome) được gọi từ `AfeAudioEngine::HandleWakeWordResult` trên cùng âm thanh AFE với WakeNet; đặc trưng `esphome/esp-micro-speech-features` 1.2.3, suy luận `espressif/esp-tflite-micro` 1.4.1 (tensor arena trong PSRAM), `esp-nn` ghim 1.4.1 | Ít sửa upstream hơn |
| Model `.tflite` | Model biên dịch vào firmware từ `mww_model_data.c` (sinh bằng `tools/wakeword/export/tflite_to_c.py`); bản commit là placeholder rỗng: bộ nhận tự tắt và ghi cảnh báo | Chưa có model đã train |
| Ngưỡng | `CONFIG_JARVIS_MWW_CUTOFF_PCT` (97%), `CONFIG_JARVIS_MWW_SLIDING_WINDOW` (5 khung) | Chỉnh khi đo |
| Pipeline train | `tools/wakeword/`: `gen_positives.py` (VieNeu, Piper), `gen_negatives.py` (câu gần âm), `record_personal.py`, `augment.py`, `train.sh`, `eval_wakeword.py` (hướng dẫn trong README của thư mục) | – |
| Ngắt lời bằng wake word | Mặc định tắt nhận wake word khi đang nói (`CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n`, D12); server không trả lời riêng wake word (D15) | Không có khử vọng: giọng của chính mũ có thể tự kích hoạt |

Biến thể `jarvis-helmet-mww` còn trống 26% phân vùng app (các biến thể khác 31%); 8 cảnh báo khi build đều nằm trong mã của
thư viện esp-tflite-micro (`-Wshadow`), không có trong mã board.

**Tiêu chí nghiệm thu:** tất cả chờ mũ thật và model đã train.

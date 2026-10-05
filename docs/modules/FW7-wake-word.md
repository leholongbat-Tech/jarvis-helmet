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
- [ ] Đo phương án A, lấy làm mốc
- [ ] Thử B1, ghi kết quả
- [ ] Nếu B1 không đạt: dựng pipeline train (B2), train, chuyển sang TFLite int8, tích hợp `CompositeWakeWord`
- [ ] Thử ngắt lời bằng wake word khi bật/tắt khử vọng phía server
- [ ] Viết báo cáo so sánh, chọn phương án cuối cùng

## 5. Tiêu chí nghiệm thu
1. Mỗi từ nhận đúng ≥18/20 lần trong phòng yên tĩnh, ≥15/20 khi có nhạc nền, ở khoảng cách đội mũ.
2. Kích nhầm ≤1 lần mỗi giờ khi phát podcast/TV.
3. Độ trễ từ lúc nói xong wake word đến khi thiết bị phản hồi (âm báo/HUD) dưới 500ms.
4. Không vấp audio, RAM còn trống ≥30KB internal sau khi bật đủ các thành phần.

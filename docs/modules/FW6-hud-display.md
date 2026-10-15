# FW6 – HUD Iron Man & font tiếng Việt

| | |
|---|---|
| **Mục tiêu** | Giao diện HUD kiểu Iron Man trên LCD 2.4" ST7789 dùng ngang 320×240: trạng thái trợ lý, câu hội thoại có dấu tiếng Việt, thông tin hệ thống, thẻ thông báo |
| **Phụ thuộc** | FW1 (LCD chạy được). Lấy trạng thái từ FW2, FW3, FW4 |
| **Kết quả giao nộp** | `jarvis_hud_display.{h,cc}`, font LVGL tiếng Việt, ảnh chụp từng trạng thái |

## 1. Font tiếng Việt (làm trước)

Font có sẵn `font_noto_sans_basic_16_4` chỉ có ASCII và Latin-1, **thiếu chữ có dấu tiếng Việt** (ạ, ế, ữ, đ…).

Cách làm:
- Tạo font bằng `lv_font_conv` từ **Noto Sans** (hoặc Be Vietnam Pro), 4bpp, cỡ 16px (nội dung) và 14px (chữ nhỏ). Dải ký tự:
  - `0x20-0x7E`: ASCII
  - `0xA0-0xFF`: Latin-1
  - `0x102-0x103, 0x110-0x111, 0x128-0x129, 0x168-0x169, 0x1A0-0x1A1, 0x1AF-0x1B0`: Ă Đ Ĩ Ũ Ơ Ư
  - `0x300-0x303, 0x309, 0x323`: dấu tổ hợp
  - `0x1EA0-0x1EF9`: toàn bộ chữ Việt có dấu dựng sẵn
- Biên dịch font vào firmware (khoảng 100–150KB, flash 16MB thừa chỗ).
- Đặt `BUILTIN_TEXT_FONT` của board trỏ tới font này. **Cần đọc kỹ** cách `main/CMakeLists.txt` dùng biến này: tên symbol và chỗ khai báo font.
- Phương án dự phòng: cơ chế `glyph_push` (`docs/glyph-push.md`), trong đó server gửi ký tự còn thiếu. Phức tạp hơn nên chỉ dùng khi cách trên không làm được.
- Chữ số và nhãn tiếng Anh (giờ, "SYS", "PWR") có thể dùng thêm font kiểu kỹ thuật (Orbitron, Rajdhani), chỉ lấy chữ số và chữ hoa.

## 2. Bố cục 320×240 (màn 2.4" nằm ngang)

```
┌──────────────────────────────────────────────────┐
│ 14:32   ▂▄▆ LAN    5.02V               ⚠OFFLINE  │  thanh trạng thái 22px
│                                                  │
│ [MASK]           ╭──────────╮           [EYES]   │
│ ▲ OPEN          ╱  ╭──────╮  ╲            ● ON   │  vòng arc reactor 128px
│                │  │  HÀU   │  │                  │  (3 vòng lv_arc xoay
│                │  │        │  │                  │   + vòng sóng âm)
│                 ╲  ╰──────╯  ╱                   │
│                   ╰──────────╯                   │
│ ──────────────────────────────────────────────── │
│ » mở mặt nạ rồi bật đèn mắt                      │  câu bạn nói (màu nhạt)
│ Đã mở mặt nạ và bật đèn mắt, thưa ngài. Hệ       │  câu Jarvis trả lời (màu sáng,
│ thống sẵn sàng.                                  │  tự cuộn khi dài)
└──────────────────────────────────────────────────┘
```

Chiều dọc giống bố cục 240×240 ban đầu; chiều ngang dãn theo bề rộng màn (vòng reactor ở giữa, panel EYES và nhãn OFFLINE bám mép phải). Toạ độ pixel thật: [`docs/hud.md`](../hud.md) mục 1.

**Màu:** nền `#00070D`, chính `#00E5FF` (cyan), phụ `#FFB000` (cam), lỗi `#FF3B30`.

**Vòng arc reactor theo trạng thái** (`SetStatus` / trạng thái thiết bị):

| Trạng thái | Hiệu ứng |
|---|---|
| Chờ | Cyan mờ, sáng lên tối xuống chậm (chu kỳ 3 giây) |
| Đang nghe | Cyan sáng, vòng ngoài to nhỏ theo mức âm mic. Nếu không lấy được mức âm thì dùng nhịp cố định |
| Đang nghĩ | Cam, các vòng xoay ngược chiều nhau |
| Đang nói | Cyan, các vạch sóng quanh vòng nhảy theo âm lượng phát |
| Lỗi / offline | Đỏ, nháy chậm |

**Thẻ thông tin** (`self.display.show_info`): thẻ trượt lên đè vùng chữ, có tiêu đề màu cam và nội dung; tự ẩn sau `seconds` giây.

**Cảm xúc từ LLM** (`SetEmotion`): đổi màu viền vòng hoặc hiện biểu tượng nhỏ ở giữa, không dùng bộ emoji mặc định.

## 3. Thiết kế mã
```cpp
class JarvisHudDisplay : public SpiLcdDisplay {
 public:
  using SpiLcdDisplay::SpiLcdDisplay;
  void SetupUI() override;                               // dựng toàn bộ giao diện HUD
  void SetStatus(const char* status) override;
  void SetEmotion(const char* emotion) override;
  void SetChatMessage(const char* role, const char* content) override;
  void UpdateStatusBar(bool update_all = false) override;
  // Riêng của HUD:
  void ShowInfo(const std::string& title, const std::string& text, int seconds);
  void SetMaskState(MaskState s);
  void SetEyesState(bool on, int brightness);
  void SetVbus(int millivolts);
};
```
- Mọi thao tác LVGL đều nằm trong `DisplayLockGuard`, theo cách upstream làm trong `lcd_display.cc`.
- Hiệu năng: SPI 40MHz, buffer một phần, chỉ vẽ lại vùng thay đổi. Mục tiêu animation ≥20 FPS; animation **không được làm vấp audio** (kiểm tra mức CPU).

## 4. Công việc
- [x] Tạo và tích hợp font tiếng Việt; câu có đủ các loại dấu được `tools/fonts/check_glyphs.py` kiểm tra tự động
- [x] Dựng bố cục, animation theo trạng thái
- [x] Thẻ thông tin, biểu tượng mặt nạ/mắt, điện áp, nhãn OFFLINE
- [ ] Đo FPS và mức CPU trên màn thật, chụp ảnh từng trạng thái

## 5. Tiêu chí nghiệm thu
1. Câu "Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài!" hiện đủ dấu, không có ô vuông thay ký tự.
2. Mỗi trạng thái (chờ, nghe, nghĩ, nói, lỗi) có hình ảnh khác nhau rõ ràng; chuyển trạng thái dưới 100ms.
3. Animation ≥20 FPS. Trong lúc animation chạy, audio không vấp và nhận dạng giọng nói vẫn bình thường.
4. Câu trả lời dài hơn vùng hiển thị thì tự cuộn, không bị cắt mất.
5. Thanh trạng thái cập nhật giờ, WiFi, điện áp ít nhất mỗi 5 giây.

## Ghi chú triển khai (10/2026)

**Trạng thái:** code xong, build OK; tài liệu chi tiết (bố cục đúng tọa độ pixel, màu, trạng thái, luồng dữ liệu, hiệu năng,
cách chụp màn hình, đối chiếu tiêu chí): [`docs/hud.md`](../hud.md). **Chưa chạy trên màn thật.**

| Kế hoạch | Thực tế | Lý do |
|---|---|---|
| Đổi `BUILTIN_TEXT_FONT` sang font tiếng Việt | Giữ `BUILTIN_TEXT_FONT font_noto_sans_basic_16_4` trong CMake (upstream kiểm tra bằng regex); font tiếng Việt là file `.c` riêng của board: `font_jarvis_vi_16_4`, `font_jarvis_vi_14_4` (Noto Sans Medium) và `font_jarvis_hud_16_4` (Rajdhani, chữ số/nhãn), sinh bằng `lv_font_conv` 1.5.3 `--no-compress` qua `tools/fonts/generate_fonts.py` | D10. Tổng ~49 KB flash (24,0 + 20,3 + 4,5 KB), nhỏ hơn dự tính 100–150 KB |
| Dải ký tự như mục 1 | Thêm `0x2010-0x2027` (gạch nối, ngoặc kép, dấu ba chấm) và `0x20AB` (₫) | Câu trả lời của LLM hay có các ký tự này |
| `class JarvisHudDisplay : public SpiLcdDisplay` ghi đè vài hàm | HUD vẽ **đè** lên giao diện gốc: gọi `SpiLcdDisplay::SetupUI()` trước, ẩn widget gốc, root HUD đục nằm trên cùng; ghi đè thêm `SetTheme`, `SetTextFont`, `ShowNotification`, `ClearChatMessages`, `SetPreviewImage`, `SetHideSubtitle` | Nhiều hàm upstream dùng widget gốc mà không kiểm tra null |
| Mức âm mic/loa | Lấy từ `MeteredAudioCodec` (lớp bọc codec, đo RMS mỗi khối PCM) | Upstream không có callback mức âm |
| Trạng thái "Đang nghĩ" | Suy ra: sau câu STT của người dùng cho tới khi chuyển sang nói | Upstream không có trạng thái "thinking" |
| – | `CONFIG_JARVIS_HUD=n` dùng lại giao diện gốc để gỡ lỗi | – |

**Tiêu chí nghiệm thu:** 1 đạt phần tự động (đủ glyph cho câu nghiệm thu và toàn bộ bảng chữ cái); 2–5 chờ màn thật.

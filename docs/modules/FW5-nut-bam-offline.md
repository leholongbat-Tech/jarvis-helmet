# FW5 – Nút bấm & chế độ offline

| | |
|---|---|
| **Mục tiêu** | Nút PTT dùng để nói và ngắt lời. Nút mặt nạ mở/đóng mặt nạ ngay trên thiết bị, kể cả khi mất mạng hoặc server lỗi |
| **Phụ thuộc** | FW4 (`run_saved`), FW2, FW3 |
| **Kết quả giao nộp** | Hàm `InitializeButtons()` trong `jarvis_helmet_board.cc`, bảng thao tác nút trong README của board |

## 1. Bảng thao tác

Nút dùng class `Button` của xiaozhi (`main/boards/common/button.h`), có các sự kiện `OnClick`, `OnDoubleClick`, `OnLongPress`, `OnPressDown`, `OnPressUp`.

| Nút | Thao tác | Hành động | Cần mạng? |
|---|---|---|---|
| **PTT** (GPIO10) | Bấm 1 lần | `Application::ToggleChatState()`: bắt đầu hội thoại; nếu Jarvis đang nói thì **ngắt lời** | Có |
| | Nhấn giữ | `OnPressDown` → `StartListening()`, `OnPressUp` → `StopListening()` (giữ để nói, như bộ đàm) | Có |
| **Mặt nạ** (GPIO11) | Bấm 1 lần | Mặt nạ đang đóng (hoặc đang chạy về vị trí đóng) → `run_saved("open_mask")`; ngược lại → `run_saved("close_mask")` | **Không** |
| | Nhấn giữ | Bật/tắt đèn mắt (`power_up` / `fade_out`) | **Không** |
| | Bấm đúp | Dừng mọi chuỗi đang chạy (`self.sequence.stop`), dừng khẩn cấp | **Không** |
| **BOOT** (GPIO0) | Bấm lúc đang khởi động | Vào chế độ cấu hình WiFi (giữ nguyên như upstream) | – |
| | Bấm khi đang chạy | Giống bấm 1 lần nút PTT | Có |

Lưu ý:
- Bấm 1 lần và nhấn giữ trên nút PTT có thể xung đột: cả hai đều sinh sự kiện press down/up. Cách xử lý: nếu thả tay trước **400ms** thì coi là bấm 1 lần, giữ lâu hơn thì coi là PTT. Thử nghiệm thực tế để chọn ngưỡng.
- Khi bấm nút mặt nạ lúc trợ lý đang nói, mặt nạ vẫn chạy và **không ngắt lời**.

## 2. Hành vi khi mất mạng
- Khi mất mạng: nút mặt nạ, đèn mắt và các chuỗi đã lưu vẫn chạy được. HUD hiện biểu tượng "OFFLINE". Wake word vẫn nhận nhưng thiết bị báo bằng giọng hoặc thông báo trên màn: "mất kết nối với server".
- Đồng bộ chuỗi: khi kết nối lại, plugin SV6 có thể gửi `self.sequence.save` để cập nhật `open_mask` / `close_mask` theo `tasks.yaml` (tùy chọn `sync_to_device: true` trong YAML). Nhờ vậy nút bấm offline chạy đúng bản tác vụ mới nhất.

## 3. Công việc
- [x] Thêm 2 đối tượng `Button` (GPIO10, GPIO11) vào board
- [x] Viết logic phân biệt bấm 1 lần / nhấn giữ cho PTT (ngưỡng `CONFIG_JARVIS_PTT_HOLD_MS`, mặc định 400 ms); [ ] chỉnh ngưỡng trên mũ thật
- [x] Logic nút mặt nạ dựa vào `MaskController::GetState()`
- [x] Hiện OFFLINE trên HUD và thông báo khi mất server
- [x] Đồng bộ chuỗi từ `tasks.yaml` xuống thiết bị (`sync_to_device`, SV6)

## 4. Tiêu chí nghiệm thu
1. Rút điện server (hoặc tắt WiFi): nút mặt nạ vẫn mở/đóng được, nhấn giữ vẫn bật/tắt đèn mắt.
2. Bấm PTT trong lúc Jarvis đang nói: tiếng dừng trong vòng 300ms và thiết bị chuyển sang nghe.
3. Nhấn giữ PTT, nói, rồi thả: server nhận đúng đoạn đã nói.
4. Bấm đúp nút mặt nạ khi mặt nạ đang chạy: dừng ngay tại chỗ.
5. Không có lần nào nhận nhầm bấm 1 lần thành nhấn giữ hoặc ngược lại trong 20 lần thử mỗi loại.

## Ghi chú triển khai (10/2026)

**Trạng thái:** code xong, build OK. **Chưa thử trên mũ thật.** Bảng thao tác chính thức (người dùng đọc):
`firmware/main/boards/jarvis-helmet/README.md` mục 6 và 9.

- **PTT (D8):** bấm nhanh khi Jarvis đang nói → gửi abort và **xả ngay âm thanh đang phát** rồi nghe tiếp; trạng thái khác →
  `ToggleChatState`. Giữ ≥400 ms (`CONFIG_JARVIS_PTT_HOLD_MS`, 200–1500) → nghe, thả → gửi câu; thả khi đang kết nối thì tự
  dừng nghe ngay khi kết nối xong. "Bấm 1 lần" được nhận ~80 ms sau khi thả (PTT) và không bao giờ sinh thêm khi đã nhấn giữ.
- **Nút mặt nạ (D9):** chạy thẳng trên controller/runner, **không** qua `Application::Schedule` (vòng lặp chính của upstream bị
  chặn tới ~10 s khi đang kết nối server). Nhấn giữ ≥800 ms bật/tắt đèn mắt; bấm đúp dừng khẩn cấp ngay khi nhấn lần hai.
- **OFFLINE:** bật theo trạng thái Wi-Fi, lần kết nối server thất bại, OTA không phản hồi sau 20 s lúc khởi động; tắt khi mở
  được kênh hội thoại.
- **Giới hạn của upstream (ghi nhận, không sửa sâu):** WebSocket chỉ mở khi bắt đầu hội thoại nên lúc chờ không biết server đã
  tắt; wake word chỉ bật ở trạng thái Idle và model chỉ nạp sau lần kết nối Wi-Fi đầu tiên kể từ khi bật nguồn; không có mạng
  nào trong tầm thì sau 60 s thiết bị vào chế độ cấu hình Wi-Fi.

**Tiêu chí nghiệm thu:** tất cả chờ mũ thật.

# Giao tiếp giữa các module

File này mô tả cách firmware (FW4) và server (SV6) giao tiếp với nhau. **Muốn đổi bất kỳ mục nào ở đây thì phải sửa cả hai phía cùng lúc.**

## 1. Kênh truyền

- Firmware và server nói chuyện qua giao thức WebSocket của xiaozhi (`docs/websocket.md` trong repo firmware). Audio dùng Opus: chiều lên 16 kHz, chiều xuống 24 kHz, khung 60 ms.
- Server điều khiển thiết bị bằng **MCP (JSON-RPC 2.0)** trong message `{"type":"mcp", ...}`. Firmware đăng ký tool qua `McpServer::GetInstance().AddTool(...)`. Server lấy danh sách tool bằng `tools/list` và gọi tool bằng `tools/call`.
- Tham số tool MCP của xiaozhi chỉ có 3 kiểu: `boolean`, `integer`, `string`. Vì vậy dữ liệu có cấu trúc (chuỗi bước) được **gửi dưới dạng chuỗi JSON** trong tham số kiểu `string`.

## 2. Danh sách MCP tool trên thiết bị

| Tool | Tham số | Trả về | Ghi chú |
|---|---|---|---|
| `self.mask.open` | `speed` int 10–100 (mặc định 70) | `"opening"` | Chạy nền, trả về ngay (xem lưu ý dưới bảng) |
| `self.mask.close` | `speed` int 10–100 | `"closing"` | Chạy nền |
| `self.mask.set_position` | `percent` int 0–100, `speed` int | `"moving"` | 0 = đóng, 100 = mở. Chạy nền |
| `self.mask.get_state` | – | JSON `{"state":"open\|closed\|moving","position":0-100}` | |
| `self.eyes.set` | `on` bool, `brightness` int 0–100 (mặc định 100) | `"ok"` | |
| `self.eyes.effect` | `name` string ∈ {`breathe`,`blink`,`flicker`,`power_up`,`fade_out`}, `duration_ms` int 100–10000 | `"ok"` | `breathe` lặp đến khi có lệnh khác |
| `self.output.set` | `on` bool | `"ok"` | Kênh dự phòng (GPIO9) |
| `self.sequence.run` | `steps` string (mảng JSON, mục 3) | `"started"` hoặc lỗi kiểm tra | **Chạy nền**: trả về ngay sau khi kiểm tra hợp lệ. Lệnh mới hủy chuỗi đang chạy |
| `self.sequence.run_saved` | `name` string | `"started"` | Chạy một chuỗi đã lưu trên thiết bị |
| `self.sequence.save` | `name` string `[a-z0-9_]{1,11}`, `steps` string | `"saved"` | Lưu vào NVS để dùng khi offline (ví dụ cho nút bấm) |
| `self.sequence.stop` | – | `"stopped"` | |
| `self.display.show_info` | `title` string ≤24 ký tự, `text` string ≤200 ký tự, `seconds` int 1–30 | `"ok"` | Hiện thẻ thông tin trên HUD |
| `self.system.get_status` | – | JSON `{"mask":…, "eyes":…, "output":…, "vbus_mv":…, "rssi":…, "uptime_s":…}` | |
| `self.servo.calibrate` *(user-only)* | `servo` int 0–1, `angle` int 0–180 | `"ok"` | Chỉnh góc thô khi hiệu chỉnh. Đăng ký bằng `AddUserOnlyTool` để LLM không tự gọi |
| `self.servo.save_calibration` *(user-only)* | `servo` int, `closed_angle` int, `open_angle` int | `"saved"` | Lưu vào NVS |

Mỗi tool phải có mô tả song ngữ Việt–Anh ngắn gọn, để LLM chọn đúng tool.

> **Tool không được chặn luồng.** xiaozhi-esp32 chạy callback của tool trên luồng chính (`Application::Schedule` trong `main/mcp_server.cc`). Nếu một tool chạy lâu, toàn bộ vòng xử lý chính (gồm cả audio) bị kẹt theo. Vì vậy mọi tool điều khiển chỉ được kiểm tra tham số rồi đẩy lệnh vào queue, phải trả về trong vòng ~10ms. Chuyển động thật diễn ra trong task riêng (FW2, FW3, FW4).

## 3. Định dạng chuỗi bước (sequence steps)

Chuỗi bước là một mảng JSON. **Mỗi phần tử là một object có đúng một khóa**, khóa đó là loại bước:

```json
[
  {"eyes":    {"effect": "fade_out", "duration_ms": 300}},
  {"mask":    {"action": "open", "speed": 80}},
  {"delay":   300},
  {"eyes":    {"on": true, "brightness": 100}},
  {"output":  {"on": true}},
  {"servo":   {"id": 0, "angle": 45, "duration_ms": 500}},
  {"display": {"title": "COMBAT MODE", "text": "Hệ thống vũ khí sẵn sàng", "seconds": 3}},
  {"sound":   {"name": "power_up"}}
]
```

| Bước | Trường | Ràng buộc |
|---|---|---|
| `mask` | `action` ∈ {`open`,`close`} **hoặc** `position` 0–100; `speed` 10–100 | Đợi chuyển động xong rồi mới chạy bước tiếp |
| `servo` | `id` 0–1, `angle` 0–180, `duration_ms` 0–5000 | Bị giới hạn trong khoảng góc đã hiệu chỉnh |
| `eyes` | `on` bool + `brightness` 0–100, **hoặc** `effect` + `duration_ms` | |
| `output` | `on` bool | |
| `delay` | số nguyên ms, 0–10000 | |
| `display` | `title`, `text`, `seconds` | Giới hạn giống `self.display.show_info` |
| `sound` | `name`: âm thanh có sẵn trong assets | Tùy chọn, làm ở giai đoạn sau |

Quy tắc kiểm tra (thiết bị kiểm tra **toàn bộ chuỗi trước khi chạy**; sai một bước thì từ chối cả chuỗi và báo lỗi rõ ràng):
- Tối đa 64 bước, tổng thời gian ước tính tối đa 60 giây.
- Khóa lạ hoặc giá trị ngoài khoảng → lỗi `invalid step #i: <lý do>`.

## 4. Dữ liệu lưu trên thiết bị (NVS)

Namespace `helmet`. Lưu ý tên khóa NVS dài tối đa 15 ký tự.

| Khóa | Kiểu | Ý nghĩa |
|---|---|---|
| `s0_closed`, `s0_open`, `s1_closed`, `s1_open` | u8 | Góc hiệu chỉnh của servo 0 (trái) và 1 (phải) |
| `mask_speed` | u8 | Tốc độ mặc định |
| `eyes_bright` | u8 | Độ sáng mặc định |
| `seq_<name>` | blob (JSON) | Chuỗi đã lưu; `<name>` dài tối đa 11 ký tự |

Chuỗi `open_mask` và `close_mask` có sẵn trong firmware, chỉ dùng khi NVS chưa có bản lưu.

## 5. File `server/tasks/tasks.yaml`

```yaml
version: 1
tasks:
  open_mask:                                   # id: [a-z0-9_]+
    description: "Mở mặt nạ / open the mask / lift the faceplate"   # mô tả cho LLM, nên song ngữ
    quick_phrases: ["mở mặt nạ", "open mask", "open the mask"]     # tùy chọn: khớp nhanh, không qua LLM
    reply: "Đã mở mặt nạ, thưa ngài."          # tùy chọn; hoặc {vi: "...", en: "..."}
    steps:                                     # cùng định dạng mục 3
      - eyes: {effect: fade_out, duration_ms: 300}
      - mask: {action: open, speed: 80}
  close_mask:
    description: "Đóng mặt nạ / close the mask"
    quick_phrases: ["đóng mặt nạ", "close mask", "close the mask"]
    reply: "Đã đóng mặt nạ."
    steps:
      - mask: {action: close}
      - delay: 300
      - eyes: {effect: power_up, duration_ms: 1200}
```

Cách server dùng file này:
- Mỗi tác vụ được đăng ký thành một function cho LLM, tên là `task_<id>`.
- Khi function được gọi, plugin gửi `self.sequence.run` với `steps` (đã chuyển sang chuỗi JSON).
- **Khớp nhanh:** chuẩn hóa câu nhận dạng được (chữ thường, bỏ dấu câu, bỏ wake word ở đầu câu) rồi so với `quick_phrases`. Nếu khớp thì chạy ngay, phát `reply` và bỏ qua LLM.
- Plugin tự nạp lại file khi file thay đổi. Kiểm tra định dạng trước khi nạp; nếu file lỗi thì giữ bản cũ và ghi log.

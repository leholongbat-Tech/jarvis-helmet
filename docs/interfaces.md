# Giao tiếp giữa các module

File này mô tả cách firmware (FW4) và server (SV6) giao tiếp với nhau. **Muốn đổi bất kỳ mục nào ở đây thì phải sửa cả hai phía cùng lúc** (và bộ fixture dùng chung `tools/tests/fixtures/sequences/`).

Các quyết định đánh số Dxx là của hợp đồng triển khai; mục 7 tóm tắt những quyết định ảnh hưởng tới giao tiếp.

## 1. Kênh truyền

- Firmware và server nói chuyện qua giao thức WebSocket của xiaozhi (`docs/websocket.md` trong repo firmware). Audio dùng Opus khung 60 ms: chiều lên 16 kHz, chiều xuống 24 kHz. Server báo `audio_params.sample_rate: 24000` trong `hello` (bản vá D3), firmware giải mã theo con số đó.
- Server điều khiển thiết bị bằng **MCP (JSON-RPC 2.0)** trong message `{"type":"mcp", ...}`. Firmware đăng ký tool qua `McpServer::GetInstance().AddTool(...)`. Server lấy danh sách tool bằng `tools/list` (trang tối đa 8000 byte, có `nextCursor`) và gọi tool bằng `tools/call`.
- Tham số tool MCP của xiaozhi chỉ có 3 kiểu: `boolean`, `integer`, `string`. Vì vậy dữ liệu có cấu trúc (chuỗi bước) được **gửi dưới dạng chuỗi JSON** trong tham số kiểu `string`.
- Khi một câu trả lời của LLM gọi nhiều tool, server v0.9.7 chạy chúng **song song**. Vì thế các lệnh `task_*` của cùng một lượt được gộp thành **một** `self.sequence.run` (mục 5, D4).
- Truy cập từ xa đi qua Cloudflare Tunnel + Access. Firmware gửi header `CF-Access-Client-Id` / `CF-Access-Client-Secret` **chỉ** trên kết nối `https`/`wss` tới đúng host đã đăng ký (D13, D31); giá trị nằm trong NVS `jsecrets` (mục 4).

## 2. Danh sách MCP tool trên thiết bị

| Tool | Tham số | Trả về | Ghi chú |
|---|---|---|---|
| `self.mask.open` | `speed` int 10–100 (mặc định 70) | `"opening"` | Chạy nền, trả về ngay (xem lưu ý dưới bảng) |
| `self.mask.close` | `speed` int 10–100 (mặc định 70) | `"closing"` | Chạy nền |
| `self.mask.set_position` | `percent` int 0–100, `speed` int 10–100 (mặc định 70) | `"moving"` | 0 = đóng, 100 = mở. Chạy nền |
| `self.mask.get_state` | – | JSON `{"state":"open\|closed\|moving","position":0-100}` | |
| `self.eyes.set` | `on` bool, `brightness` int 0–100 (mặc định 100) | `"ok"` | |
| `self.eyes.effect` | `name` string ∈ {`breathe`,`blink`,`flicker`,`power_up`,`fade_out`}, `duration_ms` int **0** hoặc 100–10000 (mặc định 0) | `"ok"` | `duration_ms` 0 = thời lượng mặc định của hiệu ứng: `breathe` 2500 (một chu kỳ, lặp đến khi có lệnh khác), `flicker` 600, còn lại 1000; `blink` không dài quá 600 ms (D26). 1–99 bị từ chối |
| `self.output.set` | `on` bool | `"ok"` | Kênh dự phòng (GPIO9) |
| `self.sequence.run` | `steps` string ≤ 4000 byte (mảng JSON, mục 3) | `"started"` hoặc lỗi kiểm tra | **Chạy nền**: trả về ngay sau khi kiểm tra hợp lệ. Lệnh mới hủy chuỗi đang chạy |
| `self.sequence.run_saved` | `name` string | `"started"` | Chạy một chuỗi đã lưu trên thiết bị; `open_mask`/`close_mask` có sẵn trong firmware |
| `self.sequence.save` | `name` string `[a-z0-9_]{1,11}`, `steps` string ≤ 4000 byte | `"saved"` | Lưu vào NVS để dùng khi offline (nút bấm). Server tự lưu các tác vụ `sync_to_device` (mục 5) |
| `self.sequence.stop` | – | `"stopped"` | |
| `self.display.show_info` | `title` string 0–24 ký tự (được rỗng, D30), `text` string 0–200 ký tự, `seconds` int 1–30 (mặc định 5) | `"ok"` | Hiện thẻ thông tin trên HUD. Giới hạn đếm theo ký tự Unicode; ở tầng MCP `maxLength` là 96/800 byte UTF-8 |
| `self.system.get_status` | – | JSON `{"mask":{"state","position"}, "eyes":{"on","brightness","effect"}, "output":{"on"}, "sequence":{"running","name"}, "vbus_mv", "rssi" (null khi mất Wi-Fi), "uptime_s", "free_heap"}` | |
| `self.servo.calibrate` *(user-only)* | `servo` int 0–1, `angle` int 0–180 | `"ok"` | Chỉnh góc thô khi hiệu chỉnh. Đăng ký bằng `AddUserOnlyTool` để LLM không tự gọi |
| `self.servo.save_calibration` *(user-only)* | `servo` int 0–1, `closed_angle` int 0–180, `open_angle` int 0–180 (phải khác nhau) | `"saved"` | Lưu vào NVS |

- Mỗi tool có mô tả song ngữ Việt–Anh ngắn gọn để LLM chọn đúng tool. Bản chụp `tools/list` mà server nhận được nằm ở `tools/tests/llm/device_tools.json`; test so file đó với mã firmware (tên, mô tả, kiểu, default, min/max, maxLength).
- Server v0.9.7 **không bao giờ lấy tool user-only**, nên việc hiệu chỉnh servo đi qua WebSocket MCP cục bộ của mũ (mục 6).
- Lỗi của tool là một câu tiếng Anh ngắn, đã qua `helmet::SanitizeMessage` (không có `"`, `\`, ký tự điều khiển), vì `McpServer::ReplyError` dán chuỗi vào JSON mà không escape. Ví dụ: `unknown effect disco, use breathe, blink, flicker, power_up or fade_out`, `unknown sequence party`, `title too long (max 24 chars)`.

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
| `mask` | `action` ∈ {`open`,`close`} **hoặc** `position` 0–100; `speed` 10–100 (mặc định 70) | Đợi chuyển động xong rồi mới chạy bước tiếp |
| `servo` | `id` 0–1, `angle` 0–180 (bắt buộc), `duration_ms` 0–5000 (mặc định 0) | Bị giới hạn trong khoảng góc đã hiệu chỉnh |
| `eyes` | `on` bool + `brightness` 0–100 (mặc định 100), **hoặc** `effect` + `duration_ms` (0 hoặc 100–10000; thiếu/0 = mặc định của hiệu ứng như `self.eyes.effect`) | Không trộn hai dạng |
| `output` | `on` bool | |
| `delay` | số nguyên ms, 0–10000 | |
| `display` | `title` 0–24 ký tự, `text` 0–200 ký tự (bắt buộc), `seconds` 1–30 (mặc định 5) | Giống `self.display.show_info` |
| `sound` | `name` khớp `[a-z0-9_]{1,32}` | Âm thanh có sẵn trong assets; tùy chọn, làm ở giai đoạn sau |

Quy tắc kiểm tra (thiết bị kiểm tra **toàn bộ chuỗi trước khi chạy**; sai một bước thì từ chối cả chuỗi và báo lỗi rõ ràng):
- JSON gọn của `steps` ≤ 4000 byte; tối đa 64 bước; tổng thời gian ước tính ≤ 60000 ms.
- Thời gian ước tính của từng bước: `mask` = cả hành trình ở tốc độ của bước, `800 × 100 / speed` ms (chia nguyên; cả với `position`, trường hợp xấu nhất); `servo` = `duration_ms`; `eyes` hiệu ứng = `duration_ms` đã thay mặc định, riêng `breathe` tính 0 (lặp đến lệnh sau); `delay` = giá trị; các bước khác 0.
- Tên khóa phân biệt hoa thường; khóa lạ, khóa trùng, thiếu trường bắt buộc, sai kiểu hoặc ngoài khoảng → lỗi. Số nguyên là số JSON không có phần lẻ (`80.0` được, `true` không phải số).
- Lỗi của một bước: `invalid step #n: <lý do>` với **n đếm từ 1** (D23); lý do nêu tên trường gây lỗi. Lỗi cấp trên chứa `array` (không phải mảng), `64` (quá nhiều bước) hoặc `60000` (quá dài).
- Server kiểm tra `tasks.yaml` bằng cùng các luật (`server/tasks/taskslib.py`); bộ fixture `tools/tests/fixtures/sequences/` quy định kết quả mong đợi cho cả hai phía.

## 4. Dữ liệu lưu trên thiết bị (NVS)

Partition `helmet` (nvs, offset 0x10000, 0xC000), namespace `helmet` (D6). Lưu ý tên khóa NVS dài tối đa 15 ký tự.

| Khóa | Kiểu | Ý nghĩa |
|---|---|---|
| `s0_closed`, `s0_open`, `s1_closed`, `s1_open` | u8 | Góc hiệu chỉnh của servo 0 (trái) và 1 (phải) |
| `s0_pos`, `s1_pos` | u8 | Góc nghỉ cuối cùng của từng servo (0–180) |
| `mask_pos` | u8 | Vị trí mặt nạ cuối cùng (0–100), để khởi động không giật |
| `mask_speed` | u8 | Tốc độ mặc định |
| `eyes_bright` | u8 | Độ sáng mặc định |
| `seq_<name>` | blob (JSON) | Chuỗi đã lưu; `<name>` dài tối đa 11 ký tự |

Chuỗi `open_mask` và `close_mask` có sẵn trong firmware, chỉ dùng khi NVS chưa có bản lưu.

Partition `jsecrets` (nvs, offset 0x1C000, 0x4000), namespace `secrets`, chỉ đọc trong firmware, ghi bằng `tools/provision_secrets.py` (D13, D27):

| Khóa | Ý nghĩa |
|---|---|
| `cf_id`, `cf_secret` | Service token Cloudflare Access (thiếu thì dùng Kconfig `JARVIS_CF_ACCESS_ID/SECRET`) |
| `mcp_token` | Token của WebSocket MCP cục bộ (mục 6), 1–64 ký tự `A-Z a-z 0-9 - . _ ~` |

## 5. File `server/tasks/tasks.yaml`

```yaml
version: 1
tasks:
  open_mask:                                   # id: [a-z0-9_]+
    description: "Mở mặt nạ / open the mask / lift the faceplate"   # mô tả cho LLM, nên song ngữ
    quick_phrases: ["mở mặt nạ", "open mask", "open the mask"]     # tùy chọn: khớp nhanh, không qua LLM
    reply: "Đã mở mặt nạ, thưa ngài."          # tùy chọn; hoặc {vi: "...", en: "..."}
    sync_to_device: true                       # tùy chọn: lưu chuỗi vào NVS của mũ khi mũ kết nối
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

- Khóa của một tác vụ: `description` và `steps` bắt buộc; `quick_phrases`, `reply`, `sync_to_device` tùy chọn. Id khớp **toàn bộ** `[a-z0-9_]+` (kiểm tra bằng `fullmatch`, xuống dòng ở cuối cũng bị từ chối – D29), dài tối đa 59 ký tự để `task_<id>` không quá 64 ký tự; tác vụ `sync_to_device` cần id ≤ 11 ký tự (tên NVS).
- Mỗi tác vụ được đăng ký thành một function cho LLM, tên là `task_<id>`. Khi function được gọi, plugin gửi `self.sequence.run` với `steps` (đã chuyển sang chuỗi JSON).
- **Nhiều tác vụ trong một lượt (D4):** các lệnh `task_*` của cùng một câu trả lời (cùng `sentence_id`, trong cửa sổ ~80 ms) được ghép theo thứ tự LLM gọi thành **một** `self.sequence.run`. Tác vụ nào làm chuỗi ghép vượt giới hạn mục 3 thì bị từ chối riêng; mỗi lệnh vẫn nhận kết quả của chính nó.
- **Khớp nhanh:** chuẩn hóa câu nhận dạng được (chữ thường, bỏ dấu câu, bỏ wake word "Jarvis"/"Quản gia"/"Giắc vít" ở đầu câu) rồi so **cả câu** với `quick_phrases` (ngưỡng giống 90/100, so cả bản có dấu và bản bỏ dấu). Nếu khớp thì chạy ngay, phát `reply` và bỏ qua LLM. Câu có nhiều lệnh ("... rồi ...") luôn đi qua LLM.
- **Đồng bộ xuống mũ:** khi mũ kết nối và trả xong `tools/list`, server gọi `self.sequence.save(<id>, <steps>)` cho mọi tác vụ `sync_to_device: true` (bỏ qua bản đã lưu không đổi), để nút bấm chạy được cả khi mất mạng.
- Plugin tự nạp lại file khi file thay đổi (kiểm tra ở mỗi câu nói). Kiểm tra định dạng trước khi nạp; nếu file lỗi thì giữ bản cũ và ghi log kèm số dòng.

## 6. WebSocket MCP cục bộ trên mũ (D7, D27)

- `ws://<ip-mũ>:8080/ws` (Kconfig `CONFIG_JARVIS_LOCAL_MCP_WS`, mặc định bật; cổng `CONFIG_JARVIS_LOCAL_MCP_WS_PORT`). Message là JSON-RPC MCP như mục 2, đưa thẳng vào `McpServer::ParseMessage`, nên gọi được cả tool user-only. Dùng cho `tools/servo_calibrate.py` và thử FW4 bằng tay.
- `tools/call` chỉ được gọi các tool phần cứng và trạng thái của mũ: `self.mask.*`, `self.servo.*`, `self.eyes.*`, `self.output.*`, `self.sequence.*`, `self.display.show_info`, `self.system.get_status`, `self.get_device_status`. Mọi tool khác (`self.reboot`, `self.upgrade_firmware`, `self.assets.set_download_url`, `self.screen.snapshot`, …) bị từ chối. Message lồng quá 16 cấp `[`/`{` bị từ chối trước khi parse.
- Nếu NVS có `secrets/mcp_token`, handshake phải kèm `?token=<giá trị>` (so sánh thời gian hằng), sai hoặc thiếu → HTTP 401. Không có token thì **ai cùng mạng Wi-Fi cũng điều khiển được mặt nạ và đổi hiệu chỉnh servo** (rủi ro đã ghi nhận): nên cấp token bằng `tools/provision_secrets.py --mcp-token` và chạy `tools/servo_calibrate.py --token`.

## 7. Quyết định ảnh hưởng tới giao tiếp

| | Nội dung |
|---|---|
| D3 | `hello` của server báo tần số mẫu thật 24 kHz |
| D4 | Các `task_*` của một lượt được gộp thành một `self.sequence.run` |
| D6 | Partition NVS riêng `helmet` và `jsecrets`, khóa `mask_pos` |
| D7 | Hiệu chỉnh servo qua WebSocket MCP cục bộ (tool user-only không đi qua server) |
| D13, D31 | Header Cloudflare Access chỉ gửi qua TLS tới đúng host |
| D15 | Wake word/exit word so khớp sau khi bỏ khoảng trắng, liệt kê đủ biến thể; `enable_greeting: false`, server không trả lời riêng wake word |
| D23 | Lỗi `invalid step #n`, n đếm từ 1 |
| D26 | `duration_ms` 0 = thời lượng mặc định theo hiệu ứng |
| D27 | Token tùy chọn cho WebSocket MCP cục bộ |
| D29 | Id tác vụ và tên âm thanh kiểm tra bằng `fullmatch` |
| D30 | Tiêu đề thẻ HUD được rỗng |

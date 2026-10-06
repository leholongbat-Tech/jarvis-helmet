# SV6 – Plugin tác vụ `tasks.yaml`

| | |
|---|---|
| **Mục tiêu** | Khai báo tác vụ tùy biến trong YAML. LLM hiểu yêu cầu bằng ngôn ngữ tự nhiên, gọi đúng tác vụ, rồi gửi chuỗi bước xuống mũ. Có khớp nhanh không qua LLM. Thêm tác vụ không cần nạp lại firmware |
| **Phụ thuộc** | SV5, FW4 (`self.sequence.*`) |
| **Kết quả giao nộp** | `server/xiaozhi/plugins/helmet_tasks.py`, `server/tasks/taskslib.py` (logic thuần, dùng chung với CLI và test), bản vá hook (`server/xiaozhi/patches/`), `server/tasks/tasks.yaml`, `server/tasks/schema.json`, `tools/tasks_cli.py`, fixture chuỗi bước dùng chung `tools/tests/fixtures/sequences/` |
| **Trạng thái** | Code xong, đã chạy trong stack trên máy dev với bộ giả lập mũ: khớp nhanh, gọi tác vụ qua LLM, gộp 2 tác vụ một lượt, đồng bộ xuống mũ khi kết nối. Chưa thử với mũ thật |

## 1. Thiết kế

**Nạp và kiểm tra:**
- Đọc `tasks.yaml` (mount vào container), kiểm tra bằng `schema.json`: cùng ràng buộc với interfaces.md mục 3 và 5.
- Kiểm tra ở server để bắt lỗi sớm và dễ đọc. Thiết bị vẫn kiểm tra lại.
- Ở **mỗi câu nói**, plugin xem thời gian sửa file; nếu file đổi thì nạp lại và làm mới danh sách tool của kết nối. Nếu file mới bị lỗi thì **giữ bản cũ** và ghi log từng lỗi kèm số dòng.

**Đăng ký function cho LLM:**
- Mỗi tác vụ đăng ký thành một function `task_<id>` (dùng `register_function` trong `plugins_func/register.py`, kiểu `ToolType.SYSTEM_CTL` để có `conn`). Mô tả lấy từ `description`.
- Xiaozhi-server tạo danh sách function **theo từng kết nối**; plugin làm mới danh sách đó khi nạp lại file, nên tác vụ mới dùng được **ngay từ câu nói tiếp theo**. Tác vụ đã xóa vẫn có câu trả lời lịch sự bằng tiếng Việt nếu LLM còn gọi tên cũ.
- Nếu có hơn 20 tác vụ (`max_task_functions`): gom lại thành một function `run_helmet_task(task: enum)` để danh sách tool không quá dài.

**Chạy tác vụ:**
- Gọi tool MCP trên thiết bị bằng `call_mcp_tool(conn, conn.mcp_client, "self_sequence_run", …)` (hàm ở `core/providers/tools/device_mcp/mcp_handler.py`), sau khi chờ MCP sẵn sàng tối đa 3 s, timeout 5 s.
- **Nhiều tác vụ trong một lượt (D4):** server v0.9.7 chạy các tool call song song, nên các `task_*` cùng `sentence_id` trong ~80 ms được ghép theo thứ tự LLM gọi thành **một** `self.sequence.run`; tác vụ làm chuỗi ghép vượt giới hạn thì bị từ chối riêng, mỗi lệnh nhận kết quả của chính nó, lỗi chung chỉ xin lỗi một lần.
- Có `reply` → trả `Action.RESPONSE` (đọc ngay câu đó). Không có → trả `Action.REQLLM` kèm kết quả để LLM tự diễn đạt.
- Thiết bị chưa sẵn sàng MCP hoặc báo lỗi kiểm tra → trả lời "Xin lỗi thưa ngài, tôi không thực hiện được: <lý do>".

**Khớp nhanh (bỏ qua LLM):**
- Vá `core/handle/intentHandler.py::handle_user_intent`: thêm bước `helmet_tasks.try_quick_phrase(conn, text)` **trước** dòng `if conn.intent_type == "function_call": return False` (bản vá có neo, build dừng nếu upstream đổi chỗ đó).
- Chuẩn hóa câu:
  1. chữ thường;
  2. bỏ dấu câu;
  3. bỏ wake word ở đầu câu ("jarvis", "quản gia", "giắc vít");
  4. gộp khoảng trắng.
- Chỉ coi là khớp khi **cả câu** gần giống một `quick_phrase`: dùng `rapidfuzz` với ngưỡng ≥90, so sánh cả bản có dấu và bản bỏ dấu. Câu có nhiều lệnh ("mở mặt nạ rồi bật đèn") thì vẫn đi qua LLM.
- Khi khớp: chạy tác vụ và đọc `reply` (cách `speak_txt` của upstream, D5); `reply` có thể theo ngôn ngữ (`{vi, en}`). Ghi dòng `[LAT] stage=quick_phrase` để đo độ trễ.

**Đồng bộ xuống thiết bị:**
- Tác vụ có `sync_to_device: true` và id ≤11 ký tự: khi thiết bị kết nối (MCP sẵn sàng), plugin gửi `self.sequence.save(id, steps)`.
- Nhờ vậy nút bấm offline (FW5) luôn dùng bản tác vụ mới nhất. Bản đã lưu không đổi thì bỏ qua (nhớ trong 1 giờ); lệnh lưu gửi sau 0,3 s để không tranh với lệnh đầu tiên của người dùng.

## 2. Tác vụ khởi đầu trong `tasks.yaml`

| id | Ý nghĩa | Các bước chính |
|---|---|---|
| `open_mask` | Mở mặt nạ | mắt `fade_out` → mask open |
| `close_mask` | Đóng mặt nạ | mask close → delay 300 → mắt `power_up` |
| `eyes_on` / `eyes_off` | Bật / tắt đèn mắt | eyes `power_up` / `fade_out` |
| `combat_mode` | Chế độ chiến đấu | mask close → mắt `flicker` → mắt sáng 100% → display "COMBAT MODE" |
| `stealth_mode` | Chế độ ẩn | mắt `fade_out` → output off → display "STEALTH" |
| `salute` | Chào | mask open → mắt `blink` → delay 1000 → mask close |
| `party_mode` | (để sẵn, đang comment) | dùng để thử tiêu chí 1: bỏ comment là có hiệu lực |

## 3. `tools/tasks_cli.py`
- `validate`: kiểm tra `tasks.yaml` theo schema.
- `list`: in danh sách tác vụ và câu khớp nhanh.
- `steps <id> [id…]`: in chuỗi JSON sẽ gửi xuống thiết bị (nhiều id: chuỗi đã gộp như một lượt LLM).
- `match "<câu>"`: thử khớp nhanh một câu nói.
- `fixtures`: chạy bộ fixture chuỗi bước dùng chung với firmware.
- Dùng trong CI hoặc chạy tay trước khi lưu file.

## 4. Công việc
- [x] `schema.json` + `tasks_cli.py validate`
- [x] `helmet_tasks.py`: nạp lại file, đăng ký function, gọi MCP, xử lý lỗi
- [x] Bản vá khớp nhanh (bản vá có neo, ghi trong `server/xiaozhi/patches/README.md`)
- [x] Đồng bộ chuỗi xuống thiết bị
- [x] Viết 7 tác vụ khởi đầu (+ `party_mode` để thử)
- [x] Unit test: chuẩn hóa câu, ngưỡng khớp, nạp lại file khi file lỗi, gộp nhiều tác vụ, fixture dùng chung với firmware

## 5. Tiêu chí nghiệm thu
1. Thêm một tác vụ mới vào YAML (ví dụ `party_mode`) **không khởi động lại gì**. Ở lượt hội thoại sau, nói "Jarvis, bật party mode" → tác vụ chạy.
2. Câu khớp nhanh ("open mask", "mở mặt nạ"): thời gian từ lúc nói xong đến khi servo bắt đầu chạy ≤800ms qua LAN, đo ở chế độ PTT (chế độ tự nhận câu cộng thêm thời gian VAD chờ im lặng, `VAD_MIN_SILENCE_MS` = 700, D28). Máy dev (GTX 1660, Whisper small): 788 ms.
3. "Jarvis, mở mặt nạ rồi bật đèn mắt lên" → LLM gọi đúng 2 tác vụ theo đúng thứ tự.
4. Câu không phải lệnh ("mặt nạ này nặng không?") không bị khớp nhanh nhầm (thử 20 câu gây nhiễu).
5. Sửa YAML thành file lỗi: plugin vẫn dùng bản cũ, log chỉ rõ dòng lỗi.

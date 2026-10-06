# Bộ thử gọi tool của LLM (SV3 – tiêu chí nghiệm thu 2)

Gửi các câu nói mẫu tới LiteLLM với **đúng request mà xiaozhi-server sẽ gửi** (prompt Jarvis, các tin
nhắn few-shot, danh sách tool) rồi chấm tool và tham số trong câu trả lời đầu tiên của model.

Mục tiêu: **≥ 95 %** câu ra lệnh đúng tool và đúng tham số; **10/10** câu không được gọi tool.

| File | Nội dung |
|---|---|
| `tool_calls.yaml` | 30 câu ra lệnh (Việt, Anh, trộn) + 10 câu không gọi tool, kèm lời gọi mong đợi |
| `run_tool_calls.py` | dựng request như xiaozhi, gọi LiteLLM (stream), chấm điểm, xuất JSON |
| `device_tools.json` | danh sách tool MCP của mũ (thứ tự như firmware); `test_run_tool_calls.py` đối chiếu với mã firmware |
| `test_run_tool_calls.py` | kiểm thử offline của bộ này (không gọi mạng) |

## Request được dựng thế nào

- `system`: `server/xiaozhi/prompts/jarvis-prompt.txt` render bằng Jinja2 như `PromptManager` của xiaozhi
  (`{{current_time}}` = giờ hiện tại), persona lấy từ `server/xiaozhi/data/.config.yaml.tmpl`.
- Few-shot tạm thời của xiaozhi (`direct_answer`, `handle_exit_intent`) với chuỗi trong
  `server/xiaozhi/patches/strings_vi.json` (`--upstream-strings` để dùng bản tiếng Trung gốc).
- Tool: `handle_exit_intent`, `get_lunar`, `task_<id>` từ `server/tasks/tasks.yaml`, `ask_expert` (mô tả
  đọc từ plugin), tool của mũ đã "sanitize" (`self.mask.open` → `self_mask_open`), cuối cùng là
  `direct_answer` (tool ảo của xiaozhi: gọi nó = trả lời thẳng, tính là "không gọi tool").
- Tham số: `stream=true`, `max_tokens` và `temperature` lấy từ khối `LLM` của template (300 / 0.6),
  không `tool_choice`, không `top_p` / `frequency_penalty`.

## Cài đặt

Python ≥ 3.10 và: `pip install pyyaml httpx jinja2 jsonschema` (`jinja2` để render prompt giống hệt
xiaozhi, `jsonschema` để kiểm tra tham số theo schema của tool – nếu thiếu thì bỏ qua bước đó và in cảnh báo).

## Chạy

Trên máy server (LiteLLM chỉ nằm trong mạng Docker), cách gọn nhất là chạy trong một container cùng mạng:

```bash
cd /opt/jarvis
docker run --rm --network jarvis_app -v "$PWD:/w" -w /w python:3.12-slim sh -c \
  "pip install -q pyyaml httpx jinja2 jsonschema && \
   python tools/tests/llm/run_tool_calls.py --base-url http://litellm:4000/v1 --env-file server/.env --no-fallbacks"
```

Hoặc mở cổng LiteLLM trên `127.0.0.1` bằng `docker-compose.debug.yml` rồi chạy trực tiếp trên host:

```bash
cd /opt/jarvis/server && docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d litellm
cd /opt/jarvis && python3 tools/tests/llm/run_tool_calls.py --env-file server/.env --no-fallbacks
```

Trên máy dev (Docker Desktop, Ollama của máy với model nhỏ `qwen3:4b-instruct`):

```bash
cd server
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.debug.yml up -d redis litellm
cd .. && python tools/tests/llm/run_tool_calls.py --env-file server/.env
```

Tùy chọn hay dùng:

| Tùy chọn | Ý nghĩa |
|---|---|
| `--no-fallbacks` | gửi `disable_fallbacks=true`: chỉ đo model local (không thì một lần timeout sẽ do Claude trả lời) |
| `--only open_vi,multi_e3` | chỉ chạy vài câu |
| `--repeat 3` | chạy mỗi câu 3 lần (đo độ ổn định) |
| `--json-out kq.json` | lưu chi tiết từng câu |
| `--model jarvis-cloud` | thử với model cloud |
| `--dry-run`, `--dump-request req.json`, `--list-tools` | chỉ dựng request, không gọi LiteLLM |
| `--shuffle-plugins 7` | đảo thứ tự tool plugin như một tiến trình xiaozhi mới (xiaozhi dùng `set`) |

## Đọc kết quả

```
ID                 RESULT  TTFT  TOTAL  GOT                          EXPECTED
open_vi            PASS    0.39   0.39  task_open_mask               task_open_mask | self_mask_open
open_en            ALT     0.59   0.59  self_mask_open(speed=50)     task_open_mask | self_mask_open
mask_slow_vi       FAIL    0.58   0.58  self_mask_open(speed=5)      ...  [schema: self_mask_open.speed: 5 is less than the minimum of 10]
```

- `PASS` đúng phương án ưu tiên; `ALT` một phương án khác vẫn được chấp nhận; `FAIL` sai; `ERROR` lỗi
  HTTP/kết nối (không tính vào tỉ lệ, nhưng nếu mọi request đều lỗi thì mã thoát là 2).
- Tham số phải đúng **cả kiểu** (`false` khác `"false"`) và hợp lệ theo schema của tool (min/max, bắt
  buộc): mũ sẽ từ chối lời gọi sai schema.
- `[fallback->jarvis-cloud]`: câu trả lời đến từ model cloud. `[quick-phrase:open_mask]`: câu này sẽ được
  plugin `helmet_tasks` xử lý trước khi tới LLM (chỉ để tham khảo).
- Lời gọi thêm chỉ để đọc trạng thái (`settings.tolerated_extra_tools`) được bỏ qua ở câu ra lệnh.
- Mã thoát: `0` đạt mục tiêu, `1` dưới mục tiêu, `2` lỗi hạ tầng/đầu vào.

Kết quả tham khảo (2026-10-06, model nhỏ `qwen3:4b-instruct` trên GTX 1660 SUPER, cấu hình
`config.local.yaml`): 28–29/30 câu ra lệnh, 9/10 câu không gọi tool, token đầu tiên trung vị 0.53 s.
Số liệu nghiệm thu thật phải đo với model 30B trên RTX 5090.

## Sửa bộ thử

- Thêm câu: xem chú thích đầu `tool_calls.yaml` (`calls`, `one_of`, `args` với `min`/`max`/`contains`/
  `contains_any`/`one_of`, `args_any`). `test_run_tool_calls.py` kiểm tra mọi tool mong đợi đều được
  đưa cho LLM và giá trị mong đợi hợp lệ theo schema.
- Firmware đổi tool MCP → `test_device_tools_match_firmware` báo lỗi → cập nhật `device_tools.json`
  (tên, kiểu, default, min/max, maxLength, thứ tự như firmware). Có thể thay cả file bằng kết quả
  `tools/list` thật (log thiết bị hoặc `tools/tests/e2e/device_sim.py`) qua `--device-tools`.

```bash
.venv/Scripts/python -m pytest tools/tests/llm -q
```

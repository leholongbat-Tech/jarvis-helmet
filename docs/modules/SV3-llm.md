# SV3 – LLM local + chuyển sang cloud

| | |
|---|---|
| **Mục tiêu** | "Bộ não" hội thoại chạy local trên RTX 5090, gọi tool (function calling) chính xác. Tự chuyển sang cloud khi local lỗi, hoặc khi câu hỏi cần model mạnh hơn |
| **Phụ thuộc** | SV1 |
| **Kết quả giao nộp** | `server/litellm/config.yaml` + `jarvis_callbacks.py`, `server/ollama/entrypoint.sh`, plugin `server/xiaozhi/plugins/ask_expert.py`, bộ thử gọi tool `tools/tests/llm/` |
| **Trạng thái** | Code xong. Đã chạy thử trên máy dev với model nhỏ thay thế (`qwen3:4b-instruct`) và một "cloud" giả cùng cách tính giá (`docker-compose.local.yml`); dự phòng sang cloud, ghi log chi phí, ngân sách và `ask_expert` hoạt động. Chưa chạy model 30B trên batai và chưa có khóa Anthropic |

## 1. Thiết kế

```
xiaozhi-server ── OpenAI API ──► LiteLLM (model "jarvis-main")
                                   ├─► Ollama: Qwen MoE local (chính)
                                   └─► (khi lỗi hoặc quá thời gian) model "jarvis-cloud": Claude / OpenAI / Gemini
plugin ask_expert ── gọi thẳng ──► LiteLLM (model "jarvis-cloud")
```

**Model local (D2):**
- `qwen3:30b-a3b-instruct-2507-q4_K_M` trong Ollama 0.35.1 (19 GB, bản instruct không "thinking", gọi tool được), `OLLAMA_CONTEXT_LENGTH=8192`, `OLLAMA_KEEP_ALIVE=-1` (luôn nằm trong VRAM), `OLLAMA_NUM_PARALLEL=1`, flash attention. Đổi bằng `OLLAMA_MODEL` trong `.env`.
- LiteLLM gọi bằng `ollama_chat/` với `think: false`. `ollama/entrypoint.sh` nạp sẵn model khi container khởi động, để câu đầu tiên sau khi bật máy không bị timeout rồi rơi sang cloud trả tiền. `scripts/pull_models.sh` tải model.
- Phương án khác: `gemma4:26b-a4b-it-qat`, `qwen3.6:35b-a3b-q4_K_M` (24 GB, chỉ khi đủ VRAM). Nếu cần độ trễ thấp hơn nữa thì dùng vLLM thay Ollama.

**LiteLLM:**
- `model_list` gồm 2 model logic: `jarvis-main` (Ollama) và `jarvis-cloud` (nhà cung cấp cloud cấu hình qua `.env`).
- Chuyển sang cloud: `fallbacks: [{jarvis-main: [jarvis-cloud]}]`, timeout 15 giây, 1 lần thử lại (nghỉ 0,2 s trước khi thử lại) – LiteLLM v1.104.0 ghim digest (D25).
- Đổi nhà cung cấp cloud chỉ cần sửa `CLOUD_MODEL` + khóa trong `.env` và giá trong `litellm/config.yaml`, không động vào xiaozhi-server (`server/README.md` mục 10).
- **Model cloud (D1):** `anthropic/claude-opus-5-5`. Model này không nhận `temperature`/`top_p` nên LiteLLM bỏ các tham số đó; giá khai báo $4/$20 mỗi triệu token (vào/ra) để tính ngân sách; `max_tokens` của lượt gọi cloud được nâng tối thiểu 1024 vì token "nghĩ" cũng tính vào đó. Rẻ/nhanh hơn: `claude-sonnet-5-5`, `claude-haiku-4-5`.
- **Chặn chi phí:** `max_budget` = `CLOUD_MONTHLY_BUDGET_USD` mỗi tháng cho `jarvis-cloud`, chi tiêu lưu trong Redis nên không mất khi khởi động lại; hết ngân sách → 429, không gọi cloud nữa đến kỳ sau. Giá trị phải > 0 (LiteLLM hiểu 0 là "không giới hạn", nên callback từ chối khởi động); muốn chặn hẳn cloud thì để trống khóa. Callback `jarvis_callbacks.py` ghi một dòng log cho mỗi lần gọi cloud (model, token, chi phí).
- Khóa nhà cung cấp chỉ vào container LiteLLM dưới tên `JARVIS_*_API_KEY`: route pass-through của LiteLLM (`/anthropic`…) dùng tên mặc định của nhà cung cấp và bỏ qua ngân sách, nên tên đó không bao giờ có trong container.

**Plugin `ask_expert(question)`:**
- Mô tả cho LLM local: chỉ gọi khi câu hỏi cần kiến thức chuyên sâu hoặc mới, khi local không chắc chắn, hoặc khi người dùng nói "hỏi cloud" / "ask the expert".
- Plugin gửi câu hỏi tới `jarvis-cloud` kèm prompt hệ thống: "trả lời ngắn để đọc thành tiếng, cùng ngôn ngữ với câu hỏi, giữ phong cách quản gia".
- Kết quả trả về dạng `Action.RESPONSE` để đọc luôn, không vòng lại LLM local, cho đỡ trễ. Trong lúc chờ cloud, mũ nói một câu đệm ngắn.

**Cấu hình phía xiaozhi-server** (chi tiết ở SV5):
- `LLM: JarvisLLM` với `type: openai`, `base_url: http://litellm:4000/v1`, `model_name: jarvis-main`, khóa `LITELLM_MASTER_KEY`.
- Intent `function_call`.

## 2. Bộ thử gọi tool (`tools/tests/llm/tool_calls.yaml`, chạy bằng `run_tool_calls.py`)
- **30 câu ra lệnh** nhiều cách nói, cả trộn ngôn ngữ, mỗi câu kèm tool mong đợi. Ví dụ:
  - "cho tôi xem mặt" → `task_open_mask`
  - "lower the faceplate" → `task_close_mask`
  - "tắt đèn mắt đi" → `self.eyes.set(on=false)`
- **10 câu không được gọi tool**, ví dụ "mặt nạ này làm bằng gì?".
- Script chạy thẳng với LiteLLM, dùng đúng định nghĩa tool mà xiaozhi-server sẽ gửi: các `task_*` sinh từ `tasks.yaml`, `ask_expert`, và tool MCP của mũ lấy từ `device_tools.json` (test đối chiếu với mã firmware). Cách chạy: `tools/tests/llm/README.md`.

## 3. Công việc
- [x] Chọn model local (D2); [ ] tải và thử trên batai
- [x] Viết `litellm/config.yaml` (2 model, fallback, budget, log)
- [x] Viết plugin `ask_expert`
- [x] Viết bộ thử gọi tool (30 câu gọi tool + 10 câu không gọi); [ ] chạy với model 30B và chỉnh mô tả/prompt cho đến khi đạt

## 4. Tiêu chí nghiệm thu
1. Token đầu tiên (local) ≤400ms. Câu trả lời ngắn (≤40 từ) xong trong ≤1.5 giây.
2. Bộ thử gọi tool: ≥95% đúng tool và đúng tham số; 10/10 câu không gọi tool thì đúng là không gọi.
3. Dừng container Ollama: câu tiếp theo vẫn được trả lời nhờ cloud (chậm hơn tối đa ~3 giây). Log ghi rõ là đã chuyển sang cloud.
4. "Jarvis, hỏi chuyên gia: …" → gọi `ask_expert`, nhận câu trả lời cloud ngắn gọn bằng đúng ngôn ngữ đã hỏi.

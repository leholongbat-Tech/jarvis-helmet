# SV3 – LLM local + chuyển sang cloud

| | |
|---|---|
| **Mục tiêu** | "Bộ não" hội thoại chạy local trên RTX 5090, gọi tool (function calling) chính xác. Tự chuyển sang cloud khi local lỗi, hoặc khi câu hỏi cần model mạnh hơn |
| **Phụ thuộc** | SV1 |
| **Kết quả giao nộp** | `server/litellm/config.yaml`, cấu hình Ollama, plugin `ask_expert` (nằm chung với SV6), bộ thử gọi tool |

## 1. Thiết kế

```
xiaozhi-server ── OpenAI API ──► LiteLLM (model "jarvis-main")
                                   ├─► Ollama: Qwen MoE local (chính)
                                   └─► (khi lỗi hoặc quá thời gian) model "jarvis-cloud": Claude / OpenAI / Gemini
plugin ask_expert ── gọi thẳng ──► LiteLLM (model "jarvis-cloud")
```

**Model local:**
- Dòng Qwen3.x MoE khoảng 30–35B, kích hoạt ~3B tham số mỗi token (ví dụ `Qwen3.6-35B-A3B`), lượng tử 4-bit, chạy trong Ollama.
- **Tên và phiên bản model phải kiểm tra lại lúc triển khai.** Tiêu chí chọn: tiếng Việt tốt, gọi tool ổn định, vừa ~20GB VRAM.
- **Tắt chế độ thinking** (`think: false`, hoặc dùng bản instruct) để giảm độ trễ. Câu trả lời bằng giọng nói cần ngắn và nhanh.
- Dự phòng: Gemma 4 26B-A4B (đa ngôn ngữ, có gọi tool sẵn). Nếu cần độ trễ thấp hơn nữa thì dùng vLLM thay Ollama.

**LiteLLM:**
- `model_list` gồm 2 model logic: `jarvis-main` (Ollama) và `jarvis-cloud` (nhà cung cấp cloud cấu hình qua `.env`).
- Chuyển sang cloud: `fallbacks: [{jarvis-main: [jarvis-cloud]}]`, timeout 15 giây, 1 lần thử lại.
- Đổi nhà cung cấp cloud chỉ cần sửa `litellm/config.yaml` và `.env`, không động vào xiaozhi-server.
- **Model ID cloud:** tra danh sách mới nhất lúc triển khai. Với Claude, dùng skill/tài liệu Claude API để lấy model ID hiện hành; không ghi cứng từ trí nhớ.
- **Chặn chi phí:** đặt `max_budget` theo tháng cho `jarvis-cloud` và ghi log mỗi lần gọi cloud.

**Plugin `ask_expert(question)`:**
- Mô tả cho LLM local: chỉ gọi khi câu hỏi cần kiến thức chuyên sâu hoặc mới, khi local không chắc chắn, hoặc khi người dùng nói "hỏi cloud" / "ask the expert".
- Plugin gửi câu hỏi tới `jarvis-cloud` kèm prompt hệ thống: "trả lời ngắn để đọc thành tiếng, cùng ngôn ngữ với câu hỏi, giữ phong cách quản gia".
- Kết quả trả về dạng `Action.RESPONSE` để đọc luôn, không vòng lại LLM local, cho đỡ trễ.

**Cấu hình phía xiaozhi-server** (chi tiết ở SV5):
- `LLM: OpenAILLM` với `type: openai`, `base_url: http://litellm:4000/v1`, `model_name: jarvis-main`.
- Intent `function_call`.

## 2. Bộ thử gọi tool (`tools/tests/llm/tool_calls.yaml`)
- **30 câu ra lệnh** nhiều cách nói, cả trộn ngôn ngữ, mỗi câu kèm tool mong đợi. Ví dụ:
  - "cho tôi xem mặt" → `task_open_mask`
  - "lower the faceplate" → `task_close_mask`
  - "tắt đèn mắt đi" → `self.eyes.set(on=false)`
- **10 câu không được gọi tool**, ví dụ "mặt nạ này làm bằng gì?".
- Script chạy thẳng với LiteLLM, dùng đúng định nghĩa tool mà xiaozhi-server sẽ gửi.

## 3. Công việc
- [ ] Chọn và tải model local; thử tiếng Việt và gọi tool bằng tay
- [ ] Viết `litellm/config.yaml` (2 model, fallback, budget, log)
- [ ] Viết plugin `ask_expert`
- [ ] Viết và chạy bộ thử gọi tool; chỉnh mô tả tool / prompt cho đến khi đạt

## 4. Tiêu chí nghiệm thu
1. Token đầu tiên (local) ≤400ms. Câu trả lời ngắn (≤40 từ) xong trong ≤1.5 giây.
2. Bộ thử gọi tool: ≥95% đúng tool và đúng tham số; 10/10 câu không gọi tool thì đúng là không gọi.
3. Dừng container Ollama: câu tiếp theo vẫn được trả lời nhờ cloud (chậm hơn tối đa ~3 giây). Log ghi rõ là đã chuyển sang cloud.
4. "Jarvis, hỏi chuyên gia: …" → gọi `ask_expert`, nhận câu trả lời cloud ngắn gọn bằng đúng ngôn ngữ đã hỏi.

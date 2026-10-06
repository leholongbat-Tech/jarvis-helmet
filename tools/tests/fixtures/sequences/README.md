# Bộ fixture chuỗi bước (sequence steps) dùng chung

Thư mục này chứa các ca kiểm thử cho **bộ kiểm tra chuỗi bước** (docs/interfaces.md mục 3).
Hai bên cùng nạp **mọi** file `*.json` ở đây và phải cho cùng kết quả:

- firmware: test chạy trên máy tính `firmware/tests/host/` (FW-C, `seq::Validate`);
- server: `server/tasks/taskslib.py` (`check_steps`), chạy bởi `server/tests/test_taskslib.py`
  và `python tools/tasks_cli.py fixtures`.

Chủ sở hữu: SV-D. Muốn đổi luật thì sửa đồng thời interfaces.md, firmware, server và fixture.
Trong thư mục chỉ được có file fixture `.json` (đúng định dạng dưới đây) và README này.

## 1. Định dạng một file

```json
{
  "name": "invalid_mask_speed_low",
  "steps": [ {"mask": {"action": "open", "speed": 5}} ],
  "valid": false,
  "error_step": 1,
  "error_contains": "speed",
  "estimated_ms": null,
  "note": "speed < 10."
}
```

| Khóa | Kiểu | Ý nghĩa |
|---|---|---|
| `name` | string | Trùng tên file (không có `.json`). Tiền tố `valid_` / `invalid_`; `*_total_*` là ca về giới hạn 60 s |
| `steps` | JSON bất kỳ | Giá trị đưa vào bộ kiểm tra. Thường là mảng, nhưng có ca cố ý là object/chuỗi |
| `valid` | bool | Kết quả mong đợi |
| `error_step` | int \| null | Chỉ dùng khi `valid=false`: số thứ tự bước lỗi **đếm từ 1** (D23). `null` = lỗi cấp trên (không phải mảng, quá 64 bước, quá 60 s) |
| `error_contains` | string \| null | Chỉ dùng khi `valid=false`: chuỗi con **bắt buộc** có trong thông báo lỗi (phân biệt hoa thường). `null` = không kiểm tra nội dung |
| `estimated_ms` | int \| null | Chỉ dùng khi `valid=true`: tổng thời gian ước tính (ms) phải bằng đúng giá trị này |
| `note` | string | Giải thích ngắn (tiếng Việt), bỏ qua khi chạy |

## 2. Cách chạy một fixture (harness)

1. Đọc file, lấy `steps`.
2. Chuyển `steps` về chuỗi JSON gọn (UTF-8, không escape ASCII — ví dụ `cJSON_PrintUnformatted`
   hoặc `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`) rồi đưa vào bộ kiểm tra
   (server có thể kiểm tra thẳng giá trị đã parse, kết quả như nhau).
3. So sánh:
   - `valid` phải khớp;
   - nếu hợp lệ: `estimated_ms` phải bằng tổng ước tính;
   - nếu lỗi và `error_step` là số `n`: thông báo phải **bắt đầu bằng** `invalid step #n: `;
   - nếu lỗi và `error_step` là `null`: thông báo **không** được bắt đầu bằng `invalid step #`;
   - nếu `error_contains` khác `null`: thông báo phải chứa đúng chuỗi đó.

Giới hạn 4000 byte của tham số `steps` (MCP `maxLength`, blob NVS) **không** nằm trong bộ fixture:
mọi fixture đều nhỏ hơn 4000 byte khi viết gọn.

## 3. Luật kiểm tra (chuẩn chung cho cả hai bên)

Thiết bị kiểm tra **toàn bộ** chuỗi trước khi chạy; sai một bước thì từ chối cả chuỗi.

Thứ tự kiểm tra: (1) phải là mảng JSON → (2) tối đa 64 bước → (3) từng bước theo thứ tự, gặp lỗi
đầu tiên thì dừng → (4) tổng ước tính ≤ 60000 ms. Mảng rỗng `[]` **hợp lệ** (không làm gì).

Mỗi phần tử là object có **đúng một khóa**, khóa là loại bước (phân biệt hoa thường). Trong mỗi bước
chỉ được dùng các trường liệt kê dưới đây; trường lạ là lỗi.

| Bước | Trường | Luật |
|---|---|---|
| `mask` | `action` ∈ {`open`,`close`} **hoặc** `position` 0..100 (đúng một trong hai, bắt buộc); `speed` 10..100 (tùy chọn) | |
| `servo` | `id` 0..1 (bắt buộc), `angle` 0..180 (bắt buộc), `duration_ms` 0..5000 (tùy chọn, mặc định 0) | |
| `eyes` | dạng bật/tắt: `on` bool (bắt buộc) + `brightness` 0..100 (tùy chọn, dùng được cả khi `on=false`) **hoặc** dạng hiệu ứng: `effect` ∈ {`breathe`,`blink`,`flicker`,`power_up`,`fade_out`} (bắt buộc) + `duration_ms` 100..10000 (tùy chọn, mặc định 1000) | Không trộn hai dạng: `brightness` chỉ đi với `on`, `duration_ms` chỉ đi với `effect` |
| `output` | `on` bool (bắt buộc) | |
| `delay` | giá trị là **số nguyên trần** 0..10000 (ms), không phải object | |
| `display` | `title` string ≤ 24 ký tự (bắt buộc, được rỗng), `text` string ≤ 200 ký tự (bắt buộc), `seconds` 1..30 (tùy chọn, mặc định 5) | Giống `self.display.show_info` |
| `sound` | `name` khớp `[a-z0-9_]{1,32}` (bắt buộc) | |

Kiểu dữ liệu:
- "Số nguyên" theo JSON: `true`/`false` không phải số; `300.5` không phải số nguyên.
  (Số viết `300.0` được coi là 300 vì JSON chỉ có một kiểu số — không có fixture cho trường hợp này,
  server luôn gửi số nguyên.)
- `null` **không bao giờ** hợp lệ: trường có mặt mà bằng `null` là lỗi sai kiểu, không coi là bỏ trống.
- Độ dài chuỗi của `display` tính bằng **code point Unicode** của chuỗi nhận được, không chuẩn hóa
  (cùng cách với `helmet::Utf8Length`). Chuỗi tiếng Việt dạng NFD có nhiều code point hơn dạng NFC.
  Server chuẩn hóa NFC trước khi gửi nên chuỗi từ `tasks.yaml` luôn ở dạng NFC.

## 4. Công thức ước tính thời gian (`seq::EstimateStepMs`)

| Bước | Thời gian tính vào tổng |
|---|---|
| `mask` | cả hành trình ở tốc độ của bước: `800 * 100 / speed` (chia nguyên kiểu C++; thiếu `speed` → 70 → 1142). Bước `position` cũng tính cả hành trình (trường hợp xấu nhất) |
| `servo` | `duration_ms` (thiếu → 0) |
| `eyes` dạng hiệu ứng | `duration_ms` (thiếu → 1000); riêng `breathe` = 0 vì lặp đến lệnh sau |
| `delay` | giá trị của nó |
| `eyes` dạng on, `output`, `display`, `sound` | 0 |

Ví dụ: `open_mask` = fade_out 300 + mask speed 80 (1000) = 1300 ms.

## 5. Quy ước thông báo lỗi

- Lỗi gắn với một bước: `invalid step #<n>: <lý do>`, `n` đếm từ 1 (D23).
- Lỗi cấp trên (không có `invalid step #`):
  - không phải mảng: phải chứa `array`;
  - quá 64 bước: phải chứa `64`;
  - quá 60 s: phải chứa `60000` (giới hạn tính bằng ms).
- Lý do phải **nêu tên trường hoặc loại bước gây lỗi** đúng như trong JSON (ví dụ `speed`,
  `duration_ms`, `title`, `laser`, `color`). Với các tên quá ngắn dễ trùng (`id`, `on`) fixture để
  `error_contains: null`.
- Phía thiết bị: thông báo không được chứa `"`, `\` hoặc ký tự xuống dòng (đi qua
  `helmet::SanitizeMessage`), vì `McpServer::ReplyError` không escape.

Ví dụ thông báo của server: `invalid step #1: mask.speed out of range 10..100 (got 5)`,
`invalid step #2: unknown step type 'laser' (allowed: mask, servo, eyes, output, delay, display, sound)`,
`too many steps: 65 (max 64)`, `estimated duration 60001 ms exceeds the 60000 ms limit`.

## 6. Danh mục ca kiểm thử

- `valid_*` (24 ca): mảng rỗng, `open_mask`/`close_mask`, ví dụ đủ 7 loại bước của interfaces.md,
  biên của mọi khoảng giá trị, trường tùy chọn và giá trị mặc định, tiêu đề/nội dung tiếng Việt đúng
  24/200 ký tự, tiêu đề rỗng, đúng 64 bước, tổng đúng 60000 ms theo nhiều cách (delay, mask tốc độ 10, breathe = 0,
  mask tốc độ mặc định).
- `invalid_*` (59 ca): không phải mảng, loại bước lạ, sai hoa thường, hai khóa trong một bước, object
  rỗng, phần tử không phải object/null, trường lạ, mọi giá trị ngoài khoảng, sai kiểu (chuỗi, null,
  số thực, bool), trộn dạng (`action`+`position`, `on`+`effect`, `effect`+`brightness`,
  `on`+`duration_ms`), thiếu trường bắt buộc, tên hiệu ứng/âm thanh sai, tiêu đề 25 ký tự, NFD,
  65 bước, tổng 60001 ms theo nhiều cách (`invalid_total_*`), lỗi chỉ ở bước cuối.

Các ca `invalid_total_*` chỉ vi phạm giới hạn 60 s — luật mà JSON Schema (`server/tasks/schema.json`)
không diễn đạt được; mọi ca `invalid_*` khác cũng bị schema từ chối.

Chạy kiểm tra phía server:

```bash
.venv/Scripts/python tools/tasks_cli.py fixtures
.venv/Scripts/python -m pytest server/tests/test_taskslib.py -k fixture
```

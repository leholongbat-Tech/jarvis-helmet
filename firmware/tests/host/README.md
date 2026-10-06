# Host unit tests – phần C++ thuần của board `jarvis-helmet`

Các test này chạy trên máy tính (không cần ESP32) cho những phần không phụ thuộc ESP-IDF:

| File test | Kiểm tra |
|---|---|
| `test_sequence_validator.cpp` | `main/boards/jarvis-helmet/sequence_validator.cc`: mọi quy tắc ở `docs/interfaces.md` mục 3, đánh số lỗi từ 1 (contract D23), chuỗi `open_mask`/`close_mask` có sẵn trong firmware khớp với interfaces.md mục 5 |
| `test_helmet_common.cpp` | `helmet_common.h`: `MaskMoveMs`, kiểm tra tên, `Utf8Length`, `SanitizeMessage`, `JsonNestingWithin` (chặn JSON lồng quá sâu trước cJSON) |
| `test_local_mcp_policy.cpp` | `local_mcp_policy.h`: danh sách tool mà server MCP cục bộ (`ws://<IP-mũ>:8080/ws`) cho gọi, giới hạn độ lồng của frame |
| `test_local_mcp_token.cpp` | `local_mcp_policy.h`, token truy cập tùy chọn của server MCP cục bộ (contract D27): đọc `token=` trong query của URL bắt tay (thiếu, rỗng, mã hóa `%XX`/`+`, tham số khác, khóa lặp, escape hỏng, fragment), so sánh thời gian hằng (sai độ dài, sai hoa thường), ký tự hợp lệ của token (1–64 ký tự `A-Z a-z 0-9 - . _ ~`) và quyết định chấp nhận / từ chối bắt tay |
| `test_extra_headers.cpp` | `main/protocols/extra_headers.cc` (core, header CF Access): chỉ gắn cho đúng host và chỉ với URL `https://`/`wss://`. `stubs/esp_log.h` thay cho `esp_log.h` của ESP-IDF |
| `test_sequence_fixtures.cpp` | Chạy mọi fixture dùng chung trong `tools/tests/fixtures/sequences/*.json` (contract 4.11). Nếu thư mục rỗng hoặc chưa có thì test được **bỏ qua (skipped)** kèm thông báo |
| `test_*.cpp` khác | CMake tự gom mọi file `test_*.cpp` trong thư mục này (ví dụ `test_actuator_math.cpp` của FW-B) |

Thư viện: cJSON v1.7.18 và Catch2 v3.7.1, tải bằng CMake `FetchContent` (có kiểm tra SHA256).

## Chạy test

Dùng image `espressif/idf:v6.1` (đã có CMake 4, Ninja, g++ 13). Từ thư mục gốc repo, trong **Git Bash**:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo:ro" -w /repo/firmware/tests/host espressif/idf:v6.1 bash -c "cmake -S . -B /tmp/b -G Ninja && cmake --build /tmp/b && ctest --test-dir /tmp/b --output-on-failure"
```

Hoặc ghi rõ đường dẫn repo:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "C:/Users/Longlhvip/0.Projects/Jarvis_helmet/jarvis-helmet:/repo:ro" -w /repo/firmware/tests/host espressif/idf:v6.1 bash -c "cmake -S . -B /tmp/b -G Ninja && cmake --build /tmp/b && ctest --test-dir /tmp/b --output-on-failure"
```

Trong **PowerShell** (từ thư mục gốc repo):

```powershell
docker run --rm -v "${PWD}:/repo:ro" -w /repo/firmware/tests/host espressif/idf:v6.1 bash -c "cmake -S . -B /tmp/b -G Ninja && cmake --build /tmp/b && ctest --test-dir /tmp/b --output-on-failure"
```

Ghi chú:
- Repo được mount chỉ đọc (`:ro`); thư mục build nằm trong container (`/tmp/b`) nên không sinh file nào trong repo. Mỗi lần chạy cần mạng để tải cJSON và Catch2 (cả lệnh mất khoảng 30 giây trên máy 12 nhân).
- Chạy một nhóm test: thay lệnh `ctest ...` bằng `/tmp/b/jarvis_host_tests "[validator]"` (tag: `[validator]`, `[common]`, `[fixtures]`, `[builtins]`, `[actuator]`, `[extra_headers]`, `[local_mcp]`, `[token]` ...).
- Dùng thư mục fixture khác: đặt biến môi trường `JARVIS_SEQUENCE_FIXTURES_DIR=/đường/dẫn` khi chạy test (`docker run -e ...`), hoặc cấu hình `-DJARVIS_SEQUENCE_FIXTURES_DIR=...`.
- Không có mạng: tải sẵn mã nguồn rồi truyền `-DFETCHCONTENT_SOURCE_DIR_CJSON=<thư mục cJSON>` và `-DFETCHCONTENT_SOURCE_DIR_CATCH2=<thư mục Catch2>`.

## Quy ước kiểm tra mà phía server (SV-D, Python) phải làm giống

Firmware dùng cJSON nên phía Python cần theo đúng các điểm sau để hai bên cho cùng kết quả trên mọi fixture:

- **Số nguyên** là số JSON không có phần thập phân: `300` và `300.0` đều hợp lệ, `300.5` thì không (cJSON không phân biệt `300` với `300.0`). `true`/`false` **không** phải số (lưu ý trong Python `isinstance(True, int)` là `True`).
- **Boolean** chỉ nhận `true`/`false`, không nhận `0`/`1`.
- **Tên khóa** so khớp chính xác, phân biệt hoa thường (`Mask` là khóa lạ). Khóa trùng trong cùng một object bị từ chối.
- **Độ dài chữ** của `display.title` (0–24) và `display.text` (0–200) tính theo **code point** UTF-8 (giống `len()` của Python với chuỗi đã NFC). Dấu tổ hợp rời (NFD) tính thành code point riêng.
- **Ước lượng thời gian**: `mask` = hành trình đầy đủ `800 * 100 // speed` (mặc định speed 70 → 1142 ms), `servo` = `duration_ms` (mặc định 0), `eyes` effect = `duration_ms` (mặc định 1000; `breathe` = 0), `delay` = giá trị, các bước khác = 0. Tổng tối đa 60000 ms.
- **Thứ tự kiểm tra**: mảng → số bước (≤ 64) → từng bước theo thứ tự (lỗi đầu tiên được báo) → tổng thời gian.

Nội dung lỗi (sau `invalid step #<n>: `, n đếm từ 1):

| Trường hợp | Thông báo |
|---|---|
| Phần tử không phải object | `step must be an object` |
| Không đúng một khóa | `step must have exactly one key (got <k>)` |
| Loại bước lạ | `unknown step type <key>` |
| Trường lạ / trùng | `unknown field <field> in <type>` / `duplicate field <field> in <type>` |
| Thiếu trường bắt buộc | `missing field <field>` |
| Sai kiểu | `<field> must be an integer` / `must be a boolean` / `must be a string`, `<type> must be an object` |
| Ngoài khoảng | `<field> out of range <min>..<max>` (ví dụ `speed out of range 10..100`, `delay out of range 0..10000`) |
| `mask` | `mask needs exactly one of action or position`, `action must be open or close` |
| `eyes` | `eyes needs on or effect`, `eyes cannot mix on/brightness with effect/duration_ms`, `unknown effect <name> (use breathe, blink, flicker, power_up, fade_out)` |
| `display` | `title too long (max 24 chars)`, `text too long (max 200 chars)` (tiêu đề rỗng hợp lệ: thẻ chỉ có nội dung) |
| `sound` | `sound name must match [a-z0-9_]{1,32}` |

Lỗi cấp cao nhất (không có `invalid step #`): `steps must be a JSON array` (thêm ` (invalid JSON)` hoặc ` (nesting too deep)` khi chuỗi không phải JSON hợp lệ), `too many steps (max 64)`, `sequence too long: <ms> ms (max 60000)`.

Mọi thông báo đều đi qua `helmet::SanitizeMessage` (không có `"`, `\` hay ký tự điều khiển) vì firmware gốc chèn thẳng thông báo lỗi vào JSON trả về.

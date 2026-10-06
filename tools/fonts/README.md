# Font LVGL cho HUD (FW6)

Thư mục này sinh ra các font LVGL mà HUD của mũ (`JarvisHudDisplay`) dùng trực tiếp, để hiện
đủ chữ tiếng Việt có dấu. Font có sẵn của xiaozhi (`font_noto_sans_basic_16_4`) chỉ có 23/90
chữ Việt dựng sẵn và thiếu dấu `–`, nên câu nghiệm thu của FW6 bị mất chữ.

| File sinh ra (trong `firmware/main/boards/jarvis-helmet/`) | Font gốc | Cỡ | Dùng cho |
|---|---|---|---|
| `font_jarvis_vi_16_4.c` | Noto Sans Medium 2.015 | 16 px, 4 bpp | Câu trả lời của Jarvis, tiêu đề thẻ thông tin |
| `font_jarvis_vi_14_4.c` | Noto Sans Medium 2.015 | 14 px, 4 bpp | Câu người dùng nói, dòng trạng thái, nội dung thẻ |
| `font_jarvis_hud_16_4.c` | Rajdhani SemiBold 1.201 | 16 px, 4 bpp | Đồng hồ, điện áp, nhãn `MASK`/`EYES`/`OFFLINE` (chỉ ASCII) |

Ba file này **được commit vào repo**: build firmware không cần Node.js hay mạng. Chỉ chạy lại
script khi muốn đổi font, cỡ chữ hoặc dải ký tự.

## Yêu cầu

- Python ≥ 3.10 (chỉ dùng thư viện chuẩn), ví dụ `.venv` của repo.
- Node.js có `npx` trong PATH (đã thử với Node 24). Script tự gọi `npx -y lv_font_conv@1.5.3`.
- Mạng ở lần chạy đầu: tải font (≈ 0,8 MB) và gói `lv_font_conv` (npm cache).

## Cách chạy

```bash
# Từ thư mục gốc của repo
python tools/fonts/generate_fonts.py            # tải font (nếu chưa có), chuyển đổi, kiểm tra glyph
python tools/fonts/generate_fonts.py --check-only   # chỉ kiểm tra glyph của các file đã có
python tools/fonts/generate_fonts.py --cache-dir D:/fontcache   # đổi thư mục cache
python tools/fonts/generate_fonts.py --offline  # không tải gì, chỉ dùng cache
```

- Font được tải từ nguồn chính thức, **ghim theo commit** và kiểm tra SHA-256:
  Noto Sans từ `notofonts/notofonts.github.io` (bản static unhinted), Rajdhani từ `google/fonts`.
  Sai SHA-256 thì script dừng, không sinh file.
- Cache mặc định là `tools/fonts/cache/` (hoặc biến môi trường `JARVIS_FONT_CACHE`). Thư mục này
  không được commit (cần dòng `tools/fonts/cache/` trong `.gitignore`).
- `lv_font_conv` chạy bên trong thư mục cache với đường dẫn tương đối, nên dòng `Opts:` ở đầu file
  `.c` không chứa đường dẫn riêng của máy; chạy lại với cùng phiên bản cho ra file giống hệt.
- Sau khi thêm/đổi tên file font trong thư mục board, CMake phải chạy lại (board dùng `file(GLOB)`
  lúc configure). `scripts/build.py` luôn chạy `idf.py reconfigure` nên không cần làm gì thêm;
  nếu build tay bằng `idf.py build` thì chạy `idf.py reconfigure` trước.

Tham số chuyển đổi (giống cách xiaozhi-fonts 2.0.0 sinh font của upstream):

```
--bpp 4 --format lvgl --no-compress --no-prefilter --force-fast-kern-format --lv-include lvgl.h
```

`--no-compress` là bắt buộc: firmware build với `CONFIG_LV_USE_FONT_COMPRESSED=n`, font nén sẽ
không hiện gì.

## Dải ký tự

Theo quyết định D10 của contract:

| Dải | Nội dung |
|---|---|
| `0x20-0x7E` | ASCII |
| `0xA0-0xFF` | Latin-1 (`« » · ° é ê ô`…) |
| `0x102-0x103, 0x110-0x111, 0x128-0x129, 0x168-0x169, 0x1A0-0x1A1, 0x1AF-0x1B0` | Ă ă Đ đ Ĩ ĩ Ũ ũ Ơ ơ Ư ư |
| `0x300-0x303, 0x309, 0x323` | Dấu thanh dạng tổ hợp (dự phòng khi văn bản không ở dạng NFC) |
| `0x1EA0-0x1EF9` | Toàn bộ chữ Việt có dấu dựng sẵn |
| `0x2010-0x2027` | Dấu gạch và ngoặc: `– — ‘ ’ “ ” • …` |
| `0x20AB` | `₫` |

Font HUD (Rajdhani) chỉ có `0x20-0x7E` và `°`. Trong firmware, font HUD được nối **fallback**
sang `font_jarvis_vi_16_4`, nên nếu lỡ đưa chữ có dấu vào nhãn HUD thì chữ vẫn hiện (bằng Noto
Sans). Các font Việt lại được nối fallback sang font chữ của assets (`font_noto_sans_common_16_4`)
khi assets nạp xong, để ký tự hiếm (chữ Hán, ký hiệu lạ) từ LLM vẫn hiện được.

Các biểu tượng của HUD (mũi tên, Wi-Fi, cảnh báo) lấy từ font icon Material Symbols có sẵn trong
firmware, vòng tròn/vạch vẽ bằng LVGL, nên không cần thêm dải ký tự ký hiệu.

LVGL không tự ghép dấu: chuỗi `a` + `U+0301` sẽ hiện thành hai glyph rời. Server nên chuẩn hóa
văn bản về **NFC** (`unicodedata.normalize("NFC", s)`) trước khi gửi xuống thiết bị.

## Kiểm tra glyph — `check_glyphs.py`

Firmware build với `CONFIG_LV_USE_FONT_PLACEHOLDER=n`: ký tự thiếu **biến mất im lặng**, không
hiện ô vuông. Vì vậy "không thấy ô vuông" chưa đủ để kết luận font đủ chữ; phải kiểm tra trên dữ
liệu font. `check_glyphs.py` đọc file `.c` giống cách LVGL tra ký tự (bảng cmap → glyph id →
glyph descriptor) và yêu cầu mỗi ký tự có bitmap thật:

- profile `vi` (file `font_jarvis_vi_*`): câu nghiệm thu FW6
  `Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài!`, toàn bộ bảng chữ cái tiếng Việt
  (12 nguyên âm × 6 thanh, chữ hoa và chữ thường, các phụ âm kể cả `đ Đ f j w z`), chữ số, dấu câu
  thường gặp (`– — ‘ ’ “ ” … ₫ « » · • °` và ASCII) và 6 dấu tổ hợp — 248 ký tự;
- profile `hud` (file `font_jarvis_hud_*`): ASCII in được và `°`.

```bash
python tools/fonts/check_glyphs.py firmware/main/boards/jarvis-helmet/font_jarvis_*.c
```

Thiếu ký tự nào thì in ra danh sách (kèm mã U+XXXX) và trả mã lỗi 1. Script cũng in số glyph,
`line_height`, phần glyph vượt khỏi dòng (overshoot) và ước lượng dung lượng flash.

## Xem trước — `preview_font.py`

Vẽ một chuỗi bằng đúng dữ liệu bitmap trong file `.c` ra ảnh PNG (nền và màu chữ của HUD), để
xem chữ có dấu trông thế nào mà không cần phần cứng. Ký tự thiếu được vẽ thành ô đỏ.

```bash
python tools/fonts/preview_font.py firmware/main/boards/jarvis-helmet/font_jarvis_vi_16_4.c \
    -o preview.png --scale 2 --text "Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài!"
```

Không có `--text` thì vẽ câu nghiệm thu, cả bảng chữ cái tiếng Việt và các dấu câu.

## Dung lượng

| Font | Glyph | Bitmap | Ước lượng flash (bitmap + descriptor + cmap) |
|---|---|---|---|
| `font_jarvis_vi_16_4` | 324 | 17,4 KB | ≈ 22 KB (+ bảng kerning) |
| `font_jarvis_vi_14_4` | 324 | 13,6 KB | ≈ 18 KB (+ bảng kerning) |
| `font_jarvis_hud_16_4` | 96 | 3,0 KB | ≈ 4 KB |

Số đo thật từ file map của bản build nằm trong `docs/hud.md` (mục Hiệu năng). Tổng cộng chưa tới
1,5 % slot ứng dụng 0x3F0000 (≈ 3,94 MB).

## Đổi font hoặc thêm ký tự

Sửa danh sách `FONTS` / `VI_RANGES` / `HUD_RANGES` trong `generate_fonts.py` (font mới thì thêm
URL ghim theo commit và SHA-256 vào `SOURCES`), chạy lại script, rồi build firmware. Nếu thêm ký
tự cần thấy trên HUD, thêm cả vào `PROFILES` của `check_glyphs.py` để lần sau script tự kiểm tra.

## Bản quyền

Noto Sans (© 2022 The Noto Project Authors) và Rajdhani (© 2014 Indian Type Foundry) dùng giấy
phép SIL Open Font License 1.1. Dữ liệu bitmap trong các file `.c` là bản chuyển đổi của các font
này và được phân phối theo cùng giấy phép: xem
[`firmware/main/boards/jarvis-helmet/FONTS-LICENSE.md`](../../firmware/main/boards/jarvis-helmet/FONTS-LICENSE.md).

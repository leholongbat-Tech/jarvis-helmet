# Script sinh hình phần cứng

Các hình trong `docs/hardware/` được sinh bằng script, không vẽ tay. Khi đổi chân GPIO, linh kiện hoặc vị trí, sửa script rồi sinh lại để hình luôn khớp với `docs/hardware/wiring.md`.

| Script | Sinh ra |
|---|---|
| `gen_wiring.py` | `docs/hardware/wiring.svg`: sơ đồ đấu dây toàn hệ thống |
| `gen_perfboard.py` | `docs/hardware/perfboard-7x9.svg`: bố trí linh kiện trên lưới lỗ 27×35 |
| `breadboard_layout.py` | `docs/hardware/breadboard.svg` và các bảng trong `breadboard.md` (xem docstring đầu file) |

```bash
python tools/hardware/gen_wiring.py docs/hardware/wiring.svg
python tools/hardware/gen_perfboard.py docs/hardware/perfboard-7x9.svg
python tools/hardware/breadboard_layout.py
```

Chỉ cần Python 3 chuẩn, không cần thư viện ngoài. Sau khi sinh lại:
- mở `docs/hardware/index.html` bằng trình duyệt để xem;
- đối chiếu bảng vị trí (mục 5 của `wiring.md`) và bảng chân GPIO với `firmware/main/boards/jarvis-helmet/config.h`.

# Kế hoạch tổng thể – Jarvis Helmet

## 1. Bối cảnh và mục tiêu

Mục tiêu là làm một mũ giáp kiểu Iron Man có trợ lý ảo chạy chủ yếu trên server AI tại nhà. Trợ lý cần:

1. Nhận dạng tiếng Việt và tiếng Anh **trộn trong cùng một câu** (ví dụ: "Jarvis, open mask rồi bật đèn mắt lên").
2. Thức dậy khi nghe "Jarvis" hoặc "Quản gia".
3. Trả lời bằng giọng nói, theo phong cách một quản gia lịch lãm.
4. Điều khiển 2 servo nâng mặt nạ, bật/tắt đèn mắt và một output dự phòng.
5. Chạy các tác vụ tùy biến (mở mặt nạ, đóng mặt nạ, chế độ chiến đấu…). Thêm tác vụ mới **không cần nạp lại firmware**.
6. Hiển thị trạng thái, câu hội thoại và HUD trên màn 240×240.

Không thuộc phạm vi: thiết kế cơ khí (khung, bản lề, vỏ mũ).

## 2. Các quyết định đã chốt

| Hạng mục | Quyết định |
|---|---|
| Server | Ubuntu, RTX 5090 32GB, RAM 128GB, Docker |
| Mạng | Ưu tiên WiFi nhà. Khi ra ngoài, bắt hotspot điện thoại và kết nối qua **Cloudflare Tunnel (WSS)** |
| LLM | Chạy local mặc định, chuyển sang cloud khi cần. Nhà cung cấp cloud đổi được bằng file cấu hình (LiteLLM) |
| Ngôn ngữ | Trộn Việt–Anh trong cùng một câu |
| Wake word | Chạy trên ESP32. "Jarvis" dùng model ESP-SR có sẵn; "Quản gia" phải làm thêm (xem FW7) |
| Firmware | Dựa trên **xiaozhi-esp32 v2.5.0** (ESP-IDF ≥6.0.1, khuyên dùng 6.1) |
| Server phần mềm | **xiaozhi-esp32-server v0.9.7**, bản Python chế độ tối giản; thay các module ASR, LLM, TTS |
| Servo | Cả 2 servo cùng nâng mặt nạ, chạy đồng bộ, servo phải đảo chiều |
| Đèn mắt | Module LED cosplay chạy pin CR2032. 2 mắt chung 1 kênh, thêm 1 kênh dự phòng |
| Nút bấm | Nút nói (PTT) và nút mở/đóng mặt nạ. Nút mặt nạ vẫn hoạt động khi mất mạng |
| Nguồn | Sạc dự phòng 5V, dòng ra ≥2.4A |
| Màn hình | Trạng thái, câu hội thoại, HUD thông tin, giao diện kiểu Iron Man |
| Tác vụ | Ra lệnh bằng ngôn ngữ tự nhiên. Tác vụ khai báo trong `tasks.yaml`, có thêm các câu lệnh khớp nhanh |
| Ngắt lời | Bằng nút PTT (tin cậy) và bằng wake word (thử nghiệm) |
| Tính cách | Quản gia lịch lãm, gọi người dùng là "thưa ngài" / "Sir", giọng nam trầm |
| Bản vẽ điện | Bảng đấu dây + hình minh họa SVG/HTML |

## 3. Cấu trúc repo (dự kiến)

```
jarvis-helmet/
├─ README.md
├─ docs/
│  ├─ PLAN.md                 ← file này
│  ├─ interfaces.md           ← giao tiếp giữa firmware và server
│  ├─ modules/                ← kế hoạch chi tiết từng module
│  └─ hardware/               ← bảng đấu dây, BOM, hình SVG (HW1–HW4)
├─ firmware/                  ← xiaozhi-esp32 v2.5.0 đưa thẳng vào repo + board jarvis-helmet (FW1–FW7)
│  └─ main/boards/jarvis-helmet/
├─ server/                    ← docker compose + các service (SV1–SV7)
│  ├─ docker-compose.yml
│  ├─ xiaozhi/                ← Dockerfile mỏng dựa trên image chính thức + config + plugin
│  ├─ asr/  litellm/  tts/  cloudflared/
│  └─ tasks/tasks.yaml
└─ tools/
   ├─ wakeword/               ← train microWakeWord "Quản gia" (FW7)
   └─ tests/                  ← bộ câu thử, đo độ trễ (IT1)
```

Firmware được **đưa thẳng mã nguồn upstream vào repo** chứ không dùng submodule:
- Commit đầu tiên là `vendor: xiaozhi-esp32 v2.5.0`, chép nguyên bản upstream.
- Các commit sau chỉ chứa thay đổi của dự án.
- Nhờ vậy `git diff` thấy rõ phần tự viết, và khi nâng cấp upstream chỉ cần áp lại các commit đó.

## 4. Lộ trình

| Giai đoạn | Module | Kết quả nghiệm thu |
|---|---|---|
| **P0** Chuẩn bị | SV1, mua linh kiện (HW1–HW3 BOM) | Môi trường ESP-IDF 6.1 trên Windows. Server chạy được container có GPU |
| **P1** Thử phần cứng trên breadboard | HW2, FW1 | Mic, loa, LCD chạy với firmware gốc; nói chuyện được với server mặc định |
| **P2** Dựng server AI | SV2, SV3, SV4, SV5 | Hội thoại Việt–Anh qua LAN với server nhà |
| **P3** Cơ cấu chấp hành | HW1, HW3, FW2, FW3, FW4 | Ra lệnh bằng giọng nói để mở/đóng mặt nạ, bật đèn |
| **P4** Tác vụ tùy biến | SV6, FW5 | Thêm tác vụ YAML không cần nạp lại firmware; nút bấm chạy offline |
| **P5** Giao diện | FW6 | HUD Iron Man, hiện chữ tiếng Việt có dấu |
| **P6** Lắp ráp | HW4 | Mạch trên PCB 7×9, đi dây trong mũ |
| **P7** Wake word | FW7 | Gọi được bằng cả "Jarvis" và "Quản gia" |
| **P8** Dùng ngoài trời & nghiệm thu | SV7, IT1 | Dùng qua 4G, đạt các chỉ tiêu ở IT1 |

Thứ tự phụ thuộc:

```
SV1 ─► SV2 ─┐
     ├► SV3 ─┼► SV5 ─► SV6 ─► SV7 ──────────┐
     └► SV4 ─┘                               │
HW1 ─► HW2 ─► FW1 ─┬► FW2 ─┐                 ├► IT1
         └─► HW3 ──┤       ├► FW4 ─► FW5 ────┤
                   ├► FW3 ─┘                 │
                   ├► FW6 ───────────────────┤
                   └► FW7 (cần SV4 tạo dữ liệu) ┘
HW1..HW3 + FW1 ─► HW4
```

## 5. Rủi ro chính

| Rủi ro | Ảnh hưởng | Cách xử lý |
|---|---|---|
| Server v0.9.7 không tương thích firmware v2.5.0 | Không kết nối được | Kiểm tra ngay ở P2. Ghim phiên bản cả hai; nếu lỗi thì lùi firmware về bản server hỗ trợ |
| ASR nhận sai câu trộn Việt–Anh | Hiểu sai lệnh | Thêm prompt từ vựng, câu khớp nhanh, so sánh A/B hai model ASR (SV2) |
| Mic và loa cùng nằm trong mũ, gây tiếng vọng | Ngắt lời bằng giọng nói kém | Nút PTT luôn ngắt được. Thử khử vọng phía server |
| Servo kéo dòng làm sụt áp, ESP32 tự reset | Mũ khởi động lại giữa chừng | Tách nhánh nguồn, tụ lớn, servo chạy mượt có gia tốc (HW1, FW2) |
| Wake word "Quản gia" kích nhầm hoặc không nhận | Khó gọi trợ lý | Chỉnh ngưỡng; còn nút PTT dự phòng (FW7) |
| Sạc dự phòng tự tắt khi tải nhỏ | Mất nguồn | Không dùng deep sleep; thử chạy liên tục 2 giờ (IT1) |
| GPU lộ ra internet qua tunnel | Người lạ dùng ké | Bật xác thực token + danh sách MAC thiết bị (SV7) |

## 6. Quy ước

- Tên tool MCP dùng tiền tố `self.` (quy ước của xiaozhi), ví dụ `self.mask.open`.
- Mỗi module có mục **Tiêu chí nghiệm thu**. Module chỉ coi là xong khi đạt hết các tiêu chí đó.
- Phiên bản phần mềm bên ngoài ghi trong module tương ứng. Tên model AI (LLM, ASR) cần kiểm tra lại bản mới nhất lúc triển khai.

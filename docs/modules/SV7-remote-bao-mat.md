# SV7 – Truy cập từ xa & bảo mật

| | |
|---|---|
| **Mục tiêu** | Khi ở ngoài và bắt hotspot 4G, mũ vẫn dùng được server ở nhà qua Cloudflare Tunnel. Người lạ không dùng được server (GPU, chi phí API cloud) |
| **Phụ thuộc** | SV5. Cần một bản vá nhỏ cho firmware (FW1) để gửi kèm header xác thực |
| **Kết quả giao nộp** | `server/cloudflared/config.yml`, hướng dẫn cấu hình Cloudflare Zero Trust, bản vá firmware thêm header, `.env.example` cập nhật |

## 1. Cloudflare Tunnel
- Tên miền đã trỏ về Cloudflare. Tạo tunnel loại do Cloudflare quản lý (`TUNNEL_TOKEN` để trong `.env`); container `cloudflared` chạy trong compose.
- Định tuyến (ingress):
  ```yaml
  ingress:
    - hostname: jarvis.<domain>
      path: ^/xiaozhi/ota/
      service: http://xiaozhi:8003
    - hostname: jarvis.<domain>
      service: http://xiaozhi:8000        # WebSocket /xiaozhi/v1/
    - service: http_status:404
  ```
- Firmware: `CONFIG_OTA_URL = https://jarvis.<domain>/xiaozhi/ota/`. Server: `server.websocket = wss://jarvis.<domain>/xiaozhi/v1/`. Hai địa chỉ này **dùng được cả ở nhà lẫn khi đi ra ngoài**, nên không cần đổi cấu hình theo mạng.
- **Đi thẳng trong LAN khi ở nhà (chỉ làm nếu đo thấy cần):** ở nhà, dữ liệu vẫn đi vòng qua Cloudflare, thêm khoảng 20–60ms. Nếu IT1 đo thấy chênh hơn 100ms: vá API OTA để khi `CF-Connecting-IP` trùng IP WAN của nhà thì trả về `ws://<IP-LAN>:8000/xiaozhi/v1/`.

## 2. Xác thực nhiều lớp
1. **Cloudflare Access service token** (lớp ngoài, quan trọng nhất):
   - Tạo một Access Application cho `jarvis.<domain>`, chính sách chỉ cho phép "Service Auth".
   - Firmware gửi `CF-Access-Client-Id` và `CF-Access-Client-Secret` trong **cả** request OTA **lẫn** khi mở WebSocket.
   - Request không có token bị Cloudflare chặn (403) trước khi đến được server.
   - Bản vá firmware: thêm 2 dòng `SetHeader(...)` ở 2 chỗ upstream đang đặt header: `main/ota.cc` (cạnh `Device-Id`, dòng ~61) và `main/protocols/websocket_protocol.cc` (cạnh `Authorization`, dòng ~102).
   - Giá trị bí mật đặt trong Kconfig `JARVIS_CF_ACCESS_ID` / `JARVIS_CF_ACCESS_SECRET`, nằm trong `sdkconfig` cục bộ (**không commit**). Hoặc nạp vào NVS bằng script cấu hình.
2. **Xác thực của xiaozhi-server** (lớp trong):
   - Bật `server.auth.enabled`.
   - **Không dựa vào danh sách MAC** `allowed_devices`: thiết bị trong danh sách được bỏ qua kiểm tra token, mà header `Device-Id` thì giả được.
   - Dùng cơ chế token của v0.9.7. **Cần đọc mã nguồn** để biết token được tạo và cấp qua OTA như thế nào.
3. **Giới hạn chi phí cloud:** đặt `max_budget` trong LiteLLM (SV3).
4. **Firewall:** máy server không mở cổng nào ra internet; chỉ `cloudflared` kết nối ra ngoài. Cổng 8000 và 8003 chỉ mở trong LAN (SV1).

## 3. Hotspot điện thoại
- ESP32-S3 **chỉ có WiFi 2.4GHz**:
  - iPhone: bật "Tối đa hóa tương thích" (Maximize Compatibility).
  - Android: chọn băng tần 2.4GHz.
- Lưu cả WiFi nhà và hotspot trên thiết bị (FW1).
- Dung lượng 4G: Opus khoảng 16–32 kbps khi đang nói chuyện, nên chỉ tốn vài MB mỗi giờ dùng thật.

## 4. Công việc
- [ ] Tạo tunnel, cấu hình ingress, thêm `cloudflared` vào compose
- [ ] Tạo Access Application + service token, thử bằng `curl` có và không có header
- [ ] Vá firmware thêm 2 header (OTA + WebSocket), cấu hình qua Kconfig
- [ ] Đọc và bật xác thực token của xiaozhi-server
- [ ] Thử thật: ở nhà, chuyển sang 4G, rồi về lại nhà

## 5. Tiêu chí nghiệm thu
1. `curl https://jarvis.<domain>/xiaozhi/ota/` không có header → 403. Có service token → 200.
2. Qua 4G: hội thoại trọn vẹn, từ lúc nói xong đến khi có tiếng trả lời ≤2.5 giây.
3. Đổi mạng (tắt WiFi nhà): mũ tự kết nối lại và dùng được trong vòng 30 giây.
4. Repo không chứa bí mật nào: `git grep` không thấy token hay secret. Có `.env.example` và hướng dẫn nạp bí mật.

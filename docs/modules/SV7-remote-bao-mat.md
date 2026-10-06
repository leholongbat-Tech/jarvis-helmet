# SV7 – Truy cập từ xa & bảo mật

| | |
|---|---|
| **Mục tiêu** | Khi ở ngoài và bắt hotspot 4G, mũ vẫn dùng được server ở nhà qua Cloudflare Tunnel. Người lạ không dùng được server (GPU, chi phí API cloud) |
| **Phụ thuộc** | SV5. Cần một bản vá nhỏ cho firmware (FW1) để gửi kèm header xác thực |
| **Kết quả giao nộp** | `server/cloudflared/` (README hướng dẫn Zero Trust, `ingress.json` + `push_ingress.sh`, `config.yml` cho cách quản lý cục bộ), service `cloudflared` trong compose (profile `tunnel`), header CF trong firmware (`cf_access.cc`), `tools/provision_secrets.py`, `.env.example` |
| **Trạng thái** | Code và hướng dẫn xong. Chưa làm: tạo tunnel/Access application trên tài khoản Cloudflare, nạp service token vào mũ, thử qua 4G |

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
- **Đi thẳng trong LAN khi ở nhà (chỉ làm nếu đo thấy cần):** ở nhà, dữ liệu vẫn đi vòng qua Cloudflare, thêm khoảng 20–60ms. Nếu IT1 đo thấy chênh hơn 100ms: vá API OTA để khi `CF-Connecting-IP` trùng IP WAN của nhà thì trả về `ws://<IP-LAN>:8100/xiaozhi/v1/` (cổng host 8100, D21).
- **Đã làm (D22):** tunnel do Cloudflare quản lý (`TUNNEL_TOKEN`), route đặt trên dashboard hoặc đẩy bằng `server/cloudflared/push_ingress.sh` từ `ingress.json` (route nằm trong git); `config.yml` chỉ dùng cho cách quản lý cục bộ. Hướng dẫn từng bước: `server/cloudflared/README.md`.

## 2. Xác thực nhiều lớp
1. **Cloudflare Access service token** (lớp ngoài, quan trọng nhất):
   - Tạo một Access Application cho `jarvis.<domain>`, chính sách chỉ cho phép "Service Auth".
   - Firmware gửi `CF-Access-Client-Id` và `CF-Access-Client-Secret` trong **cả** request OTA **lẫn** khi mở WebSocket.
   - Request không có token bị Cloudflare chặn trước khi đến được server: thực tế trả **302** (chuyển sang trang đăng nhập) hoặc **401** khi bật "401 Response for Service Auth policies"; 403 chỉ gặp với token sai ở chế độ strict.
   - Đã làm (D13, D31): một "sổ đăng ký" header theo host trong firmware (`main/protocols/extra_headers.cc`), áp cho request OTA, tải firmware và bắt tay WebSocket, **chỉ trên https/wss tới đúng host đã đăng ký**.
   - Bí mật nằm trong partition NVS `jsecrets` (khóa `cf_id`, `cf_secret`), nạp bằng `tools/provision_secrets.py` (đọc từ biến môi trường để không lọt vào lịch sử shell). Kconfig `JARVIS_CF_ACCESS_ID/SECRET` chỉ là phương án dự phòng (giá trị sẽ nằm trong file firmware).
2. **Xác thực của xiaozhi-server** (lớp trong):
   - Bật `server.auth.enabled`.
   - **Không dựa vào danh sách MAC** `allowed_devices`: thiết bị trong danh sách được bỏ qua kiểm tra token, mà header `Device-Id` thì giả được.
   - Cơ chế token của v0.9.7: token HMAC ký bằng `server.auth_key` (cố định trong `.env`), cấp qua API OTA cho **bất kỳ ai gọi được OTA**. Vì vậy Cloudflare Access mới là lớp chặn từ Internet; LAN được coi là tin cậy (D14).
3. **Giới hạn chi phí cloud:** ngân sách tháng `CLOUD_MONTHLY_BUDGET_USD` trong LiteLLM, chi tiêu lưu trong Redis (SV3). Khóa nhà cung cấp chỉ đưa vào container LiteLLM dưới tên riêng, để route pass-through của LiteLLM không thể dùng khóa đó vượt ngân sách.
4. **Firewall:** máy server không mở cổng nào ra internet; chỉ `cloudflared` kết nối ra ngoài. Cổng 8100 (WebSocket) và 8003 (OTA) chỉ mở trong LAN (SV1); tùy chọn `XIAOZHI_BIND_IP` và `server/scripts/firewall_lan.sh` (luật DOCKER-USER chỉ cho mạng LAN).
5. **Cô lập trong máy chủ:** mạng Docker tách theo nhóm (`edge`, `app`, `llm`, `tts`, `asrnet`): xiaozhi và cloudflared không với tới Redis, Ollama hay VieNeu; các container chính chạy với `no-new-privileges`. Bí mật không vào git (`.env`, `jsecrets`), entrypoint từ chối khóa yếu, log che `Authorization`/`CF-Access-*`.
6. **WebSocket MCP cục bộ của mũ** (cổng 8080, D7/D27): chỉ gọi được tool phần cứng/trạng thái; nên cấp token (`tools/provision_secrets.py --generate-mcp-token`). Token đi qua `ws://` không mã hóa trong LAN.

## 3. Hotspot điện thoại
- ESP32-S3 **chỉ có WiFi 2.4GHz**:
  - iPhone: bật "Tối đa hóa tương thích" (Maximize Compatibility).
  - Android: chọn băng tần 2.4GHz.
- Lưu cả WiFi nhà và hotspot trên thiết bị (FW1).
- Dung lượng 4G: Opus khoảng 16–32 kbps khi đang nói chuyện, nên chỉ tốn vài MB mỗi giờ dùng thật.

## 4. Công việc
- [x] Service `cloudflared` trong compose, `ingress.json` + `push_ingress.sh`, hướng dẫn tạo tunnel
- [ ] Tạo tunnel + Access Application + service token trên tài khoản Cloudflare, thử bằng `curl` có và không có header (cần người dùng)
- [x] Firmware gửi header CF (OTA, tải firmware, WebSocket), bí mật trong NVS `jsecrets`
- [x] Bật xác thực token của xiaozhi-server (`auth.enabled`, `auth_key` cố định)
- [ ] Thử thật: ở nhà, chuyển sang 4G, rồi về lại nhà

## 5. Tiêu chí nghiệm thu
1. `curl https://jarvis.<domain>/xiaozhi/ota/` không có header → bị chặn (302, hoặc 401 khi bật tùy chọn ở `server/cloudflared/README.md` mục 2). Có service token → 200.
2. Qua 4G: hội thoại trọn vẹn, từ lúc nói xong đến khi có tiếng trả lời ≤2.5 giây.
3. Đổi mạng (tắt WiFi nhà): mũ tự kết nối lại và dùng được trong vòng 30 giây.
4. Repo không chứa bí mật nào: `git grep` không thấy token hay secret. Có `.env.example` và hướng dẫn nạp bí mật.

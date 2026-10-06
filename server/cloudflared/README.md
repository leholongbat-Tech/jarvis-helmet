# Truy cập từ xa: Cloudflare Tunnel + Cloudflare Access

Khi đi ra ngoài (mũ bắt hotspot 4G), mũ vẫn dùng server ở nhà qua `https://jarvis.<domain>`:

```
Mũ (ESP32-S3) ── HTTPS/WSS + CF-Access-Client-Id/-Secret ──► Cloudflare edge
                                                              │ Access: chỉ cho qua khi có service token
                                                              ▼
                                   tunnel (container cloudflared, chỉ kết nối RA ngoài, cổng 7844)
                                                              │
                         /xiaozhi/ota/...  ──► http://xiaozhi:8003   (OTA, tải firmware)
                         mọi đường dẫn khác ──► http://xiaozhi:8000   (WebSocket /xiaozhi/v1/)
```

- Không mở cổng nào trên router hay máy server: `cloudflared` tự kết nối ra Cloudflare.
- **Cloudflare Access là lớp bảo vệ chính.** API OTA của xiaozhi cấp token WebSocket cho bất kỳ ai gọi
  được nó, nên không có Access thì ai biết tên miền cũng dùng được GPU và tài khoản Claude của bạn.
- Firmware gửi `CF-Access-Client-Id` / `CF-Access-Client-Secret` trong request OTA, khi tải firmware và
  khi mở WebSocket (kèm `User-Agent`), xem `firmware/main/boards/jarvis-helmet/cf_access.cc`.

Có hai cách quản lý tunnel; dự án dùng **cách A (tunnel do Cloudflare quản lý, remote-managed)**:
token trong `server/.env`, các route nằm trên Cloudflare (dashboard hoặc `ingress.json` +
`push_ingress.sh`). Cách B (tunnel cục bộ + `config.yml`) ở mục 9.

| File | Dùng cho |
|---|---|
| `ingress.json` | route của tunnel (cách A), dạng đúng body của API, `${JARVIS_HOSTNAME}` được thay khi đẩy |
| `push_ingress.sh` | đẩy `ingress.json` lên Cloudflare qua API (mặc định chỉ chạy thử) |
| `config.yml` | chỉ cho cách B (tunnel cục bộ) |
| `compose.locally-managed.yml` | override compose cho cách B |

## 1. Chuẩn bị

- Tên miền đã dùng DNS của Cloudflare, có tổ chức Zero Trust (gói Free là đủ).
- Chọn một tên miền con **một cấp**, ví dụ `jarvis.example.com` (tên nhiều cấp như `a.b.example.com`
  cần Advanced Certificate). Trong tài liệu này gọi là `jarvis.<domain>`.
- Tổ chức Zero Trust tạo từ 2026-10-05 trở đi luôn bật "Strict service token authentication"
  (token sai bị trả 401/403 thay vì chuyển hướng); không ảnh hưởng tới cách làm dưới đây.

## 2. Tạo Access application TRƯỚC khi mở route

Làm bước này trước: nếu tạo route mà chưa có Access application thì hostname mở cho cả Internet.

1. **Service token** – Zero Trust › Access controls › Service credentials › Service Tokens ›
   *Create Service Token*: tên `jarvis-helmet`, thời hạn (ví dụ 1 năm) › *Generate token*.
   - Chép ngay **Client ID** (dạng `<32 ký tự hex>.access`, 39 ký tự – phải giữ cả đuôi `.access`) và
     **Client Secret** (dạng `cfast_…` 54 ký tự; token cũ là 64 ký tự hex). Secret chỉ hiện **một lần**.
   - Cất vào trình quản lý mật khẩu. **Không** ghi vào repo, `.env` hay `sdkconfig` được commit.
2. **Application** – Access controls › Applications › *Create new application* › *Self-hosted and
   private* › *Add public hostname*: `jarvis.<domain>`, để trống path (cả host: OTA + WebSocket).
3. **Policy** – hành động **Service Auth** (không phải *Allow*: *Allow* sẽ đòi đăng nhập qua trình duyệt),
   Include › *Service Token* › `jarvis-helmet`.
4. **Additional settings** › bật **"401 Response for Service Auth policies"**: request không có token
   nhận mã 401 thay vì bị chuyển hướng (302) sang trang đăng nhập – dễ kiểm tra, hợp với thiết bị không
   có trình duyệt.
5. (Nên làm) Notifications › *Expiring Access Service Token* để được báo trước khi token hết hạn.

## 3. Tạo tunnel và chạy `cloudflared`

1. Networking › Tunnels › *Create a tunnel* › *Cloudflared* › tên `jarvis` › chép **token** (`eyJ…`).
   Không chạy lệnh cài đặt Cloudflare gợi ý trên máy host: `cloudflared` chạy trong Docker.
2. Trên server, thêm vào `server/.env`:
   ```
   TUNNEL_TOKEN=eyJ...
   COMPOSE_PROFILES=tunnel        # (tùy chọn) để mọi lệnh docker compose đều gồm cloudflared
   ```
3. Chạy và kiểm tra:
   ```bash
   cd /opt/jarvis/server
   docker compose --profile tunnel up -d cloudflared
   docker compose ps cloudflared                       # (healthy) = có kết nối tới Cloudflare
   docker compose logs cloudflared | grep -E "Registered tunnel connection|Updated to new configuration"
   ```
   Dòng cảnh báo `No ingress rules were defined in provided config ...` là bình thường với tunnel
   remote-managed (lỗi đã biết của cloudflared), miễn là có dòng `Updated to new configuration`.

Container dùng image `cloudflare/cloudflared:2026.10.0` (ghim digest), không có shell; healthcheck là
`cloudflared tunnel --metrics 127.0.0.1:20241 ready`. Token được truyền qua biến môi trường
`TUNNEL_TOKEN` (không nằm trên dòng lệnh). Chỉ container này nhận `TUNNEL_TOKEN`.

## 4. Route (public hostname)

Hai route, **theo đúng thứ tự** (route có path đứng trước; luật khớp từ trên xuống, path là regex Go,
không bị cắt hay viết lại):

| # | Hostname | Path | Service |
|---|---|---|---|
| 1 | `jarvis.<domain>` | `^/xiaozhi/ota/` | `http://xiaozhi:8003` |
| 2 | `jarvis.<domain>` | (trống) | `http://xiaozhi:8000` |
| – | (mọi host khác) | | `http_status:404` |

`/mcp/vision/explain` (API vision của xiaozhi, nhận request không cần token) cố ý **không** được trỏ
tới cổng 8003: nó rơi vào route 2 (cổng WebSocket) và không làm được gì.

### Cách A1 – dashboard

Networking › Tunnels › `jarvis` › *Routes* (hoặc *Public Hostname*) › *Add route* › *Published application*:
- route 1: subdomain `jarvis`, domain của bạn, **Path** `^/xiaozhi/ota/`, Service *HTTP* `xiaozhi:8003`;
- route 2: như trên nhưng để trống Path, Service *HTTP* `xiaozhi:8000`.

Dashboard tự tạo bản ghi DNS (CNAME tới `<tunnel-id>.cfargotunnel.com`). Kiểm tra cấu hình thật trong dòng
log `Updated to new configuration` của cloudflared (dashboard có thể lưu path khác chuỗi bạn gõ).

### Cách A2 – API (`push_ingress.sh`, route nằm trong git)

1. Tạo API token: My Profile › API Tokens › *Create Token* › Custom: *Account › Cloudflare Tunnel › Edit*
   (thêm *Zone › DNS › Edit* nếu dùng `--dns`). Nên đặt thời hạn ngắn và xóa sau khi dùng.
2. Đặt biến môi trường **trong phiên shell** (không ghi vào file nào trong repo):
   ```bash
   read -rs CF_API_TOKEN && export CF_API_TOKEN       # dán token, không hiện ra màn hình
   export CF_ACCOUNT_ID=<32 hex>  TUNNEL_ID=<uuid của tunnel>  JARVIS_HOSTNAME=jarvis.example.com
   export CF_ZONE_ID=<32 hex>     # chỉ khi dùng --dns
   ```
3. Chạy thử (in JSON đã thay hostname và lệnh `curl`, token bị che), rồi đẩy thật:
   ```bash
   bash server/cloudflared/push_ingress.sh            # dry-run, kiểm tra JSON
   bash server/cloudflared/push_ingress.sh --apply    # PUT .../cfd_tunnel/$TUNNEL_ID/configurations
   bash server/cloudflared/push_ingress.sh --apply --dns   # + tạo CNAME (khi KHÔNG tạo route qua dashboard)
   bash server/cloudflared/push_ingress.sh --show     # đọc lại cấu hình hiện tại
   ```
   Token được đưa cho `curl` qua một file tạm quyền 0600, không xuất hiện trên dòng lệnh (`ps`).

Kiểm tra luật ngay trên máy (đã chạy thử với cloudflared 2026.10.0 cho cả `config.yml` lẫn
`ingress.json`): `ingress validate` → `OK`; `/xiaozhi/ota/` và `/xiaozhi/ota/download/<file>` khớp
luật #0 (8003), `/xiaozhi/v1/` và `/mcp/vision/explain` khớp luật #1 (8000), host khác → 404.

```bash
docker run --rm -v "$PWD/server/cloudflared:/etc/cloudflared:ro" cloudflare/cloudflared:2026.10.0 \
  tunnel --config /etc/cloudflared/config.yml ingress rule https://jarvis.example.com/xiaozhi/ota/
```

## 5. Cấu hình server và vùng (zone) Cloudflare

1. `server/.env`:
   ```
   PUBLIC_WS_URL=wss://jarvis.<domain>/xiaozhi/v1/
   PUBLIC_OTA_URL=https://jarvis.<domain>/xiaozhi/ota/
   ```
   rồi `docker compose up -d xiaozhi` (cấu hình xiaozhi được dựng lại lúc khởi động). OTA sẽ trả địa
   chỉ `wss://…` cho mũ; địa chỉ này dùng được cả ở nhà lẫn bên ngoài (ở nhà dữ liệu vẫn đi vòng qua
   Cloudflare, thêm khoảng 20–60 ms).
2. Zone `<domain>` trên Cloudflare:
   - *Network › WebSockets*: bật (mặc định đã bật).
   - **Browser Integrity Check** (mặc định bật) chặn client không có User-Agent "chuẩn trình duyệt".
     Firmware có gửi User-Agent, nhưng nên tạo *Rules › Configuration Rules*: `http.host eq
     "jarvis.<domain>"` → Browser Integrity Check **Off**.
   - **Bot Fight Mode**: tắt cho zone này (không thể bỏ qua bằng rule, có thể chặn thiết bị IoT).
   - Không bật "I'm Under Attack" cho hostname này.
3. WebSocket im lặng quá lâu có thể bị Cloudflare đóng (server xiaozhi không gửi ping); firmware tự kết
   nối lại. Đây là giới hạn đã biết.

## 6. Nạp service token vào mũ

Khuyến nghị: NVS (phân vùng `jsecrets`, namespace `secrets`, khóa `cf_id`, `cf_secret`) bằng
`tools/provision_secrets.py` (cần `pip install esp-idf-nvs-partition-gen esptool`):

```bash
# Windows cmd:  set CF_ACCESS_CLIENT_ID=... & set CF_ACCESS_CLIENT_SECRET=...
export CF_ACCESS_CLIENT_ID=0123...abcd.access
read -rs CF_ACCESS_CLIENT_SECRET && export CF_ACCESS_CLIENT_SECRET
python tools/provision_secrets.py --dry-run          # chỉ tạo file .bin (không nạp)
python tools/provision_secrets.py --port COM5        # nạp vào offset 0x1C000
python tools/provision_secrets.py --clear --port COM5   # xóa token khỏi mũ
```

Cách dự phòng: Kconfig `JARVIS_CF_ACCESS_ID` / `JARVIS_CF_ACCESS_SECRET` (nằm trong `sdkconfig` cục bộ,
**không commit**). Địa chỉ OTA của mũ: `CONFIG_OTA_URL=https://jarvis.<domain>/xiaozhi/ota/` hoặc đổi
lúc chạy trong tab nâng cao của trang cấu hình Wi-Fi (NVS `wifi.ota_url`).

## 7. Kiểm tra bằng curl

```bash
H1="CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID"; H2="CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET"
U=https://jarvis.<domain>
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' $U/xiaozhi/ota/                     # 401 (hoặc 302)
curl -sS -o /dev/null -w '%{http_code}\n' -H "$H1" -H "CF-Access-Client-Secret: sai" $U/xiaozhi/ota/  # 401/403 (hoặc 302)
curl -sS -H "$H1" -H "$H2" $U/xiaozhi/ota/                                                    # 200 + văn bản
curl -sS -A "" -o /dev/null -w '%{http_code}\n' -H "$H1" -H "$H2" $U/xiaozhi/ota/             # vẫn 200 (BIC)
# Bắt tay WebSocket: mong đợi "HTTP/1.1 101 Switching Protocols"
curl -i -N --http1.1 -A "" -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==" -H "$H1" -H "$H2" -H "Protocol-Version: 1" \
  -H "Device-Id: aa:bb:cc:dd:ee:ff" -H "Client-Id: 00000000-0000-0000-0000-000000000000" \
  $U/xiaozhi/v1/ --max-time 5
```

| Request | Kết quả mong đợi |
|---|---|
| không có header, đã bật "401 Response for Service Auth policies" | **401** |
| không có header, chưa bật tùy chọn đó | **302** tới `https://<team>.cloudflareaccess.com/cdn-cgi/access/login/...` |
| token sai / hết hạn | 401 hoặc 403 (strict) – hoặc 302 |
| token đúng, `GET /xiaozhi/ota/` | **200**, văn bản `OTA接口运行正常，向设备发送的websocket地址是：wss://jarvis.<domain>/xiaozhi/v1/` (tiếng Trung, không hiện trên mũ) – cho biết địa chỉ WS server đang cấp |
| token đúng, bắt tay WebSocket | **101**; xiaozhi đóng kết nối ngay sau đó vì curl không có token xiaozhi – bình thường |

Kế hoạch SV7 ghi "không header → 403": thực tế Cloudflare trả 302 (mặc định) hoặc 401 (khi bật tùy chọn
ở mục 2.4); 403 chỉ gặp với token sai khi bật strict.

## 8. Xoay vòng và thu hồi

- **Mất mũ / lộ secret**: xóa service token `jarvis-helmet` (mũ bị chặn ngay), tạo token mới, nạp lại
  (mục 6). Nên đổi luôn `XIAOZHI_AUTH_KEY` trong `.env` (mọi token xiaozhi đã cấp mất hiệu lực) rồi
  `docker compose up -d xiaozhi`.
- **Đổi secret định kỳ**: service token › *Rotate secret* (có thời gian ân hạn) › nạp secret mới vào mũ.
- **Token tunnel**: tunnel › *Rotate token* › cập nhật `TUNNEL_TOKEN` › `docker compose --profile tunnel up -d cloudflared`.
- **API token** của `push_ingress.sh`: quyền tối thiểu, thời hạn ngắn, xóa khi xong việc.

## 9. Cách B: tunnel quản lý cục bộ (`config.yml`)

Chỉ dùng khi bắt buộc muốn route nằm trong file YAML. Lưu ý: với tunnel remote-managed, cấu hình từ
Cloudflare **luôn ghi đè** luật trong `config.yml`, nên không gắn file này cho tunnel cách A.

```bash
docker run -it --rm -v "$HOME/.cloudflared:/home/nonroot/.cloudflared" cloudflare/cloudflared:2026.10.0 tunnel login
docker run -it --rm -v "$HOME/.cloudflared:/home/nonroot/.cloudflared" cloudflare/cloudflared:2026.10.0 tunnel create jarvis
docker run -it --rm -v "$HOME/.cloudflared:/home/nonroot/.cloudflared" cloudflare/cloudflared:2026.10.0 tunnel route dns jarvis jarvis.example.com
docker run -it --rm -v "$HOME/.cloudflared:/home/nonroot/.cloudflared" cloudflare/cloudflared:2026.10.0 tunnel token jarvis
```

Giữ `cert.pem` và file credentials `<uuid>.json` **ngoài repo** (thư mục `~/.cloudflared` ở trên).
Đặt kết quả lệnh cuối vào `TUNNEL_TOKEN` trong `server/.env`, sửa `jarvis.example.com` trong `config.yml`,
rồi chạy:

```bash
docker compose -f docker-compose.yml -f cloudflared/compose.locally-managed.yml --profile tunnel up -d cloudflared
```

Không bấm *Migrate* cho tunnel này trên dashboard (tunnel sẽ thành remote-managed và `config.yml` hết tác dụng).
Access application (mục 2) vẫn bắt buộc như cách A.

## 10. Sự cố

| Hiện tượng | Nguyên nhân thường gặp |
|---|---|
| Log mũ `Failed to check version, status code: 302` (hoặc 401) | thiếu/sai header CF: Client ID thiếu `.access`, token hết hạn, policy không phải *Service Auth*, chưa nạp NVS |
| Trả về trang HTML "Just a moment…" / 403 có challenge | Browser Integrity Check hoặc Bot Fight Mode (mục 5.2) |
| 502 / 530 từ Cloudflare | tunnel mất kết nối (`docker compose logs cloudflared`), xiaozhi không chạy, sai service URL trong route |
| OTA chạy nhưng mũ không vào được WebSocket khi ở ngoài | `PUBLIC_WS_URL` vẫn là `ws://<IP LAN>…`: sửa `.env`, `docker compose up -d xiaozhi`, khởi động lại mũ |
| WebSocket rớt sau một lúc im lặng | Cloudflare đóng kết nối rảnh; mũ tự kết nối lại (giới hạn đã biết) |
| `push_ingress.sh` báo `Authentication error` | API token sai quyền (cần *Cloudflare Tunnel › Edit*) hoặc sai `CF_ACCOUNT_ID` |

# Board `jarvis-helmet`

Firmware cho mũ Jarvis (ESP32-S3 **N16R8**: 16 MB flash, 8 MB PSRAM octal) dựa trên xiaozhi-esp32 v2.5.0.
Phần cứng: mic INMP441 + amply MAX98357A (I2S simplex, vào 16 kHz / ra 48 kHz: MAX98357A không nhận LRCLK 24 kHz), LCD ST7789 1.54" 240×240,
2 servo nâng mặt nạ, MOSFET đèn mắt + 1 output dự phòng, nút PTT, nút mặt nạ, cầu phân áp đo 5V.
Bảng chân đầy đủ ở `config.h` (theo `docs/modules/HW2-dau-noi-tin-hieu.md`).

| Chức năng | GPIO |
|---|---|
| Mic WS / SCK / SD | 4 / 5 / 6 |
| Loa DIN / BCLK / LRC | 7 / 15 / 16 |
| LCD MOSI / SCLK / DC / CS / RST / BLK | 47 / 21 / 40 / 41 (bản 7 chân: không có) / **39** / 42 |
| Servo trái S0 / phải S1 | 17 / 18 |
| Đèn mắt / output dự phòng | 8 / 9 |
| Nút PTT / nút mặt nạ / BOOT | 10 / 11 / 0 |
| VSENSE (ADC1_CH0) | 1 |
| LED trạng thái WS2812 | 48 (xem mục LED) |

## 1. Các biến thể build

`config.json` khai báo 4 biến thể, cùng `type` = `jarvis-helmet` (OTA của server nhận diện theo `type`):

| `--name` | Dùng khi | Ghi chú |
|---|---|---|
| `jarvis-helmet` | LCD 8 chân (có CS) – **mặc định** | `CONFIG_LCD_ST7789_240X240=y` |
| `jarvis-helmet-7pin` | LCD 7 chân (không có CS) | `CONFIG_LCD_ST7789_240X240_7PIN=y` (SPI mode 3) |
| `jarvis-helmet-multinet` | Wake word MultiNet "jia wei si" + "guan jia" (phương án B1, FW7) | Build **không** có `--wake-word` |
| `jarvis-helmet-mww` | Thử nghiệm microWakeWord "Quản gia" trên thiết bị (phương án B2, FW7) | `CONFIG_JARVIS_MICRO_WAKE_WORD=y` |

Mọi biến thể dùng chung: bảng phân vùng riêng `partitions_jarvis_16m.csv` và `CONFIG_HTTPD_WS_SUPPORT=y`
(server MCP cục bộ để hiệu chỉnh servo; `CONFIG_JARVIS_LOCAL_MCP_WS` tự bật thêm
`CONFIG_HTTPD_WS_PRE_HANDSHAKE_CB_SUPPORT` để kiểm tra token trước khi mở WebSocket, mục 7.2).

## 2. Build

`scripts/build.py` **tạo lại `sdkconfig` mỗi lần build** (bản cũ chuyển thành `sdkconfig.old`), nên chỉnh trong
`idf.py menuconfig` sẽ mất ở lần build sau. Muốn giữ một option lâu dài thì thêm vào `sdkconfig_append` của
`config.json` (không bao giờ đưa bí mật vào đó, xem mục 7).

### 2.1 Bằng Docker (Windows/Linux, không cần cài ESP-IDF)

Từ thư mục gốc repo (Git Bash trên Windows):

```bash
tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet --language vi-VN --wake-word wn9_jarvis_tts
# LCD 7 chân:
tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet-7pin --language vi-VN --wake-word wn9_jarvis_tts
# MultiNet (không có --wake-word):
tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet-multinet --language vi-VN
# microWakeWord (B2, thử nghiệm; với model placeholder bộ nhận tự tắt):
tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet-mww --language vi-VN --wake-word wn9_jarvis_tts
# Mỗi biến thể nên dùng volume riêng để build tăng dần: thêm FW_VOLUME=jarvis-fw-<tên> trước lệnh
# Lấy file .bin về máy: thêm FW_OUT=out/fw ; build sạch: FW_CLEAN=1
FW_OUT=out/fw tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet --language vi-VN --wake-word wn9_jarvis_tts
```

Script dùng image `espressif/idf:v6.1` và giữ `build/` + `managed_components/` trong volume Docker để lần sau build nhanh.
Docker Desktop không truy cập được cổng COM: nạp từ máy host (mục 3).

### 2.2 ESP-IDF 6.1 cài trực tiếp (EIM)

```powershell
winget install Espressif.EIM-CLI      # hoặc Espressif.EIM (giao diện)
eim install -i v6.1
# Mở "ESP-IDF 6.1 PowerShell" (hoặc chạy export.ps1), rồi:
cd firmware
python scripts/build.py jarvis-helmet --name jarvis-helmet --language vi-VN --wake-word wn9_jarvis_tts
idf.py -p COM5 flash monitor
```

Có thể dùng VS Code + extension ESP-IDF trỏ tới bản 6.1 vừa cài. Tối thiểu ESP-IDF 6.0.1.

## 3. Nạp firmware

- **Lần đầu (và mỗi khi đổi bảng phân vùng) phải nạp đầy đủ qua cáp**: OTA không đổi được bảng phân vùng.
  - Với ESP-IDF: `idf.py -p COM5 flash` (ghi bootloader, bảng phân vùng, app và phân vùng `assets`).
  - Với file từ Docker (`FW_OUT=out/fw`), trong thư mục `out/fw/build`:

    ```bash
    python -m esptool --chip esp32s3 -p COM5 -b 921600 write-flash \
      0x0 bootloader/bootloader.bin 0x8000 partition_table/partition-table.bin \
      0xd000 ota_data_initial.bin 0x20000 xiaozhi.bin 0x800000 generated_assets.bin
    ```
- **Cẩn thận với `merged-binary.bin`**: đây là ảnh liền mạch từ 0x0, các khoảng trống được điền 0xFF, nên nạp nó sẽ **xóa**
  `nvs` (Wi-Fi đã lưu), `helmet` (hiệu chỉnh servo, chuỗi đã lưu) và `jsecrets` (token Cloudflare, token MCP). Chỉ dùng cho mũ mới
  (`write-flash 0x0 merged-binary.bin`). `idf.py flash` và lệnh ở trên không đụng tới 3 phân vùng đó; cập nhật OTA cũng vậy.
- `idf.py app-flash` chỉ ghi app; khi đổi wake word, font hay emoji phải `idf.py flash` để ghi lại `assets`.
- Log khởi động xác nhận đúng board: `Board: UUID=... SKU=jarvis-helmet` và dòng `jarvis-helmet/<phiên bản>` trên màn.
  (`board type` trong log upstream luôn là `wifi`, đó không phải tên board.)

Bảng phân vùng (`partitions_jarvis_16m.csv`, giống `partitions/v2/16m.csv` cộng 2 phân vùng NVS ở khoảng trống 0x10000–0x1FFFF):

| Tên | Offset | Kích thước | Dùng cho |
|---|---|---|---|
| `nvs` | 0x9000 | 16 KB | Wi-Fi, cấu hình upstream |
| `helmet` | 0x10000 | 48 KB | Hiệu chỉnh servo, vị trí mặt nạ, chuỗi bước đã lưu (`HelmetStore`) |
| `jsecrets` | 0x1C000 | 16 KB | Token Cloudflare Access, token của server MCP cục bộ (mục 7) |
| `ota_0` / `ota_1` | 0x20000 / … | 2 × 4 MB | Firmware |
| `assets` | 0x800000 | 8 MB | Font, wake word model, âm thanh |

## 4. Địa chỉ server (OTA URL)

Thiết bị hỏi OTA URL để biết địa chỉ WebSocket. Thứ tự ưu tiên: NVS `wifi/ota_url` (đặt qua cổng cấu hình Wi-Fi) → `CONFIG_OTA_URL`.
**Cả 4 biến thể trong `config.json` đã đặt sẵn `CONFIG_OTA_URL` = `http://192.168.1.27:8003/xiaozhi/ota/`** (server batai trong
LAN, từ 7/10/2026); bỏ dòng đó thì quay về mặc định upstream `https://api.tenclass.net/xiaozhi/ota/`.

- **Cách 1 – cổng cấu hình Wi-Fi (không cần build lại):** vào chế độ cấu hình (mục 5), mở http://192.168.4.1,
  tab **Advanced**, điền OTA URL, lưu. Giá trị này thắng `CONFIG_OTA_URL`.
- **Cách 2 – cố định lúc build:** sửa dòng `CONFIG_OTA_URL=...` trong `sdkconfig_append` của biến thể trong `config.json`
  (ví dụ `https://jarvis.<domain>/xiaozhi/ota/` khi dùng Cloudflare). Không chỉnh bằng menuconfig (build.py tạo lại `sdkconfig`).

**Nạp sẵn Wi-Fi qua cáp (không cần điện thoại):** esp-wifi-connect lưu mạng trong NVS namespace `wifi`, khóa `ssid`/`password`
(mạng thứ hai `ssid1`/`password1`…). Tạo ảnh 16 KB rồi ghi vào phân vùng `nvs` (0x9000). Ảnh này **thay cả phân vùng `nvs`**
(UUID thiết bị tự tạo lại; không đụng `helmet`/`jsecrets`), và file CSV chứa mật khẩu dạng chữ thường nên xóa ngay sau khi nạp:

```bash
pip install esp-idf-nvs-partition-gen esptool
printf 'key,type,encoding,value\nwifi,namespace,,\nssid,data,string,<SSID>\npassword,data,string,<mật khẩu>\n' > wifi.csv
python -m esp_idf_nvs_partition_gen generate wifi.csv wifi_nvs.bin 0x4000
python -m esptool --chip esp32s3 -p COM9 write-flash 0x9000 wifi_nvs.bin && rm wifi.csv wifi_nvs.bin
```

Theo giai đoạn: P1 để mặc định (server xiaozhi.me) để thử phần cứng; P2 `http://<IP-LAN>:8003/xiaozhi/ota/`;
P8 `https://jarvis.<domain>/xiaozhi/ota/` (qua Cloudflare, cần token ở mục 7).

## 5. Wi-Fi nhiều mạng (nhà + hotspot điện thoại)

- Vào chế độ cấu hình: bấm **BOOT** trong lúc khởi động, hoặc tự vào sau 60 giây nếu không kết nối được mạng nào đã lưu.
  Kết nối điện thoại/laptop vào điểm phát `Xiaozhi-XXXX`, mở http://192.168.4.1.
- Lưu được **tối đa 10 mạng**. Mạng mới thêm được đưa lên đầu danh sách; khi đầy, mạng cũ nhất bị xóa.
- **Không có thứ tự ưu tiên**: khi quét, thiết bị kết nối tới mạng đã lưu có **sóng mạnh nhất (RSSI)**. Đang kết nối
  thì không tự chuyển sang mạng khác, kể cả khi về nhà mà hotspot vẫn bật.
- Cổng cấu hình **thử kết nối trước khi lưu**: mỗi mạng phải đang phát khi thêm, và mỗi lần chỉ thêm được một mạng
  (sau khi lưu, thiết bị thoát chế độ cấu hình). Cách làm: thêm Wi-Fi nhà; sau đó bật hotspot điện thoại, vào lại chế độ
  cấu hình và thêm hotspot.
- iPhone: bật **Tối đa hóa tương thích** (Maximize Compatibility). Android: chọn băng tần 2.4 GHz. ESP32-S3 chỉ có 2.4 GHz.
- Mất mạng đang dùng: thiết bị thử lại mạng đó 5 lần, rồi quét lại. Với `CONFIG_JARVIS_WIFI_FAST_RESCAN=y` (mặc định)
  chu kỳ quét là 3 giây, tăng dần tới 30 giây (upstream: 10 → 300 giây), để chuyển sang hotspot nhanh hơn (mục tiêu ≤ 30 giây).
  Board làm việc này bằng cách khởi tạo `WifiManager` trước `WifiBoard::StartNetwork()` với cùng cấu hình upstream cộng
  khoảng quét ngắn hơn (log sẽ có dòng `WifiManager: Already initialized`, là bình thường).

## 6. Nút bấm

| Nút | Thao tác | Hành động | Cần mạng? |
|---|---|---|---|
| **PTT** (GPIO10) | Bấm nhanh (< `CONFIG_JARVIS_PTT_HOLD_MS`, mặc định 400 ms) | Đang nói: **ngắt lời ngay** (gửi abort, xả âm thanh đang phát) rồi nghe tiếp. Trạng thái khác: bắt đầu / kết thúc hội thoại (`ToggleChatState`) | Có |
| | Nhấn giữ (≥ 400 ms) | Giữ để nói: bắt đầu nghe khi đủ 400 ms, thả tay thì gửi câu. Thả tay khi đang kết nối: thiết bị tự dừng nghe ngay khi kết nối xong | Có |
| **Mặt nạ** (GPIO11) | Bấm 1 lần | Mặt nạ đang đóng (hoặc đang chạy về phía đóng) → chạy chuỗi `open_mask`, ngược lại → `close_mask` (bản lưu trong NVS, nếu chưa có thì bản có sẵn trong firmware) | **Không** |
| | Nhấn giữ (≥ 800 ms) | Bật / tắt đèn mắt (`power_up` / `fade_out`) | **Không** |
| | Bấm đúp | Dừng khẩn cấp ngay khi **nhấn lần hai** (không chờ thả tay, nhấn lâu cũng được): hủy chuỗi đang chạy, servo đứng yên tại chỗ | **Không** |
| **BOOT** (GPIO0) | Bấm lúc khởi động | Vào chế độ cấu hình Wi-Fi | – |
| | Bấm khi đang chạy | Giống bấm nhanh nút PTT | Có |

Ghi chú:
- "Bấm 1 lần" được nhận sau khi thả tay: nút PTT khoảng 80 ms, nút BOOT và nút mặt nạ khoảng 180 ms (thư viện chờ xem có
  bấm lần hai không). Nhấn giữ không bao giờ sinh thêm "bấm 1 lần". Bấm rồi nhấn giữ ngay trong khoảng chờ đó (bấm + giữ liền nhau)
  không được tính là nhấn giữ: thả tay, đợi một chút rồi giữ lại.
- Nút mặt nạ chạy trực tiếp trên controller (không qua vòng lặp chính của upstream, vốn bị chặn khi đang kết nối server)
  và không ngắt lời Jarvis.
- Ngắt lời bằng PTT trong phiên "giữ để nói": server trả `tts stop` về trạng thái chờ, board tự mở lại chế độ nghe (tự dừng
  theo VAD). Có thể còn nghe thấy tối đa vài chục ms âm thanh đang trên đường truyền.

## 7. Bí mật Cloudflare Access (khi dùng qua Internet)

Firmware gửi `CF-Access-Client-Id` và `CF-Access-Client-Secret` trong request OTA, tải firmware và handshake WebSocket,
**chỉ tới host của OTA URL và của địa chỉ WebSocket do server trả về, và chỉ khi URL là `https://` / `wss://`**
(kiểm tra lại ở mỗi request: một URL `http://` tới cùng host, ví dụ URL firmware đưa xuống lúc chạy, không bao giờ mang token).
Handshake WebSocket của board này còn gửi thêm `User-Agent` (Browser Integrity Check của Cloudflare chặn request không có UA).

Nạp token vào phân vùng `jsecrets` (cần đã nạp bảng phân vùng của board, mục 3):

```bash
# Git Bash; trên cmd dùng: set CF_ACCESS_CLIENT_ID=...
export CF_ACCESS_CLIENT_ID='<32 hex>.access'
export CF_ACCESS_CLIENT_SECRET='cfast_...'
python tools/provision_secrets.py --port COM5            # tạo ảnh NVS rồi ghi vào 0x1C000
python tools/provision_secrets.py --dry-run --out x.bin  # chỉ tạo file
python tools/provision_secrets.py --clear --port COM5    # xóa token khỏi mũ
```

Cần `pip install esp-idf-nvs-partition-gen esptool` (hoặc chạy trong ESP-IDF shell). Script không in giá trị bí mật; file
`.bin` chứa bí mật ở dạng rõ nên mặc định nằm trong thư mục tạm và bị xóa sau khi nạp. Khởi động lại mũ, log
`CfAccess: Service token loaded from NVS (...)` và `Headers registered for jarvis.<domain>` xác nhận đã nhận token.
Kconfig `CONFIG_JARVIS_CF_ACCESS_ID/SECRET` chỉ là dự phòng khi NVS trống; không nên dùng vì giá trị nằm trong firmware.

## 8. Hiệu chỉnh servo (làm một lần sau khi lắp cơ khí)

Server xiaozhi v0.9.7 không liệt kê tool user-only, nên hiệu chỉnh đi qua **server MCP cục bộ trên mũ**
`ws://<IP-mũ>:8080/ws` (`CONFIG_JARVIS_LOCAL_MCP_WS=y`, chạy sau khi Wi-Fi kết nối; IP xem trên router hoặc log
`LocalMcp: MCP WebSocket server ...`). Trên laptop cùng mạng:

```bash
pip install websockets
python tools/servo_calibrate.py --host 192.168.1.50
```

Quy trình (giá trị mặc định trước hiệu chỉnh là 90° ± 10° để không ép cơ khí):
1. `cal 0 90` rồi `+` / `-` (hoặc `+5`, `- 3`) từng chút cho tới khi nửa trái mặt nạ **đóng khít**. Ghi lại góc.
2. Tiếp tục tới khi **mở hết**. Ghi lại góc.
3. Lặp lại với servo 1 (bên phải, lắp đối xứng nên góc đóng thường lớn hơn góc mở).
4. `save 0 <góc_đóng> <góc_mở>` và `save 1 <góc_đóng> <góc_mở>` (lưu vào NVS, còn sau khi rút điện).
5. Kiểm tra: `close`, `open 70`, `state`, `pos 50`.

`cal` điều khiển servo trực tiếp, **không** bị kẹp trong khoảng đã hiệu chỉnh: tăng từng bước nhỏ, tránh ép cơ khí.
Lệnh khác: `list` (mọi tool, kể cả user-only), `status`, `call <tool> <json>`. Chế độ một lệnh:
`python tools/servo_calibrate.py --host 192.168.1.50 --call self.servo.calibrate '{"servo": 0, "angle": 95}'`.

Server cục bộ **không có xác thực**, nên `tools/call` chỉ nhận các tool phần cứng của mũ (`self.mask.*`,
`self.servo.*`, `self.eyes.*`, `self.output.*`, `self.sequence.*`, `self.display.show_info`) và tool trạng thái
(`self.system.get_status`, `self.get_device_status`). Tool hệ thống của upstream (`self.upgrade_firmware`, `self.reboot`,
`self.assets.set_download_url`, `self.screen.*`, `self.get_system_info`…) bị từ chối với lỗi `-32601` (lệnh `list` vẫn
hiện chúng); frame JSON lồng quá 16 cấp cũng bị từ chối. Người cùng mạng vẫn điều khiển được mặt nạ, đèn, đổi hiệu chỉnh
servo và chuỗi đã lưu: tắt `CONFIG_JARVIS_LOCAL_MCP_WS` khi không cần, nhất là khi dùng hotspot ở nơi công cộng.

### 8.1 Kiểm tra mic và loa (không cần màn hình)

Lúc khởi động, board dò kênh I2S mà mic đang gửi dữ liệu: đọc 64 ms cả hai kênh, có kéo lên chân SD, mất khoảng 0,4 s.
Kết quả ghi vào log `MicProbe:` và vào `self.system.get_status` → `audio.mic_probe`:

| `mic_probe` | Nghĩa | Cần làm |
|---|---|---|
| `left` | Đúng sơ đồ (L/R nối GND) | – |
| `right` | Mic gửi ở kênh phải: L/R đang nối 3V3 hoặc để hở | Nối L/R xuống GND. Firmware tạm dùng kênh phải nên vẫn chạy, nhưng chân để hở có thể đổi kênh giữa các lần bật |
| `no_data` | SD bị kéo lên mà không có dữ liệu: mic không đưa gì ra | Kiểm tra VDD (3V3) và GND của mic, dây SD → GPIO6, SCK → GPIO5, WS → GPIO4 (SCK và WS không bị đảo) |
| `stuck_low` | SD luôn ở mức thấp ở cả hai kênh | Như `no_data` (module INMP441 thường có điện trở kéo xuống 100 kΩ trên SD, nên mic không chạy cũng đọc ra mức thấp), thêm: SD chập GND, mic hỏng |

Phát câu thử rồi đọc mức âm thanh:

```bash
python tools/servo_calibrate.py --host 192.168.1.50 --call self.sequence.run '{"steps": "[{\"sound\": {\"name\": \"xin_chao\"}}]"}'
python tools/servo_calibrate.py --host 192.168.1.50 --call self.system.get_status
```

- `audio.mic_dbfs`: -100 là mic không gửi dữ liệu (toàn số 0); phòng yên tĩnh khoảng -60; nói gần mic từ -30 đến -10.
- `audio.speaker_dbfs`: khoảng -15 đến -20 trong lúc phát câu "Xin chào thế giới", -100 khi không phát. Số này đo trên dữ liệu
  gửi ra amply: firmware không biết loa có kêu thật hay không. Không nghe thấy gì: amply MAX98357A phải có 5V ở chân VIN (trên
  breadboard lấy từ ray RT, cần cắm sạc; cáp USB của máy tính không cấp điện cho ray này), DIN → GPIO7, BCLK → GPIO15,
  LRC → GPIO16, loa nối vào cọc của amply.
- Máy tính chạy Tailscale có nhận subnet route trùng dải LAN (ví dụ 192.168.1.0/24) thì gói tin tới mũ đi vào Tailscale và
  hết giờ: tắt nhận subnet route trên máy đó (`tailscale set --accept-routes=false`), hoặc chạy lệnh từ máy khác trong LAN.

## 9. Khi mất mạng (offline)

Vẫn hoạt động không cần mạng: nút mặt nạ, nhấn giữ bật/tắt đèn mắt, bấm đúp dừng khẩn cấp, các chuỗi đã lưu
(`open_mask`, `close_mask` và chuỗi được server đồng bộ bằng `self.sequence.save`).

Biểu tượng **OFFLINE** trên HUD bật khi:
- mất Wi-Fi đang dùng, hoặc đang ở chế độ cấu hình Wi-Fi;
- lần kết nối server gần nhất thất bại (trạng thái Connecting quay về Idle);
- lúc khởi động, kiểm tra OTA chưa có phản hồi sau 20 giây (lúc đang chờ nhập mã kích hoạt thì không tính, vì server
  đã trả lời); tự tắt ngay khi một lần thử lại sau đó có phản hồi;
- HUD tự nhận lỗi kiểm tra OTA / lỗi kết nối server do upstream báo; lỗi OTA chỉ được xóa khi một lần kiểm tra thật sự có
  phản hồi (upstream bỏ cuộc sau 10 lần thử thì OFFLINE vẫn giữ).

và tắt khi mở được kênh hội thoại (Listening/Speaking). Khi kết nối thất bại, upstream còn phát âm báo lỗi và hiện
"Không thể kết nối đến dịch vụ, vui lòng thử lại sau".

Giới hạn (của upstream, đã ghi nhận, không sửa sâu):
- WebSocket chỉ kết nối khi bắt đầu hội thoại, nên lúc đang chờ không biết server đã tắt; chỉ phát hiện ở lần gọi tiếp theo.
- Trong lúc kết nối server (tối đa ~10 giây chờ server hello), vòng lặp chính của upstream bị chặn: cập nhật màn hình và
  lệnh MCP bị trễ. Nút mặt nạ không bị ảnh hưởng.
- Wake word chỉ bật khi thiết bị tới trạng thái Idle; model wake word nằm trong phân vùng `assets` và chỉ được nạp sau khi
  Wi-Fi kết nối lần đầu kể từ lúc khởi động. Khởi động không có Wi-Fi thì chưa gọi được bằng giọng nói.
- Khởi động mà không có mạng nào đã lưu trong tầm, sau 60 giây thiết bị vào chế độ cấu hình Wi-Fi và ở đó tới khi khởi động lại.

## 10. LED trạng thái

`BUILTIN_LED_GPIO` = GPIO48 (WS2812 trên ESP32-S3-DevKitC-1 v1.0 và đa số bản clone N16R8). **DevKitC-1 v1.1 đặt LED RGB ở
GPIO38**: đổi `BUILTIN_LED_GPIO` trong `config.h` thành `GPIO_NUM_38` nếu dùng bản này (GPIO38 đang trống trong bảng chân).
Màu: xanh dương nhấp nháy = khởi động / cấu hình Wi-Fi, xanh dương sáng = đang kết nối, đỏ = đang nghe (sáng hơn khi có tiếng),
xanh lá = đang nói, tắt = chờ.

## 11. Menu cấu hình "Jarvis Helmet"

`idf.py menuconfig` → Xiaozhi Assistant → Jarvis Helmet (nhớ: build.py tạo lại sdkconfig, nên muốn giữ thì đưa vào `config.json`).

| Option | Mặc định | Ý nghĩa |
|---|---|---|
| `CONFIG_JARVIS_HUD` | y | HUD Iron Man; `n` dùng giao diện gốc `SpiLcdDisplay` để gỡ lỗi |
| `CONFIG_JARVIS_PTT_HOLD_MS` | 400 | Ngưỡng phân biệt bấm nhanh / nhấn giữ PTT (200–1500) |
| `CONFIG_JARVIS_EYES_ON_AT_BOOT` | n | Bật đèn mắt khi khởi động |
| `CONFIG_JARVIS_EYES_FOLLOW_STATE` | n | Đèn mắt theo trạng thái trợ lý (nghe/nói/chờ) |
| `CONFIG_JARVIS_WIFI_FAST_RESCAN` | y | Quét lại Wi-Fi 3 → 30 giây thay vì 10 → 300 giây |
| `CONFIG_JARVIS_WAKE_WORD_BARGE_IN` | n | Cho phép ngắt lời bằng wake word khi đang nói (không có AEC nên dễ tự kích) |
| `CONFIG_JARVIS_LOCAL_MCP_WS` | y | Server MCP cục bộ cho hiệu chỉnh servo |
| `CONFIG_JARVIS_LOCAL_MCP_WS_PORT` | 8080 | Cổng của server trên |
| `CONFIG_JARVIS_CF_ACCESS_ID` / `_SECRET` | "" | Token Cloudflare Access dự phòng (ưu tiên NVS) |
| `CONFIG_JARVIS_MICRO_WAKE_WORD` | n | Bộ nhận microWakeWord trên thiết bị (thử nghiệm) |
| `CONFIG_JARVIS_MWW_CUTOFF_PCT` / `_SLIDING_WINDOW` | 97 / 5 | Ngưỡng và cửa sổ trượt của microWakeWord (chỉ hiện khi bật option trên) |

## 12. Đèn mắt và mặt nạ (chi tiết hành vi)

Hiệu ứng đèn mắt (`self.eyes.effect`, bước `eyes` trong chuỗi, nhấn giữ nút mặt nạ):

| Hiệu ứng | Hành vi |
|---|---|
| `blink` | Nháy nhanh 2 lần trong `min(duration_ms, 600 ms)` (tối thiểu 150 ms), rồi trở lại độ sáng trước đó |
| `flicker` | Chập chờn ngẫu nhiên trong `duration_ms`, kết thúc sáng ổn định ở độ sáng mặc định (NVS `eyes_bright`, chưa đặt thì 100) |
| `power_up` | Tổng thời gian = `duration_ms`: chập chờn tối đa 300 ms (1/4 thời gian) rồi sáng dần 0 → độ sáng mặc định |
| `fade_out` | Tối dần tới tắt trong `duration_ms` |
| `breathe` | Thở lặp lại tới khi có lệnh đèn khác; `duration_ms` là chu kỳ (nên dùng 2500) |

- Lệnh mới hủy hiệu ứng đang chạy. Bấm đúp nút mặt nạ (dừng khẩn cấp) giữ nguyên độ sáng lúc đó (riêng `blink` trở về
  mức nghỉ). Độ sáng được hiệu chỉnh gamma 2.2 nên 10/50/100 % trông đều mắt.
- `CONFIG_JARVIS_EYES_FOLLOW_STATE=y`: đang nghe → 100 %, đang nói → độ sáng theo giọng, chờ → về độ sáng người dùng đã
  chọn. Chỉ tác động khi đèn đang bật và không có hiệu ứng; lệnh tường minh được ưu tiên tới lần đổi trạng thái tiếp theo.

Mặt nạ:
- Hai servo đi cùng lúc theo đường cong mềm (cosine), S1 bắt đầu sau 30 ms để giảm dòng khởi động, cả hai tới đích cùng lúc.
  Lệnh mới tiếp tục từ vị trí hiện tại. Bấm đúp nút mặt nạ dừng servo ngay tại chỗ; `self.sequence.stop` dừng chuỗi
  đang chạy (servo đứng ở vị trí lúc đó).
- Vị trí (`mask_pos`) và góc riêng của từng servo (`s0_pos`, `s1_pos`) được lưu mỗi khi servo dừng hẳn (kể cả sau khi dừng
  khẩn cấp, sau bước `servo` chỉ chạy một servo hay lệnh `cal`). Khởi động lại đặt mỗi servo đúng góc đã lưu của nó (kẹp vào
  khoảng hiệu chỉnh; chưa có thì theo `mask_pos`), S1 bật xung sau S0 30 ms, nên mặt nạ không giật.
- `self.mask.get_state` báo `open` cho mọi vị trí nghỉ lớn hơn 0 (ví dụ mở một nửa: `state=open, position=50`).
- `self.servo.calibrate` di chuyển êm khoảng 6 ms mỗi độ. Lệnh mặt nạ khi một servo đang nằm ngoài khoảng hiệu chỉnh (sau `cal`)
  vẫn đi mềm theo tốc độ đã chọn, tính trên góc thật (tối đa 8 giây). `save` khi servo đang ở ngoài khoảng mới thì servo tự đi
  chậm về giới hạn gần nhất.

Bước `sound` trong chuỗi: tên `popup`, `success`, `exclamation`, `vibration`, `low_battery` (hiệu ứng ngắn ≤ 1,1 s) và
`xin_chao` (câu "Xin chào thế giới" 1,3 s, giọng VieNeu, dùng để thử loa). Mỗi file phải ≤ 18 gói Opus để vào hết hàng đợi
decoder mà không phải chờ, nên các câu nói dài của upstream không dùng được. Bước này **không chờ** âm thanh phát xong và bị bỏ qua khi Jarvis đang nói hoặc loa đang phát
âm khác (để không làm mất tiếng TTS); muốn phát nối tiếp thì thêm bước `delay`.

## 13. Hạn chế đã biết

- Các biến thể dùng chung `type` nên server OTA tự host gửi cùng một file firmware cho mọi biến thể: chỉ giữ một biến thể
  sau khi xác định loại màn hình.
- Ngắt lời bằng giọng nói không tin cậy (mic và loa cùng trong mũ, không có AEC; AEC phía server v0.9.7 chỉ chạy qua MQTT).
  Dùng nút PTT.
- `WifiBoard::StartNetwork()` của upstream được giữ nguyên; nếu upstream đổi cấu hình `WifiManagerConfig`, cập nhật bản sao
  trong `JarvisHelmetBoard::StartNetwork()`.
- Tải `assets` / âm thanh notify qua Cloudflare chưa có header CF Access (upstream dùng client HTTP riêng); chỉ OTA, tải firmware
  và WebSocket có.
- Bảng phân vùng riêng chỉ đổi được bằng cách nạp qua cáp.

Thay đổi trên file upstream (Kconfig, CMake, OTA, WebSocket, Application, AFE audio engine, `idf_component.yml`,
`scripts/build_default_assets.py`) được liệt kê trong `firmware/PATCHES.md`.

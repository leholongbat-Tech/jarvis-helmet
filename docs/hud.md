# HUD Iron Man (FW6) — bố cục, trạng thái, nguồn dữ liệu

Tài liệu này mô tả giao diện HUD mà `JarvisHudDisplay`
(`firmware/main/boards/jarvis-helmet/jarvis_hud_display.{h,cc}`) vẽ trên màn ST7789 240×240,
cách nó lấy dữ liệu, và cách chụp ảnh màn hình thật để nghiệm thu. Kế hoạch gốc:
[`docs/modules/FW6-hud-display.md`](modules/FW6-hud-display.md).

> Ảnh chụp từng trạng thái sẽ được bổ sung khi có phần cứng (mục 9). Các hình dưới đây là sơ
> đồ đúng toạ độ pixel của code.

## 1. Bố cục 240×240

```
 x: 0        46  65     112           178      239
 y ┌──────────────────────────────────────────────┐
 0 │ 14:32   ◔  LAN    5.02V           [OFFLINE] │ thanh trạng thái (22 px)
22 ├──────────────────────────────────────────────┤ vạch mảnh
28 │ MASK                  · · ·             EYES │ panel trái / phải (góc trên,
   │ CLOSED            ·  ╭────╮  ·        ON 80% │ cạnh phần hẹp của vòng tròn)
   │ ▬▬▬▬▬▬           ·  ╱ ╭──╮ ╲  ·        ▬▬▬▬▬ │
   │                 ·  │ │J.A.R.│ │ ·            │ arc reactor 128 px, tâm (120, 88):
   │                 ·  │ │V.I.S.│ │ ·            │  32 vạch sóng âm, 3 vòng lv_arc,
   │                  ·  ╲ ╰──╯ ╱  ·             │  lõi tròn 54 px
   │                   ·  ╰────╯  ·              │
152│               Đang lắng nghe...              │ dòng trạng thái (14 px, giữa)
172├──────────────────────────────────────────────┤ vạch mảnh
173│ » mở mặt nạ rồi bật đèn mắt                  │ câu người dùng (14 px, nhạt)
193│ Đã mở mặt nạ và bật đèn mắt, thưa            │ câu Jarvis trả lời (16 px, sáng,
   │ ngài.                                        │  2 dòng, tự cuộn dọc khi dài)
239└──────────────────────────────────────────────┘
```

| Vùng | Toạ độ (x, y, rộng × cao) | Font |
|---|---|---|
| Đồng hồ `HH:MM` | (4, 3) | `font_jarvis_hud_16_4` (Rajdhani) |
| Icon Wi-Fi | (46, 3), 16 px | `font_material_symbols_16_4` |
| `LAN` / `WAN` | (65, 3) | HUD |
| Điện áp 5V `5.02V` | (112, 3) | HUD |
| Nhãn `OFFLINE` | (178, 3), 58 × 16, nền đỏ | HUD |
| Panel `MASK` | (4, 28), 64 px | HUD + icon mũi tên |
| Panel `EYES` | (172, 28), 64 px, căn phải | HUD |
| Arc reactor | (56, 24), 128 × 128 | — |
| Chữ lõi `J.A.R.` / `V.I.S.` hoặc icon cảm xúc | giữa lõi | HUD / `font_material_symbols_30_4` |
| Dòng trạng thái | (4, 152), 232 × 20 | `font_jarvis_vi_14_4` |
| Câu người dùng | (4, 173), 232 × 20 | `font_jarvis_vi_14_4` |
| Câu trả lời | (4, 193), 232 × 46 (2 dòng) | `font_jarvis_vi_16_4` |
| Thẻ thông tin | (2, 239 − cao), 236 × 68…136 | tiêu đề 16 px, nội dung 14 px |

Kích thước vòng arc reactor:

| Phần | Đường kính | Độ dày | Đoạn sáng mặc định |
|---|---|---|---|
| Vạch sóng âm (32 vạch) | bán kính trong 56, dài 1…7 px | 2 px | — |
| Vòng ngoài | 106 px | 3 px (2…7 khi nghe) | 270° |
| Vòng giữa | 88 px | 5 px | 110° |
| Vòng trong | 70 px | 3 px | 220° |
| Lõi | 54 px, viền 2 px | — | — |

Mỗi vòng gồm một "rãnh" mờ đủ 360° và một đoạn sáng (indicator).

## 2. Màu sắc

| Tên | Mã | Dùng cho |
|---|---|---|
| Nền | `#00070D` | toàn màn hình |
| Cyan (chính) | `#00E5FF` | vòng, chữ trạng thái, đồng hồ, điện áp |
| Cam (phụ) | `#FFB000` | trạng thái "đang nghĩ", mặt nạ đang chạy, tin nhắn hệ thống, thẻ thông tin |
| Đỏ (lỗi) | `#FF3B30` | lỗi / offline, nhãn `OFFLINE`, điện áp rất thấp |
| Cyan tối | `#0E5866` | vạch kẻ, rãnh thanh tiến độ, vạch sóng khi không nói |
| Chữ sáng | `#D6FAFF` | câu trả lời của Jarvis, nội dung thẻ |
| Chữ nhạt | `#5FA4B2` | câu người dùng, nhãn panel, dòng trạng thái lúc chờ |

## 3. Trạng thái và hiệu ứng

HUD có 7 kiểu hiển thị. Chuyển trạng thái được áp dụng ngay trong lời gọi `SetStatus()` (màu,
góc, độ mờ), khung hình kế tiếp của LVGL (≤ 33 ms) đã khác hẳn → dưới 100 ms.

| Kiểu | Khi nào | Vòng | Vạch sóng | Chữ trạng thái |
|---|---|---|---|---|
| **Khởi động** | `Đang khởi tạo...`, `Đang kiểm tra phiên bản mới...`, `Đang đăng nhập...`, `Kích hoạt`, `Chế độ cấu hình Wi-Fi`, nâng cấp OTA | cyan, vòng giữa quay đều 120°/s | ngắn, cyan tối | cyan, nội dung status |
| **Chờ** | `Chờ` (`STANDBY`) hoặc thiết bị Idle | cyan **mờ, thở** (độ sáng lên xuống chu kỳ 3 s), không quay | ngắn, cyan tối | nhạt: `Chờ` |
| **Đang kết nối** | `Đang kết nối...` | cyan, vòng giữa 200°/s, vòng trong quay ngược 140°/s | ngắn | cyan |
| **Đang nghe** | `Đang lắng nghe...` | cyan sáng; **vòng ngoài to/nhỏ theo mức mic**: cung 40°…360° quanh đỉnh, dày 2…7 px; hai vòng trong quay chậm ngược chiều | ngắn | cyan |
| **Đang nghĩ** | sau khi nhận câu người dùng (STT) cho tới khi bắt đầu nói | **cam**, 3 vòng quay ngược chiều nhau (100°/s, −170°/s, 240°/s) | ngắn, cyan tối | cam: `Đang suy nghĩ...` |
| **Đang nói** | `Đang nói...` | cyan; vòng giữa quay chậm, vòng trong sáng theo âm lượng | **nhảy theo âm lượng loa** thành sóng chạy quanh vòng (1…7 px) | cyan |
| **Lỗi / offline** | `Lỗi`, hoặc đang chờ mà offline | **đỏ, nháy chậm** (sáng 0,8 s / mờ 0,6 s) | ngắn, đỏ | đỏ: `Lỗi`, `Mất kết nối Wi-Fi`, `Mất kết nối máy chủ` hoặc `Ngoại tuyến` |

Quy tắc suy ra trạng thái (xiaozhi không có trạng thái "đang nghĩ" hay "offline"):

- **Đang nghĩ**: `SetChatMessage("user", …)` (server gửi kết quả STT) khi đang Nghe/Chờ/Kết nối
  → Đang nghĩ; thoát khi `SetStatus(SPEAKING)`. Quá 20 s không có tiếng trả lời thì quay về
  Nghe (nếu thiết bị vẫn đang nghe) hoặc Chờ.
- **Chờ theo trạng thái thật**: sau lỗi server đầu tiên, upstream không gửi lại `STANDBY` nữa
  (`last_error_message_` không bao giờ được xoá với WebSocket). HUD đọc
  `Application::GetDeviceState()` 5 lần/giây: thiết bị Idle mà HUD còn ở Nghe/Nói/Kết nối →
  chuyển Chờ. Kiểu Lỗi tự về Chờ sau 30 s nếu thiết bị đã Idle.
- **Offline** (nhãn `OFFLINE` + vòng đỏ khi đang chờ) = bất kỳ điều kiện nào:
  - board gọi `SetOffline(true)` (mất Wi-Fi đã từng kết nối, vào chế độ cấu hình Wi-Fi, server
    không trả lời bước kiểm tra OTA);
  - icon mạng là `WIFI_OFF` (không tính lúc khởi động đang quét Wi-Fi);
  - tin nhắn hệ thống bắt đầu bằng `SERVER_NOT_CONNECTED`, `SERVER_TIMEOUT`, `SERVER_ERROR`
    hoặc phần cố định của `CHECK_NEW_VERSION_FAILED`, hay cảm xúc `cloud_off`. Cờ lỗi server
    được xoá khi có phiên làm việc thật (`LISTENING`/`SPEAKING`) hoặc khi OTA thành công
    (`LOADING_PROTOCOL`).
  Đang Nghe/Nghĩ/Nói thì luôn hiện trạng thái đó (phiên đang chạy chứng tỏ có kết nối).
- `ShowNotification(text, ms)` (ví dụ `Đang quét Wi-Fi...`, `Đã kết nối đến <SSID>`) hiện tạm ở
  dòng trạng thái màu cyan; status mới sẽ thay nó (giống upstream).
- Status lạ (alert từ server…) chỉ đổi chữ ở dòng trạng thái, giữ nguyên hiệu ứng.

## 4. Thanh trạng thái

- **Đồng hồ** `HH:MM`, cập nhật mỗi giây. Giờ hệ thống lấy từ phản hồi OTA của server
  (`server.timezone_offset: 7`); chưa có giờ thì hiện `--:--`.
- **Wi-Fi**: icon theo `Board::GetNetworkStateIcon()` (3 mức sóng, `WIFI_OFF` màu đỏ).
  Bên cạnh là `LAN` (URL WebSocket dạng `ws://`, server trong nhà) hoặc `WAN` (`wss://`, qua
  Cloudflare Tunnel), đọc từ NVS `websocket/url`.
- **Điện áp 5V** từ `VbusMonitor`: cyan khi bình thường, cam khi dưới `VBUS_LOW_WARN_MV`
  (4600 mV, `config.h`), đỏ khi dưới 4400 mV; `--.--V` khi chưa đọc được ADC.
- **`OFFLINE`**: nền đỏ chữ tối, ẩn khi có mạng (quy tắc ở mục 3).

Thanh trạng thái làm mới ít nhất mỗi giây (FW6 yêu cầu ≤ 5 s), và ngay lập tức khi có sự kiện
mạng (`UpdateStatusBar(true)`), `SetVbus()` hoặc `SetOffline()`.

## 5. Panel MASK và EYES

| Panel | Hiển thị |
|---|---|
| MASK (trái) | `CLOSED` / `OPEN` / `NN%` (mở dở), thanh vị trí 0…100 %. Đang chạy: mũi tên ▲/▼ (Material Symbols) + `NN%`, màu cam |
| EYES (phải) | `ON NN%` (cyan) hoặc `OFF` (nhạt), thanh độ sáng |

Dữ liệu lấy từ `MaskController::GetState()` và `OutputController::GetState()` qua
`HudDataSource` (10 lần/giây) hoặc từ `SetMaskState()` / `SetEyesState()`.

## 6. Vùng hội thoại

- **Câu người dùng**: `» ` + nội dung, một dòng màu nhạt; dài hơn 232 px thì chạy ngang vòng
  tròn (`LV_LABEL_LONG_MODE_SCROLL_CIRCULAR`, 35 px/s).
- **Câu Jarvis**: mỗi lần server gửi một câu (`tts sentence_start`) thì thay câu cũ, giống phụ
  đề. Hiện 2 dòng; câu dài hơn thì **tự cuộn dọc**: giữ 2,5 s → cuộn xuống 11 px/s (khớp tốc độ
  đọc của TTS) → giữ 2,5 s ở cuối → quay lên nhanh → lặp lại. Không mất chữ nào.
- **Tin nhắn hệ thống** (`role = "system"`, ví dụ chuỗi phiên bản lúc khởi động, mã kích hoạt,
  hướng dẫn cấu hình Wi-Fi, chi tiết lỗi) hiện ở chỗ câu trả lời, **màu cam**.
  Tin nhắn hệ thống rỗng xoá cả hai dòng (đầu/cuối phiên), như upstream.
- `ClearChatMessages()` (mỗi lần về Idle) xoá cả hai dòng.
- Văn bản `null` được coi là rỗng; văn bản quá dài bị cắt đúng ranh giới UTF-8 và thêm `…`
  (câu trả lời 1200 byte, câu người dùng 400 byte, dòng trạng thái 160 byte).
- Assets `hide_subtitle: true` (`SetHideSubtitle(true)`) ẩn cả vùng hội thoại.

## 7. Thẻ thông tin (`self.display.show_info`)

`ShowInfo(title, text, seconds)` (tool MCP, bước `display` của sequence):

- Thẻ nền tối, viền và vạch trái màu cam, **tiêu đề cam** 16 px (dài thì chạy ngang), nội dung
  14 px màu sáng.
- Chiều cao tự co giãn theo nội dung: tối thiểu 68 px (che đúng vùng hội thoại), tối đa 136 px
  (che thêm phần dưới vòng reactor). Nội dung vẫn dài hơn thì tự cuộn dọc như câu trả lời.
- **Trượt lên** từ mép dưới trong 160 ms, **tự ẩn** sau `seconds` giây (1…30) bằng hiệu ứng trượt
  xuống 140 ms. Gọi `ShowInfo` khi thẻ đang hiện thì thay nội dung và tính lại thời gian.

## 8. Cảm xúc (`SetEmotion`)

Không dùng bộ emoji ảnh. Cảm xúc tô màu vòng trong + viền lõi (trừ khi đang Nghĩ hoặc Lỗi, vì
màu khi đó mang ý nghĩa trạng thái) và có thể thay chữ `J.A.R.V.I.S.` bằng một icon nhỏ:

| Cảm xúc | Màu | Icon ở lõi |
|---|---|---|
| happy, laughing, funny, silly, winking, delicious | xanh ngọc `#3DFFB0` | — |
| loving, kissy | hồng `#FF5C93` | ♥ (`favorite`) |
| sad, crying | xanh dương `#4D8BFF` | — |
| angry | đỏ | — |
| surprised, shocked | vàng `#FFE14D` | — |
| thinking | cam | — |
| confused | cam | ? (`help`) |
| embarrassed | cam hồng `#FF8A5C` | — |
| sleepy (chế độ tiết kiệm điện) | xanh tối `#3A6EA5` | — |
| cool, confident, relaxed, neutral, robot_2 | màu trạng thái | — |
| cancel / cloud_off (firmware) | đỏ | `cancel` / `cloud_off` |
| warning / gear (firmware) | cam | `warning` / `settings` |
| link, download, cloud_download (firmware) | màu trạng thái | icon cùng tên |

Tên khác có trong bộ Material Symbols của firmware thì hiện icon đó. Firmware gửi `neutral` ở mỗi
lần đổi trạng thái nên icon tự mất khi bắt đầu phiên mới.

## 9. Nguồn dữ liệu và luồng (thread)

```cpp
HudDataSource source;
source.mask_state    = [this] { return mask_->GetState(); };
source.eyes_state    = [this](bool& on, int& b) { auto s = output_->GetState(); on = s.eyes_on; b = s.eyes_brightness; };
source.vbus_mv       = [this] { return vbus_->GetMillivolts(); };
source.mic_level     = [codec] { return codec->GetInputLevel(); };    // MeteredAudioCodec
source.speaker_level = [codec] { return codec->GetOutputLevel(); };
hud->SetDataSource(std::move(source));
```

- Một `lv_timer` 40 ms (25 FPS) làm hết: đọc mức âm (mỗi khung), MASK/EYES (10 Hz), điện áp
  và thanh trạng thái (1 Hz hoặc khi có cờ), suy luận trạng thái (5 Hz), hiệu ứng reactor, cuộn
  chữ và trượt thẻ. Timer chạy trong `taskLVGL` (ưu tiên 1, core 1), đã giữ khoá LVGL.
- **Các callback của `HudDataSource` chạy trong timer khi đang giữ khoá màn hình**: phải nhanh,
  không chờ, và không được gọi ngược vào `Display`.
- `SetMaskState`, `SetEyesState`, `SetVbus`, `SetOffline` chỉ ghi biến atomic, không khoá →
  gọi được từ callback nút bấm (task `esp_timer`) mà không bị chặn. Timer vẽ thay đổi ở khung kế.
- Mọi hàm `Display` (`SetStatus`, `SetChatMessage`, `ShowInfo`…) tự lấy `DisplayLockGuard`, gọi
  được từ mọi task (task chính, `activation`, `seq_runner`…).
- `UpdateStatusBar()` (task chính, mỗi giây) chỉ đọc icon mạng và URL WebSocket rồi đánh dấu cho
  timer; không gọi base nên không đụng tới widget gốc.
- **Mức âm** (`MeteredAudioCodec`): RMS của mỗi khối PCM (mic 10 ms, loa 60 ms), quy đổi dBFS →
  0…1 (mic −58…−14 dBFS, loa −50…−12 dBFS, loa đo **trước** khi nhân âm lượng), làm mượt
  attack 25 ms / release 300 ms. Mức không đổi quá 250 ms (không còn ghi/đọc audio) thì HUD coi là
  im lặng. Không có callback mức âm thì HUD dùng nhịp cố định.

## 10. Tương thích với code upstream

HUD được vẽ **đè lên** UI gốc: `SetupUI()` gọi `SpiLcdDisplay::SetupUI()` trước (các widget gốc
vẫn tồn tại vì nhiều hàm upstream dùng chúng không kiểm tra null), ẩn chúng, rồi tạo root HUD
đục (opaque) sau cùng → LVGL bắt đầu vẽ từ root này, không tốn công vẽ UI gốc.

| Hàm override | Lý do |
|---|---|
| `SetStatus`, `ShowNotification` (2 bản), `SetEmotion`, `SetChatMessage`, `ClearChatMessages`, `UpdateStatusBar` | lái HUD thay cho UI gốc |
| `SetTheme` | bỏ qua `nullptr`; trước `SetupUI` chỉ lưu theme; sau đó để widget gốc (ẩn) theo theme, HUD giữ bảng màu riêng (`self.screen.set_theme` vẫn chạy được) |
| `SetTextFont` | `Assets::Apply` gọi mỗi lần khởi động với font `common` của assets: giữ hành vi gốc cho theme, đồng thời nối font đó làm fallback sau font tiếng Việt của HUD; các nhãn HUD giữ font riêng |
| `SetHideSubtitle` | ẩn vùng hội thoại HUD (bản gốc sẽ hiện lại `bottom_bar_`) |
| `SetPreviewImage` | bỏ qua (bản gốc sẽ hiện lại `emoji_box_`) |

`CONFIG_JARVIS_HUD=n` dùng lại `SpiLcdDisplay` gốc để gỡ lỗi.

## 11. Hiệu năng

- Một timer 25 FPS (chu kỳ làm tươi LVGL là 33 ms), chỉ gọi hàm LVGL khi giá trị thay đổi
  (góc, độ dày, độ mờ đều được lượng tử hoá và so với giá trị cũ).
- Vạch sóng vẽ bằng một sự kiện `LV_EVENT_DRAW_MAIN` (`lv_draw_line`), chỉ invalidate vùng nhỏ
  quanh các vạch đổi độ dài; không vẽ lại cả màn hình, không xoay/biến đổi ảnh lớn, không dùng
  bóng đổ hay layer trong suốt.
- Lúc chờ (phần lớn thời gian) chỉ có hiệu ứng thở: ~10 lần vẽ lại vùng 106 × 106 mỗi giây.
  Lúc nói/nghĩ: vẽ lại vùng reactor (≤ 128 × 128 ≈ 33 KB, ~7 ms truyền SPI ở 40 MHz) ở 25 FPS.
- `taskLVGL` có ưu tiên thấp nhất (1) nên khi CPU bận, HUD rớt khung chứ không làm vấp audio
  (audio_input 8, audio_output 4, opus 2).
- Font (số đo từ file map của bản build, mục nghiệm thu bên dưới): xem bảng cuối mục.
- Đo trên phần cứng: thêm vào `sdkconfig_append` của bản build thử
  `CONFIG_LV_USE_SYSMON=y`, `CONFIG_LV_USE_PERF_MONITOR=y`, `CONFIG_LV_USE_PERF_MONITOR_LOG_MODE=y`
  → log FPS và % CPU của LVGL mỗi giây; CPU toàn hệ thống: `SystemInfo::PrintTaskCpuUsage()`
  (gọi từ một task debug, hàm này chặn trong lúc đo). Vừa nói chuyện vừa xem log: FPS ≥ 20, audio
  không vấp (FW6 tiêu chí 3).

| Dữ liệu font trong firmware | Flash |
|---|---|
| `font_jarvis_vi_16_4` | _(điền từ build map)_ |
| `font_jarvis_vi_14_4` | _(điền từ build map)_ |
| `font_jarvis_hud_16_4` | _(điền từ build map)_ |

## 12. Chụp ảnh màn hình thật

Firmware có sẵn tool MCP **user-only** `self.screen.snapshot` (`url`, `quality` 1…100): chụp
màn hình bằng `lv_snapshot` (ảnh chính là HUD vì root HUD nằm trên cùng), nén JPEG, rồi `POST`
`multipart/form-data` (trường `file`, `screenshot.jpg`, truyền dạng *chunked*) tới `url`. Server
xiaozhi không liệt kê tool user-only, nên gọi qua MCP cục bộ của mũ
(`ws://<ip-mũ>:8080/ws`, `CONFIG_JARVIS_LOCAL_MCP_WS=y`, contract D7) bằng
`tools/servo_calibrate.py`.

1. Trên laptop cùng mạng LAN, chạy bộ nhận ảnh (lưu thành `shot-<giờ>.jpg`):

   ```python
   # snapshot_server.py - nhận ảnh từ self.screen.snapshot (chunked multipart)
   import http.server, time

   class H(http.server.BaseHTTPRequestHandler):
       def do_POST(self):
           if "chunked" in self.headers.get("Transfer-Encoding", ""):
               body = b""
               while True:
                   size = int(self.rfile.readline().split(b";")[0], 16)
                   if size == 0:
                       self.rfile.readline()
                       break
                   body += self.rfile.read(size)
                   self.rfile.readline()
           else:
               body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
           jpeg = body.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
           name = time.strftime("shot-%H%M%S.jpg")
           open(name, "wb").write(jpeg)
           print("saved", name, len(jpeg), "bytes")
           self.send_response(200)
           self.send_header("Content-Length", "2")
           self.end_headers()
           self.wfile.write(b"ok")

   http.server.HTTPServer(("0.0.0.0", 8765), H).serve_forever()
   ```

2. Chụp (thay IP):

   ```bash
   python tools/servo_calibrate.py --host 192.168.1.50 \
       --call self.screen.snapshot '{"url": "http://192.168.1.20:8765/upload", "quality": 90}'
   ```

3. Lần lượt đưa mũ vào từng trạng thái rồi chụp, lưu vào `docs/img/hud-<trạng thái>.jpg`:

   | Trạng thái | Cách tạo |
   |---|---|
   | Chờ | để yên sau khi khởi động xong |
   | Đang nghe | gọi "Jarvis" hoặc bấm PTT; chụp khi đang nói vào mic |
   | Đang nghĩ | chụp ngay sau khi nói xong câu dài (trước khi Jarvis trả lời) |
   | Đang nói | hỏi một câu có câu trả lời dài (kiểm tra cả tự cuộn) |
   | Lỗi / offline | tắt server hoặc rút Wi-Fi router, chờ nhãn `OFFLINE` |
   | Thẻ thông tin | `--call self.display.show_info '{"title":"COMBAT MODE","text":"Hệ thống vũ khí sẵn sàng","seconds":10}'` |
   | Chữ tiếng Việt | `--call self.display.show_info '{"title":"Kiểm tra","text":"Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài!","seconds":10}'` |

   Việc chụp chạy trên task chính và tốn khoảng một giây: nên chụp lúc không có audio.
   Không cần phần cứng thì có thể xem trước font bằng `tools/fonts/preview_font.py`.

## 13. Đối chiếu tiêu chí nghiệm thu FW6

| Tiêu chí | Cách HUD đáp ứng | Kiểm tra |
|---|---|---|
| 1. Câu có đủ dấu, không mất chữ | font Noto Sans tiếng Việt (D10), `check_glyphs.py` kiểm tra câu nghiệm thu + toàn bộ bảng chữ cái | `python tools/fonts/check_glyphs.py …` (tự động), ảnh thẻ "Chữ tiếng Việt" (phần cứng) |
| 2. 5 trạng thái khác nhau rõ, chuyển < 100 ms | mục 3: màu/chuyển động khác nhau; áp dụng ngay trong `SetStatus` | ảnh từng trạng thái; log thời gian |
| 3. ≥ 20 FPS, audio không vấp | timer 25 FPS, invalidate tối thiểu, LVGL ưu tiên thấp nhất | perf monitor trên phần cứng |
| 4. Câu dài tự cuộn, không mất chữ | cuộn dọc 2 dòng (câu trả lời, thẻ), cuộn ngang (câu người dùng, trạng thái) | hỏi câu có trả lời dài |
| 5. Thanh trạng thái cập nhật ≤ 5 s | làm mới mỗi giây + ngay khi có sự kiện mạng/điện áp/offline | quan sát đồng hồ, rút Wi-Fi |

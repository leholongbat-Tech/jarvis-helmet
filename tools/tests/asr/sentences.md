# Kịch bản thu âm bộ thử ASR (SV2)

Đây là danh sách câu để **đọc khi thu bộ dữ liệu thật bằng mũ** (mic INMP441, xem `README.md`).
Có 63 câu chia 3 nhóm; mã câu cho biết nhóm:

- `M..` = lệnh điều khiển **trộn Việt–Anh** (nhóm `mixed`)
- `V..` = câu **tiếng Việt thuần** (nhóm `vi`)
- `E..` = câu **tiếng Anh thuần** (nhóm `en`)

Cách đọc:
- Đọc tự nhiên như khi ra lệnh cho mũ, **đúng nguyên văn** (kể cả "Jarvis", "Quản gia" ở đầu câu).
- Câu cố tình không có chữ số để việc chấm WER không phụ thuộc cách viết số ("50" hay "năm mươi").
- Nếu đọc nhầm, đọc lại cả câu; công cụ ghép bản ghi (`capture_to_manifest.py`) sẽ chọn bản khớp nhất.
- Các script (`make_synthetic.py`, `capture_to_manifest.py`, `eval.py`) đọc trực tiếp các bảng bên dưới:
  giữ đúng định dạng `| Mã | Câu |` khi thêm câu mới.

## Nhóm 1 – Lệnh trộn Việt–Anh (`mixed`)

| Mã | Câu |
|---|---|
| M01 | Jarvis, open mask rồi bật đèn mắt lên. |
| M02 | Mở mặt nạ đi Jarvis. |
| M03 | Jarvis, close mask giúp tôi. |
| M04 | Bật combat mode ngay. |
| M05 | Jarvis, chuyển sang stealth mode. |
| M06 | Open the mask rồi tắt đèn mắt. |
| M07 | Quản gia, close the mask. |
| M08 | Cho đèn mắt chạy hiệu ứng breathe. |
| M09 | Jarvis, eyes on với độ sáng tối đa. |
| M10 | Tắt eye lights đi. |
| M11 | Activate combat mode cho tôi. |
| M12 | Jarvis, mở mask ra một nửa thôi. |
| M13 | Đóng mask lại rồi bật output. |
| M14 | Jarvis, chạy sequence salute. |
| M15 | Bật party mode lên nào. |
| M16 | Jarvis, status hệ thống thế nào? |
| M17 | Mở mặt nạ rồi power up đèn mắt. |
| M18 | Jarvis, fade out đèn mắt rồi close mask. |
| M19 | Quản gia, open mask nhanh lên. |
| M20 | Cho tôi xem battery level của mũ. |
| M21 | Jarvis, stop sequence ngay. |
| M22 | Bật đèn mắt chế độ flicker. |
| M23 | Jarvis, hỏi expert xem pin lithium sạc bao lâu thì đầy. |
| M24 | Tắt combat mode, về chế độ bình thường. |
| M25 | Jarvis, set mask position ở mức một nửa. |

## Nhóm 2 – Câu tiếng Việt thuần (`vi`)

| Mã | Câu |
|---|---|
| V01 | Jarvis, mấy giờ rồi? |
| V02 | Hôm nay là thứ mấy? |
| V03 | Thời tiết hôm nay thế nào? |
| V04 | Mở mặt nạ. |
| V05 | Đóng mặt nạ lại. |
| V06 | Bật đèn mắt lên. |
| V07 | Tắt đèn mắt đi. |
| V08 | Chuyển sang chế độ chiến đấu. |
| V09 | Bật chế độ tàng hình. |
| V10 | Pin của mũ còn bao nhiêu phần trăm? |
| V11 | Kể cho tôi một câu chuyện cười ngắn. |
| V12 | Thủ đô của nước Úc là thành phố nào? |
| V13 | Một năm có bao nhiêu ngày? |
| V14 | Hãy giới thiệu bản thân đi. |
| V15 | Quản gia, hôm nay tôi có nên mang ô không? |
| V16 | Từ Hà Nội đến Đà Nẵng bao xa? |
| V17 | Cảm ơn nhé, thế là đủ rồi. |
| V18 | Giải thích ngắn gọn trí tuệ nhân tạo là gì. |
| V19 | Nhắc tôi uống nước sau giờ làm. |
| V20 | Tạm biệt Jarvis. |
| V21 | Jarvis, nói cảm ơn các bạn đã đến dự tiệc. |
| V22 | Jarvis, chào tạm biệt và hẹn gặp lại các bạn. |
| V23 | Jarvis, bấm chuông thông báo khi pin yếu. |

## Nhóm 3 – Câu tiếng Anh thuần (`en`)

| Mã | Câu |
|---|---|
| E01 | Jarvis, what's the weather like? |
| E02 | What time is it? |
| E03 | Open the mask. |
| E04 | Close the mask, please. |
| E05 | Turn on the eye lights. |
| E06 | Turn off the eyes. |
| E07 | Activate combat mode. |
| E08 | Switch to stealth mode. |
| E09 | How much battery is left? |
| E10 | Tell me a short joke. |
| E11 | Run the salute sequence. |
| E12 | What is the capital of Japan? |
| E13 | Set the mask to half open. |
| E14 | That's all, thank you Jarvis. |
| E15 | Goodbye. |

## Mẫu âm tính (không cần đọc)

Bộ thử còn cần các mẫu **không có lời nói** (nhóm `noise`, tham chiếu rỗng) để kiểm tra tiêu chí
"30 giây im lặng hoặc tiếng ồn → chuỗi rỗng": `make_synthetic.py` tự tạo im lặng, ồn trắng, ồn hồng,
tiếng ù 50 Hz. Với bộ thật, nên thu thêm vài mẫu bấm PTT mà không nói (trong phòng yên tĩnh và ngoài
đường) rồi đặt `group: "noise"`, `reference: ""` trong manifest.

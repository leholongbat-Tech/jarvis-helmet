# tools/wakeword – Train và đánh giá wake word "Quản gia" (FW7, phương án B2)

Thư mục này chứa toàn bộ pipeline để tạo model **microWakeWord** cho từ "Quản gia", chạy trên
ESP32-S3 song song với WakeNet "Jarvis" (`CONFIG_JARVIS_MICRO_WAKE_WORD`, mặc định tắt).
Phương án A (chỉ "Jarvis") và B1 (MultiNet, không cần train) không cần thư mục này; xem
`docs/wakeword-report.md`.

```
gen_positives.py   tạo 2.000–5.000 câu "quản gia" bằng VieNeu-TTS (+ Piper vi_VN nếu có)
gen_negatives.py   tạo câu âm gần âm (quản lý, gia đình, quán gà, hoàng gia…) từ negatives_vi.txt
record_personal.py ghi 50–100 mẫu giọng của chính bạn bằng mic laptop (giọng Bắc và Nam)
augment.py         trộn nhiễu, tiếng vọng kiểu trong mũ giáp, đổi âm lượng
train.sh           train trên server RTX 5090 bằng image microWakeWord trainer (Docker)
export/tflite_to_c.py  model .tflite + manifest .json -> firmware/.../mww_model_data.c
eval_wakeword.py   đếm số lần nhận/kích nhầm trên file ghi âm, đúng thuật toán của firmware
wwcommon.py, wwsynth.py  thư viện dùng chung
tests/             pytest (không cần GPU, không cần server TTS)
```

Tất cả script dùng Python 3.12. File âm thanh xuất ra luôn là WAV PCM 16 bit, mono, 16 kHz
(đúng định dạng trainer cần) kèm `manifest.jsonl` ghi giọng, tốc độ, cao độ… của từng file.

## 0. Cài đặt

```bash
python -m pip install -r tools/wakeword/requirements.txt          # tạo dữ liệu, ghi âm, export
python -m pip install -r tools/wakeword/requirements-eval.txt     # eval_wakeword.py (Linux/macOS)
python -m pytest tools/wakeword/tests -q                          # kiểm tra nhanh
```

- `sounddevice` chỉ cần cho `record_personal.py`; `piper-tts` chỉ cần khi dùng giọng Piper.
- `pymicro-features` và `ai-edge-litert` (cho `eval_wakeword.py`) không có bản cho Windows.
  Trên Windows chạy trong Docker, từ thư mục gốc repo:

```bash
# Git Bash: MSYS_NO_PATHCONV=1 để Git Bash không đổi /w/... thành đường dẫn Windows, $(pwd -W) = C:/...
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -w /w/tools/wakeword python:3.12-slim bash -c \
  "pip install -q -r requirements-eval.txt && python eval_wakeword.py --model ... --positives ..."
```

```powershell
# PowerShell (một dòng; trong PowerShell "\" không nối dòng)
docker run --rm -v "${PWD}:/w" -w /w/tools/wakeword python:3.12-slim bash -c "pip install -q -r requirements-eval.txt && python eval_wakeword.py --model ... --positives ..."
```

  Không cần `-it` (lệnh không tương tác; trong cửa sổ Git Bash mặc định `-it` còn cần `winpty`). Trên Linux/macOS dùng
  `-v "$PWD:/w"`.

## 1. Dữ liệu dương: `gen_positives.py`

VieNeu-TTS chạy trong compose (`server/`), không mở cổng ra host. Có hai cách gọi:

```bash
# a) Chạy script trong một container nối vào mạng của compose (project "jarvis", mạng `jarvis_tts` của VieNeu), từ thư mục gốc repo
#    (Windows/Git Bash: thêm MSYS_NO_PATHCONV=1 trước docker và dùng "$(pwd -W):/w"):
docker run --rm -v "$PWD:/w" -w /w/tools/wakeword --network jarvis_tts python:3.12-slim bash -c \
  "pip install -q -r requirements.txt && python gen_positives.py \
     --api-url http://vieneu:8000/v1/audio/speech --sampling-params --count 3000 --out /w/out/positives"

# b) Hoặc qua tts-router (đã chuẩn hoá văn bản), khi mở cổng debug 127.0.0.1 bằng docker-compose.debug.yml:
python tools/wakeword/gen_positives.py --api-url http://127.0.0.1:<port>/v1/audio/speech --count 3000
```

- `--voices all` (mặc định) lấy toàn bộ giọng từ `GET /v1/voices` (VieNeu có 25 giọng: Bắc,
  Trung, Nam, nam và nữ). Dùng `--voices "Hải Đăng,Thùy Dung"` để chọn tay. Qua tts-router, danh sách
  có cả giọng Kokoro (tiếng Anh, khi bật profile `english-tts`); script chỉ giữ các mục có
  `"engine": "vieneu"` (router sẽ âm thầm đọc chữ tiếng Việt bằng một giọng VieNeu mặc định nếu nhận tên giọng Kokoro).
- VieNeu **bỏ qua `speed`**, nên tốc độ được đổi bằng resample (`--speed 0.85 1.20`), cộng thêm
  đổi nhịp WSOLA (`--tempo`) và đổi cao độ (`--semitones`) cho một nửa số file.
- `--variants 3`: mỗi lần gọi TTS sinh 3 biến thể, đỡ tốn GPU.
- `--sampling-params`: thay đổi `temperature`/`top_p` của VieNeu mỗi lần gọi (chỉ dùng khi gọi
  thẳng VieNeu; router có thể không nhận các trường này).
- Piper vi_VN (tuỳ chọn): `--piper-voice vi_VN-vivos-x_low.onnx` (65 người nói),
  `--piper-share 0.25`. `--api-url none` = chỉ dùng Piper. Lưu ý: với piper-tts 1.8 các giọng
  vi_VN in cảnh báo "Missing phoneme from id map" (mất dấu thanh) – nghe lại trước khi dùng nhiều.
- Chạy lại cùng tham số + `--seed` sẽ bỏ qua file đã có (tiếp tục khi bị ngắt).
- `--dry-run` tạo tín hiệu giả (không cần server) để thử pipeline.

Câu mặc định chỉ là "quản gia" với các dấu câu khác nhau và vài từ đệm phía trước ("này
quản gia"). **Không** để chữ nào phía sau "gia": trainer giữ phần cuối của mỗi clip.

## 2. Dữ liệu âm: `gen_negatives.py`

```bash
python tools/wakeword/gen_negatives.py --api-url http://vieneu:8000/v1/audio/speech --per-phrase 6
```

Danh sách câu nằm trong `negatives_vi.txt` (quản lý, quản trị, gia đình, quán gà, hoàng gia,
bảng giá, quảng cáo, "Jarvis"…). Thêm câu bạn hay nói gần mũ vào đó. Script từ chối mọi câu chứa
"quản gia". Mỗi câu được đọc đều nhau, giọng chọn ngẫu nhiên.

## 3. Giọng của chính bạn: `record_personal.py`

```bash
python tools/wakeword/record_personal.py --list-devices
python tools/wakeword/record_personal.py --speaker long --count 80 --accents bac,nam
```

Script lần lượt yêu cầu đọc giọng Bắc ("kwản za") và giọng Nam ("wảng da" / "quảng ya"), đổi
khoảng cách, độ to, tốc độ, có lần đội mũ. Mỗi lần ghi 2,5 s, tự cắt khoảng lặng, báo nếu quá
nhỏ hoặc bị rè. Phím: Enter = giữ, `r` = ghi lại, `p` = nghe lại, `s` = bỏ qua, `q` = thoát.
Nên ghi thêm khi đội mũ thật và bằng chính mic INMP441 (bật `CONFIG_USE_AUDIO_DEBUGGER`, xem
`docs/wakeword-report.md`). Đây là dữ liệu quan trọng nhất: trainer nhân trọng số 3 cho thư mục
`personal_samples`.

## 4. Tăng cường: `augment.py`

```bash
python tools/wakeword/augment.py --in out/personal --out out/personal_aug --variants 3 --keep-original
python tools/wakeword/augment.py --in out/positives --out out/positives_aug --noise-dir noise/
```

Thêm tiếng vọng ngắn của vỏ mũ (phản xạ 0,3–1,8 ms, đuôi 40–150 ms, lọc thông thấp), nhiễu nền
(thư mục `--noise-dir`, hoặc nhiễu trắng/hồng/nâu/tiếng quạt tự tạo) ở SNR 3–20 dB, lọc thông
cao như mic MEMS và đổi âm lượng. `--rir-dir` dùng thêm impulse response phòng có sẵn. Trainer
cũng tự trộn nhiễu/phòng riêng, nên không cần tạo quá nhiều biến thể.

## 5. Train trên server 5090: `train.sh`

Chạy **trên server GPU** (cần Docker + NVIDIA). Ví dụ:

```bash
tools/wakeword/train.sh --data-dir /mnt/data1/mww \
  --positives out/positives,out/positives_aug,out/personal,out/personal_aug \
  --negatives out/negatives --samples 30000 --steps 40000
tools/wakeword/train.sh --dry-run ...     # chỉ in kế hoạch
```

- Image: `ghcr.io/tatertotterson/microwakeword:v32-blackwell` (ghim digest trong script). GPU
  không phải RTX 50: `--image ghcr.io/tatertotterson/microwakeword:v32`.
- GPU: `--gpu cdi` (mặc định, đúng cho server batai, không cần khởi động lại dockerd) hoặc
  `--gpu all` (Docker Desktop).
- Script chép clip của ta vào `<data>/personal_samples/` và `<data>/negative_samples/`, xoá cache
  feature cũ (trainer chỉ tính lại khi bộ TTS của nó đổi), rồi chạy trong container:
  `setup_python_venv`, `setup_training_datasets` (lần đầu tải nhiều GB), tải giọng Piper vi_VN,
  `train_wake_word --language=vi --tts-mode=hybrid "quản gia"`. Trainer tự sinh thêm mẫu
  OmniVoice (tiếng Việt: thử nghiệm) + Piper. Không mở cổng nào (không dùng web UI).
- Kết quả: `<data>/output/<thời gian>-quan_gia-…/quan_gia.tflite` + `quan_gia.json`, được chép
  về `out/model/`. Manifest có `probability_cutoff` và `sliding_window_size` đã hiệu chỉnh.
- **Giấy phép:** image Blackwell dùng bản TensorFlow 2.22 không chính thức
  (chivitiH/tensorflow-blackwell-python313) theo **CC BY-NC 4.0** – chỉ dùng phi thương mại.
  Một số bộ dữ liệu âm trainer tải về cũng chỉ cho dùng phi thương mại.

## 6. Đánh giá và chọn ngưỡng: `eval_wakeword.py`

```bash
python tools/wakeword/eval_wakeword.py --model out/model/quan_gia.tflite --manifest out/model/quan_gia.json \
  --positives rec/quangia_quiet rec/quangia_music --negatives rec/podcast_1h.mp3 \
  --cutoffs 90:99:1 --windows 3:8:1 --csv out/eval.csv
```

Script chạy đúng thuật toán của firmware (`micro_wake_word.cc`): feature 40 kênh mỗi 10 ms, lượng
tử hoá int8 theo tham số của model, một lần suy luận mỗi `stride` hàng feature, trung bình trượt
W xác suất so với ngưỡng `pct*255/100`, thời gian chờ 1 s sau mỗi lần nhận. Kết quả là bảng
recall (số clip dương có nhận) và số lần kích nhầm mỗi giờ cho từng cặp (ngưỡng, cửa sổ); dấu
`*` đánh dấu cặp đạt tiêu chí FW7 (≥ 90% và ≤ 1 lần/giờ, đổi bằng `--min-recall`,
`--max-fa-per-hour`). Mỗi clip dương được thêm 1,5 s yên lặng phía trước (như thiết bị đang chạy).

## 7. Đưa model vào firmware

```bash
python tools/wakeword/export/tflite_to_c.py --model out/model/quan_gia.tflite --manifest out/model/quan_gia.json
```

Lệnh ghi đè `firmware/main/boards/jarvis-helmet/mww_model_data.c` (kiểm tra định danh `TFL3`,
`feature_step_size` = 10 ms, và nếu có runtime TFLite thì kiểm tra tensor vào int8 [1, n, 40] /
ra uint8 [1, 1]) và in dòng Kconfig gợi ý, ví dụ:

```
CONFIG_JARVIS_MWW_CUTOFF_PCT=97
CONFIG_JARVIS_MWW_SLIDING_WINDOW=5
```

Rồi build bản `jarvis-helmet-mww` (có `CONFIG_JARVIS_MICRO_WAKE_WORD=y`):

```bash
tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet-mww --language vi-VN --wake-word wn9_jarvis_tts
```

Trong repo chỉ commit file placeholder (kích thước 0): khi đó firmware ghi log
`No model linked in ... disabled` và chạy như phương án A. `--placeholder` tạo lại file đó.
Model đã train **không** commit vào repo nếu chưa rõ giấy phép dữ liệu (xem mục 5).

## 8. Ghi chú

- Firmware nhận diện "Quản gia" bằng đoạn audio sau AFE (giống WakeNet, NS/AGC/AEC đang tắt nên
  gần như là audio thô của mic). Khi đang phát TTS, mặc định wake word bị tắt
  (`CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n`), xem báo cáo.
- Log firmware (`MicroWakeWord`): thời gian suy luận trung bình và xác suất cao nhất mỗi 10 s
  (mức DEBUG: `esp_log_level_set("MicroWakeWord", ESP_LOG_DEBUG)`), dùng để chỉnh ngưỡng trên
  thiết bị.
- Mã C++ trên thiết bị tự viết dựa trên thư viện Apache-2.0 (esp-tflite-micro,
  esp-micro-speech-features); không sao chép mã ESPHome (GPLv3).

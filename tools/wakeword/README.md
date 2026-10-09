# tools/wakeword – Train và đánh giá wake word tiếng Việt (FW7, phương án B2)

Thư mục này chứa toàn bộ pipeline để tạo model **microWakeWord** cho một từ tiếng Việt ("Quản gia"
theo kế hoạch, mặc định của các script; "Hàu ơi" là từ người dùng chọn, mục 9), chạy trên
ESP32-S3 song song với WakeNet "Jarvis" (`CONFIG_JARVIS_MICRO_WAKE_WORD`, mặc định tắt).
Phương án A (chỉ "Jarvis") và B1 (MultiNet, không cần train) không cần thư mục này; xem
`docs/wakeword-report.md`.

```
gen_positives.py   tạo 2.000–5.000 câu "quản gia" bằng VieNeu-TTS (+ Piper vi_VN nếu có)
gen_negatives.py   tạo câu âm gần âm (quản lý, gia đình, quán gà, hoàng gia…) từ negatives_vi.txt
record_personal.py ghi 50–100 mẫu giọng của chính bạn bằng mic laptop (giọng Bắc và Nam)
augment.py         trộn nhiễu, tiếng vọng kiểu trong mũ giáp, đổi âm lượng
ctx_positives.py   mẫu dương có câu lệnh nói liền sau từ đánh thức ("Hàu ơi, mở mặt nạ")
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
- Không có GPU dùng được (batai: VRAM bị chiếm, chưa cài NVIDIA Container Toolkit): `--gpu none
  --cpus 12` chạy bằng CPU, giới hạn container và các luồng TensorFlow/OpenMP ở 12 nhân; dùng image
  `...:v32` (không phải Blackwell).
- Script **thay** nội dung `<data>/personal_samples/` và `<data>/negative_samples/` bằng clip của
  lần chạy này (clip của lần trước, có thể của từ khác, bị xoá), xoá cache feature cũ (trainer chỉ
  tính lại khi bộ TTS của nó đổi), rồi chạy trong container: `setup_python_venv`,
  `setup_training_datasets` (lần đầu tải ~30 GB), tải giọng Piper vi_VN,
  `train_wake_word --language=vi --tts-mode=hybrid "quản gia"`. Trainer tự sinh thêm mẫu
  OmniVoice (tiếng Việt: thử nghiệm) + Piper. Không mở cổng nào (không dùng web UI).
- `--setup-only`: chỉ cài venv, tải dataset và giọng rồi dừng (chạy trước trong lúc tạo dữ liệu).
- Trainer lọc mẫu TTS của nó bằng Silero VAD lấy từ venv của web UI; chạy không UI thì venv đó không
  có, nên script cài `silero-vad` vào venv train và trỏ `REC_VENV_DIR` vào đó (thiếu bước này trainer
  dừng ở bước tạo mẫu).
- **Piper vi_VN:** chỉ `vi_VN-vais1000-medium` giữ được dấu thanh. `vivos-x_low` (65 người nói) và
  `25hours_single-low` (piper 0.2.0) không có ký hiệu thanh điệu trong bảng phoneme, phonemizer của
  piper-tts 1.8 sinh số thanh (`hˈa2w`) rồi bị bỏ âm thầm: "hàu", "hầu", "hậu" đọc như nhau. Script
  chỉ tải vais1000 (một giọng).
- Mọi clip đưa vào (`--positives`, `--negatives`, `--own-tts`) phải là WAV PCM 16 bit: lớp `Clips` của
  microWakeWord đọc header clip đầu tiên bằng module `wave` của Python (không đọc được WAV float, ví
  dụ FLEURS gốc) và trainer chỉ hỏng sau bước tăng cường dài. Script kiểm tra trước khi chép.
- `--own-tts out/x/tts`: dùng clip của ta (ví dụ VieNeu, 25 giọng, đúng thanh) làm bộ mẫu TTS chính
  của trainer thay cho bộ nó tự sinh. Script chép clip thành `work/wake_word_samples/0..N-1.wav`
  kèm manifest mang đúng "chữ ký" mà trainer tự tính (`tts_generate_samples.py --dry-run`), nên
  trainer dùng lại thư mục đó; `--samples` khi đó là số clip.
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
`--max-fa-per-hour`). Mỗi clip dương được thêm 1,5 s yên lặng phía trước và 1 s phía sau
(`--lead-ms`, `--trail-ms`), như thiết bị đang nghe liên tục. Phần phía sau là bắt buộc: trainer đặt
từ đánh thức kết thúc 0,2–0,3 s trước cuối cửa sổ, nên model kích hoạt *sau* âm tiết cuối; cắt clip
ngay sau từ thì không bao giờ nhận (bản đầu của script thiếu phần này và báo recall ~5% cho chính mẫu
train).

## 7. Đưa model vào firmware

```bash
python tools/wakeword/export/tflite_to_c.py --model out/model/quan_gia.tflite --manifest out/model/quan_gia.json
```

Lệnh ghi đè `firmware/main/boards/jarvis-helmet/mww_model_data.c` (kiểm tra định danh `TFL3`,
`feature_step_size` = 10 ms, và nếu có runtime TFLite thì kiểm tra tensor vào int8 [1, n, 40] /
ra uint8 [1, 1]). File cũng chứa chữ mà thiết bị gửi lên server khi nhận được từ
(`g_jarvis_mww_wake_word`, UTF-8): mặc định là `wake_word` trong manifest viết hoa chữ đầu, đổi
bằng `--wake-word "Hàu ơi"`. Server phải có chữ đó (viết liền) trong `wakeup_words` (D15), nếu
không nó sẽ bị coi là một câu hỏi. Lệnh in dòng Kconfig gợi ý, ví dụ:

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

- Firmware nhận diện từ của microWakeWord ("Quản gia", "Hàu ơi"…) bằng đoạn audio sau AFE (giống
  WakeNet, NS/AGC/AEC đang tắt nên gần như là audio thô của mic). Khi đang phát TTS, mặc định wake
  word bị tắt (`CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n`), xem báo cáo.
- Log firmware (`MicroWakeWord`): thời gian suy luận trung bình và xác suất cao nhất mỗi 10 s
  (mức DEBUG: `esp_log_level_set("MicroWakeWord", ESP_LOG_DEBUG)`), dùng để chỉnh ngưỡng trên
  thiết bị.
- Mã C++ trên thiết bị tự viết dựa trên thư viện Apache-2.0 (esp-tflite-micro,
  esp-micro-speech-features); không sao chép mã ESPHome (GPLv3).

## 9. Từ khác: "Hàu ơi" (chỉ giọng TTS, train bằng CPU)

Người dùng chọn "Hàu ơi" làm tên gọi tiếng Việt của mũ (D33). Các script đều nhận `--wake-word`
(mặc định vẫn là "quản gia"); dưới đây là đúng các lệnh đã chạy trên batai (10/2026). Chỉ dùng
giọng TTS, chưa có giọng thật của người dùng.

```bash
# Từ thư mục gốc repo. 1. Dữ liệu bằng VieNeu (chạy CPU trong compose); chạy trong container
# nối mạng jarvis_tts như mục 1.
API=http://vieneu:8000/v1/audio/speech
W=tools/wakeword
python $W/gen_positives.py --wake-word "hàu ơi" --api-url $API --sampling-params --count 3000 \
    --workers 2 --out out/hau_oi/positives
python $W/gen_positives.py --wake-word "hàu ơi" --api-url $API --sampling-params --count 7500 \
    --workers 2 --seed 2 --out out/hau_oi/tts                  # bộ mẫu chính cho trainer
python $W/gen_negatives.py --wake-word "hàu ơi" --phrases $W/negatives_hau_oi.txt --api-url $API \
    --sampling-params --per-phrase 8 --workers 2 --out out/hau_oi/negatives
python $W/gen_negatives.py --wake-word "hàu ơi" --phrases $W/negatives_vi.txt --api-url $API \
    --sampling-params --per-phrase 4 --workers 2 --seed 7 --out out/hau_oi/negatives_vi
python $W/augment.py --in out/hau_oi/positives --out out/hau_oi/positives_aug --variants 1
# "Hàu ơi" + câu lệnh nói liền: câu lệnh bằng VieNeu, rồi ghép
python $W/gen_negatives.py --wake-word "hàu ơi" --phrases $W/commands_vi.txt --api-url $API \
    --sampling-params --per-phrase 10 --workers 2 --seed 21 --out out/hau_oi/commands
python $W/ctx_positives.py --wake out/hau_oi/positives --next out/hau_oi/commands \
    --out out/hau_oi/positives_ctx --count 3000
# Giọng người thật làm mẫu âm: FLEURS vi_vn train + dev (CC BY 4.0; bộ test để đánh giá), tải
# huggingface.co/datasets/google/fleurs data/vi_vn/audio/{train,dev}.tar.gz rồi đổi WAV float
# sang PCM 16 bit (trainer không đọc được WAV float):
python -c "import sys, pathlib; sys.path.insert(0, '$W'); import wwcommon as ww
for f in sorted(pathlib.Path('fleurs').glob('*/*.wav')):
    ww.write_wav(pathlib.Path('out/hau_oi/fleurs_neg') / f'{f.parent.name}_{f.name}', ww.read_audio(f))"

# 2. Train bằng CPU (lần đầu --setup-only tải ~30 GB dataset, chạy song song với bước 1)
IMG=ghcr.io/tatertotterson/microwakeword:v32@sha256:be8a8d6516cdbeee32300f3219402b9b865bd4be66b8cb75acf0b1d9624ded72
tools/wakeword/train.sh --setup-only --data-dir ~/mww --gpu none --cpus 12 --tts-mode piper --image $IMG
tools/wakeword/train.sh --data-dir ~/mww --gpu none --cpus 12 --tts-mode piper --image $IMG \
    --wake-word "hàu ơi" --title "Hàu ơi" --slug hau_oi --own-tts out/hau_oi/tts \
    --positives out/hau_oi/positives,out/hau_oi/positives_aug,out/hau_oi/positives_ctx \
    --negatives out/hau_oi/negatives,out/hau_oi/negatives_vi,out/hau_oi/fleurs_neg --steps 40000

# 3. Firmware: chữ "Hàu ơi" gửi lên server nằm cùng model; biến thể jarvis-helmet-mww đặt
#    ngưỡng 97 / cửa sổ 5 cho v4 (bảng dưới)
python tools/wakeword/export/tflite_to_c.py --model out/model/hau_oi.tflite \
    --manifest out/model/hau_oi.json --wake-word "Hàu ơi"
```

Kết quả (train 35 phút trên 12 nhân CPU, model 135 KB; đánh giá bằng `eval_wakeword.py` trên clip
VieNeu mới `--seed 99` và 3,18 giờ FLEURS vi_vn test – giọng người thật đọc tin tức):

| Model | Ngưỡng / cửa sổ | "Hàu ơi" (300) | "Hàu ơi, <lệnh>" (200) | Kích nhầm/giờ |
|---|---|---|---|---|
| v1: chưa có mẫu nối lệnh, chưa có âm FLEURS | 97 / 5 | 98,0% | ~63% | 3,46 |
| **v2**: thêm `positives_ctx`, `fleurs_neg` và các cụm cuối trong `negatives_hau_oi.txt` | **95 / 5** | **97,7%** | **76,0%** | **0,63** |

**Thử qua mic của board thử (9/10/2026).** Firmware tạm có `CONFIG_USE_AUDIO_DEBUGGER` gửi âm thanh mic thô
(16 kHz) qua UDP về batai; loa màn hình đặt cạnh mic phát clip ở 70% âm lượng, rồi cắt bản thu theo
mốc thời gian (căn bằng tương quan chéo). Đo trên 296 clip "Hàu ơi" thu được: SNR trung vị 7,5 dB
(nền ồn văn phòng −23 dBFS, ban đêm −33 dBFS, mic yên tĩnh lẽ ra ~−60), 60% clip cắt đỉnh 0 dBFS,
kênh loa → mic giảm trầm 8 dB, tăng cao 6–8 dB. v3 thêm 155 clip thu qua mic (13 giọng, ×3) làm mẫu
dương và tiếng ồn mic làm mẫu âm; v4 thêm câu nói thường và mẫu gần âm đã đưa qua kênh mic mô phỏng
(đáp tuyến + tiếng ồn đo được) làm mẫu âm. Đánh giá trên 12 giọng còn lại thu qua mic:

| Model | Ngưỡng / cửa sổ | Qua mic (141 clip, 12 giọng) | Sạch (500 clip) | Kích nhầm/giờ, FLEURS sạch | qua mic mô phỏng |
|---|---|---|---|---|---|
| v2 | 95 / 5 | 30,5% | 89,2% | 0,63 | 0 |
| v3 | 98 / 5 | **73,0%** | 96,0% | 4,71 | 0 |
| **v4 (đang nạp)** | **97 / 5** | 35,5% | **95,4%** | **0,63** | 0 |

v3 học được "màu" của loa màn hình nên nhận qua mic tốt nhưng kích nhầm nhiều trên giọng nói sạch
(giọng của chính người đội mũ ở sát mic gần với điều kiện sạch); v4 giữ kích nhầm thấp và tốt hơn v2 ở
mọi bộ đo. Nút thắt là SNR của mic: ASR của server (Whisper medium) cho WER 11% trên giọng thật sạch
(FLEURS) và 17% trên câu lệnh sạch, nhưng 41–52% khi mô phỏng kênh mic này, và trả về rỗng cho 57%
clip "Hàu ơi" thu qua mic. Cần mic gần miệng (trong mũ), mút chắn gió, giảm khuếch đại mic 6–12 dB để
hết cắt đỉnh, thử khử ồn (NS) của AFE.

Kích nhầm của v1 rơi vào "làm **hòa với**", "**xa hoa**", "**Hoa** Kỳ", "**hoặc** trong"…: model
chưa từng nghe tiếng Việt nói thường làm mẫu âm. Trên board thử (8/10): `Ready for "Hàu ơi"`, mỗi lần
suy luận 7,1 ms (49 lần/s), `minimal sram` 33.468 B, không tự thức trong 76 phút để chờ giữa văn phòng
có người nói chuyện. Model không commit (giấy phép dataset), nằm ở `out/model/` trên máy build.

- **Gần đồng âm:** ASR của server (Whisper medium) nghe phần lớn clip VieNeu "hàu ơi" thành "Hậu ơi",
  một số thành "hầu ơi", "Hào ơi" (Whisper chuộng tên riêng "Hậu"; trên clip 0,5–0,8 s nó còn đoán
  ra "Hawaii", "How are you?"). Vì vậy `negatives_hau_oi.txt` **không** có "hầu ơi", "hậu ơi",
  "hào ơi": làm mẫu âm thì chúng mâu thuẫn với mẫu dương. Đổi lại mũ có thể thức khi nghe các từ đó.
- Không lọc mẫu dương bằng ASR: đoán sai của Whisper trên clip ngắn sẽ loại nhầm nhiều mẫu tốt.
- Server: `wakeup_words` có "hàuơi", "Hàuơi" và cách ASR hay viết nhầm ("hậuơi", "Hậuơi", "Hàoơi");
  `taskslib` bỏ "hàu ơi"/"hào ơi" (không phân biệt dấu) ở đầu câu lệnh; quy tắc 5 của prompt cấm
  LLM nói "hàu ơi".
- Đánh giá độc lập: 300 clip VieNeu mới (`--seed 99`), 200 câu có lệnh phía sau ("Hàu ơi, mở mặt nạ
  đi."), 120 clip gần đồng âm và 3,2 giờ giọng người thật (FLEURS vi_vn test, CC BY 4.0) nối thành
  luồng 30 phút để đếm kích nhầm.

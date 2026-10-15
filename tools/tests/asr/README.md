# Bộ thử ASR (`tools/tests/asr`)

Đánh giá nhận dạng giọng nói Việt–Anh của service `server/asr` (SV2): thu **bộ dữ liệu thật ≥ 50 câu qua mũ**, chấm WER,
độ chính xác từ khóa lệnh, độ trễ, kiểm tra im lặng/tiếng ồn, so sánh A/B các cấu hình và ghi báo cáo `RESULTS.md`.

| File | Vai trò |
|---|---|
| `sentences.md` | Kịch bản đọc: 60 câu (M01–M25 trộn Việt–Anh, V01–V20 tiếng Việt, E01–E15 tiếng Anh). Các script đọc trực tiếp bảng `\| Mã \| Câu \|` |
| `keywords.txt` | Từ khóa lệnh cho tiêu chí "nhận đúng từ khóa ≥ 95 %" (mỗi dòng một từ khóa, cách viết thay thế ngăn bằng `\|`) |
| `capture_to_manifest.py` | Biến dữ liệu do service ASR ghi lại (`ASR_SAVE_DIR`) thành manifest có văn bản đúng |
| `eval.py` | Chạy từng cấu hình trên manifest, chấm điểm, ghi `RESULTS.md` + chi tiết từng câu |
| `configs.example.yaml` | 5 cấu hình A/B của SV2 |
| `make_synthetic.py` | Sinh bộ **smoke tổng hợp** (TTS/SAPI/espeak + nhiễu) để thử công cụ – **không phải bộ dữ liệu thật** |
| `asrlib.py` | Chuẩn hóa văn bản, WER, từ khóa, đọc/ghi manifest (dùng chung) |
| `test_asr_eval.py` | Unit test của các công cụ trên |
| `recordings/` | Dữ liệu âm thanh (`recordings/real/`, `recordings/synthetic/`). **Bị `.gitignore` bỏ qua** (`tools/tests/**/recordings/`): giọng nói không vào repo, tự sao lưu riêng |
| `RESULTS.md` | Báo cáo do `eval.py` ghi ra (mặc định), commit sau khi chạy trên bộ thật |

Quy trình:

```
mũ (PTT) ─► xiaozhi ─► asr (ASR_SAVE_DIR: wav + transcripts.jsonl)
    └─► docker compose cp ─► capture_to_manifest.py ─► rà soát manifest ─► eval.py (A/B) ─► RESULTS.md ─► chốt cấu hình trong server/.env
```

---

## 1. Thu bộ dữ liệu thật qua mũ

Theo hợp đồng triển khai (D19) dữ liệu được ghi **ngay trong service ASR**, không lấy từ `output_dir` của xiaozhi như kế hoạch SV2
ban đầu (xiaozhi chạy `delete_audio: true`, file bị xóa sau mỗi câu). Service lưu **đúng các byte WAV xiaozhi gửi lên** (16 kHz mono,
đã giải mã từ Opus của mũ) cùng một dòng JSON cho mỗi request.

### 1.1 Bật ghi dữ liệu

1. Trong `server/.env`: `ASR_SAVE_DIR=/data/asr-dataset` (thư mục này nằm trong volume `asr_dataset` của compose).
2. **Lỗi quyền đã biết (kiểm tra ngày 2026-10-06):** service ASR chạy bằng user `asr` (uid 10001), còn volume mount ở
   `/data/asr-dataset` được Docker tạo với chủ sở hữu root, nên service không ghi được (log
   `dataset capture disabled (/data/asr-dataset): ... Permission denied`, `/health` có `"capture": false`). Cho tới khi
   compose/Dockerfile được sửa, sau lần `docker compose up -d` đầu tiên chạy một lần:

   ```bash
   docker run --rm --user root -v jarvis_asr_dataset:/data/asr-dataset --entrypoint chown jarvis/asr:dev -R asr:asr /data/asr-dataset
   ```

   (Git Bash trên Windows: đặt `MSYS_NO_PATHCONV=1` trước lệnh.)
3. Tạo lại service: `cd server && docker compose up -d --force-recreate asr`.
4. Kiểm tra: log `docker compose logs asr | grep -i capture` có `dataset capture enabled: /data/asr-dataset` và dòng
   `[ASR] ready ... capture=True`; hoặc (khi chạy kèm `docker-compose.debug.yml`) `curl -s http://127.0.0.1:9000/health` có `"capture": true`.

Khi bật, **mọi câu nói của bất kỳ ai dùng mũ** đều bị ghi lại. Tắt (`ASR_SAVE_DIR=` rồi tạo lại `asr`) ngay khi thu xong.

### 1.2 Đọc kịch bản

- Đội mũ như khi dùng thật (mic INMP441 trong mũ), môi trường thật (phòng ở nhà; nếu được, thêm một lượt có tiếng quạt/ngoài đường).
- Dùng **PTT nhấn giữ** cho từng câu: giữ nút, đọc đúng **một** câu, thả tay. Mỗi lần nhấn giữ = một request ASR = một file.
  (Chế độ tự động theo VAD cũng ghi được, nhưng câu có thể bị cắt đôi nếu ngừng ≥ 700 ms giữa câu.)
- Đọc **đúng nguyên văn và đúng thứ tự** trong `sentences.md` (M01 → M25, V01 → V20, E01 → E15): `capture_to_manifest.py` dựa vào
  thứ tự để chọn câu khi hai câu giống nhau. Đọc nhầm thì đọc lại cả câu (bản đọc lại có mã `M05_2`…, giữ bản tốt khi rà soát).
- Các câu là lệnh thật: mũ sẽ trả lời và có thể mở mặt nạ, bật đèn… Nhấn giữ PTT cho câu tiếp theo sẽ ngắt lời Jarvis.
  Nên tháo nguồn servo hoặc giữ khoảng trống quanh mặt nạ khi thu.
- `E15 Goodbye.` khớp đúng từ kết thúc của server nên phiên bị đóng; các câu chào tạm biệt khác (`V17`, `V20`, `E14`) cũng có thể
  làm phiên kết thúc. Nhấn PTT lại để tiếp tục (bản ghi vẫn được lưu vì ASR chạy trước khi xử lý ý định).
- **Mẫu âm tính** (tiêu chí 4): nhấn giữ PTT mà **không nói**, khoảng 30 giây mỗi lần, vài lần trong phòng yên tĩnh và vài lần nơi
  có tiếng ồn. Các mẫu này thường bị cổng lọc (speech gate) chặn trước Whisper (`dropped: "gate:..."`), xem mục 1.4.
- Mục tiêu: đủ 60 câu (SV2 yêu cầu ≥ 50 câu có lời nói) + ≥ 3 mẫu âm tính. Thêm người đọc thứ hai nếu có thể (một manifest mỗi người
  hoặc gộp chung).

### 1.3 Lấy dữ liệu ra khỏi volume

```bash
cd server
docker compose cp asr:/data/asr-dataset ./asr-dataset
```

Rồi chép thư mục `asr-dataset` về máy chạy đánh giá. Cấu trúc:

```
asr-dataset/
  audio/<YYYYMMDD>/<HHMMSSmmm>_<rid>.wav     đúng byte xiaozhi đã gửi
  transcripts.jsonl                          mỗi request một dòng
```

Mỗi dòng `transcripts.jsonl` có: `ts`, `rid`, `audio` (đường dẫn tương đối), `sid` (session xiaozhi, khớp với dòng log `[LAT]`),
`client_filename`, `client_model`, `text`, `raw_text` (trước bộ lọc ảo giác), `dropped` (`null`, `gate:<lý do>` hoặc lý do lọc),
`notes`, `language`, `language_probability`, `duration_s`, `input` (format, subtype, sample_rate, channels, bytes, truncated), `timing_ms`
(decode, gate, wait, infer, filter, total), `gate`, `options` (language, initial_prompt, hotwords, beam_size, max_new_tokens),
`overrides`, `engine`, `segments`.

### 1.4 Tạo manifest: `capture_to_manifest.py`

```bash
.venv/Scripts/python tools/tests/asr/capture_to_manifest.py --capture ./asr-dataset --out tools/tests/asr/recordings/real
```

(Linux/macOS: `.venv/bin/python`.) Tham số (đã đối chiếu `--help`):

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--capture` (bắt buộc) | – | Bản chép của `ASR_SAVE_DIR` (`transcripts.jsonl` + `audio/`) |
| `--out` | `tools/tests/asr/recordings/real` | Thư mục bộ dữ liệu cần tạo (`manifest.jsonl` + `audio/<id>.wav`) |
| `--sentences` | `sentences.md` | Kịch bản để đối chiếu |
| `--min-score` | 0.5 | Độ giống (0–1) tối thiểu; thấp hơn → `needs_review: true` |
| `--include-gated` | tắt | Giữ cả bản ghi bị speech gate chặn (nhấn PTT không nói, tiếng ồn) |
| `--no-copy` | tắt | Không chép audio, trỏ thẳng tới file gốc (đường dẫn tuyệt đối) |

Cách ghép: văn bản ASR của mỗi bản ghi được so (độ giống ký tự sau chuẩn hóa) với mọi câu trong kịch bản; câu tiếp theo theo thứ tự đọc
thắng khi điểm gần bằng (chênh ≤ 0.1). Khớp được → `reference` là **văn bản kịch bản** (văn bản đúng), không phải kết quả ASR; mã là mã câu
(đọc lại: `M05_2`). Không khớp → mã `U<số thứ tự>`, `needs_review: true`, `reference` tạm là văn bản ASR. Script in ra:

```
62 utterances -> tools/tests/asr/recordings/real/manifest.jsonl
needs review (2): U017, U041
sentences without a recording (0): -
```

### 1.5 Rà soát manifest (bắt buộc trước khi chấm)

`eval.py` chấm **mọi dòng**, không quan tâm `needs_review`. Một dòng `needs_review` mà để nguyên thì `reference` = văn bản ASR → WER 0 %,
làm đẹp kết quả một cách sai. Vì vậy:
- Nghe từng dòng `needs_review: true`: sửa `reference` (đúng nguyên văn kịch bản) và `group`, hoặc xóa dòng.
- Kiểm tra cả các dòng **không** bị đánh dấu nhưng `match_score` thấp (dưới ~0,8) hoặc có mã nhảy cóc/sai thứ tự đọc (ví dụ `M02` rồi
  `V02` trong khi câu kế tiếp phải là `M03`): khi ASR nhận sai nặng, văn bản có thể giống một câu **khác** trong kịch bản hơn câu thật
  đã đọc (ví dụ dựng thử bằng dữ liệu giả: đọc `V04 Mở mặt nạ.` mà ASR ra "hôm nay ăn gì" → bị ghép vào `V02 Hôm nay là thứ mấy?` với điểm 0,58, không bị
  đánh dấu). So `asr_text` với `reference`, nghe lại, sửa `id`/`reference` cho đúng câu đã đọc.
- Bản đọc lại (`M05_2`…): giữ một bản, xóa bản đọc lỗi.
- Mẫu âm tính (thu với `--include-gated`): đặt `"group": "noise"`, `"reference": ""`.
- Câu thiếu bản ghi (`sentences without a recording`): thu bổ sung, hoặc chấp nhận nếu vẫn ≥ 50 câu.

---

## 2. Định dạng manifest

JSON Lines, mỗi dòng một mẫu, đường dẫn audio tương đối so với file manifest (hoặc tuyệt đối). Dòng trống và dòng bắt đầu bằng `#`
bị bỏ qua.

```json
{"id": "M01", "audio": "audio/M01.wav", "reference": "Jarvis, open mask rồi bật đèn mắt lên.", "group": "mixed"}
{"id": "N01", "audio": "audio/N01.wav", "reference": "", "group": "noise", "note": "PTT không nói, phòng yên tĩnh"}
```

| Khóa | Bắt buộc | Ý nghĩa |
|---|---|---|
| `audio` | có | File WAV (hoặc định dạng service ASR đọc được) |
| `reference` | có | Văn bản đúng; mẫu âm tính để `""` |
| `group` | có | `mixed` \| `vi` \| `en` \| `noise` |
| `id` | không | Mã mẫu (thiếu → `#<số dòng>`) |
| `synthetic` | không | `true` = mẫu tổng hợp; `eval.py` sẽ in cảnh báo DỮ LIỆU SMOKE |
| `source`, `note` | không | Ghi chú tự do |
| `match_score`, `asr_text`, `rid`, `captured`, `needs_review` | không | Do `capture_to_manifest.py` thêm vào, `eval.py` không dùng |

---

## 3. Cách chấm điểm

- **Chuẩn hóa** trước khi so: Unicode NFC, chữ thường, bỏ dấu câu/ký hiệu, bỏ dấu nháy, thống nhất vị trí dấu thanh kiểu cũ/mới
  (`hòa` = `hoà`, `thủy` = `thuỷ`). Tiếng Việt tính theo âm tiết (mỗi từ cách nhau bằng khoảng trắng là một token).
  Chữ số ở cả hai phía được đọc thành chữ như bộ TTS của mũ đọc (`1 năm` = `một năm`; nhóm `en`: `50` = `fifty`; số bắt
  đầu bằng 0 đọc từng chữ số), và cách viết liền/rời của kết quả được chỉnh theo câu tham chiếu (`Good bye` = `Goodbye`).
- **WER** = (thay thế + xóa + chèn) / số token tham chiếu, cộng dồn theo nhóm (không phải trung bình WER từng câu).
- **WER thô**: WER của văn bản *trước* bộ lọc ảo giác (`raw_text`, cần `response_format=verbose_json`, server/asr có hỗ trợ).
  WER thô thấp hơn nhiều so với WER → bộ lọc đang xóa nhầm lời nói thật.
- **Từ khóa**: với mỗi câu có lời nói, đếm số lần mỗi từ khóa (`keywords.txt`, so nguyên cụm từ sau chuẩn hóa) xuất hiện trong tham
  chiếu và trong kết quả; trúng = min(hai số đó), thừa = phần dư trong kết quả. Độ chính xác từ khóa = tổng trúng / tổng trong tham chiếu.
- **Âm tính**: mẫu `noise` (hoặc tham chiếu rỗng) mà kết quả có chữ → lỗi bịa câu.
- **Độ trễ**: p50/p90 phía client (gồm HTTP); "p50 câu 2–4 s" = trung vị thời gian xử lý **phía server** (`timing_ms.total`) của các câu
  dài 2–4 giây (tiêu chí 3); RTF p50 = thời gian server / độ dài âm thanh.

---

## 4. Chạy `eval.py`

Service ASR không mở cổng ra host trong cấu hình thường: chạy compose kèm `docker-compose.debug.yml` để có `127.0.0.1:9000`
(đổi cổng bằng `DEBUG_ASR_PORT`).

```bash
# 3 cấu hình mặc định trên một service: <model>+vi, <model>+auto, <model>+vi+prompt
.venv/Scripts/python tools/tests/asr/eval.py --manifest tools/tests/asr/recordings/real/manifest.jsonl \
    --url http://127.0.0.1:9000/v1/audio/transcriptions --label "Bộ thật 2026-10-xx, người đọc A, phòng khách"

# Đủ 5 cấu hình A/B (mục 5)
.venv/Scripts/python tools/tests/asr/eval.py --manifest tools/tests/asr/recordings/real/manifest.jsonl \
    --configs tools/tests/asr/configs.example.yaml --out tools/tests/asr/RESULTS.md

# Thử nhanh 5 câu nhóm mixed
.venv/Scripts/python tools/tests/asr/eval.py --manifest tools/tests/asr/recordings/real/manifest.jsonl --groups mixed --limit 5
```

Tham số (đã đối chiếu `--help`):

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--manifest` (bắt buộc) | – | Manifest JSONL |
| `--configs` | – | File YAML/JSON danh sách cấu hình; không có → 3 cấu hình mặc định trên `--url` |
| `--url` | `http://127.0.0.1:9000/v1/audio/transcriptions` | Endpoint cho cấu hình mặc định |
| `--prompt` | "Hàu ơi, Jarvis, open mask, close mask, mặt nạ, đèn mắt, combat mode, stealth mode, thưa ngài." | Câu mồi của cấu hình `+prompt` mặc định |
| `--keywords` | `keywords.txt` | Danh sách từ khóa |
| `--out` | `tools/tests/asr/RESULTS.md` | Báo cáo Markdown |
| `--details-dir` | `<thư mục manifest>/eval_details` | Chi tiết từng câu, mỗi cấu hình một file `<tên>.jsonl` |
| `--label` | – | Dòng tiêu đề in đầu báo cáo (người đọc, ngày, môi trường) |
| `--groups` | – | Chỉ giữ các nhóm, ví dụ `mixed,vi` |
| `--limit` | 0 | Chỉ N mẫu đầu |
| `--timeout` | 120 | Timeout mỗi request (giây) |
| `--no-warmup` | tắt | Không gửi một request làm nóng (không tính) trước mỗi cấu hình |

Mỗi cấu hình trong file YAML/JSON có các khóa: `name`, `url`, `language` (`vi`, `en`, `auto`, `vi,en` = tự nhận nhưng chỉ trong danh sách),
`prompt` (`none` = tắt câu mồi `ASR_INITIAL_PROMPT` của service; bỏ khóa = dùng mặc định của service), `model` (mặc định `whisper-large-v3`,
service không dùng), `verbose` (mặc định `true` → `verbose_json` để có WER thô, thời gian server, `dropped`), `note`. Chuỗi `{model}`
trong `name` được thay bằng tên model lấy từ `/health`.

Trong lúc chạy, script in `/health` của mỗi cấu hình, từng câu (`mã nhóm WER độ_trễ kết_quả`) và một dòng tổng; cuối cùng ghi báo cáo
và các file chi tiết. Mã thoát 0 (kể cả khi chưa đạt tiêu chí), 2 khi manifest rỗng.

Lưu ý: xiaozhi chỉ gửi `file` + `model`, nên khi chạy thật **mọi tùy chọn đến từ biến môi trường** của service (`ASR_LANGUAGE`,
`ASR_INITIAL_PROMPT`…). `language`/`prompt` theo từng request chỉ dùng để so sánh A/B trên cùng một service.

---

## 5. So sánh A/B

Các cấu hình SV2 (`configs.example.yaml`):

| Tên | Service | Cách tạo |
|---|---|---|
| `large-v3+vi` | service `asr` chính (`:9000`) | Ghi đè theo request: `language: vi`, `prompt: none` |
| `large-v3+auto` | service `asr` chính | `language: auto`, `prompt: none` |
| `large-v3+vi+prompt` | service `asr` chính | `language: vi` + câu mồi từ vựng |
| `qwen3-asr+vi` | container ASR thứ hai (`:9001`) | `ASR_ENGINE=qwen3_vllm`, proxy tới sidecar vLLM `asr-qwen` (profile `asr-qwen`) |
| `phowhisper+vi` | container ASR thứ ba (`:9002`) | `ASR_MODEL=phowhisper-large` (bản CT2 `qbsmlabs/PhoWhisper-large`, revision ghim sẵn) |

Ba cấu hình đầu cần service chính chạy `ASR_ENGINE=faster_whisper`, `ASR_MODEL=large-v3` (mặc định trong `server/.env.example`).
Engine và model không đổi được theo request, nên Qwen3-ASR và PhoWhisper chạy bằng **container `jarvis/asr:dev` riêng** trên mạng của
compose (`jarvis_asrnet`, mạng của `asr` và `asr-qwen`), publish trên `127.0.0.1` các cổng khớp với `configs.example.yaml`:

```bash
cd server
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d      # asr chính trên 127.0.0.1:9000

# Qwen3-ASR-1.7B: sidecar vLLM (~6,4 GB VRAM, --gpu-memory-utilization 0.2) + container proxy (không chạy model, không cần GPU)
docker compose --profile asr-qwen up -d asr-qwen
docker run -d --name jarvis-asr-ab-qwen --network jarvis_asrnet -p 127.0.0.1:9001:9000 \
    -e ASR_ENGINE=qwen3_vllm -e ASR_VLLM_URL=http://asr-qwen:8000 \
    -e ASR_MODEL=Qwen/Qwen3-ASR-1.7B -e ASR_LANGUAGE=vi jarvis/asr:dev

# PhoWhisper-large (CT2): cần GPU; cache model trong volume riêng mount ở /cache/hf (thư mục có sẵn của image, user asr ghi được)
docker run -d --name jarvis-asr-ab-pho --network jarvis_asrnet -p 127.0.0.1:9002:9000 --gpus all \
    -v jarvis-asr-ab-hf:/cache/hf \
    -e ASR_MODEL=phowhisper-large -e ASR_MODEL_REVISION= -e ASR_LANGUAGE=vi -e ASR_COMPUTE_TYPE=float16 jarvis/asr:dev
```

- Trên server dùng CDI: thay `--gpus all` bằng `--device nvidia.com/gpu=all`. Git Bash: thêm `MSYS_NO_PATHCONV=1`.
- **Không** truyền `--env-file server/.env` cho container PhoWhisper: `ASR_MODEL_REVISION` trong `.env` là revision của large-v3; nếu bị
  kế thừa, service sẽ cố tải PhoWhisper ở commit của large-v3 và báo `cannot load qbsmlabs/PhoWhisper-large@edaa852...`.
  `-e ASR_MODEL_REVISION=` (rỗng) dùng revision ghim sẵn trong code.
- Chờ cả ba `/health` trả `"status": "ok"` (lần đầu tải model mất vài phút). Container proxy Qwen chờ vLLM tối đa `ASR_VLLM_WAIT_S`
  (300 s) rồi **thoát** (`vLLM not reachable ... Application startup failed`): khi `asr-qwen` đã healthy thì `docker start jarvis-asr-ab-qwen`.
- VRAM: large-v3 float16 (~4,5 GB) + Qwen3-ASR (~6,4 GB) + PhoWhisper large (~4,5 GB) không vừa card 6 GB của máy dev; trên server dùng
  chung GPU, chạy từng cấu hình một nếu thiếu VRAM (bỏ các mục chưa chạy khỏi file cấu hình).

Chạy so sánh rồi dọn dẹp:

```bash
.venv/Scripts/python tools/tests/asr/eval.py --manifest tools/tests/asr/recordings/real/manifest.jsonl \
    --configs tools/tests/asr/configs.example.yaml --label "A/B bộ thật ..."
docker rm -f jarvis-asr-ab-qwen jarvis-asr-ab-pho
cd server && docker compose --profile asr-qwen stop asr-qwen
```

**Chốt cấu hình mặc định:** chọn cấu hình đạt cả 4 tiêu chí (mục 7), ưu tiên từ khóa và WER `mixed`; ghi lý do vào `RESULTS.md` (mục
"Kết luận" tự thêm cuối file). Đưa lựa chọn vào `server/.env` (`ASR_ENGINE`, `ASR_MODEL`, `ASR_MODEL_REVISION`, `ASR_LANGUAGE`,
`ASR_INITIAL_PROMPT`) rồi `docker compose up -d --force-recreate asr`. Cấu hình `+prompt` thắng → đặt `ASR_INITIAL_PROMPT` đúng câu mồi đã thử.

---

## 6. Bộ smoke tổng hợp: `make_synthetic.py` (KHÔNG phải bộ dữ liệu thật)

Chỉ để kiểm tra công cụ và service ASR khi chưa có mũ. Giọng TTS sạch, không qua mic INMP441, không có tiếng ồn thật: **kết quả trên
bộ này không được dùng để đánh giá SV2**. Mọi dòng manifest có `"synthetic": true`; `eval.py` tự in khung cảnh báo "DỮ LIỆU SMOKE" ở
đầu báo cáo, và thư mục có `README.txt` ghi rõ.

Nội dung: các câu của `sentences.md` (theo `--groups`) + 5 mẫu âm tính dài 30 giây (`N01` im lặng tuyệt đối, `N02` nền phòng yên tĩnh
−65 dBFS, `N03` ồn trắng −30 dBFS, `N04` ồn hồng −30 dBFS, `N05` tiếng ù 50 Hz −35 dBFS). Mỗi câu được đệm 600 ms im lặng trước, 700 ms
sau (giống cách xiaozhi cắt câu) và thêm nền nhiễu −70 dBFS; 16 kHz mono.

Nguồn giọng (`--engine`):
- `router`: tts-router của dự án (`POST /v1/audio/speech`, mọi nhóm câu) – cần router truy cập được (`docker-compose.debug.yml`).
- `sapi`: Windows SAPI – chỉ có giọng tiếng Anh (trừ khi cài giọng vi-VN); câu Việt/trộn bị bỏ qua, hoặc đọc bằng espeak-ng với
  `--espeak` (giọng robot, chỉ để chạy được nhánh tiếng Việt; dùng `espeak-ng` cài sẵn, nếu không có thì chạy container `--espeak-image`).
- `auto` (mặc định): router nếu `/health` của router trả 200, ngược lại SAPI.

```bash
.venv/Scripts/python tools/tests/asr/make_synthetic.py                       # auto
.venv/Scripts/python tools/tests/asr/make_synthetic.py --espeak              # SAPI + espeak-ng cho vi/mixed
.venv/Scripts/python tools/tests/asr/make_synthetic.py --engine router --tts-url http://127.0.0.1:8880/v1/audio/speech
# chấm, ghi ra file riêng để không đè RESULTS.md của bộ thật
.venv/Scripts/python tools/tests/asr/eval.py --manifest tools/tests/asr/recordings/synthetic/manifest.jsonl \
    --out tools/tests/asr/RESULTS.smoke.md
```

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--out` | `tools/tests/asr/recordings/synthetic` | Thư mục ra (`manifest.jsonl`, `audio/`, `README.txt`) |
| `--engine` | `auto` | `auto` \| `router` \| `sapi` (`router` mà router không trả lời → thoát mã 2) |
| `--tts-url` | `http://127.0.0.1:8880/v1/audio/speech` | Endpoint tts-router |
| `--voice` | – | Giọng router, hoặc tên chính xác của giọng SAPI |
| `--sentences` | `sentences.md` | Kịch bản |
| `--groups` | `mixed,vi,en` | Nhóm câu cần sinh |
| `--limit` | 0 | Tối đa N câu mỗi nhóm (0 = tất cả) |
| `--pad-ms` | `600,700` | Im lặng trước,sau mỗi câu (ms) |
| `--noise-dbfs` | `-70` | Nền nhiễu thêm vào câu nói, hoặc `off` |
| `--rate` | 0 | Tốc độ đọc SAPI (−10..10) |
| `--espeak` | tắt | Chế độ SAPI: đọc câu vi/mixed bằng espeak-ng |
| `--espeak-image` | `alpine:latest` | Image dùng khi máy không có `espeak-ng` |
| `--no-negatives` | tắt | Không sinh 5 mẫu âm tính |
| `--seed` | 1234 | Hạt giống nhiễu |

Vì `recordings/` bị gitignore, bản clone mới không có bộ smoke: chạy lại `make_synthetic.py` khi cần.

---

## 7. `RESULTS.md` và tiêu chí nghiệm thu SV2

`eval.py` ghi báo cáo Markdown (tiếng Việt) gồm:
1. Khung cảnh báo (nhãn `--label`; "DỮ LIỆU SMOKE" nếu có mẫu tổng hợp), thời điểm chạy, manifest và số mẫu mỗi nhóm.
2. **Tổng hợp**: mỗi cấu hình một dòng – WER chung, WER thô, WER mixed/vi/en, từ khóa, câu nói bị lọc, câu nói ra rỗng, âm tính có
   chữ, p50/p90 ms, p50 câu 2–4 s, RTF p50, số lỗi HTTP.
3. **Đối chiếu tiêu chí nghiệm thu SV2**: ĐẠT / CHƯA ĐẠT / – (thiếu dữ liệu) cho từng cấu hình.
4. Cấu hình đã chạy (URL, language, prompt, thông tin service lấy từ `/health`).
5. Từ khóa bị nhận sai nhiều nhất; tối đa 8 câu sai nhiều nhất mỗi cấu hình.
6. Lệnh chạy lại.

| # | Tiêu chí SV2 | Ngưỡng trong `eval.py` | Ghi chú |
|---|---|---|---|
| 1 | Từ khóa lệnh nhận đúng | ≥ 95 % | Trên mọi câu có lời nói |
| 2 | WER câu trộn ngôn ngữ / câu tiếng Việt thuần | ≤ 15 % / ≤ 10 % | WER nhóm `mixed` / `vi`; nhóm `en` chỉ để tham khảo |
| 3 | Câu 3 giây xử lý ≤ 300 ms | p50 thời gian server của câu 2–4 s ≤ 300 ms | Chỉ có giá trị khi đo trên server RTX 5090, model đã nạp sẵn; GPU máy dev không dùng để kết luận |
| 4 | 30 giây im lặng/tiếng ồn → chuỗi rỗng | 0 mẫu `noise` có chữ | Bộ thật cần có mẫu âm tính (mục 1.2) |

Sau khi chạy trên **bộ thật**: commit `RESULTS.md` (chỉ văn bản, không có âm thanh), thêm mục "Kết luận" ghi cấu hình được chọn, rồi dán
bảng tổng hợp vào `docs/test-report.md` (mục SV2).

---

## 8. Kiểm thử công cụ

```bash
.venv/Scripts/python -m pytest tools/tests/asr -q
```

(Test của service: `server/tests/test_asr_*.py`.)

## 9. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `/health` có `"capture": false`, log `dataset capture disabled ... Permission denied` | Lỗi quyền volume (mục 1.1, bước 2) |
| Service ASR không tải được model lần đầu (`Permission denied` dưới `/hf-cache`) | Cùng nguyên nhân: compose mount volume `hf_cache` ở `/hf-cache` (thuộc root) trong khi service chạy uid 10001. Tạm thời: `docker run --rm --user root -v jarvis_hf_cache:/hf-cache --entrypoint chown jarvis/asr:dev -R asr:asr /hf-cache` |
| `/health` trả 503 `"status": "loading"` | Model đang tải/làm nóng; xem `docker compose logs -f asr`, chờ dòng `[ASR] ready ...` |
| HTTP 400 `invalid language code` | Giá trị `language` sai (chỉ mã Whisper 2–3 chữ, `auto`, hoặc danh sách `vi,en`) |
| Nhiều câu `dropped: gate:silence` / `gate:no_speech` / `gate:too_short` | Cổng lọc thấy quá nhỏ/không có tiếng: kiểm tra mic và khoảng cách; ngưỡng `ASR_MIN_RMS_DBFS` (−60), `ASR_VAD_GATE`, `ASR_VAD_THRESHOLD` (0.5), `ASR_VAD_MIN_SPEECH_MS` (250) |
| WER thô thấp hơn hẳn WER | Bộ lọc ảo giác xóa nhầm: chỉnh `ASR_FILTER_*` hoặc dòng tương ứng trong `server/asr/config/hallucinations.txt` |
| Mẫu `noise` vẫn ra chữ | Thêm câu bịa vào `hallucinations.txt` (dạng `=câu` nếu chỉ chặn khi trùng toàn bộ); không bao giờ thêm lệnh thật hay wake word |
| Container Qwen báo `vLLM not reachable` | Sidecar `asr-qwen` chưa lên (lần đầu tải model) hoặc không đủ VRAM: `docker compose --profile asr-qwen logs asr-qwen` |
| Báo cáo có khung "DỮ LIỆU SMOKE" | Manifest có dòng `synthetic: true`: đây không phải kết quả SV2 |

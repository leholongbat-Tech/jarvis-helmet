# syntax=docker/dockerfile:1
# Jarvis ASR: faster-whisper 1.2.1 + CTranslate2 4.8.2 (CUDA 12.8 wheels, no cuDNN needed).
# CUDA runtime must stay <= 13.2 for the host driver 595 (ext-ai-stack.md section 0).
FROM nvidia/cuda:12.8.2-runtime-ubuntu24.04@sha256:7e62235c5d6f22a57ec8ba51f1583634fdbbf4b08a15b42dc1a60f086145c210

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/cache/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    CUDA_CACHE_PATH=/cache/nv \
    CUDA_CACHE_MAXSIZE=1073741824

# Ubuntu 24.04 python3 = 3.12 (PEP 668: install into a venv). tzdata makes TZ work for capture timestamps.
# APT_CHECK_DATE=false only as a workaround for "Release file ... is not valid yet" (clock skew).
ARG APT_CHECK_DATE=true
RUN apt-get -o Acquire::Check-Date=${APT_CHECK_DATE} update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/venv

COPY requirements.txt constraints.txt /tmp/
RUN pip install -r /tmp/requirements.txt -c /tmp/constraints.txt \
 && python -c "import av, ctranslate2, faster_whisper; \
assert int(av.__version__.split('.')[0]) < 19, av.__version__; \
print('ctranslate2', ctranslate2.__version__, 'faster-whisper', faster_whisper.__version__, 'av', av.__version__)"

ARG APP_UID=10001
RUN useradd --system --uid ${APP_UID} --user-group --create-home --home-dir /home/asr \
        --shell /usr/sbin/nologin asr \
 && mkdir -p /cache/hf /cache/nv /data \
 && chown -R asr:asr /cache /data

WORKDIR /app
COPY app.py hallucinations.txt ./
COPY jarvis_asr ./jarvis_asr

USER asr
EXPOSE 9000
# Generous start period: the first start may download the model (large-v3 ~3 GB) unless prefetched.
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --start-interval=10s --retries=3 \
  CMD ["python", "-c", "import json,sys,urllib.request as u; sys.exit(0 if json.load(u.urlopen('http://127.0.0.1:9000/health', timeout=4)).get('status') == 'ok' else 1)"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000", "--no-access-log", "--timeout-graceful-shutdown", "5"]

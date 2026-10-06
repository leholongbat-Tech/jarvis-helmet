# Jarvis TTS router: FastAPI on :8880, normalizes text and streams PCM from VieNeu / Kokoro.
#   docker build -t jarvis/tts-router:dev server/tts/router
#   docker run --rm -p 127.0.0.1:8880:8880 -e VIENEU_URL=http://vieneu:8000 jarvis/tts-router:dev
FROM python:3.12.14-slim-trixie@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8880

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 1000 router

COPY tts_router/ tts_router/
USER router

EXPOSE 8880
# 200 only when VieNeu answers (/livez is the liveness-only probe).
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8880'), timeout=4).status == 200 else 1)"]
CMD ["python", "-m", "tts_router"]

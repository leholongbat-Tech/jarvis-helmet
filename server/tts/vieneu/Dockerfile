# syntax=docker/dockerfile:1.7
# VieNeu-TTS v3 Turbo: OpenAI-compatible streaming speech API (GPU) for the Jarvis Helmet.
#
# Mirrors upstream docker/Dockerfile.gpu, target "prod", of VieNeu-TTS tag app-v0.18.9
# (commit 85344322b7258b4e25479b692e8e3396baf9db34): CUDA 12.8 runtime on Ubuntu 24.04,
# Python 3.12 from deadsnakes, `uv sync --frozen --no-dev --extra cuda --no-install-project
# --no-build` (torch 2.8.0+cu128: sm_75 ... sm_120), app run from the source tree via
# PYTHONPATH, non-root user `app` (uid 1000).
# Jarvis deviations (marked "jarvis:"): sources cloned and commit-verified in a builder stage,
# base image and uv pinned by digest (CUDA 12.8.2 = same base layers as server/asr), HF cache
# directory pre-created for the volume, default command = speech API on 0.0.0.0:8000,
# healthcheck, optional persisted voices file (cloned voices), helper scripts in /opt/jarvis.
#
#   docker build -t jarvis/vieneu:dev server/tts/vieneu
#   docker run --rm --gpus all -p 127.0.0.1:8000:8000 \
#       -v jarvis-svb-hf:/home/app/.cache/huggingface jarvis/vieneu:dev

ARG VIENEU_REPO=https://github.com/pnnbao97/VieNeu-TTS.git
ARG VIENEU_REF=app-v0.18.9
ARG VIENEU_COMMIT=85344322b7258b4e25479b692e8e3396baf9db34

# --- jarvis: pinned upstream sources -------------------------------------------------------
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8 AS src
ARG VIENEU_REPO
ARG VIENEU_REF
ARG VIENEU_COMMIT
# The wav deletion reproduces upstream's .dockerignore ("*.wav" except "sample/*.wav").
RUN apk add --no-cache git \
 && git clone --depth 1 --branch "${VIENEU_REF}" "${VIENEU_REPO}" /src \
 && test "$(git -C /src rev-parse HEAD)" = "${VIENEU_COMMIT}" \
 && rm -rf /src/.git \
 && find /src -name '*.wav' ! -path '*/sample/*' -delete

# --- upstream stage "base" -----------------------------------------------------------------
FROM nvidia/cuda:12.8.2-runtime-ubuntu24.04@sha256:7e62235c5d6f22a57ec8ba51f1583634fdbbf4b08a15b42dc1a60f086145c210 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT="/opt/venv" \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/workspace/src:/workspace" \
    HF_HOME=/home/app/.cache/huggingface

# jarvis: APT_CHECK_DATE=false only for build hosts whose clock lags (apt then rejects fresh
# Release files as "not valid yet"); signatures are still verified. Keep the default.
ARG APT_CHECK_DATE=true
# Python 3.12 + certificates only (every dependency ships wheels).
RUN apt-get -o Acquire::Check-Date=${APT_CHECK_DATE} update && apt-get install -y --no-install-recommends \
    software-properties-common ca-certificates \
    && add-apt-repository -n ppa:deadsnakes/ppa \
    && apt-get -o Acquire::Check-Date=${APT_CHECK_DATE} update \
    && apt-get install -y --no-install-recommends python3.12 \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1

# jarvis: uv pinned (upstream uses the floating ghcr.io/astral-sh/uv:latest).
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/
# jarvis: Ubuntu 24.04 images may ship a default "ubuntu" user with uid 1000; free the uid first.
RUN (userdel -r ubuntu 2>/dev/null || true) \
    && useradd --create-home --uid 1000 app && mkdir -p /workspace /opt/venv && chown -R app:app /workspace /opt/venv
WORKDIR /workspace

# Dependencies only, wheels only, no project build (the app runs from the source tree).
COPY --from=src --chown=app:app /src/pyproject.toml /src/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra cuda --no-install-project --no-build

# --- upstream stage "prod" -----------------------------------------------------------------
FROM base AS prod
COPY --from=src --chown=app:app /src/src/ src/
COPY --from=src --chown=app:app /src/apps/ apps/
COPY --from=src --chown=app:app /src/config.yaml ./
COPY --from=src --chown=app:app /src/examples/audio_ref/ examples/audio_ref/

# jarvis: HF cache and voices mount points owned by `app` (a fresh named volume inherits the
# owner, otherwise the model download fails with "Permission denied"); pristine copy of the
# built-in voices so the entrypoint can restore them.
RUN mkdir -p /home/app/.cache/huggingface /voices \
    && chown -R app:app /home/app/.cache /voices \
    && cp src/vieneu/assets/voices_v3_turbo.json src/vieneu/assets/voices_v3_turbo.builtin.json
COPY --chown=app:app jarvis_entrypoint.sh healthcheck.py clone_voice.py /opt/jarvis/
RUN sed -i 's/\r$//' /opt/jarvis/jarvis_entrypoint.sh && chmod 0755 /opt/jarvis/jarvis_entrypoint.sh

USER app

# jarvis: serve the speech API on all interfaces (upstream defaults to loopback). One user ->
# few stream slots (cheaper CUDA graph, lower TTFA/VRAM). The watermark is a no-op in the cuda
# extra (resemble-perth not installed). The SDK cannot pin model revisions and the MOSS codec
# loads remote code: JARVIS_HF_OFFLINE=auto makes the entrypoint set HF_HUB_OFFLINE=1 as soon as
# both repos are cached (1 = always offline, 0 = always online).
ENV HOST=0.0.0.0 \
    PORT=8000 \
    VIENEU_MAX_STREAMS=4 \
    VIENEU_QUEUE=8 \
    VIENEU_QUEUE_TIMEOUT=10 \
    VIENEU_WATERMARK=0 \
    VIENEU_VOICES_FILE=/voices/voices_v3_turbo.json \
    JARVIS_HF_OFFLINE=auto

EXPOSE 8000
# The model loads (and on first start downloads ~0.6 GB) before the port opens. A dead stream
# scheduler (503) makes healthcheck.py stop PID 1 so the restart policy starts a fresh container.
HEALTHCHECK --interval=15s --timeout=10s --start-period=600s --retries=3 \
    CMD ["python", "/opt/jarvis/healthcheck.py"]
ENTRYPOINT ["/opt/jarvis/jarvis_entrypoint.sh"]
CMD ["python", "-m", "apps.openai_speech"]

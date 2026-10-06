# syntax=docker/dockerfile:1
# Jarvis xiaozhi-server image (SV-C): upstream xiaozhi-esp32-server v0.9.7 pinned by digest
# + Jarvis plugins (ask_expert, helmet_tasks), TTS provider jarvis_stream, prompt, config
# template rendered at start, and anchored upstream patches (server/xiaozhi/patches).
#
# Build context = server/ (needs server/tasks/ as well):
#   docker build -f server/xiaozhi/Dockerfile -t jarvis/xiaozhi:dev server
# Only the paths listed in Dockerfile.dockerignore are sent to the builder.
FROM ghcr.io/xinnan-tech/xiaozhi-esp32-server:server_0.9.7@sha256:c008cddff83fb5a65d57f2844a6251f7e97af6278682312ecc89c5ba84ace943

# Upstream WORKDIR; relative config paths (prompts/, tmp/, models/) resolve here.
WORKDIR /opt/xiaozhi-esp32-server

# The base image points pip at mirrors.aliyun.com; use PyPI. tzdata is already installed
# (Debian 13 base), so TZ is enough for {{current_time}} in the prompt.
ENV PIP_INDEX_URL=https://pypi.org/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Ho_Chi_Minh \
    PYTHONPATH=/opt/jarvis/lib \
    HELMET_TASKS_FILE=/opt/jarvis/tasks/tasks.yaml \
    JARVIS_CONFIG_TEMPLATE=/opt/jarvis/config/.config.yaml.tmpl \
    XIAOZHI_DIR=/opt/xiaozhi-esp32-server

# rapidfuzz is not in the base image (3.14.5 = newest release for Python 3.10);
# jsonschema 4.26.0 is already present (via mcp) and pinned here so it cannot drift.
RUN pip install "rapidfuzz==3.14.5" "jsonschema==4.26.0"

# Jarvis support files
COPY xiaozhi/entrypoint.py /opt/jarvis/entrypoint.py
COPY xiaozhi/data/.config.yaml.tmpl /opt/jarvis/config/.config.yaml.tmpl
COPY xiaozhi/patches/ /opt/jarvis/patches/
COPY xiaozhi/assets/ /opt/jarvis/assets/
# Pure task library (SV-D) on PYTHONPATH; default tasks (compose mounts server/tasks over them)
COPY tasks/taskslib.py /opt/jarvis/lib/taskslib.py
COPY tasks/tasks.yaml tasks/schema.json /opt/jarvis/tasks/

# Files dropped into the upstream tree
COPY xiaozhi/prompts/ ./prompts/
COPY xiaozhi/plugins/*.py ./plugins_func/functions/
COPY xiaozhi/providers/tts/*.py ./core/providers/tts/

# 1. anchored upstream patches - the build fails on any anchor mismatch
# 2. neutral fallback wake sound instead of the upstream Chinese clip
# 3. Python 3.10 syntax check of everything Jarvis adds
# 4. report of CJK strings left in active code paths (cat /opt/jarvis/patches/cjk_report.txt)
RUN python /opt/jarvis/patches/apply_patches.py --root /opt/xiaozhi-esp32-server \
 && python /opt/jarvis/assets/make_wakeup_chime.py config/assets/wakeup_words_short.wav \
 && python -m py_compile /opt/jarvis/entrypoint.py /opt/jarvis/lib/taskslib.py \
      plugins_func/functions/ask_expert.py plugins_func/functions/helmet_tasks.py \
      core/providers/tts/jarvis_stream.py core/utils/jarvis_support.py \
 && python /opt/jarvis/patches/scan_cjk.py --root /opt/xiaozhi-esp32-server > /opt/jarvis/patches/cjk_report.txt \
 && tail -n 1 /opt/jarvis/patches/cjk_report.txt

# 8000 = WebSocket, 8003 = OTA / vision HTTP
EXPOSE 8000 8003

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/xiaozhi/ota/', timeout=3)" || exit 1

# Render data/.config.yaml from the environment, then exec the upstream command.
ENTRYPOINT ["python", "/opt/jarvis/entrypoint.py"]
CMD ["python", "app.py"]

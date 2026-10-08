# Jarvis dashboard: status page for the helmet and the server stack (FastAPI on :8080).
#   docker build -t jarvis/dashboard:dev server/dashboard
#   docker run --rm -p 127.0.0.1:8090:8080 -e DASHBOARD_PASSWORD=... jarvis/dashboard:dev
FROM python:3.12.14-slim-trixie@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 1000 dashboard

COPY jarvis_dashboard/ jarvis_dashboard/
USER dashboard
EXPOSE 8080

# /healthz needs no password and returns no data.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8080'), timeout=4).status == 200 else 1)"]
CMD ["python", "-m", "jarvis_dashboard"]

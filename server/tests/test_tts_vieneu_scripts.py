"""VieNeu image helpers (server/tts/vieneu): healthcheck.py restart rule and the entrypoint's
Hugging Face offline mode (TTS_HF_OFFLINE = auto | 1 | 0)."""
import http.server
import importlib.util
import os
import shutil
import signal
import socket
import subprocess
import threading
from pathlib import Path

import pytest

VIENEU = Path(__file__).resolve().parents[1] / "tts" / "vieneu"
REPOS = ("pnnbao-ump--VieNeu-TTS-v3-Turbo", "OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano")


def _load_healthcheck():
    spec = importlib.util.spec_from_file_location("vieneu_healthcheck", VIENEU / "healthcheck.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hc = _load_healthcheck()


class _Health:
    """127.0.0.1 server answering GET /health with a fixed status and body."""

    def __init__(self, status, body):
        outer = self
        self.status, self.body = status, body

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = outer.body.encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _check(status=None, body=""):
    kills = []
    server = _Health(status, body) if status is not None else None
    if server is None:  # nothing listening: model still loading
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
    else:
        port = server.port
    try:
        rc = hc.check(port, kill=lambda pid, sig: kills.append((pid, sig)), sleep=lambda s: None)
    finally:
        if server is not None:
            server.close()
    return rc, kills


def test_healthy_server_passes_and_is_left_alone():
    assert _check(200, '{"status": "ok"}') == (0, [])


def test_dead_stream_scheduler_stops_pid_1():
    body = '{"status": "error", "error": "stream scheduler is down: RuntimeError(\'CUDA error\')"}'
    rc, kills = _check(503, body)
    assert rc == 1
    assert kills == [(1, signal.SIGTERM), (1, signal.SIGINT)]  # graceful, then uvicorn's forced exit


@pytest.mark.parametrize("status, body", [(503, '{"status": "loading"}'), (500, "stream scheduler is down")])
def test_other_failures_never_kill(status, body):
    assert _check(status, body) == (1, [])


def test_starting_server_is_a_plain_failure():
    assert _check() == (1, [])  # connection refused during the model download / warm-up


# --- entrypoint: offline mode ------------------------------------------------------------------
def _find_bash():
    if os.name == "nt":  # Git Bash; System32\bash.exe would run inside WSL
        for cand in (Path(r"C:\Program Files\Git\bin\bash.exe"), Path(r"C:\Program Files (x86)\Git\bin\bash.exe")):
            if cand.is_file():
                return str(cand)
        return None
    return shutil.which("bash") or shutil.which("sh")


BASH = _find_bash()


def _offline(tmp_path, mode=None, cached=()):
    hf = tmp_path / "hf"
    for repo in cached:
        (hf / "hub" / f"models--{repo}" / "refs").mkdir(parents=True)
        (hf / "hub" / f"models--{repo}" / "refs" / "main").write_text("0123abcd", encoding="utf-8")
        (hf / "hub" / f"models--{repo}" / "snapshots" / "0123abcd").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k not in ("JARVIS_HF_OFFLINE", "HF_HUB_OFFLINE",
                                                             "VIENEU_VOICES_FILE")}
    env["HF_HOME"] = str(hf).replace("\\", "/")
    if mode is not None:
        env["JARVIS_HF_OFFLINE"] = mode
    script = str(VIENEU / "jarvis_entrypoint.sh").replace("\\", "/")
    res = subprocess.run([BASH, script, "sh", "-c", 'echo "RESULT=$HF_HUB_OFFLINE"'], capture_output=True,
                         text=True, env=env, timeout=30)
    assert res.returncode == 0, res.stderr
    return res.stdout


@pytest.mark.skipif(BASH is None, reason="bash/sh not found")
@pytest.mark.parametrize("mode, cached, expected", [
    (None, (), "0"),                    # first start: download (online)
    (None, REPOS[:1], "0"),             # codec still missing
    (None, REPOS, "1"),                 # both cached: offline from now on
    ("auto", REPOS, "1"),
    ("0", REPOS, "0"),                  # forced online (model/device change)
    ("1", (), "1"),                     # forced offline
])
def test_entrypoint_offline_mode(tmp_path, mode, cached, expected):
    out = _offline(tmp_path, mode, cached)
    assert f"RESULT={expected}" in out
    assert f"jarvis: HF_HUB_OFFLINE={expected}" in out

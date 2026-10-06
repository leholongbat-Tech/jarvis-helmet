"""Container healthcheck: VieNeu /health must answer 200.

503 with "stream scheduler is down" means the GPU stream scheduler died (CUDA error / OOM); VieNeu
never recovers from that and plain Docker never restarts an unhealthy container. So the check stops
the server (PID 1): SIGTERM for uvicorn's graceful exit, then SIGINT, which uvicorn turns into a forced
exit once a shutdown is under way; `restart: unless-stopped` then starts a fresh container.
Connection refused / timeouts (model download and warm-up) stay plain failures.
"""
import os
import signal
import sys
import time
import urllib.error
import urllib.request

SCHEDULER_DOWN = "stream scheduler is down"


def check(port: int, kill=os.kill, sleep=time.sleep) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=4) as resp:
            return 0 if resp.status == 200 else 1
    except urllib.error.HTTPError as exc:  # 503 and friends carry a JSON body
        try:
            body = exc.read(4096).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - broken body: treat as a plain failure
            body = ""
        if exc.code == 503 and SCHEDULER_DOWN in body:
            sys.stderr.write("jarvis healthcheck: VieNeu stream scheduler is down, restarting the container\n")
            try:
                kill(1, signal.SIGTERM)
                sleep(3)
                kill(1, signal.SIGINT)
            except OSError:  # PID 1 already gone
                pass
        return 1
    except Exception:  # connection refused while the model loads, timeout
        return 1


if __name__ == "__main__":
    sys.exit(check(int(os.environ.get("PORT", "8000"))))

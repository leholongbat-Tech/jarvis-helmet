"""Jarvis dashboard: one page with the helmet, the server stack and the recent conversation.

Data sources, all read-only: xiaozhi's log file (volume xiaozhi_tmp), the helmet's local MCP
WebSocket (status tools only) and the /health endpoints of the services on the "app" network.
Every page and API call needs HTTP Basic auth (DASHBOARD_USER / DASHBOARD_PASSWORD): the page shows
what the helmet heard.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import Settings
from .device import DeviceProbe, probe
from .health import check_services
from .logstate import LogFollower, LogState

STATIC = Path(__file__).with_name("static")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                               "style-src 'self' 'unsafe-inline'; img-src 'self' data:",
}
log = logging.getLogger("jarvis_dashboard")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_virtual(device_id: str) -> bool:
    """Locally administered MAC (e.g. tools/tests/e2e/device_sim.py uses 02:4a:48:4d:00:01)."""
    try:
        return bool(int(device_id.split(":")[0], 16) & 0x02)
    except ValueError:
        return False


class Monitor:
    """Everything the page shows, refreshed by the background loops."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logs = LogState()
        self.follower = LogFollower(settings.log_file, self.logs)
        self.probes: Dict[str, DeviceProbe] = {}
        self.services: List[dict] = []
        self.services_checked = ""
        self.started = _now()

    def device_hosts(self) -> List[str]:
        """Configured hosts plus the IPs real helmets connected from."""
        hosts = list(self.settings.device_hosts)
        for dev in self.logs.devices.values():
            if dev.ip and dev.user_agent.startswith("jarvis-helmet/") and not is_virtual(dev.id) \
                    and dev.ip not in hosts:
                hosts.append(dev.ip)
        return hosts

    async def poll_logs(self) -> None:
        while True:
            try:
                self.follower.poll()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive whatever happens
                self.follower.error = f"{type(exc).__name__}: {exc}"[:200]
            await asyncio.sleep(1.0)

    async def poll_devices(self) -> None:
        while True:
            try:
                hosts = self.device_hosts()
                for host in hosts:
                    self.probes.setdefault(host, DeviceProbe(host=host))
                now = _now()
                await asyncio.gather(*(probe(self.probes[h], self.settings.mcp_port, self.settings.mcp_token, now)
                                       for h in hosts))
            except Exception:  # noqa: BLE001
                log.exception("device poll failed")
            await asyncio.sleep(self.settings.device_poll_s)

    async def poll_services(self) -> None:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            while True:
                try:
                    self.services = await check_services(client, self.settings.services)
                    self.services_checked = _now()
                except Exception:  # noqa: BLE001
                    log.exception("service check failed")
                await asyncio.sleep(self.settings.health_poll_s)

    def snapshot(self) -> dict:
        logs = self.logs.snapshot()
        devices = logs["devices"]
        for dev in devices:
            dev["virtual"] = is_virtual(dev["id"])
        by_ip = {d["ip"]: d for d in devices if d.get("ip") and not d["virtual"]}
        for host, found in self.probes.items():
            entry = by_ip.get(host)
            if entry is None:  # configured host that has not talked to the server yet
                entry = {"id": host, "ip": host, "user_agent": "", "last_seen": "", "last_ota": "",
                         "session": None, "virtual": False}
                devices.append(entry)
            entry["mcp"] = asdict(found)
        return {
            "now": _now(),
            "started": self.started,
            "llm": {"local": self.settings.llm_model, "cloud": self.settings.cloud_model},
            "services": self.services,
            "services_checked": self.services_checked,
            "devices": devices,
            "turns": logs["turns"],
            "events": logs["events"],
            "log": {"file": self.settings.log_file, "lines": logs["lines"], "error": self.follower.error},
        }


def create_app(settings: Settings, monitor: Optional[Monitor] = None, background: bool = True) -> FastAPI:
    monitor = monitor or Monitor(settings)
    basic = HTTPBasic(realm="Jarvis")

    def require_user(creds: HTTPBasicCredentials = Depends(basic)) -> None:
        good_user = secrets.compare_digest(creds.username.encode(), settings.user.encode())
        good_password = secrets.compare_digest(creds.password.encode(), settings.password.encode())
        if not (settings.password and good_user and good_password):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unauthorized",
                                headers={"WWW-Authenticate": 'Basic realm="Jarvis"'})

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks = []
        if background:
            tasks = [asyncio.create_task(loop()) for loop in
                     (monitor.poll_logs, monitor.poll_devices, monitor.poll_services)]
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Jarvis dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.monitor = monitor

    @app.get("/healthz")
    async def healthz() -> dict:  # docker healthcheck: no auth, no data
        return {"status": "ok"}

    @app.get("/api/status", dependencies=[Depends(require_user)])
    async def api_status() -> JSONResponse:
        return JSONResponse(monitor.snapshot(), headers=SECURITY_HEADERS)

    @app.get("/", dependencies=[Depends(require_user)])
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"), headers=SECURITY_HEADERS)

    return app

"""Health checks of the stack's services over the compose network "app"."""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List

import httpx


def _detail(name: str, response: httpx.Response) -> str:
    """One short line about what the service reports."""
    try:
        body = response.json()
    except ValueError:
        return "OK" if response.is_success else f"HTTP {response.status_code}"
    if name == "asr" and isinstance(body, dict):
        return " · ".join(str(body[k]) for k in ("engine", "model", "device", "compute_type") if body.get(k))
    if name == "tts-router" and isinstance(body, dict):
        vieneu = (body.get("backends") or {}).get("vieneu") or {}
        backend = (vieneu.get("detail") or {}).get("backend", "")
        parts = [f"VieNeu {backend}".strip(), f"giọng {body['voice']}" if body.get("voice") else ""]
        return " · ".join(p for p in parts if p)
    if isinstance(body, str):
        return body[:80]
    return "OK" if response.is_success else f"HTTP {response.status_code}"


async def check_one(client: httpx.AsyncClient, name: str, url: str) -> dict:
    started = time.monotonic()
    try:
        response = await client.get(url)
        ok, detail = response.is_success, _detail(name, response)
        if not ok:
            detail = f"HTTP {response.status_code} {detail}".strip()
    except httpx.HTTPError as exc:
        ok, detail = False, type(exc).__name__
    return {"name": name, "ok": ok, "ms": int((time.monotonic() - started) * 1000), "detail": detail[:120]}


async def check_services(client: httpx.AsyncClient, services: Dict[str, str]) -> List[dict]:
    return list(await asyncio.gather(*(check_one(client, name, url) for name, url in services.items())))

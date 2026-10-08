"""Dashboard settings from environment variables (server/README.md, section 3.7)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

# Health URLs of the stack, all on the compose network "app".
DEFAULT_SERVICES: Dict[str, str] = {
    "xiaozhi": "http://xiaozhi:8003/xiaozhi/ota/",
    "asr": "http://asr:9000/health",
    "tts-router": "http://tts-router:8880/health",
    "litellm": "http://litellm:4000/health/liveliness",
}


def _get(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(_get(env, name, str(default)))
    except ValueError:
        return default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(_get(env, name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    user: str = "jarvis"
    password: str = ""  # required: the page shows what the helmet heard
    log_file: str = "/xiaozhi-tmp/server.log"  # xiaozhi's tmp/server.log (volume xiaozhi_tmp, read-only)
    device_hosts: List[str] = field(default_factory=list)  # more are learned from the log
    mcp_port: int = 8080
    mcp_token: str = ""
    llm_model: str = ""
    cloud_model: str = ""
    device_poll_s: float = 5.0
    health_poll_s: float = 10.0
    services: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SERVICES))
    host: str = "0.0.0.0"
    port: int = 8080

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        hosts = [h.strip() for h in _get(env, "JARVIS_DEVICE_HOSTS", "").split(",") if h.strip()]
        return cls(
            user=_get(env, "DASHBOARD_USER", "jarvis"),
            password=_get(env, "DASHBOARD_PASSWORD", ""),
            log_file=_get(env, "XIAOZHI_LOG_FILE", "/xiaozhi-tmp/server.log"),
            device_hosts=hosts,
            mcp_port=_int(env, "JARVIS_MCP_PORT", 8080),
            mcp_token=_get(env, "JARVIS_MCP_TOKEN", ""),
            llm_model=_get(env, "OLLAMA_MODEL", ""),
            cloud_model=_get(env, "CLOUD_MODEL", ""),
            device_poll_s=max(2.0, _float(env, "DASHBOARD_DEVICE_POLL_S", 5.0)),
            health_poll_s=max(2.0, _float(env, "DASHBOARD_HEALTH_POLL_S", 10.0)),
            host=_get(env, "HOST", "0.0.0.0"),
            port=_int(env, "PORT", 8080),
        )

"""Static checks of the compose stack against the contract (section 4.1/4.2, D20-D22, D25) and against
the code of the services: every environment variable a service reads must be passed by compose,
secrets only go to the containers that need them, only xiaozhi publishes ports, GPUs via CDI.
With docker available, every compose variant is also validated with `docker compose config`.

    .venv/Scripts/python -m pytest server/scripts/tests -q
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
SERVER = REPO / "server"
BASE = SERVER / "docker-compose.yml"
LOCAL = SERVER / "docker-compose.local.yml"
DEBUG = SERVER / "docker-compose.debug.yml"
LOCALLY_MANAGED = SERVER / "cloudflared" / "compose.locally-managed.yml"
SHARED_HOST = SERVER / "docker-compose.shared-host.example.yml"
ENV_EXAMPLE = SERVER / ".env.example"

GPU_SERVICES = {"asr", "ollama", "vieneu", "kokoro", "asr-qwen"}
PINNED = {  # contract section 4.1
    "ollama": "ollama/ollama:0.35.1@sha256:292ee7945dfc3d5840a181f3ab86fedb1e66703e02c8af98b50f4da56b7e278c",
    "litellm": "ghcr.io/berriai/litellm:v1.104.0@sha256:625981c83410a3ea68eb0697590a57ec1d764d634514d54fa5db0591077ee839",
    "kokoro": "ghcr.io/remsky/kokoro-fastapi-gpu:v0.9.0-cu128@sha256:c6683ee24a3b3bc3b49130ecc52c32e8010a1052ae32781a91682fa09c6ba984",
    "asr-qwen": "vllm/vllm-openai:v0.31.0-cu129@sha256:ff29e51a9457f191deb332b96fa6440584fa941aa2a548449b604e2b1e5e1eeb",
    "cloudflared": "cloudflare/cloudflared:2026.10.0@sha256:9b49eed8f62806d5d45ddf59ecefb5710429598ea6d3fcccd2af938f621b2b07",
}
CONTRACT_ENV_KEYS = [  # contract section 4.2
    "TZ", "LAN_IP", "XIAOZHI_WS_PORT", "XIAOZHI_OTA_PORT", "PUBLIC_WS_URL", "PUBLIC_OTA_URL", "XIAOZHI_AUTH_KEY",
    "LITELLM_MASTER_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "CLOUD_MODEL",
    "CLOUD_MONTHLY_BUDGET_USD", "OLLAMA_MODEL", "ASR_ENGINE", "ASR_MODEL", "ASR_LANGUAGE", "ASR_INITIAL_PROMPT",
    "ASR_COMPUTE_TYPE", "ASR_DEVICE", "ASR_SAVE_DIR", "TTS_VOICE", "TTS_ENGLISH_ENGINE", "TUNNEL_TOKEN", "HF_TOKEN",
]


class ComposeLoader(yaml.SafeLoader):
    """SafeLoader that accepts compose's !override / !reset tags."""


def _tagged(loader, _suffix, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


ComposeLoader.add_multi_constructor("!", _tagged)


def load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader)


@pytest.fixture(scope="module")
def base():
    return load(BASE)


@pytest.fixture(scope="module")
def services(base):
    return base["services"]


def env_of(service: dict) -> dict:
    env = service.get("environment") or {}
    assert isinstance(env, dict), "use the mapping form for environment"
    return env


def env_file_keys(path: Path, commented: bool = False) -> list[str]:
    pattern = r"^#\s*([A-Z][A-Z0-9_]*)=" if commented else r"^([A-Z][A-Z0-9_]*)="
    return [m.group(1) for line in path.read_text(encoding="utf-8").splitlines() if (m := re.match(pattern, line))]


# ---- structure ----------------------------------------------------------------------------------
def test_project_name(base):
    assert base["name"] == "jarvis"


def test_no_service_loads_the_whole_env_file(services):
    for name, svc in services.items():
        assert "env_file" not in svc, f"{name}: use an explicit environment list (least privilege)"


def test_restart_healthcheck_logging(services):
    for name, svc in services.items():
        assert svc.get("restart") == "unless-stopped", name
        assert (svc.get("healthcheck") or {}).get("test"), f"{name}: healthcheck missing"
        log = svc.get("logging") or {}
        assert log.get("driver") == "json-file", name
        assert log.get("options") == {"max-size": "20m", "max-file": "3"}, name


def test_pinned_images(services):
    for name, image in PINNED.items():
        assert services[name]["image"] == image, name
    assert re.fullmatch(r"redis:7\.4[.\d]*-alpine@sha256:[0-9a-f]{64}", services["redis"]["image"])
    for name in ("xiaozhi", "asr", "tts-router", "vieneu"):
        assert services[name]["image"].startswith(f"jarvis/{name}:"), name
        assert "build" in services[name], name


def test_profiles(services):
    assert services["cloudflared"]["profiles"] == ["tunnel"]
    assert services["kokoro"]["profiles"] == ["english-tts"]
    assert services["asr-qwen"]["profiles"] == ["asr-qwen"]
    for name in ("xiaozhi", "asr", "ollama", "litellm", "redis", "tts-router", "vieneu"):
        assert "profiles" not in services[name], name


# ---- ports (D21) --------------------------------------------------------------------------------
def test_only_xiaozhi_publishes_ports(services):
    for name, svc in services.items():
        if name != "xiaozhi":
            assert not svc.get("ports"), f"{name} must not publish host ports"
    assert services["xiaozhi"]["ports"] == [
        "${XIAOZHI_BIND_IP:-0.0.0.0}:${XIAOZHI_WS_PORT:-8100}:8000",
        "${XIAOZHI_BIND_IP:-0.0.0.0}:${XIAOZHI_OTA_PORT:-8003}:8003",
    ]


def test_debug_override_binds_loopback_only():
    data = load(DEBUG)
    ports = [p for svc in data["services"].values() for p in svc.get("ports", [])]
    assert ports and all(str(p).startswith("127.0.0.1:") for p in ports)
    assert "ollama" not in data["services"], "never publish 11434 (the host runs its own Ollama)"


# ---- GPU (D20) ----------------------------------------------------------------------------------
def test_gpu_services_use_cdi(services):
    for name in GPU_SERVICES:
        devices = services[name]["deploy"]["resources"]["reservations"]["devices"]
        assert devices == [{"driver": "cdi", "device_ids": ["nvidia.com/gpu=all"], "capabilities": ["gpu"]}], name
    for name in set(services) - GPU_SERVICES:
        assert "deploy" not in services[name], f"{name} should not reserve a GPU"


def test_local_override_uses_nvidia_driver():
    data = load(LOCAL)
    for name in GPU_SERVICES:
        devices = data["services"][name]["deploy"]["resources"]["reservations"]["devices"]
        assert devices == [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}], name
    text = LOCAL.read_text(encoding="utf-8")
    assert "devices: !override" in text, "the CDI request of the base file must be replaced, not merged"


# ---- environment vs code ------------------------------------------------------------------------
def test_asr_env_matches_code(services):
    code = (SERVER / "asr" / "jarvis_asr" / "config.py").read_text(encoding="utf-8")
    code += (SERVER / "asr" / "app.py").read_text(encoding="utf-8")
    read = set(re.findall(r'"(ASR_[A-Z0-9_]+)"', code)) - {"ASR_FAKE_TEXT"}  # fake engine: tests only
    missing = read - set(env_of(services["asr"]))
    assert not missing, f"ASR reads {sorted(missing)} but compose does not pass them"


def test_asr_mounts_are_owned_by_the_image_user(services):
    dockerfile = (SERVER / "asr" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER asr$", dockerfile, re.M)
    created = re.search(r"mkdir -p ([^\\\n&]+)", dockerfile).group(1).split()
    for volume in services["asr"]["volumes"]:
        if volume.startswith("./"):
            continue  # read-only bind mounts (config) need no owner
        target = volume.split(":")[1]
        assert target in created, f"{target} is not created (owned by asr) in server/asr/Dockerfile"
    assert "HF_HOME" not in env_of(services["asr"]), "the image sets HF_HOME=/cache/hf"


def test_asr_hallucination_list_is_a_live_directory_mount(services):
    """audio-services-9: host edits apply without a rebuild (a single-file mount misses renames)."""
    assert "./asr/config:/app/config:ro" in services["asr"]["volumes"]
    assert (SERVER / "asr" / "config" / "hallucinations.txt").is_file()
    config = (SERVER / "asr" / "jarvis_asr" / "config.py").read_text(encoding="utf-8")
    assert 'APP_DIR / "config" / "hallucinations.txt"' in config
    assert "COPY config ./config" in (SERVER / "asr" / "Dockerfile").read_text(encoding="utf-8")


# security-config-5: each container shares a network only with the services it talks to.
NETWORKS = {
    "cloudflared": {"edge"},
    "xiaozhi": {"edge", "app"},
    "litellm": {"app", "llm"},
    "asr": {"app", "asrnet"},
    "tts-router": {"app", "tts"},
    "redis": {"llm"},
    "ollama": {"llm"},
    "vieneu": {"tts"},
    "kokoro": {"tts"},
    "asr-qwen": {"asrnet"},
}


def _peers(name: str) -> set[str]:
    return {other for other, nets in NETWORKS.items() if other != name and nets & NETWORKS[name]}


def test_networks_isolate_backends(base, services):
    assert set(base["networks"]) == {"edge", "app", "llm", "tts", "asrnet"}
    assert set(services) == set(NETWORKS)
    for name, service in services.items():
        assert set(service.get("networks") or []) == NETWORKS[name], name
    assert _peers("redis") <= {"litellm", "ollama"}
    assert _peers("ollama") <= {"litellm", "redis"}
    assert _peers("vieneu") <= {"tts-router", "kokoro"}
    assert _peers("cloudflared") == {"xiaozhi"}
    assert not {"redis", "ollama", "vieneu"} & _peers("xiaozhi")


def test_every_internal_url_is_reachable(services):
    """Each http://<service>:<port> a container is configured with points to a network peer."""
    sources = {name: " ".join(str(v) for v in env_of(svc).values()) for name, svc in services.items()}
    sources["xiaozhi"] += (SERVER / "xiaozhi" / "data" / ".config.yaml.tmpl").read_text(encoding="utf-8")
    sources["litellm"] += (SERVER / "litellm" / "config.yaml").read_text(encoding="utf-8")
    sources["cloudflared"] += (SERVER / "cloudflared" / "ingress.json").read_text(encoding="utf-8")
    seen = set()
    for name, text in sources.items():
        for host in re.findall(r"https?://([a-z][a-z0-9-]*):\d+", text):
            if host in services:
                assert host in _peers(name), f"{name} calls {host} but shares no network with it"
                seen.add((name, host))
    assert {("xiaozhi", "litellm"), ("xiaozhi", "asr"), ("xiaozhi", "tts-router"), ("litellm", "ollama"),
            ("tts-router", "vieneu"), ("tts-router", "kokoro"), ("asr", "asr-qwen"),
            ("cloudflared", "xiaozhi")} <= seen


def test_no_new_privileges_where_it_is_cheap(services):
    for name in ("xiaozhi", "litellm", "tts-router"):
        assert "no-new-privileges:true" in (services[name].get("security_opt") or []), name


def test_vieneu_mounts_are_prepared_by_the_image(services):
    dockerfile = (SERVER / "tts" / "vieneu" / "Dockerfile").read_text(encoding="utf-8")
    for volume in services["vieneu"]["volumes"]:
        assert volume.split(":")[1] in dockerfile


def test_tts_router_env_matches_code(services):
    code = (SERVER / "tts" / "router" / "tts_router" / "config.py").read_text(encoding="utf-8")
    read = set(re.findall(r'_(?:get|float|int)\(env, "([A-Z0-9_]+)"', code))
    read -= {"HOST", "PORT", "LOG_LEVEL", "VIENEU_API_KEY"}  # image defaults / unused
    missing = read - set(env_of(services["tts-router"]))
    assert not missing, f"tts-router reads {sorted(missing)} but compose does not pass them"


def test_xiaozhi_env_matches_template(services):
    template = (SERVER / "xiaozhi" / "data" / ".config.yaml.tmpl").read_text(encoding="utf-8")
    lines = [ln for ln in template.splitlines() if not ln.lstrip().startswith("#")]
    used = set(re.findall(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}", "\n".join(lines)))
    env = env_of(services["xiaozhi"])
    assert used, "template placeholders not found"
    assert used <= set(env), f"template uses {sorted(used - set(env))} but compose does not pass them"
    assert env["HELMET_TASKS_FILE"] == "/opt/jarvis/tasks/tasks.yaml"
    for secret in ("XIAOZHI_AUTH_KEY", "LITELLM_MASTER_KEY"):
        assert env[secret].startswith("${" + secret + ":?"), f"{secret} must be required"


def test_litellm_env_matches_configs(services):
    env = set(env_of(services["litellm"]))
    prod = (SERVER / "litellm" / "config.yaml").read_text(encoding="utf-8")
    missing = set(re.findall(r"os\.environ/([A-Z0-9_]+)", prod)) - env
    assert not missing, f"config.yaml needs {sorted(missing)}"
    local_env = env | set(env_of(load(LOCAL)["services"]["litellm"]))
    local = (SERVER / "litellm" / "config.local.yaml").read_text(encoding="utf-8")
    missing = set(re.findall(r"os\.environ/([A-Z0-9_]+)", local)) - local_env
    assert not missing, f"config.local.yaml needs {sorted(missing)}"
    callbacks = (SERVER / "litellm" / "jarvis_callbacks.py").read_text(encoding="utf-8")
    missing = set(re.findall(r'os\.environ\.get\("(JARVIS_[A-Z0-9_]+)"', callbacks)) - env
    assert not missing, f"jarvis_callbacks.py reads {sorted(missing)}"
    assert services["litellm"]["volumes"] == ["./litellm:/etc/litellm:ro"]
    assert services["litellm"]["command"] == ["--config", "/etc/litellm/config.yaml", "--port", "4000"]


def test_secrets_only_where_needed(services):
    allowed = {
        "JARVIS_ANTHROPIC_API_KEY": {"litellm"}, "JARVIS_OPENAI_API_KEY": {"litellm"},
        "JARVIS_GEMINI_API_KEY": {"litellm"},
        "LITELLM_MASTER_KEY": {"litellm", "xiaozhi"}, "XIAOZHI_AUTH_KEY": {"xiaozhi"},
        "TUNNEL_TOKEN": {"cloudflared"}, "HF_TOKEN": {"asr", "asr-qwen"},
    }
    for secret, owners in allowed.items():
        have = {name for name, svc in services.items() if secret in env_of(svc)}
        assert have <= owners, f"{secret} reaches {sorted(have - owners)}"


def test_no_default_provider_key_names_anywhere(services):
    # LiteLLM's pass-through routes read these default names and bypass the router budget.
    default_names = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY",
                     "GOOGLE_API_KEY"}
    for name, svc in services.items():
        leaked = default_names & set(env_of(svc))
        assert not leaked, f"{name} gets {sorted(leaked)} (use JARVIS_* names)"
    env = env_of(services["litellm"])
    for provider in ("ANTHROPIC", "OPENAI", "GEMINI"):
        assert env[f"JARVIS_{provider}_API_KEY"] == "${" + provider + "_API_KEY:-}"
    config = (SERVER / "litellm" / "config.yaml").read_text(encoding="utf-8")
    code = "\n".join(ln.split("#", 1)[0] for ln in config.splitlines())
    assert "use_in_pass_through" not in code


def test_ollama_service(services):
    svc = services["ollama"]
    env = env_of(svc)
    assert env["OLLAMA_KEEP_ALIVE"] == "-1"
    assert env["OLLAMA_CONTEXT_LENGTH"] == "${OLLAMA_CONTEXT_LENGTH:-8192}"
    assert env["OLLAMA_NUM_PARALLEL"] == "${OLLAMA_NUM_PARALLEL:-1}"
    assert env["OLLAMA_FLASH_ATTENTION"] == "${OLLAMA_FLASH_ATTENTION:-1}"
    assert env["JARVIS_PRELOAD_MODEL"] == "${OLLAMA_MODEL:-qwen3:30b-a3b-instruct-2507-q4_K_M}"
    assert svc["init"] is True
    assert svc["entrypoint"] == ["/bin/sh", "/opt/jarvis/ollama-entrypoint.sh"]
    assert "./ollama/entrypoint.sh:/opt/jarvis/ollama-entrypoint.sh:ro" in svc["volumes"]
    assert (SERVER / "ollama" / "entrypoint.sh").is_file()
    assert services["litellm"]["depends_on"]["ollama"]["condition"] == "service_started"


def test_litellm_main_model_follows_ollama_model(services):
    env = env_of(services["litellm"])
    assert env["LITELLM_MAIN_MODEL"] == "ollama_chat/${OLLAMA_MODEL:-qwen3:30b-a3b-instruct-2507-q4_K_M}"
    assert env["CLOUD_MODEL"] == "${CLOUD_MODEL:-anthropic/claude-opus-5-5}"


# ---- .env.example ---------------------------------------------------------------------------------
def test_env_example_has_contract_keys_and_placeholders():
    keys = env_file_keys(ENV_EXAMPLE)
    missing = [k for k in CONTRACT_ENV_KEYS if k not in keys]
    assert not missing, missing
    values = dict(line.split("=", 1) for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
                  if re.match(r"^[A-Z][A-Z0-9_]*=", line))
    assert values["XIAOZHI_WS_PORT"] == "8100" and values["XIAOZHI_OTA_PORT"] == "8003"
    assert values["PUBLIC_WS_URL"].startswith("ws://") and ":8100/xiaozhi/v1/" in values["PUBLIC_WS_URL"]
    assert values["PUBLIC_OTA_URL"].endswith(":8003/xiaozhi/ota/")
    assert values["CLOUD_MODEL"] == "anthropic/claude-opus-5-5"
    assert values["OLLAMA_MODEL"] == "qwen3:30b-a3b-instruct-2507-q4_K_M"
    assert "change-me" in values["XIAOZHI_AUTH_KEY"]
    assert values["LITELLM_MASTER_KEY"].startswith("sk-change-me")
    for secret in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "TUNNEL_TOKEN", "HF_TOKEN"):
        assert values[secret] == "", f"{secret} must be empty in the example"


def test_every_env_example_key_is_used():
    compose_text = "\n".join(p.read_text(encoding="utf-8") for p in (BASE, LOCAL, DEBUG))
    consumed = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", compose_text))
    consumed |= set(re.findall(r"^\s+([A-Z][A-Z0-9_]*):\s*$", compose_text, re.M))  # pass-through
    script_only = {"LAN_IP", "COMPOSE_PROFILES"}  # firewall_lan.sh / read by docker compose itself
    keys = set(env_file_keys(ENV_EXAMPLE)) | set(env_file_keys(ENV_EXAMPLE, commented=True))
    unused = keys - consumed - script_only
    assert not unused, f".env.example documents keys nothing reads: {sorted(unused)}"


# ---- docker compose config (needs docker) -------------------------------------------------------
def _docker_compose_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


VARIANTS = {
    "default": ([BASE], []),
    "all-profiles": ([BASE], ["tunnel", "english-tts", "asr-qwen"]),
    "local": ([BASE, LOCAL], []),
    "debug": ([BASE, DEBUG], []),
    "local+debug": ([BASE, LOCAL, DEBUG], []),
    "local+local-ollama": ([BASE, LOCAL], ["local-ollama"]),
    "locally-managed-tunnel": ([BASE, LOCALLY_MANAGED], ["tunnel"]),
    "shared-host": ([BASE, SHARED_HOST], []),
}


def test_shared_host_template_keeps_the_relay_off_the_lan():
    """The relay to the host Ollama binds the pinned llm gateway only, never 0.0.0.0."""
    shared = load(SHARED_HOST)
    relay = shared["services"]["ollama-relay"]
    assert relay["network_mode"] == "host"
    assert relay["image"].startswith("alpine/socat:") and "@sha256:" in relay["image"]
    listen = relay["command"][0]
    assert listen.startswith("TCP-LISTEN:11436,bind=10.250.99.1,")
    gateway = shared["networks"]["llm"]["ipam"]["config"][0]["gateway"]
    assert gateway == "10.250.99.1" and relay["command"][1] == "TCP:127.0.0.1:11434"
    assert shared["services"]["ollama"]["profiles"] == ["own-ollama"]


@pytest.fixture(scope="module")
def dummy_env(tmp_path_factory):
    path = tmp_path_factory.mktemp("compose") / "test.env"
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    text = re.sub(r"^ANTHROPIC_API_KEY=.*$", "ANTHROPIC_API_KEY=sk-ant-test-dummy", text, flags=re.M)
    text = re.sub(r"^TUNNEL_TOKEN=.*$", "TUNNEL_TOKEN=test-dummy-token", text, flags=re.M)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.skipif(not _docker_compose_ok(), reason="docker compose not available")
@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_compose_config_validates(variant, dummy_env):
    files, profiles = VARIANTS[variant]
    cmd = ["docker", "compose", "-p", "jarvis-config-test", "--env-file", str(dummy_env)]
    for f in files:
        cmd += ["-f", str(f)]
    for p in profiles:
        cmd += ["--profile", p]
    res = subprocess.run(cmd + ["config", "--format", "json"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=SERVER)
    assert res.returncode == 0, res.stderr
    cfg = json.loads(res.stdout)
    want_driver = "nvidia" if LOCAL in files else "cdi"
    for name, svc in cfg["services"].items():
        devices = ((svc.get("deploy") or {}).get("resources") or {}).get("reservations", {}).get("devices") or []
        if name in GPU_SERVICES:
            # shared host: ASR/VieNeu on the CPU, no GPU device requests at all
            want = [] if SHARED_HOST in files else [want_driver]
            assert [d["driver"] for d in devices] == want, (name, devices)
        ports = svc.get("ports") or []
        if name != "xiaozhi" and DEBUG not in files:
            assert not ports, name
        for port in ports:
            if name != "xiaozhi":
                assert port.get("host_ip") == "127.0.0.1", (name, port)
    if profiles == ["tunnel"] and LOCALLY_MANAGED in files:
        assert "/etc/cloudflared/config.yml" in cfg["services"]["cloudflared"]["command"]


@pytest.mark.skipif(not _docker_compose_ok(), reason="docker compose not available")
def test_compose_config_fails_without_secrets(tmp_path):
    env = tmp_path / "missing.env"
    env.write_text("\n".join(line for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
                             if not line.startswith("XIAOZHI_AUTH_KEY=")), encoding="utf-8")
    res = subprocess.run(["docker", "compose", "-p", "jarvis-config-test", "--env-file", str(env), "-f", str(BASE),
                          "config", "-q"], capture_output=True, text=True, cwd=SERVER)
    assert res.returncode != 0
    assert "XIAOZHI_AUTH_KEY" in res.stderr

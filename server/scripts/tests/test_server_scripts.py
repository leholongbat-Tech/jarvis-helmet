"""Tests for the host/deploy scripts in server/scripts, server/cloudflared and server/ollama.

The scripts run under bash (Git Bash on Windows, never WSL's bash.exe) with stub executables
first on PATH: every command that could change a host (ssh, rsync, sudo, iptables, apt-get,
systemctl, crontab, usermod, curl, git, docker ...) is replaced by a stub that only logs its
arguments. The tests check that dry-runs never call them (except read-only queries) and that
the printed commands are the intended ones.

    .venv/Scripts/python -m pytest server/scripts/tests -q
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SERVER = REPO / "server"
SCRIPTS = SERVER / "scripts"
HOST_SCRIPTS = ["bootstrap_host.sh", "deploy.sh", "gpu_check.sh", "pull_models.sh", "firewall_lan.sh",
                "vram_report.sh"]
ALL_SHELL = sorted(SCRIPTS.glob("*.sh")) + [SERVER / "cloudflared" / "push_ingress.sh",
                                             SERVER / "ollama" / "entrypoint.sh"]
STUBBED = ["ssh", "rsync", "scp", "sudo", "iptables", "apt-get", "systemctl", "crontab", "usermod", "curl",
           "git", "tar", "docker", "nvidia-ctk", "nvidia-smi"]


def find_bash() -> str | None:
    if os.name == "nt":  # Git Bash; System32\bash.exe would run inside WSL
        candidates = [Path(r"C:\Program Files\Git\bin\bash.exe"), Path(r"C:\Program Files (x86)\Git\bin\bash.exe")]
        git = shutil.which("git")
        if git:
            candidates.append(Path(git).resolve().parents[1] / "bin" / "bash.exe")
        for cand in candidates:
            if cand.is_file():
                return str(cand)
        return None
    return shutil.which("bash")


BASH = find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash (Git Bash on Windows) not found")


def posix(path: Path) -> str:
    return str(path).replace("\\", "/")


class Stubs:
    """A directory of logging stub executables placed first on PATH."""

    def __init__(self, root: Path):
        self.dir = root / "stubs"
        self.dir.mkdir()
        self.log = root / "calls.log"
        self.log.write_text("", encoding="utf-8")
        for name in STUBBED:
            self.add(name, "exit 1")

    def add(self, name: str, body: str) -> None:
        path = self.dir / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "{name} $*" >> "$STUB_LOG"\n' + body + "\n",
            encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def calls(self) -> list[str]:
        return [line for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def env(self) -> dict:
        env = os.environ.copy()
        env["PATH"] = str(self.dir) + os.pathsep + env.get("PATH", "")
        env["JARVIS_TEST_STUBS"] = posix(self.dir)
        env["STUB_LOG"] = posix(self.log)
        env["NO_COLOR"] = "1"
        for key in ("JARVIS_DEPLOY_HOST", "JARVIS_DEPLOY_DIR", "JARVIS_SSH_OPTS", "DRY_RUN", "COMPOSE_FILE",
                    "COMPOSE_PROFILES", "CF_API_TOKEN", "CF_ACCOUNT_ID", "TUNNEL_ID", "JARVIS_HOSTNAME",
                    "CF_ZONE_ID"):
            env.pop(key, None)
        return env


@pytest.fixture
def stubs(tmp_path):
    return Stubs(tmp_path)


def test_stubs_shadow_real_binaries(tmp_path, stubs):
    """Canary: every stubbed command must resolve to the stub (no real ssh/curl/git/... in tests)."""
    probe = tmp_path / "probe.sh"
    probe.write_text('#!/usr/bin/env bash\nfor c in ' + " ".join(STUBBED) + '; do "$c" probe || true; done\n',
                     encoding="utf-8", newline="\n")
    res = run(probe, env=stubs.env())
    assert res.returncode == 0, res.stderr
    assert stubs.calls() == [f"{name} probe" for name in STUBBED]


# Git for Windows' bash.exe is a launcher that puts its own mingw64/bin and usr/bin (real curl, ssh,
# tar, git ...) in front of the inherited PATH, so the stub directory is prepended again inside bash.
STUB_PREFIX = ('d="$JARVIS_TEST_STUBS"; command -v cygpath >/dev/null 2>&1 && d="$(cygpath -u "$d")"; '
               'PATH="$d:$PATH"; export PATH; exec bash "$@"')


def run(script: Path, *args: str, env: dict | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    if env is None:
        env = os.environ.copy()
        env["NO_COLOR"] = "1"
    if env.get("JARVIS_TEST_STUBS"):
        cmd = [BASH, "-c", STUB_PREFIX, "bash", posix(script), *args]
    else:
        cmd = [BASH, posix(script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                          timeout=timeout)


def copy_tree(tmp_path: Path) -> Path:
    """Minimal copy of the repo layout the scripts expect (for tests that need their own .env)."""
    root = tmp_path / "repo"
    (root / "server" / "scripts").mkdir(parents=True)
    for f in SCRIPTS.glob("*.sh"):
        shutil.copy2(f, root / "server" / "scripts" / f.name)
    for name in ("docker-compose.yml", ".env.example"):
        shutil.copy2(SERVER / name, root / "server" / name)
    (root / "tools" / "tests" / "llm").mkdir(parents=True)
    (root / "tools" / "tasks_cli.py").write_text("# stub\n", encoding="utf-8")
    (root / "tools" / "tests" / "llm" / "run_tool_calls.py").write_text("# stub\n", encoding="utf-8")
    return root


# ---- static checks ------------------------------------------------------------------------------
@pytest.mark.parametrize("script", ALL_SHELL, ids=lambda p: p.name)
def test_bash_syntax(script):
    res = subprocess.run([BASH, "-n", posix(script)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("script", ALL_SHELL, ids=lambda p: p.name)
def test_lf_line_endings_and_strict_mode(script):
    data = script.read_bytes()
    assert b"\r" not in data, f"{script.name} has CRLF line endings"
    text = data.decode("utf-8")
    assert text.startswith("#!"), "missing shebang"
    if script.name not in ("_common.sh", "entrypoint.sh"):
        assert "set -euo pipefail" in text


def _docker_image_present(image: str) -> bool:
    if shutil.which("docker") is None:
        return False
    res = subprocess.run(["docker", "image", "inspect", image], capture_output=True)
    return res.returncode == 0


@pytest.mark.skipif(not _docker_image_present("koalaman/shellcheck:stable"),
                    reason="docker or image koalaman/shellcheck:stable not available")
def test_shellcheck_clean():
    rel = [posix(p.relative_to(REPO)) for p in ALL_SHELL]
    res = subprocess.run(["docker", "run", "--rm", "-v", f"{REPO}:/mnt", "-w", "/mnt", "koalaman/shellcheck:stable",
                          "-x", *rel], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    assert res.returncode == 0, res.stdout + res.stderr


FORBIDDEN = re.compile(
    r"""^\s*(?:run(?:_sh)?\s+)?["']?(?:\$\{SUDO\[@\]\}"?\s+)?(?:sudo\s+)?
        (?:systemctl\s+(?:restart|stop|reload|daemon-reload)\s*(?:docker|$)|systemctl\s+daemon-reload
          |nvidia-ctk\s+runtime\s+configure|ufw\s+(?:enable|--force|default))""", re.X)


@pytest.mark.parametrize("script", ALL_SHELL, ids=lambda p: p.name)
def test_never_restarts_dockerd_or_touches_ufw(script):
    for n, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        assert not FORBIDDEN.search(line), f"{script.name}:{n}: {line.strip()}"


@pytest.mark.parametrize("script", ALL_SHELL, ids=lambda p: p.name)
def test_no_credentials_in_scripts(script):
    text = script.read_text(encoding="utf-8")
    assert not re.search(r"sk-ant-[A-Za-z0-9]", text)
    assert not re.search(r"\beyJ[A-Za-z0-9_-]{20,}", text), "looks like a tunnel token"
    assert not re.search(r"cfast_[A-Za-z0-9]{20,}", text), "looks like a CF Access secret"
    assert not re.search(r"(?i)password\s*=\s*\S+", text)


# ---- --help / bad options -----------------------------------------------------------------------
@pytest.mark.parametrize("name", HOST_SCRIPTS)
def test_help(name, stubs):
    res = run(SCRIPTS / name, "--help", env=stubs.env())
    assert res.returncode == 0, res.stderr
    assert "Usage:" in res.stdout
    assert stubs.calls() == []


@pytest.mark.parametrize("name", HOST_SCRIPTS)
def test_unknown_option_fails(name, stubs):
    res = run(SCRIPTS / name, "--definitely-not-an-option", env=stubs.env())
    assert res.returncode != 0
    assert "unknown option" in res.stderr


# ---- deploy.sh ----------------------------------------------------------------------------------
def test_deploy_dry_run_tar(stubs):
    res = run(SCRIPTS / "deploy.sh", "--method", "tar", "--profiles", "tunnel english-tts", "--pull-models",
              env=stubs.env())
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    assert "DRY-RUN" in out
    assert "--exclude=.env" in out and "--exclude=server/tests" in out
    assert "--exclude=server/docker-compose.override.yml" in out
    assert "tools/tasks_cli.py tools/tests/llm" in out
    assert "ssh -o BatchMode=yes batai" in out
    assert "docker compose pull --ignore-buildable --quiet" in out
    assert "docker compose build --pull" in out
    assert "docker compose up -d --remove-orphans" in out
    assert "COMPOSE_PROFILES=tunnel" in out and "english-tts" in out
    assert "scripts/pull_models.sh --apply" in out
    # docker is stubbed (compose pre-check skipped); nothing else may run
    assert [c for c in stubs.calls() if not c.startswith("docker ")] == []


def test_deploy_dry_run_rsync_copy_only(stubs):
    env = stubs.env()
    env["JARVIS_DEPLOY_HOST"] = "jarvis@10.0.0.5"
    env["JARVIS_DEPLOY_DIR"] = "/srv/jarvis"
    res = run(SCRIPTS / "deploy.sh", "--method", "rsync", "--copy-only", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "rsync -az --delete" in res.stdout
    assert "--exclude=/tests/" in res.stdout and "--exclude=.env" in res.stdout
    assert "--exclude=/docker-compose.override.yml " in res.stdout  # host-local file, never deleted
    assert "jarvis@10.0.0.5:/srv/jarvis/server/" in res.stdout
    assert "docker compose" not in res.stdout
    assert [c for c in stubs.calls() if not c.startswith("docker ")] == []


def test_deploy_archive_only_reads_git(stubs):
    stubs.add("git", "exit 0")
    res = run(SCRIPTS / "deploy.sh", "--method", "archive", "--ref", "main", "--no-build", env=stubs.env())
    assert res.returncode == 0, res.stdout + res.stderr
    assert "archive --format=tar.gz main server" in res.stdout
    assert "docker compose build" not in res.stdout
    calls = [c for c in stubs.calls() if not c.startswith("docker ")]
    assert calls and all(re.match(r"git -C .+ status --porcelain", c) for c in calls), calls


@pytest.mark.parametrize("args", [["--host", "bad host"], ["--dir", "relative/path"], ["--dir", "/opt/../etc"],
                                  ["--method", "ftp"], ["--profiles", "tunnel;rm"]])
def test_deploy_rejects_bad_arguments(args, stubs):
    res = run(SCRIPTS / "deploy.sh", *args, env=stubs.env())
    assert res.returncode != 0
    assert stubs.calls() == []


def test_deploy_refuses_crlf_scripts(tmp_path, stubs):
    root = copy_tree(tmp_path)
    (root / "server" / "broken.sh").write_bytes(b"#!/bin/sh\r\necho hi\r\n")
    res = run(root / "server" / "scripts" / "deploy.sh", "--method", "tar", env=stubs.env())
    assert res.returncode != 0
    assert "CRLF" in res.stderr and "broken.sh" in res.stderr
    assert stubs.calls() == []


# ---- bootstrap_host.sh --------------------------------------------------------------------------
def _gpu_host_stubs(stubs: Stubs, cdi_list: str = "nvidia.com/gpu=0\nnvidia.com/gpu=all") -> None:
    stubs.add("docker", r'''
case "$1 $2" in
  "version --format") echo 29.8.2 ;;
  "info --format")
    case "$3" in
      *CDISpecDirs*) echo '["/etc/cdi","/var/run/cdi"]' ;;
      *DefaultRuntime*) echo runc ;;
      *) echo null ;;
    esac ;;
  "compose version") echo 2.40.0 ;;
  run\ *) echo "GPU 0: NVIDIA GeForce RTX 5090 (UUID: GPU-test)" ;;
  *) exit 1 ;;
esac''')
    stubs.add("nvidia-smi", 'echo "0, NVIDIA GeForce RTX 5090, 595.84, 1200, 32607"')
    stubs.add("nvidia-ctk", 'if [ "$1" = "--version" ]; then echo "NVIDIA Container Toolkit CLI version 1.20.1"; '
                            f'elif [ "$1 $2" = "cdi list" ]; then printf "{cdi_list}\\n"; else exit 1; fi')
    stubs.add("systemctl", 'case "$1" in cat) exit 0 ;; is-enabled) echo disabled ;; is-active) echo inactive ;; '
                           '*) exit 1 ;; esac')


def test_bootstrap_dry_run_changes_nothing(stubs):
    _gpu_host_stubs(stubs)
    res = run(SCRIPTS / "bootstrap_host.sh", "--user", "jarvisdeploy", "--deploy-dir", "/opt/jarvis-test",
              env=stubs.env())
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    assert "DRY-RUN" in out
    assert "usermod -aG docker jarvisdeploy" in out
    assert "systemctl restart nvidia-cdi-refresh.service" in out
    assert "systemctl enable --no-reload nvidia-cdi-refresh.path" in out
    assert "install -d -m 0750 -o jarvisdeploy -g jarvisdeploy /opt/jarvis-test" in out
    assert "gpu_check.sh --mode cdi" in out
    for call in stubs.calls():
        name, _, rest = call.partition(" ")
        assert name in ("docker", "nvidia-smi", "nvidia-ctk", "systemctl"), call
        if name == "systemctl":
            assert rest.split()[0] in ("cat", "is-enabled", "is-active"), call
        if name == "docker":
            assert rest.split()[0] in ("version", "info", "compose"), call


def test_bootstrap_rejects_bad_user(stubs):
    res = run(SCRIPTS / "bootstrap_host.sh", "--user", "Bad User", env=stubs.env())
    assert res.returncode != 0


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="--apply must never run on a Linux test machine")
def test_bootstrap_apply_refuses_non_linux(stubs):
    _gpu_host_stubs(stubs)
    res = run(SCRIPTS / "bootstrap_host.sh", "--apply", "--user", "jarvisdeploy", env=stubs.env())
    assert res.returncode != 0
    assert "blocking problem" in res.stderr
    assert not any(c.startswith(("sudo", "apt-get", "usermod")) for c in stubs.calls())


# ---- firewall_lan.sh ----------------------------------------------------------------------------
def test_firewall_dry_run_rules(stubs):
    res = run(SCRIPTS / "firewall_lan.sh", "--subnet", "192.168.1.0/24", env=stubs.env())
    assert res.returncode == 0, res.stdout + res.stderr
    lines = [ln for ln in res.stdout.splitlines() if "[dry-run]" in ln]
    for port in ("8100", "8003"):
        drop = [i for i, ln in enumerate(lines) if f"--ctorigdstport {port}" in ln and "-j DROP" in ln]
        ret = [i for i, ln in enumerate(lines) if f"--ctorigdstport {port}" in ln and "-j RETURN" in ln]
        assert len(drop) == 1 and len(ret) == 1
        assert drop[0] < ret[0], "RETURN must be inserted after DROP (so it ends up above it)"
        assert "-s 192.168.1.0/24" in lines[ret[0]]
    assert all("DOCKER-USER" in ln and "--comment jarvis-lan" in ln for ln in lines)
    read_only = ("sudo -n iptables -S DOCKER-USER", "iptables -S DOCKER-USER")  # user / root
    assert all(c in read_only for c in stubs.calls()), stubs.calls()


def test_firewall_reads_lan_ip_and_ports_from_env(tmp_path, stubs):
    root = copy_tree(tmp_path)
    (root / "server" / ".env").write_text("LAN_IP=10.1.2.3\nXIAOZHI_WS_PORT=9100\n", encoding="utf-8")
    res = run(root / "server" / "scripts" / "firewall_lan.sh", env=stubs.env())
    assert res.returncode == 0, res.stdout + res.stderr
    assert "-s 10.1.2.0/24" in res.stdout
    assert "--ctorigdstport 9100" in res.stdout and "--ctorigdstport 8003" in res.stdout


def test_firewall_persist_uses_crontab_not_systemd(stubs):
    res = run(SCRIPTS / "firewall_lan.sh", "--subnet", "192.168.1.0/24", "--persist", env=stubs.env())
    assert res.returncode == 0
    assert "crontab" in res.stdout and "@reboot" in res.stdout
    assert "systemctl" not in res.stdout
    assert not any(c.startswith("crontab") for c in stubs.calls())


@pytest.mark.parametrize("args", [["--subnet", "300.1.1.0/24"], ["--subnet", "10.0.0.0/33"],
                                  ["--subnet", "10.0.0.0/24", "--ports", "8100 70000"],
                                  ["--subnet", "10.0.0.0/24", "--ports", "abc"]])
def test_firewall_rejects_bad_arguments(args, stubs):
    res = run(SCRIPTS / "firewall_lan.sh", *args, env=stubs.env())
    assert res.returncode != 0


def test_firewall_needs_subnet_without_env(tmp_path, stubs):
    root = copy_tree(tmp_path)
    res = run(root / "server" / "scripts" / "firewall_lan.sh", env=stubs.env())
    assert res.returncode != 0 and "LAN_IP" in res.stderr


# ---- pull_models.sh -----------------------------------------------------------------------------
def test_pull_models_dry_run_uses_env_model(tmp_path, stubs):
    root = copy_tree(tmp_path)
    (root / "server" / ".env").write_text("OLLAMA_MODEL=qwen3:test-model\n", encoding="utf-8")
    res = run(root / "server" / "scripts" / "pull_models.sh", "--asr", env=stubs.env())
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    assert "compose up -d ollama" in out  # docker stub: ollama "not running"
    assert "ollama pull qwen3:test-model" in out
    assert "ollama run qwen3:test-model ''" in out
    assert "jarvis_asr.prefetch" in out
    assert all(c.startswith("docker compose ps") for c in stubs.calls()), stubs.calls()


def test_pull_models_extra_model_preloads_only_main(tmp_path, stubs):
    root = copy_tree(tmp_path)
    res = run(root / "server" / "scripts" / "pull_models.sh", "--model", "gemma4:26b-a4b-it-qat",
              env=stubs.env())
    assert res.returncode == 0
    assert "ollama pull gemma4:26b-a4b-it-qat" in res.stdout
    assert "ollama run qwen3:30b-a3b-instruct-2507-q4_K_M" in res.stdout  # default OLLAMA_MODEL


def test_pull_models_rejects_bad_tag(stubs):
    res = run(SCRIPTS / "pull_models.sh", "--model", "x;rm -rf /", env=stubs.env())
    assert res.returncode != 0


# ---- vram_report.sh -----------------------------------------------------------------------------
CID = {"ollama": "a" * 64, "asr": "b" * 64, "other": "c" * 64}


def _vram_fixture(tmp_path: Path, stubs: Stubs) -> dict:
    stubs.add("nvidia-smi", r'''
case "$*" in
  *--query-gpu*) echo "0, NVIDIA GeForce RTX 5090, 31000, 32607, 40" ;;
  *--query-compute-apps*) printf "101, /usr/bin/ollama, 20000\n202, python3, 4500\n303, vllm, 6000\n404, Xorg, [N/A]\n" ;;
esac''')
    stubs.add("docker", f'''
case "$1" in
  ps) printf "{CID['ollama']} jarvis-ollama-1\\n{CID['asr']} jarvis-asr-1\\n{CID['other']} other-vllm-1\\n" ;;
  exec) echo "NAME ID SIZE PROCESSOR CONTEXT UNTIL"; echo "qwen3:30b x 20 GB 100% GPU 8192 Forever" ;;
  *) exit 1 ;;
esac''')
    proc = tmp_path / "proc"
    for pid, cid in (("101", CID["ollama"]), ("202", CID["asr"]), ("303", CID["other"])):
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cgroup").write_text(f"0::/system.slice/docker-{cid}.scope\n", encoding="utf-8")
    (proc / "404").mkdir()
    (proc / "404" / "cgroup").write_text("0::/user.slice\n", encoding="utf-8")
    env = stubs.env()
    env["PROC_ROOT"] = posix(proc)
    return env


def test_vram_report_maps_processes(tmp_path, stubs):
    env = _vram_fixture(tmp_path, stubs)
    res = run(SCRIPTS / "vram_report.sh", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    out = res.stdout
    assert re.search(r"101\s+jarvis-ollama-1\s+20000", out)
    assert re.search(r"303\s+other-vllm-1\s+6000", out)
    assert re.search(r"404\s+host\s+\[N/A\]", out)
    assert "24500 MiB of a 28672 MiB budget" in out
    assert "other processes on the GPU: 6000 MiB" in out
    assert "100% GPU" in out
    assert "within budget" in out


def test_vram_report_strict_over_budget(tmp_path, stubs):
    env = _vram_fixture(tmp_path, stubs)
    res = run(SCRIPTS / "vram_report.sh", "--budget-gb", "20", "--strict", env=env)
    assert res.returncode == 1
    assert "exceed the budget" in res.stderr


# ---- gpu_check.sh -------------------------------------------------------------------------------
def test_gpu_check_passes_with_cdi(stubs):
    _gpu_host_stubs(stubs)
    res = run(SCRIPTS / "gpu_check.sh", env=stubs.env())
    assert res.returncode == 0, res.stdout + res.stderr
    assert "nvidia.com/gpu=all" in res.stdout
    assert "GPU 0: NVIDIA GeForce RTX 5090" in res.stdout
    assert any(c.startswith("docker run --rm --device nvidia.com/gpu=all") for c in stubs.calls())


def test_gpu_check_fails_without_cdi_spec(stubs):
    _gpu_host_stubs(stubs, cdi_list="nvidia.com/gpu=0")
    res = run(SCRIPTS / "gpu_check.sh", "--no-smoke", env=stubs.env())
    assert res.returncode == 1
    assert "bootstrap_host.sh" in res.stderr


def test_gpu_check_gpus_mode(stubs):
    _gpu_host_stubs(stubs)
    res = run(SCRIPTS / "gpu_check.sh", "--mode", "gpus", env=stubs.env())
    assert res.returncode == 0, res.stdout + res.stderr
    assert any(c.startswith("docker run --rm --gpus all") for c in stubs.calls())


# ---- push_ingress.sh ----------------------------------------------------------------------------
PUSH = SERVER / "cloudflared" / "push_ingress.sh"
CF_ENV = {"JARVIS_HOSTNAME": "jarvis.example.com", "CF_ACCOUNT_ID": "0123456789abcdef0123456789abcdef",
          "TUNNEL_ID": "12345678-1234-1234-1234-123456789abc", "CF_API_TOKEN": "tok-SECRET-123"}


def test_push_ingress_dry_run(stubs):
    env = {**stubs.env(), **CF_ENV}
    res = run(PUSH, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert '"hostname": "jarvis.example.com"' in res.stdout
    assert "${JARVIS_HOSTNAME}" not in res.stdout
    assert "cfd_tunnel/12345678-1234-1234-1234-123456789abc/configurations" in res.stdout
    assert "tok-SECRET-123" not in res.stdout + res.stderr
    assert stubs.calls() == []


@pytest.mark.parametrize("override", [{"JARVIS_HOSTNAME": ""}, {"TUNNEL_ID": "not-a-uuid"},
                                      {"CF_ACCOUNT_ID": "xyz"}, {"JARVIS_HOSTNAME": "Jarvis.Example.COM"}])
def test_push_ingress_rejects_bad_env(override, stubs):
    env = {**stubs.env(), **CF_ENV, **override}
    res = run(PUSH, env=env)
    assert res.returncode != 0
    assert stubs.calls() == []


def test_push_ingress_apply_keeps_token_off_the_command_line(stubs):
    # curl stub: answer {"success": true} into the -o file and record the header file content.
    stubs.add("curl", r'''
out=""; prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  case "$a" in @*) f="${a#@}"; if [ -f "$f" ] && grep -q Authorization "$f"; then echo "HEADERFILE $(cat "$f")" >> "$STUB_LOG"; fi ;; esac
  prev="$a"
done
[ -n "$out" ] && echo '{"success": true, "result": {}}' > "$out"
exit 0''')
    env = {**stubs.env(), **CF_ENV}
    res = run(PUSH, "--apply", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    curl_calls = [c for c in stubs.calls() if c.startswith("curl ")]
    assert len(curl_calls) == 1
    assert "tok-SECRET-123" not in curl_calls[0], "token must not be a curl argument"
    assert "-X PUT" in curl_calls[0]
    assert any(c == "HEADERFILE Authorization: Bearer tok-SECRET-123" for c in stubs.calls())


# ---- _common.sh helpers -------------------------------------------------------------------------
def _bash_common(code: str, env: dict | None = None) -> subprocess.CompletedProcess:
    script = f'source "{posix(SCRIPTS / "_common.sh")}"\n{code}\n'
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def test_env_get(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_bytes(
        b"# comment\nA=plain\nB=two words\nC=\"quoted value\"\nD='single'\nE=crlf\r\nA=last wins\nEMPTY=\n")
    p = posix(env_file)
    res = _bash_common(
        f'for k in A B C D E EMPTY; do printf "%s=[%s]\\n" "$k" "$(env_get "$k" "{p}")"; done; '
        f'env_get MISSING "{p}"; echo "missing_rc=$?"')
    assert "A=[last wins]" in res.stdout
    assert "B=[two words]" in res.stdout
    assert "C=[quoted value]" in res.stdout
    assert "D=[single]" in res.stdout
    assert "E=[crlf]" in res.stdout
    assert "EMPTY=[]" in res.stdout
    assert "missing_rc=1" in res.stdout


def test_crlf_files(tmp_path):
    (tmp_path / "ok.sh").write_bytes(b"#!/bin/sh\necho ok\n")
    (tmp_path / "bad.sh").write_bytes(b"#!/bin/sh\r\necho bad\r\n")
    (tmp_path / "Dockerfile").write_bytes(b"FROM x\r\n")
    (tmp_path / "notes.md").write_bytes(b"crlf is fine here\r\n")
    res = _bash_common(f'crlf_files "{posix(tmp_path)}" | sort')
    names = sorted(Path(line).name for line in res.stdout.splitlines())
    assert names == ["Dockerfile", "bad.sh"]


def test_squote_round_trip():
    res = _bash_common('s="it'"'"'s a \\"test\\" \\$HOME"; q="$(squote "$s")"; eval "r=$q"; '
                       '[ "$r" = "$s" ] && echo same')
    assert res.stdout.strip() == "same", res.stdout + res.stderr


def test_valid_ipv4_and_version_ge():
    res = _bash_common('for ip in 192.168.1.27 10.0.0.0 256.1.1.1 1.2.3 a.b.c.d; do '
                       'valid_ipv4 "$ip" && echo "$ip ok" || echo "$ip bad"; done; '
                       'version_ge 29.8.2 28.2 && echo v1; version_ge 2.24.3 2.24.4 || echo v2')
    out = res.stdout
    for line in ("192.168.1.27 ok", "10.0.0.0 ok", "256.1.1.1 bad", "1.2.3 bad", "a.b.c.d bad", "v1", "v2"):
        assert line in out, out


# ---- ollama/entrypoint.sh -----------------------------------------------------------------------
def _ollama_stub(stubs: Stubs, model_present: bool = True) -> None:
    show = "exit 0" if model_present else "exit 1"
    stubs.add("ollama", f'''
case "$1" in
  list) exit 0 ;;
  show) {show} ;;
  run) exit 0 ;;
  serve) sleep 1; exit 0 ;;
esac''')


def _wait_for(stubs: Stubs, predicate, timeout: float = 15.0) -> list[str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        calls = stubs.calls()
        if predicate(calls):
            return calls
        time.sleep(0.2)
    return stubs.calls()


def test_ollama_entrypoint_preloads_model(stubs):
    _ollama_stub(stubs)
    env = {**stubs.env(), "JARVIS_PRELOAD_MODEL": "qwen3:test"}
    res = run(SERVER / "ollama" / "entrypoint.sh", env=env)
    assert res.returncode == 0, res.stderr
    calls = _wait_for(stubs, lambda c: any(x.startswith("ollama run") for x in c))
    assert "ollama serve" in calls
    assert "ollama show qwen3:test" in calls
    assert "ollama run qwen3:test " in calls  # empty prompt argument


def test_ollama_entrypoint_skips_missing_model(stubs):
    _ollama_stub(stubs, model_present=False)
    env = {**stubs.env(), "JARVIS_PRELOAD_MODEL": "qwen3:absent"}
    run(SERVER / "ollama" / "entrypoint.sh", env=env)
    calls = _wait_for(stubs, lambda c: any(x.startswith("ollama show") for x in c))
    time.sleep(1.0)
    calls = stubs.calls()
    assert "ollama show qwen3:absent" in calls
    assert not any(c.startswith("ollama run") for c in calls)


def test_ollama_entrypoint_preload_disabled(stubs):
    _ollama_stub(stubs)
    env = {**stubs.env(), "JARVIS_PRELOAD_MODEL": "qwen3:test", "JARVIS_PRELOAD": "0"}
    res = run(SERVER / "ollama" / "entrypoint.sh", env=env)
    assert res.returncode == 0
    time.sleep(1.5)
    assert stubs.calls() == ["ollama serve"]

"""Dry-run tests for tools/wakeword/train.sh (no Docker, no GPU).

The trainer container runs as root, so the feature caches it creates on the bind-mounted
data dir are root-owned: removing them from the host as a non-root docker user fails and
aborted every retrain. The cleanup must happen inside the container.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TRAIN = Path(__file__).resolve().parents[1] / "train.sh"
CACHES = ("wake_word_samples_augmented", "personal_augmented_features",
          "reviewed_negative_features")


def find_bash() -> str | None:
    if os.name == "nt":  # Git Bash; System32\bash.exe would run inside WSL
        candidates = [Path(r"C:\Program Files\Git\bin\bash.exe"),
                      Path(r"C:\Program Files (x86)\Git\bin\bash.exe")]
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


def dry_run(tmp_path: Path, *extra: str) -> list[str]:
    data_dir = str(tmp_path / "mww").replace("\\", "/")
    result = subprocess.run(
        [BASH, str(TRAIN).replace("\\", "/"), "--dry-run", "--no-pull", "--data-dir", data_dir,
         *extra],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def docker_run_line(lines: list[str]) -> str:
    runs = [line for line in lines if line.startswith("[dry-run] docker run")]
    assert len(runs) == 1, lines
    return runs[0]


def test_feature_caches_are_removed_inside_the_container(tmp_path):
    lines = dry_run(tmp_path)
    # Nothing is deleted on the host (the caches belong to root there).
    assert not [line for line in lines if line.startswith("[dry-run] rm")], lines
    run = docker_run_line(lines)
    assert "FRESH=1" in run
    for cache in CACHES:
        assert f"/data/work/{cache}" in run
    assert "rm -rf /data/work/wake_word_samples_augmented" in run
    # Trained models are handed back to the calling user.
    assert "HOST_UID=" in run and "HOST_GID=" in run
    assert "chown -R" in run


def test_keep_features_skips_the_cleanup(tmp_path):
    run = docker_run_line(dry_run(tmp_path, "--keep-features"))
    assert "FRESH=0" in run

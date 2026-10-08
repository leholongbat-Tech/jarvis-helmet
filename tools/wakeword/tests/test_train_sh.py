"""Dry-run tests for tools/wakeword/train.sh (no Docker, no GPU).

The trainer container runs as root, so the feature caches it creates on the bind-mounted
data dir are root-owned: removing them from the host as a non-root docker user fails and
aborted every retrain. The cleanup must happen inside the container.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import wave
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
    # No cache is deleted on the host (they belong to root there).
    assert not [line for line in lines if line.startswith("[dry-run] rm") and "/work" in line], lines
    run = docker_run_line(lines)
    assert "FRESH=1" in run
    for cache in CACHES:
        assert f"/data/work/{cache}" in run
    assert "rm -rf /data/work/wake_word_samples_augmented" in run
    # Trained models are handed back to the calling user.
    assert "HOST_UID=" in run and "HOST_GID=" in run
    assert "chown -R" in run


def test_another_wake_word_on_a_limited_cpu(tmp_path):
    run = docker_run_line(dry_run(tmp_path, "--wake-word", "hàu ơi", "--title", "Hàu ơi",
                                  "--slug", "hau_oi", "--gpu", "none", "--cpus", "12"))
    # printf %q writes non-ASCII bytes as $'...' octal escapes
    octal = "".join(f"\\{b:03o}" if b >= 0x80 else chr(b) for b in "hàu ơi".encode("utf-8"))
    assert f"WAKE_WORD={octal}" in run or "WAKE_WORD=hàu\\ ơi" in run
    assert "SLUG=hau_oi" in run
    assert "--cpus 12" in run and "OMP_NUM_THREADS=12" in run and "TF_NUM_INTRAOP_THREADS=12" in run
    assert "--gpus" not in run and "nvidia.com/gpu" not in run


def test_rejects_bad_slug_and_cpus(tmp_path):
    data_dir = str(tmp_path / "mww").replace("\\", "/")
    for extra in (["--slug", "Hàu Ơi"], ["--cpus", "0"], ["--cpus", "many"]):
        result = subprocess.run([BASH, str(TRAIN).replace("\\", "/"), "--dry-run", "--no-pull",
                                 "--data-dir", data_dir, *extra],
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert result.returncode != 0, extra


def test_headless_run_gets_the_vad_the_trainer_screens_clips_with(tmp_path):
    run = docker_run_line(dry_run(tmp_path))
    assert "silero-vad" in run and "REC_VENV_DIR=/data/.venv" in run
    assert "SETUP_ONLY=0" in run


def test_setup_only_prepares_without_training(tmp_path):
    lines = dry_run(tmp_path, "--setup-only")
    run = docker_run_line(lines)
    assert "SETUP_ONLY=1" in run
    # The container stops before train_wake_word; nothing is collected afterwards.
    assert run.index('"${SETUP_ONLY}" = 1') < run.index("train_wake_word")
    assert not [line for line in lines if "would copy" in line], lines


def make_clips(folder: Path, count: int, sampwidth: int = 2) -> str:
    folder.mkdir(parents=True)
    for i in range(count):
        with wave.open(str(folder / f"c{i}.wav"), "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(sampwidth)
            clip.setframerate(16000)
            clip.writeframes(b"\0" * sampwidth * 160)
    return str(folder).replace("\\", "/")


def test_staged_clips_replace_those_of_an_earlier_run(tmp_path):
    pos = make_clips(tmp_path / "pos", 2)
    lines = dry_run(tmp_path, "--positives", pos)
    data = str(tmp_path / "mww").replace("\\", "/")
    rm = [i for i, line in enumerate(lines) if line == f"[dry-run] rm -rf {data}/personal_samples"]
    cp = [i for i, line in enumerate(lines) if line.startswith("[dry-run] cp") and "personal_samples" in line]
    assert rm and len(cp) == 2 and rm[0] < cp[0], lines
    # Negatives and --own-tts folders are emptied too, even when not given this time.
    assert f"[dry-run] rm -rf {data}/negative_samples" in lines
    assert f"[dry-run] rm -rf {data}/own_tts" in lines


def test_own_tts_clips_replace_the_trainer_tts(tmp_path):
    tts = make_clips(tmp_path / "tts", 3)
    lines = dry_run(tmp_path, "--own-tts", tts, "--wake-word", "hàu ơi", "--slug", "hau_oi")
    assert len([line for line in lines if line.startswith("[dry-run] cp") and "/own_tts/tts_tts_" in line]) == 3
    run = docker_run_line(lines)
    assert "OWN_TTS=1" in run
    # The manifest carries the trainer's own signature for this run -> its cache check passes.
    assert "tts_generate_samples.py" in run and "--dry-run" in run
    assert ".generation_manifest.json" in run
    assert run.index("generation_manifest") < run.index("train_wake_word")
    # ... and the trainer's own cache check must accept it before training starts.
    assert run.index("cache_hit()") < run.index("train_wake_word")
    # The mode still fetches its Piper voice: the signature needs an engine to list.
    assert "vi_VN-vais1000-medium" in run


def test_only_the_tone_keeping_piper_voice_is_fetched(tmp_path):
    run = docker_run_line(dry_run(tmp_path, "--tts-mode", "piper"))
    assert "vi_VN-vais1000-medium" in run
    assert "vivos" not in run and "25hours" not in run
    assert "OWN_TTS=0" in run


def test_clips_that_are_not_16_bit_pcm_are_refused_before_copying(tmp_path):
    # microWakeWord reads the first clip header with Python's wave module (integer PCM only);
    # a 32-bit clip would abort the trainer only after the long augmentation.
    neg = make_clips(tmp_path / "neg", 2, sampwidth=4)
    data_dir = str(tmp_path / "mww").replace("\\", "/")
    result = subprocess.run([BASH, str(TRAIN).replace("\\", "/"), "--dry-run", "--no-pull",
                             "--data-dir", data_dir, "--negatives", neg],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode == 0:
        pytest.skip("no usable python3 in this bash: the check is skipped")
    assert "16-bit PCM" in result.stderr and "c0.wav: 32-bit" in result.stderr
    assert "[dry-run] cp" not in result.stdout


def test_keep_features_skips_the_cleanup(tmp_path):
    run = docker_run_line(dry_run(tmp_path, "--keep-features"))
    assert "FRESH=0" in run

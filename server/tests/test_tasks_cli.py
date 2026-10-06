"""Tests for tools/tasks_cli.py (SV-D), run as a subprocess like a user or CI would."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "tools" / "tasks_cli.py"
TASKS_FILE = REPO / "server" / "tasks" / "tasks.yaml"


def cli(*args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("HELMET_TASKS_SCHEMA", None)
    proc = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True,
                          encoding="utf-8", env=env, cwd=cwd or REPO, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def test_validate_real_file():
    code, out, err = cli("validate")
    assert code == 0, err
    assert out.startswith("OK: ") and "7 tác vụ" in out
    assert "open_mask, close_mask, eyes_on, eyes_off" in out
    assert "đã kiểm theo schema" in out


def test_validate_broken_file_exits_1_with_line_numbers(tmp_path):
    bad = tmp_path / "tasks.yaml"
    text = TASKS_FILE.read_text(encoding="utf-8")
    text = text.replace("mask: {action: open, speed: 80}", "mask: {action: open, speed: 500}")
    text = text.replace("    description: \"Tắt đèn mắt", "    descriptoin: \"Tắt đèn mắt")
    bad.write_text(text, encoding="utf-8")
    code, out, err = cli("validate", "--file", bad)
    assert code == 1
    assert "không hợp lệ (3 lỗi)" in err
    lines = [l for l in err.splitlines() if l.startswith(str(bad))]
    assert len(lines) == 3                    # sorted by line number
    speed_line = next(i for i, l in enumerate(text.splitlines(), 1) if "speed: 500" in l)
    typo_line = next(i for i, l in enumerate(text.splitlines(), 1) if "descriptoin" in l)
    assert re.search(rf"tasks\.yaml:{speed_line}:\d+: task 'open_mask': invalid step #2: mask\.speed out of range", lines[0])
    assert re.search(rf"tasks\.yaml:{typo_line - 1}:\d+: task 'eyes_off': 'description' is required", lines[1])
    assert re.search(rf"tasks\.yaml:{typo_line}:\d+: task 'eyes_off': unknown field 'descriptoin' "
                     r"\(did you mean 'description'\?\)", lines[2])


def test_validate_yaml_syntax_error(tmp_path):
    bad = tmp_path / "tasks.yaml"
    bad.write_text("version: 1\ntasks:\n  a:\n    description: [unclosed\n", encoding="utf-8")
    code, _, err = cli("validate", "--file", bad, "--no-schema")
    assert code == 1
    assert re.search(r"tasks\.yaml:\d+:\d+: YAML syntax error", err)


def test_list_shows_tasks_and_quick_phrases():
    code, out, err = cli("list")
    assert code == 0, err
    for tid in ("open_mask", "close_mask", "eyes_on", "eyes_off", "combat_mode", "stealth_mode", "salute"):
        assert f"\n  {tid}  (task_{tid};" in out
    assert '"mở mặt nạ"' in out and '"open the mask"' in out and '"tắt đèn mắt"' in out
    assert "đồng bộ xuống mũ" in out
    assert "câu khớp nhanh: - (chỉ qua LLM)" in out        # combat_mode etc.


def test_list_json():
    code, out, _ = cli("list", "--json")
    assert code == 0
    rows = json.loads(out)
    assert [r["id"] for r in rows][:2] == ["open_mask", "close_mask"]
    assert rows[0]["function"] == "task_open_mask" and rows[0]["sync_to_device"] is True
    assert rows[0]["steps"] == json.loads(rows[0]["steps_json"])


def test_steps_prints_exact_device_json():
    code, out, err = cli("steps", "open_mask")
    assert code == 0
    assert out == '[{"eyes":{"effect":"fade_out","duration_ms":300}},{"mask":{"action":"open","speed":80}}]\n'
    assert "2 bước, ước tính 1300 ms, 88 byte" in err


def test_steps_vietnamese_is_not_escaped():
    code, out, _ = cli("steps", "combat_mode")
    assert code == 0
    assert '"text":"Hệ thống chiến đấu sẵn sàng"' in out


def test_steps_merged_and_pretty():
    code, out, err = cli("steps", "open_mask", "eyes_on")
    assert code == 0
    assert json.loads(out) == [{"eyes": {"effect": "fade_out", "duration_ms": 300}},
                               {"mask": {"action": "open", "speed": 80}},
                               {"eyes": {"effect": "power_up", "duration_ms": 1200}}]
    assert "ước tính 2500 ms" in err
    code, out, _ = cli("steps", "eyes_off", "--pretty")
    assert code == 0 and out.startswith("[\n  {\n")


def test_steps_unknown_id_exits_2():
    code, _, err = cli("steps", "fly_away")
    assert code == 2
    assert "không có tác vụ fly_away" in err and "open_mask" in err


def test_match_shows_normalization_and_score():
    code, out, _ = cli("match", "Jarvis, mở mặt nạ!")
    assert code == 0
    assert "chuẩn hóa          : jarvis mở mặt nạ" in out
    assert "bỏ wake word       : mở mặt nạ" in out
    assert "bản bỏ dấu         : mo mat na" in out
    assert re.search(r"100\.00\s+open_mask\s+\"mở mặt nạ\"", out)
    assert "KẾT QUẢ: khớp open_mask" in out


def test_match_distractor_and_multi_command():
    code, out, _ = cli("match", "mặt nạ này nặng không?")
    assert code == 3
    assert "không khớp (điểm cao nhất thấp hơn ngưỡng)" in out
    code, out, _ = cli("match", "mở mặt nạ rồi bật đèn mắt", "--threshold", "40")
    assert code == 3
    assert "câu có nhiều lệnh (từ nối 'rồi')" in out


def test_fixtures_command():
    code, out, _ = cli("fixtures")
    assert code == 0
    assert re.search(r"^(\d+)/\1 fixture đạt", out, re.M)


def test_fixtures_command_reports_failures(tmp_path):
    (tmp_path / "invalid_wrong.json").write_text(json.dumps({
        "name": "invalid_wrong", "steps": [{"delay": 100}], "valid": False, "error_step": 1,
        "error_contains": "delay", "estimated_ms": None, "note": "deliberately wrong"}), encoding="utf-8")
    code, out, _ = cli("fixtures", "--dir", tmp_path)
    assert code == 1
    assert "SAI  invalid_wrong.json: expected valid=False, got valid=True" in out


def test_help_runs():
    code, out, _ = cli("--help")
    assert code == 0 and "validate" in out and "match" in out

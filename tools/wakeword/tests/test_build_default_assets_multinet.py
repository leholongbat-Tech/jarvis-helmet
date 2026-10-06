"""Tests for the [jarvis] multi-command patch in firmware/scripts/build_default_assets.py (B1)."""
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "build_default_assets", REPO / "firmware" / "scripts" / "build_default_assets.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def test_single_command_is_unchanged():
    assert BUILD.build_custom_wake_word_commands("xiao tu dou", "小土豆") == [
        {"command": "xiao tu dou", "text": "小土豆", "action": "wake"}]


def test_single_command_keeps_exact_strings():
    # Upstream passes the strings through untouched; so does the fast path.
    assert BUILD.build_custom_wake_word_commands(" ni hao ", "Hi ")[0] == {
        "command": " ni hao ", "text": "Hi ", "action": "wake"}


def test_two_commands_pair_up():
    assert BUILD.build_custom_wake_word_commands("jia wei si;guan jia", "Jarvis;Quản gia") == [
        {"command": "jia wei si", "text": "Jarvis", "action": "wake"},
        {"command": "guan jia", "text": "Quản gia", "action": "wake"},
    ]


def test_missing_display_texts_repeat_the_last_one():
    commands = BUILD.build_custom_wake_word_commands("jia wei si; guan jia ; kuan za", "Jarvis;Quản gia")
    assert [c["command"] for c in commands] == ["jia wei si", "guan jia", "kuan za"]
    assert [c["text"] for c in commands] == ["Jarvis", "Quản gia", "Quản gia"]


def test_empty_segments_are_ignored_and_extra_texts_dropped(capsys):
    commands = BUILD.build_custom_wake_word_commands("guan jia;;", "Quản gia;Jarvis")
    assert commands == [{"command": "guan jia", "text": "Quản gia", "action": "wake"}]
    assert "extra custom wake word display" in capsys.readouterr().out


def test_empty_display_falls_back_to_commands():
    commands = BUILD.build_custom_wake_word_commands("a b;c d", ";")
    assert [c["text"] for c in commands] == ["a b", "c d"]


def test_no_command_is_an_error():
    with pytest.raises(ValueError):
        BUILD.build_custom_wake_word_commands(";", "x;y")


def test_sdkconfig_to_index_json(tmp_path):
    sdkconfig = tmp_path / "sdkconfig"
    sdkconfig.write_text(
        "CONFIG_USE_CUSTOM_WAKE_WORD=y\n"
        'CONFIG_CUSTOM_WAKE_WORD="jia wei si;guan jia"\n'
        'CONFIG_CUSTOM_WAKE_WORD_DISPLAY="Jarvis;Quản gia"\n'
        "CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=20\n", encoding="utf-8")
    config = BUILD.read_custom_wake_word_from_sdkconfig(str(sdkconfig))
    info = {"language": "cn", "duration": 3000, "threshold": config["threshold"],
            "commands": BUILD.build_custom_wake_word_commands(config["wake_word"], config["display"])}
    BUILD.generate_index_json(str(tmp_path), "srmodels.bin", None, None, multinet_model_info=info)
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["multinet_model"]["threshold"] == 0.2
    assert [c["command"] for c in index["multinet_model"]["commands"]] == ["jia wei si", "guan jia"]
    assert [c["text"] for c in index["multinet_model"]["commands"]] == ["Jarvis", "Quản gia"]

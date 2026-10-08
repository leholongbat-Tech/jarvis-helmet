"""Unit tests for server/tasks/taskslib.py (SV-D): pure, no xiaozhi runtime needed."""
import json
import random
import re
import sys
import unicodedata
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO / "server" / "tasks"
TASKS_FILE = TASKS_DIR / "tasks.yaml"
SCHEMA_FILE = TASKS_DIR / "schema.json"
FIXTURES_DIR = REPO / "tools" / "tests" / "fixtures" / "sequences"
if str(TASKS_DIR) not in sys.path:
    sys.path.insert(0, str(TASKS_DIR))

import taskslib  # noqa: E402

jsonschema = pytest.importorskip("jsonschema")
CJK = re.compile(r"[\u3400-\u9fff\uff00-\uffef]")


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def table(schema):
    return taskslib.load_tasks(str(TASKS_FILE), schema=schema)


def parse(text, schema=None):
    return taskslib.parse_tasks(text, "t.yaml", schema)


def errors_of(text, schema=None):
    with pytest.raises(taskslib.TasksError) as info:
        parse(text, schema)
    return info.value


MINIMAL = """\
version: 1
tasks:
  open_mask:
    description: "Mở mặt nạ / open the mask"
    quick_phrases: ["mở mặt nạ", "open mask"]
    reply: "Đã mở mặt nạ, thưa ngài."
    steps:
      - eyes: {effect: fade_out, duration_ms: 300}
      - mask: {action: open, speed: 80}
"""


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("  Mở   MẶT nạ!!! ", "mở mặt nạ"),
    ("Jarvis… mở—mặt «nạ»?", "jarvis mở mặt nạ"),
    ("“Open”, the MASK.", "open the mask"),
    ("that's all", "thats all"),
    ("đèn\tmắt\nbật", "đèn mắt bật"),
    ("50% pin", "50 pin"),
    ("mở\u200bmặt nạ", "mở mặt nạ"),
    ("", ""),
])
def test_normalize_basic(raw, expected):
    assert taskslib.normalize_basic(raw) == expected


def test_normalize_basic_turns_nfd_into_nfc():
    nfd = unicodedata.normalize("NFD", "MỞ MẶT NẠ")
    out = taskslib.normalize_basic(nfd)
    assert out == "mở mặt nạ"
    assert unicodedata.is_normalized("NFC", out)


@pytest.mark.parametrize("raw, expected", [
    ("mở mặt nạ", "mo mat na"),
    ("đóng mặt nạ lại", "dong mat na lai"),
    ("bật đèn mắt", "bat den mat"),
    ("tắt", "tat"),
    ("ơi ư ữ ự", "oi u u u"),
    ("Đà Nẵng", "Da Nang"),
    (unicodedata.normalize("NFD", "quảng gia"), "quang gia"),
])
def test_strip_accents(raw, expected):
    assert taskslib.strip_accents(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Jarvis, mở mặt nạ.", "mở mặt nạ"),
    ("jarvis mở mặt nạ", "mở mặt nạ"),
    ("JARVIS! Open the mask", "open the mask"),
    ("Hey Jarvis, open the mask!", "open the mask"),
    ("ok jarvis bật đèn mắt", "bật đèn mắt"),
    ("Này Jarvis ơi, tắt đèn mắt", "tắt đèn mắt"),
    ("Jarvis ơi, đóng mặt nạ", "đóng mặt nạ"),
    ("Giắc vít, mở mặt nạ", "mở mặt nạ"),
    ("Giắc-vít ơi mở mặt nạ", "mở mặt nạ"),
    ("giắcvít mở mặt nạ", "mở mặt nạ"),
    ("Quản gia, đóng mặt nạ", "đóng mặt nạ"),
    ("Quảng gia ơi đóng mặt nạ", "đóng mặt nạ"),
    ("quảngia mở mặt nạ", "mở mặt nạ"),
    ("quan gia mo mat na", "mo mat na"),            # ASR without diacritics
    ("Hàu ơi, mở mặt nạ", "mở mặt nạ"),
    ("Này hàu ơi bật đèn mắt", "bật đèn mắt"),
    ("Hậu ơi, đóng mặt nạ", "đóng mặt nạ"),          # ASR spellings of "hàu ơi"
    ("hào ơi mở mặt nạ", "mở mặt nạ"),
    ("hầu như mọi người", "hầu như mọi người"),      # "hầu" without "ơi" is a word
    ("hàu nướng ngon không", "hàu nướng ngon không"),
    ("jarvis jarvis mở mặt nạ", "mở mặt nạ"),
    ("mở mặt nạ đi jarvis", "mở mặt nạ đi"),          # trailing wake word
    ("mở mặt nạ đi Jarvis ơi", "mở mặt nạ đi"),
    ("Jarvis", ""),
    ("hey jarvis", ""),
    ("ok mở mặt nạ", "ok mở mặt nạ"),                 # interjection alone is kept
    ("mở mặt nạ cho jarvis xem", "mở mặt nạ cho jarvis xem"),   # not at an edge
    ("gia vị này ngon", "gia vị này ngon"),           # not a wake word
])
def test_wake_word_stripping(raw, expected):
    assert taskslib.normalize_text(raw) == expected


def test_extra_wake_words():
    assert taskslib.normalize_text("Friday, mở mặt nạ") == "friday mở mặt nạ"
    assert taskslib.normalize_text("Friday, mở mặt nạ", wake_words=["friday"]) == "mở mặt nạ"


def test_detect_lang_and_pick_reply():
    assert taskslib.detect_lang("open the mask") == "en"
    assert taskslib.detect_lang("mở mặt nạ") == "vi"
    assert taskslib.detect_lang("Jarvis, open mask rồi bật đèn") == "vi"
    assert taskslib.detect_lang("") == "vi"
    reply = {"vi": "Đã mở.", "en": "Opened."}
    assert taskslib.pick_reply(reply, "en") == "Opened."
    assert taskslib.pick_reply(reply, "vi") == "Đã mở."
    assert taskslib.pick_reply({"en": "Opened."}, "vi") == "Opened."
    assert taskslib.pick_reply("Đã mở.", "en") == "Đã mở."
    assert taskslib.pick_reply(None, "vi") is None


def test_similarity_fallback_matches_rapidfuzz():
    rapidfuzz = pytest.importorskip("rapidfuzz")
    rng = random.Random(1234)
    alphabet = "aăâbcdđeêghiklmnoôơpqrstuưvxy ạảãáàệởợữ"
    pairs = [("mở mặt na", "mở mặt nạ"), ("tát mặt", "tắt mắt"), ("", ""), ("", "a"),
             ("open the mask", "open the door")]
    for _ in range(300):
        a = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 25)))
        b = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 25)))
        pairs.append((a, b))
    for a, b in pairs:
        assert taskslib._lcs_ratio(a, b) == pytest.approx(rapidfuzz.fuzz.ratio(a, b), abs=1e-9), (a, b)


# ---------------------------------------------------------------------------
# Quick-phrase matching on the real tasks.yaml
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("utterance, task_id", [
    ("Jarvis, mở mặt nạ.", "open_mask"),
    ("mở mặt nạ", "open_mask"),
    ("Mở mặt nạ ra!", "open_mask"),
    ("Quản gia ơi, mở mặt nạ đi.", "open_mask"),
    ("Hàu ơi, mở mặt nạ đi.", "open_mask"),
    ("Hey Jarvis, open the mask.", "open_mask"),
    ("OPEN MASK", "open_mask"),
    ("mở mặt na", "open_mask"),        # ASR tone error: accent-free form matches
    ("mo mat na", "open_mask"),        # ASR without diacritics
    ("Giắc vít, đóng mặt nạ lại.", "close_mask"),
    ("close the mask", "close_mask"),
    ("hạ mặt nạ xuống", "close_mask"),
    ("Jarvis, bật đèn mắt lên", "eyes_on"),
    ("eyes on", "eyes_on"),
    ("turn on the eyes", "eyes_on"),
    ("Quảng gia, tắt đèn mắt", "eyes_off"),
    ("tắt mắt", "eyes_off"),
    ("Eyes off!", "eyes_off"),
    ("turn off your eyes", "eyes_off"),
])
def test_quick_phrase_matches(table, utterance, task_id):
    match = taskslib.match_quick_phrase(utterance, table)
    assert match is not None, taskslib.analyze_quick_phrase(utterance, table)
    assert match.task_id == task_id
    assert match.score >= 90


DISTRACTORS = [
    "mặt nạ này nặng không?",
    "Jarvis, mặt nạ này nặng không?",
    "mở mặt nạ có tốn pin không?",
    "làm sao để mở mặt nạ?",
    "đừng mở mặt nạ",
    "không mở mặt nạ",
    "mặt nạ đóng chưa?",
    "mặt nạ của tôi đâu rồi",
    "tôi muốn mua mặt nạ mới",
    "tắt đèn phòng khách",
    "bật nhạc lên",
    "đèn mắt đẹp quá",
    "mắt tôi bị mờ",
    "tát mặt",
    "Jarvis, mấy giờ rồi?",
    "thời tiết hôm nay thế nào",
    "kể chuyện cười đi",
    "open the door",
    "close the window",
    "what is a mask",
    "my eyes are tired",
    "mask open?",
    "close mask later",
    "Quản gia ơi, hôm nay thứ mấy?",
    "Jarvis",
]


@pytest.mark.parametrize("utterance", DISTRACTORS)
def test_distractors_do_not_match(table, utterance):
    analysis = taskslib.analyze_quick_phrase(utterance, table)
    assert analysis.match is None, analysis


def test_at_least_twenty_distractors():
    assert len(DISTRACTORS) >= 20


@pytest.mark.parametrize("utterance", [
    "mở mặt nạ rồi bật đèn mắt",
    "Jarvis, mở mặt nạ rồi bật đèn mắt lên",
    "đóng mặt nạ xong tắt đèn mắt",
    "mở mặt nạ và bật đèn mắt",
    "open mask and eyes on",
    "open the mask then eyes on",
    "mo mat na roi bat den mat",
])
def test_multi_command_never_matches(table, utterance):
    assert taskslib.match_quick_phrase(utterance, table) is None
    # Even with a very low threshold the connector rule rejects it.
    analysis = taskslib.analyze_quick_phrase(utterance, table, threshold=30)
    assert analysis.match is None
    assert analysis.reason == "multi_command"
    assert analysis.connector is not None


def test_connector_without_second_part_is_not_multi_command(table):
    assert taskslib.find_command_join("mở mặt nạ rồi") is None
    assert taskslib.find_command_join("rồi mở mặt nạ") is None
    assert taskslib.find_command_join("mở mặt nạ rồi bật đèn") == "rồi"


def test_connector_inside_phrase_is_allowed():
    text = MINIMAL.replace('["mở mặt nạ", "open mask"]', '["rock and roll", "mở mặt nạ"]')
    t = parse(text)
    match = taskslib.match_quick_phrase("Jarvis, rock and roll!", t)
    assert match is not None and match.task_id == "open_mask"


def test_threshold_behaviour(table):
    utterance = "mở mặt nạ đi nhé"   # extra particle: ~86 points
    analysis = taskslib.analyze_quick_phrase(utterance, table)
    assert analysis.match is None and analysis.reason == "below_threshold"
    best = analysis.candidates[0][0]
    assert 80 <= best < 90
    assert taskslib.match_quick_phrase(utterance, table, threshold=best).task_id == "open_mask"
    assert taskslib.match_quick_phrase(utterance, table, threshold=best + 0.01) is None
    # threshold 100 = exact match after normalization only
    assert taskslib.match_quick_phrase("Jarvis, MỞ MẶT NẠ!", table, threshold=100).task_id == "open_mask"
    assert taskslib.match_quick_phrase("mở mặt na", table, threshold=100).form == "unaccented"


def test_accent_floor_blocks_accent_free_collisions(table):
    # "tát mặt" (slap face) and "tắt mắt" (eyes off) are equal without diacritics.
    analysis = taskslib.analyze_quick_phrase("tát mặt", table)
    assert analysis.match is None
    assert analysis.candidates[0][0] < 75
    # Lowering the floor would let the accent-free form win (documents the knob).
    assert taskslib.match_quick_phrase("tát mặt", table, accent_floor=70).task_id == "eyes_off"


def test_ambiguous_scores_are_rejected(table):
    analysis = taskslib.analyze_quick_phrase("ặt đèn mắt", table)
    assert analysis.reason == "ambiguous"
    assert analysis.match is None
    assert taskslib.match_quick_phrase("ặt đèn mắt", table, margin=0) is not None


def test_match_reports_language_of_phrase(table):
    assert taskslib.match_quick_phrase("open the mask", table).lang == "en"
    assert taskslib.match_quick_phrase("mở mặt nạ", table).lang == "vi"


def test_empty_and_no_phrases():
    t = parse(MINIMAL.replace('    quick_phrases: ["mở mặt nạ", "open mask"]\n', ""))
    assert taskslib.analyze_quick_phrase("mở mặt nạ", t).reason == "no_phrases"
    assert taskslib.analyze_quick_phrase("Jarvis!", t).reason == "empty"


# ---------------------------------------------------------------------------
# tasks.yaml loading, line numbers
# ---------------------------------------------------------------------------
def test_minimal_file_parses():
    t = parse(MINIMAL)
    task = t.tasks["open_mask"]
    assert task.steps_json == '[{"eyes":{"effect":"fade_out","duration_ms":300}},{"mask":{"action":"open","speed":80}}]'
    assert task.estimated_ms == 1300
    assert task.line == 3
    assert task.reply == "Đã mở mặt nạ, thưa ngài."
    assert [e.phrase for e in t.phrase_index] == ["mở mặt nạ", "open mask"]


def test_yaml_syntax_error_has_line_number():
    broken = MINIMAL.replace("      - mask: {action: open, speed: 80}", "      - mask: {action: open, speed: 80")
    err = errors_of(broken)
    assert len(err.issues) == 1
    issue = err.issues[0]
    assert "YAML syntax error" in issue.message
    assert issue.line in (9, 10)
    assert err.format().startswith("t.yaml:")


def test_bad_indentation_reports_line():
    broken = MINIMAL.replace("    reply:", "   reply:")
    err = errors_of(broken)
    assert err.issues[0].line == 6


def test_tab_character_reports_line():
    broken = MINIMAL.replace("    reply:", "\treply:")
    err = errors_of(broken)
    assert err.issues[0].line == 6


def test_step_error_points_to_field_line():
    broken = MINIMAL.replace("speed: 80}", "speed: 5}")
    err = errors_of(broken)
    assert len(err.issues) == 1
    issue = err.issues[0]
    assert issue.line == 9
    assert "invalid step #2" in issue.message and "speed" in issue.message
    assert "task 'open_mask'" in issue.message
    assert re.match(r"t\.yaml:9:\d+: task 'open_mask': invalid step #2: mask\.speed", err.format())


def test_block_style_step_error_line():
    text = """\
version: 1
tasks:
  combat_mode:
    description: "Combat"
    steps:
      - mask:
          action: close
          speed: 90
      - eyes:
          effect: rainbow
          duration_ms: 800
"""
    err = errors_of(text)
    assert err.issues[0].line == 10
    assert "invalid step #2" in err.issues[0].message and "effect" in err.issues[0].message


def test_duplicate_task_id_reports_both_lines():
    text = MINIMAL + """\
  open_mask:
    description: "again"
    steps:
      - delay: 100
"""
    err = errors_of(text)
    dup = [i for i in err.issues if "duplicate key 'open_mask'" in i.message]
    assert dup and dup[0].line == 10 and "line 3" in dup[0].message


def test_task_id_with_trailing_newline_is_rejected():
    # Python's "$" also matches before a final "\n": ids must be checked with fullmatch (D29).
    text = MINIMAL + """\
  "abc\\n":
    description: "trailing newline in the id"
    steps:
      - delay: 100
"""
    err = errors_of(text)
    assert any("abc" in i.message for i in err.issues), [i.message for i in err.issues]


def test_sound_name_with_trailing_newline_is_rejected():
    with pytest.raises(taskslib.StepError) as info:
        taskslib.validate_steps([{"sound": {"name": "beep\n"}}])
    assert info.value.index == 1 and "sound" in info.value.message


def test_unknown_field_suggests_fix():
    err = errors_of(MINIMAL.replace("quick_phrases:", "quick_phrase:"))
    msg = err.issues[0].message
    assert "unknown field 'quick_phrase'" in msg and "did you mean 'quick_phrases'" in msg
    assert err.issues[0].line == 5


def test_on_key_and_yaml11_values_load():
    # Key 'on' must stay the string "on" (YAML 1.1 would make it True); the value 'off' is false.
    text = MINIMAL.replace("      - mask: {action: open, speed: 80}",
                           "      - eyes: {on: true, brightness: 100}\n      - output: {on: off}")
    t = parse(text)
    assert json.loads(t.tasks["open_mask"].steps_json)[1:] == [
        {"eyes": {"on": True, "brightness": 100}}, {"output": {"on": False}}]


def test_sync_to_device_needs_short_id():
    text = MINIMAL.replace("open_mask:", "open_the_mask:").replace(
        "    steps:", "    sync_to_device: true\n    steps:")
    err = errors_of(text)
    assert "at most 11 characters" in err.issues[0].message
    assert err.issues[0].line == 7


def test_quick_phrase_conflicts_between_tasks():
    text = MINIMAL + """\
  close_mask:
    description: "Đóng mặt nạ"
    quick_phrases: ["Mở mặt nạ!"]
    steps:
      - mask: {action: close}
"""
    err = errors_of(text)
    assert "already used by task 'open_mask'" in err.issues[0].message
    assert err.issues[0].line == 12


def test_quick_phrase_only_wake_word_is_rejected():
    err = errors_of(MINIMAL.replace('"open mask"]', '"Jarvis!"]'))
    assert "empty after normalization" in err.issues[0].message


def test_duplicate_quick_phrase_in_task():
    err = errors_of(MINIMAL.replace('"open mask"]', '"Mở mặt nạ."]'))
    assert "duplicate quick phrase" in err.issues[0].message


@pytest.mark.parametrize("text, needle", [
    ("", "file is empty"),
    ("- a\n- b\n", "top level must be a mapping"),
    ("version: 2\ntasks: {}\n", "version must be 1"),
    ("tasks: {}\n", "missing 'version: 1'"),
    ("version: 1\n", "missing 'tasks'"),
    ("version: 1\ntasks:\n", "tasks must be a mapping"),
    ("version: 1\ntasks: {}\nextra: 1\n", "unknown top-level key 'extra'"),
    ("version: 1\ntasks:\n  Open-Mask:\n    description: x\n    steps: [{delay: 1}]\n", "must match [a-z0-9_]+"),
    ("version: 1\ntasks:\n  " + "a" * 60 + ":\n    description: x\n    steps: [{delay: 1}]\n", "too long"),
    ("version: 1\ntasks:\n  a:\n    steps: [{delay: 1}]\n", "'description' is required"),
    ("version: 1\ntasks:\n  a:\n    description: x\n", "'steps' is required"),
    ("version: 1\ntasks:\n  a:\n    description: x\n    steps: []\n", "at least one step"),
    ("version: 1\ntasks:\n  a:\n    description: '  '\n    steps: [{delay: 1}]\n", "must not be empty"),
    ("version: 1\ntasks:\n  a:\n    description: x\n    reply: {fr: bonjour}\n    steps: [{delay: 1}]\n", "unknown language 'fr'"),
    ("version: 1\ntasks:\n  a:\n    description: x\n    reply: 5\n    steps: [{delay: 1}]\n", "reply must be a string"),
    ("version: 1\ntasks:\n  a:\n    description: x\n    sync_to_device: maybe\n    steps: [{delay: 1}]\n", "sync_to_device must be true or false"),
    ("version: 1\ntasks:\n  a:\n    description: x\n    quick_phrases: hello\n    steps: [{delay: 1}]\n", "quick_phrases must be a list"),
    ("version: 1\ntasks:\n  a:\n    description: x\n    steps: [{delay: 1.5}]\n", "invalid step #1: delay must be an integer"),
])
def test_document_errors(text, needle, schema):
    err = errors_of(text, schema)
    assert any(needle in issue.message for issue in err.issues), err.format()


def test_reply_forms():
    t = parse(MINIMAL.replace('reply: "Đã mở mặt nạ, thưa ngài."',
                              'reply: {vi: "Đã mở mặt nạ.", en: "Mask open."}'))
    assert t.tasks["open_mask"].reply == {"vi": "Đã mở mặt nạ.", "en": "Mask open."}


def test_merge_keys_and_override_are_supported():
    text = """\
version: 1
base: &slow {action: open, speed: 20}
tasks:
  a:
    description: x
    steps:
      - mask: {<<: *slow, speed: 90}
"""
    err = errors_of(text)    # 'base' is an unknown top-level key, nothing else
    assert [i.message for i in err.issues] == ["unknown top-level key 'base'"]
    text = text.replace("base: &slow {action: open, speed: 20}\n", "").replace(
        "      - mask: {<<: *slow, speed: 90}", "      - mask: &m {action: open, speed: 20}\n      - mask: {<<: *m, speed: 90}")
    t = parse(text)
    assert json.loads(t.tasks["a"].steps_json) == [{"mask": {"action": "open", "speed": 20}},
                                                   {"mask": {"action": "open", "speed": 90}}]


def test_issues_sorted_by_line_and_formatted():
    text = """\
version: 1
tasks:
  b:
    description: x
    steps: [{delay: -1}]
  a:
    descriptin: x
    steps: [{laser: 1}]
"""
    err = errors_of(text)
    lines = [i.line for i in err.issues]
    assert lines == sorted(lines)
    out = err.format().splitlines()
    assert all(re.match(r"t\.yaml:\d+(:\d+)?: ", line) for line in out)


# ---------------------------------------------------------------------------
# Durations, limits, merging
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("step, ms", [
    ({"mask": {"action": "open"}}, 1142),
    ({"mask": {"action": "close", "speed": 80}}, 1000),
    ({"mask": {"position": 30, "speed": 10}}, 8000),
    ({"mask": {"action": "open", "speed": 100}}, 800),
    ({"servo": {"id": 0, "angle": 10, "duration_ms": 750}}, 750),
    ({"servo": {"id": 1, "angle": 10}}, 0),
    ({"eyes": {"effect": "blink"}}, 1000),
    ({"eyes": {"effect": "flicker"}}, 600),
    ({"eyes": {"effect": "power_up", "duration_ms": 0}}, 1000),
    ({"eyes": {"effect": "breathe"}}, 0),
    ({"eyes": {"effect": "flicker", "duration_ms": 250}}, 250),
    ({"eyes": {"effect": "breathe", "duration_ms": 4000}}, 0),
    ({"eyes": {"on": True, "brightness": 30}}, 0),
    ({"delay": 1234}, 1234),
    ({"output": {"on": True}}, 0),
    ({"display": {"title": "A", "text": "B", "seconds": 30}}, 0),
    ({"sound": {"name": "beep"}}, 0),
])
def test_estimate_formula(step, ms):
    assert taskslib.estimate_step_ms(step) == ms


def test_step_count_limit():
    assert taskslib.check_steps([{"delay": 0}] * 64).ok
    result = taskslib.check_steps([{"delay": 0}] * 65)
    assert not result.ok and result.error_step is None and "64" in result.error


def test_duration_limit_in_tasks_file():
    steps = "\n".join("      - delay: 10000" for _ in range(6)) + "\n      - eyes: {effect: blink}\n"
    err = errors_of(f"version: 1\ntasks:\n  long:\n    description: x\n    steps:\n{steps}")
    assert "61000 ms exceeds the 60000 ms limit" in err.issues[0].message
    assert err.issues[0].line == 5


def test_json_byte_limit_in_tasks_file():
    text = "Hệ thống giáp đã sẵn sàng, mọi chỉ số đều ổn định. " * 4
    step = '      - display: {title: "STATUS", text: "%s"}' % text[:200]
    steps = "\n".join([step] * 20)
    err = errors_of(f"version: 1\ntasks:\n  big:\n    description: x\n    steps:\n{steps}\n")
    assert "bytes (max 4000)" in err.issues[0].message


def test_display_length_counts_nfc_code_points_after_normalization():
    title = unicodedata.normalize("NFD", "Chế độ chiến đấu sẵn")   # 20 chars NFC, 30 NFD
    raw = [{"display": {"title": title, "text": "x"}}]
    assert not taskslib.check_steps(raw).ok                # raw validator counts code points as-is
    normalized = taskslib.normalize_steps(raw)
    assert taskslib.check_steps(normalized).ok             # tasks.yaml strings are sent as NFC
    t = parse(f'version: 1\ntasks:\n  a:\n    description: x\n    steps:\n      - display: {{title: "{title}", text: "x"}}\n')
    assert unicodedata.is_normalized("NFC", t.tasks["a"].steps_json)


def test_normalize_steps_and_json():
    steps = [{"delay": 300.0}, {"display": {"title": unicodedata.normalize("NFD", "Ồ"), "text": "x"}}]
    out = taskslib.normalize_steps(steps)
    assert out[0] == {"delay": 300} and isinstance(out[0]["delay"], int)
    assert taskslib.steps_to_json(steps) == '[{"delay":300},{"display":{"title":"Ồ","text":"x"}}]'


def test_check_steps_json():
    assert taskslib.check_steps_json('[{"delay":1}]').ok
    assert "not valid JSON" in taskslib.check_steps_json("[{").error
    big = json.dumps([{"display": {"title": "t", "text": "x" * 200}}] * 20)
    assert "bytes" in taskslib.check_steps_json(big).error


def test_merge_steps_keeps_order_and_rechecks_limits(table):
    merged, total = taskslib.merge_steps([table.tasks["open_mask"].steps, table.tasks["eyes_on"].steps])
    assert merged == table.tasks["open_mask"].steps + table.tasks["eyes_on"].steps
    assert total == 1300 + 1200
    with pytest.raises(taskslib.StepError) as info:
        taskslib.merge_steps([[{"delay": 10000}] * 4, [{"delay": 10000}] * 3])
    assert "60000" in info.value.message


def test_plan_merge_rejects_only_what_does_not_fit():
    a = [{"delay": 10000}] * 4          # 40 s
    b = [{"delay": 10000}] * 3          # 30 s -> would exceed
    c = [{"eyes": {"on": True}}]        # 0 s -> still fits after a
    plan = taskslib.plan_merge([a, b, c])
    assert plan.accepted == [0, 2]
    assert [idx for idx, _ in plan.rejected] == [1]
    assert "60000" in plan.rejected[0][1]
    assert json.loads(plan.steps_json) == a + c
    assert plan.estimated_ms == 40000
    too_many = taskslib.plan_merge([[{"delay": 0}] * 40, [{"delay": 0}] * 30])
    assert too_many.accepted == [0] and "64" in too_many.rejected[0][1]


# ---------------------------------------------------------------------------
# Shared fixtures (contract 4.11) and schema parity
# ---------------------------------------------------------------------------
FIXTURES = taskslib.load_fixtures(str(FIXTURES_DIR))


def test_fixture_set_is_complete():
    assert len(FIXTURES) >= 25
    names = [f["_file"] for f in FIXTURES]
    assert all(f["name"] + ".json" == f["_file"] for f in FIXTURES)
    assert len(set(names)) == len(names)
    assert set(FIXTURES_DIR.iterdir()) == {FIXTURES_DIR / n for n in names} | {FIXTURES_DIR / "README.md"}
    for f in FIXTURES:
        assert set(f) - {"_file"} == set(taskslib.FIXTURE_KEYS)
        assert f["name"].startswith("valid_" if f["valid"] else "invalid_")
        if f["valid"]:
            assert f["error_step"] is None and f["error_contains"] is None
            assert isinstance(f["estimated_ms"], int)
        else:
            assert f["estimated_ms"] is None
    kinds = {next(iter(s)) for f in FIXTURES if f["valid"] and isinstance(f["steps"], list)
             for s in f["steps"]}
    assert kinds == set(taskslib.STEP_TYPES)
    assert any(f["valid"] and f["steps"] == [] for f in FIXTURES)


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["name"] for f in FIXTURES])
def test_fixture(fixture):
    ok, why = taskslib.check_fixture(fixture)
    assert ok, why
    # The JSON text path (what the device receives) gives the same verdict.
    text = json.dumps(fixture["steps"], ensure_ascii=False, separators=(",", ":"))
    assert len(text.encode("utf-8")) <= taskslib.MAX_SEQUENCE_JSON_BYTES
    assert taskslib.check_steps_json(text).ok == fixture["valid"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["name"] for f in FIXTURES])
def test_fixture_schema_parity(fixture, schema):
    sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/steps"}
    schema_ok = jsonschema.Draft202012Validator(sub).is_valid(fixture["steps"])
    if fixture["name"].startswith("invalid_total_"):
        assert schema_ok, "duration-only fixtures are beyond JSON Schema"
    else:
        assert schema_ok == fixture["valid"]


def test_schema_is_valid_draft_2020_12(schema):
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$defs"]["eyes"]["properties"]["duration_ms"]["default"] == 0
    assert schema["$defs"]["display"]["properties"]["seconds"]["default"] == 5


def test_real_file_passes_schema_directly(schema):
    data, _, issues = taskslib.load_yaml_with_lines(TASKS_FILE.read_text(encoding="utf-8"))
    assert not issues
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(data)) == []


@pytest.mark.parametrize("snippet", [
    "version: 1\ntasks:\n  a:\n    description: x\n    steps: [{mask: {action: open, position: 3}}]\n",
    "version: 1\ntasks:\n  a:\n    description: x\n    steps: [{eyes: {on: true, effect: blink}}]\n",
    "version: 1\ntasks:\n  a:\n    description: x\n    steps: [{display: {title: x}}]\n",
    "version: 1\ntasks:\n  a:\n    description: x\n    quick_phrases: [ok, ok]\n    steps: [{delay: 1}]\n",
    "version: 1\ntasks:\n  aaaaaaaaaaaa:\n    description: x\n    sync_to_device: true\n    steps: [{delay: 1}]\n",
    "version: 1\ntasks:\n  a:\n    description: x\n    colour: red\n    steps: [{delay: 1}]\n",
])
def test_schema_and_code_both_reject(snippet, schema):
    data, _, _ = taskslib.load_yaml_with_lines(snippet)
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(data)), "schema accepted"
    with pytest.raises(taskslib.TasksError):
        parse(snippet, schema)


# ---------------------------------------------------------------------------
# The real tasks.yaml content (SV6 section 2, interfaces.md section 5)
# ---------------------------------------------------------------------------
def test_real_tasks_content(table):
    assert table.ids == ["open_mask", "close_mask", "eyes_on", "eyes_off", "combat_mode",
                         "stealth_mode", "salute"]
    synced = {t.id for t in table.tasks.values() if t.sync_to_device}
    assert synced == {"open_mask", "close_mask", "eyes_on", "eyes_off"}
    assert json.loads(table.tasks["open_mask"].steps_json) == [
        {"eyes": {"effect": "fade_out", "duration_ms": 300}}, {"mask": {"action": "open", "speed": 80}}]
    assert json.loads(table.tasks["close_mask"].steps_json) == [
        {"mask": {"action": "close"}}, {"delay": 300}, {"eyes": {"effect": "power_up", "duration_ms": 1200}}]
    for tid in ("open_mask", "close_mask", "eyes_on", "eyes_off"):
        langs = {taskslib.detect_lang(p) for p in table.tasks[tid].quick_phrases}
        assert langs == {"vi", "en"}, tid
    for task in table.tasks.values():
        assert " / " in task.description                      # bilingual for the LLM
        assert isinstance(task.reply, str) and task.reply.endswith(".")
        assert taskslib.detect_lang(task.reply) == "vi"
        assert task.estimated_ms <= taskslib.MAX_SEQUENCE_MS
    combat = json.loads(table.tasks["combat_mode"].steps_json)
    assert [next(iter(s)) for s in combat] == ["mask", "eyes", "eyes", "display"]
    assert combat[3]["display"]["title"] == "COMBAT MODE"
    stealth = json.loads(table.tasks["stealth_mode"].steps_json)
    assert stealth[0]["eyes"]["effect"] == "fade_out" and stealth[1] == {"output": {"on": False}}
    assert stealth[2]["display"]["title"] == "STEALTH"
    salute = json.loads(table.tasks["salute"].steps_json)
    assert salute[0]["mask"]["action"] == "open" and salute[1]["eyes"]["effect"] == "blink"
    assert salute[2] == {"delay": 1000} and salute[3]["mask"]["action"] == "close"


def test_party_mode_example_is_valid_when_uncommented(schema):
    text = TASKS_FILE.read_text(encoding="utf-8")
    block = re.search(r"^  # party_mode:\n(?:  #.*\n)+", text, re.M)
    assert block, "commented party_mode example missing"
    uncommented = "\n".join(line.replace("  # ", "  ", 1) for line in block.group(0).splitlines()) + "\n"
    t = parse(text + uncommented, schema)
    assert "party_mode" in t.tasks and len(t.tasks) == 8
    assert t.tasks["party_mode"].reply == {"vi": "Bật chế độ tiệc tùng, chúc ngài vui vẻ.",
                                           "en": "Party mode on, enjoy, sir."}


# ---------------------------------------------------------------------------
# Spoken error texts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("MCP错误: invalid step #3: mask.speed out of range 10..100 (got 5)",
     "bước 3 không hợp lệ (mask.speed out of range 10..100 (got 5))"),
    ("MCP错误：Property 'steps': string length 4100 exceeds maximum 4000",
     "chuỗi lệnh quá dài (4100 byte, tối đa 4000)"),
    ("MCP错误: Missing required argument: steps", "thiếu tham số steps"),
    ("MCP错误: Unknown tool: self.sequence.run", "mũ không có lệnh self.sequence.run, cần cập nhật firmware"),
    ("工具调用错误: busy", "busy"),
    ("工具调用请求超时", "lỗi không xác định"),
])
def test_describe_device_error(raw, expected):
    assert taskslib.describe_device_error(raw) == expected


def test_apology_format_never_chinese():
    text = taskslib.apology("bước 2 không hợp lệ")
    assert text == "Xin lỗi thưa ngài, tôi không thực hiện được: bước 2 không hợp lệ."
    assert taskslib.apology("mũ không phản hồi kịp!") == \
        "Xin lỗi thưa ngài, tôi không thực hiện được: mũ không phản hồi kịp!"
    for reason in ("MCP客户端尚未准备就绪", "", "设备 busy"):
        out = taskslib.apology(reason)
        assert out.startswith(taskslib.APOLOGY_PREFIX) and not CJK.search(out)

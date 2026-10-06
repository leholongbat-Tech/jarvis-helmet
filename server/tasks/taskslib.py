"""Pure helpers for the Jarvis Helmet task file ``tasks.yaml`` (SV6).

This module has **no xiaozhi imports**. It is copied into the xiaozhi image at
``/opt/jarvis/lib`` (on ``PYTHONPATH``) and used by the ``helmet_tasks`` plugin
(Python 3.10), by ``tools/tasks_cli.py`` and by the unit tests.

Sections:

* sequence steps: validation that mirrors the firmware validator
  (docs/interfaces.md section 3, contract D23: ``invalid step #n: <reason>``, n from 1)
  and the firmware duration estimate (``seq::EstimateStepMs``);
* ``tasks.yaml``: loading with line numbers, hand-written checks with precise
  messages, cross-check against ``schema.json`` (JSON Schema draft 2020-12);
* steps -> compact JSON for ``self.sequence.run`` and merging of several tasks
  into one sequence (contract D4);
* text normalization and quick-phrase matching (rapidfuzz, with an exact
  pure-Python fallback when rapidfuzz is missing);
* shared sequence fixtures (``tools/tests/fixtures/sequences/*.json``, contract 4.11).
"""
from __future__ import annotations

import difflib
import functools
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import yaml

try:  # rapidfuzz is installed in the xiaozhi image by server/xiaozhi/Dockerfile
    from rapidfuzz import fuzz as _rf_fuzz
except Exception:  # pragma: no cover - exercised only when rapidfuzz is missing
    _rf_fuzz = None

try:
    import jsonschema as _jsonschema
except Exception:  # pragma: no cover - exercised only when jsonschema is missing
    _jsonschema = None

__all__ = [
    "Issue", "TasksError", "StepError", "SequenceCheck", "Task", "TaskTable", "PhraseEntry",
    "QuickMatch", "QuickPhraseAnalysis", "MergePlan",
    "check_steps", "check_steps_json", "validate_steps", "estimate_step_ms", "normalize_steps",
    "steps_to_json", "merge_steps", "plan_merge",
    "load_yaml_with_lines", "parse_tasks", "load_tasks", "load_schema", "find_schema",
    "normalize_basic", "normalize_text", "strip_accents", "strip_wake_words", "similarity",
    "analyze_quick_phrase", "match_quick_phrase", "find_command_join", "detect_lang", "pick_reply",
    "apology", "describe_device_error", "load_fixtures", "check_fixture",
]

# ---------------------------------------------------------------------------
# Limits: keep in sync with firmware/main/boards/jarvis-helmet/helmet_common.h
# ---------------------------------------------------------------------------
SPEED_MIN, SPEED_MAX, DEFAULT_SPEED = 10, 100, 70
POSITION_MIN, POSITION_MAX = 0, 100
SERVO_ID_MIN, SERVO_ID_MAX = 0, 1
ANGLE_MIN, ANGLE_MAX = 0, 180
SERVO_DURATION_MIN_MS, SERVO_DURATION_MAX_MS = 0, 5000
BRIGHTNESS_MIN, BRIGHTNESS_MAX = 0, 100
EFFECT_DURATION_MIN_MS, EFFECT_DURATION_MAX_MS = 100, 10000
DEFAULT_EFFECT_DURATION_MS = 1000
DELAY_MIN_MS, DELAY_MAX_MS = 0, 10000
INFO_TITLE_MAX_CHARS = 24
INFO_TEXT_MAX_CHARS = 200
INFO_SECONDS_MIN, INFO_SECONDS_MAX = 1, 30
DEFAULT_INFO_SECONDS = 5
MAX_STEPS = 64
MAX_SEQUENCE_MS = 60000
SEQUENCE_NAME_MAX_LEN = 11      # NVS key "seq_<name>" must fit in 15 chars
SOUND_NAME_MAX_LEN = 32
MAX_SEQUENCE_JSON_BYTES = 4000  # self.sequence.run/save "steps" maxLength (bytes)
MASK_BASE_MS = 800              # full 0 -> 100 travel takes MASK_BASE_MS * 100 / speed

EYE_EFFECTS = ("breathe", "blink", "flicker", "power_up", "fade_out")
MASK_ACTIONS = ("open", "close")
STEP_TYPES = ("mask", "servo", "eyes", "output", "delay", "display", "sound")

TASK_ID_RE = re.compile(r"^[a-z0-9_]+$")
TASK_ID_MAX_LEN = 59            # "task_" + id must fit the 64-char LLM function name limit
TASK_KEYS = ("description", "quick_phrases", "reply", "sync_to_device", "steps")
TOP_KEYS = ("version", "tasks")
DESCRIPTION_MAX_CHARS = 1000
PHRASE_MAX_CHARS = 100
REPLY_MAX_CHARS = 300
REPLY_LANGS = ("vi", "en")
SOUND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")

FIXTURE_KEYS = ("name", "steps", "valid", "error_step", "error_contains", "estimated_ms", "note")


# ---------------------------------------------------------------------------
# Issues / errors
# ---------------------------------------------------------------------------
@dataclass
class Issue:
    """One problem found in a tasks file. ``line``/``column`` are 1-based."""

    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    path: Tuple[Any, ...] = ()

    def format(self, filename: str = "tasks.yaml") -> str:
        if self.line is None:
            return f"{filename}: {self.message}"
        if self.column is None:
            return f"{filename}:{self.line}: {self.message}"
        return f"{filename}:{self.line}:{self.column}: {self.message}"


class TasksError(Exception):
    """Raised when a tasks file cannot be used; ``issues`` carries every problem found."""

    def __init__(self, issues: Sequence[Issue], filename: str = "tasks.yaml"):
        self.issues = sorted(issues, key=lambda i: (i.line or 0, i.column or 0))
        self.filename = filename
        super().__init__(self.format())

    def format(self) -> str:
        return "\n".join(issue.format(self.filename) for issue in self.issues)


class StepError(ValueError):
    """A sequence validation error. ``index`` is the 1-based step number, None for top-level."""

    def __init__(self, index: Optional[int], reason: str, where: Tuple[str, ...] = ()):
        self.index = index
        self.reason = reason
        self.where = where
        super().__init__(self.message)

    @property
    def message(self) -> str:
        if self.index is None:
            return self.reason
        return f"invalid step #{self.index}: {self.reason}"


@dataclass
class SequenceCheck:
    ok: bool
    estimated_ms: int = 0
    error: Optional[str] = None
    error_step: Optional[int] = None          # 1-based, None for top-level errors
    error_where: Tuple[str, ...] = ()          # (kind,) or (kind, field) inside the failing step


# ---------------------------------------------------------------------------
# Sequence steps (mirror of firmware seq::Validate / seq::EstimateStepMs)
# ---------------------------------------------------------------------------
def _int_value(value: Any) -> Optional[int]:
    """JSON integer semantics: bool is not a number, 300.0 counts as 300, 300.5 is not."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _object(i: int, kind: str, value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise StepError(i, f"{kind} must be an object", (kind,))
    return value


def _allowed(i: int, kind: str, obj: Dict[str, Any], allowed: Sequence[str]) -> None:
    for key in obj:
        if key not in allowed:
            raise StepError(i, f"{kind}: unknown field '{key}' (allowed: {', '.join(allowed)})",
                            (kind, str(key)))


def _require(i: int, kind: str, obj: Dict[str, Any], name: str) -> Any:
    if name not in obj:
        raise StepError(i, f"{kind}.{name} is required", (kind, name))
    return obj[name]


def _int_field(i: int, kind: str, obj: Dict[str, Any], name: str, lo: int, hi: int) -> int:
    value = _int_value(obj[name])
    if value is None:
        raise StepError(i, f"{kind}.{name} must be an integer {lo}..{hi}", (kind, name))
    if value < lo or value > hi:
        raise StepError(i, f"{kind}.{name} out of range {lo}..{hi} (got {value})", (kind, name))
    return value


def _bool_field(i: int, kind: str, obj: Dict[str, Any], name: str) -> bool:
    value = obj[name]
    if not isinstance(value, bool):
        raise StepError(i, f"{kind}.{name} must be true or false", (kind, name))
    return value


def _check_mask(i: int, value: Any) -> int:
    obj = _object(i, "mask", value)
    _allowed(i, "mask", obj, ("action", "position", "speed"))
    has_action, has_position = "action" in obj, "position" in obj
    if has_action and has_position:
        raise StepError(i, "mask takes either 'action' or 'position', not both", ("mask",))
    if not has_action and not has_position:
        raise StepError(i, "mask needs 'action' (open|close) or 'position' (0..100)", ("mask",))
    if has_action:
        action = obj["action"]
        if not isinstance(action, str) or action not in MASK_ACTIONS:
            raise StepError(i, "mask.action must be 'open' or 'close'", ("mask", "action"))
    else:
        _int_field(i, "mask", obj, "position", POSITION_MIN, POSITION_MAX)
    speed = DEFAULT_SPEED
    if "speed" in obj:
        speed = _int_field(i, "mask", obj, "speed", SPEED_MIN, SPEED_MAX)
    # Worst case: a full travel at this speed (C++ integer division).
    return MASK_BASE_MS * 100 // speed


def _check_servo(i: int, value: Any) -> int:
    obj = _object(i, "servo", value)
    _allowed(i, "servo", obj, ("id", "angle", "duration_ms"))
    _require(i, "servo", obj, "id")
    _require(i, "servo", obj, "angle")
    _int_field(i, "servo", obj, "id", SERVO_ID_MIN, SERVO_ID_MAX)
    _int_field(i, "servo", obj, "angle", ANGLE_MIN, ANGLE_MAX)
    if "duration_ms" in obj:
        return _int_field(i, "servo", obj, "duration_ms", SERVO_DURATION_MIN_MS,
                          SERVO_DURATION_MAX_MS)
    return 0


def _check_eyes(i: int, value: Any) -> int:
    obj = _object(i, "eyes", value)
    _allowed(i, "eyes", obj, ("on", "brightness", "effect", "duration_ms"))
    has_on, has_effect = "on" in obj, "effect" in obj
    if has_on and has_effect:
        raise StepError(i, "eyes takes either 'on' (+ brightness) or 'effect' (+ duration_ms), "
                           "not both", ("eyes",))
    if not has_on and not has_effect:
        raise StepError(i, "eyes needs 'on' (+ brightness) or 'effect' (+ duration_ms)", ("eyes",))
    if has_on:
        if "duration_ms" in obj:
            raise StepError(i, "eyes.duration_ms is only allowed together with 'effect'",
                            ("eyes", "duration_ms"))
        _bool_field(i, "eyes", obj, "on")
        if "brightness" in obj:
            _int_field(i, "eyes", obj, "brightness", BRIGHTNESS_MIN, BRIGHTNESS_MAX)
        return 0
    if "brightness" in obj:
        raise StepError(i, "eyes.brightness is only allowed together with 'on'",
                        ("eyes", "brightness"))
    effect = obj["effect"]
    if not isinstance(effect, str) or effect not in EYE_EFFECTS:
        raise StepError(i, f"eyes.effect must be one of {', '.join(EYE_EFFECTS)}",
                        ("eyes", "effect"))
    duration = DEFAULT_EFFECT_DURATION_MS
    if "duration_ms" in obj:
        duration = _int_field(i, "eyes", obj, "duration_ms", EFFECT_DURATION_MIN_MS,
                              EFFECT_DURATION_MAX_MS)
    # breathe loops until the next command: it never blocks the runner.
    return 0 if effect == "breathe" else duration


def _check_output(i: int, value: Any) -> int:
    obj = _object(i, "output", value)
    _allowed(i, "output", obj, ("on",))
    _require(i, "output", obj, "on")
    _bool_field(i, "output", obj, "on")
    return 0


def _check_delay(i: int, value: Any) -> int:
    ms = _int_value(value)
    if ms is None:
        raise StepError(i, f"delay must be an integer {DELAY_MIN_MS}..{DELAY_MAX_MS} (milliseconds)",
                        ("delay",))
    if ms < DELAY_MIN_MS or ms > DELAY_MAX_MS:
        raise StepError(i, f"delay out of range {DELAY_MIN_MS}..{DELAY_MAX_MS} (got {ms})",
                        ("delay",))
    return ms


def _check_display(i: int, value: Any) -> int:
    obj = _object(i, "display", value)
    _allowed(i, "display", obj, ("title", "text", "seconds"))
    for name, limit in (("title", INFO_TITLE_MAX_CHARS), ("text", INFO_TEXT_MAX_CHARS)):
        text = _require(i, "display", obj, name)
        if not isinstance(text, str):
            raise StepError(i, f"display.{name} must be a string", ("display", name))
        # Python str length == number of Unicode code points (same as helmet::Utf8Length).
        if len(text) > limit:
            raise StepError(i, f"display.{name} is {len(text)} characters (max {limit})",
                            ("display", name))
    if "seconds" in obj:
        _int_field(i, "display", obj, "seconds", INFO_SECONDS_MIN, INFO_SECONDS_MAX)
    return 0


def _check_sound(i: int, value: Any) -> int:
    obj = _object(i, "sound", value)
    _allowed(i, "sound", obj, ("name",))
    name = _require(i, "sound", obj, "name")
    if not isinstance(name, str) or not SOUND_NAME_RE.match(name):
        raise StepError(i, f"sound.name must match [a-z0-9_]{{1,{SOUND_NAME_MAX_LEN}}}",
                        ("sound", "name"))
    return 0


_STEP_CHECKERS = {
    "mask": _check_mask,
    "servo": _check_servo,
    "eyes": _check_eyes,
    "output": _check_output,
    "delay": _check_delay,
    "display": _check_display,
    "sound": _check_sound,
}


def _check_step(i: int, step: Any) -> int:
    if not isinstance(step, dict):
        raise StepError(i, "step must be an object with exactly one key (the step type)")
    if len(step) != 1:
        raise StepError(i, f"step must have exactly one key (the step type), got {len(step)}")
    (kind, value), = step.items()
    checker = _STEP_CHECKERS.get(kind)
    if checker is None:
        raise StepError(i, f"unknown step type '{kind}' (allowed: {', '.join(STEP_TYPES)})",
                        (str(kind),))
    return checker(i, value)


def validate_steps(steps: Any) -> int:
    """Validate a parsed step list like the device does; returns the estimated ms.

    Raises StepError. Check order: array, step count, every step in order (first error
    wins), then the 60 s total.
    """
    if not isinstance(steps, list):
        raise StepError(None, "steps must be a JSON array")
    if len(steps) > MAX_STEPS:
        raise StepError(None, f"too many steps: {len(steps)} (max {MAX_STEPS})")
    total = 0
    for i, step in enumerate(steps, 1):
        total += _check_step(i, step)
    if total > MAX_SEQUENCE_MS:
        raise StepError(None, f"estimated duration {total} ms exceeds the {MAX_SEQUENCE_MS} ms limit")
    return total


def check_steps(steps: Any) -> SequenceCheck:
    """Non-raising variant of validate_steps()."""
    try:
        total = validate_steps(steps)
    except StepError as exc:
        return SequenceCheck(False, 0, exc.message, exc.index, exc.where)
    return SequenceCheck(True, total)


def check_steps_json(text: Union[str, bytes]) -> SequenceCheck:
    """Validate the raw JSON string a device would receive (adds the 4000-byte limit)."""
    raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if len(raw) > MAX_SEQUENCE_JSON_BYTES:
        return SequenceCheck(False, 0, f"steps JSON is {len(raw)} bytes (max {MAX_SEQUENCE_JSON_BYTES})")
    try:
        steps = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return SequenceCheck(False, 0, f"steps is not valid JSON: {exc}")
    return check_steps(steps)


def estimate_step_ms(step: Any) -> int:
    """Worst-case blocking time of one (valid) step; raises StepError if invalid."""
    return _check_step(1, step)


def normalize_steps(steps: Any) -> Any:
    """JSON-ready deep copy: integral floats become ints, strings are NFC-normalized."""
    if isinstance(steps, bool) or steps is None:
        return steps
    if isinstance(steps, float) and steps.is_integer():
        return int(steps)
    if isinstance(steps, str):
        return unicodedata.normalize("NFC", steps)
    if isinstance(steps, list):
        return [normalize_steps(v) for v in steps]
    if isinstance(steps, dict):
        return {k: normalize_steps(v) for k, v in steps.items()}
    return steps


def steps_to_json(steps: Any) -> str:
    """Compact JSON for ``self.sequence.run`` / ``self.sequence.save`` (UTF-8, no ASCII escapes:
    the device limit counts bytes and Vietnamese letters are 2-3 bytes instead of 6)."""
    return json.dumps(normalize_steps(steps), ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def merge_steps(step_lists: Iterable[Sequence[Any]]) -> Tuple[List[Any], int]:
    """Concatenate several step lists and re-check every limit; raises StepError."""
    merged: List[Any] = []
    for steps in step_lists:
        merged.extend(steps)
    total = validate_steps(merged)
    size = len(steps_to_json(merged).encode("utf-8"))
    if size > MAX_SEQUENCE_JSON_BYTES:
        raise StepError(None, f"steps JSON is {size} bytes (max {MAX_SEQUENCE_JSON_BYTES})")
    return merged, total


@dataclass
class MergePlan:
    steps: List[Any]
    steps_json: str
    estimated_ms: int
    accepted: List[int]                 # indexes (input order) merged into ``steps``
    rejected: List[Tuple[int, str]]     # (index, reason) that would break a limit


def plan_merge(step_lists: Sequence[Sequence[Any]]) -> MergePlan:
    """Greedy merge in order (contract D4): each list is appended only if the combined
    sequence still passes every device limit; lists that do not fit are rejected."""
    merged: List[Any] = []
    total = 0
    accepted: List[int] = []
    rejected: List[Tuple[int, str]] = []
    for idx, steps in enumerate(step_lists):
        candidate = merged + list(steps)
        result = check_steps(candidate)
        if not result.ok:
            rejected.append((idx, result.error or "invalid sequence"))
            continue
        size = len(steps_to_json(candidate).encode("utf-8"))
        if size > MAX_SEQUENCE_JSON_BYTES:
            rejected.append((idx, f"steps JSON would be {size} bytes (max {MAX_SEQUENCE_JSON_BYTES})"))
            continue
        merged, total = candidate, result.estimated_ms
        accepted.append(idx)
    return MergePlan(merged, steps_to_json(merged), total, accepted, rejected)


# ---------------------------------------------------------------------------
# YAML with line numbers
# ---------------------------------------------------------------------------
class _LineLoader(yaml.SafeLoader):
    """SafeLoader used only to compose nodes and construct scalars; we walk the tree."""


_MERGE_TAG = "tag:yaml.org,2002:merge"
_MAX_DEPTH = 40


def _mark(node: yaml.Node) -> Tuple[int, int]:
    return node.start_mark.line + 1, node.start_mark.column + 1


def _build_value(loader: _LineLoader, node: yaml.Node, path: Tuple[Any, ...],
                 lines: Dict[Tuple[Any, ...], Tuple[int, int]], issues: List[Issue],
                 depth: int) -> Any:
    lines.setdefault(path, _mark(node))
    if depth > _MAX_DEPTH:
        line, col = _mark(node)
        issues.append(Issue("nesting is too deep (recursive alias?)", line, col, path))
        return None
    if isinstance(node, yaml.ScalarNode):
        try:
            return loader.construct_object(node, deep=True)
        except yaml.YAMLError as exc:
            line, col = _mark(node)
            issues.append(Issue(f"cannot read value: {exc}", line, col, path))
            return None
    if isinstance(node, yaml.SequenceNode):
        return [_build_value(loader, child, path + (idx,), lines, issues, depth + 1)
                for idx, child in enumerate(node.value)]
    if isinstance(node, yaml.MappingNode):
        # Explicit duplicates are errors; keys pulled in by "<<: *anchor" may be overridden.
        explicit: Dict[str, int] = {}
        for key_node, _ in node.value:
            if isinstance(key_node, yaml.ScalarNode) and key_node.tag != _MERGE_TAG:
                if key_node.value in explicit:
                    line, col = _mark(key_node)
                    issues.append(Issue(f"duplicate key '{key_node.value}' (first defined on line "
                                        f"{explicit[key_node.value]})", line, col,
                                        path + (key_node.value,)))
                else:
                    explicit[key_node.value] = _mark(key_node)[0]
        try:
            loader.flatten_mapping(node)
        except yaml.YAMLError as exc:
            line, col = _mark(node)
            issues.append(Issue(f"invalid merge key: {exc}", line, col, path))
        out: Dict[str, Any] = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                line, col = _mark(key_node)
                issues.append(Issue("mapping keys must be plain strings", line, col, path))
                continue
            # Raw key text: YAML 1.1 would turn keys like 'on'/'off'/'yes' into booleans.
            key = key_node.value
            lines[path + (key,)] = _mark(key_node)
            out[key] = _build_value(loader, value_node, path + (key,), lines, issues, depth + 1)
        return out
    line, col = _mark(node)
    issues.append(Issue(f"unsupported YAML node {type(node).__name__}", line, col, path))
    return None


def load_yaml_with_lines(text: str, filename: str = "tasks.yaml"
                         ) -> Tuple[Any, Dict[Tuple[Any, ...], Tuple[int, int]], List[Issue]]:
    """Parse YAML keeping ``path -> (line, column)`` (1-based) for every node.

    Mapping keys are kept as their raw text (so ``on: true`` keeps the key ``"on"``).
    Raises TasksError for syntax errors; returns structural issues (duplicates...) as a list.
    """
    loader = _LineLoader(text)
    try:
        try:
            node = loader.get_single_node()
        except yaml.MarkedYAMLError as exc:
            mark = exc.problem_mark or exc.context_mark
            parts = [p for p in (exc.context, exc.problem) if p]
            message = "YAML syntax error: " + ("; ".join(parts) if parts else str(exc))
            line = mark.line + 1 if mark is not None else None
            col = mark.column + 1 if mark is not None else None
            raise TasksError([Issue(message, line, col)], filename) from None
        except yaml.YAMLError as exc:
            raise TasksError([Issue(f"YAML error: {exc}")], filename) from None
        lines: Dict[Tuple[Any, ...], Tuple[int, int]] = {}
        issues: List[Issue] = []
        value = None if node is None else _build_value(loader, node, (), lines, issues, 0)
        return value, lines, issues
    finally:
        loader.dispose()


def _locate(path: Tuple[Any, ...], lines: Dict[Tuple[Any, ...], Tuple[int, int]]
            ) -> Tuple[Optional[int], Optional[int]]:
    probe = tuple(path)
    while True:
        if probe in lines:
            return lines[probe]
        if not probe:
            return None, None
        probe = probe[:-1]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PhraseEntry:
    task_id: str
    phrase: str      # as written in tasks.yaml
    norm: str        # normalize_text(phrase)
    plain: str       # strip_accents(norm)
    lang: str        # detect_lang(phrase)


@dataclass
class Task:
    id: str
    description: str
    steps: List[Any]
    steps_json: str
    estimated_ms: int
    quick_phrases: Tuple[str, ...] = ()
    reply: Union[None, str, Dict[str, str]] = None
    sync_to_device: bool = False
    line: Optional[int] = None

    @property
    def steps_bytes(self) -> int:
        return len(self.steps_json.encode("utf-8"))

    @property
    def steps_hash(self) -> str:
        return hashlib.sha1(self.steps_json.encode("utf-8")).hexdigest()


@dataclass
class TaskTable:
    version: int
    tasks: Dict[str, Task]
    source: str = "tasks.yaml"
    phrase_index: List[PhraseEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def get(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    @property
    def ids(self) -> List[str]:
        return list(self.tasks)

    @property
    def content_hash(self) -> str:
        blob = json.dumps({tid: [t.description, t.steps_json, list(t.quick_phrases), t.reply,
                                 t.sync_to_device] for tid, t in self.tasks.items()},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# tasks.yaml validation
# ---------------------------------------------------------------------------
def _suggest(word: str, choices: Sequence[str]) -> str:
    close = difflib.get_close_matches(str(word), list(choices), n=1, cutoff=0.6)
    return f" (did you mean '{close[0]}'?)" if close else ""


def _scope(path: Tuple[Any, ...]) -> Tuple[Any, ...]:
    if len(path) >= 2 and path[0] == "tasks":
        return path[:2]
    return path[:1]


class _Collector:
    def __init__(self, lines: Dict[Tuple[Any, ...], Tuple[int, int]]):
        self.lines = lines
        self.issues: List[Issue] = []

    def add(self, path: Tuple[Any, ...], message: str) -> None:
        line, col = _locate(tuple(path), self.lines)
        self.issues.append(Issue(message, line, col, tuple(path)))

    def scopes(self) -> set:
        return {_scope(issue.path) for issue in self.issues}


def _check_string(col: _Collector, path: Tuple[Any, ...], label: str, value: Any,
                  max_chars: int) -> Optional[str]:
    if not isinstance(value, str):
        col.add(path, f"{label} must be a string")
        return None
    if not value.strip():
        col.add(path, f"{label} must not be empty")
        return None
    text = unicodedata.normalize("NFC", value.strip())
    if len(text) > max_chars:
        col.add(path, f"{label} is {len(text)} characters (max {max_chars})")
        return None
    return text


def _check_reply(col: _Collector, path: Tuple[Any, ...], tid: str, reply: Any
                 ) -> Union[None, str, Dict[str, str]]:
    label = f"task '{tid}': reply"
    if isinstance(reply, str):
        return _check_string(col, path, label, reply, REPLY_MAX_CHARS)
    if isinstance(reply, dict):
        if not reply:
            col.add(path, f"{label} needs at least one of: vi, en")
            return None
        out: Dict[str, str] = {}
        for lang, text in reply.items():
            if lang not in REPLY_LANGS:
                col.add(path + (lang,), f"{label}: unknown language '{lang}' (allowed: vi, en)")
                continue
            checked = _check_string(col, path + (lang,), f"{label}.{lang}", text, REPLY_MAX_CHARS)
            if checked is not None:
                out[lang] = checked
        return out or None
    col.add(path, f"{label} must be a string or a mapping {{vi: ..., en: ...}}")
    return None


def _validate_document(data: Any, col: _Collector) -> List[Task]:
    tasks_out: List[Task] = []
    if data is None:
        col.add((), "file is empty (expected 'version: 1' and 'tasks:')")
        return tasks_out
    if not isinstance(data, dict):
        col.add((), "top level must be a mapping with 'version' and 'tasks'")
        return tasks_out
    for key in data:
        if key not in TOP_KEYS:
            col.add((key,), f"unknown top-level key '{key}'{_suggest(key, TOP_KEYS)}")
    if "version" not in data:
        col.add(("version",), "missing 'version: 1'")
    elif _int_value(data["version"]) != 1:
        col.add(("version",), f"version must be 1 (got {data['version']!r})")
    if "tasks" not in data:
        col.add(("tasks",), "missing 'tasks' mapping")
        return tasks_out
    tasks = data["tasks"]
    if not isinstance(tasks, dict):
        col.add(("tasks",), "tasks must be a mapping of task id -> task (use 'tasks: {}' for none)")
        return tasks_out

    phrase_owner: Dict[str, Tuple[str, str]] = {}
    for tid, spec in tasks.items():
        base = ("tasks", tid)
        ok = True
        if not TASK_ID_RE.match(tid):
            col.add(base, f"task id '{tid}' must match [a-z0-9_]+ (lowercase letters, digits, _)")
            ok = False
        elif len(tid) > TASK_ID_MAX_LEN:
            col.add(base, f"task id '{tid}' is too long ({len(tid)} > {TASK_ID_MAX_LEN}): the LLM "
                          f"function name task_<id> is limited to 64 characters")
            ok = False
        if not isinstance(spec, dict):
            col.add(base, f"task '{tid}' must be a mapping with 'description' and 'steps'")
            continue
        for key in spec:
            if key not in TASK_KEYS:
                col.add(base + (key,), f"task '{tid}': unknown field '{key}'{_suggest(key, TASK_KEYS)}")
                ok = False

        description = None
        if "description" not in spec:
            col.add(base, f"task '{tid}': 'description' is required (the LLM reads it)")
            ok = False
        else:
            description = _check_string(col, base + ("description",), f"task '{tid}': description",
                                        spec["description"], DESCRIPTION_MAX_CHARS)
            ok = ok and description is not None

        phrases: List[str] = []
        if "quick_phrases" in spec:
            raw = spec["quick_phrases"]
            if not isinstance(raw, list):
                col.add(base + ("quick_phrases",), f"task '{tid}': quick_phrases must be a list of strings")
                ok = False
            else:
                seen_local: Dict[str, int] = {}
                for idx, phrase in enumerate(raw):
                    ppath = base + ("quick_phrases", idx)
                    text = _check_string(col, ppath, f"task '{tid}': quick phrase #{idx + 1}",
                                         phrase, PHRASE_MAX_CHARS)
                    if text is None:
                        ok = False
                        continue
                    norm = normalize_text(text)
                    if not norm:
                        col.add(ppath, f"task '{tid}': quick phrase '{text}' is empty after "
                                       f"normalization (only punctuation or a wake word?)")
                        ok = False
                        continue
                    if norm in seen_local:
                        col.add(ppath, f"task '{tid}': duplicate quick phrase '{text}' (same as "
                                       f"#{seen_local[norm] + 1} after normalization)")
                        ok = False
                        continue
                    seen_local[norm] = idx
                    owner = phrase_owner.get(norm)
                    if owner is not None and owner[0] != tid:
                        col.add(ppath, f"task '{tid}': quick phrase '{text}' is already used by task "
                                       f"'{owner[0]}' ('{owner[1]}')")
                        ok = False
                        continue
                    phrase_owner[norm] = (tid, text)
                    phrases.append(text)

        reply = None
        if "reply" in spec:
            reply = _check_reply(col, base + ("reply",), tid, spec["reply"])
            ok = ok and reply is not None

        sync = False
        if "sync_to_device" in spec:
            value = spec["sync_to_device"]
            if not isinstance(value, bool):
                col.add(base + ("sync_to_device",), f"task '{tid}': sync_to_device must be true or false")
                ok = False
            else:
                sync = value
                if sync and len(tid) > SEQUENCE_NAME_MAX_LEN:
                    col.add(base + ("sync_to_device",),
                            f"task '{tid}': sync_to_device needs an id of at most "
                            f"{SEQUENCE_NAME_MAX_LEN} characters (device NVS key seq_<id>), "
                            f"this one has {len(tid)}")
                    ok = False

        steps_json = ""
        estimated = 0
        steps: List[Any] = []
        if "steps" not in spec:
            col.add(base, f"task '{tid}': 'steps' is required")
            ok = False
        elif not isinstance(spec["steps"], list):
            col.add(base + ("steps",), f"task '{tid}': steps must be a list (sequence steps)")
            ok = False
        elif not spec["steps"]:
            col.add(base + ("steps",), f"task '{tid}': steps must contain at least one step")
            ok = False
        else:
            steps = normalize_steps(spec["steps"])
            result = check_steps(steps)
            if not result.ok:
                where = base + ("steps",)
                if result.error_step is not None:
                    where = where + (result.error_step - 1,) + result.error_where
                col.add(where, f"task '{tid}': {result.error}")
                ok = False
            else:
                estimated = result.estimated_ms
                steps_json = steps_to_json(steps)
                size = len(steps_json.encode("utf-8"))
                if size > MAX_SEQUENCE_JSON_BYTES:
                    col.add(base + ("steps",), f"task '{tid}': steps JSON is {size} bytes "
                                               f"(max {MAX_SEQUENCE_JSON_BYTES})")
                    ok = False

        if ok and description is not None:
            line, _ = _locate(base, col.lines)
            tasks_out.append(Task(id=tid, description=description, steps=steps,
                                  steps_json=steps_json, estimated_ms=estimated,
                                  quick_phrases=tuple(phrases), reply=reply,
                                  sync_to_device=sync, line=line))
    return tasks_out


def _schema_issues(data: Any, schema: Dict[str, Any], col: _Collector) -> None:
    """Report schema errors that the hand-written checks did not already cover."""
    validator_cls = _jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)
    covered = col.scopes()
    pending: List[Tuple[Tuple[Any, ...], str]] = []
    for err in validator.iter_errors(data):
        path = tuple(err.absolute_path)
        if _scope(path) in covered or () in covered:
            continue
        location = "/".join(str(p) for p in path) or "(root)"
        pending.append((path, f"schema: {location}: {err.message}"))
    for path, message in sorted(pending, key=lambda item: [str(p) for p in item[0]]):
        col.add(path, message)


def find_schema(tasks_path: Optional[str] = None) -> Optional[str]:
    """schema.json lookup: $HELMET_TASKS_SCHEMA, next to the tasks file, next to this module."""
    candidates = [os.environ.get("HELMET_TASKS_SCHEMA") or ""]
    if tasks_path:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(tasks_path)), "schema.json"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.json"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def load_schema(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or find_schema()
    if not path:
        raise FileNotFoundError("schema.json not found")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_tasks(text: str, filename: str = "tasks.yaml",
                schema: Optional[Dict[str, Any]] = None) -> TaskTable:
    """Parse and validate tasks.yaml content; raises TasksError with line numbers."""
    data, lines, issues = load_yaml_with_lines(text, filename)
    col = _Collector(lines)
    col.issues.extend(issues)
    tasks = _validate_document(data, col)
    warnings: List[str] = []
    if schema is not None:
        if _jsonschema is None:
            warnings.append("jsonschema is not installed: schema cross-check skipped")
        else:
            _schema_issues(data, schema, col)
    if col.issues:
        raise TasksError(col.issues, filename)
    table = TaskTable(version=1, tasks={t.id: t for t in tasks}, source=filename, warnings=warnings)
    for task in tasks:
        for phrase in task.quick_phrases:
            norm = normalize_text(phrase)
            table.phrase_index.append(PhraseEntry(task.id, phrase, norm, strip_accents(norm),
                                                  detect_lang(phrase)))
    return table


def load_tasks(path: str, schema: Any = "auto") -> TaskTable:
    """Load a tasks file. ``schema``: "auto" (find_schema, skipped if absent), None (skip),
    a path, or an already-loaded dict."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise TasksError([Issue(f"cannot read file: {exc}")], path) from None
    except UnicodeDecodeError as exc:
        raise TasksError([Issue(f"file is not UTF-8: {exc}")], path) from None
    schema_dict: Optional[Dict[str, Any]] = None
    if schema == "auto":
        found = find_schema(path)
        schema_dict = load_schema(found) if found else None
    elif isinstance(schema, str):
        schema_dict = load_schema(schema)
    elif isinstance(schema, dict):
        schema_dict = schema
    return parse_tasks(text, path, schema_dict)


# ---------------------------------------------------------------------------
# Text normalization and quick phrases
# ---------------------------------------------------------------------------
# Wake words stripped at the start (and end) of an utterance; ASR spellings included.
DEFAULT_WAKE_WORDS = (
    "jarvis", "giắc vít", "giắc vis", "giác vít", "gia vít", "giắt vít", "jar vít", "jar vis",
    "quản gia", "quảng gia",
)
WAKE_INTERJECTIONS = ("hey", "hi", "hế lô", "ok", "okay", "ô kê", "này", "ê", "alo", "a lô", "nè")
WAKE_VOCATIVES = ("ơi", "à")
# Words that join two commands ("mở mặt nạ rồi bật đèn mắt"): such utterances go to the LLM.
COMMAND_CONNECTORS = ("rồi", "và", "xong", "sau đó", "tiếp theo", "đồng thời", "với lại",
                      "then", "and", "after that", "afterwards", "also")
_APOSTROPHES = frozenset("'’‘ʼ`´")


def normalize_basic(text: str) -> str:
    """NFC, lowercase, punctuation/symbols -> space (apostrophes dropped), collapse spaces."""
    s = unicodedata.normalize("NFC", text or "")
    s = unicodedata.normalize("NFC", s.lower())
    out = []
    for ch in s:
        if ch in _APOSTROPHES:
            continue
        cat = unicodedata.category(ch)
        if cat[0] in "PSZ" or cat in ("Cc", "Cf"):
            out.append(" ")
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def strip_accents(text: str) -> str:
    """Remove Vietnamese diacritics: NFD, drop combining marks, đ -> d."""
    s = unicodedata.normalize("NFD", text or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFC", s)


@functools.lru_cache(maxsize=32)
def _token_patterns_cached(words: Tuple[str, ...]) -> Tuple[Tuple[str, ...], ...]:
    return tuple(_token_patterns(words))


def _token_patterns(words: Iterable[str]) -> List[Tuple[str, ...]]:
    patterns = set()
    for word in words:
        tokens = strip_accents(normalize_basic(word)).split()
        if tokens:
            patterns.add(tuple(tokens))
            if len(tokens) > 1:
                patterns.add(("".join(tokens),))   # "giắcvít", "quảngia" (D15 spellings)
    return sorted(patterns, key=len, reverse=True)


def _match_at(plain: Sequence[str], pos: int, patterns: Sequence[Tuple[str, ...]]) -> int:
    for pattern in patterns:
        n = len(pattern)
        if tuple(plain[pos:pos + n]) == pattern:
            return n
    return 0


def strip_wake_words(norm: str, wake_words: Optional[Iterable[str]] = None) -> str:
    """Strip leading (and trailing) wake words from an already normalize_basic()'d text.

    Handles "hey jarvis", "jarvis ơi", "giắc vít", "quản gia", "quảng gia", ... and is
    accent-insensitive (ASR may drop or change diacritics).
    """
    words = tuple(DEFAULT_WAKE_WORDS) + tuple(wake_words or ())
    patterns = _token_patterns_cached(words)
    interjections = _token_patterns_cached(WAKE_INTERJECTIONS)
    tokens = norm.split()
    plain = [strip_accents(t) for t in tokens]

    def drop_vocative(toks: List[str], pl: List[str]) -> Tuple[List[str], List[str]]:
        if toks and (toks[0] in WAKE_VOCATIVES or pl[0] == "oi"):
            return toks[1:], pl[1:]
        return toks, pl

    changed = True
    while changed and tokens:
        changed = False
        for pre in interjections:
            n = len(pre)
            if tuple(plain[:n]) == pre and _match_at(plain, n, patterns):
                tokens, plain = tokens[n:], plain[n:]
                break
        n = _match_at(plain, 0, patterns)
        if n:
            tokens, plain = drop_vocative(tokens[n:], plain[n:])
            changed = True
    # trailing "... jarvis" / "... jarvis ơi"
    if len(tokens) > 1:
        end = len(tokens)
        if tokens[-1] in WAKE_VOCATIVES or plain[-1] == "oi":
            end -= 1
        for pattern in patterns:
            n = len(pattern)
            if 0 < end - n and tuple(plain[end - n:end]) == pattern:
                tokens = tokens[:end - n]
                break
    return " ".join(tokens)


def normalize_text(text: str, wake_words: Optional[Iterable[str]] = None) -> str:
    """Full normalization used for quick phrases: normalize_basic + strip_wake_words."""
    return strip_wake_words(normalize_basic(text), wake_words)


def _lcs_ratio(a: str, b: str, score_cutoff: float = 0.0) -> float:
    """Exact equivalent of rapidfuzz.fuzz.ratio: 100 * 2 * LCS / (len(a) + len(b))."""
    if not a and not b:
        return 100.0
    if not a or not b:
        return 0.0
    total = len(a) + len(b)
    if 200.0 * min(len(a), len(b)) / total < score_cutoff:
        return 0.0
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    score = 200.0 * prev[-1] / total
    return score if score >= score_cutoff else 0.0


def similarity(a: str, b: str) -> float:
    """Whole-string similarity 0..100 (rapidfuzz fuzz.ratio, i.e. normalized Indel)."""
    if _rf_fuzz is not None:
        return float(_rf_fuzz.ratio(a, b))
    return _lcs_ratio(a, b)


def detect_lang(text: str) -> str:
    """'vi' when the text has Vietnamese letters, 'en' when it only has ASCII letters."""
    norm = normalize_basic(text)
    if strip_accents(norm) != norm:
        return "vi"
    if re.search(r"[a-z]", norm):
        return "en"
    return "vi"


def pick_reply(reply: Union[None, str, Dict[str, str]], lang: str = "vi") -> Optional[str]:
    if reply is None:
        return None
    if isinstance(reply, str):
        return reply or None
    return reply.get(lang) or reply.get("vi") or reply.get("en") or None


def find_command_join(norm: str, exclude: str = "") -> Optional[str]:
    """Return the connector that joins two non-empty parts of ``norm`` ("A rồi B"), or None.

    Connectors that also occur in ``exclude`` (the matched quick phrase) are ignored. For an
    utterance without diacritics the accent-free connector spellings are accepted too.
    """
    tokens = norm.split()
    plain = [strip_accents(t) for t in tokens]
    no_diacritics = plain == tokens
    excluded = " " + normalize_basic(exclude) + " "
    excluded_plain = " " + strip_accents(normalize_basic(exclude)) + " "
    for connector in COMMAND_CONNECTORS:
        conn_tokens = tuple(normalize_basic(connector).split())
        conn_plain = tuple(strip_accents(t) for t in conn_tokens)
        if (" " + " ".join(conn_tokens) + " ") in excluded or \
                (no_diacritics and (" " + " ".join(conn_plain) + " ") in excluded_plain):
            continue
        n = len(conn_tokens)
        for i in range(1, len(tokens) - n):
            if tuple(tokens[i:i + n]) == conn_tokens or \
                    (no_diacritics and tuple(plain[i:i + n]) == conn_plain):
                return connector
    return None


@dataclass
class QuickMatch:
    task_id: str
    phrase: str
    score: float
    form: str            # "accented" | "unaccented"
    normalized: str
    plain: str
    lang: str


@dataclass
class QuickPhraseAnalysis:
    text: str
    basic: str                     # NFC + lowercase + punctuation stripped
    normalized: str                # ... + wake words stripped
    plain: str                     # accent-free variant of ``normalized``
    candidates: List[Tuple[float, str, str, str]]   # (score, task_id, phrase, form), best first
    match: Optional[QuickMatch]
    reason: str                    # matched | empty | no_phrases | below_threshold | ambiguous | multi_command
    connector: Optional[str] = None


def analyze_quick_phrase(text: str, phrases: Union[TaskTable, Sequence[PhraseEntry]],
                         threshold: float = 90.0, wake_words: Optional[Iterable[str]] = None,
                         accent_floor: float = 75.0, margin: float = 3.0,
                         top: int = 5) -> QuickPhraseAnalysis:
    """Score ``text`` against every quick phrase (whole utterance, rapidfuzz ratio).

    Score per phrase = max(accented ratio, accent-free ratio). The accent-free ratio is only
    used when one side has no diacritics or the accented ratio is >= ``accent_floor``, so
    "mở mặt na" (ASR tone error) matches "mở mặt nạ" but "tát mặt" does not match "tắt mắt".
    A match needs score >= ``threshold``, no near-tie (< ``margin``) with another task and no
    connector joining two commands.
    """
    index = phrases.phrase_index if isinstance(phrases, TaskTable) else list(phrases)
    basic = normalize_basic(text)
    norm = strip_wake_words(basic, wake_words)
    plain = strip_accents(norm)
    analysis = QuickPhraseAnalysis(text, basic, norm, plain, [], None, "empty")
    if not norm:
        return analysis
    if not index:
        analysis.reason = "no_phrases"
        return analysis
    utter_has_diacritics = plain != norm
    scored: List[Tuple[float, PhraseEntry, str]] = []
    for entry in index:
        acc = similarity(norm, entry.norm)
        unacc = 0.0
        if not utter_has_diacritics or entry.plain == entry.norm or acc >= accent_floor:
            unacc = similarity(plain, entry.plain)
        if unacc > acc:
            scored.append((unacc, entry, "unaccented"))
        else:
            scored.append((acc, entry, "accented"))
    scored.sort(key=lambda item: item[0], reverse=True)
    analysis.candidates = [(round(s, 2), e.task_id, e.phrase, f) for s, e, f in scored[:top]]
    best_score, best, form = scored[0]
    analysis.connector = find_command_join(norm, exclude=best.phrase)
    if best_score < threshold:
        analysis.reason = "below_threshold"
        return analysis
    # Checked before ambiguity: "A rồi B" naturally scores alike for both of its commands.
    if analysis.connector is not None:
        analysis.reason = "multi_command"
        return analysis
    rival = next((s for s, e, _ in scored if e.task_id != best.task_id), None)
    if rival is not None and best_score - rival < margin:
        analysis.reason = "ambiguous"
        return analysis
    analysis.reason = "matched"
    analysis.match = QuickMatch(best.task_id, best.phrase, best_score, form, norm, plain, best.lang)
    return analysis


def match_quick_phrase(text: str, phrases: Union[TaskTable, Sequence[PhraseEntry]],
                       threshold: float = 90.0, wake_words: Optional[Iterable[str]] = None,
                       accent_floor: float = 75.0, margin: float = 3.0) -> Optional[QuickMatch]:
    """Best quick-phrase match for a whole utterance, or None (see analyze_quick_phrase)."""
    return analyze_quick_phrase(text, phrases, threshold, wake_words, accent_floor, margin).match


# ---------------------------------------------------------------------------
# Spoken error texts
# ---------------------------------------------------------------------------
APOLOGY_PREFIX = "Xin lỗi thưa ngài, tôi không thực hiện được: "
_CJK_RE = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]+")
_UPSTREAM_PREFIXES = ("MCP错误:", "MCP错误：", "工具调用错误:", "工具调用错误：")


def apology(reason: str) -> str:
    """``Xin lỗi thưa ngài, tôi không thực hiện được: <lý do>`` (never empty, never Chinese)."""
    reason = " ".join(_CJK_RE.sub(" ", reason or "").split()).strip(" :;,") or "lỗi không xác định"
    text = APOLOGY_PREFIX + reason
    return text if text[-1] in ".!?" else text + "."


def describe_device_error(message: str) -> str:
    """Turn a device / upstream MCP error into a short Vietnamese reason (no Chinese)."""
    msg = (message or "").strip()
    for prefix in _UPSTREAM_PREFIXES:
        if msg.startswith(prefix):
            msg = msg[len(prefix):].strip()
            break
    m = re.match(r"invalid step #(\d+):\s*(.*)$", msg, re.S)
    if m:
        detail = _CJK_RE.sub(" ", m.group(2)).strip()
        return f"bước {m.group(1)} không hợp lệ ({detail})" if detail else f"bước {m.group(1)} không hợp lệ"
    m = re.match(r"Property '([^']*)': string length (\d+) exceeds maximum (\d+)", msg)
    if m:
        return f"chuỗi lệnh quá dài ({m.group(2)} byte, tối đa {m.group(3)})"
    m = re.match(r"Missing required argument: (.+)$", msg)
    if m:
        return f"thiếu tham số {m.group(1).strip()}"
    m = re.match(r"Unknown tool: (.+)$", msg)
    if m:
        return f"mũ không có lệnh {m.group(1).strip()}, cần cập nhật firmware"
    cleaned = " ".join(_CJK_RE.sub(" ", msg).split())
    if not cleaned:
        return "lỗi không xác định"
    return cleaned if len(cleaned) <= 160 else cleaned[:157] + "..."


# ---------------------------------------------------------------------------
# Shared sequence fixtures (tools/tests/fixtures/sequences/*.json)
# ---------------------------------------------------------------------------
def load_fixtures(directory: str) -> List[Dict[str, Any]]:
    """Load every ``*.json`` fixture (sorted by file name); raises ValueError if malformed."""
    fixtures = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        with open(path, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)
        missing = [k for k in FIXTURE_KEYS if k not in fixture]
        if missing:
            raise ValueError(f"{name}: missing keys {missing}")
        if not isinstance(fixture["valid"], bool):
            raise ValueError(f"{name}: 'valid' must be a boolean")
        for key in ("error_step", "estimated_ms"):
            if fixture[key] is not None and _int_value(fixture[key]) is None:
                raise ValueError(f"{name}: '{key}' must be an integer or null")
        fixture["_file"] = name
        fixtures.append(fixture)
    return fixtures


def check_fixture(fixture: Dict[str, Any]) -> Tuple[bool, str]:
    """Run one fixture through check_steps(); returns (passed, explanation)."""
    result = check_steps(fixture["steps"])
    problems = []
    if result.ok != fixture["valid"]:
        problems.append(f"expected valid={fixture['valid']}, got valid={result.ok} ({result.error})")
    elif result.ok:
        expected = fixture.get("estimated_ms")
        if expected is not None and result.estimated_ms != expected:
            problems.append(f"expected estimated_ms={expected}, got {result.estimated_ms}")
    else:
        error = result.error or ""
        step = fixture.get("error_step")
        if result.error_step != step:
            problems.append(f"expected error_step={step}, got {result.error_step} ({error})")
        if step is not None and not error.startswith(f"invalid step #{step}: "):
            problems.append(f"error should start with 'invalid step #{step}: ' ({error})")
        if step is None and error.startswith("invalid step #"):
            problems.append(f"top-level error expected ({error})")
        contains = fixture.get("error_contains")
        if contains and contains not in error:
            problems.append(f"error should contain {contains!r} ({error})")
    return (not problems, "; ".join(problems) if problems else "ok")

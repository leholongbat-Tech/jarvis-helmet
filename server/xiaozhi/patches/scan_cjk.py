#!/usr/bin/env python3
"""List CJK string literals left in the xiaozhi-server code paths that Jarvis uses.

Every string literal containing CJK characters is classified as:
  LOG   - argument of a logger call / print (never reaches the device)
  DOC   - docstring or bare string statement (comment-like)
  PUNCT - only CJK punctuation / full-width symbols (text-processing tables)
  CMP   - operand of a comparison / membership test (placeholder checks such as "你" in key)
  EXC   - argument of a raise statement (review: some exception texts are spoken)
  OTHER - anything else (may be spoken, displayed or sent to the LLM: review it)

Usage:
  python scan_cjk.py --root /opt/xiaozhi-esp32-server [--all] [--json]
Exit code is always 0 (this is a report, not a gate); --fail-on-other makes
it non-zero when OTHER entries exist.
Python 3.8+ (runs inside the xiaozhi image and in the dev venv).
"""
import argparse
import ast
import fnmatch
import json
import os
import re
import sys

CJK_RE = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
IDEOGRAPH_RE = re.compile(r"[㐀-䶿一-鿿]")

# Files reachable with the Jarvis configuration (minimal mode, OpenaiASR, openai LLM,
# jarvis_stream TTS, SileroVAD, function_call intent, nomem, device MCP).
ACTIVE_PATTERNS = [
    "app.py",
    "config/*.py",
    "core/*.py",
    "core/api/*.py",
    "core/handle/*.py",
    "core/handle/textHandler/*.py",
    "core/providers/asr/base.py",
    "core/providers/asr/openai.py",
    "core/providers/asr/dto/*.py",
    "core/providers/llm/base.py",
    "core/providers/llm/openai/openai.py",
    "core/providers/tts/base.py",
    "core/providers/tts/default.py",
    "core/providers/tts/jarvis_stream.py",
    "core/providers/tts/dto/*.py",
    "core/providers/vad/base.py",
    "core/providers/vad/silero.py",
    "core/providers/intent/base.py",
    "core/providers/intent/function_call/*.py",
    "core/providers/memory/base.py",
    "core/providers/memory/nomem/*.py",
    "core/providers/tools/*.py",
    "core/providers/tools/base/*.py",
    "core/providers/tools/server_plugins/*.py",
    "core/providers/tools/device_mcp/*.py",
    "core/utils/*.py",
    "core/utils/cache/*.py",
    "plugins_func/*.py",
    "plugins_func/functions/handle_exit_intent.py",
    "plugins_func/functions/get_time.py",
    "plugins_func/functions/helmet_tasks.py",
    "plugins_func/functions/ask_expert.py",
]

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "trace", "success"}


def _is_log_call(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "print":
        return True
    if isinstance(func, ast.Attribute) and func.attr in LOG_METHODS:
        # logger.info / self.logger.bind(...).info / conn.logger.bind(...).debug
        src = ast.dump(func.value)
        return "logger" in src or "bind" in src or "logging" in src
    return False


def _docstring_nodes(tree):
    """Docstrings and bare string statements (both are comments in practice)."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            docs.add(id(node.value))
    return docs


def _classify(node, parents, docs):
    if id(node) in docs:
        return "DOC"
    if not IDEOGRAPH_RE.search(node.value):
        return "PUNCT"
    cur, child = parents.get(id(node)), node
    while cur is not None:
        if _is_log_call(cur):
            return "LOG"
        if isinstance(cur, ast.Compare):
            return "CMP"
        if isinstance(cur, ast.Raise):
            return "EXC"
        if isinstance(cur, (ast.stmt,)) and not isinstance(cur, ast.Raise):
            # stop at the enclosing statement: everything else is a value that flows somewhere
            return "OTHER"
        cur, child = parents.get(id(cur)), cur
    return "OTHER"


def scan_file(path):
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return [{"line": e.lineno or 0, "kind": "SYNTAX", "text": str(e)}]
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    docs = _docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if not CJK_RE.search(node.value):
            continue
        kind = _classify(node, parents, docs)
        found.append({"line": getattr(node, "lineno", 0), "kind": kind, "text": node.value})
    found.sort(key=lambda d: d["line"])
    return found


def active_files(root):
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            if any(fnmatch.fnmatchcase(rel, pat) and rel.count("/") == pat.count("/") for pat in ACTIVE_PATTERNS):
                out.append(rel)
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.getcwd(), help="xiaozhi-server root (default: cwd)")
    ap.add_argument("--all", action="store_true", help="also print LOG, DOC, PUNCT and CMP entries")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fail-on-other", action="store_true", help="exit 1 if OTHER entries remain")
    try:  # before parse_args: --help contains CJK characters (Windows consoles)
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    args = ap.parse_args(argv)

    report = {}
    totals = {"LOG": 0, "DOC": 0, "PUNCT": 0, "CMP": 0, "EXC": 0, "OTHER": 0, "SYNTAX": 0}
    for rel in active_files(args.root):
        entries = scan_file(os.path.join(args.root, rel))
        for e in entries:
            totals[e["kind"]] = totals.get(e["kind"], 0) + 1
        shown = entries if args.all else [e for e in entries if e["kind"] in ("OTHER", "EXC", "SYNTAX")]
        if shown:
            report[rel] = shown
    if args.json:
        json.dump({"totals": totals, "files": report}, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        for rel, entries in report.items():
            print(rel)
            for e in entries:
                text = e["text"].replace("\n", "\\n")
                if len(text) > 90:
                    text = text[:87] + "..."
                print("  %5d %-6s %s" % (e["line"], e["kind"], text))
        print("totals: " + ", ".join("%s=%d" % kv for kv in sorted(totals.items())))
    if args.fail_on_other and (totals.get("OTHER") or totals.get("SYNTAX")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

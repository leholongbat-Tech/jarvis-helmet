#!/usr/bin/env python3
"""Kiểm tra và xem tasks.yaml của mũ Jarvis (SV6).

    python tools/tasks_cli.py validate [--file F] [--schema S | --no-schema]
    python tools/tasks_cli.py list [--json]
    python tools/tasks_cli.py steps <id> [<id> ...] [--pretty]
    python tools/tasks_cli.py match "<câu nói>" [--threshold 90]
    python tools/tasks_cli.py fixtures [--dir D]

Mã thoát: 0 = ổn; 1 = tasks.yaml lỗi / fixture sai; 2 = id không tồn tại;
3 = (match) câu nói không khớp câu khớp nhanh nào.
Logic nằm trong server/tasks/taskslib.py (cùng module mà plugin helmet_tasks dùng trên server).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS_DIR = os.path.join(REPO, "server", "tasks")
DEFAULT_TASKS_FILE = os.path.join(TASKS_DIR, "tasks.yaml")
DEFAULT_FIXTURES_DIR = os.path.join(REPO, "tools", "tests", "fixtures", "sequences")

if TASKS_DIR not in sys.path:
    sys.path.insert(0, TASKS_DIR)
import taskslib  # noqa: E402

REASONS_VI = {
    "below_threshold": "điểm cao nhất thấp hơn ngưỡng",
    "ambiguous": "hai tác vụ có điểm gần bằng nhau",
    "multi_command": "câu có nhiều lệnh",
    "empty": "câu rỗng sau khi bỏ dấu câu và wake word",
    "no_phrases": "tasks.yaml chưa có câu khớp nhanh nào",
}


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _schema_arg(args: argparse.Namespace):
    if getattr(args, "no_schema", False):
        return None
    return args.schema or "auto"


def _load(args: argparse.Namespace):
    """Load the tasks file or print every error (with line numbers) and return None."""
    try:
        return taskslib.load_tasks(args.file, schema=_schema_arg(args))
    except taskslib.TasksError as exc:
        print(f"LỖI: {args.file} không hợp lệ ({len(exc.issues)} lỗi):", file=sys.stderr)
        print(exc.format(), file=sys.stderr)
        return None


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.1f} s"


def cmd_validate(args: argparse.Namespace) -> int:
    table = _load(args)
    if table is None:
        return 1
    for warning in table.warnings:
        print(f"cảnh báo: {warning}", file=sys.stderr)
    synced = [t.id for t in table.tasks.values() if t.sync_to_device]
    schema = "không kiểm schema" if _schema_arg(args) is None else (
        "đã kiểm theo schema" if not table.warnings else "chưa kiểm schema")
    print(f"OK: {args.file} hợp lệ - {len(table.tasks)} tác vụ, {len(table.phrase_index)} câu khớp "
          f"nhanh, đồng bộ xuống mũ: {', '.join(synced) or '-'} ({schema})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    table = _load(args)
    if table is None:
        return 1
    if args.json:
        rows = [{
            "id": t.id,
            "function": f"task_{t.id}",
            "description": t.description,
            "quick_phrases": list(t.quick_phrases),
            "reply": t.reply,
            "sync_to_device": t.sync_to_device,
            "estimated_ms": t.estimated_ms,
            "steps": json.loads(t.steps_json),
            "steps_json": t.steps_json,
        } for t in table.tasks.values()]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{len(table.tasks)} tác vụ trong {args.file}:")
    for t in table.tasks.values():
        flags = [f"{len(t.steps)} bước", f"~{_seconds(t.estimated_ms)}", f"{t.steps_bytes} byte"]
        if t.sync_to_device:
            flags.append("đồng bộ xuống mũ")
        print(f"\n  {t.id}  (task_{t.id}; {', '.join(flags)})")
        print(f"      {t.description}")
        if t.reply is not None:
            reply = t.reply if isinstance(t.reply, str) else " | ".join(
                f"{lang}: {text}" for lang, text in t.reply.items())
            print(f"      trả lời: {reply}")
        if t.quick_phrases:
            print("      câu khớp nhanh: " + ", ".join(f'"{p}"' for p in t.quick_phrases))
        else:
            print("      câu khớp nhanh: - (chỉ qua LLM)")
    return 0


def cmd_steps(args: argparse.Namespace) -> int:
    table = _load(args)
    if table is None:
        return 1
    unknown = [i for i in args.ids if i not in table.tasks]
    if unknown:
        print(f"LỖI: không có tác vụ {', '.join(unknown)}. Có: {', '.join(table.ids)}", file=sys.stderr)
        return 2
    tasks = [table.tasks[i] for i in args.ids]
    if len(tasks) == 1:
        text, estimated = tasks[0].steps_json, tasks[0].estimated_ms
    else:
        try:
            merged, estimated = taskslib.merge_steps([t.steps for t in tasks])
        except taskslib.StepError as exc:
            print(f"LỖI: gộp {', '.join(args.ids)} vượt giới hạn: {exc.message}", file=sys.stderr)
            return 1
        text = taskslib.steps_to_json(merged)
    if args.pretty:
        print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
    else:
        print(text)
    print(f"# {len(json.loads(text))} bước, ước tính {estimated} ms, {len(text.encode('utf-8'))} byte"
          f" -> self.sequence.run {{\"steps\": <chuỗi trên>}}", file=sys.stderr)
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    table = _load(args)
    if table is None:
        return 1
    analysis = taskslib.analyze_quick_phrase(
        args.utterance, table, threshold=args.threshold, wake_words=args.wake_word,
        accent_floor=args.accent_floor, margin=args.margin, top=args.top)
    print(f"câu gốc            : {analysis.text}")
    print(f"chuẩn hóa          : {analysis.basic}")
    print(f"bỏ wake word       : {analysis.normalized}")
    print(f"bản bỏ dấu         : {analysis.plain}")
    print(f"từ nối nhiều lệnh  : {analysis.connector or '-'}")
    print(f"ngưỡng             : {args.threshold:g} (sàn có dấu {args.accent_floor:g}, "
          f"cách biệt {args.margin:g})")
    if analysis.candidates:
        print("ứng viên tốt nhất  :")
        for score, task_id, phrase, form in analysis.candidates:
            label = "có dấu" if form == "accented" else "bỏ dấu"
            print(f"    {score:6.2f}  {task_id:<14} \"{phrase}\" ({label})")
    match = analysis.match
    if match is not None:
        print(f"KẾT QUẢ: khớp {match.task_id} với \"{match.phrase}\" (điểm {match.score:.1f}) "
              f"-> chạy ngay, không qua LLM")
        return 0
    reason = REASONS_VI.get(analysis.reason, analysis.reason)
    if analysis.reason == "multi_command":
        reason += f" (từ nối '{analysis.connector}')"
    print(f"KẾT QUẢ: không khớp ({reason}) -> chuyển cho LLM")
    return 3


def cmd_fixtures(args: argparse.Namespace) -> int:
    try:
        fixtures = taskslib.load_fixtures(args.dir)
    except (OSError, ValueError) as exc:
        print(f"LỖI: không đọc được fixture: {exc}", file=sys.stderr)
        return 1
    failed = 0
    for fixture in fixtures:
        ok, why = taskslib.check_fixture(fixture)
        if not ok:
            failed += 1
            print(f"SAI  {fixture['_file']}: {why}")
        elif args.verbose:
            print(f"ĐẠT  {fixture['_file']}")
    print(f"{len(fixtures) - failed}/{len(fixtures)} fixture đạt ({args.dir})")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tasks_cli.py", description="Kiểm tra và xem tasks.yaml của mũ Jarvis (SV6).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", default=DEFAULT_TASKS_FILE,
                        help="đường dẫn tasks.yaml (mặc định: server/tasks/tasks.yaml)")
    common.add_argument("--schema", default=None,
                        help="đường dẫn schema.json (mặc định: cạnh tasks.yaml)")
    common.add_argument("--no-schema", action="store_true", help="bỏ qua kiểm tra theo schema.json")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", parents=[common], help="kiểm tra tasks.yaml (lỗi kèm số dòng)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("list", parents=[common], help="liệt kê tác vụ và câu khớp nhanh")
    p.add_argument("--json", action="store_true", help="in dạng JSON")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("steps", parents=[common],
                       help="in chuỗi JSON gửi xuống mũ (nhiều id: chuỗi đã gộp như một lượt LLM)")
    p.add_argument("ids", nargs="+", metavar="id")
    p.add_argument("--pretty", action="store_true", help="in thụt lề cho dễ đọc")
    p.set_defaults(func=cmd_steps)

    p = sub.add_parser("match", parents=[common], help="thử khớp nhanh một câu nói")
    p.add_argument("utterance")
    p.add_argument("--threshold", type=float, default=90.0, help="ngưỡng điểm 0..100 (mặc định 90)")
    p.add_argument("--accent-floor", type=float, default=75.0,
                   help="điểm có dấu tối thiểu để dùng điểm bỏ dấu (mặc định 75)")
    p.add_argument("--margin", type=float, default=3.0,
                   help="cách biệt tối thiểu với tác vụ đứng thứ hai (mặc định 3)")
    p.add_argument("--wake-word", action="append", default=[], help="thêm wake word (lặp được)")
    p.add_argument("--top", type=int, default=5, help="số ứng viên in ra")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("fixtures", help="chạy bộ fixture chuỗi bước dùng chung với firmware")
    p.add_argument("--dir", default=DEFAULT_FIXTURES_DIR)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_fixtures)
    return parser


def main(argv=None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

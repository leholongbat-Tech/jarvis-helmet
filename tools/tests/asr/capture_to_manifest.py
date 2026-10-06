#!/usr/bin/env python3
"""Turn a dataset captured by the ASR service (ASR_SAVE_DIR) into an eval manifest.

    python tools/tests/asr/capture_to_manifest.py --capture ./asr-dataset \
        --out tools/tests/asr/recordings/real

The reader follows sentences.md, so every captured utterance is matched to the closest script
sentence and ``reference`` is the SCRIPT text (ground truth), not the ASR output. The next
sentence in reading order wins near-ties. Low-similarity items get "needs_review": true:
listen to them and fix ``reference``/``group`` (or delete the line) before running eval.py.
Audio files are copied to <out>/audio/<id>.<ext> so the dataset folder is self-contained.
"""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asrlib  # noqa: E402

HERE = Path(__file__).resolve().parent


def similarity(a: str, b: str) -> float:
    """Character-level similarity of two normalized texts (robust to word splits)."""
    a, b = asrlib.normalize_text(a), asrlib.normalize_text(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def match(hyp: str, sentences: List[Dict], expected: int, near: float = 0.1) -> Tuple[int, float]:
    """Index and score of the best sentence; the expected (next in order) one wins near-ties."""
    scores = [similarity(hyp, s["text"]) for s in sentences]
    best = max(range(len(sentences)), key=lambda i: scores[i])
    if 0 <= expected < len(sentences) and scores[expected] >= scores[best] - near:
        best = expected
    return best, scores[best]


def load_records(capture: Path, include_gated: bool) -> List[Dict]:
    records = []
    for line in (capture / "transcripts.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if (rec.get("dropped") or "").startswith("gate:") and not include_gated:
            continue
        records.append(rec)
    return records


def build(records: List[Dict], sentences: List[Dict], capture: Path, out: Path,
          min_score: float, copy: bool) -> Tuple[List[Dict], List[str]]:
    items: List[Dict] = []
    used: Dict[str, int] = {}
    expected = 0
    for n, rec in enumerate(records, 1):
        hyp = rec.get("text") or rec.get("raw_text") or ""
        idx, score = match(hyp, sentences, expected) if hyp else (expected, 0.0)
        idx = min(idx, len(sentences) - 1)
        sent = sentences[idx]
        ok = score >= min_score
        if ok:
            expected = idx + 1
            used[sent["id"]] = used.get(sent["id"], 0) + 1
            item_id = sent["id"] if used[sent["id"]] == 1 else f"{sent['id']}_{used[sent['id']]}"
        else:
            item_id = f"U{n:03d}"
        src = capture / rec["audio"]
        audio_rel: str
        if copy:
            dst = out / "audio" / f"{item_id}{src.suffix}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            audio_rel = dst.relative_to(out).as_posix()
        else:
            audio_rel = str(src.resolve())
        items.append({
            "id": item_id,
            "audio": audio_rel,
            "reference": sent["text"] if ok else hyp,
            "group": sent["group"],
            "match_score": round(score, 3),
            "asr_text": hyp,
            "rid": rec.get("rid"),
            "captured": rec.get("ts"),
            "needs_review": not ok,
        })
    missing = [s["id"] for s in sentences if s["id"] not in used]
    return items, missing


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--capture", required=True, help="copy of ASR_SAVE_DIR (transcripts.jsonl + audio/)")
    p.add_argument("--out", default=str(HERE / "recordings" / "real"), help="dataset dir to create")
    p.add_argument("--sentences", default=str(HERE / "sentences.md"))
    p.add_argument("--min-score", type=float, default=0.5, help="below this the item needs review")
    p.add_argument("--include-gated", action="store_true",
                   help="also keep utterances the speech gate rejected (empty presses, noise)")
    p.add_argument("--no-copy", action="store_true", help="reference audio in place (absolute paths)")
    args = p.parse_args(argv)

    capture, out = Path(args.capture), Path(args.out)
    sentences = asrlib.load_sentences(Path(args.sentences))
    records = load_records(capture, args.include_gated)
    items, missing = build(records, sentences, capture, out, args.min_score, not args.no_copy)
    asrlib.write_manifest(out / "manifest.jsonl", items)
    review = [it["id"] for it in items if it["needs_review"]]
    print(f"{len(items)} utterances -> {out / 'manifest.jsonl'}")
    print(f"needs review ({len(review)}): {', '.join(review) or '-'}")
    print(f"sentences without a recording ({len(missing)}): {', '.join(missing) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

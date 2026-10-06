"""Optional dataset capture (ASR_SAVE_DIR): the uploaded audio + one JSON line per request.

Layout::

    <ASR_SAVE_DIR>/audio/<YYYYMMDD>/<HHMMSSmmm>_<rid>.wav   exact bytes sent by xiaozhi
    <ASR_SAVE_DIR>/transcripts.jsonl                       text, timing, engine config ...

tools/tests/asr/capture_to_manifest.py turns this into an eval manifest.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("jarvis_asr.capture")


class DatasetCapture:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        (self.root / "audio").mkdir(parents=True, exist_ok=True)
        probe = self.root / ".write_test"
        probe.write_text("ok", encoding="utf-8")  # fail early if not writable
        probe.unlink()
        self.jsonl = self.root / "transcripts.jsonl"
        self._lock = threading.Lock()

    def save(self, rid: str, audio_bytes: bytes, ext: str, record: Dict[str, Any]) -> Optional[str]:
        """Write audio + JSONL line; never raises (capture must not break recognition)."""
        try:
            now = datetime.now().astimezone()
            rel = Path("audio") / now.strftime("%Y%m%d") / f"{now.strftime('%H%M%S%f')[:9]}_{rid}.{ext}"
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio_bytes)
            line = {"ts": now.isoformat(timespec="milliseconds"), "rid": rid, "audio": rel.as_posix()}
            line.update(record)
            payload = json.dumps(line, ensure_ascii=False, default=str)
            with self._lock, open(self.jsonl, "a", encoding="utf-8") as fh:
                fh.write(payload + "\n")
            return rel.as_posix()
        except Exception as exc:  # noqa: BLE001
            log.warning("dataset capture failed for rid=%s: %s", rid, exc)
            return None

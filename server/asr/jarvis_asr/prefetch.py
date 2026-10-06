"""Download the configured model into the Hugging Face cache without loading it.

    docker compose run --rm --no-deps asr python -m jarvis_asr.prefetch
    docker run --rm -v jarvis_hf_cache:/cache/hf -e ASR_MODEL=large-v3 jarvis/asr:dev python -m jarvis_asr.prefetch
"""

from __future__ import annotations

import os
import sys

from .config import Settings
from .engines.faster_whisper_engine import resolve_model


def main() -> int:
    settings = Settings.from_env()
    if settings.engine != "faster_whisper":
        print(f"ASR_ENGINE={settings.engine}: nothing to prefetch")
        return 0
    model_id, revision = resolve_model(settings.model, settings.model_revision)
    if os.path.isdir(model_id):
        print(f"local model directory {model_id}: nothing to download")
        return 0
    from faster_whisper.utils import download_model

    path = download_model(model_id, revision=revision)
    print(f"{model_id}@{revision or 'latest'} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

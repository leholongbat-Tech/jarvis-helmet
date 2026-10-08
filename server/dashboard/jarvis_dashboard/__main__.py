"""python -m jarvis_dashboard: serve the dashboard (settings from the environment)."""
from __future__ import annotations

import logging
import sys

import uvicorn

from .config import Settings
from .main import create_app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if not settings.password:
        print("DASHBOARD_PASSWORD is empty: the page shows what the helmet heard, refusing to serve it "
              "without a password (server/README.md, section 3.7)", file=sys.stderr)
        return 2
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning",
                access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

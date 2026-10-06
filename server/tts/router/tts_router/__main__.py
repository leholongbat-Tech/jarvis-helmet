"""`python -m tts_router`: serve the router with uvicorn (HOST/PORT from the environment)."""
import uvicorn

from .config import Settings
from .main import create_app


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                log_level=settings.log_level, access_log=False)


if __name__ == "__main__":
    main()

"""`python -m server` — run Backchannel on 127.0.0.1 (never 0.0.0.0)."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app
from .config import load_dotenv, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m server", description="Run the Backchannel server.")
    parser.add_argument("--port", type=int, help="override the port from config.json / BACKCHANNEL_PORT")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The Mac app starts us directly (no shell), so pick up ANTHROPIC_API_KEY etc. from .env here.
    loaded = load_dotenv()
    if loaded:
        logging.getLogger("backchannel").info("Loaded %s from .env", ", ".join(sorted(loaded)))
    settings = load_settings()
    if args.port:
        settings.port = args.port
    app = create_app(settings)
    # The live-update stream never ends on its own, so cap graceful shutdown. Otherwise a stopped
    # server keeps old streams open and open windows never notice it went away.
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level=args.log_level, access_log=False,
                proxy_headers=False, server_header=False, timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()

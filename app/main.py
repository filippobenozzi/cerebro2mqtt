from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

from app.config_store import ConfigStore
from app.service import BridgeService
from app.web import create_web_app


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def main() -> None:
    setup_logging()

    config_path = Path(os.getenv("ALGODOMO_CONFIG", "./config/config.json"))
    store = ConfigStore(config_path)
    service = BridgeService(store)
    service.start()

    app = create_web_app(store, service)

    def _shutdown(signum, frame):
        service.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    cfg = store.config
    app.run(host=cfg.web.host, port=cfg.web.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

import threading

import uvicorn

from .api import create_app
from .brokers import make_broker
from .config import Config
from .engine import Engine
from .risk import RiskManager
from .store import Store
from .strategy.sma_cross import SmaCross


def main() -> None:
    cfg = Config()
    cfg.validate()
    engine = Engine(cfg, make_broker(cfg), SmaCross(cfg.sma_fast, cfg.sma_slow), Store(cfg.db_path), RiskManager(cfg))
    threading.Thread(target=engine.run, daemon=True).start()
    try:
        uvicorn.run(create_app(engine, cfg.api_token), host=cfg.api_host, port=cfg.api_port)
    finally:
        engine.stop()
        engine.broker.close()


if __name__ == "__main__":
    main()

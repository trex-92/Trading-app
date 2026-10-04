from ..config import Config
from .base import Broker


def make_broker(cfg: Config) -> Broker:
    if cfg.broker == "paper":
        from .paper import PaperBroker
        return PaperBroker()
    if cfg.broker == "moomoo":
        import os
        from .moomoo import MoomooBroker
        return MoomooBroker(os.getenv("MOOMOO_HOST", "127.0.0.1"), int(os.getenv("MOOMOO_PORT", "11111")),
                            cfg.trade_env, os.getenv("MOOMOO_MARKET", "US"))
    if cfg.broker == "moomoo_rest":
        import os
        from .moomoo_rest import MoomooRestBroker
        return MoomooRestBroker(cfg.trade_env, os.getenv("MOOMOO_ACC_ID", ""))
    if cfg.broker == "webull":
        from .webull import WebullBroker
        return WebullBroker()
    raise SystemExit(f"Unknown BROKER={cfg.broker}")

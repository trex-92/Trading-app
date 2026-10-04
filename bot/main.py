from .brokers import make_broker
from .config import Config
from .engine import Engine
from .risk import RiskManager
from .store import Store
from .strategy.sma_cross import SmaCross
from .sync import SupabaseSync


def main() -> None:
    cfg = Config()
    cfg.validate()
    sync = SupabaseSync(cfg.supabase_url, cfg.supabase_service_key, cfg.supabase_user_id)
    engine = Engine(cfg, make_broker(cfg), SmaCross(cfg.sma_fast, cfg.sma_slow),
                    Store(cfg.db_path, sync), RiskManager(cfg))
    try:
        engine.run()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        engine.broker.close()


if __name__ == "__main__":
    main()

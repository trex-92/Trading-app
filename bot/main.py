import os
import threading

from .brokers import make_broker
from .config import Config
from .engine import Engine
from .risk import RiskManager
from .store import Store
from .strategy.sma_cross import SmaCross
from .intraday.worker import BacktestWorker
from .sync import SupabaseSync


def main() -> None:
    cfg = Config()
    cfg.validate()
    sync = SupabaseSync(cfg.supabase_url, cfg.supabase_service_key, cfg.supabase_user_id)
    engine = Engine(cfg, make_broker(cfg), SmaCross(cfg.sma_fast, cfg.sma_slow),
                    Store(cfg.db_path, sync), RiskManager(cfg))
    stop = threading.Event()
    if os.getenv("BACKTEST_WORKER", "yes") == "yes":
        bars = getattr(engine.broker, "intraday_bars", None)
        if bars is None:
            print("[backtest] worker off: this broker has no historical intraday data (use BROKER=moomoo_rest)")
        else:
            worker = BacktestWorker(sync, bars, os.getenv("CALENDAR_FILE", "data/calendar.json"))
            threading.Thread(target=worker.run_forever, args=(stop,), daemon=True).start()
    try:
        engine.run()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        engine.stop()
        engine.broker.close()


if __name__ == "__main__":
    main()

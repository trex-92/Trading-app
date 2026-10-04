import os
import threading

from .brokers import make_broker
from .config import Config
from .engine import Engine
from .risk import RiskManager
from .store import Store
from .strategy.sma_cross import SmaCross
from .intraday.live import LiveRunner
from .intraday.params import SharedParams, apply_overrides
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
    if os.getenv("LIVE_STRATEGIES", "no") == "yes":
        if cfg.broker != "moomoo_rest" or cfg.trade_env != "SIMULATE":
            raise SystemExit("LIVE_STRATEGIES=yes is paper-only: it needs BROKER=moomoo_rest and TRADE_ENV=SIMULATE")
        env_map = {"RISK_PER_TRADE_PCT": "risk_per_trade_pct", "RISK_HARD_CEILING_PCT": "risk_hard_ceiling_pct",
                   "DAILY_MAX_LOSS_PCT": "daily_max_loss_pct"}
        try:
            shared = apply_overrides(SharedParams(), {v: os.environ[k] for k, v in env_map.items() if os.getenv(k)}).validate()
            runner = LiveRunner(engine.broker, sync, universe=[x.strip().upper() for x in os.getenv("STRATEGY_UNIVERSE", "SPY,QQQ").split(",")],
                                configs=sync.get_strategy_configs, calendar_path=os.getenv("CALENDAR_FILE", "data/calendar.json"),
                                halted=lambda: engine.risk.halted, shared=shared)
        except ValueError as e:
            raise SystemExit(f"Refusing to start the strategy engine: {e}")
        threading.Thread(target=runner.run_forever, args=(stop,), daemon=True).start()
        print("[live] strategy engine running on the SIMULATED account. Stops are held by this bot, not the broker.")
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

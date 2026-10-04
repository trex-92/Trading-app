import os
import threading

from .brokers import make_broker
from .config import Config
from .engine import Engine
from .risk import RiskManager
from .store import Store
from .strategy.sma_cross import SmaCross
from .brokers.local_paper import LocalPaperBroker
from .intraday.live import LiveRunner
from .intraday.market import DEFAULTS, get_market
from .intraday.params import apply_overrides
from .intraday.runner import base_shared
from .intraday.supervisor import MarketSupervisor
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
            worker = BacktestWorker(sync, bars, os.getenv("CALENDAR_FILE", "data/calendar.json"),
                                    markets_path=os.getenv("MARKETS_FILE", "data/markets.json"))
            threading.Thread(target=worker.run_forever, args=(stop,), daemon=True).start()
    if os.getenv("LIVE_STRATEGIES", "no") == "yes":
        if cfg.broker != "moomoo_rest" or cfg.trade_env != "SIMULATE":
            raise SystemExit("LIVE_STRATEGIES=yes is paper-only: it needs BROKER=moomoo_rest and TRADE_ENV=SIMULATE")
        env_map = {"RISK_PER_TRADE_PCT": "risk_per_trade_pct", "RISK_HARD_CEILING_PCT": "risk_hard_ceiling_pct",
                   "DAILY_MAX_LOSS_PCT": "daily_max_loss_pct"}
        risk_env = {v: os.environ[k] for k, v in env_map.items() if os.getenv(k)}
        data_broker = engine.broker
        markets_file = os.getenv("MARKETS_FILE", "data/markets.json")

        def make_runner(code, row, universe):
            market = get_market(code, markets_file)
            shared = apply_overrides(base_shared(market), risk_env).validate()  # refuses to start above the risk ceiling
            if market.sim_account:
                broker = data_broker                                         # Moomoo simulated account
            else:
                broker = LocalPaperBroker(data_broker, market, f"data/paper_{code}.json", float(row.get("paper_balance") or 0))
            rows = lambda c=code: [r for r in sync.get_strategy_configs() if r.get("market", "US") == c]  # noqa: E731
            return LiveRunner(broker, sync, universe=universe, configs=rows, calendar_path=os.getenv("CALENDAR_FILE", "data/calendar.json"),
                              state_path=f"data/live_state_{code}.json", journal_path=f"data/journal_{code}.jsonl",
                              halted=lambda: engine.risk.halted, shared=shared, market=market)

        try:
            for code in DEFAULTS:  # fail fast on a bad risk setting even before a market is switched on
                apply_overrides(base_shared(get_market(code, markets_file)), risk_env).validate()
        except ValueError as e:
            raise SystemExit(f"Refusing to start the strategy engine: {e}")
        supervisor = MarketSupervisor(sync, make_runner, markets_path=markets_file)
        threading.Thread(target=supervisor.run_forever, args=(stop,), daemon=True).start()
        print("[live] strategy engines are paper-only. Stops are held by this bot, not the broker. "
              "Choose markets in the app: Settings > Markets.")
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

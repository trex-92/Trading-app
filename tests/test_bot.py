from bot.brokers.paper import PaperBroker
from bot.config import Config
from bot.engine import Engine
from bot.models import Order
from bot.risk import RiskManager
from bot.store import Store
from bot.strategy.sma_cross import SmaCross


def make(**kw):
    cfg = Config(symbols=["AAPL"], sma_fast=3, sma_slow=5, **kw)
    store, risk = Store(":memory:"), RiskManager(cfg)
    return cfg, Engine(cfg, PaperBroker(seed=1), SmaCross(3, 5), store, risk)


def test_sma_cross_signals():
    s = SmaCross(2, 4)
    assert s.signal([10, 10, 10, 9, 12], 0) == "BUY"
    assert s.signal([10, 10, 10, 11, 8], 1) == "SELL"
    assert s.signal([10, 10, 10, 9, 12], 1) is None


def test_risk_limits():
    cfg, eng = make(max_position_qty=2)
    r = eng.risk
    assert r.check(Order("A", "BUY", 3), 0, 0, 0) == "max position size exceeded"
    assert r.check(Order("A", "SELL", 1), 0, 0, 0)
    assert r.check(Order("A", "BUY", 1), 0, 0, -cfg.max_daily_loss)
    r.halted = True
    assert r.check(Order("A", "BUY", 1), 0, 0, 0) == "trading halted"


def test_engine_runs_and_respects_halt():
    _, eng = make()
    for _ in range(40):
        eng.tick()
    assert eng.store.orders()  # random walk should trade at least once
    eng.risk.halted = True
    n = len(eng.store.orders(500))
    for _ in range(40):
        eng.tick()
    new = eng.store.orders(500)[: len(eng.store.orders(500)) - n]
    assert all(o["status"] == "BLOCKED" for o in new)


class FakeSync:
    def __init__(self, cmds):
        self.cmds, self.snaps, self.orders, self.events = cmds, [], [], []

    def claim_commands(self):
        out, self.cmds = self.cmds, []
        return out

    def push_snapshot(self, s): self.snaps.append(s)
    def push_order(self, o): self.orders.append(o)
    def push_event(self, level, msg): self.events.append((level, msg))


def test_commands_from_app_toggle_halt_and_mirror():
    _, eng = make()
    eng.store.sync = FakeSync(["halt"])
    eng.apply_commands()
    assert eng.risk.halted and eng.store.sync.snaps[-1]["halted"] is True
    eng.store.sync.cmds = ["halt", "resume"]
    eng.apply_commands()
    assert not eng.risk.halted
    eng.tick()
    assert eng.store.sync.snaps  # tick pushes a snapshot too


def test_other_live_host_detects_recent_foreign_engine():
    import httpx
    from datetime import datetime, timezone
    from bot import sync as S
    fresh = datetime.now(timezone.utc).isoformat()
    def make(rows):
        h = httpx.MockTransport(lambda req: httpx.Response(200, json=rows))
        return S.SupabaseSync("https://x.supabase.co", "k", "u", client=httpx.Client(base_url="https://x.supabase.co/rest/v1", transport=h))
    assert make([{"state": {"host": "other-pc"}, "updated_at": fresh}]).other_live_host() == "other-pc"
    assert make([{"state": {"host": S.HOST}, "updated_at": fresh}]).other_live_host() is None
    assert make([{"state": {"host": "other-pc"}, "updated_at": "2020-01-01T00:00:00+00:00"}]).other_live_host() is None

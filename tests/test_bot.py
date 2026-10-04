from fastapi.testclient import TestClient

from bot.api import create_app
from bot.brokers.paper import PaperBroker
from bot.config import Config
from bot.engine import Engine
from bot.models import Order
from bot.risk import RiskManager
from bot.store import Store
from bot.strategy.sma_cross import SmaCross


def make(**kw):
    cfg = Config(symbols=["AAPL"], sma_fast=3, sma_slow=5, api_token="t", db_path=":memory:", **kw)
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


def test_api_auth_and_halt():
    cfg, eng = make()
    c = TestClient(create_app(eng, "t"))
    assert c.get("/status").status_code == 401
    h = {"Authorization": "Bearer t"}
    assert c.get("/status", headers=h).json()["halted"] is False
    c.post("/halt", headers=h)
    assert eng.risk.halted

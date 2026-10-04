import json
import os
import stat
import time

import httpx
import pytest

from bot.brokers.moomoo_rest import MoomooRestBroker
from bot.models import Order
from bot.moomoo_oauth import AuthError, OAuthSession, TokenStore, authorize_url, pkce_pair


def store(tmp_path, **over):
    s = TokenStore(str(tmp_path / "t.json"))
    s.save({"client_id": "cid", "access_token": "AT", "expires_at": time.time() + 3600,
            "refresh_token": "RT", **over})
    return s


def broker(tmp_path, handler, env="SIMULATE", acc="1", cache_dir=None, now=None, **store_over):
    http = httpx.Client(base_url="https://webapi.moomoo.com", transport=httpx.MockTransport(handler))
    sess = OAuthSession(store(tmp_path, **store_over), httpx.Client(transport=httpx.MockTransport(handler)))
    return MoomooRestBroker(env, acc, sess, http, sleep=lambda s: None, cache_dir=cache_dir,
                           **({'now': now} if now else {}))


def ok_sim(data):
    return httpx.Response(200, json={"ret_code": 0, "data": data})


def ok_trd(d):
    return httpx.Response(200, json={"s": "ok", "d": d})


def test_pkce_challenge_matches_verifier():
    import base64, hashlib
    v, c = pkce_pair()
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    url = authorize_url("cid", c, "st")
    assert "code_challenge_method=S256" in url and "state=st" in url and "client_id=cid" in url


def test_token_file_is_private(tmp_path):
    s = store(tmp_path)
    assert stat.S_IMODE(os.stat(s.path).st_mode) == 0o600


def test_refreshes_expired_token_and_persists(tmp_path):
    calls = []

    def h(req):
        calls.append(req)
        assert req.url.path == "/oauth2/token" and b"grant_type=refresh_token" in req.content
        return httpx.Response(200, json={"access_token": "NEW", "expires_in": 7200, "scope": "trade:read"})

    s = store(tmp_path, expires_at=time.time() - 5)
    sess = OAuthSession(s, httpx.Client(transport=httpx.MockTransport(h)))
    assert sess.access_token() == "NEW"
    assert sess.access_token() == "NEW" and len(calls) == 1  # cached
    assert s.load()["refresh_token"] == "RT"


def test_not_logged_in_raises(tmp_path):
    with pytest.raises(AuthError):
        OAuthSession(TokenStore(str(tmp_path / "none.json"))).access_token()


def test_sim_reads_and_order_body(tmp_path):
    seen = {}

    def h(req):
        p = req.url.path
        assert req.headers["authorization"] == "Bearer AT"
        if p.endswith("/history-kline"):
            return ok_sim({"kline_list": [{"time_key": 2, "close": 11.0}, {"time_key": 1, "close": 10.0}]})
        if p.endswith("/snapshot"):
            return ok_sim({"snapshot_list": [{"last_price": 12.5}]})
        if p.endswith("/cash-info"):
            return ok_sim({"balance": "1000"})
        if p.endswith("/positions"):
            return ok_sim({"positions": [{"symbol": "AAPL", "qty": "3", "cost_price": "10", "cur_price": "12", "pstn_type": 0},
                                         {"symbol": "X", "qty": "0", "cost_price": "1", "cur_price": "1"}]})
        if p.endswith("/orders"):
            seen.update(json.loads(req.content))
            return ok_sim({"order_id": "77"})
        raise AssertionError(p)

    b = broker(tmp_path, h)
    assert b.history("AAPL", 2) == [10.0, 11.0]  # sorted ascending
    assert b.last_price("AAPL") == 12.5 and b.cash() == 1000.0
    pos = b.positions()
    assert len(pos) == 1 and pos[0].symbol == "AAPL" and pos[0].qty == 3
    o = b.place_order(Order("AAPL", "SELL", 2))
    assert (o.status, o.id) == ("SUBMITTED", "77")
    # marketable limit: 1% below the 12.5 last price for a sell; plain market orders are rejected by the sim
    assert seen == {"market": 2, "symbol": "AAPL", "order_type": 1, "order_side": 2, "qty": "2", "price": "12.38"}
    assert o.price == 12.38


def test_real_order_body_and_confirmation_is_not_auto_confirmed(tmp_path):
    bodies = []

    def h(req):
        if req.url.path.endswith("/order_confirm"):
            raise AssertionError("must never auto-confirm")
        if req.url.path.endswith("/snapshot"):
            return ok_sim({"snapshot_list": [{"last_price": 100.0}]})
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"s": "error", "errcode": -2100, "errmsg": "Order confirmation required.",
                                         "need_order_confirm": True, "confirm_id": "abc"})

    b = broker(tmp_path, h, env="REAL", acc="9")
    o = b.place_order(Order("AAPL", "BUY", 1))
    assert bodies[0] == {"code": "US.AAPL", "qty": "1", "side": "BUY", "order_type": "LIMIT", "price": "101.0000",
                         "time_in_force": "DAY", "session": "RTH"}
    assert o.status == "REJECTED" and "abc" in o.reason


def test_401_triggers_one_refresh_then_retries(tmp_path):
    n = {"api": 0}

    def h(req):
        if req.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "FRESH", "expires_in": 7200})
        n["api"] += 1
        if req.headers["authorization"] == "Bearer AT":
            return httpx.Response(401, json={})
        return ok_sim({"balance": "5"})

    assert broker(tmp_path, h).cash() == 5.0 and n["api"] == 2


def test_429_backs_off_and_error_envelope_becomes_rejected_order(tmp_path):
    n = {"c": 0}

    def h(req):
        if req.url.path.endswith("/snapshot"):
            return ok_sim({"snapshot_list": [{"last_price": 10.0}]})
        n["c"] += 1
        if n["c"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json={"ret_code": -3, "ret_msg": "bad qty"})

    o = broker(tmp_path, h).place_order(Order("AAPL", "BUY", 1))
    assert n["c"] == 2 and o.status == "REJECTED" and "bad qty" in o.reason


def test_account_discovery(tmp_path):
    def sim(req):
        return ok_sim({"accounts": [{"account_id": "1", "market_id": 1}, {"account_id": "2", "market_id": 100}]})
    b = broker(tmp_path, sim, acc="")
    assert b.acc_id == "2" and b.sim_market == 100  # real service labels the US sim account 100

    def real(req):
        return ok_trd({"accounts": [{"account_id": 5}, {"account_id": 6}]})
    with pytest.raises(Exception, match="MOOMOO_ACC_ID"):
        broker(tmp_path, real, env="REAL", acc="")


def test_sim_positions_falls_back_to_market_filter(tmp_path):
    seen = []

    def h(req):
        seen.append(req.url.params.get("market"))
        if req.url.params.get("market") != "100":
            return httpx.Response(200, json={"ret_code": -5, "ret_msg": "backend business error"})
        return ok_sim({"positions": [{"symbol": "AAPL", "qty": "1", "cost_price": "1", "cur_price": "2"}]})

    b = broker(tmp_path, h)
    b.sim_market = 100
    assert [p.symbol for p in b.positions()] == ["AAPL"]
    b.positions()
    assert seen == ["100", "100"]  # remembered the working filter


def test_paper_engine_calls(tmp_path):
    def h(req):
        p = req.url.path
        if p.endswith("/quote/snapshot"):
            return ok_sim({"snapshot_list": [{"code": "US.SPY", "last_price": 601.25, "bid_price": 601.2, "ask_price": 601.3,
                                              "update_time": 1790000000000}]})
        if p.endswith("/history-kline"):
            return ok_sim({"kline_list": [{"time_key": 1790000000000 + 60000 * i, "open": 1, "high": 2, "low": 0.5,
                                           "close": 1.5, "volume": 10} for i in range(5)]})
        if p.endswith("/cash-info"):
            return ok_sim({"balance": "1", "total_asset": "999.5"})
        if p.endswith("/cancel"):
            seen["cancel"] = p
            return ok_sim({"order_id": "9"})
        if p.endswith("/orders"):
            return ok_sim({"orders": [
                {"order_id": "1", "side": 1, "symbol": "SPY", "status": 4, "cum_qty": "10", "avg_fill_price": "600.5"},
                {"order_id": "2", "side": 2, "symbol": "QQQ", "status": 3, "cum_qty": "5", "avg_fill_price": "500"},
                {"order_id": "3", "side": 1, "symbol": "SPY", "status": 5, "cum_qty": "0", "avg_fill_price": "0"}]})
        raise AssertionError(p)

    seen = {}
    b = broker(tmp_path, h)
    snap = b.snapshot(["SPY"])["SPY"]
    assert snap["last"] == 601.25 and snap["ts"].tzinfo is not None and snap["bid"] == 601.2
    assert len(b.recent_bars("SPY", 3)) == 3 and b.equity() == 999.5
    assert b.order_status("1") == {"status": "FILLED", "filled_qty": 10.0, "avg_price": 600.5}
    assert b.order_status("2")["status"] == "PARTIAL" and b.order_status("404")["status"] == "UNKNOWN"
    assert [(o["id"], o["side"]) for o in b.open_orders()] == [("2", "SELL")]
    b.cancel_order("2")
    assert seen["cancel"].endswith("/orders/2/cancel")


def test_paper_engine_calls_refuse_the_real_account(tmp_path):
    b = broker(tmp_path, lambda req: ok_trd({}), env="REAL", acc="9")
    for call in (lambda: b.order_status("1"), lambda: b.open_orders(), lambda: b.cancel_order("1")):
        with pytest.raises(NotImplementedError):
            call()


def _observed_history_endpoint(days_available, cap=1000):
    """Emulates what the real endpoint did on the user's account: first `cap` bars at/after `start`, ascending,
    `end` exclusive, next_time always None."""
    from bot.intraday.data import synthetic_day
    from bot.intraday.market import US
    all_rows = []
    for d in sorted(days_available):
        for b in synthetic_day(d, "range", 1, 100.0, US):
            all_rows.append((b.ts, {"time_key": int(b.ts.timestamp() * 1000), "open": b.open, "high": b.high, "low": b.low,
                                    "close": b.close, "volume": b.volume}))
    calls = []

    def h(req):
        from datetime import date
        s_, e_ = date.fromisoformat(req.url.params["start"]), date.fromisoformat(req.url.params["end"])
        calls.append((s_, e_))
        rows = [r for ts, r in all_rows if s_ <= ts.date() < e_][:cap]
        return ok_sim({"kline_list": rows, "next_time": None})

    return h, calls


def test_history_is_read_forward_in_pages_until_the_whole_range_is_covered(tmp_path):
    from datetime import date
    from bot.intraday.data import business_days
    wanted = business_days(date(2026, 9, 1), date(2026, 9, 30))
    h, calls = _observed_history_endpoint(wanted)
    bars = broker(tmp_path, h).intraday_bars("SPY", date(2026, 9, 1), date(2026, 9, 30))
    assert sorted({b.ts.date() for b in bars}) == wanted          # every trading day, including the last one (end is exclusive)
    assert len(bars) == len({b.ts for b in bars}) == 390 * len(wanted)   # no duplicates, nothing lost
    assert all(c[0] < c[1] for c in calls) and [c[0] for c in calls] == sorted(c[0] for c in calls)  # moves forward
    assert 8 <= len(calls) <= 14                                    # ~1000 bars per call, not one call per bar or per day


def test_history_stops_when_the_data_ends_and_handles_empty_ranges(tmp_path):
    from datetime import date
    h, calls = _observed_history_endpoint([date(2026, 9, 2)])
    b = broker(tmp_path, h)
    assert len(b.intraday_bars("SPY", date(2026, 9, 1), date(2026, 9, 30))) == 390 and len(calls) == 1
    assert b.intraday_bars("SPY", date(2026, 10, 1), date(2026, 10, 5)) == []


def test_recent_bars_reads_todays_bars_forward(tmp_path):
    from datetime import datetime
    from bot.intraday.bars import NY
    seen = {}

    def h(req):
        seen.update(dict(req.url.params))
        return ok_sim({"kline_list": [{"time_key": 1790000000000 + 60000 * i, "open": 1, "high": 2, "low": 0.5, "close": 1.5,
                                       "volume": 10} for i in range(40)]})

    bars = broker(tmp_path, h).recent_bars("SPY", 15)
    today = datetime.now(NY).date()
    assert len(bars) == 15 and seen["start"] == today.isoformat() and seen["end"] > seen["start"]


def test_rate_limit_replies_are_retried_with_backoff(tmp_path):
    sleeps, n = [], {"c": 0}

    def h(req):
        n["c"] += 1
        if n["c"] <= 2:
            return httpx.Response(200, json={"ret_code": -9, "ret_msg": "rate limit exceeded"})
        return ok_sim({"balance": "5"})

    b = broker(tmp_path, h)
    b._sleep = sleeps.append
    assert b.cash() == 5.0 and sleeps == [2, 4]                      # waited 2s then 4s
    n["c"] = -100                                                     # never recovers: gives up after the last wait
    with pytest.raises(Exception, match="rate limit"):
        b.cash()
    assert sleeps[2:] == [2, 4, 8, 16, 32]


def test_finished_days_are_cached_so_reruns_do_not_hit_the_api(tmp_path):
    from datetime import date, datetime, timedelta
    from bot.intraday.bars import NY
    from bot.intraday.data import business_days
    wanted = [d for d in business_days(date(2026, 9, 1), date(2026, 9, 30)) if d != date(2026, 9, 7)]   # Sep 7 = holiday, no bars
    h, calls = _observed_history_endpoint(wanted)
    b = broker(tmp_path, h, cache_dir=tmp_path / "cache")
    first = b.intraday_bars("SPY", date(2026, 9, 1), date(2026, 9, 30))
    n1 = len(calls)
    assert sorted({x.ts.date() for x in first}) == wanted and n1 >= 8
    again = b.intraday_bars("SPY", date(2026, 9, 1), date(2026, 9, 30))
    assert len(calls) == n1 and [x.ts for x in again] == [x.ts for x in first]          # second run: zero requests
    assert (tmp_path / "cache" / "US_SPY_2026-09-07.json").read_text() == "[]"           # the holiday is remembered as empty
    # a longer range only fetches the new tail
    h2, calls2 = _observed_history_endpoint(wanted + [date(2026, 10, 1)])
    b2 = broker(tmp_path, h2, cache_dir=tmp_path / "cache")
    longer = b2.intraday_bars("SPY", date(2026, 9, 1), date(2026, 10, 1))
    assert len(calls2) == 1 and calls2[0][0] == date(2026, 10, 1) and date(2026, 10, 1) in {x.ts.date() for x in longer}


def test_today_is_never_cached_because_it_is_still_changing(tmp_path):
    from datetime import date, datetime
    from bot.intraday.bars import NY
    today = date(2026, 9, 30)
    h, calls = _observed_history_endpoint([today])
    b = broker(tmp_path, h, cache_dir=tmp_path / "cache", now=lambda tz: datetime(2026, 9, 30, 11, 0, tzinfo=NY))
    b.intraday_bars("SPY", today, today)
    b.intraday_bars("SPY", today, today)
    assert len(calls) == 2 and not list((tmp_path / "cache").glob("*"))


def test_history_requests_are_throttled_but_other_calls_are_not(tmp_path):
    from datetime import date
    h, calls = _observed_history_endpoint([date(2026, 9, 2)])
    clock = {"t": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(round(s, 1))
        clock["t"] += s

    b = broker(tmp_path, lambda req: h(req) if req.url.path.endswith("/history-kline") else ok_sim({"balance": "1"}))
    b._sleep, b._clock, b._hist_limit = sleep, lambda: clock["t"], (3, 30.0)
    for _ in range(7):
        b._request("GET", "/api/v1.0/quote/US.SPY/history-kline", params={"start": "2026-09-02", "end": "2026-09-03"})
    # 3 per 30 s: calls 4-6 wait for the first window to clear, call 7 for the second
    assert len(sleeps) == 2 and all(abs(x - 30.0) < 0.2 for x in sleeps)
    sleeps.clear()
    for _ in range(10):
        b._request("GET", "/api/v1.0/sim-trade/1/cash-info")   # not a history path: untouched
    assert sleeps == []


def test_a_failed_run_keeps_the_days_it_already_downloaded(tmp_path):
    from datetime import date
    from bot.intraday.data import business_days
    wanted = business_days(date(2026, 9, 1), date(2026, 9, 18))
    h_ok, calls = _observed_history_endpoint(wanted)
    n = {"c": 0}

    def flaky(req):
        n["c"] += 1
        if n["c"] > 4:                      # the service starts refusing after four pages, forever
            return httpx.Response(200, json={"ret_code": -9, "ret_msg": "rate limit exceeded"})
        return h_ok(req)

    b = broker(tmp_path, flaky, cache_dir=tmp_path / "cache")
    with pytest.raises(Exception, match="rate limit"):
        b.intraday_bars("SPY", date(2026, 9, 1), date(2026, 9, 18))
    saved = sorted(p.name for p in (tmp_path / "cache").glob("US_SPY_*.json"))
    assert len(saved) >= 5                                      # progress was kept
    n["c"] = -1000                                              # service healthy again
    before = len(calls)
    b2 = broker(tmp_path, flaky, cache_dir=tmp_path / "cache")
    bars = b2.intraday_bars("SPY", date(2026, 9, 1), date(2026, 9, 18))
    assert sorted({x.ts.date() for x in bars}) == wanted
    first_resume_start = calls[before][0]
    assert first_resume_start > date(2026, 9, 1)                # resumed after the saved days, not from the beginning


def test_fractional_quantities_are_sent_and_read_correctly(tmp_path):
    seen = {}

    def h(req):
        p = req.url.path
        if p.endswith("/snapshot"):
            return ok_sim({"snapshot_list": [{"last_price": 743.0}]})
        if p.endswith("/orders") and req.method == "POST":
            seen.update(__import__("json").loads(req.content))
            return ok_sim({"order_id": "5"})
        if p.endswith("/positions"):
            return ok_sim({"positions": [{"symbol": "QQQ", "qty": "0.13", "cost_price": "743", "cur_price": "744", "pstn_type": 0},
                                         {"symbol": "SPY", "qty": "100.0", "cost_price": "1", "cur_price": "1", "pstn_type": 0}]})
        raise AssertionError(p)

    b = broker(tmp_path, h)
    b.place_order(Order("QQQ", "BUY", 0.13, price=743.0))
    assert seen["qty"] == "0.13"
    b.place_order(Order("SPY", "BUY", 100.0, price=1.0))
    assert seen["qty"] == "100"                                         # never "100.0"
    pos = {p.symbol: p.qty for p in b.positions()}
    assert pos == {"QQQ": 0.13, "SPY": 100} and isinstance(pos["SPY"], int)

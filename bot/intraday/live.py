"""Paper-trading engine for strategies A/B/C on the SIMULATED Moomoo account.

IMPORTANT: stops, targets and time exits are held by THIS PROCESS, not by the broker (the simulated account has no
stop orders). If the bot, the PC or the internet is down, nothing protects an open position. That is acceptable for
practice money and is NOT acceptable for real money.

Safety behaviour implemented here:
 * restart reconcile: positions/orders at the broker are compared with the saved state BEFORE anything trades;
   any mismatch halts entries and says why (exits of known positions keep working)
 * stale feed (>stale_seconds without a fresh quote): no entries, stale open orders cancelled, status published
 * any rejected order: entries halt until the bot is restarted (the "kill switch"); exits keep retrying
 * REAL accounts are refused outright
"""
import json
import math
import os
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path

from ..models import Order
from .bars import NY, Bar
from .calendar import Calendar
from .context import DayContext, TickerState
from .market import US, Market, explain_error
from .params import SharedParams, apply_overrides
from .qty import clean
from .risk import DayRisk, DrawdownGuard, cost_per_share, size_position
from .runner import base_shared, build

BAR_LAG = timedelta(seconds=2)       # wait this long after a minute ends before trusting its bar
FRESH = timedelta(seconds=90)        # only bars this recent may trigger an entry (catch-up bars never do)
GRACE = timedelta(seconds=10)         # keep processing this long after a session segment ends so its last bar is seen
SELL_BUFFERS = (0.001, 0.002, 0.004)  # marketable-limit sell: 0.1%, then 0.2%, then 0.4% below the last price


class LiveRunner:
    def __init__(self, broker, sync, *, universe, configs, calendar_path="data/calendar.json",
                 state_path="data/live_state.json", journal_path="data/journal.jsonl", clock=None,
                 sleep=_time.sleep, log=print, shared: SharedParams | None = None, halted=lambda: False,
                 poll_seconds=5.0, fill_timeout=20.0, stale_seconds=None, strategy_factory=None, real=False,
                 market: Market = US):
        if real:
            raise RuntimeError("The strategy engine is paper-only in this phase; REAL accounts are refused.")
        self.market = market
        self.broker, self.sync, self.configs = broker, sync, configs
        self._universe_src = universe
        self.universe = list(universe() if callable(universe) else universe)
        self.enabled = True   # the app's per-market switch; False = manage open positions but take no new entries
        self.calendar_path, self.state_path, self.journal_path = calendar_path, Path(state_path), Path(journal_path)
        self.clock = clock or (lambda: datetime.now(market.tz))
        self.sleep, self.log, self.halted = sleep, log, halted
        self.shared = (shared or base_shared(market)).validate()   # raises (and the bot refuses to start) above the risk ceiling
        self.poll, self.fill_timeout = poll_seconds, fill_timeout
        self.stale_seconds = stale_seconds if stale_seconds is not None else market.stale_seconds
        self.strategy_factory = strategy_factory or (lambda codes, overrides: build(codes, overrides)[0])
        self.day: date | None = None
        self.ctx: dict[str, DayContext] = {}
        self.tstate: dict[str, TickerState] = {}
        self.prior: dict[str, dict | None] = {}
        self.last_bar: dict[str, datetime] = {}
        self.risk: DayRisk | None = None
        self.lock: dict[str, str] = {}
        self.pos: dict | None = None
        self.halt_reason: str | None = None
        self.data_error: str | None = None
        self._retry_at: datetime | None = None
        self.feed_ok = True
        self.strategies: list = []
        self.cfg: dict = {}
        self._cfg_at: datetime | None = None
        self._minute: datetime | None = None
        self._pub_at: datetime | None = None
        self._booted = False
        self._load_state()

    # ---- persistence ---------------------------------------------------------------------------------
    def _load_state(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text())
        except (FileNotFoundError, ValueError):
            raw = {}
        self.pos = raw.get("pos")
        self.guard = DrawdownGuard(self.shared, **(raw.get("guard") or {}))
        self._saved_day = raw.get("day")
        self._saved_risk = raw.get("risk")
        self._saved_lock = raw.get("lock") or {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "day": self.day.isoformat() if self.day else None, "pos": self.pos, "lock": self.lock, "guard": self.guard.dump(),
            "risk": self.risk and {"start_equity": self.risk.start_equity, "realized": self.risk.realized,
                                   "trades": self.risk.trades, "consecutive_losses": self.risk.consecutive_losses}}))
        os.replace(tmp, self.state_path)

    # ---- main loop -----------------------------------------------------------------------------------
    def run_forever(self, stop) -> None:
        while not stop.is_set():
            try:
                self.cycle()
            except Exception as e:  # noqa: BLE001 - one bad cycle must never kill the engine
                self.log(f"[live] cycle error: {type(e).__name__}: {e}")
            stop.wait(self.poll)

    def cycle(self) -> None:
        now = self.clock().astimezone(self.market.tz)
        in_session = now.weekday() < 5 and (self.market.minute_of_session(now) is not None
                                            or self.market.minute_of_session(now - GRACE) is not None)
        if not in_session:
            self._publish(now, "closed")
            return
        self._refresh_configs(now)
        if self.day != now.date():
            if self._retry_at and now < self._retry_at:
                self._publish(now, "open")
                return
            try:
                self._new_day(now)
                self.data_error = None
            except Exception as e:  # noqa: BLE001 - e.g. no quote right / unsupported market: say so once, retry in a minute
                msg = explain_error(e, self.market)
                if msg != self.data_error:
                    self.log(f"[live] {self.market.code} data problem: {msg}")
                self.data_error, self.day, self._retry_at = msg, None, now + timedelta(seconds=60)
                self._publish(now, "open", force=True)
                return
        prices = self._snapshot(now)
        if prices is not None:
            self._manage_price(now, prices)
            minute = now.replace(second=0, microsecond=0)
            if self._minute != minute and now.second >= BAR_LAG.seconds:
                self._minute = minute
                self._process_bars(now, prices)
        self._save_state()
        self._publish(now, "open")

    # ---- day setup & reconcile -----------------------------------------------------------------------
    def _new_day(self, now: datetime) -> None:
        day = now.date()
        cal = Calendar.load(self.calendar_path, self.market.code)
        src = self._universe_src() if callable(self._universe_src) else self._universe_src
        self.universe = list(dict.fromkeys(list(src) + ([self.pos["ticker"]] if self.pos else [])))
        self.blocked = cal.blocked(day)
        self.not_before = cal.no_entries_before(day)
        self.calendar_ok = cal.configured
        self.ctx, self.last_bar, self.lock = {}, {}, {}
        for t in self.universe:
            self._warm(t, day)
        if not self._booted:
            self._reconcile()
        total = sum(c["budget"] for c in self.cfg.values() if c["enabled"])
        same_day = self._saved_day == day.isoformat() and self._saved_risk
        self.risk = DayRisk(**self._saved_risk) if same_day else DayRisk(total)
        if same_day:
            self.lock = dict(self._saved_lock)
        self.day = day
        self._booted = True
        self.log(f"[live] new day {day}: blocked={self.blocked} calendar_configured={self.calendar_ok}")

    def _warm(self, ticker: str, day: date) -> None:
        """Replay recent history so indicators/prior-day levels exist; today's bars are caught up without trading."""
        bars = self.broker.intraday_bars(ticker, day - timedelta(days=8), day)
        by_day: dict[date, list[Bar]] = {}
        for b in sorted(bars, key=lambda x: x.ts):
            by_day.setdefault(b.ts.astimezone(self.market.tz).date(), []).append(b)
        if ticker not in self.tstate:  # fresh process: build indicator state from earlier days
            self.tstate[ticker] = TickerState()
            prior = None
            for d in sorted(x for x in by_day if x < day):
                c = DayContext(ticker, d, self.tstate[ticker], prior, self.market)
                for b in by_day[d]:
                    if self.market.is_regular(b.ts):
                        c.add_1m(b)
                if c.bars1:
                    prior = {"high": c.hod, "low": c.lod, "close": c.bars1[-1].close}
            self.prior[ticker] = prior
        elif ticker in self.ctx:
            old = self.ctx[ticker]
            self.prior[ticker] = {"high": old.hod, "low": old.lod, "close": old.bars1[-1].close} if old.bars1 else self.prior.get(ticker)
        self.ctx[ticker] = DayContext(ticker, day, self.tstate[ticker], self.prior.get(ticker), self.market)

    def _reconcile(self) -> None:
        """Compare broker positions/orders with the saved state before anything is allowed to trade."""
        held = {p.symbol: p.qty for p in self.broker.positions()}
        for o in self.broker.open_orders():
            if o["symbol"] in self.universe:
                self.log(f"[live] reconcile: cancelling leftover open order {o['id']} on {o['symbol']}")
                self.broker.cancel_order(o["id"])
        for t in self.universe:
            q = held.get(t, 0)
            mine = self.pos and self.pos["ticker"] == t
            if mine and abs(q - self.pos["remaining"]) < 1e-6:
                self.log(f"[live] reconcile: resuming {t} position of {q} shares")
            elif mine and abs(q) < 1e-9:
                self.log(f"[live] reconcile: saved {t} position no longer at broker; cleared")
                self.pos = None
            elif mine:
                self.halt_reason = f"reconcile mismatch on {t}: saved {self.pos['remaining']} shares, broker has {q}"
            elif abs(q) > 1e-9:
                self.halt_reason = f"unexpected {q} {t} at broker (not opened by this engine); flatten it, then restart"
        if self.halt_reason:
            self.log(f"[live] HALTED ENTRIES: {self.halt_reason}")

    # ---- config, prices, feed health -----------------------------------------------------------------
    def _refresh_configs(self, now: datetime) -> None:
        if self._cfg_at and now - self._cfg_at < timedelta(seconds=30):
            return
        self._cfg_at = now
        try:
            rows = {r["strategy"]: r for r in self.configs()}
        except Exception as e:  # noqa: BLE001
            self.log(f"[live] could not read strategy configs: {e}")
            return
        new = {}
        for c, r in rows.items():
            params = dict(r.get("params") or {})
            shared_over = params.pop("shared", {}) or {}   # {"max_trade_notional": 100, "allow_fractional": true}
            new[c] = {"enabled": bool(r.get("enabled")), "budget": float(r.get("budget") or 0), "params": params,
                      "shared": shared_over}
        if new != self.cfg:
            self.cfg = new
            if self.risk:  # daily-loss limit is a % of the capital currently allocated to enabled strategies
                self.risk.start_equity = sum(c["budget"] for c in new.values() if c["enabled"])
            codes = [c for c, r in new.items() if r["enabled"] and r["budget"] > 0]
            try:
                self.strategies = [s for c in codes for s in self.strategy_factory([c], {c: new[c]["params"]})]
            except ValueError as e:
                self.strategies = []
                self.log(f"[live] invalid strategy params, nothing enabled: {e}")
            self.log(f"[live] enabled strategies: {[s.name for s in self.strategies]}")

    def _snapshot(self, now: datetime):
        try:
            snap = self.broker.snapshot(self.universe)
        except Exception as e:  # noqa: BLE001
            return self._stale(now, f"quote request failed: {e}")
        for t in self.universe:
            q = snap.get(t)
            if q is None or q["ts"] is None or (now - q["ts"]).total_seconds() > self.stale_seconds:
                return self._stale(now, f"{t} quote older than {self.stale_seconds:.0f}s")
        if not self.feed_ok:
            self.log("[live] feed recovered")
        self.feed_ok = True
        return snap

    def _stale(self, now, why):
        if self.feed_ok:
            self.log(f"[live] FEED STALE ({why}): entries blocked, open orders cancelled."
                     + (" Bot-held stops are NOT being monitored." if self.pos else ""))
            self._cancel_open_orders()
        self.feed_ok = False
        return None

    def _cancel_open_orders(self) -> None:
        try:
            for o in self.broker.open_orders():
                if o["symbol"] in self.universe:
                    self.broker.cancel_order(o["id"])
        except Exception as e:  # noqa: BLE001
            self.log(f"[live] cancel failed: {e}")

    # ---- bars ----------------------------------------------------------------------------------------
    def _process_bars(self, now: datetime, prices) -> None:
        for t in self.universe:
            ctx = self.ctx[t]
            try:
                bars = self.broker.recent_bars(t, 15) if t in self.last_bar else self.broker.intraday_bars(t, now.date(), now.date())
            except Exception as e:  # noqa: BLE001
                self.log(f"[live] bar fetch failed for {t}: {e}")
                continue
            fresh_bars = [b for b in sorted(bars, key=lambda x: x.ts) if b.ts.astimezone(self.market.tz).date() == now.date()]
            for b in fresh_bars:
                if self.market.is_pre(b.ts):
                    ctx.add_premarket(b)
            ready = [b for b in fresh_bars if self.market.is_regular(b.ts) and b.ts > self.last_bar.get(t, datetime.min.replace(tzinfo=NY))
                     and b.ts + timedelta(minutes=1) + BAR_LAG <= now]
            for i, b in enumerate(ready):
                new5 = ctx.add_1m(b)
                self.last_bar[t] = b.ts
                latest = i == len(ready) - 1
                self._on_bar_close(now, t, ctx, b, new5, fresh=latest and now - ctx.now <= FRESH, price=prices[t]["last"])

    def _on_bar_close(self, now, t, ctx, bar, new5, fresh, price) -> None:
        if self.pos and self.pos["ticker"] == t and fresh:
            reason = self._bar_exit_reason(ctx, new5, bar)
            if reason:
                self._exit(self.pos["remaining"], reason, price)
        sigs = [s for st in self.strategies if self.lock.get(t, st.family) == st.family
                for s in [st.on_bar(ctx, new5)] if s]
        if fresh and sigs and not self.pos:
            self._try_enter(now, sigs[0])

    def _bar_exit_reason(self, ctx, new5, bar):
        pos = self.pos
        strat = next((s for s in self.strategies if s.name == pos["strategy"]), None)
        if strat:
            r = strat.discretionary_exit(pos, ctx, new5)
            if r:
                return r
        mins = ctx.elapsed - pos["entry_elapsed"]   # trading minutes (a lunch break does not count)
        if pos["time_stop_minutes"] and mins >= pos["time_stop_minutes"] and (pos["time_stop_always"] or not pos["t1_done"]):
            return "time_stop"
        if ctx.elapsed >= pos["flat_min"]:
            return "flat_eod"
        # safety net for a spike the 5-second polling missed; only bars that began after the stop took effect count
        if bar.ts >= datetime.fromisoformat(pos["stop_since"]) and bar.low <= pos["stop"]:
            return "stop_from_bar"
        return None

    # ---- entries -------------------------------------------------------------------------------------
    def _try_enter(self, now, sig) -> None:
        why = self._entry_block(now, sig)
        if why:
            self.log(f"[live] {sig.strategy} {sig.ticker} signal skipped: {why}")
            return
        cfg = self.cfg[sig.strategy]
        try:   # per-strategy limits set in the app: max money per trade, fractional shares (re-validated against the risk ceiling)
            shared = apply_overrides(self.shared, cfg.get("shared")).validate()
        except ValueError as e:
            self.log(f"[live] {sig.strategy} {sig.ticker} signal skipped: invalid limits in the app ({e})")
            return
        equity = min(cfg["budget"], self.broker.equity())
        sizing = size_position(equity, sig.entry, sig.stop, shared, self.guard.risk_pct)
        if sizing["skip"]:
            self.log(f"[live] {sig.strategy} {sig.ticker} signal skipped: {sizing['skip']}")
            return
        order = self.broker.place_order(Order(sig.ticker, "BUY", sizing["shares"], price=math.ceil(sig.entry * 100 - 1e-9) / 100,  # never round a buy limit down
                                              reason=f"strategy {sig.strategy}"))
        if order.status == "REJECTED":
            self.halt_reason = f"entry order rejected ({order.reason}); entries halted until restart"
            self.log(f"[live] KILL SWITCH: {self.halt_reason}")
            return
        filled, avg = self._wait_fill(order.id)
        if filled <= 0:
            self.log(f"[live] {sig.strategy} {sig.ticker} entry at {sig.entry} not filled in {self.fill_timeout:.0f}s; cancelled")
            return
        self.risk.trades += 1
        self.lock[sig.ticker] = sig.family
        self.pos = {"strategy": sig.strategy, "ticker": sig.ticker, "entry_ts": now.isoformat(), "entry_day": now.date().isoformat(),
                    "entry": avg, "planned_entry": sig.entry, "stop_since": now.replace(second=0, microsecond=0).isoformat(), "shares": clean(filled), "remaining": clean(filled), "unit": shared.fractional_step if shared.allow_fractional else shared.lot_size,
                    "stop": sig.stop, "stop0": sig.stop, "t1": sig.t1, "t2": sig.t2, "t1_frac": sig.t1_frac, "t1_done": False,
                    "time_stop_minutes": sig.time_stop_minutes, "time_stop_always": sig.time_stop_always,
                    "flat_min": sig.flat_min, "entry_elapsed": sig.elapsed, "regime": {**sig.regime, "sizing": sizing}, "exits": []}
        self._save_state()
        self.log(f"[live] ENTERED {sig.strategy} {sig.ticker} {clean(filled)} sh @ {avg:.2f} stop {sig.stop:.2f} T1 {sig.t1:.2f}")

    def _entry_block(self, now, sig) -> str | None:
        if not self.enabled:
            return f"{self.market.code} market switched off in the app"
        if self.halt_reason:
            return self.halt_reason
        if self.halted():
            return "halted from the app"
        if not self.feed_ok:
            return "feed stale"
        if self.blocked:
            return f"calendar block ({self.blocked})"
        if self.not_before and sig.elapsed < self.not_before:
            return f"no entries before {self.not_before} today"
        if self.risk.can_enter(self.shared):
            return self.risk.can_enter(self.shared)
        if sig.strategy not in self.cfg or not self.cfg[sig.strategy]["enabled"] or self.cfg[sig.strategy]["budget"] <= 0:
            return "strategy disabled or no budget"
        return None

    def _wait_fill(self, order_id: str) -> tuple[float, float]:
        """Poll until filled/closed or timeout; then cancel what is left. Returns (filled_qty, avg_price)."""
        deadline, st = _time.monotonic() + self.fill_timeout, {"filled_qty": 0.0, "avg_price": 0.0, "status": "OPEN"}
        while True:
            st = self.broker.order_status(order_id)
            if st["status"] in ("FILLED", "CANCELLED", "REJECTED"):
                break
            if _time.monotonic() >= deadline:
                self.broker.cancel_order(order_id)
                self.sleep(0.5)
                st = self.broker.order_status(order_id)
                break
            self.sleep(1.0)
        if st["status"] == "REJECTED":
            self.halt_reason = f"order {order_id} rejected; entries halted until restart"
            self.log(f"[live] KILL SWITCH: {self.halt_reason}")
        return st["filled_qty"], st["avg_price"]

    # ---- exits (bot-held) ----------------------------------------------------------------------------
    def _manage_price(self, now, prices) -> None:
        pos = self.pos
        if not pos:
            return
        last = prices[pos["ticker"]]["last"]
        if pos["entry_day"] != now.date().isoformat():
            self._exit(pos["remaining"], "carried_over", last)
        elif last <= pos["stop"]:
            self._exit(pos["remaining"], "breakeven_stop" if pos["t1_done"] else "stop", last)
        elif not pos["t1_done"] and last >= pos["t1"]:
            unit = pos.get("unit") or self.shared.lot_size
            half = clean(math.floor(pos["shares"] * pos["t1_frac"] / unit + 1e-9) * unit)
            qty = pos["remaining"] if half < unit else half  # cannot split a single lot / minimum step
            self._exit(qty, "target1", last)
            if self.pos:
                self.pos["t1_done"], self.pos["stop"] = True, self.pos["entry"]
                self.pos["stop_since"] = now.replace(second=0, microsecond=0).isoformat()
        elif pos["t1_done"] and pos["t2"] and last >= pos["t2"]:
            self._exit(pos["remaining"], "target2", last)

    def _exit(self, qty, reason: str, last: float) -> None:
        pos = self.pos
        qty = clean(min(qty, pos["remaining"]))
        sold, value = 0, 0.0
        for buf in SELL_BUFFERS:
            if qty - sold <= 1e-9:
                break
            o = self.broker.place_order(Order(pos["ticker"], "SELL", clean(qty - sold), price=round(last * (1 - buf), 2), reason=reason))
            if o.status == "REJECTED":
                self.halt_reason = f"exit order rejected ({o.reason}); entries halted until restart"
                self.log(f"[live] KILL SWITCH: {self.halt_reason}")
                continue
            filled, avg = self._wait_fill(o.id)
            sold = clean(sold + filled)
            value += filled * avg
            if sold < qty:
                try:
                    last = self.broker.snapshot([pos["ticker"]])[pos["ticker"]]["last"]
                except Exception:  # noqa: BLE001
                    pass
        if sold:
            pos["exits"].append({"ts": self.clock().isoformat(), "qty": sold, "price": round(value / sold, 4), "reason": reason})
            pos["remaining"] = clean(pos["remaining"] - sold)
        if sold < qty - 1e-9:
            self.log(f"[live] ERROR: could only sell {sold}/{qty} {pos['ticker']} ({reason}); will retry next cycle")
        self.log(f"[live] EXIT {pos['strategy']} {pos['ticker']} {sold} sh ({reason})")
        if pos["remaining"] <= 1e-9:
            self._finish_trade()
        else:
            self._save_state()

    def _finish_trade(self) -> None:
        pos = self.pos
        gross = sum((e["price"] - pos["entry"]) * e["qty"] for e in pos["exits"])
        costs = cost_per_share(pos["entry"], self.shared) * pos["shares"]  # ASSUMED; the simulator reports no fees
        net = gross - costs
        trade = {"date": pos["entry_day"], "ticker": pos["ticker"], "strategy": pos["strategy"], "mode": "paper",
                 "market": self.market.code,
                 "entry_ts": pos["entry_ts"], "entry": round(pos["entry"], 4), "stop": pos["stop0"], "shares": pos["shares"],
                 "exits": pos["exits"], "costs": round(costs, 4), "pnl": round(net, 4),
                 "r": round(net / (pos["shares"] * (pos["planned_entry"] - pos["stop0"])), 3), "regime": pos["regime"]}
        self.risk.record(net)
        base = sum(c["budget"] for c in self.cfg.values() if c["enabled"]) or pos["shares"] * pos["entry"]
        before = self.guard.level
        if self.guard.record(net, base) > before:
            self.log(f"[live] DRAWDOWN BREAKER {self.guard.level} tripped at {self.guard.drawdown_pct(base):.1f}% from peak: "
                     + (f"risk per trade now {self.shared.breaker1_risk_pct}%" if self.guard.level == 1
                        else "live trading disabled (this engine is paper-only, so it keeps paper trading)"))
        self.pos = None
        self._save_state()
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a") as f:  # local copy survives a Supabase outage
            f.write(json.dumps(trade) + "\n")
        try:
            self.sync.push_trade(trade)
        except Exception as e:  # noqa: BLE001
            self.log(f"[live] journal push failed (saved locally): {e}")
        self.log(f"[live] TRADE CLOSED {trade['strategy']} {trade['ticker']} pnl {trade['pnl']} ({trade['r']}R)")

    # ---- status for the app --------------------------------------------------------------------------
    def status(self, session: str) -> dict:
        p = self.pos
        return {
            "data_error": self.data_error, "mode": "paper", "market": self.market.code, "currency": self.market.currency, "session": session,
            "simulated_by": "moomoo simulated account" if self.market.sim_account else "the bot (real quotes, simulated fills)", "feed_ok": self.feed_ok, "halt_reason": self.halt_reason,
            "stops": "held by the bot (not at the broker)",
            "risk": {"per_trade_pct": self.guard.risk_pct, "breaker_level": self.guard.level,
                     "live_disabled": self.guard.live_disabled, "daily_max_loss_pct": self.shared.daily_max_loss_pct}, "calendar_configured": getattr(self, "calendar_ok", False),
            "blocked_today": getattr(self, "blocked", None),
            "enabled": [s.name for s in self.strategies],
            "day": self.risk and {"trades": self.risk.trades, "realized": round(self.risk.realized, 2),
                                  "consecutive_losses": self.risk.consecutive_losses},
            "position": p and {"strategy": p["strategy"], "ticker": p["ticker"], "shares": p["remaining"],
                               "entry": round(p["entry"], 2), "stop": round(p["stop"], 2), "t1": round(p["t1"], 2)},
        }

    def _publish(self, now, session, force=False) -> None:
        if not force and self._pub_at and now - self._pub_at < timedelta(seconds=10):
            return
        self._pub_at = now
        try:
            self.sync.push_engine_status(self.status(session), self.market.code)
        except Exception as e:  # noqa: BLE001
            self.log(f"[live] status publish failed: {e}")

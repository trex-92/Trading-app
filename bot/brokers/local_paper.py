"""Bot-side simulated account for markets where Moomoo has NO simulated trading (SGX, Bursa Malaysia).

Quotes and bars are REAL (from the data broker); only the orders are simulated here, against the real bid/ask:
 * a BUY limit fills at the ask if ask <= limit (else at last if there is no ask and last <= limit), otherwise it waits
 * a SELL limit fills at the bid if bid >= limit (else at last, same rule)
 * quantities must be whole board lots; selling more than held is rejected (long only)
Fees are not deducted from cash; the journal charges the assumed cost model instead. State survives restarts.
Optimistic in ways a real broker is not: no queue position, no partial fills, no market impact. Treat results as an
upper bound."""
import json
import os
from pathlib import Path

from ..models import Order, Position


class LocalPaperBroker:
    def __init__(self, data, market, state_path, starting_cash: float):
        self.data, self.market, self.path = data, market, Path(state_path)
        try:
            self.s = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            self.s = {"cash": float(starting_cash), "positions": {}, "orders": {}, "next_id": 1}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s))
        os.replace(tmp, self.path)

    # ---- data passthrough (real quotes, real bars) ----------------------------------------------------
    def intraday_bars(self, symbol, start, end):
        return self.data.intraday_bars(symbol, start, end, market=self.market)

    def recent_bars(self, symbol, n=15):
        return self.data.recent_bars(symbol, n, market=self.market)

    def snapshot(self, symbols):
        return self.data.snapshot(symbols, market=self.market)

    # ---- simulated account ----------------------------------------------------------------------------
    def cash(self) -> float:
        return self.s["cash"]

    def equity(self) -> float:
        total = self.s["cash"]
        if self.s["positions"]:
            try:
                snap = self.snapshot(list(self.s["positions"]))
            except Exception:  # noqa: BLE001
                snap = {}
            for sym, p in self.s["positions"].items():
                total += p["qty"] * (snap.get(sym, {}).get("last") or p["avg"])
        return total

    def positions(self):
        return [Position(sym, p["qty"], p["avg"], p["avg"]) for sym, p in self.s["positions"].items() if p["qty"]]

    def open_orders(self):
        return [{"id": k, "symbol": o["symbol"], "side": o["side"]} for k, o in self.s["orders"].items() if o["status"] == "OPEN"]

    def place_order(self, order: Order) -> Order:
        oid = f"L{self.s['next_id']}"
        self.s["next_id"] += 1
        order.id = oid
        lot = self.market.lot_size
        held = self.s["positions"].get(order.symbol, {}).get("qty", 0)
        if order.price is None or order.qty <= 0 or order.qty % lot:
            order.status, order.reason = "REJECTED", f"quantity must be a whole number of {lot}-share lots"
        elif order.side == "SELL" and order.qty > held:
            order.status, order.reason = "REJECTED", "cannot sell more than held (long only)"
        elif order.side == "BUY" and order.qty * order.price > self.s["cash"] + 1e-6:
            order.status, order.reason = "REJECTED", "insufficient simulated cash"
        else:
            self.s["orders"][oid] = {"symbol": order.symbol, "side": order.side, "qty": order.qty, "price": order.price,
                                     "status": "OPEN", "filled_qty": 0, "avg_price": 0.0}
            order.status = "SUBMITTED"
            self._try_fill(oid)
        self._save()
        return order

    def _try_fill(self, oid: str) -> None:
        o = self.s["orders"][oid]
        if o["status"] != "OPEN":
            return
        try:
            q = self.snapshot([o["symbol"]])[o["symbol"]]
        except Exception:  # noqa: BLE001
            return
        bid, ask, last = q.get("bid") or 0, q.get("ask") or 0, q["last"]
        if o["side"] == "BUY":
            px = ask if ask and ask <= o["price"] else (last if not ask and last <= o["price"] else None)
        else:
            px = bid if bid and bid >= o["price"] else (last if not bid and last >= o["price"] else None)
        if px is None:
            return
        sign = 1 if o["side"] == "BUY" else -1
        pos = self.s["positions"].setdefault(o["symbol"], {"qty": 0, "avg": 0.0})
        if sign > 0:
            pos["avg"] = (pos["avg"] * pos["qty"] + px * o["qty"]) / (pos["qty"] + o["qty"])
        pos["qty"] += sign * o["qty"]
        if pos["qty"] == 0:
            del self.s["positions"][o["symbol"]]
        self.s["cash"] -= sign * px * o["qty"]
        o.update(status="FILLED", filled_qty=o["qty"], avg_price=px)

    def order_status(self, oid: str) -> dict:
        o = self.s["orders"].get(oid)
        if o is None:
            return {"status": "UNKNOWN", "filled_qty": 0.0, "avg_price": 0.0}
        self._try_fill(oid)
        self._save()
        return {"status": o["status"], "filled_qty": float(o["filled_qty"]), "avg_price": float(o["avg_price"])}

    def cancel_order(self, oid: str) -> None:
        o = self.s["orders"].get(oid)
        if o and o["status"] == "OPEN":
            o["status"] = "CANCELLED"
            self._save()

"""Moomoo adapter. Requires the OpenD gateway running and `pip install moomoo-api`.

Defaults to SIMULATE; REAL is gated by Config.validate() (ALLOW_LIVE=yes).
Verify field names against your installed moomoo-api version before going live.
"""
import os

from ..models import Order, Position
from .base import Broker


class MoomooBroker(Broker):
    def __init__(self, host: str, port: int, trade_env: str, market: str = "US"):
        import moomoo as ft  # imported lazily so the package stays optional

        self.ft = ft
        self.env = ft.TrdEnv.REAL if trade_env == "REAL" else ft.TrdEnv.SIMULATE
        self.quote = ft.OpenQuoteContext(host=host, port=port)
        self.trd = ft.OpenSecTradeContext(
            filter_trdmarket=getattr(ft.TrdMarket, market), host=host, port=port,
            security_firm=ft.SecurityFirm.FUTUINC,
        )
        if self.env == ft.TrdEnv.REAL:
            ret, data = self.trd.unlock_trade(os.environ["MOOMOO_TRADE_PWD"])
            if ret != ft.RET_OK:
                raise RuntimeError(f"unlock_trade failed: {data}")

    @staticmethod
    def _code(symbol: str) -> str:
        return symbol if "." in symbol else f"US.{symbol}"

    def history(self, symbol, bars):
        ret, data, _ = self.quote.request_history_kline(
            self._code(symbol), ktype=self.ft.KLType.K_DAY, max_count=bars)
        if ret != self.ft.RET_OK:
            raise RuntimeError(data)
        return list(data["close"])[-bars:]

    def last_price(self, symbol):
        ret, data = self.quote.get_market_snapshot([self._code(symbol)])
        if ret != self.ft.RET_OK:
            raise RuntimeError(data)
        return float(data["last_price"][0])

    def positions(self):
        ret, data = self.trd.position_list_query(trd_env=self.env)
        if ret != self.ft.RET_OK:
            raise RuntimeError(data)
        return [Position(r.code.split(".", 1)[-1], int(r.qty), float(r.cost_price), float(r.nominal_price))
                for r in data.itertuples() if r.qty]

    def cash(self):
        ret, data = self.trd.accinfo_query(trd_env=self.env)
        if ret != self.ft.RET_OK:
            raise RuntimeError(data)
        return float(data["cash"][0])

    def place_order(self, order):
        side = self.ft.TrdSide.BUY if order.side == "BUY" else self.ft.TrdSide.SELL
        px = order.price or self.last_price(order.symbol)
        ot = self.ft.OrderType.NORMAL if order.price else self.ft.OrderType.MARKET
        ret, data = self.trd.place_order(px, order.qty, self._code(order.symbol), side,
                                         order_type=ot, trd_env=self.env)
        if ret != self.ft.RET_OK:
            order.status, order.reason = "REJECTED", str(data)
            return order
        order.id, order.status = str(data["order_id"][0]), "SUBMITTED"
        return order

    def close(self):
        self.quote.close()
        self.trd.close()

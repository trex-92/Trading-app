from .config import Config
from .models import Order


class RiskManager:
    """Pre-trade checks. Returns a rejection reason, or None if the order may go."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.halted = False  # kill switch, toggled from the API/app

    def check(self, order: Order, held_qty: int, orders_today: int, day_pnl: float) -> str | None:
        if self.halted:
            return "trading halted"
        if day_pnl <= -self.cfg.max_daily_loss:
            return f"daily loss limit hit ({day_pnl:.2f})"
        if orders_today >= self.cfg.max_orders_per_day:
            return "max orders per day reached"
        if order.side == "BUY" and held_qty + order.qty > self.cfg.max_position_qty:
            return "max position size exceeded"
        if order.side == "SELL" and order.qty > held_qty:
            return "cannot sell more than held (no shorting)"
        return None

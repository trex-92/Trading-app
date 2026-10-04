from .base import Strategy


class SmaCross(Strategy):
    """Buy when fast SMA crosses above slow SMA; sell on cross below."""

    def __init__(self, fast: int, slow: int):
        self.fast, self.slow = fast, slow
        self.warmup = slow + 1

    def _sma(self, xs, n, offset=0):
        w = xs[len(xs) - n - offset: len(xs) - offset]
        return sum(w) / n

    def signal(self, closes, held_qty):
        if len(closes) < self.warmup:
            return None
        was_below = self._sma(closes, self.fast, 1) <= self._sma(closes, self.slow, 1)
        is_above = self._sma(closes, self.fast) > self._sma(closes, self.slow)
        if was_below and is_above and held_qty == 0:
            return "BUY"
        if not was_below and not is_above and held_qty > 0:
            return "SELL"
        return None

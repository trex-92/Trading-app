from collections import deque


class Ema:
    """EMA seeded with the SMA of the first n values (value is None until then)."""

    def __init__(self, n: int):
        self.n, self.k, self.value, self._seed = n, 2 / (n + 1), None, []

    def update(self, x: float):
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) == self.n:
                self.value = sum(self._seed) / self.n
        else:
            self.value = x * self.k + self.value * (1 - self.k)
        return self.value


class Atr:
    """Wilder ATR, seeded with the mean of the first n true ranges."""

    def __init__(self, n: int = 14):
        self.n, self.value, self._prev_close, self._seed = n, None, None, []

    def update(self, high: float, low: float, close: float):
        tr = high - low if self._prev_close is None else max(
            high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        if self.value is None:
            self._seed.append(tr)
            if len(self._seed) == self.n:
                self.value = sum(self._seed) / self.n
        else:
            self.value = (self.value * (self.n - 1) + tr) / self.n
        return self.value


class RollingMean:
    def __init__(self, n: int):
        self.n, self._q, self._sum = n, deque(), 0.0

    def update(self, x: float):
        self._q.append(x)
        self._sum += x
        if len(self._q) > self.n:
            self._sum -= self._q.popleft()
        return self.value

    @property
    def value(self):
        return self._sum / len(self._q) if len(self._q) == self.n else None

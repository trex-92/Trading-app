"""Webull adapter (OpenAPI). SKELETON: not yet implemented.

Webull's official OpenAPI requires an approved app key/secret, and I have not
verified the SDK's method names. Implement each method against the docs at
https://developer.webull.com before use, then register it in brokers/__init__.py.
"""
from .base import Broker


class WebullBroker(Broker):
    def __init__(self, *a, **kw):
        raise NotImplementedError("Webull adapter is a skeleton; see module docstring")

    def history(self, symbol, bars): raise NotImplementedError
    def last_price(self, symbol): raise NotImplementedError
    def positions(self): raise NotImplementedError
    def cash(self): raise NotImplementedError
    def place_order(self, order): raise NotImplementedError

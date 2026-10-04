import hmac

from fastapi import Depends, FastAPI, Header, HTTPException

from .engine import Engine


def create_app(engine: Engine, token: str) -> FastAPI:
    app = FastAPI(title="Trading Bot Monitor")

    def auth(authorization: str = Header("")):
        if not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "unauthorized")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/status", dependencies=[Depends(auth)])
    def status():
        return engine.store.get("snapshot") or engine.snapshot()

    @app.get("/orders", dependencies=[Depends(auth)])
    def orders(limit: int = 100):
        return engine.store.orders(min(limit, 500))

    @app.get("/events", dependencies=[Depends(auth)])
    def events(limit: int = 100):
        return engine.store.events(min(limit, 500))

    @app.post("/halt", dependencies=[Depends(auth)])
    def halt():
        engine.risk.halted = True
        engine.store.log("WARN", "trading HALTED via API")
        return {"halted": True}

    @app.post("/resume", dependencies=[Depends(auth)])
    def resume():
        engine.risk.halted = False
        engine.store.log("INFO", "trading RESUMED via API")
        return {"halted": False}

    return app

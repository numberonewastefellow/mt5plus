"""FastAPI server for the XAUUSD manual order pad.

- WebSocket /ws  -> pushes live price + position/health state to the browser.
- POST /buy /sell /close_all -> hand the command to the single MT5 worker
  thread and return its result (retcode + ticket) synchronously.
- Serves the static UI at /.

Bound to 127.0.0.1 only (see config.HOST) so it is never reachable on the
network -- this server can place real trades.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from mt5_worker import Mt5Worker

STATIC_DIR = Path(__file__).parent / "static"
WEBUI_DIR = Path(__file__).parent / "webui"

worker = Mt5Worker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker.start()
    try:
        yield
    finally:
        worker.stop()


app = FastAPI(title="XauOrderPad", lifespan=lifespan)


class OrderReq(BaseModel):
    volume: float | None = None
    sl: float | None = None
    tp: float | None = None
    sl_tp_mode: str | None = None


class PlaceReq(BaseModel):
    """Unified order request used by the new UI (market or limit).

    The `auto_test` flag is set TRUE by the browser ONLY while the Auto-Test
    burst engine is running. The /order handler refuses requests with this flag
    on any non-demo account (defense-in-depth backend guard that survives JS
    bugs, console exec, forged curl, or a swapped MT5 login mid-session).
    Manual orders from the UI never carry this flag, so they behave unchanged.
    """
    symbol: str | None = None
    side: str
    volume: float | None = None
    type: str | None = "market"
    price: float | None = None
    sl: float | None = None
    tp: float | None = None
    auto_test: bool = False     # Auto-Test → backend demo-only guard (see /order)


class CloseReq(BaseModel):
    ticket: int
    volume: float | None = None


def _check_token(token: str | None) -> None:
    if config.API_TOKEN and token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing token")


@app.get("/api/state")
def get_state():
    return worker.get_state()


@app.get("/api/account/safety")
def account_safety():
    """Lightweight endpoint the UI calls BEFORE enabling Auto-Test.

    Returns just the fields the browser needs to make the demo/real decision —
    deliberately minimal so the UI doesn't have to scrape /api/state. Mirrors
    the account block from worker.get_state(); kept thin so the safety check
    on the START button doesn't pull positions, stats, ticks, etc.
    """
    st = worker.get_state()
    acc = st.get("account") or {}
    return {
        "connected": bool(st.get("connected")),
        "healthy": bool(st.get("healthy")),
        "login": acc.get("login"),
        "server": acc.get("server"),
        "trade_mode": acc.get("trade_mode"),     # 0=demo 1=contest 2=real (MT5 enum)
        "is_demo": bool(acc.get("is_demo")),
        "margin_mode": acc.get("margin_mode"),   # 0=netting 2=hedging
    }


async def _do(cmd: dict):
    fut = worker.submit(cmd)
    return await asyncio.wrap_future(fut)


@app.post("/buy")
async def buy(req: OrderReq, x_token: str | None = Header(default=None)):
    _check_token(x_token)
    cmd = {"action": "buy", **req.model_dump()}
    return JSONResponse(await _do(cmd))


@app.post("/sell")
async def sell(req: OrderReq, x_token: str | None = Header(default=None)):
    _check_token(x_token)
    cmd = {"action": "sell", **req.model_dump()}
    return JSONResponse(await _do(cmd))


@app.post("/order")
async def order(req: PlaceReq, x_token: str | None = Header(default=None)):
    """Unified market/limit order endpoint for the new UI.

    Demo-only guard: when `req.auto_test` is True, this handler refuses (403)
    unless the connected MT5 account is a demo account. This is the deepest
    safety layer for the Auto-Test feature -- it survives JS bugs, console
    exec, forged curl requests, or a swapped MT5 login between page-load and
    order-send. The flag is set ONLY by the browser's AutoTest engine; manual
    orders never carry it and bypass the check entirely.
    """
    _check_token(x_token)

    # Auto-Test live-account guard. Must fire BEFORE the order is submitted.
    if req.auto_test:
        st = worker.get_state()
        acc = st.get("account") or {}
        if not bool(acc.get("is_demo")):
            raise HTTPException(
                status_code=403,
                detail=(
                    "auto_test orders refused: connected account is not a demo "
                    f"account (login={acc.get('login')!r}, "
                    f"server={acc.get('server')!r}, "
                    f"trade_mode={acc.get('trade_mode')!r})"
                ),
            )

    res = await _do({"action": "order", **req.model_dump()})
    if not res.get("ok"):
        detail = res.get("error") or res.get("comment") or f"retcode {res.get('retcode')}"
        raise HTTPException(status_code=400, detail=detail)
    return {"ticket": res.get("ticket"), "price": res.get("price"),
            "state": res.get("state", "open")}


@app.post("/close")
async def close(req: CloseReq, x_token: str | None = Header(default=None)):
    """Close (full/partial) a position by ticket, or cancel a pending order."""
    _check_token(x_token)
    res = await _do({"action": "close", "ticket": req.ticket, "volume": req.volume})
    if not res.get("ok"):
        detail = res.get("error") or res.get("comment") or "close failed"
        raise HTTPException(status_code=400, detail=detail)
    return res


@app.post("/close_all")
async def close_all(x_token: str | None = Header(default=None)):
    _check_token(x_token)
    return JSONResponse(await _do({"action": "close_all"}))


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    period = 1.0 / max(1, config.POLL_HZ)
    try:
        while True:
            await websocket.send_json(worker.get_state())
            await asyncio.sleep(period)
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close()


# Mount UIs last so the API routes above take precedence. The old simple panel
# stays at /legacy; the new terminal UI is served at /. ("/legacy" must be
# registered before "/" so it is not swallowed by the root mount.)
app.mount("/legacy", StaticFiles(directory=str(STATIC_DIR), html=True), name="legacy")
app.mount("/", StaticFiles(directory=str(WEBUI_DIR), html=True), name="webui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")

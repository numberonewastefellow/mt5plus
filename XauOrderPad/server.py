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
import logging
import math
import os
import re
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import accounts
import config
from logger_setup import setup_logging
from mt5_worker import Mt5Worker

STATIC_DIR = Path(__file__).parent / "static"
WEBUI_DIR = Path(__file__).parent / "webui"

# Initialise the JSONL daily logger BEFORE constructing the worker, so any
# logging calls inside the worker's __init__ / start path land in the file
# from the very first line.
setup_logging("XauOrderPad")
log = logging.getLogger("XauOrderPad.server")


class _RedactToken(logging.Filter):
    """Keep the API token out of the access log.

    uvicorn.access logs the request path WITH its query string, so a `/ws?token=...`
    connect writes the shared trading secret to the console -- and on the EC2 box, to
    whatever captures that console -- in plaintext, once per reconnect. The token has to
    travel as a query param (the browser's WebSocket API cannot set handshake headers),
    so the only place left to fix this is the log boundary.

    Installed at MODULE level, not under `if __name__ == "__main__"`. Redaction that only
    works when the server happens to be started one particular way is not redaction:
    `uvicorn server:app` is a perfectly normal way to run this, and it must not leak.

    It must scrub BOTH the message and the args, and it must be attached to BOTH loggers.
    `uvicorn.access` carries ordinary HTTP requests, but the WebSocket accept line -- the
    one that actually carries `?token=` -- is emitted on `uvicorn.error` by uvicorn's
    websockets implementation, as a fully-formatted message with no args. An access-log-only
    filter looks like it works and leaks the token on every single /ws connect. (Found by
    the test, not by reading it.)
    """

    _RE = re.compile(r"(token=)[^&\s\"'\]]+")

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.args, tuple):
            record.args = tuple(
                self._RE.sub(r"\1<redacted>", a) if isinstance(a, str) else a
                for a in record.args
            )
        if isinstance(record.msg, str) and "token=" in record.msg:
            record.msg = self._RE.sub(r"\1<redacted>", record.msg)
        return True


# Both loggers: HTTP requests go to `uvicorn.access`, the WebSocket accept line to
# `uvicorn.error`. The token only ever rides on /ws, i.e. the one this would have missed.
for _lg in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_lg).addFilter(_RedactToken())

worker = Mt5Worker()


def _lan_ip() -> str | None:
    """The IP of the interface the OS would actually use to reach the outside world.

    A UDP socket is `connect()`ed to a public address and then asked for its own name. UDP is
    connectionless, so this sends NO packets and needs no reachability -- it just makes the OS
    consult its routing table. That is what picks the Wi-Fi adapter over the pile of WSL,
    Hyper-V and VMware virtual adapters a dev box accumulates, all of which have perfectly
    valid private IPs that a phone cannot reach.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def _print_banner() -> None:
    """Say exactly which URLs are reachable, and say it loudly when they are not.

    Uvicorn is not a Vite dev server: it binds ONE address and offers no "Network" URL. When
    HOST is 127.0.0.1, a phone gets connection-refused -- and that reliably gets misdiagnosed as
    a firewall problem, because it looks like one. The socket simply is not on the network.

    The second warning is the one that matters. API_TOKEN defaults to "", which is harmless on
    loopback and is NOT harmless the moment this is on the LAN: this process sends real MT5
    orders, so an unauthenticated network bind means any device on the Wi-Fi can place trades
    and flatten the book.

    Three binds, three different answers to "what do I type into the phone?":

      127.0.0.1   nothing -- the socket is not on the network at all.
      0.0.0.0     every interface, so the address to dial is not in HOST. Ask the routing
                  table (_lan_ip). This is the desktop-on-your-LAN case.
      100.x.y.z   an EXPLICIT bind (the box's Tailscale address). The answer IS HOST, and
                  _lan_ip() is actively wrong here: on EC2 it returns the AWS private
                  172.31.x.x, because that is the route to 8.8.8.8. Handing the operator an
                  unreachable address while captioning it "type this into the Android app"
                  is worse than printing nothing.
    """
    port = config.PORT
    loopback = config.HOST in ("127.0.0.1", "localhost")
    all_ifaces = config.HOST in ("0.0.0.0", "::")
    # Only ask the routing table when the bind itself doesn't name an address.
    dialable = _lan_ip() if all_ifaces else config.HOST

    lines = ["", "  XauOrderPad listening", f"    Local:    http://127.0.0.1:{port}"]

    if loopback:
        lines += [
            "    Network:  NOT REACHABLE -- bound to 127.0.0.1 (loopback only).",
            "              A phone CANNOT connect. This is not a firewall issue: the",
            "              socket does not exist on your Wi-Fi interface.",
            "              Fix (desktop, phone on the same Wi-Fi): XAUORDERPAD_HOST=0.0.0.0",
            "              Fix (EC2 box): XAUORDERPAD_HOST=<the 100.x.y.z Tailscale address>.",
            "              Never 0.0.0.0 on EC2 -- that publishes a trading API to the internet.",
        ]
    elif dialable:
        lines.append(f"    Network:  http://{dialable}:{port}     <-- type this into the Android app")
    else:
        lines.append(f"    Network:  bound to {config.HOST} (could not determine the LAN IP)")

    if config.API_TOKEN:
        lines.append("    Auth:     token REQUIRED")
    elif loopback:
        lines.append("    Auth:     none (loopback only, so nothing else can reach it)")
    else:
        lines += [
            "    Auth:     *** NONE -- AND THIS SERVER IS ON THE NETWORK ***",
            "              Every device that can reach the address above can now place",
            "              orders and close your positions. This process sends REAL MT5",
            "              orders.",
            "              Fix: set XAUORDERPAD_TOKEN and restart.",
        ]
    lines.append("")
    print("\n".join(lines), flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # `server_started` is the first event of the run. Subsequent events
    # (orders, account snapshots, etc.) can be filtered against this line's
    # `pid` field to attribute everything to the right process instance.
    try:
        import MetaTrader5 as _mt5_for_version
        mt5_pkg_ver = getattr(_mt5_for_version, "__version__", "unknown")
    except Exception:
        mt5_pkg_ver = "unknown"
    log.info("server started", extra={
        "event": "server_started",
        "host": config.HOST,
        "port": config.PORT,
        "pid": os.getpid(),
        "python_version": sys.version.split()[0],
        "mt5_package_version": mt5_pkg_ver,
        "magic": int(config.MAGIC),
        "symbol_config": config.SYMBOL,
    })
    worker.start()
    _print_banner()
    # Convenience: pop the UI in a Chrome/Edge "app-mode" window (no address
    # bar / tabs) once the server is accepting. Runs on a daemon thread and is
    # fully fail-safe -- a missing browser never affects the trading server.
    if getattr(config, "LAUNCH_BROWSER", False):
        from browser_launch import launch_when_ready
        url = f"http://{config.HOST}:{config.PORT}/"
        launch_when_ready(url, config.HOST, config.PORT,
                          getattr(config, "BROWSER_MODE", "app"))
    try:
        yield
    finally:
        worker.stop()
        log.info("server stopped", extra={"event": "server_stopped"})


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


class CloseWhereReq(BaseModel):
    """Bulk close, filtered by live P&L sign.

    The sign is evaluated SERVER-SIDE against fresh broker state (see
    Mt5Worker._select), not against whatever the client last saw. A client that
    filtered its own snapshot and sent a ticket list would be racing the market:
    a position can cross zero between the frame it rendered and the close
    landing. It would also cost N round-trips instead of one, which matters on a
    ~150 ms mobile link.
    """
    filter: str = "all"          # "all" | "losing" | "profit"


class StrategyReq(BaseModel):
    """Enable/disable + tune ONE automated strategy engine.

    All fields optional: send just `enabled` to toggle, or any subset of params
    to retune live. Params are the union across engines and each engine ignores
    what it does not recognise -- so one model serves both without the endpoint
    needing to know which engine it is talking to.

    The WORKER enforces every guard (demo-only, hedging where required, the
    ladder's spread check, the kill-switch). This endpoint only forwards. That
    ordering is deliberate: a guard that lives in the HTTP layer is one forged
    curl away from being bypassed.
    """
    enabled: bool | None = None
    # straddle
    rvol_threshold: float | None = None
    sl_atr_mult: float | None = None
    tp_r: float | None = None
    max_hold_min: float | None = None
    cooldown_min: float | None = None
    max_concurrent: int | None = None
    vol_filter: bool | None = None
    # ladder
    side: str | None = None
    trigger: float | None = None
    max_positions: int | None = None
    entry_mode: str | None = None
    entry_step: float | None = None
    entry_gap_ms: int | None = None
    target: float | None = None
    retrace: float | None = None
    hard_sl: float | None = None
    paper: bool | None = None
    # shared
    volume: float | None = None
    max_daily_loss: float | None = None


class ProfileReq(BaseModel):
    """Save (or overwrite) a saved account profile. The password is stored in
    the OS credential vault by accounts.save_profile; it is never persisted to
    profiles.json and never logged."""
    label: str | None = None
    login: int
    password: str
    server: str
    path: str | None = None


class LoginReq(BaseModel):
    """Log in / switch account. Either reference a saved `profile_id`, or pass
    ad-hoc `login`/`password`/`server` (optionally `save=True` to remember)."""
    profile_id: str | None = None
    login: int | None = None
    password: str | None = None
    server: str | None = None
    path: str | None = None
    save: bool = False
    label: str | None = None


def _check_token(token: str | None) -> None:
    if config.API_TOKEN and token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing token")


@app.get("/api/state")
def get_state(x_token: str | None = Header(default=None)):
    # Token-checked for the same reason /ws is: this returns the FULL snapshot --
    # balance, equity, login, and every open position. Leaving it open while /ws was
    # locked would have been security theatre: an attacker who can reach the port just
    # polls this instead of opening a socket.
    _check_token(x_token)
    return worker.get_state()


@app.get("/api/account/safety")
def account_safety(x_token: str | None = Header(default=None)):
    """Lightweight endpoint the UI calls BEFORE enabling Auto-Test.

    Returns just the fields the browser needs to make the demo/real decision —
    deliberately minimal so the UI doesn't have to scrape /api/state. Mirrors
    the account block from worker.get_state(); kept thin so the safety check
    on the START button doesn't pull positions, stats, ticks, etc.
    """
    _check_token(x_token)
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


# ---- automated strategy: status + enable/disable + tune ------------------
@app.get("/api/strategies")
def strategies_status(x_token: str | None = Header(default=None)):
    """Status of EVERY engine, keyed by id (also in /ws state under `strategies`)."""
    _check_token(x_token)
    st = worker.get_state().get("strategies")
    if st:
        return st
    return {sid: s.status() for sid, s in worker.strategies.items()}


@app.get("/api/strategy/{sid}/status")
def strategy_status_by_id(sid: str, x_token: str | None = Header(default=None)):
    _check_token(x_token)
    s = worker.strategies.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail=f"unknown strategy {sid!r}")
    return (worker.get_state().get("strategies") or {}).get(sid) or s.status()


@app.post("/api/strategy/{sid}")
async def strategy_control_by_id(sid: str, req: StrategyReq,
                                 x_token: str | None = Header(default=None)):
    """Enable/disable or retune ONE engine. Runs through the worker queue so it is
    serialized with ticks/orders. Params with value None are left unchanged.

    404 on an unknown id rather than silently doing nothing: a typo'd id that
    returned 200 would leave the caller believing a strategy was armed when no
    engine had ever heard of it."""
    _check_token(x_token)
    if sid not in worker.strategies:
        raise HTTPException(status_code=404, detail=f"unknown strategy {sid!r}")
    params = {k: v for k, v in req.model_dump().items() if k != "enabled" and v is not None}
    log.info("strategy control", extra={"event": "strategy_control_http",
                                        "strategy": sid, "enabled": req.enabled,
                                        "params": params})
    return JSONResponse(await _do({"action": "strategy", "id": sid,
                                   "enabled": req.enabled, "params": params or None}))


# ---- back-compat: the current web panel still talks to the un-namespaced pair.
# Both alias the straddle. Kept so the UI keeps working across the migration; drop
# them once webui/strategy.js has moved to the /{sid} routes.
@app.get("/api/strategy/status")
def strategy_status(x_token: str | None = Header(default=None)):
    _check_token(x_token)
    return strategy_status_by_id("straddle", x_token)


@app.post("/api/strategy")
async def strategy_control(req: StrategyReq, x_token: str | None = Header(default=None)):
    return await strategy_control_by_id("straddle", req, x_token)


# ---- account profiles: list / save / delete -----------------------------
@app.get("/api/accounts")
def list_accounts(x_token: str | None = Header(default=None)):
    _check_token(x_token)
    return {"accounts": accounts.list_profiles()}


@app.post("/api/accounts")
def save_account(req: ProfileReq, x_token: str | None = Header(default=None)):
    _check_token(x_token)
    try:
        return accounts.save_profile(req.label, req.login, req.password,
                                     req.server, req.path or "")
    except RuntimeError as exc:           # keyring backend unavailable -> fail closed
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/accounts/{profile_id}")
def delete_account(profile_id: str, x_token: str | None = Header(default=None)):
    _check_token(x_token)
    return {"deleted": accounts.delete_profile(profile_id)}


# ---- account session: login / switch / logout ---------------------------
# The last profile that actually logged in. Used to put the session BACK after a failed
# login attempt -- see _restore_session(). Just an id; no secret is held here.
_last_good_profile: str | None = None


async def _restore_session() -> str | None:
    """Put the terminal back on the last profile that worked.

    ── Why this exists (measured, not theorised) ──

    `mt5.login()` with a wrong password does not merely fail -- it TEARS DOWN the session
    that was already running, and the worker does NOT recover on its own. Reproduced against
    a live terminal: logged in, bid streaming; one login with a bad password; and then
    `connected: False, error: "terminal not connected (-6: Terminal: Authorization failed)"`
    for as long as you care to wait.

    So a single fat-fingered password is enough to take the order pad off the market. If
    positions are open, the strategy engines stop managing their exits -- they cannot see an
    account. That is a very expensive way to punish a typo, and it became a great deal easier
    to trigger once the phone got a password field.

    Restoring the PREVIOUS account is also the honest semantics of a failed switch: the
    account you asked for is not available, so you are still on the one you were on.

    Returns the restored profile id, or None if there was nothing to restore or the restore
    itself failed (in which case the caller still reports the original error -- we must never
    let a recovery attempt mask the thing that actually went wrong).
    """
    pid = _last_good_profile
    if not pid:
        return None
    prof = accounts.get_profile(pid)
    pw = accounts.get_password(pid) if prof else None
    if not prof or not pw:
        return None
    try:
        res = await _do({"action": "login", "login": prof["login"], "password": pw,
                         "server": prof["server"], "path": prof.get("path") or None})
    except Exception:
        log.exception("session restore failed", extra={"event": "session_restore_failed",
                                                       "profile": pid})
        return None
    if res.get("ok"):
        log.warning("login failed; restored previous session",
                    extra={"event": "session_restored", "profile": pid})
        return pid
    log.error("login failed AND the previous session could not be restored",
              extra={"event": "session_restore_failed", "profile": pid,
                     "error": res.get("error")})
    return None


@app.post("/api/login")
async def login(req: LoginReq, x_token: str | None = Header(default=None)):
    """Log in or switch the terminal's account.

    Resolves a saved `profile_id` to credentials (password read from the OS
    vault) or uses ad-hoc credentials. The password is handed to the worker and
    is never logged here. Returns the worker result incl. `is_demo` and
    `prev_open` (positions left open on the previous account).

    A FAILED attempt puts the previous session back -- see _restore_session().
    """
    global _last_good_profile
    _check_token(x_token)
    login_id, password, server = req.login, req.password, req.server
    path = req.path

    if req.profile_id:
        prof = accounts.get_profile(req.profile_id)
        if not prof:
            raise HTTPException(status_code=404,
                                detail=f"profile {req.profile_id!r} not found")
        login_id, server = prof["login"], prof["server"]
        path = prof.get("path") or None
        password = accounts.get_password(req.profile_id)
        if not password:
            raise HTTPException(
                status_code=400,
                detail="stored password missing for this profile — re-add it")

    if not (login_id and password and server):
        raise HTTPException(status_code=400,
                            detail="login, password and server are required")

    res = await _do({"action": "login", "login": login_id,
                     "password": password, "server": server, "path": path})
    if not res.get("ok"):
        err = res.get("error") or "login failed"
        # The attempt just killed whatever session was running. Put it back before answering,
        # and SAY SO in the same breath -- a user who is told "login failed" and nothing else
        # has no reason to suspect they are now flat on the market.
        restored = await _restore_session()
        if restored:
            err = f"{err} — the previous account ({restored}) is still connected"
        elif _last_good_profile:
            err = (f"{err} — WARNING: the terminal is now DISCONNECTED and could not be "
                   f"restored. Log in again.")
        raise HTTPException(status_code=400, detail=err)

    # Persist on ad-hoc login if asked; best-effort (login already succeeded).
    if req.save and not req.profile_id:
        try:
            saved = accounts.save_profile(req.label, login_id, password, server,
                                          path or "", res.get("trade_mode"))
            _last_good_profile = saved.get("id")
        except RuntimeError:
            log.warning("login ok but profile not saved (no keyring backend)")
    elif req.profile_id:
        _last_good_profile = req.profile_id
        try:
            accounts.update_trade_mode(req.profile_id, res.get("trade_mode"))
        except Exception:
            pass
    return res


@app.post("/api/logout")
async def logout(x_token: str | None = Header(default=None)):
    _check_token(x_token)
    global _last_good_profile
    # Forget the restore target. Logging out is DELIBERATE: if the next login attempt fails,
    # silently reconnecting the account the user just walked away from would be the opposite
    # of what they asked for.
    _last_good_profile = None
    return JSONResponse(await _do({"action": "logout"}))


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

    Error shape: 400 responses use a STRUCTURED detail dict so the browser
    can populate the auto-test retcode histogram without text-parsing:
        { "message": "...", "retcode": 10018, "comment": "Market closed" }
    `detail.message` preserves the original human-readable string so existing
    UI toasts (which read `.detail`) remain backward-compatible.
    """
    _check_token(x_token)

    # Auto-Test live-account guard. Must fire BEFORE the order is submitted.
    if req.auto_test:
        st = worker.get_state()
        acc = st.get("account") or {}
        if not bool(acc.get("is_demo")):
            log.error("auto_test order refused on non-demo account", extra={
                "event": "auto_test_refused_live_account",
                "login": acc.get("login"),
                "server": acc.get("server"),
                "trade_mode": acc.get("trade_mode"),
                "side": req.side,
                "volume": req.volume,
            })
            raise HTTPException(
                status_code=403,
                detail=(
                    "auto_test orders refused: connected account is not a demo "
                    f"account (login={acc.get('login')!r}, "
                    f"server={acc.get('server')!r}, "
                    f"trade_mode={acc.get('trade_mode')!r})"
                ),
            )

    # Log every order request at HTTP-handler boundary so the file shows both
    # what the browser asked for AND what the worker actually did (the worker
    # also logs `order_request` / `order_filled` / `order_failed` independently).
    log.info("order request received", extra={
        "event": "order_request_http",
        "symbol": req.symbol,
        "side": req.side,
        "volume": req.volume,
        "type": req.type,
        "price": req.price,
        "sl": req.sl,
        "tp": req.tp,
        "auto_test": req.auto_test,
    })

    res = await _do({"action": "order", **req.model_dump()})
    if not res.get("ok"):
        message = res.get("error") or res.get("comment") or f"retcode {res.get('retcode')}"
        raise HTTPException(status_code=400, detail={
            "message": message,
            "retcode": res.get("retcode"),
            "comment": res.get("comment"),
        })
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


@app.post("/close_where")
async def close_where(req: CloseWhereReq,
                      x_token: str | None = Header(default=None)):
    """Bulk close filtered by live P&L sign: "all" | "losing" | "profit".

    `/close_all` remains as an alias for filter="all" -- the web UI's Esc hotkey
    and the Auto-Test stop path both call it, so it must keep working.
    """
    _check_token(x_token)
    if req.filter not in ("all", "losing", "profit"):
        raise HTTPException(
            status_code=400,
            detail=f"filter must be one of all|losing|profit, got {req.filter!r}")
    log.info("close-where requested", extra={"event": "close_where_http",
                                             "filter": req.filter})
    return JSONResponse(await _do({"action": "close_where", "filter": req.filter}))


@app.get("/api/config")
def client_config():
    """Unauthenticated capability probe, so a client knows whether to ask the
    user for a token BEFORE it fires a request and eats a 401. Deliberately
    leaks nothing: it reports THAT a token is required, never what it is."""
    return {"auth_required": bool(config.API_TOKEN), "poll_hz": int(config.POLL_HZ)}


@app.websocket("/ws")
async def ws(websocket: WebSocket, token: str | None = None, hz: float | None = None):
    """Live state stream.

    `token` is a QUERY PARAM, not a header, because the browser's WebSocket API
    cannot set headers on the handshake. OkHttp can, but using one mechanism for
    both clients keeps this from silently diverging.

    Auth here is not optional paranoia: this stream carries balance, equity and
    every open position. Before this check existed, `/ws` was readable by anyone
    who could reach the port even when API_TOKEN was set on every other route.

    `hz` lets a client ask for a slower push than the server's POLL_HZ. The phone
    asks for 5: at 15 Hz the full-snapshot stream is ~0.1-0.3 GB/hr, which is a
    lot of mobile data for a screen showing a quote and a few rows. Unchanged
    snapshots are skipped entirely.
    """
    # Accept FIRST, then close with 4401 on a bad token. This ordering is load-bearing:
    # calling close() *before* accept() makes Starlette reject the HTTP handshake with a
    # 403, and a browser cannot read a close code from a failed handshake -- it reports
    # 1006 (abnormal). The client's "stop retrying, the token is wrong" branch keys on
    # 4401, so a pre-accept close would send it into an infinite 1s reconnect loop
    # against a server that will never let it in. Accepting costs nothing: we send no
    # state before closing.
    await websocket.accept()
    if config.API_TOKEN and token != config.API_TOKEN:
        await websocket.close(code=4401)   # 4401: app-level "unauthorized"
        return

    # Clamp to [1, POLL_HZ]: a client cannot poll faster than the worker refreshes,
    # and 0/negative would be a busy-loop.
    #
    # NaN needs its own guard, because it does NOT clamp: it propagates through both
    # min() and max() unchanged (max(nan, 1.0) is nan), so `period` becomes nan and
    # asyncio.sleep(nan) returns instantly. `/ws?hz=nan` is accepted by pydantic as a
    # float and spins this loop ~87k times/sec, each pass taking the worker lock --
    # starving the thread that actually sends orders.
    if hz is None or not math.isfinite(hz):
        rate = float(config.POLL_HZ)
    else:
        rate = min(max(float(hz), 1.0), float(config.POLL_HZ))
    period = 1.0 / rate

    # Skipping unchanged snapshots saves real bandwidth, but SILENCE IS NOT FREE.
    #
    # webui/chart.js drives a synthetic random walk whenever no real frame has arrived
    # in the last 1500 ms (chart.js:178), and it starts that generator for every enabled
    # chart, not just in demo mode (chart.js:198). Before the skip existed, a snapshot
    # went out every 67 ms unconditionally, so the synthetic path could never fire
    # against a live feed. With the skip, a quiet market -- a weekend, thin rollover
    # hours, or a frozen MT5 feed -- produces byte-identical snapshots, the server goes
    # quiet, and after 1.5 s the chart begins plotting INVENTED candles around 2400 on
    # a live XAUUSD chart. A trader would be reading fabricated prices.
    #
    # A forced resend well inside that 1.5 s window keeps the feed provably alive while
    # still cutting an idle stream from 15 Hz to 1 Hz. It also restores dead-socket
    # detection: this loop never calls receive(), so a client that vanished is only
    # noticed when a send fails -- which, while skipping, might never happen.
    HEARTBEAT_S = 1.0

    last: dict | None = None
    last_sent = 0.0
    try:
        while True:
            state = worker.get_state()
            # `ts` changes on every poll, so compare everything else -- otherwise
            # nothing would ever look unchanged and the skip would never fire.
            cmp = {k: v for k, v in state.items() if k != "ts"}
            now = time.monotonic()
            if cmp != last or (now - last_sent) >= HEARTBEAT_S:
                await websocket.send_json(state)
                last = cmp
                last_sent = now
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

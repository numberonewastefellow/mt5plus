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
import hmac
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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import accounts
import config
import instance_paths
import log_context
from logger_setup import setup_logging
from mt5_worker import Mt5Worker

STATIC_DIR = Path(__file__).parent / "static"
WEBUI_DIR = Path(__file__).parent / "webui"

# Initialise the JSONL daily logger BEFORE constructing the worker, so any
# logging calls inside the worker's __init__ / start path land in the file
# from the very first line.
#
# The NAMESPACE stays "XauOrderPad" (every module's getLogger("XauOrderPad.x") is a
# child of it); the FOLDER and FILENAME are per-instance, so N servers write N files
# instead of interleaving one -- each with its own midnight rollover and prune, which
# cannot then race over the other's files. Unset XAUORDERPAD_INSTANCE -> original paths.
#
# set_instance FIRST: it is what stamps `instance` onto every line, including the
# "logger ready" line that setup_logging itself emits.
log_context.set_instance(instance_paths.instance_name())
setup_logging("XauOrderPad",
              file_prefix=instance_paths.app_name(),
              log_dir=instance_paths.log_dir())
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
            "              Desktop + phone on the same Wi-Fi: set BOTH",
            "                  XAUORDERPAD_HOST=0.0.0.0  AND  XAUORDERPAD_TOKEN=<secret>",
            "              (the server now REFUSES to start on a network bind with no token).",
            "              EC2: do NOT bind the network here -- the mTLS front door (Caddy on",
            "              8443) is the only way in; uvicorn stays on loopback. See deploy/README.",
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


def _require_auth_when_networked() -> None:
    """Refuse to start a trading API on the network with no token.

    API_TOKEN == "" disables auth. That is fine on loopback (only this machine can reach it, and
    it is how a plain `python server.py` on the desktop has always worked), and catastrophic on
    any other bind: the process places REAL orders, so an unauthenticated network socket lets any
    device on the LAN -- or the internet, on 0.0.0.0 -- trade the account and flatten the book.

    The banner used to only WARN about this and even recommended 0.0.0.0. Warnings are not
    controls. Same fail-closed posture the Caddy/mTLS design already takes: die instead."""
    loopback = config.HOST in ("127.0.0.1", "localhost", "::1")
    if not loopback and not config.API_TOKEN:
        raise SystemExit(
            f"REFUSING TO START: bound to {config.HOST} (reachable off this machine) with no "
            f"XAUORDERPAD_TOKEN set. That is an unauthenticated trading API on the network. "
            f"Set XAUORDERPAD_TOKEN, or bind 127.0.0.1 for loopback-only use."
        )


def _require_terminal_path_when_instanced() -> None:
    """A named instance MUST name its terminal. Same fail-closed posture as above.

    With several terminals running, `mt5.initialize()` WITHOUT a path does not pick
    "yours" -- it attaches to the machine's registry default. That was measured during
    bring-up: with three terminals up, a pathless initialize landed on
    `C:\\Program Files\\MetaTrader 5` every time, regardless of which account the
    caller wanted.

    So on a multi-instance box a blank MT5_PATH means every instance silently drives
    the SAME terminal -- and a close-all then flattens an account nobody aimed it at.
    Refuse to start instead. Unset XAUORDERPAD_INSTANCE (single-account mode) keeps
    the historical "attach to whatever is running" behaviour, which is correct there
    because there IS only one.
    """
    if instance_paths.instance_name() and not config.MT5_PATH:
        raise SystemExit(
            f"REFUSING TO START: XAUORDERPAD_INSTANCE="
            f"{instance_paths.instance_name()!r} is set but XAUORDERPAD_MT5_PATH is "
            f"empty. With several terminals running, a pathless mt5.initialize() "
            f"attaches to the machine default -- i.e. possibly another account's "
            f"terminal. Set XAUORDERPAD_MT5_PATH to this instance's terminal64.exe."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed BEFORE the worker starts or the socket serves: a networked bind with no token
    # is refused outright, not merely warned about.
    _require_auth_when_networked()
    _require_terminal_path_when_instanced()
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
        # HOST may be a BIND address (0.0.0.0 / ::), which Windows refuses as a CONNECT
        # target -- so wait_for_port() could never succeed, and every LAN start burned the
        # full 15 s timeout and then printed an un-openable http://0.0.0.0:8765/. This
        # window opens on the machine running the server, so loopback is always the right
        # thing to dial. Same all-interfaces test as _print_banner() above; _lan_ip() is
        # deliberately NOT used here -- that answers "what do I type into the phone?".
        dial = "127.0.0.1" if config.HOST in ("0.0.0.0", "::") else config.HOST
        url = f"http://{dial}:{config.PORT}/"
        launch_when_ready(url, dial, config.PORT,
                          getattr(config, "BROWSER_MODE", "app"))
    try:
        yield
    finally:
        worker.stop()
        log.info("server stopped", extra={"event": "server_stopped"})


app = FastAPI(title="XauOrderPad", lifespan=lifespan)


# Extra browser origins allowed to call this API, comma-separated (e.g. a dev tool on another
# port). Empty by default -- same-origin and loopback are always allowed without listing them.
_ALLOWED_ORIGINS = {
    o.strip().rstrip("/").lower()
    for o in os.environ.get("XAUORDERPAD_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
}


def _origin_allowed(origin: str | None, host: str | None) -> bool:
    """Reject cross-site browser requests; wave through everything without an Origin.

    The threat is CSRF: /close_all and /api/logout take no body, so a plain cross-origin HTML
    form POST reaches them with no preflight, and /ws leaks the account to any site (WebSocket
    handshakes are exempt from CORS). There is no other Origin/CSRF control anywhere.

    A browser ALWAYS attaches Origin on a cross-site request. Native clients -- the Android app's
    OkHttp, curl, the server's own tools -- send NONE, so `origin is None` must pass or we lock
    out the very clients this API is for. When Origin IS present it must be same-origin (its
    host:port equals the request's Host), loopback, or explicitly allow-listed.
    """
    if not origin:
        return True                       # no browser => no CSRF vector
    o = origin.strip().rstrip("/").lower()
    if o in _ALLOWED_ORIGINS:
        return True
    try:
        from urllib.parse import urlsplit
        oh = urlsplit(o).hostname
    except Exception:
        return False
    if oh in ("127.0.0.1", "localhost", "::1"):
        return True
    # Same-origin: the Origin's authority matches the Host header the request arrived with.
    if host and o.split("://", 1)[-1] == host.strip().lower():
        return True
    return False


@app.middleware("http")
async def _origin_guard(request, call_next):
    if not _origin_allowed(request.headers.get("origin"), request.headers.get("host")):
        return JSONResponse(status_code=403, content={"detail": "cross-origin request refused"})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def _redacted_validation_error(request, exc: RequestValidationError):
    """FastAPI's default 422 body echoes the SUBMITTED INPUT back to the caller. Pydantic v2 puts
    the whole request body in each error's `input`, so a POST /api/login or /api/accounts that
    fails validation (e.g. a missing `server`) returns the broker PASSWORD verbatim in the
    response. Strip `input` from every error before it leaves the process.

    The field locations and messages are kept -- that is what makes a 422 useful to the client
    (Api.parseError reads them) -- only the echoed values are dropped."""
    errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


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
    # Who suggested this order (e.g. "rider"). Attribution ONLY -- it selects a
    # broker comment from a server-side whitelist (Mt5Worker._order_comment) and
    # cannot change magic, routing, or any guard. Unknown values fall back to the
    # plain manual comment, so a forged value can only mislabel itself as manual.
    origin: str | None = None


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


class GuardReq(BaseModel):
    """Arm/disarm the account P&L guard: auto-close the whole book when FLOATING P&L hits a target.

    All fields optional (send just `enabled` to toggle). The WORKER enforces it against live broker
    P&L every poll; this endpoint only forwards — a guard that lived in the HTTP layer would be one
    forged request away from being bypassed, same reasoning as StrategyReq.
    """
    enabled: bool | None = None
    target_pl: float | None = None      # >= 0; the amount, in account currency
    side: str | None = None             # "profit" (close at >= +target) | "loss" (close at <= -target)


class TickLogReq(BaseModel):
    """Turn opt-in tick logging on/off. The WORKER owns the writer and persists the choice
    (ticklog.json), so it survives a restart; this endpoint only forwards the toggle."""
    enabled: bool | None = None


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
    #
    # This list is an ALLOWLIST, not documentation: pydantic drops anything not declared
    # here, silently and with a 200. A parameter added to the engine but forgotten here is
    # accepted by the API, echoed back unchanged in `params`, and simply never applied --
    # which looks exactly like an engine that ignores its own settings.
    side: str | None = None
    trigger: float | None = None
    max_positions: int | None = None
    max_lots: float | None = None
    entry_mode: str | None = None
    entry_step: float | None = None
    entry_gap_ms: int | None = None
    target: float | None = None
    stop_mode: str | None = None
    retrace: float | None = None
    floor_offset: float | None = None
    hard_sl: float | None = None
    cooldown_s: float | None = None
    max_ladders_per_day: int | None = None
    close_batch: int | None = None
    paper: bool | None = None
    # rider (vol-regime trend-rider; paper/suggestion only)
    thrust_mult: float | None = None
    sl: float | None = None
    trail: float | None = None
    tp: float | None = None
    max_hold: int | None = None
    atr_win: int | None = None
    use_ny_hours: bool | None = None
    risk_frac: float | None = None
    # Execution switches, one per account class. The ENGINE decides what these mean
    # and re-checks the connected account every poll (StrategyBase.evaluate); setting
    # auto_real here does not by itself let anything trade on a real account, because
    # the engine must also declare `allows_real`.
    auto_demo: bool | None = None
    auto_real: bool | None = None
    # shared
    volume: float | None = None
    max_daily_loss: float | None = None


# An MT5 login is a positive account number. The upper bound is deliberately generous (brokers
# use 6-10 digits) but finite: pydantic would otherwise happily accept 10**30 and write it into
# profiles.json, where it becomes a profile you can never log into and can only delete by hand.
_LOGIN_MIN, _LOGIN_MAX = 1, 999_999_999_999

# Length caps on the free-text account fields. `login` is bounded above; these were not, so a
# client could post a multi-megabyte label/server/password to be written into profiles.json or the
# credential vault. Generous vs any real broker value (servers/passwords are short), finite vs abuse.
_LABEL_MAX, _SERVER_MAX, _PASSWORD_MAX = 120, 120, 256


def _validated_server(v: str | None) -> str | None:
    """Broker server name: collapse whitespace, reject empty.

    Validated on the SERVER and not only in the phone, because the phone is not the only client
    -- the web panel and plain curl post here too. The server string is formatted straight into
    the profile id, so " Exness-MT5Trial16" and "Exness-MT5Trial16" would become two profiles
    for one account, each with its own vault entry, and the one you did not mean would fail to
    log in for reasons nobody could see.
    """
    if v is None:
        return None
    s = " ".join(v.split())
    if not s:
        raise ValueError("server must not be blank")
    return s


class ProfileReq(BaseModel):
    """Save (or overwrite) a saved account profile. The password is stored in
    the OS credential vault by accounts.save_profile; it is never persisted to
    profiles.json and never logged."""
    label: str | None = Field(default=None, max_length=_LABEL_MAX)
    login: int = Field(ge=_LOGIN_MIN, le=_LOGIN_MAX)
    # min_length=1: an empty password would be stored in the vault and then fail every login
    # with an opaque broker error. NOT stripped -- an MT5 password may legitimately contain
    # spaces, and silently trimming a user's password is how you lock them out of their account.
    password: str = Field(min_length=1, max_length=_PASSWORD_MAX)
    server: str = Field(max_length=_SERVER_MAX)
    # NOTE: there is deliberately NO `path` field. It used to exist here and on LoginReq, flowed
    # unvalidated into mt5.initialize(path=...), and that call LAUNCHES the named executable -- so a
    # UNC path (\\attacker\share\evil.exe) on this otherwise-unauthenticated API was remote code
    # execution, then persisted to profiles.json and replayed on every login and auto-restore. The
    # terminal path comes from config.MT5_PATH (env XAUORDERPAD_MT5_PATH on the box); a client must
    # never be able to choose which binary the server runs.

    @field_validator("server")
    @classmethod
    def _srv(cls, v: str) -> str:
        return _validated_server(v)


class LoginReq(BaseModel):
    """Log in / switch account. Either reference a saved `profile_id`, or pass
    ad-hoc `login`/`password`/`server` (optionally `save=True` to remember)."""
    profile_id: str | None = Field(default=None, max_length=_SERVER_MAX + 24)
    login: int | None = Field(default=None, ge=_LOGIN_MIN, le=_LOGIN_MAX)
    password: str | None = Field(default=None, max_length=_PASSWORD_MAX)
    server: str | None = Field(default=None, max_length=_SERVER_MAX)
    # No `path` -- see ProfileReq. The client does not get to pick the executable.
    save: bool = False
    label: str | None = Field(default=None, max_length=_LABEL_MAX)

    @field_validator("server")
    @classmethod
    def _srv(cls, v: str | None) -> str | None:
        return _validated_server(v)


def _token_ok(token: str | None) -> bool:
    """Constant-time token comparison. `==` on secrets leaks length and prefix through timing;
    hmac.compare_digest does not. Empty API_TOKEN means the check is disabled (loopback dev) --
    but the startup guard below refuses to run in that state on a non-loopback bind."""
    if not config.API_TOKEN:
        return True
    return isinstance(token, str) and hmac.compare_digest(token, config.API_TOKEN)


def _check_token(token: str | None) -> None:
    if not _token_ok(token):
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


# ---- account session state ----------------------------------------------
# Serialises account switching.
#
# /api/login awaits the worker, so two clients -- the desktop web UI and the phone -- can have
# logins in flight at the same time. Without this they interleave: the "what account are we on"
# snapshot one request takes can be invalidated by another request's login before the first one
# acts on it, so a restore can put the terminal on an account nobody asked for.
#
# Account identity is the one piece of state where "last writer wins" is not good enough,
# because every order the app sends afterwards is aimed at whatever it resolved to.
_account_lock = asyncio.Lock()


# ---- account profiles: list / save / delete -----------------------------
@app.get("/api/accounts")
def list_accounts(x_token: str | None = Header(default=None)):
    _check_token(x_token)
    return {"accounts": accounts.list_profiles()}


@app.post("/api/accounts")
def save_account(req: ProfileReq, x_token: str | None = Header(default=None)):
    _check_token(x_token)
    try:
        # No path: profiles never carry a client-supplied executable path any more.
        return accounts.save_profile(req.label, req.login, req.password, req.server)
    except RuntimeError as exc:           # keyring backend unavailable -> fail closed
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/accounts/{profile_id}")
def delete_account(profile_id: str, x_token: str | None = Header(default=None)):
    """Forget a saved profile: the index record AND the vault password.

    `active` in the response is the thing the caller must not ignore. Deleting the profile you
    are LOGGED INTO does not log you out -- the terminal keeps trading it -- but it does destroy
    the only copy of the password, so that session can no longer be restored if a later login
    fails. The row vanishing from the list while the account is still live is exactly the sort
    of quiet inconsistency that gets someone trading an account they think they removed.
    """
    _check_token(x_token)
    live = _live_account()
    was_active = bool(live and f"{live['login']}@{live['server']}" == profile_id)
    deleted = accounts.delete_profile(profile_id)
    if deleted and was_active:
        log.warning("forgot the profile the terminal is CURRENTLY logged into",
                    extra={"event": "active_profile_deleted", "profile": profile_id})
    return {"deleted": deleted, "active": was_active}


# ---- account session: login / switch / logout ---------------------------

def _live_account() -> dict | None:
    """The account the terminal is ACTUALLY on right now, or None if it is not connected.

    ── Why this is read from the worker and not remembered in a variable ──

    The first version of this kept a `_last_good_profile` global, updated by hand on each
    successful login. It was wrong, and wrong in the direction that costs money.

    The global was only written for profile logins and for saved ad-hoc logins. So: log into
    saved account A, then log in ad-hoc to B with "Remember" OFF (B succeeds; the global still
    says A), then fat-finger a password for C. The restore would read the stale global and put
    the terminal on **A** -- an account the user had left, possibly a REAL one -- while the
    error text claimed "the previous account (A) is still connected". It was not the previous
    account. B was. Every order after that would have gone to the wrong account.

    Any hand-maintained mirror of "which account are we on" is free to drift from the truth.
    The worker already knows, and reports it on every poll (`account.login` / `account.server`),
    so ask it. There is nothing to keep in sync and nothing to race.
    """
    st = worker.get_state()
    if not st.get("connected"):
        return None
    acc = st.get("account") or {}
    login_id, srv = acc.get("login"), acc.get("server")
    if not login_id or not srv:
        return None
    return {"login": int(login_id), "server": str(srv)}


async def _restore_session(prev: dict) -> str | None:
    """Put the terminal back on the account it was on before a failed login attempt.

    ── Why this exists (measured, not theorised) ──

    `mt5.login()` with a wrong password does not merely fail -- it TEARS DOWN the session that
    was already running, and the worker does NOT recover on its own. Reproduced against a live
    terminal: logged in with the bid streaming; one login with a bad password; and then
    `connected: False, error: "terminal not connected (-6: Terminal: Authorization failed)"`
    for as long as you care to wait.

    So a single fat-fingered password takes the order pad off the market. With positions open,
    the strategy engines stop managing their exits -- they cannot see an account. That is an
    expensive way to punish a typo, and it got far easier to trigger once the phone grew a
    password field.

    Restoring is also the honest semantics of a failed switch: the account you asked for is not
    available, so you are still on the one you were on.

    Restoring needs the password, and we hold none -- so it works only if that account is SAVED.
    An ad-hoc session with "Remember" off cannot be restored, and the caller says so plainly
    rather than leaving the user to discover it.

    Returns the restored profile id, or None. A failed restore must never mask the original
    error, so the caller reports both.
    """
    pid = f"{prev['login']}@{prev['server']}"
    prof = accounts.get_profile(pid)
    pw = accounts.get_password(pid) if prof else None
    if not prof or not pw:
        log.error("cannot restore the previous session: it is not a saved account",
                  extra={"event": "session_restore_impossible", "profile": pid})
        return None
    try:
        # No "path": the worker falls back to config.MT5_PATH. Any path left in an old
        # profiles.json is deliberately ignored -- it must not be able to launch a chosen binary.
        res = await _do({"action": "login", "login": prof["login"], "password": pw,
                         "server": prof["server"]})
    except Exception:
        log.exception("session restore failed",
                      extra={"event": "session_restore_failed", "profile": pid})
        return None
    if res.get("ok"):
        log.warning("login failed; restored the previous session",
                    extra={"event": "session_restored", "profile": pid})
        return pid
    log.error("login failed AND the previous session could not be restored",
              extra={"event": "session_restore_failed", "profile": pid,
                     "error": res.get("error")})
    return None


@app.post("/api/login")
async def login(req: LoginReq, x_token: str | None = Header(default=None)):
    """Log in or switch the terminal's account.

    Resolves a saved `profile_id` to credentials (password read from the OS vault) or uses
    ad-hoc credentials. The password is handed to the worker and is never logged here. Returns
    the worker result incl. `is_demo` and `prev_open` (positions left open on the previous
    account).

    A FAILED attempt puts the previous session back where it can -- see _restore_session() --
    and says so either way. The whole thing is under `_account_lock`: two clients switching
    accounts at once must not interleave.
    """
    _check_token(x_token)

    async with _account_lock:
        login_id, password, server = req.login, req.password, req.server

        if req.profile_id:
            prof = accounts.get_profile(req.profile_id)
            if not prof:
                raise HTTPException(status_code=404,
                                    detail=f"profile {req.profile_id!r} not found")
            login_id, server = prof["login"], prof["server"]
            password = accounts.get_password(req.profile_id)
            if not password:
                raise HTTPException(
                    status_code=400,
                    detail="stored password missing for this profile — re-add it")

        if not (login_id and password and server):
            raise HTTPException(status_code=400,
                                detail="login, password and server are required")

        # Snapshot what we are on BEFORE the attempt. This is the fact the restore needs, and
        # reading it here -- rather than trusting a remembered value -- is what keeps it true.
        prev = _live_account()

        # No "path": the worker uses config.MT5_PATH. A client never chooses the executable.
        res = await _do({"action": "login", "login": login_id,
                         "password": password, "server": server})

        if not res.get("ok"):
            err = res.get("error") or "login failed"
            # A DUPLICATE rejection is refused before MT5 is contacted at all, so the
            # existing session is untouched. Restoring here would re-login the account
            # we are already on -- which now fails against our OWN account lock (locks
            # are per-handle, so the same process conflicts with itself) and would
            # produce a bogus "the terminal is now DISCONNECTED" scare.
            if res.get("already_logged_in"):
                raise HTTPException(status_code=409, detail=err)
            # Otherwise the attempt just killed whatever session was running. Put it back if we
            # can, and SAY SO either way -- a user told only "login failed" has no reason to
            # suspect they are now off the market with positions open.
            if prev:
                restored = await _restore_session(prev)
                if restored:
                    err = f"{err} — you are still on {restored}"
                else:
                    err = (f"{err} — WARNING: the terminal is now DISCONNECTED "
                           f"({prev['login']}@{prev['server']} was not a saved account, so it "
                           f"could not be restored). Log in again.")
            raise HTTPException(status_code=400, detail=err)

        # Persist on ad-hoc login if asked. The login already succeeded, so a save failure is not
        # fatal -- but it must NOT be swallowed. It used to be caught and dropped with a log line,
        # so the client saw a plain 200 and told the user "stored on the server" when nothing was
        # stored. That matters: an unsaved account cannot be restored by _restore_session, so the
        # next failed login takes the terminal off the market with no way back. Report the outcome.
        if req.save and not req.profile_id:
            try:
                accounts.save_profile(req.label, login_id, password, server,
                                      last_trade_mode=res.get("trade_mode"))
                res["saved"] = True
                res["save_error"] = None
            except RuntimeError as exc:
                log.warning("login ok but profile not saved: %s", exc)
                res["saved"] = False
                res["save_error"] = str(exc)
        elif req.profile_id:
            try:
                accounts.update_trade_mode(req.profile_id, res.get("trade_mode"))
            except Exception:
                pass
        return res


@app.post("/api/logout")
async def logout(x_token: str | None = Header(default=None)):
    _check_token(x_token)
    # Under the same lock as login: a logout racing a login could otherwise tear down the
    # session the login just established, leaving the server believing it is connected.
    #
    # Nothing to forget afterwards -- the restore target is derived from the LIVE account
    # (_live_account), and once we are logged out there is no live account, so a subsequent
    # failed login has nothing to restore and correctly says so.
    async with _account_lock:
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
        "origin": req.origin,      # which UI suggested it (attribution audit trail)
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


@app.post("/api/guard")
async def set_guard(req: GuardReq, x_token: str | None = Header(default=None)):
    """Arm/disarm/retune the account P&L guard. Serialized through the worker queue with
    ticks/orders, so a fire never races a manual order. The worker enforces it; this only forwards."""
    _check_token(x_token)
    if req.side is not None and req.side not in ("profit", "loss"):
        raise HTTPException(status_code=400,
                            detail=f"side must be profit|loss, got {req.side!r}")
    if req.target_pl is not None:
        if not math.isfinite(req.target_pl) or req.target_pl < 0 or req.target_pl > 1e7:
            raise HTTPException(status_code=400,
                                detail="target_pl must be a finite number in [0, 1e7]")
    log.info("guard set", extra={"event": "guard_set_http", "enabled": req.enabled,
                                 "target_pl": req.target_pl, "side": req.side})
    return JSONResponse(await _do({"action": "guard", "enabled": req.enabled,
                                   "target_pl": req.target_pl, "side": req.side}))


@app.post("/api/ticklog")
async def set_ticklog(req: TickLogReq, x_token: str | None = Header(default=None)):
    """Enable/disable tick logging to CSV. Forwarded through the worker queue; the worker
    flips the writer and persists the flag so it survives a restart. Places no orders."""
    _check_token(x_token)
    log.info("ticklog set", extra={"event": "ticklog_set_http", "enabled": req.enabled})
    return JSONResponse(await _do({"action": "ticklog", "enabled": req.enabled}))


@app.get("/api/ticklog")
def get_ticklog(x_token: str | None = Header(default=None)):
    """Current tick-logging status: {enabled, rows, path}. Thin read of the worker snapshot."""
    _check_token(x_token)
    return worker.get_state().get("ticklog", {"enabled": False, "rows": 0,
                                              "path": config.TICKLOG_PATH})


@app.get("/api/history")
async def history(x_token: str | None = Header(default=None)):
    """Today's CLOSED trades (entry->exit paired) + current OPEN positions + PENDING orders,
    account-wide, with aggregate stats (net/gross P&L, win %, biggest/avg win & loss). Read-only,
    on-demand (history_deals_get is heavy); serialized through the worker queue so it never races a
    tick or order. 'Today' is local midnight -- matches the daily_realized shown live."""
    _check_token(x_token)
    return JSONResponse(await _do({"action": "history"}))


@app.get("/api/config")
def client_config():
    """Unauthenticated capability probe, so a client knows whether to ask the
    user for a token BEFORE it fires a request and eats a 401. Deliberately
    leaks nothing: it reports THAT a token is required, never what it is.

    `keyring_ok` lets the client decide UP FRONT whether "Remember this account" can work, rather
    than accept the save, get a 200, and only then find it was silently dropped. On the EC2 box
    the server runs as a session-0 Scheduled Task where a DPAPI vault can be unusable."""
    return {
        "auth_required": bool(config.API_TOKEN),
        "poll_hz": int(config.POLL_HZ),
        "keyring_ok": accounts.keyring_ok(),
    }


@app.websocket("/ws")
async def ws(websocket: WebSocket, token: str | None = None, hz: float | None = None):
    """Live state stream.

    The token is taken from the `x-token` HEADER if present, else the `token` QUERY PARAM.
    Native clients (the Android app's OkHttp) send the header -- which never lands in an access
    log. Browsers cannot set headers on a WebSocket handshake, so the web UI still uses the query
    param; that path is covered by the log-redaction filter here and by Caddy's query-redacting
    log on the box. Preferring the header keeps the secret out of the URL wherever the client can
    manage it.

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
    # CSRF: a browser on a malicious page can open a WebSocket to us (handshakes are exempt from
    # CORS), and would then stream balance/equity/positions to that page. The HTTP middleware does
    # not run for the /ws route, so the same Origin check is enforced here. Native clients send no
    # Origin and pass. Closed with 4403 (app-level "forbidden"), distinct from the 4401 token code.
    if not _origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=4403)
        return
    # Header wins over the query param -- see the docstring. A native client that sends the header
    # never puts the token in the URL.
    ws_token = websocket.headers.get("x-token") or token
    if not _token_ok(ws_token):
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


def _use_selector_loop_on_windows() -> None:
    """Do not let Windows' Proactor event loop serve this.

    ── Measured, in this project, with a phone on the Wi-Fi ──

    asyncio's default loop on Windows is the Proactor loop. Its accept() path surfaces transient
    network errors as OSError, and when one lands the accept loop **stops accepting for good**:

        Accept failed on a socket
        socket: <TransportSocket fd=1208, laddr=('0.0.0.0', 8765)>
        OSError: [WinError 64] The specified network name is no longer available

    The process does not exit. The MT5 worker is a separate thread, so it keeps polling, keeps
    logging "account snapshot" once a minute, and keeps the broker session alive. Everything
    looks healthy. The HTTP port simply never answers again -- not even on loopback.

    That is the worst failure shape a trading server has: up, connected to the broker, holding
    your positions, and unreachable. The phone shows a stale feed and no reason. Any client that
    was mid-order gets nothing back.

    WinError 64 is exactly what a phone roaming between Wi-Fi and cellular produces -- i.e. the
    normal operating condition of this app. It happened within minutes of a phone connecting.

    The Selector loop handles a failed accept per-connection and carries on serving, which is the
    behaviour every other platform already has. Its limits (no subprocess support, ~512 sockets)
    are irrelevant to a single-user order pad.
    """
    if sys.platform != "win32":
        return
    policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy is not None:
        asyncio.set_event_loop_policy(policy())


if __name__ == "__main__":
    import uvicorn

    _use_selector_loop_on_windows()
    # loop="asyncio" makes uvicorn honour the policy set above rather than picking its own.
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info", loop="asyncio")

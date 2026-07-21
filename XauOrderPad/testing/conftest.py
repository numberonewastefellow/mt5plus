"""Shared rig for the endpoint tests: ONE fake-broker server for the whole session.

── Why the fixtures live here and not in a test module ──

pytest registers fixtures per MODULE NAMESPACE. A session-scoped fixture that is imported
into a second test file (`from test_endpoints import live_server`) becomes a SECOND
FixtureDef with its own cache, so it runs AGAIN -- a second uvicorn thread racing the first
for the same port, dying with STARTUP_FAILURE inside a daemon thread. Every test still
passes, both files pass in isolation, and the only trace is a PytestUnhandledThreadException
warning attributed to whichever test happened to be running. conftest.py is the one place a
fixture can be shared with a single definition, which is why the rig lives here.

Read testing/README.md first. This fakes a broker: the stub refuses to import without
XAUORDERPAD_FAKE_MT5=1, publishes no host port, and must NEVER be run on the EC2 box.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import httpx
import pytest

os.environ.setdefault("XAUORDERPAD_FAKE_MT5", "1")

import MetaTrader5 as stub          # the fake one, first on PYTHONPATH
import config
import server                       # the REAL server module

PORT = 8766                         # never 8765 -- the real server lives there
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "test-token-do-not-use-in-prod"

# The stub self-identifies, so an accidental run against a real broker is impossible to miss.
assert stub.__version__.endswith("STUB")


class _LogCapture(logging.Handler):
    """Capture uvicorn.access records AFTER filters run, so we can prove the token was
    redacted on the real logging path rather than by re-implementing the regex here."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


ACCESS_LOG = _LogCapture()


@pytest.fixture(scope="session", autouse=True)
def live_server():
    import uvicorn
    from strategies import state as strategy_state

    config.LAUNCH_BROWSER = False       # would otherwise hunt for Chrome in the container
    config.API_TOKEN = ""               # individual tests flip this; _check_token reads it live

    # Start from NO persisted strategy state.
    #
    # StrategyBase.reconcile() resumes new entries when the saved state is younger than
    # LADDER_RESUME_MAX_AGE_S (600 s) -- correct in production, a landmine here. A run killed
    # mid-test leaves `enabled: true` with a fresh timestamp, so the NEXT session boots with a
    # live engine that starts trading during unrelated tests. The symptom is a cascade of
    # failures in tests that never touched a strategy, which is a miserable thing to debug.
    try:
        strategy_state._path().unlink(missing_ok=True)
    except Exception:
        pass

    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=PORT,
                         log_level="info", access_log=True)
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()

    for _ in range(100):                # wait for startup (lifespan starts the worker thread)
        try:
            httpx.get(f"{BASE}/api/config", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("stub server never came up")

    # Attach the capture handler AFTER uvicorn has started, not before.
    #
    # uvicorn.Server.run() calls logging.config.dictConfig(), which REMOVES existing handlers
    # from every logger it configures -- including uvicorn.access and uvicorn.error. A handler
    # added beforehand is silently wiped and captures nothing.
    #
    # dictConfig does NOT clear FILTERS, which is why server.py's redaction filter (installed
    # at import) survives. But that is exactly the sort of thing that must be demonstrated
    # rather than assumed, which is what test_be3 does.
    #
    # BOTH loggers: HTTP requests land on uvicorn.access, but the WebSocket accept line --
    # the only line that ever carries ?token= -- is emitted on uvicorn.error.
    for _name in ("uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(_name)
        lg.addHandler(ACCESS_LOG)
        lg.setLevel(logging.INFO)

    yield
    srv.should_exit = True
    t.join(timeout=5)


@pytest.fixture(autouse=True)
def clean():
    """Fresh book + logged-in session before each test."""
    stub._reset()
    config.API_TOKEN = ""
    _login()
    yield
    config.API_TOKEN = ""


def _login() -> None:
    r = httpx.post(f"{BASE}/api/login", timeout=10, json={
        "login": 999999, "password": "stub", "server": "STUB-NOT-REAL", "save": False,
    })
    assert r.status_code == 200, r.text
    _wait_healthy()


def _wait_healthy(timeout: float = 5.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        st = httpx.get(f"{BASE}/api/state", timeout=5,
                       headers=_hdr()).json()
        if st.get("healthy"):
            return st
        time.sleep(0.05)
    raise AssertionError("worker never became healthy")


def _hdr() -> dict:
    return {"x-token": config.API_TOKEN} if config.API_TOKEN else {}


def _ws_url(**params) -> str:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return f"ws://127.0.0.1:{PORT}/ws" + (f"?{q}" if q else "")

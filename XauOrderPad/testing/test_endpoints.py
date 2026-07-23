"""Endpoint tests for the REAL XauOrderPad server, against a FAKE MetaTrader5.

Nothing here is a reimplementation: `import server` pulls in the actual production module.
The only substitution is the MetaTrader5 package itself (testing/stub/), which is the sole
Windows-only dependency in the import graph.

Run:
    docker compose -f testing/docker-compose.yml exec tests pytest -v testing/test_endpoints.py

Read testing/README.md first. This fakes a broker.
"""

from __future__ import annotations

import json
import os
import time

import httpx
import pytest

os.environ.setdefault("XAUORDERPAD_FAKE_MT5", "1")

import MetaTrader5 as stub          # the fake one, first on PYTHONPATH
import config
import server                       # the REAL server module

# Helpers only -- NEVER the fixtures.
#
# `live_server` and `clean` live in conftest.py and are both autouse, so pytest supplies them
# to every test here without being asked. Importing them by name would register a SECOND
# FixtureDef in this module's namespace, with its own cache: a second uvicorn on the same
# port, and a teardown that closes the socket the surviving server is still using. See the
# note at the top of conftest.py.
from conftest import (              # noqa: F401
    ACCESS_LOG, BASE, PORT, TOKEN, _hdr, _login, _wait_healthy, _ws_url,
)


# ================================================================ the stub is fake, loudly

def test_stub_self_identifies_as_not_a_broker():
    """If this ever fails, someone pointed the tests at a real terminal."""
    acc = httpx.get(f"{BASE}/api/state", timeout=5).json()["account"]
    assert acc["server"] == "STUB-NOT-REAL"
    assert acc["is_demo"] is True
    assert acc["trade_mode"] != 2          # 2 == REAL


# ================================================================ BE-1: the heartbeat
#
# The bug: /ws skips unchanged snapshots. webui/chart.js falls back to a SYNTHETIC RANDOM
# WALK after 1500 ms of socket silence (chart.js:178) and starts that generator for every
# enabled chart, not just in demo (chart.js:198). So a quiet market -- a weekend, thin
# rollover hours, a frozen MT5 feed -- made the server go silent and the chart began drawing
# INVENTED candles on a live XAUUSD chart.
#
# The fix forces a resend every 1.0 s, comfortably inside chart.js's 1.5 s window.

@pytest.mark.asyncio
async def test_be1_heartbeat_keeps_the_socket_alive_when_nothing_changes():
    import websockets

    stub._freeze(True)                  # byte-identical snapshots => dedupe suppresses everything
    time.sleep(0.3)

    frames, t0 = 0, time.time()
    async with websockets.connect(_ws_url(hz=15)) as ws:
        while time.time() - t0 < 4.0:
            try:
                await __import__("asyncio").wait_for(ws.recv(), timeout=2.0)
                frames += 1
            except Exception:
                break

    # Pre-fix this was ONE frame in 4 s (the initial snapshot), the socket then went silent,
    # and chart.js started fabricating prices. Post-fix: ~1/s.
    assert frames >= 3, (
        f"only {frames} frames in 4s with a frozen tick -- the socket went quiet, so "
        f"chart.js would start drawing a synthetic random walk on a LIVE chart"
    )
    # And it must NOT be sending at the full 15 Hz -- the dedupe must still be doing its job.
    assert frames <= 12, f"{frames} frames in 4s: the skip-unchanged dedupe is not working"


@pytest.mark.asyncio
async def test_dedupe_still_suppresses_duplicates():
    """The heartbeat must not have turned the dedupe into dead code."""
    import asyncio
    import websockets

    stub._freeze(False)                 # tick moves => every snapshot differs => no skipping
    frames, t0 = 0, time.time()
    async with websockets.connect(_ws_url(hz=15)) as ws:
        while time.time() - t0 < 2.0:
            try:
                await asyncio.wait_for(ws.recv(), timeout=1.0)
                frames += 1
            except Exception:
                break
    assert frames > 15, f"moving tick should stream ~15/s, got {frames} in 2s"


# ================================================================ BE-2: the NaN busy-loop
#
# max(nan, 1.0) is nan and min(nan, 15.0) is nan, so `period = 1/nan = nan` and
# asyncio.sleep(nan) returns INSTANTLY. Pydantic accepts "nan" for a float, so /ws?hz=nan
# spun the loop ~87,000x/sec, each pass taking the worker lock -- starving the thread that
# actually sends orders.

async def _ws_loop_iterations(hz, seconds: float = 1.0) -> int:
    """Count how many times the /ws loop spins in `seconds`.

    The loop calls worker.get_state() exactly once per pass, so wrapping it counts
    iterations directly -- and get_state() is also what takes the worker lock, which is the
    resource the order path needs. This measures the bug itself, not a proxy for it.
    """
    import asyncio
    import websockets

    n = 0
    real = server.worker.get_state

    def counting():
        nonlocal n
        n += 1
        return real()

    server.worker.get_state = counting
    try:
        async with websockets.connect(_ws_url(hz=hz)) as ws:
            await ws.recv()                    # let it settle
            n = 0
            await asyncio.sleep(seconds)
    finally:
        server.worker.get_state = real
    return n


@pytest.mark.asyncio
async def test_be2_hz_nan_falls_back_to_poll_hz():
    """`/ws?hz=nan` must behave exactly like the default rate. Bounded on BOTH sides.

    pydantic accepts the string "nan" for `float | None` (verified: "abc" is rejected, "nan"
    is not), and NaN then propagates through min() and max() unchanged -- max(nan, 1.0) is
    nan -- so `period = 1/nan = nan`.

    The consequence is PLATFORM-DEPENDENT, which is why this asserts a floor as well as a
    ceiling:

      * Windows (the production box): plain asyncio. sleep(nan) returns instantly and the
        loop SPINS -- measured at ~140,000 passes/sec -- taking the worker lock every time
        and starving the thread that sends orders.

      * Linux + uvloop (uvicorn[standard], i.e. this container): the NaN timer never fires
        and sleep(nan) HANGS. The /ws feed STALLS DEAD SILENT -- measured at 1 frame/sec,
        then nothing. Which is exactly the condition that makes chart.js fabricate prices.

    A ceiling-only assertion passes cheerfully while the feed is stalled -- I wrote that
    first, and the mutation test caught it. Assert the rate is SANE, not merely not-huge.
    """
    stub._freeze(False)

    baseline = await _ws_loop_iterations(15)          # POLL_HZ, the legitimate max
    nan_rate = await _ws_loop_iterations("nan")

    assert nan_rate >= max(5, baseline // 2), (
        f"/ws?hz=nan polled only {nan_rate}x in 1s (hz=15 baseline: {baseline}x) -- the feed "
        f"has STALLED. sleep(nan) never returns under uvloop. A silent socket is what makes "
        f"chart.js start drawing a synthetic random walk on a live chart."
    )
    assert nan_rate <= max(45, baseline * 3), (
        f"/ws?hz=nan spun the loop {nan_rate}x in 1s (hz=15 baseline: {baseline}x) -- it is "
        f"busy-looping and taking the worker lock on every pass, starving the order path."
    )


@pytest.mark.asyncio
async def test_hz_nan_does_not_starve_the_rest_path():
    """/api/state must still answer promptly while a hz=nan socket is open."""
    import asyncio
    import websockets

    stub._freeze(False)
    async with websockets.connect(_ws_url(hz="nan")) as ws:
        await ws.recv()
        await asyncio.sleep(0.5)
        t0 = time.time()
        r = httpx.get(f"{BASE}/api/state", timeout=5.0)
        latency = time.time() - t0

    assert r.status_code == 200
    assert latency < 1.0, f"/api/state took {latency:.2f}s -- the order path is being starved"


@pytest.mark.parametrize("hz,expect_min,expect_max", [
    (0, 1, 4),          # clamped up to 1 Hz
    (99999, 20, 45),    # clamped down to POLL_HZ (15)
])
@pytest.mark.asyncio
async def test_hz_is_clamped(hz, expect_min, expect_max):
    import asyncio
    import websockets

    stub._freeze(False)
    frames, t0 = 0, time.time()
    async with websockets.connect(_ws_url(hz=hz)) as ws:
        while time.time() - t0 < 2.0:
            try:
                await asyncio.wait_for(ws.recv(), timeout=1.5)
                frames += 1
            except Exception:
                break
    assert expect_min <= frames <= expect_max, f"hz={hz} produced {frames} frames in 2s"


# ================================================================ BE-3: token redaction
#
# The token must be a QUERY param (the browser WebSocket API cannot set handshake headers),
# and uvicorn.access logs the path WITH its query string -- so the shared trading secret was
# being written to the console in plaintext on every reconnect.

@pytest.mark.asyncio
async def test_be3_token_is_redacted_from_the_access_log():
    import websockets

    config.API_TOKEN = TOKEN
    ACCESS_LOG.lines.clear()

    async with websockets.connect(_ws_url(token=TOKEN, hz=1)) as ws:
        await ws.recv()

    time.sleep(0.2)
    blob = "\n".join(ACCESS_LOG.lines)
    assert "/ws" in blob, f"no /ws access-log line captured: {ACCESS_LOG.lines!r}"
    assert TOKEN not in blob, "THE API TOKEN WAS WRITTEN TO THE ACCESS LOG IN PLAINTEXT"
    assert "token=<redacted>" in blob, f"redaction did not fire: {blob!r}"


# ================================================================ auth matrix

def test_api_config_is_open_and_leaks_nothing():
    config.API_TOKEN = TOKEN
    r = httpx.get(f"{BASE}/api/config", timeout=5)       # no token sent
    assert r.status_code == 200
    body = r.json()
    # Reports capability, never the secret itself. keyring_ok lets the client decide up front
    # whether "Remember this account" can work (it cannot under the EC2 box's S4U task if DPAPI
    # is unusable); it is a plain bool and reveals nothing sensitive.
    assert body["auth_required"] is True
    assert body["poll_hz"] == 15
    assert isinstance(body["keyring_ok"], bool)
    assert TOKEN not in r.text                            # reports THAT, never WHAT


def test_validation_error_does_not_echo_the_password():
    """A 422 must NOT contain the submitted password.

    FastAPI's default RequestValidationError body echoes the whole request body back in each
    error's `input` field -- so a login that fails validation returns the broker password
    verbatim. server.py installs a handler that strips `input`. This proves it: the password must
    be nowhere in the response, and the field location must still be reported.

    A BOUNDS violation on `login` (LoginReq constrains it with ge/le) is what forces a Pydantic
    422 -- the other fields are Optional, so a missing one is a plain 400 from the handler, with
    no echo. -1 is below the minimum, so Pydantic rejects it before the handler runs.
    """
    config.API_TOKEN = ""
    secret = "sup3r-secret-broker-pw"
    r = httpx.post(f"{BASE}/api/login", timeout=5,
                   json={"login": -1, "password": secret, "server": "X"})   # out of range -> 422
    assert r.status_code == 422, r.text
    assert secret not in r.text, "THE PASSWORD WAS ECHOED BACK IN THE 422 RESPONSE"
    # Still useful: the offending field is named.
    assert "login" in r.text


def test_history_aggregates_todays_closed_deals():
    """/api/history pairs entry->exit legs, tallies win/loss/flat, and totals net P&L.
    The stub seeds a fixed book (see stub._seed_deals): 2 wins (+57), 2 losses (-74), 1 flat."""
    r = httpx.get(f"{BASE}/api/history", timeout=30, headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True

    s = body["stats"]
    assert s["closed_count"] == 5
    assert s["wins"] == 2 and s["losses"] == 2 and s["flat"] == 1
    assert s["gross_profit"] == 57.00
    assert s["gross_loss"] == -74.00
    assert s["net"] == -17.00
    assert s["biggest_win"] == 48.00
    assert s["biggest_loss"] == -52.00
    assert s["win_rate"] == round(2 / 5, 4)

    # One closed row per OUT leg, newest first, with entry/exit paired.
    closed = body["closed"]
    assert len(closed) == 5
    exits = [row["exit_time"] for row in closed]
    assert exits == sorted(exits, reverse=True), "closed trades must be newest-first"
    top = closed[0]
    assert top["entry_price"] is not None and top["exit_price"] is not None
    assert top["side"] in ("BUY", "SELL")


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/state", None),
    ("POST", "/close_where", {"filter": "losing"}),
    ("GET", "/api/history", None),
])
def test_gated_endpoints_401_without_token(method, path, body):
    config.API_TOKEN = TOKEN
    r = httpx.request(method, f"{BASE}{path}", json=body, timeout=10)
    assert r.status_code == 401
    r = httpx.request(method, f"{BASE}{path}", json=body, timeout=10,
                      headers={"x-token": TOKEN})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ws_bad_token_closes_with_4401_not_1006():
    """The close must happen AFTER accept(). Closing before accept() makes Starlette reject
    the HTTP handshake with a 403, and a browser cannot read a close code from a failed
    handshake -- it sees 1006. The client's 'stop retrying, the token is wrong' branch keys
    on 4401, so a pre-accept close would send it into an infinite 1 s reconnect loop."""
    import websockets

    config.API_TOKEN = TOKEN
    with pytest.raises(websockets.exceptions.ConnectionClosed) as exc:
        async with websockets.connect(_ws_url(token="WRONG")) as ws:
            await ws.recv()
    assert exc.value.rcvd is not None, "handshake was refused -- client would see 1006, not 4401"
    assert exc.value.rcvd.code == 4401, f"got close code {exc.value.rcvd.code}, expected 4401"


@pytest.mark.asyncio
async def test_backward_compat_no_token_configured():
    """With no env vars set, everything must behave exactly as it did before auth existed."""
    import websockets

    config.API_TOKEN = ""
    assert httpx.get(f"{BASE}/api/config", timeout=5).json()["auth_required"] is False
    assert httpx.get(f"{BASE}/api/state", timeout=5).status_code == 200      # no token, no 401
    async with websockets.connect(_ws_url()) as ws:                          # tokenless /ws accepted
        assert json.loads(await ws.recv())["symbol"]


# ================================================================ MULTI-ACCOUNT SAFETY
#
# Two invariants keep N servers from trading each other's accounts. Both fail CLOSED,
# and both are the kind of fault that is silent when it breaks -- there is no error
# anywhere, just orders landing on the wrong account. Hence tests.


def test_wrong_terminal_blocks_trading():
    """A server attached to a terminal that is NOT its own must refuse to drive it.

    This is invariant I1. `positions_get()` returns only the ATTACHED account's book,
    so a mis-binding does not error -- it silently operates on someone else's money.
    The stub reports path="/stub"; point config at a different terminal and the worker
    must go unhealthy rather than carry on.
    """
    original = config.MT5_PATH
    try:
        config.MT5_PATH = "/somewhere/else/terminal64.exe"
        end = time.time() + 5.0
        st = {}
        while time.time() < end:
            st = httpx.get(f"{BASE}/api/state", timeout=5, headers=_hdr()).json()
            if st.get("wrong_terminal"):
                break
            time.sleep(0.05)

        assert st.get("wrong_terminal"), f"guard never fired; state={st}"
        assert st["healthy"] is False, "WRONG TERMINAL BUT STILL HEALTHY -- would trade it"
        assert "WRONG MT5 terminal" in (st.get("error") or "")

        # And the money path must actually be shut, not merely flagged.
        r = httpx.post(f"{BASE}/order", timeout=10, headers=_hdr(),
                       json={"side": "buy", "volume": 0.01, "type": "market"})
        assert r.status_code >= 400 or r.json().get("ok") is False, \
            "AN ORDER WAS ACCEPTED WHILE ATTACHED TO THE WRONG TERMINAL"
    finally:
        config.MT5_PATH = original
        _wait_healthy()          # prove the guard releases once the path matches again


def test_matching_terminal_path_is_accepted():
    """The guard must not fire on a CORRECT binding -- dirname() of the exe vs ti.path.

    Guards against the obvious off-by-one here: terminal_info().path is the DIRECTORY,
    so comparing it to the exe path itself would reject every correctly configured
    instance.
    """
    original = config.MT5_PATH
    try:
        config.MT5_PATH = "/stub/terminal64.exe"     # stub's terminal_info().path == "/stub"
        st = _wait_healthy()
        assert not st.get("wrong_terminal")
        assert st["healthy"] is True
    finally:
        config.MT5_PATH = original


def test_duplicate_account_login_is_refused():
    """Invariant I2: a second server cannot log into an account already being driven.

    Simulated by taking the same account lock the worker takes, from this process,
    and then asking the server to log in. Two servers on one account would give it two
    close-all paths and two P&L guards, each blind to the other.
    """
    import instance_lock

    # Log the server OUT first, so it releases its own claim. Without this the lock
    # below conflicts with the SERVER's handle -- uvicorn runs in a thread of this very
    # process, and byte-range locks are per-handle, so we would be fighting ourselves
    # rather than testing the cross-server case.
    assert httpx.post(f"{BASE}/api/logout", timeout=10, headers=_hdr()).status_code == 200

    lock = instance_lock.account_lock(999999, "STUB-NOT-REAL")
    lock.acquire({"instance": "pretend-other-server", "port": 9999})
    try:
        r = httpx.post(f"{BASE}/api/login", timeout=10, json={
            "login": 999999, "password": "stub", "server": "STUB-NOT-REAL", "save": False,
        })
        assert r.status_code == 409, f"duplicate login was NOT refused: {r.status_code} {r.text}"
        assert "already in use" in r.text
        assert "pretend-other-server" in r.text, "the rejection must name WHO holds the account"
    finally:
        lock.release()

    # Released -> the same login now succeeds. Proves the lock gates rather than bricks.
    r = httpx.post(f"{BASE}/api/login", timeout=10, json={
        "login": 999999, "password": "stub", "server": "STUB-NOT-REAL", "save": False,
    })
    assert r.status_code == 200, r.text


def test_pinned_instance_refuses_a_different_account():
    """An instance named after an account must refuse to log in any other one.

    This is what makes naming instances `472200942` rather than `a1` trustworthy: the
    name stops being a mnemonic and becomes enforced. Refused BEFORE MT5 is touched, so
    the existing session is left exactly as it was.
    """
    original = config.EXPECT_LOGIN
    try:
        config.EXPECT_LOGIN = 999999            # the stub's account
        # The pinned account is still allowed.
        r = httpx.post(f"{BASE}/api/login", timeout=10, json={
            "login": 999999, "password": "stub", "server": "STUB-NOT-REAL", "save": False})
        assert r.status_code == 200, r.text

        # A DIFFERENT account must be refused, and must not disturb the session.
        r = httpx.post(f"{BASE}/api/login", timeout=10, json={
            "login": 888888, "password": "stub", "server": "STUB-NOT-REAL", "save": False})
        assert r.status_code >= 400, f"pinned instance accepted a foreign account: {r.text}"
        assert "only drives account" in r.text or "999999" in r.text

        st = httpx.get(f"{BASE}/api/state", timeout=5, headers=_hdr()).json()
        assert (st.get("account") or {}).get("login") == 999999, \
            "the refused login disturbed the session it should not have touched"
    finally:
        config.EXPECT_LOGIN = original


def test_wrong_account_on_the_terminal_blocks_trading():
    """If the terminal is switched to another account, the order path must shut.

    Different failure from the login guard: nobody called /api/login at all. Somebody
    changed the account inside MT5, and positions_get() would report the new account's
    book without complaint.
    """
    original = config.EXPECT_LOGIN
    try:
        config.EXPECT_LOGIN = 12345            # stub reports 999999 -> mismatch
        end = time.time() + 5.0
        st = {}
        while time.time() < end:
            st = httpx.get(f"{BASE}/api/state", timeout=5, headers=_hdr()).json()
            if st.get("wrong_account"):
                break
            time.sleep(0.05)

        assert st.get("wrong_account"), f"account guard never fired; state={st}"
        assert st["healthy"] is False, "WRONG ACCOUNT BUT STILL HEALTHY -- would trade it"
        assert "pinned to 12345" in (st.get("error") or "")

        r = httpx.post(f"{BASE}/order", timeout=10, headers=_hdr(),
                       json={"side": "buy", "volume": 0.01, "type": "market"})
        assert r.status_code >= 400 or r.json().get("ok") is False, \
            "AN ORDER WAS ACCEPTED WHILE THE TERMINAL HELD THE WRONG ACCOUNT"
    finally:
        config.EXPECT_LOGIN = original
        _wait_healthy()          # recovers once the pin matches again


def test_unpinned_instance_keeps_single_account_freedom():
    """EXPECT_LOGIN == 0 is the single-account default and must change nothing."""
    assert config.EXPECT_LOGIN == 0, "tests must leave EXPECT_LOGIN unpinned"
    st = _wait_healthy()
    assert not st.get("wrong_account")
    r = httpx.post(f"{BASE}/api/login", timeout=10, json={
        "login": 999999, "password": "stub", "server": "STUB-NOT-REAL", "save": False})
    assert r.status_code == 200, r.text


def test_relogin_to_same_account_is_not_self_blocked():
    """Logging into the account this very server already drives must still work.

    Byte-range locks are per-HANDLE, not per-process: a naive implementation takes a
    second handle on its own lock file, conflicts with itself, and refuses the user's
    own account while naming them as the culprit.
    """
    for attempt in range(3):
        r = httpx.post(f"{BASE}/api/login", timeout=10, json={
            "login": 999999, "password": "stub", "server": "STUB-NOT-REAL", "save": False,
        })
        assert r.status_code == 200, f"re-login #{attempt} blocked by our own lock: {r.text}"


# ================================================================ THE MONEY PATHS
#
# This is what the harness is for. Everything above protects the plumbing; this protects
# the account.

def _close_where(filt: str) -> httpx.Response:
    return httpx.post(f"{BASE}/close_where", json={"filter": filt},
                      timeout=30, headers=_hdr())


def test_close_losing_closes_ONLY_the_loser():
    winner = stub._seed("buy", 0.10, profit=+5.00)
    loser = stub._seed("sell", 0.10, profit=-3.00)
    _wait_healthy()

    r = _close_where("losing")
    assert r.status_code == 200
    body = r.json()

    assert body["ok"] is True
    assert body["closed"] == 1
    assert body["remaining"] == 0            # counted against the FILTER, not the whole book

    book = stub._book()
    assert loser not in book, "THE LOSER WAS NOT CLOSED"
    assert winner in book, "THE WINNER WAS CLOSED -- CLOSE LOSING TOOK THE WRONG POSITION"
    assert book[winner] == 5.00


def test_close_profit_closes_ONLY_the_winner():
    winner = stub._seed("buy", 0.10, profit=+5.00)
    loser = stub._seed("sell", 0.10, profit=-3.00)
    _wait_healthy()

    body = _close_where("profit").json()
    assert body["ok"] is True and body["closed"] == 1

    book = stub._book()
    assert winner not in book, "THE WINNER WAS NOT CLOSED"
    assert loser in book, "THE LOSER WAS CLOSED -- CLOSE PROFIT TOOK THE WRONG POSITION"


def test_close_all_flattens_the_book():
    stub._seed("buy", 0.10, profit=+5.00)
    stub._seed("sell", 0.10, profit=-3.00)
    stub._seed("buy", 0.05, profit=0.0)      # exactly flat: matches neither losing nor profit
    _wait_healthy()

    body = _close_where("all").json()
    assert body["ok"] is True
    assert body["closed"] == 3
    assert stub._book() == {}


def test_a_flat_position_matches_neither_losing_nor_profit():
    """profit == 0 is strictly `< 0` false and `> 0` false. It must be left alone by both."""
    flat = stub._seed("buy", 0.05, profit=0.0)
    _wait_healthy()

    assert _close_where("losing").json()["closed"] == 0
    assert _close_where("profit").json()["closed"] == 0
    assert flat in stub._book(), "a zero-profit position was closed by a signed filter"


def test_ok_true_when_only_NON_matching_positions_remain():
    """A leftover winner after CLOSE LOSING is not a failure. `remaining` counts against the
    filter, not the book -- otherwise every CLOSE LOSING on a mixed book reports failure."""
    stub._seed("buy", 0.10, profit=+5.00)
    stub._seed("sell", 0.10, profit=-3.00)
    _wait_healthy()

    body = _close_where("losing").json()
    assert body["ok"] is True, "a surviving WINNER was miscounted as a failed CLOSE LOSING"
    assert len(stub._book()) == 1


def test_predicate_is_reread_from_live_broker_state_each_pass():
    """The whole reason this is server-side: a position can cross zero between the frame the
    phone rendered and the close landing. If _select() snapshotted once outside the loop,
    that would be a stale-snapshot close."""
    stub._seed("sell", 0.10, profit=-3.00)
    _wait_healthy()

    before = stub._calls()["positions_get"]
    _close_where("losing")
    after = stub._calls()["positions_get"]

    # _select() is called at the top of each retry pass AND once more for `remaining`.
    assert after - before >= 2, (
        "positions_get was called once -- the filter is being evaluated against a SNAPSHOT, "
        "not live broker state"
    )


def test_a_FAILED_bulk_close_returns_HTTP_200_with_ok_false():
    """This is the premise the entire Android B1 fix rests on.

    The server gives up after 5 passes and answers {ok:false, remaining:N} -- with HTTP 200,
    because it is a well-formed answer, not a protocol error. A client that trusts the status
    code reports "Closed 0 position(s)" IN GREEN on a losing book, from the lock screen.

    If anyone ever "fixes" this to a 4xx, the Android mapping becomes wrong. Pinned here.
    """
    stub._seed("sell", 0.10, profit=-3.00)
    stub._seed("sell", 0.10, profit=-8.00)
    _wait_healthy()
    stub._fail_order_send(True)

    r = _close_where("all")

    assert r.status_code == 200, "the failure path must stay HTTP 200 -- Android relies on it"
    body = r.json()
    assert body["ok"] is False
    assert body["closed"] == 0
    assert body["remaining"] == 2
    assert len(stub._book()) == 2, "nothing should have closed"


def test_unknown_filter_is_rejected():
    r = _close_where("everything")
    assert r.status_code == 400


# ================================================================ the degraded frame
#
# The Android model is ALL-NULLABLE and the notification's "no data" branch both depend on
# the exact shape of this frame. If the server ever starts emitting `positions: []` instead
# of omitting the key, the phone would render "Flat" while the book is unknown.

@pytest.mark.asyncio
async def test_degraded_frame_omits_account_positions_and_prices_entirely():
    import websockets

    httpx.post(f"{BASE}/api/logout", timeout=10, headers=_hdr()).raise_for_status()
    time.sleep(0.3)

    async with websockets.connect(_ws_url()) as ws:
        frame = json.loads(await ws.recv())

    assert frame["logged_out"] is True
    assert frame["connected"] is False
    assert frame["healthy"] is False

    # ABSENT, not null, not empty. `positions: []` would be read by the phone as "flat".
    for key in ("account", "positions", "bid", "ask", "digits", "point"):
        assert key not in frame, (
            f"{key!r} is present in a degraded frame. The Android client treats an absent "
            f"`positions` key as 'unknown' and an empty list as 'FLAT' -- emitting [] here "
            f"would tell a trader holding 8 lots that they are flat."
        )


# ---------------------------------------------------------------- the startup banner
#
# The banner is not decoration: its "type this into the Android app" line is the ONLY place
# the operator is told which address the phone should dial. If it prints an address the phone
# cannot reach, the bring-up fails in a way that looks like a firewall or a Tailscale problem
# and is neither.
#
# The specific bug these lock down: _lan_ip() UDP-connects to 8.8.8.8 and reports the source
# address the routing table picked -- i.e. the route to the PUBLIC INTERNET. On the EC2 box
# that is the AWS private 172.31.x.x, NOT the 100.x.y.z tailnet address the server is bound to.
# The banner used to print _lan_ip() for every non-loopback bind.

BANNER_LAN = "192.168.1.50"       # what the routing table would say
BANNER_TAILNET = "100.101.102.103"  # what the server is actually bound to


def _banner(monkeypatch, host, token="", lan=BANNER_LAN, port=8765) -> str:
    monkeypatch.setattr(config, "HOST", host)
    monkeypatch.setattr(config, "PORT", port)
    monkeypatch.setattr(config, "API_TOKEN", token)
    monkeypatch.setattr(server, "_lan_ip", lambda: lan)

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        server._print_banner()
    return buf.getvalue()


def test_banner_explicit_bind_prints_host_not_the_routing_table(monkeypatch):
    """The EC2 case. Bound to the tailnet address -> that is what the phone must dial."""
    out = _banner(monkeypatch, BANNER_TAILNET, token="t")

    assert f"http://{BANNER_TAILNET}:8765" in out
    assert BANNER_LAN not in out, (
        "The banner printed the routing-table IP for an EXPLICIT bind. On EC2 that is the AWS "
        "private 172.31.x.x, which the phone cannot reach -- captioned 'type this into the "
        "Android app'."
    )
    assert "type this into the Android app" in out


def test_banner_wildcard_bind_asks_the_routing_table(monkeypatch):
    """The desktop-LAN case. 0.0.0.0 names no address, so _lan_ip() is the right answer."""
    out = _banner(monkeypatch, "0.0.0.0", token="t")

    assert f"http://{BANNER_LAN}:8765" in out
    assert "0.0.0.0" not in out, "0.0.0.0 is a bind, not an address a phone can dial."


def test_banner_loopback_promises_no_network_url(monkeypatch):
    out = _banner(monkeypatch, "127.0.0.1")

    assert "NOT REACHABLE" in out
    assert "type this into the Android app" not in out
    assert BANNER_LAN not in out, "loopback must not advertise a network URL it does not have"
    # It must not recommend a bare 0.0.0.0 bind WITHOUT a token: the LAN-dev hint pairs the two,
    # and it must point EC2 at the mTLS front door rather than a raw network bind.
    assert "XAUORDERPAD_TOKEN" in out
    assert "mTLS front door" in out


def test_banner_shouts_when_on_the_network_with_no_token(monkeypatch):
    """The dangerous state: reachable + unauthenticated + places real orders."""
    out = _banner(monkeypatch, BANNER_TAILNET, token="")

    assert "*** NONE -- AND THIS SERVER IS ON THE NETWORK ***" in out
    assert "REAL MT5" in out
    assert "XAUORDERPAD_TOKEN" in out


def test_banner_port_is_not_hardcoded(monkeypatch):
    """PORT is env-configurable; the banner must follow it or it hands out a dead address."""
    out = _banner(monkeypatch, BANNER_TAILNET, token="t", port=9000)

    assert f"http://{BANNER_TAILNET}:9000" in out
    assert ":8765" not in out


# ---------------------------------------------------------------- fail-closed startup

def test_networked_bind_without_token_refuses_to_start(monkeypatch):
    """A non-loopback bind with no token is an unauthenticated trading API. It must DIE, not warn.

    This is the control the banner used to only hint at (and used to recommend the very bind that
    triggers it). server._require_auth_when_networked raises SystemExit; lifespan calls it before
    the worker starts or the socket serves.
    """
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    monkeypatch.setattr(config, "API_TOKEN", "")
    with pytest.raises(SystemExit):
        server._require_auth_when_networked()


def test_networked_bind_with_token_is_allowed(monkeypatch):
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    monkeypatch.setattr(config, "API_TOKEN", "a-real-token")
    server._require_auth_when_networked()          # must NOT raise


def test_loopback_without_token_is_allowed(monkeypatch):
    """The desktop default -- loopback, no token -- stays valid."""
    monkeypatch.setattr(config, "HOST", "127.0.0.1")
    monkeypatch.setattr(config, "API_TOKEN", "")
    server._require_auth_when_networked()          # must NOT raise


def test_cross_origin_request_is_refused_but_native_and_same_origin_pass():
    """CSRF guard: a browser cross-origin POST is 403'd; no-Origin (native app/curl) and
    same-origin are allowed. /close_all and /api/logout are bodyless, so this is the only thing
    standing between a malicious page and a flattened book when the token is empty."""
    config.API_TOKEN = ""

    # No Origin (the Android app, curl, tools) -> allowed.
    r = httpx.get(f"{BASE}/api/state", timeout=5, headers=_hdr())
    assert r.status_code == 200

    # Cross-origin browser -> refused, BEFORE the route runs.
    r = httpx.post(f"{BASE}/close_all", timeout=5,
                   headers={"Origin": "https://evil.example"})
    assert r.status_code == 403, r.text

    # Same-origin (Origin authority == Host) -> allowed through the guard.
    r = httpx.post(f"{BASE}/close_all", timeout=5,
                   headers={"Origin": f"http://127.0.0.1:{PORT}", "Host": f"127.0.0.1:{PORT}"})
    assert r.status_code != 403


def test_origin_helper_unit(monkeypatch):
    monkeypatch.setattr(server, "_ALLOWED_ORIGINS", set())
    assert server._origin_allowed(None, "anything") is True            # native client
    assert server._origin_allowed("https://evil.test", "box:8443") is False
    assert server._origin_allowed("http://127.0.0.1:8765", "x") is True  # loopback
    assert server._origin_allowed("https://box:8443", "box:8443") is True  # same-origin


def test_ws_token_check_is_constant_time_and_correct(monkeypatch):
    """_token_ok gates both HTTP and /ws. Empty token = open; else exact match only."""
    monkeypatch.setattr(config, "API_TOKEN", "")
    assert server._token_ok(None) is True          # disabled -> open

    monkeypatch.setattr(config, "API_TOKEN", "sekret")
    assert server._token_ok("sekret") is True
    assert server._token_ok("sekre") is False
    assert server._token_ok("sekrett") is False
    assert server._token_ok(None) is False
    assert server._token_ok("") is False

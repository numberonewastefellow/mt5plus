"""Trend-Ladder, driven through the REAL server against the FAKE broker.

`import server` and `from strategies...` pull in the actual production modules; the only
substitution is the MetaTrader5 package itself. So these tests exercise the real poll loop,
the real order path and the real magic filtering -- not a model of them.

Read testing/README.md first. This fakes a broker, refuses to load without
XAUORDERPAD_FAKE_MT5=1, and must NEVER be run on the EC2 box.

── Why the tick is frozen and driven by hand ──

The stub's free-running tick sweeps 2400.00 -> 2404.99 and wraps. That is fine for feed
tests, but a ladder is a statement about a PRICE PATH: arm above a level, pyramid while it
rises, flush when it comes back. Freezing the tick and setting the bid explicitly is the only
way to assert on a path rather than on whatever the sweep happened to be doing.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

os.environ.setdefault("XAUORDERPAD_FAKE_MT5", "1")

import MetaTrader5 as stub          # the fake one, first on PYTHONPATH
import config

# The session server and the per-test reset come from conftest.py, which is where pytest can
# share ONE definition of them. Importing them from the other test module instead registers a
# second FixtureDef with its own cache, which quietly starts a second server -- see the note
# at the top of conftest.py.
from conftest import BASE, _hdr        # noqa: F401

LADDER_MAGIC = 532028
SPREAD = 0.22                        # the stub's fixed ask-bid


def _px(bid: float) -> None:
    """Pin the broker's price. Frozen, so the worker sees exactly this until it is moved."""
    stub._freeze(True)
    with stub._lock:
        stub._s.bid = round(bid, 2)
        stub._s.tick_time = int(time.time())


def _arm(enabled=True, **params):
    """POST the engine's controls.

    StrategyReq is FLAT -- `enabled` sits alongside the parameters, there is no `params`
    wrapper. And it is an ALLOWLIST: pydantic drops anything it does not declare, silently,
    with a 200. Both mistakes look identical from the client (200, and nothing changed), so
    every test here asserts on the params the server ECHOES BACK, not on what was sent.
    """
    r = httpx.post(f"{BASE}/api/strategy/ladder", timeout=10, headers=_hdr(),
                   json={**params, "enabled": enabled})
    assert r.status_code == 200, r.text
    body = r.json()
    for k, v in params.items():
        assert body["params"].get(k) == pytest.approx(v) if isinstance(v, float)             else body["params"].get(k) == v, (
            f"server ignored {k}={v!r} (is it declared in StrategyReq?)")
    return body


def _status() -> dict:
    r = httpx.get(f"{BASE}/api/strategy/ladder/status", timeout=10, headers=_hdr())
    assert r.status_code == 200, r.text
    return r.json()


def _rungs() -> list:
    """Open positions belonging to the LADDER -- never the manual pad's."""
    with stub._lock:
        return [p for p in stub._s.positions.values() if int(p.magic) == LADDER_MAGIC]


def _settle(seconds: float = 1.2) -> None:
    """Let the 15 Hz worker poll a few times."""
    time.sleep(seconds)


@pytest.fixture(autouse=True)
def disarm_after():
    yield
    # Never leave an engine armed for the next test -- and the stub book is reset by `clean`,
    # so an armed ladder would otherwise re-fire against a fresh book. Also reset the volatile
    # brakes/toggles to their defaults: `_ladders_today` accumulates across the whole session
    # (it is a per-DAY counter), so a leaked `max_ladders_per_day` from one test trips "done for
    # today" and blocks the NEXT test's very first arm. Same idea for `auto_continue` / `cooldown_s`.
    try:
        httpx.post(f"{BASE}/api/strategy/ladder", timeout=10, headers=_hdr(),
                   json={"enabled": False, "auto_continue": False,
                         "cooldown_s": 0.0, "max_ladders_per_day": 0})
    except Exception:
        pass
    stub._freeze(False)


# ---------------------------------------------------------------- guards

def test_target_inside_the_spread_is_refused():
    """The oldest guard, and still the right shape: a target smaller than the spread makes
    even a WINNING trade a loss. The POST must say so, not report success and flip to
    disabled on the next poll."""
    _px(2400.00)
    s = _arm(side="buy", trigger=2399.00, target=0.10, stop_mode="retrace",
             retrace=0.50, paper=False)
    assert s["enabled"] is False
    assert "inside the live spread" in (s["error"] or "")
    assert _rungs() == []


def test_floor_inside_the_spread_is_refused():
    """New, and the same arithmetic. In floor mode the ladder is marked on the exit side the
    instant it arms, so a floor closer than the spread would flush on the very first tick --
    an engine that opens and closes a position forever, paying a spread each time."""
    _px(2400.00)
    s = _arm(side="buy", trigger=2399.00, target=1.00, stop_mode="floor",
             floor_offset=0.10, paper=False)
    assert s["enabled"] is False
    assert "inside the live" in (s["error"] or "")
    assert _rungs() == []


def test_unreachable_target_warns_but_is_allowed():
    """The live 2026-07-21 configuration: target 1.00 behind a 0.30 trail, which never once
    reached its target across 79 trades. It is improbable, not impossible -- so it WARNS and
    runs. Refusing a legal setting the operator chose would be the engine overruling them;
    staying silent about one that cannot win was the bug."""
    _px(2400.00)
    s = _arm(side="buy", trigger=2399.00, target=1.00, stop_mode="retrace",
             retrace=0.30, paper=True)
    assert s["enabled"] is True
    assert "0 target hits in 79 trades" in (s["warning"] or "")


# ---------------------------------------------------------------- the pyramid

def test_pyramid_is_capped_by_lots_not_by_count():
    """max_positions 0 means UNCAPPED, so max_lots is the only ceiling -- which is exactly the
    combination the slow-grind risk lives in. Walk the price up and assert the book stops
    growing at the lot cap and not before."""
    _px(2400.00)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=1.00,
         volume=0.01, max_positions=0, max_lots=0.05,
         entry_mode="timer", entry_gap_ms=100, paper=False)

    for step in range(1, 25):                    # a rising path, 0.05 a step
        _px(2400.00 + step * 0.05)
        time.sleep(0.12)                         # clears entry_gap_ms with room to spare

    rungs = _rungs()
    assert len(rungs) == 5, f"expected the 0.05 lot cap to bind, got {len(rungs)}"
    st = _status()
    assert st["open_positions"] == 5
    assert st["open_lots"] == pytest.approx(0.05)
    # Every rung is a SEPARATE position -- that is what needs_hedging protects.
    assert len({p.ticket for p in rungs}) == 5


def test_floor_flush_closes_the_whole_book_in_batches():
    """The exit that actually needed engineering. order_send is synchronous on the worker
    thread inside a ~66 ms budget, so a large book is drained `close_batch` per poll. Assert
    both halves: it drains in steps, and it finishes."""
    _px(2400.00)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=0.50,
         volume=0.01, max_positions=0, max_lots=0.06,
         entry_mode="timer", entry_gap_ms=100, close_batch=2, paper=False)

    for step in range(1, 25):
        _px(2400.00 + step * 0.05)
        time.sleep(0.12)
    assert len(_rungs()) == 6, "book did not fill"

    # Price returns THROUGH the floor (trigger 2400.10 - 0.50 = 2399.60, measured on the ask).
    _px(2399.00)

    seen_partial = False
    for _ in range(60):
        time.sleep(0.1)
        left = len(_rungs())
        if 0 < left < 6:
            seen_partial = True      # caught it mid-drain: proof it is not one big blocking loop
        if left == 0:
            break
    assert len(_rungs()) == 0, "the flush never finished"
    assert seen_partial, "the book vanished in one poll -- close_batch is not being honoured"

    # The status endpoint serves the POLL SNAPSHOT, which is built before the engines are
    # driven (see the ordering in Mt5Worker.poll_state) so that orders an engine places show
    # up on the following poll. It is therefore always one cycle behind the broker -- wait for
    # it to catch up rather than asserting the two are in lockstep.
    for _ in range(40):
        st = _status()
        if st["flush_remaining"] == 0 and st["ladders_done"] >= 1:
            break
        time.sleep(0.1)
    assert st["flush_remaining"] == 0
    assert st["ladders_done"] >= 1
    assert st["open_positions"] == 0


def test_flush_never_touches_another_magic():
    """The invariant the whole multi-engine design rests on. A manual position sits in the
    same book, on the same symbol, on the WRONG side -- and the ladder's flush must not see
    it. Every worker helper filters on magic; this proves it end to end."""
    _px(2400.00)
    manual = stub._seed("sell", 0.01, profit=-5.0, magic=config.MAGIC)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=0.50,
         volume=0.01, max_positions=0, max_lots=0.03,
         entry_mode="timer", entry_gap_ms=100, paper=False)
    for step in range(1, 20):
        _px(2400.00 + step * 0.05)
        time.sleep(0.12)
    assert len(_rungs()) == 3

    _px(2399.00)                                  # flush
    for _ in range(60):
        time.sleep(0.1)
        if not _rungs():
            break
    assert _rungs() == [], "ladder book did not flush"
    with stub._lock:
        assert manual in stub._s.positions, "the flush closed a MANUAL position"


# ---------------------------------------------------------------- the re-arm policy

def test_one_shot_does_not_re_arm_on_a_re_cross():
    """The default (auto_continue off). After a run's stop closes it, a bare price re-cross must
    NOT start a new run -- only a SET LEVEL does. This is the manual-reload workflow, and it makes
    the old 68-ladders-in-25-min churn impossible."""
    _px(2400.00)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=0.50,
         volume=0.01, max_positions=1, max_lots=0.0,
         entry_mode="timer", entry_gap_ms=100, paper=False)

    _px(2400.50)                                   # cross -> one rung
    _settle()
    assert len(_rungs()) == 1

    _px(2399.00)                                   # through the floor -> flush + park
    for _ in range(60):
        time.sleep(0.1)
        if not _rungs():
            break
    assert _rungs() == []

    # Cross the trigger up-and-down repeatedly -- one-shot must stay parked the whole time.
    for _ in range(4):
        _px(2400.60); time.sleep(0.2)
        _px(2400.00); time.sleep(0.2)
    _px(2400.60)
    _settle(1.5)
    assert _rungs() == [], "one-shot re-armed on a re-cross -- it must wait for a SET LEVEL"

    # A SET LEVEL (no enable toggle) re-engages -- price is already above the new trigger.
    r = httpx.post(f"{BASE}/api/strategy/ladder", timeout=10, headers=_hdr(),
                   json={"trigger": 2400.40})
    assert r.status_code == 200, r.text
    for _ in range(30):
        time.sleep(0.15)
        if _rungs():
            break
    assert len(_rungs()) == 1, "a SET LEVEL should re-arm after a one-shot park"


def test_auto_continue_re_takes_after_a_flush_while_past_the_level():
    """auto_continue ON: a run that flushes while price is still past the level starts a NEW run
    on its own -- the behaviour one-shot suppresses. (Boundary + churn-brake logic is unit-tested
    on `_rearm_gate` in test_ladder_state.py; this proves the wiring end to end.)"""
    _px(2400.00)
    # No max_ladders cap needed: the price is frozen, so run 2 arms and then just holds (no
    # bounce -> no further flush), which naturally bounds this test to two runs.
    _arm(side="sell", trigger=2399.90, target=5.00, stop_mode="retrace", retrace=0.40,
         volume=0.01, max_positions=1, entry_mode="timer", entry_gap_ms=100,
         auto_continue=True, paper=False)

    _px(2399.30)                                   # cross down -> run 1 (1 rung @ ~2399.30)
    _settle()
    assert len(_rungs()) == 1

    # Bounce 0.40 up but STILL below the 2399.90 trigger -> retrace flush, then auto re-arm at
    # the new (frozen) price. End state: run 1 done, a fresh run open -- not parked.
    _px(2399.72)
    took_again = False
    for _ in range(50):
        time.sleep(0.1)
        st = _status()
        if len(_rungs()) == 1 and st["ladders_done"] >= 1:
            took_again = True
            break
    assert took_again, "auto_continue did not re-take after a flush while price was past the level"


def test_cooldown_throttles_auto_continue():
    """In auto-continue, `cooldown_s` is the churn brake -- it holds the next run even though
    price is still past the level (the case where auto-continue would otherwise churn a chop)."""
    _px(2400.00)
    _arm(side="sell", trigger=2399.90, target=5.00, stop_mode="retrace", retrace=0.40,
         volume=0.01, max_positions=1, entry_mode="timer", entry_gap_ms=100,
         auto_continue=True, cooldown_s=3600, paper=False)

    _px(2399.30)                                   # run 1
    _settle()
    assert len(_rungs()) == 1
    _px(2399.72)                                   # flush run 1; price still below the level
    _settle(2.0)
    assert _rungs() == [], "cooldown did not throttle the auto-continue re-arm"
    assert "cooldown" in _status()["state"]


# ---------------------------------------------------------------- paper mode

def test_paper_mode_books_a_loss_and_places_nothing():
    """Paper mode exists to produce ONE number: what the trigger is worth net of the spread.
    It used to report 0.000 for every stop-out, because the basket was already emptied by the
    time the exit was booked -- so a losing engine looked harmless."""
    _px(2400.00)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=0.50,
         volume=0.01, max_positions=1, entry_mode="timer", entry_gap_ms=100, paper=True)

    _px(2400.50)
    _settle()
    _px(2399.00)                                   # through the floor
    _settle(1.5)

    st = _status()
    assert _rungs() == [], "paper mode placed a REAL order"
    assert st["paper_trades"] >= 1
    assert st["paper_pl_per_oz"] < 0, (
        f"a stop-out booked {st['paper_pl_per_oz']} -- paper P&L is not being recorded")


# ---------------------------------------------------------------- derived status

def test_effective_stop_includes_the_spread():
    """D3. The stop is measured on the entry-side price and realised on the exit side, so the
    operator gives up stop + spread. Derived server-side so the web panel and the phone cannot
    quote different numbers for the same engine."""
    _px(2400.00)
    _arm(side="buy", trigger=2399.00, target=2.00, stop_mode="floor", floor_offset=0.50,
         paper=True)
    _settle(0.5)
    st = _status()
    assert st["spread"] == pytest.approx(SPREAD, abs=0.01)
    assert st["effective_stop"] == pytest.approx(0.50 + SPREAD, abs=0.01)
    assert st["floor_price"] == pytest.approx(2399.00 - 0.50, abs=0.001)


# ---------------------------------------------------------------- manual reload UX


def _poll_status(pred, tries=40, gap=0.1) -> dict:
    """Poll the status endpoint until `pred(st)` holds (it lags the broker by one cycle)."""
    st = _status()
    for _ in range(tries):
        st = _status()
        if pred(st):
            break
        time.sleep(gap)
    return st


def test_a_parked_ladder_asks_for_a_new_level_then_clears():
    """The manual-reload contract. The engine rides ONE leg then PARKS -- it never re-enters
    itself. It latches needs_attention so the clients can alert the operator to set a new level,
    and clears it the instant they do. This is what replaces auto re-entry."""
    _px(2400.00)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=0.50,
         volume=0.01, max_positions=1, entry_mode="timer", entry_gap_ms=100, paper=False)
    _px(2400.50)                                   # cross -> one rung
    _settle()
    assert len(_rungs()) == 1
    _px(2399.00)                                   # through the floor -> flush + PARK
    for _ in range(60):
        time.sleep(0.1)
        if not _rungs():
            break
    assert _rungs() == []

    st = _poll_status(lambda s: s.get("needs_attention"))
    assert st["needs_attention"] is True, "a parked ladder must ask for a new level"
    assert "new level" in (st["attention_reason"] or "").lower()

    # Setting a NEW LEVEL (no enable toggle) is the manual re-engage -- it resolves the nag.
    r = httpx.post(f"{BASE}/api/strategy/ladder", timeout=10, headers=_hdr(),
                   json={"trigger": 2500.00})
    assert r.status_code == 200, r.text
    st = _poll_status(lambda s: not s.get("needs_attention"))
    assert st["needs_attention"] is False, "setting a new level did not clear the nag"


def test_would_fire_now_flags_an_already_crossed_level():
    """A BUY level below the ask (a SELL above the bid) arms INSTANTLY, not on a move. The
    server derives this from the live quote so both clients can warn before the operator
    commits -- the trap where a new level enters at once instead of waiting for the drop."""
    _px(2400.00)                                   # bid 2400.00, ask ~2400.22
    _arm(side="buy", trigger=2399.00, target=5.00, stop_mode="floor", floor_offset=0.50,
         paper=True)                               # trigger BELOW the ask -> already crossed
    st = _poll_status(lambda s: s.get("would_fire_now"))
    assert st["would_fire_now"] is True

    r = httpx.post(f"{BASE}/api/strategy/ladder", timeout=10, headers=_hdr(),
                   json={"trigger": 2500.00})       # now ABOVE the ask -> it would wait
    assert r.status_code == 200, r.text
    st = _poll_status(lambda s: not s.get("would_fire_now"))
    assert st["would_fire_now"] is False


def test_setting_a_new_level_overrides_the_cooldown():
    """A cooldown brakes the MACHINE re-trying the same idea; a deliberate new level is the
    operator's call and must not be held behind it. So an explicit trigger change clears the
    cooldown and re-arms from the new price -- even mid-cooldown."""
    _px(2400.00)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=0.50,
         volume=0.01, max_positions=1, entry_mode="timer", entry_gap_ms=100,
         cooldown_s=3600, paper=False)
    _px(2400.50)                                   # cross -> one rung
    _settle()
    assert len(_rungs()) == 1
    _px(2399.00)                                   # flush + park; the 3600 s cooldown now runs
    for _ in range(60):
        time.sleep(0.1)
        if not _rungs():
            break
    assert _rungs() == []

    # A bare re-cross here would be blocked by the cooldown (see test_cooldown_blocks). A
    # DELIBERATE new level must not be -- it re-arms from the new trigger straight away.
    _px(2400.50)
    r = httpx.post(f"{BASE}/api/strategy/ladder", timeout=10, headers=_hdr(),
                   json={"trigger": 2400.40})
    assert r.status_code == 200, r.text
    for _ in range(30):
        time.sleep(0.15)
        if _rungs():
            break
    assert len(_rungs()) == 1, "a deliberate new level should re-arm despite the cooldown"


# ---------------------------------------------------------------- trail activation


def test_trail_activate_holds_a_rung_through_an_early_dip():
    """End to end: trail_activate delays the retrace trail until the run is in profit. With it set,
    an early dip that a trail-from-entry would flush at a loss leaves the rung OPEN, and status
    reports the trail as not-yet-armed -- then arms it once price is up by trail_activate.

    Proves the whole wire: the param travels through StrategyReq, the engine honours it in the
    poll loop against the real order path, and the derived `trail_armed` flag reaches status so
    both clients can show "trail waiting" instead of reading the quiet stop as broken."""
    _px(2400.00)                                   # bid 2400.00, ask ~2400.22
    # trigger ABOVE the ask so it WAITS -- then a move up arms it and fixes the profit basis.
    _arm(side="buy", trigger=2400.30, target=5.00, stop_mode="retrace", retrace=0.40,
         trail_activate=1.00, volume=0.01, max_positions=1, entry_mode="timer",
         entry_gap_ms=100, paper=False)

    _px(2400.50)                                   # ask ~2400.72 > trigger -> arm, 1 rung
    _settle()
    assert len(_rungs()) == 1
    st = _status()
    assert st["trail_activate"] == pytest.approx(1.00)
    assert st["trail_armed"] is False, "the trail must not be armed before +1.0 of profit"

    # A 0.50 dip (> the 0.40 retrace) right after entry. A trail-from-entry (trail_activate=0)
    # would flush here at a loss; with the trail inert, the rung SURVIVES.
    _px(2400.00)
    _settle(1.5)
    assert len(_rungs()) == 1, "trail_activate let an early dip stop out the rung"
    assert _status()["trail_armed"] is False

    # Run up past +1.0 of profit (ask ~2401.82 vs the ~2400.72 entry) -> the trail arms.
    _px(2401.60)
    st = _poll_status(lambda s: s.get("trail_armed"))
    assert st["trail_armed"] is True, "the trail did not arm after the run made +1.0"
    assert len(_rungs()) == 1, "arming the trail must not itself close the rung"

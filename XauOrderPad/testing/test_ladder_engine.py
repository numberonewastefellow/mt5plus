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
    # so an armed ladder would otherwise re-fire against a fresh book.
    try:
        httpx.post(f"{BASE}/api/strategy/ladder", timeout=10, headers=_hdr(),
                   json={"enabled": False})
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


# ---------------------------------------------------------------- the re-arm latch

def test_a_completed_ladder_does_not_instantly_re_enter():
    """D1, the defect that turned 25 minutes into 68 ladders.

    After a ladder ends, price is still sitting past the trigger. The old engine armed a
    fresh ladder on the very next poll and bought again, forever. A crossing must be an
    EVENT: price has to trade back through the trigger first.
    """
    _px(2400.00)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=0.50,
         volume=0.01, max_positions=1, max_lots=0.0,
         entry_mode="timer", entry_gap_ms=100, paper=False)

    _px(2400.50)                                   # cross -> one rung
    _settle()
    assert len(_rungs()) == 1

    _px(2399.00)                                   # through the floor -> flush + ladder done
    for _ in range(60):
        time.sleep(0.1)
        if not _rungs():
            break
    assert _rungs() == []

    # Now put price back ABOVE the trigger and hold it there. The trigger has not been
    # RE-CROSSED from below since the ladder ended... except that the flush itself happened
    # below it, so the latch is satisfied and one new ladder is legitimate. What must NOT
    # happen is a stream of them.
    _px(2400.50)
    _settle(2.0)
    assert len(_rungs()) <= 1, (
        f"re-armed repeatedly while parked past the trigger: {len(_rungs())} rungs")


def test_cooldown_blocks_the_next_ladder():
    """The brake that does not depend on price shape at all."""
    _px(2400.00)
    _arm(side="buy", trigger=2400.10, target=5.00, stop_mode="floor", floor_offset=0.50,
         volume=0.01, max_positions=1, entry_mode="timer", entry_gap_ms=100,
         cooldown_s=3600, paper=False)

    _px(2400.50)
    _settle()
    assert len(_rungs()) == 1
    _px(2399.00)
    for _ in range(60):
        time.sleep(0.1)
        if not _rungs():
            break
    assert _rungs() == []

    _px(2400.50)                                   # cross again, well inside the cooldown
    _settle(2.0)
    assert _rungs() == [], "cooldown did not block the next ladder"
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

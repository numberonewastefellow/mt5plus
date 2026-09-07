"""Straddle-Ladder, driven through the REAL server against the FAKE broker.

`import server` / `from strategies...` pull in the actual production modules; only the
MetaTrader5 package is substituted. So these exercise the real poll loop, the real order path
and the real magic filtering.

The stub does NOT simulate broker-side SL/TP fills, so it cannot drive a resolution or the
continuation grid -- that logic is proven exhaustively in test_straddle_grid_state.py against a
tiny in-test broker. What this file proves is the WIRING the stub CAN exercise: arming places
both legs, on this engine's own magic, with the guard and the account gates in force.

Read testing/README.md first. This fakes a broker, refuses to load without XAUORDERPAD_FAKE_MT5=1,
and must NEVER be run on the EC2 box.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

os.environ.setdefault("XAUORDERPAD_FAKE_MT5", "1")

import MetaTrader5 as stub          # the fake one, first on PYTHONPATH
import config

from conftest import BASE, _hdr        # noqa: F401  (shared session server -- see conftest)

SLADDER_MAGIC = 532030
SPREAD = 0.22                        # the stub's fixed ask-bid


def _px(bid: float) -> None:
    """Pin the broker's price. Frozen, so the worker sees exactly this until it is moved."""
    stub._freeze(True)
    with stub._lock:
        stub._s.bid = round(bid, 2)
        stub._s.tick_time = int(time.time())


def _arm(enabled=True, **params):
    r = httpx.post(f"{BASE}/api/strategy/sladder", timeout=10, headers=_hdr(),
                   json={**params, "enabled": enabled})
    assert r.status_code == 200, r.text
    body = r.json()
    for k, v in params.items():
        assert (body["params"].get(k) == pytest.approx(v) if isinstance(v, float)
                else body["params"].get(k) == v), (
            f"server ignored {k}={v!r} (is it declared in StrategyReq?)")
    return body


def _status() -> dict:
    r = httpx.get(f"{BASE}/api/strategy/sladder/status", timeout=10, headers=_hdr())
    assert r.status_code == 200, r.text
    return r.json()


def _legs() -> list:
    """Open positions belonging to the STRADDLE-LADDER -- never the manual pad's or another engine's."""
    with stub._lock:
        return [p for p in stub._s.positions.values() if int(p.magic) == SLADDER_MAGIC]


def _poll_status(pred, tries=40, gap=0.1) -> dict:
    st = _status()
    for _ in range(tries):
        st = _status()
        if pred(st):
            break
        time.sleep(gap)
    return st


@pytest.fixture(autouse=True)
def disarm_after():
    yield
    # Never leave the engine armed for the next test: the stub book is reset by `clean`, so an
    # armed engine would re-fire against a fresh book.
    try:
        httpx.post(f"{BASE}/api/strategy/sladder", timeout=10, headers=_hdr(),
                   json={"enabled": False, "level": 0.0})
    except Exception:
        pass
    stub._freeze(False)


# ---------------------------------------------------------------- placement

def test_arming_at_the_level_opens_both_legs():
    """Set a level inside the current spread and the engine places a straddle at once: one long
    AND one short, both tagged this engine's magic."""
    _px(2400.00)                                     # bid 2400.00, ask 2400.22
    _arm(level=2400.11, sl=2.0, tp=2.0, gap=1.0, volume=0.01)
    for _ in range(40):
        if len(_legs()) == 2:
            break
        time.sleep(0.1)
    legs = _legs()
    assert len(legs) == 2, f"expected a long+short straddle, got {len(legs)}"
    sides = sorted(int(p.type) for p in legs)
    assert sides == [0, 1], "one BUY (0) and one SELL (1)"
    assert all(int(p.magic) == SLADDER_MAGIC for p in legs)
    st = _poll_status(lambda s: s.get("phase") == "straddle")
    assert st["phase"] == "straddle"


def test_tp_inside_the_spread_is_refused():
    """A take-profit smaller than the spread makes even a winning leg a loss. The POST must say so
    and place nothing -- the same guard shape the ladder carries."""
    _px(2400.00)
    s = _arm(level=2400.11, sl=2.0, tp=0.10, gap=1.0)   # tp 0.10 < spread 0.22
    assert s["enabled"] is False
    assert "inside the live spread" in (s["error"] or "")
    assert _legs() == []


def test_sl_inside_the_spread_is_refused():
    _px(2400.00)
    s = _arm(level=2400.11, sl=0.10, tp=2.0, gap=1.0)   # sl 0.10 < spread 0.22
    assert s["enabled"] is False
    assert "inside the live" in (s["error"] or "")
    assert _legs() == []


def test_arming_never_touches_another_magic():
    """The invariant the multi-engine design rests on: a manual position on the same symbol, on
    the wrong side, must be invisible to this engine. Every worker helper filters on magic."""
    _px(2400.00)
    manual = stub._seed("sell", 0.01, profit=-5.0, magic=config.MAGIC)
    _arm(level=2400.11, sl=2.0, tp=2.0, gap=1.0)
    for _ in range(40):
        if len(_legs()) == 2:
            break
        time.sleep(0.1)
    assert len(_legs()) == 2, "straddle legs did not open"
    with stub._lock:
        assert manual in stub._s.positions, "arming touched a MANUAL position"


# ---------------------------------------------------------------- derived status

def test_would_fire_now_flags_a_level_price_is_already_at():
    """The straddle fires the instant price sits at the level, so the server derives
    would_fire_now from the live quote -- both clients can warn that arming enters at once."""
    _px(2400.00)                                     # bid 2400.00, ask 2400.22
    _arm(enabled=False, level=2400.11, sl=2.0, tp=2.0)   # level inside the spread
    st = _poll_status(lambda s: s.get("would_fire_now"))
    assert st["would_fire_now"] is True
    assert _legs() == [], "disabled -> nothing placed"

    _arm(enabled=False, level=2500.00, sl=2.0, tp=2.0)   # level far above the ask
    st = _poll_status(lambda s: not s.get("would_fire_now"))
    assert st["would_fire_now"] is False

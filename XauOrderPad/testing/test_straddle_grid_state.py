"""StraddleGridState: the pure decision logic, no broker at all.

Two modes, one class, switched by `always_straddle`:
  * DEFAULT (always_straddle=False) -- SINGLE-LEG trend continuation: first order a straddle to find
    direction, then one leg on the trend side; each entry arms at the last SUCCESSFUL take-profit +/-
    gap (a TP advances, an SL retries the same level).
  * always_straddle=True -- WALKING STRADDLE GRID: every entry is a straddle.

Like `LadderState` it is free of MT5/IO/clock, so the exact traded code is driven tick-by-tick here.

── The tiny broker in `Sim` ──
Each leg has a real SL and TP; the state only learns a leg is gone by its ticket dropping out of the
open set. So `Sim` plays the broker: each tick it closes any leg whose bracket the price reached (a
long exits on the BID, a short on the ASK), then feeds the surviving open set into on_tick. A fixed
0.02 spread, always wider than nothing and narrower than sl/tp, keeps TP-vs-SL unambiguous.
"""

from __future__ import annotations

import pytest

from strategies.straddle_ladder import StraddleGridState

SPREAD = 0.02


class Sim:
    def __init__(self, level=100.0, sl=2.0, tp=2.0, gap=1.0,
                 max_legs=10, max_lots=0.0, volume=0.01, always_straddle=False):
        self.st = StraddleGridState(level, sl, tp, gap, max_legs, max_lots, volume, always_straddle)
        self.open: set[int] = set()
        self._t = 1000

    def _new(self) -> int:
        self._t += 1
        return self._t

    def _broker_fill(self, bid, ask) -> None:
        for tk, leg in list(self.st.legs.items()):
            if tk not in self.open:
                continue
            if leg["side"] == "buy":
                if bid >= leg["tp_px"] or bid <= leg["sl_px"]:
                    self.open.discard(tk)
            else:
                if ask <= leg["tp_px"] or ask >= leg["sl_px"]:
                    self.open.discard(tk)

    def tick(self, bid, ask) -> list[tuple]:
        self._broker_fill(bid, ask)
        acts = self.st.on_tick(bid, ask, set(self.open))
        for a in acts:
            if a[0] == "straddle":
                bt, sk = self._new(), self._new()
                self.st.register_straddle(bt, ask, sk, bid)     # buy @ ask, sell @ bid
                self.open.add(bt); self.open.add(sk)
            elif a[0] == "place":
                tk = self._new()
                self.st.register_cont(tk, ask if a[1] == "buy" else bid)
                self.open.add(tk)
            elif a[0] == "close":
                self.open.discard(int(a[1]))
        return acts


def kinds(acts):
    return [a[0] for a in acts]


def to_cont_up(s: Sim) -> Sim:
    """Straddle + resolve UP + let the winning long ride to its TP -> phase 'continuation', flat."""
    s.tick(99.99, 100.01)                              # straddle #1
    s.tick(101.98, 102.00)                             # short SL -> resolves UP (long still riding)
    s.tick(102.02, 102.04)                             # long winner TP -> flat, continuation
    return s


# ---------------------------------------------------------------- arming (both modes)

def test_straddle_fires_only_when_price_reaches_the_level():
    s = Sim(level=100.0)
    assert s.tick(97.98, 98.00) == []
    acts = s.tick(99.99, 100.01)
    assert kinds(acts) == ["straddle"] and s.st.phase == "straddle"
    assert len(s.st.legs) == 2 and s.st.n_entries == 1


# ---------------------------------------------------------------- DEFAULT: single-leg continuation

def test_single_leg_resolves_then_rides_the_trend_side_only():
    s = to_cont_up(Sim(level=100.0))
    assert s.st.direction == "buy" and s.st.phase == "continuation"
    assert s.st.trigger == pytest.approx(103.0)        # win 102 + gap 1
    assert s.st.n_entries == 1                          # only the straddle so far
    assert s.tick(102.50, 102.52) == [], "below the trigger -> wait"
    acts = s.tick(102.98, 103.00)                       # reaches 103 -> single LONG (no short)
    assert kinds(acts) == ["place"] and s.st.n_entries == 2
    assert len(s.st.legs) == 1, "one leg, not a straddle"


def test_single_leg_advances_on_a_tp():
    s = to_cont_up(Sim(level=100.0))
    s.tick(102.98, 103.00)                             # long #1 @ 103.00, tp_px 105.00
    s.tick(105.00, 105.02)                             # TP
    assert s.st.trigger == pytest.approx(106.0), "advance gap past the take-profit"


def test_single_leg_retries_the_same_level_on_an_sl():
    """The correction the operator made: an SL does NOT step off the stop. The next entry arms at the
    last SUCCESSFUL TP + gap -- i.e. the SAME level that just failed -- and waits for price to return."""
    s = to_cont_up(Sim(level=100.0))
    s.tick(102.98, 103.00)                             # long #1 @ 103.00 -> TP path below
    s.tick(105.00, 105.02)                             # TP -> trigger 106 (last TP 105 + gap)
    s.tick(105.98, 106.00)                             # long #2 @ 106.00, sl_px 104.00
    assert s.st.n_entries == 3
    s.tick(103.98, 104.00)                             # SL
    assert s.st.trigger == pytest.approx(106.0), "retry the SAME level (last TP 105 + gap 1)"
    assert s.st.direction == "buy", "an SL must not flip direction"
    # price returns to 106 -> retry the same long
    s.tick(105.50, 105.52)                             # near side -> re-arm
    acts = s.tick(105.98, 106.00)
    assert kinds(acts) == ["place"] and s.st.n_entries == 4


def test_single_leg_down_is_the_mirror():
    s = Sim(level=100.0)
    s.tick(99.99, 100.01)                              # straddle
    s.tick(97.98, 98.00)                               # long SL -> resolves DOWN (short riding)
    assert s.st.direction == "sell"
    assert s.st.trigger == pytest.approx(97.0)         # win 98 - gap 1
    s.tick(97.97, 97.99)                               # short winner TP -> flat
    acts = s.tick(97.00, 97.02)                        # bid 97.00 <= trigger 97 -> single SHORT
    assert kinds(acts) == ["place"] and s.st.direction == "sell"


def test_single_leg_is_sequential():
    s = to_cont_up(Sim(level=100.0))
    s.tick(102.98, 103.00)                             # long #1 open
    assert len(s.st.legs) == 1
    assert s.tick(104.00, 104.02) == [], "no 2nd order while one is open"
    assert len(s.st.legs) == 1


def test_single_leg_max_legs_parks():
    s = to_cont_up(Sim(level=100.0, max_legs=3))       # straddle=1; two orders -> 3
    s.tick(102.98, 103.00)                             # order (n_entries 2)
    s.tick(105.00, 105.02)                             # TP -> trigger 106
    s.tick(105.98, 106.00)                             # order (n_entries 3 == max)
    s.tick(108.00, 108.02)                             # TP -> trigger 109
    assert s.st.n_entries == 3
    assert s.tick(108.98, 109.00) == []                # at the cap -> no new order
    assert s.st.phase == "parked" and "max_legs" in (s.st.park_reason or "")


# ---------------------------------------------------------------- always_straddle: walking grid

def test_walking_grid_every_entry_is_a_straddle():
    s = Sim(level=100.0, always_straddle=True)
    s.tick(99.99, 100.01)                              # straddle #1
    s.tick(101.98, 102.00)                             # short SL -> resolve up (walking)
    assert s.st.phase == "armed" and s.st.level == pytest.approx(103.0)
    s.tick(102.02, 102.04)                             # long winner TP -> flat
    acts = s.tick(102.98, 103.00)                      # reaches 103 -> ANOTHER straddle (2 legs)
    assert kinds(acts) == ["straddle"] and len(s.st.legs) == 2 and s.st.n_entries == 2


def test_walking_grid_reversal_walks_back():
    s = Sim(level=100.0, always_straddle=True)
    s.tick(99.99, 100.01)
    s.tick(101.98, 102.00)                             # up -> level 103
    s.tick(102.02, 102.04)                             # winner TP -> flat
    s.tick(102.98, 103.00)                             # straddle #2 @ 103
    s.tick(103.00, 103.02)                             # settle
    s.tick(100.96, 100.98)                             # resolves DOWN
    assert s.st.direction == "sell" and s.st.level == pytest.approx(100.0)   # win 101 - gap 1


def test_max_lots_refuses_a_straddle_that_would_breach_the_cap():
    s = Sim(level=100.0, max_lots=0.015, volume=0.01)  # one straddle = 0.02 > cap
    assert s.tick(99.99, 100.01) == []
    assert s.st.n_entries == 0

"""LadderState: the pure decision logic, tested with no broker at all.

`LadderState` is deliberately free of MT5, of I/O and of the clock (see the note in
strategies/ladder.py) so the replay harness in analysis/ can run the EXACT code that trades
against historical ticks. That same property makes it directly unit-testable: ticks in,
actions out, nothing else.

These tests pin the GEOMETRY -- which side arms on which price, where the stop sits, which
cap binds first. test_ladder_engine.py covers the same engine end to end through the real
server; this file is the fast feedback loop underneath it, and it is where a sign error on
the sell side shows up first.
"""

from __future__ import annotations

import pytest

from strategies.ladder import LadderState, TrendLadder

TRIGGER = 100.0


def mk(**kw) -> LadderState:
    p = dict(side="buy", trigger=TRIGGER, target=1.0, max_positions=0, max_lots=0.0,
             volume=0.01, entry_mode="timer", entry_step=0.30, entry_gap_ms=200,
             stop_mode="floor", retrace=0.30, floor_offset=0.20)
    p.update(kw)
    return LadderState(**p)


def kinds(acts) -> list[str]:
    return [a[0] for a in acts]


# ---------------------------------------------------------------- arming

def test_arming_is_strict_and_reads_the_entry_side_price():
    """A buy arms on the ASK -- the price it would actually pay -- and only once the trigger
    is genuinely crossed. `>=` here would arm on a tick that merely touched the level."""
    st = mk()
    assert st.on_tick(99.96, 100.00, 0) == [], "ask == trigger must not arm"
    acts = st.on_tick(99.97, 100.01, 200)
    assert kinds(acts) == ["enter"]
    assert acts[0][1] == 0                       # rung id
    assert acts[0][2] == pytest.approx(100.01)   # entered at the ASK


def test_sell_is_the_mirror_image():
    """The asymmetry is not a modelling choice -- a short is opened at the bid and closed by
    BUYING at the ask, and that difference is the entire cost of the strategy."""
    st = mk(side="sell", floor_offset=0.20, target=99.0)
    assert st.floor == pytest.approx(TRIGGER + 0.20), "a sell's floor is ABOVE the trigger"
    acts = st.on_tick(99.99, 100.03, 0)          # bid below the trigger -> arm
    assert kinds(acts) == ["enter"]
    assert acts[0][2] == pytest.approx(99.99), "a sell enters at the BID"
    acts = st.on_tick(100.20, 100.24, 400)       # bid back at the floor
    assert kinds(acts) == ["exit_all"]
    assert acts[0][1] == pytest.approx(100.24), "a sell exits at the ASK"


# ---------------------------------------------------------------- the pyramid

def test_timer_mode_adds_only_on_a_better_price_and_only_after_the_gap():
    """This pair is what makes timer mode mean "keep adding WHILE IT RUNS" rather than
    "keep adding"."""
    st = mk(entry_gap_ms=200)
    st.on_tick(99.97, 100.01, 0)
    assert st.on_tick(99.96, 100.00, 400) == [], "added at a WORSE price"
    assert st.on_tick(99.98, 100.02, 100) == [], "added inside entry_gap_ms"
    assert kinds(st.on_tick(99.98, 100.02, 200)) == ["enter"]


def test_uncapped_means_uncapped():
    """max_positions 0 is UNCAPPED, not "none allowed" -- the reading that would turn the
    engine off rather than let it run free."""
    st = mk(max_positions=0, max_lots=0.0)
    st.on_tick(99.97, 100.01, 0)
    for k in range(20):
        st.on_tick(99.98 + k * 0.01, 100.02 + k * 0.01, 200 * (k + 1))
    assert len(st.entries) == 21
    assert [r for r, _ in st.entries] == list(range(21)), "rung ids must stay unique"


@pytest.mark.parametrize("caps,expected", [
    ({"max_positions": 3, "max_lots": 0.0}, 3),      # count binds
    ({"max_positions": 0, "max_lots": 0.05}, 5),     # lots bind
    ({"max_positions": 3, "max_lots": 0.05}, 3),     # both set -> the TIGHTER one binds
    ({"max_positions": 8, "max_lots": 0.04}, 4),     # both set -> the TIGHTER one binds
])
def test_whichever_cap_is_tighter_binds(caps, expected):
    st = mk(volume=0.01, **caps)
    st.on_tick(99.97, 100.01, 0)
    for k in range(12):
        st.on_tick(99.98 + k * 0.01, 100.02 + k * 0.01, 200 * (k + 1))
    assert len(st.entries) == expected


# ---------------------------------------------------------------- the stops

def test_floor_does_not_move_with_the_extreme():
    """The whole point of floor mode, and the difference from a trail: a ladder that has run
    a long way is NOT stopped out by a pullback, only by a return to where it was armed."""
    st = mk(stop_mode="floor", floor_offset=0.20, target=99.0)
    assert st.floor == pytest.approx(TRIGGER - 0.20)
    st.on_tick(99.97, 100.01, 0)
    for k in range(1, 8):                          # run it a long way up
        st.on_tick(100.0 + k * 0.5, 100.04 + k * 0.5, 200 * k)
    assert len(st.entries) > 1
    assert st.on_tick(102.00, 102.04, 3000) == [], "a huge pullback must not flush floor mode"
    assert kinds(st.on_tick(99.76, 99.80, 3200)) == ["exit_all"]


def test_retrace_trails_the_extreme():
    """The other mode, unchanged -- it tightens as the move runs, which is exactly why it
    exits early and often."""
    st = mk(stop_mode="retrace", retrace=0.30, target=99.0)
    st.on_tick(99.97, 100.01, 0)
    st.on_tick(100.30, 100.34, 200)                # extreme = 100.34
    acts = st.on_tick(99.99, 100.03, 400)          # pulled back 0.31
    assert kinds(acts) == ["exit_all"]
    assert acts[0][2] == "retrace"


def test_a_flush_carries_the_rungs_it_closes():
    """on_tick empties the basket as it emits exit_all, so the action has to carry what it is
    closing. A consumer that looked `entries` up afterwards would find nothing -- which is how
    paper mode came to book 0.000 for every stop-out, making a losing engine look harmless."""
    st = mk(stop_mode="floor", floor_offset=0.20, target=99.0)
    st.on_tick(99.97, 100.01, 0)
    st.on_tick(99.99, 100.03, 200)
    acts = st.on_tick(99.76, 99.80, 400)
    rungs = acts[0][3]
    assert [r for r, _ in rungs] == [0, 1]
    assert [pytest.approx(p) for _, p in rungs] == [100.01, 100.03]
    assert st.entries == [] and st.done


def test_target_closes_one_rung_and_carries_its_entry():
    st = mk(target=0.50, floor_offset=5.0)
    st.on_tick(99.97, 100.01, 0)                   # rung 0 @ 100.01
    st.on_tick(99.99, 100.03, 200)                 # rung 1 @ 100.03
    acts = st.on_tick(100.53, 100.57, 400)         # bid 100.53 clears +0.50 on both
    ones = [a for a in acts if a[0] == "exit_one"]
    assert len(ones) == 2
    assert ones[0][1] == 0 and ones[0][3] == pytest.approx(100.01)
    assert st.entries == []


def test_rollback_un_believes_a_refused_entry():
    """An order the broker refused must not appear to have happened: the phantom would consume
    a cap slot and send the stop chasing a ticket the broker never heard of."""
    st = mk()
    rid = st.on_tick(99.97, 100.01, 0)[0][1]
    st.rollback_entry(rid)
    assert st.entries == [] and st.n_taken == 0


# ---------------------------------------------------------------- trend stacking

def test_step_mode_stacks_down_a_trend_and_the_floor_holds():
    """The behaviour the operator asked for: an uncapped floor-mode SELL that pyramids DOWN a
    falling trend -- one rung per `entry_step` of new low -- and holds every rung through the
    pullbacks, flushing only when price comes back to the floor. A partial step must NOT add."""
    st = mk(side="sell", stop_mode="floor", floor_offset=0.20, target=99.0,
            entry_mode="step", entry_step=0.30, max_positions=0, max_lots=0.0)
    assert st.floor == pytest.approx(TRIGGER + 0.20)
    assert kinds(st.on_tick(99.99, 100.03, 0)) == ["enter"]        # rung 0 @ bid 99.99
    assert st.on_tick(99.84, 99.88, 100) == [], "a 0.15 dip is not a full step -> no add"
    for i in range(1, 5):                                          # each 0.35 lower (> step) adds ONE
        bid = round(99.99 - 0.35 * i, 2)
        assert kinds(st.on_tick(bid, round(bid + 0.04, 2), 100 + 100 * i)) == ["enter"], \
            f"a step down clear of entry_step should add rung {i}"
    assert len(st.entries) == 5
    assert st.on_tick(100.10, 100.14, 9000) == [], "a bounce below the floor must not flush"
    acts = st.on_tick(100.25, 100.29, 9200)                        # clearly back to the floor
    assert kinds(acts) == ["exit_all"]
    assert len(acts[0][3]) == 5, "the floor closes the WHOLE stack at once"


@pytest.mark.parametrize("spread", [0.04, 0.24, 0.50])
def test_retrace_trigger_is_measured_ask_to_ask_and_is_spread_invariant(spread):
    """The trailing stop is a DIFFERENCE on the entry-side series (highest ask - current ask for
    a buy), so the spread CANCELS: it fires when the ask gives back exactly `retrace` from its
    high, at the same tick for any spread, and FILLS at the bid. This is the regression pin for
    the "compare the bid to (highest_ask - retrace)" proposal -- that would double-count the
    spread and trip the stop a whole spread too early. bid = ask - spread throughout."""
    st = mk(side="buy", stop_mode="retrace", retrace=0.30, target=99.0,
            floor_offset=0.0, entry_mode="step", entry_step=5.0)
    st.on_tick(round(100.01 - spread, 2), 100.01, 0)               # arm @ ask 100.01
    st.on_tick(round(100.34 - spread, 2), 100.34, 200)             # extreme ask = 100.34
    assert st.on_tick(round(100.05 - spread, 2), 100.05, 300) == [], "0.29 off the high -> not yet"
    acts = st.on_tick(round(100.00 - spread, 2), 100.00, 400)      # 0.34 off the high -> fire
    assert kinds(acts) == ["exit_all"] and acts[0][2] == "retrace"
    assert acts[0][1] == pytest.approx(round(100.00 - spread, 2)), "fills at the bid = ask - spread"


# ---------------------------------------------------------------- trail activation


def test_trail_activate_zero_is_the_classic_trail_from_entry_and_can_lose():
    """The DEFAULT (trail_activate=0): the retrace trail is live from the first tick, so a dip
    straight after entry -- before the run has made a cent -- stops out at a LOSS. This is the
    behaviour trail_activate exists to make optional; pinned here so a change of default is loud."""
    st = mk(side="buy", stop_mode="retrace", retrace=0.30, target=99.0,
            trail_activate=0.0, entry_mode="step", entry_step=5.0)
    st.on_tick(99.97, 100.01, 0)                    # arm @ ask 100.01, extreme 100.01
    acts = st.on_tick(99.66, 99.70, 200)            # ask 0.31 BELOW entry, never any profit
    assert kinds(acts) == ["exit_all"] and acts[0][2] == "retrace"
    assert acts[0][1] < 100.01, "the classic trail books a LOSS on an early dip (exits below entry)"


def test_trail_activate_holds_through_the_early_dip_then_arms_in_profit():
    """trail_activate=1.0: the retrace trail is INERT until the run is up by 1.0. The same early
    dip that stopped the classic trail at a loss does nothing here; only after +1.0 does the trail
    arm, and the pullback that then fires it books a PROFIT."""
    st = mk(side="buy", stop_mode="retrace", retrace=0.30, target=99.0,
            trail_activate=1.0, entry_mode="step", entry_step=5.0)
    st.on_tick(99.97, 100.01, 0)                    # arm @ ask 100.01
    assert st.on_tick(99.66, 99.70, 200) == [], "0.31 dip before +1.0 profit: the trail is inert"
    assert st._trail_armed is False
    assert st.on_tick(101.06, 101.10, 400) == [], "run +1.09: arms the trail, no exit yet"
    assert st._trail_armed is True
    acts = st.on_tick(100.75, 100.79, 600)          # 0.31 off the 101.10 high -> fire
    assert kinds(acts) == ["exit_all"] and acts[0][2] == "retrace"
    assert acts[0][1] > 100.01, "the armed trail now exits in PROFIT, above the entry"


def test_trail_activate_at_retrace_never_stops_the_trail_below_entry():
    """The safety promise: with trail_activate >= retrace the trail cannot arm until the run is up
    by a full retrace, so its FIRST possible stop lands at breakeven on the entry-side price --
    never below entry. The realised fill still costs the spread, like every exit, and no more."""
    spread = 0.04                                   # bid = ask - 0.04 throughout
    st = mk(side="buy", stop_mode="retrace", retrace=0.30, target=99.0,
            trail_activate=0.30, entry_mode="step", entry_step=5.0)
    st.on_tick(99.97, 100.01, 0)                    # arm @ ask 100.01
    st.on_tick(100.31, 100.35, 200)                 # ask +0.34 -> arms (extreme 100.35)
    assert st._trail_armed is True
    acts = st.on_tick(100.00, 100.04, 400)          # 0.31 off the high -> fire at ask 100.04
    assert kinds(acts) == ["exit_all"]
    realized = acts[0][1] - 100.01                  # exit bid - entry ask = the buy's actual $/oz
    assert realized >= -spread - 1e-9, "trail_activate >= retrace booked a loss worse than the spread"


def test_trail_activate_is_mirrored_for_a_sell():
    """Same activation on the short side: profit is the bid FALLING, so the trail arms only once
    the bid is trail_activate below entry, and an early bounce before that does nothing."""
    st = mk(side="sell", stop_mode="retrace", retrace=0.30, target=99.0,
            trail_activate=1.0, entry_mode="step", entry_step=5.0)
    st.on_tick(99.99, 100.03, 0)                    # arm @ bid 99.99 (bid < trigger 100.0)
    assert st.on_tick(100.30, 100.34, 200) == [], "0.31 bounce before -1.0: the trail is inert"
    assert st._trail_armed is False
    assert st.on_tick(98.90, 98.94, 400) == [], "bid -1.09: arms the trail"
    assert st._trail_armed is True
    acts = st.on_tick(99.25, 99.29, 600)            # 0.35 off the 98.90 low -> fire
    assert kinds(acts) == ["exit_all"] and acts[0][2] == "retrace"
    assert acts[0][1] < 99.99, "a short's winning exit is an ASK below the entry bid"


# ---------------------------------------------------------------- re-arm policy (the gate)

def _parked_engine(side: str, trigger: float, auto_continue: bool) -> TrendLadder:
    """A TrendLadder in the exact state a run leaves behind: `_rearm_ok` False, no cooldown.
    `_rearm_gate` is pure enough (side + trigger + the quote) to test on its own -- no broker."""
    e = TrendLadder()
    e.side = side
    e.trigger = trigger
    e.auto_continue = auto_continue
    e.cooldown_s = 0.0
    e.max_ladders_per_day = 0
    e._rearm_ok = False
    e._last_ladder_end = 0.0
    e._needs_attention = False
    return e


def test_one_shot_gate_refuses_every_re_cross():
    """The default: once a run ends, a bare price move -- past the level, a re-cross up, anything
    -- never starts another run. Only a SET LEVEL / re-enable (which set `_rearm_ok`) does. This
    is what makes the old 68-ladders churn impossible."""
    e = _parked_engine("sell", 2399.90, auto_continue=False)
    assert e._rearm_gate(2399.00, 2399.04, 100.0) is not None      # far past the level
    assert e._rearm_ok is False
    assert e._rearm_gate(2400.50, 2400.54, 101.0) is not None      # a re-cross up
    assert e._rearm_ok is False


def test_auto_continue_gate_re_arms_while_price_is_past_the_level():
    e = _parked_engine("sell", 2399.90, auto_continue=True)
    assert e._rearm_gate(2399.00, 2399.04, 100.0) is None, "still below the trigger -> keep going"
    assert e._rearm_ok is True


def test_auto_continue_gate_stops_and_latches_on_return_to_the_level():
    """The boundary that makes auto-continue safe: when price comes back to the trigger the move
    is over, so it parks + alerts -- and STAYS parked even if price dips past again (the next
    move is a fresh decision, not an automatic re-entry)."""
    e = _parked_engine("sell", 2399.90, auto_continue=True)
    msg = e._rearm_gate(2399.95, 2399.99, 100.0)                   # back at the level
    assert msg is not None and "returned" in msg
    assert e._needs_attention is True and e._rearm_ok is False
    assert e._rearm_gate(2399.00, 2399.04, 101.0) is not None, "a returned move must not resume"
    assert e._rearm_ok is False


def test_auto_continue_gate_is_mirrored_for_a_buy():
    e = _parked_engine("buy", 2400.10, auto_continue=True)
    assert e._rearm_gate(2400.50, 2400.54, 100.0) is None, "ask past the trigger -> keep going"
    e2 = _parked_engine("buy", 2400.10, auto_continue=True)
    assert e2._rearm_gate(2399.90, 2399.94, 100.0) is not None, "ask back below -> stop"
    assert e2._needs_attention is True

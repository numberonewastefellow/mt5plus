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

from strategies.ladder import LadderState

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

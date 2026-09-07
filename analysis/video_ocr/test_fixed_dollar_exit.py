r"""Acceptance test for the FIXED-DOLLAR exit and the balance-scaled batch.

Run it:  python test_fixed_dollar_exit.py

This is the test the MQL5 engine failed. `RecoveryGridScalper` closed on
`TargetMove x (lots / 0.01)` -- a target proportional to open volume, which reduces algebraically
to "bid >= avg_entry + $0.30/oz" at EVERY depth. Under that rule adding positions buys exactly zero
progress toward the exit, so a basket that dipped and recovered sat waiting for a move it was never
going to need. Gate 2 below fails against it and passes against a fixed-dollar target.

The operator's description of the real strategy:

    normally the exit is $0.20-0.30/oz at 0.01 lot; but when the cycle goes into loss and comes
    back, it exits at about $0.10

is ONE rule, not two. A target fixed in dollars at cycle open (~29% of balance) produces both
numbers, because a basket that has added positions reaches the same dollars on a smaller move.

Analysis only -- no MT5, no orders. Every number here comes from OPERATOR_VERIFIED.md or cycles.csv.
"""
from __future__ import annotations

import sys

from grid_state import CONTRACT, FLAT, OPEN, PARKED, GridState
from lot_position_engine import Engine

# Balance, positions, lot and the $/oz distance the basket actually closed at, for the five cycles
# watched frame by frame. Source: OPERATOR_VERIFIED.md (cycles 5, 6, 7, 8, 10) -- where that
# document and a derived CSV disagree, the document wins.
VERIFIED = [
    # cycle, balance, positions, lot,  closed at $/oz
    (5,  1.47, 3, 0.01, 0.177),
    (6,  2.00, 3, 0.01, 0.280),
    (7,  2.84, 4, 0.01, 0.190),
    (8,  3.60, 4, 0.01, 0.290),
    (10, 5.84, 6, 0.01, 0.293),
]

# Positions the operator actually had open, by the balance the cycle opened with.
# Source: cycles.csv `max_positions_visible`. Cycles 1-3 never added, so their count IS the batch.
OBSERVED_COUNTS = [
    (0.30, 1), (0.76, 1), (0.99, 1), (1.14, 2), (1.47, 3),
    (2.00, 3), (4.76, 6), (5.84, 6), (7.60, 9), (10.61, 9),
]

FAILURES: list[str] = []


def check(gate: str, ok: bool, detail: str) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {gate}: {detail}")
    if not ok:
        FAILURES.append(gate)


# --------------------------------------------------------------------------- gate 1
def gate_fixed_dollar_reproduces_verified() -> None:
    """A target of ~29% of balance must predict the observed $/oz exits of all five cycles.

    Measured spread is 0.69-1.08x, so 0.60-1.20x is the acceptance band -- wide enough not to be
    fitted to these five, tight enough that a volume-scaled target cannot pass it.
    """
    print("\nGATE 1 -- fixed-dollar target reproduces the five hand-watched cycles")
    eng = Engine()
    worst = 0.0
    for cid, bal, npos, lot, observed in VERIFIED:
        oz = npos * lot * CONTRACT
        predicted = eng.take_profit_for(bal) / oz
        ratio = predicted / observed
        worst = max(worst, abs(1.0 - ratio))
        print(f"     cycle {cid:>2}  bal {bal:>5.2f}  {npos} pos  "
              f"predicted {predicted:.3f} $/oz vs observed {observed:.3f}  = {ratio:.2f}x")
    check("1", all(0.60 <= eng.take_profit_for(b) / (n * l * CONTRACT) / o <= 1.20
                   for _, b, n, l, o in VERIFIED),
          f"every cycle inside 0.60-1.20x (worst deviation {worst:.0%})")


# --------------------------------------------------------------------------- gate 2
def gate_adding_shortens_the_exit() -> None:
    """THE REGRESSION GUARD. Adding positions must reduce the price move the exit needs.

    This is what the MQL5 build got wrong and it is the reason the live run never exited. A target
    proportional to open volume leaves the required move constant, so this gate fails against it.
    """
    print("\nGATE 2 -- a deeper basket needs a SMALLER move (the recovery mechanism)")
    eng = Engine()
    bal = 1.00
    target = eng.take_profit_for(bal)
    needs = [target / (n * 0.01 * CONTRACT) for n in (1, 2, 3, 6)]
    for n, d in zip((1, 2, 3, 6), needs):
        print(f"     {n} position(s) = {n} oz  ->  needs {d:.3f} $/oz")
    strictly_falling = all(a > b for a, b in zip(needs, needs[1:]))
    # And the two numbers the operator quoted must both fall out of it.
    normal_ok = 0.20 <= needs[0] <= 0.30           # 1 position  -> "normally 0.20-0.30"
    recovered_ok = 0.05 <= needs[2] <= 0.15        # 3 positions -> "about 0.10"
    check("2", strictly_falling and normal_ok and recovered_ok,
          f"strictly falling={strictly_falling}, 1 pos={needs[0]:.3f} (0.20-0.30), "
          f"3 pos={needs[2]:.3f} (~0.10)")


# --------------------------------------------------------------------------- gate 3
def gate_batch_scales_with_balance() -> None:
    """A $1 account opens ONE position. The batch is a function of balance, not a constant 3."""
    print("\nGATE 3 -- batch size tracks the balance")
    eng = Engine()
    ok = True
    for bal, observed in OBSERVED_COUNTS:
        group = eng.positions_for_open(bal)
        cap = eng.max_positions_for(bal)
        # The batch may not exceed what the operator was seen holding in total at that balance.
        good = group <= observed and group >= 1
        ok &= good
        print(f"     bal {bal:>5.2f}  batch {group}  cap {cap:>2d}  (operator held {observed})")
    check("3a", ok, "batch never exceeds the observed total, and is never 0")
    check("3b", eng.positions_for_open(1.00) == 1,
          f"a $1 account opens {eng.positions_for_open(1.00)} position(s), must be 1")
    # Regression: the validated mapping at and above $2.50 must not have moved.
    caps = [eng.max_positions_for(b) for b in (2.84, 3.60, 4.76, 5.84, 7.60, 10.61, 11.88)]
    check("3c", caps == [3, 3, 6, 6, 9, 9, 9], f"caps >= $2.50 unchanged: {caps}")


# --------------------------------------------------------------------------- gate 4
def gate_operator_scenario() -> None:
    """The live case: enter at 4600 on $1, dip, bounce -- and actually exit on the bounce."""
    print("\nGATE 4 -- the operator's own scenario, replayed")
    eng = Engine()
    g = GridState(eng, "buy", 1.00, auto_reenter=False, use_dual_exit=True)
    tick = [0]
    spread = 0.04
    opened_first = None
    exit_at = None

    seq = [round(4600.00 - i * 0.01, 2) for i in range(51)]          # down to 4599.50
    seq += [round(4599.50 + i * 0.01, 2) for i in range(1, 91)]      # back up to 4600.40
    for mid in seq:
        bid, ask = round(mid - spread / 2, 3), round(mid + spread / 2, 3)
        for act in g.on_tick(bid, ask, set(g.positions)):
            if act[0] == "open":
                _, _side, lot, n = act
                ids = []
                for _ in range(n):
                    tick[0] += 1
                    ids.append(tick[0])
                g.register_entries(ids, ask, lot)
                if opened_first is None:
                    opened_first = n
                print(f"     open  mid {mid:8.2f}  +{n} -> {g.cycle.positions} pos")
            else:
                exit_at = (mid, act[1], g.cycle.basket_pnl)
                print(f"     EXIT  mid {mid:8.2f}  reason={act[1]}  "
                      f"pnl {g.cycle.basket_pnl:+.3f} on a {g.cycle.balance_open:.2f} balance")
                g.register_flat(g.balance + g.cycle.basket_pnl)
        if exit_at:
            break

    check("4a", opened_first == 1, f"opened {opened_first} position(s) at $1, must be 1")
    check("4b", exit_at is not None and exit_at[0] <= 4600.35,
          f"exited by 4600.35 (at {exit_at[0] if exit_at else 'never'})")


# --------------------------------------------------------------------------- gate 5
def gate_parked_can_still_exit() -> None:
    """A parked basket that recovers must be allowed to close, and must NOT re-enter after."""
    print("\nGATE 5 -- PARKED stops adding, not exiting")
    eng = Engine()
    g = GridState(eng, "buy", 10.00, auto_reenter=True, use_dual_exit=True)
    tick = [0]
    spread = 0.04
    seq = [round(4600.00 - i * 0.01, 2) for i in range(61)]
    seq += [round(4599.40 + i * 0.01, 2) for i in range(1, 121)]
    parked = False
    closed_from_park = False
    for mid in seq:
        bid, ask = round(mid - spread / 2, 3), round(mid + spread / 2, 3)
        acts = g.on_tick(bid, ask, set(g.positions))
        if g.phase == PARKED and not parked:
            parked = True
            print(f"     parked at mid {mid:.2f}: {g.park_reason}")
        for act in acts:
            if act[0] == "open":
                _, _side, lot, n = act
                ids = []
                for _ in range(n):
                    tick[0] += 1
                    ids.append(tick[0])
                g.register_entries(ids, ask, lot)
            else:
                if parked:
                    closed_from_park = True
                    print(f"     EXIT from PARKED at mid {mid:.2f}  reason={act[1]}  "
                          f"pnl {g.cycle.basket_pnl:+.2f} vs target {g.cycle.target:.2f}")
                g.register_flat(g.balance + g.cycle.basket_pnl)
    check("5a", parked and closed_from_park,
          f"parked={parked}, took the bounce={closed_from_park}")
    check("5b", g.phase == PARKED and not g.positions,
          f"still latched after banking it (phase={g.phase}, {len(g.positions)} open)")


def main() -> int:
    print("FIXED-DOLLAR EXIT + BALANCE-SCALED BATCH -- acceptance gates")
    gate_fixed_dollar_reproduces_verified()
    gate_adding_shortens_the_exit()
    gate_batch_scales_with_balance()
    gate_operator_scenario()
    gate_parked_can_still_exit()
    print()
    if FAILURES:
        print(f"FAILED: {', '.join(FAILURES)}")
        return 1
    print("ALL GATES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

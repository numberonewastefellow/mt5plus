r"""Replay harness for the Trend-Ladder engine.

Runs the EXACT decision code that trades (`strategies.ladder.LadderState`) over
historical ticks, so the engine and the model in TREND_LADDER_STRATEGY.md can be
held to each other. If they ever disagree, the engine has drifted from what was
validated -- fix the engine, not the doc.

The reference numbers it must reproduce (see doc §6c), for a random trigger, TP
1.00, retrace 0.30, timer entries at 5/sec:

    max_positions   1 -> about -$30 per ladder @1 lot
    max_positions  10 -> about -$105 per ladder @1 lot   (monotonically worse)

That monotonic decay IS the finding: the ladder multiplies cost, not edge.

Run:
    cd XauOrderPad
    .\.venv\Scripts\python.exe ..\analysis\ladder_replay.py
"""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "XauOrderPad"))

import MetaTrader5 as mt5                       # noqa: E402
from strategies.ladder import LadderState       # noqa: E402  <- the code that trades

SYMBOL = "XAUUSDm"
OZ = 100.0                                      # contract size: 1.00 lot = 100 oz
A, B = dt.datetime(2026, 7, 9, 12), dt.datetime(2026, 7, 9, 16)


def replay(bid, ask, ms, i0, **kw):
    """One ladder, triggered at tick i0. Returns (realized $/oz, positions taken).

    Trigger is set just beyond the current price so it fires on the very next
    tick -- an arbitrary entry, with no directional skill. That is the point: this
    measures the ENGINE's cost structure, not anyone's ability to pick a moment.

    Note the `before` snapshot. LadderState.on_tick() empties `entries` as part of
    emitting exit_all, so the basket must be read BEFORE the action is applied.
    Reading it after scores a flat book and reports zero loss -- which would make
    a losing engine look harmless.
    """
    st = LadderState("sell", trigger=bid[i0] + 0.001, target=kw["target"],
                     retrace=kw["retrace"], max_positions=kw["max_positions"],
                     entry_mode=kw.get("entry_mode", "timer"),
                     entry_step=kw.get("entry_step", 0.30),
                     entry_gap_ms=kw.get("entry_gap_ms", 200))
    pl = 0.0
    for j in range(i0, len(bid)):
        before = list(st.entries)               # snapshot BEFORE on_tick mutates
        for act in st.on_tick(float(bid[j]), float(ask[j]), int(ms[j])):
            if act[0] == "exit_one":
                pl += act[2] - act[1]
            elif act[0] == "exit_all":
                exit_px = act[1]
                for e in before:
                    pl += e - exit_px
        if st.done:
            return pl, st.n_taken
    return pl, st.n_taken


def main():
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}")
    t = mt5.copy_ticks_range(SYMBOL, A, B, mt5.COPY_TICKS_ALL)
    mt5.shutdown()
    if t is None or len(t) == 0:
        raise SystemExit("no ticks")

    bid = t["bid"].astype(float)
    ask = t["ask"].astype(float)
    ms = t["time_msc"].astype("int64")
    spread = float(np.median(ask - bid))
    print(f"{len(bid)} ticks  {A:%Y-%m-%d %H:%M}-{B:%H:%M} UTC   "
          f"median spread {spread:.3f}/oz")
    print()
    print("Random trigger (no directional skill). TP 1.00, retrace 0.30, 5 entries/sec.")
    print()
    print(f"{'max_positions':>14} {'win%':>8} {'avg/oz':>10} {'avg @1lot':>12}")

    rng = np.random.default_rng(7)
    starts = [int(x) for x in rng.choice(len(bid) - 3000, 400, replace=False)]
    for maxn in (1, 2, 3, 5, 10):
        out = [replay(bid, ask, ms, i, target=1.0, retrace=0.30,
                      max_positions=maxn)[0] for i in starts]
        R = np.array(out)
        print(f"{maxn:>14} {100 * (R > 0).mean():>7.1f}% {R.mean():>+10.3f} "
              f"{R.mean() * OZ:>+12.2f}")

    # ASCII only from here: the Windows console is cp1252 and a stray en-dash
    # kills the run AFTER the table has printed, which looks like a failure when
    # the result was fine.
    print()
    print("Expected (doc 6c): monotonically worse with size - 1 pos ~ -$30, 10 pos ~ -$105.")
    print("The ladder multiplies COST, not edge. If this table ever goes flat or")
    print("positive at a random trigger, the engine no longer matches the model.")


if __name__ == "__main__":
    main()

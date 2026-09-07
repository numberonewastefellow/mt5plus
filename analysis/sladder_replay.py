r"""Replay harness for the Straddle-Ladder engine, against RECORDED TICKS.

Runs the EXACT decision code that trades (`strategies.straddle_ladder.StraddleGridState`)
over a real tick stream, with a faithful broker that fills each leg's SL/TP the way MT5
does -- a long closes on the BID reaching its bracket, a short on the ASK. So the engine
and the model in STRADDLE_LADDER_STRATEGY.md are held to each other; if they disagree, the
engine has drifted from what was validated -- fix the engine, not the doc.

── What it measures, and the honest expectation ──

It arms the engine at an ARBITRARY level (the mid at a random tick) and replays the whole
run to completion. That is deliberate: with no skill in picking the level, the opening
straddle nets ~0 and every continuation leg is 1:1 against a spread it must first cross, so
the aggregate is NEGATIVE -- about one spread per leg. That loss IS the finding: the engine
is a faithful executor, not an edge. The only thing that can beat the spread is the
operator's level, which cannot be backtested (same conclusion the ladder reached). If this
harness ever shows a random level turning a clear PROFIT, the engine no longer matches the
model and something is wrong.

── Data ──

  * A tick CSV exported from the box's tick logger (header
    `time_msc,iso_time,bid,ask,last,volume,flags,spread`) -- pass its path as argv[1].
    This is the real-data path: pull a CSV off EC2 and replay against it.
  * No CSV + a running MT5 terminal -> a live `copy_ticks_range` pull.
  * Neither -> a deterministic synthetic random walk, so the harness (and its self-check)
    runs offline with no terminal and no file.

Run:
    cd XauOrderPad
    .\.venv\Scripts\python.exe ..\analysis\sladder_replay.py  [path\to\ticklog.csv]
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "XauOrderPad"))

from strategies.straddle_ladder import StraddleGridState   # noqa: E402  <- the code that trades

OZ = 100.0                                   # contract size: 1.00 lot = 100 oz
SYMBOL = "XAUUSDm"


# ---- tick sources -----------------------------------------------------------
def load_csv(path: str):
    """Read a box tick-log CSV (`time_msc,iso_time,bid,ask,...`) into arrays.

    Tolerant of extra columns and of a missing header (falls back to positional
    time_msc,iso,bid,ask). Rows with an unparseable bid/ask are skipped."""
    ms, bid, ask = [], [], []
    with open(path, newline="") as f:
        rdr = csv.reader(f)
        first = next(rdr, None)
        cols = {name: i for i, name in enumerate(first)} if first else {}
        has_header = "bid" in cols and "ask" in cols
        ib = cols.get("bid", 2)
        ia = cols.get("ask", 3)
        it = cols.get("time_msc", 0)
        rows = ([] if has_header else [first]) + list(rdr)
    for r in rows:
        if not r or len(r) <= max(ib, ia):
            continue
        try:
            b, a = float(r[ib]), float(r[ia])
        except (ValueError, IndexError):
            continue
        if a <= 0 or b <= 0 or a < b:
            continue
        try:
            t = int(r[it])
        except (ValueError, IndexError):
            t = len(ms)
        ms.append(t); bid.append(b); ask.append(a)
    if not bid:
        raise SystemExit(f"no usable ticks in {path}")
    return np.array(ms, "int64"), np.array(bid, float), np.array(ask, float)


def load_mt5():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()} (server stopped? terminal up?)")
    b = dt.datetime.now()
    a = b - dt.timedelta(hours=6)
    t = mt5.copy_ticks_range(SYMBOL, a, b, mt5.COPY_TICKS_ALL)
    mt5.shutdown()
    if t is None or len(t) == 0:
        raise SystemExit("no ticks from MT5")
    return (t["time_msc"].astype("int64"), t["bid"].astype(float), t["ask"].astype(float))


def synth(n=120_000, seed=7, spread=0.24, start=4000.0):
    """A deterministic random walk with a fixed spread -- offline fallback."""
    rng = np.random.default_rng(seed)
    mid = start + np.cumsum(rng.normal(0.0, 0.03, n))
    half = spread / 2.0
    ms = (np.arange(n, dtype="int64") * 200) + 1_700_000_000_000
    return ms, mid - half, mid + half


# ---- the replay: engine decisions + a broker that fills the brackets --------
def run_grid(ms, bid, ask, i0, *, sl, tp, gap, max_legs, max_lots=0.0, volume=0.01,
             always_straddle=False, horizon=15_000):
    """Arm the engine at the mid of tick i0 and replay one whole run.

    The broker mirrors MT5: a long's TP fills when the BID reaches it and its SL when the
    bid falls to it; a short is the mirror on the ASK. A leg realises exactly +tp (bracket
    hit in favour) or -sl (against) -- the spread is not added on top, it is already baked
    into the bracket geometry (the TP sits a spread FARTHER than the SL, so it is hit less
    often; that asymmetry is the cost). Returns a per-run summary in $/oz.

    `always_straddle=False` (default) drives the SINGLE-LEG trend continuation; True the walking
    straddle grid. Both open the first entry as a straddle.
    """
    level = (float(bid[i0]) + float(ask[i0])) / 2.0
    g = StraddleGridState(level, sl, tp, gap, max_legs, max_lots, volume, always_straddle)
    open_ids: set[int] = set()
    nxt = [1]
    realized = 0.0
    n_straddle = wins = losses = 0

    def new_ticket() -> int:
        nxt[0] += 1
        return nxt[0]

    end = min(len(bid), i0 + horizon)
    for j in range(i0, end):
        b, a = float(bid[j]), float(ask[j])

        # 1) broker fills: close and SCORE any leg whose bracket the price reached.
        for t in list(g.legs):
            if t not in open_ids:
                continue
            leg = g.legs[t]
            hit = None
            if leg["side"] == "buy":
                if b >= leg["tp_px"]:
                    hit = "tp"
                elif b <= leg["sl_px"]:
                    hit = "sl"
            else:
                if a <= leg["tp_px"]:
                    hit = "tp"
                elif a >= leg["sl_px"]:
                    hit = "sl"
            if hit:
                realized += tp if hit == "tp" else -sl
                wins += hit == "tp"
                losses += hit == "sl"
                open_ids.discard(t)

        # 2) engine decisions on the surviving open set.
        for act in g.on_tick(b, a, set(open_ids)):
            if act[0] == "straddle":
                bt, sk = new_ticket(), new_ticket()
                g.register_straddle(bt, a, sk, b)      # buy fills @ ask, sell @ bid
                open_ids.add(bt); open_ids.add(sk)
                n_straddle += 1
            elif act[0] == "place":                    # single-leg continuation order
                tk = new_ticket()
                g.register_cont(tk, a if act[1] == "buy" else b)
                open_ids.add(tk)
                n_straddle += 1
            elif act[0] == "close":                    # engine flattens the loser at market
                t = int(act[1])
                if t in open_ids and t in g.legs:
                    leg = g.legs[t]
                    exit_px = b if leg["side"] == "buy" else a
                    realized += (exit_px - leg["entry"]) if leg["side"] == "buy" \
                        else (leg["entry"] - exit_px)
                    open_ids.discard(t)

        if g.phase == "parked":
            break

    return {"realized": realized, "straddles": n_straddle, "wins": wins, "losses": losses}


def sweep(ms, bid, ask, *, sl, tp, gap, max_legs, n=300, seed=7):
    rng = np.random.default_rng(seed)
    # Adapt to the data size: a few minutes of live box ticks is far smaller than the synthetic set,
    # so reserve a fraction (capped at 15k) for each run's horizon rather than a fixed 45k.
    reserve = min(15_000, max(200, len(bid) // 3))
    span = max(1, len(bid) - reserve)
    n = min(n, span)
    starts = [int(x) for x in rng.choice(span, n, replace=False)]
    runs = [run_grid(ms, bid, ask, i, sl=sl, tp=tp, gap=gap, max_legs=max_legs, horizon=reserve)
            for i in starts]
    R = np.array([r["realized"] for r in runs])
    legs = np.array([r["straddles"] for r in runs])
    w = sum(r["wins"] for r in runs)
    l = sum(r["losses"] for r in runs)
    return R, legs, (w, l)


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path and os.path.exists(path):
        ms, bid, ask = load_csv(path)
        src = f"CSV {os.path.basename(path)}"
    else:
        try:
            ms, bid, ask = load_mt5()
            src = "MT5 live pull (last 6h)"
        except (ImportError, SystemExit):
            ms, bid, ask = synth()
            src = "SYNTHETIC random walk (no CSV, no MT5)"

    spread = float(np.median(ask - bid))
    print(f"{len(bid):,} ticks   source: {src}   median spread {spread:.3f}/oz")
    print()
    print("Arming at an ARBITRARY level (no skill). Each row is one (sl, tp, gap, max_legs) set,")
    print("aggregated over random arm points. avg/oz is the whole-run mean; @1lot = x100.")
    print()
    print(f"{'sl':>5} {'tp':>5} {'gap':>5} {'maxL':>5} {'runs':>6} "
          f"{'avg/oz':>10} {'@1lot':>10} {'legwin%':>8} {'straddles':>10}")

    for sl, tp, gap, mx in [(2.0, 2.0, 1.0, 10), (2.0, 2.0, 0.0, 10),
                            (6.0, 6.0, 1.0, 10), (2.0, 4.0, 1.0, 10)]:
        R, legs, (w, l) = sweep(ms, bid, ask, sl=sl, tp=tp, gap=gap, max_legs=mx)
        winpct = (100.0 * w / (w + l)) if (w + l) else 0.0
        print(f"{sl:>5.1f} {tp:>5.1f} {gap:>5.1f} {mx:>5} {len(R):>6} "
              f"{R.mean():>+10.3f} {R.mean() * OZ:>+10.2f} {winpct:>7.1f}% {legs.mean():>9.1f}")

    print()
    print("Expected: NEGATIVE at a random level -- about one spread per leg -- and no worse than")
    print("that. The straddle only detects direction; the continuation legs are 1:1 and pay the")
    print("spread. The edge is the operator's LEVEL, which cannot be backtested. If a random level")
    print("goes clearly POSITIVE here, the engine has drifted from the model -- investigate.")

    # A crude offline self-check so `python sladder_replay.py` on synthetic data still asserts
    # something: at a random level the mean must not be strongly positive.
    R, _, _ = sweep(ms, bid, ask, sl=2.0, tp=2.0, gap=1.0, max_legs=10)
    if R.mean() > 0.5:
        print()
        print(f"!! SELF-CHECK FAILED: random-level mean {R.mean():+.3f}/oz is implausibly positive.")
        sys.exit(1)


if __name__ == "__main__":
    main()

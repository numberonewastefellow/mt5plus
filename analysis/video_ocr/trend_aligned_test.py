r"""Test the position machinery in ISOLATION from the direction call.

    ..\..\XauOrderPad\.venv\Scripts\python.exe trend_aligned_test.py --csv <ticks.csv> --out <dir>

── Why this exists ──

This is a GUIDED strategy. A human watches the chart, calls buy or sell, and the algorithm's job
starts AFTER that: how many positions, what lots, when to add, when to exit. Direction is an input.

Every earlier replay tested the wrong thing. `GridState` opens its first basket on the FIRST TICK
of the window -- an arbitrary moment, often mid-drawdown -- then re-enters continuously. Those
results conflate the direction call, the entry timing, and the machinery. Only the machinery was
built here.

Run 4 is the clearest example: Sep 2 rose $63.83, but it fell $42.46 FIRST. A buy grid opened at
00:00 walked into that fall, exhausted its depth, parked, and sat out the entire $115 rally --
scoring -45.5%. No human would have called "buy" at 00:00 while price was dropping. The test
punished the algorithm for a decision the algorithm never makes.

── What this does instead ──

Build 5-minute candles, find runs of consecutive same-direction candles, and open exactly ONE cycle
at each run, in the run's direction (`auto_reenter=False`). Two entry timings bracket the answer:

  perfect   -- enter on the run's first tick. The human calls the turn exactly. Hindsight-optimistic.
  confirmed -- enter after ONE completed candle of the run. Realistic: you see a candle, then call.

The gap between them is the value of the direction call, separated from the machinery.

Reports the two claims the operator makes -- a cycle lasts under a minute, and holds 10+ positions
-- as measurements rather than assumptions, plus a single-position benchmark the grid must beat.

Analysis only. Reads ticks, writes a CSV. No MT5, no orders.
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics as st
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grid_replay import load_csv                      # noqa: E402
from grid_state import CONTRACT, GridState, PARKED    # noqa: E402
from lot_position_engine import Engine                # noqa: E402


def candles(ms, mid, minutes: int = 5):
    """Bucket ticks into fixed-width candles. Returns (start_idx, end_idx, open, close)."""
    b = (ms // (minutes * 60_000)).astype(np.int64)
    edges = np.flatnonzero(np.diff(b)) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(mid)]))
    return starts, ends, mid[starts], mid[ends - 1]


def trend_runs(o, c, min_candles: int = 2):
    """Runs of consecutive same-direction candles. Returns (dir, first_candle, last_candle)."""
    up = c > o
    runs, cur, start = [], up[0], 0
    for i in range(1, len(up)):
        if up[i] != cur:
            if i - start >= min_candles:
                runs.append(("buy" if cur else "sell", start, i))
            cur, start = up[i], i
    if len(up) - start >= min_candles:
        runs.append(("buy" if cur else "sell", start, len(up)))
    return runs


def one_cycle(ms, bid, ask, engine, direction, balance, meta, i0, i_end):
    """Open exactly ONE basket at tick i0 and run it to close or park.

    Mirrors grid_replay.replay's broker -- a buy fills at the ask and closes on the bid, a sell the
    mirror -- but opens a single cycle so the machinery can be judged on its own.
    """
    g = GridState(engine, direction, balance, auto_reenter=False)
    vol_min, vol_step = meta["vol_min"], meta["vol_step"]
    open_ids, nxt = set(), [0]
    closed = None
    for j in range(i0, i_end):
        b, a = float(bid[j]), float(ask[j])
        for act in g.on_tick(b, a, set(open_ids)):
            if act[0] == "open":
                _, side, lot, count = act
                lot = round(np.floor(lot / vol_step + 1e-9) * vol_step, 2)
                if lot < vol_min - 1e-9:
                    g.phase = PARKED
                    g.park_reason = f"lot {lot} below minimum"
                    break
                fill = a if side == "buy" else b
                tickets = []
                for _ in range(count):
                    nxt[0] += 1
                    tickets.append(nxt[0])
                    open_ids.add(nxt[0])
                if g.cycle and g.cycle.rungs == 0:
                    g.cycle.t_open_ms, g.cycle.i_open = int(ms[j]), j
                g.register_entries(tickets, fill, lot)
            elif act[0] == "close_all":
                c = g.cycle
                realised = g.basket_pnl(b, a)
                closed = {
                    "positions": c.positions, "rungs": c.rungs, "unit_lot": c.unit_lot,
                    "realised": round(realised, 2),
                    "worst_pnl": round(c.worst_pnl, 2),
                    "worst_pct_of_balance": round(c.worst_pct_of_balance, 1),
                    "duration_s": round((int(ms[j]) - c.t_open_ms) / 1000.0, 1),
                    "ticks": j - c.i_open, "reason": act[1],
                }
                open_ids.clear()
                g.register_flat(balance + realised)
        # Only a CLOSE ends the run. A parked basket keeps being marked to the horizon: parking
        # stops the engine from acting, not the market from moving against it. Breaking here (an
        # earlier version did) froze the drawdown at the moment of parking and understated it --
        # one basket read 43% while actually sitting at -1004% of balance.
        if closed:
            break
    if closed:
        return closed
    c = g.cycle
    if c is None or not g.positions:
        return {"positions": 0, "rungs": 0, "unit_lot": 0, "realised": 0.0, "worst_pnl": 0.0,
                "worst_pct_of_balance": 0.0, "duration_s": 0.0, "ticks": 0,
                "reason": g.refused or "never opened"}
    j = min(i_end, len(bid)) - 1
    return {
        "positions": c.positions, "rungs": c.rungs, "unit_lot": c.unit_lot,
        "realised": round(g.basket_pnl(float(bid[j]), float(ask[j])), 2),
        "worst_pnl": round(c.worst_pnl, 2),
        "worst_pct_of_balance": round(c.worst_pct_of_balance, 1),
        "duration_s": round((int(ms[j]) - c.t_open_ms) / 1000.0, 1),
        "ticks": j - c.i_open, "reason": "PARKED (never closed)",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--min-candles", type=int, default=2)
    a = ap.parse_args()

    ms, bid, ask, meta = load_csv(a.csv)
    mid = (bid + ask) / 2.0
    cs, ce, co, cc = candles(ms, mid, 5)
    runs = trend_runs(co, cc, a.min_candles)
    eng = Engine()
    print(f"{len(bid):,} ticks   {len(co)} five-minute candles   "
          f"{len(runs)} trend runs of >= {a.min_candles} candles\n")
    print("Each run opens exactly ONE cycle in the run's direction. auto_reenter=False.")
    print("  perfect   = enter on the run's first tick (human calls the turn exactly)")
    print("  confirmed = enter after ONE completed candle (realistic)\n")

    # HORIZON, not the run's end. Measuring to the end of a completed trend run is LOOKAHEAD: the
    # run's length is only knowable afterwards, so price has moved favourably over that span BY
    # CONSTRUCTION and every cycle "wins". An earlier version of this script did exactly that and
    # produced a 100% win rate -- an artifact of choosing the finish line, not a result.
    #
    # Instead each cycle gets a FIXED wall-clock horizon from its entry and must close itself
    # inside it. Whatever is still open at the horizon is marked to market, exactly as a parked
    # basket would be.
    HORIZON_MS = 30 * 60_000        # 30 minutes -- ~30x the operator's stated cycle length
    rows = []
    for arm in ("perfect", "confirmed"):
        for dirn, k0, k1 in runs:
            entry_candle = k0 if arm == "perfect" else min(k0 + 1, k1 - 1)
            i0 = int(cs[entry_candle])
            i_end = int(np.searchsorted(ms, ms[i0] + HORIZON_MS, side="right"))
            i_end = min(i_end, len(bid))
            if i_end - i0 < 50:
                continue
            r = one_cycle(ms, bid, ask, eng, dirn, a.balance, meta, i0, i_end)
            move = (ask[i_end - 1] - bid[i0]) if dirn == "buy" else (bid[i0] - ask[i_end - 1])
            bench = float(move) * eng.lot_for(a.balance) * CONTRACT
            rows.append({"arm": arm, "direction": dirn, "run_candles": k1 - k0,
                         "entry_candle": entry_candle, "balance": a.balance, **r,
                         "single_position_pnl": round(bench, 2),
                         "grid_beat_single": "yes" if r["realised"] > bench else "no"})

    path = os.path.join(a.out, "trend_aligned_cycles.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print("Each cycle runs a FIXED 30-minute horizon from entry and must close itself inside it.")
    print("Anything still open at the horizon is marked to market, like a parked basket.\n")
    print(f"{'arm':>10} {'n':>4} {'CLOSED':>7} {'win%':>6} {'mean':>10} | {'PARKED':>7} "
          f"{'mean mk':>10} | {'all mean':>10} {'med dur':>9} {'med pos':>8} {'beat 1':>8}")
    for arm in ("perfect", "confirmed"):
        v = [r for r in rows if r["arm"] == arm]
        # closed vs parked reported SEPARATELY: a parked basket's P&L is unrealised and counting
        # it as a "win" flatters the result. Only closed cycles have a realised outcome.
        cl = [r for r in v if "PARKED" not in r["reason"] and r["positions"] > 0]
        pk = [r for r in v if "PARKED" in r["reason"]]
        wins = sum(1 for r in cl if r["realised"] > 0)
        dur = [r["duration_s"] for r in cl]
        pos = [r["positions"] for r in v if r["positions"] > 0]
        beat = sum(1 for r in v if r["grid_beat_single"] == "yes")
        print(f"{arm:>10} {len(v):>4} {len(cl):>7} "
              f"{100*wins/max(len(cl),1):>5.0f}% {st.mean([r['realised'] for r in cl]) if cl else 0:>+10.2f} | "
              f"{len(pk):>7} {st.mean([r['realised'] for r in pk]) if pk else 0:>+10.2f} | "
              f"{st.mean([r['realised'] for r in v]):>+10.2f} "
              f"{st.median(dur) if dur else 0:>8.1f}s {st.median(pos) if pos else 0:>8.0f} "
              f"{beat:>3}/{len(v)}")

    print("\nTHE OPERATOR'S TWO CLAIMS, measured:")
    for arm in ("perfect", "confirmed"):
        v = [r for r in rows if r["arm"] == arm and r["positions"] > 0]
        dur = [r["duration_s"] for r in v]
        pos = [r["positions"] for r in v]
        if not dur:
            continue
        u60 = sum(1 for x in dur if x <= 60)
        p10 = sum(1 for x in pos if x >= 10)
        print(f"  {arm:>10}: cycle under 60 s -> {u60}/{len(dur)} "
              f"({100*u60/len(dur):.0f}%), median {st.median(dur):.1f}s")
        print(f"  {'':>10}  10+ positions   -> {p10}/{len(pos)} "
              f"({100*p10/len(pos):.0f}%), median {st.median(pos):.0f}")
    print(f"\n-> trend_aligned_cycles.csv ({len(rows)} cycles) in {a.out}")


if __name__ == "__main__":
    main()

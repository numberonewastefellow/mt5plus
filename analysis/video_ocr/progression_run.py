r"""The full compounding progression: $1 through a whole day, lots and depth growing with balance.

    ..\..\XauOrderPad\.venv\Scripts\python.exe progression_run.py --csv <ticks.csv> --out <dir>

── What this tests ──

The original strategy as the operator describes it: start small, take a direction, run cycles,
close each in profit, and as the balance grows increase BOTH the lot per position AND the number
of positions per cycle. The video showed $0.30 -> $69,000 with the ladder walking 0.01 -> 6.88.

Both progressions are already in `lot_position_engine.Engine` -- `lot_for(balance)` snaps a power
law to the ladder, and `max_positions_for(balance)` derives depth from a risk budget. Verified:

    $10 -> 0.01 lot / depth 9      $1,000  -> 0.33 lot / depth 18
    $100 -> 0.10 lot / depth 9     $69,000 -> 3.99 lot / depth 48

What has never been done is running it FROM $1 THROUGH A DAY, compounding, to see whether the
progression actually materialises or whether the account dies first.

── The direction signal is an INPUT, and it is the thing most in doubt ──

This is a guided strategy: a human calls buy or sell. Three rules are compared on identical ticks,
because the operator's use case is news spikes and it is not obvious which timeframe suits them:

    burst-1m   top-5% volatility minutes, direction = that minute's net move   (the news case)
    candle-5m  runs of >=2 same-direction 5-minute candles                     (short trend)
    block-1h   each hour's net move                                            (slow human call)

Measured on 2026-09-02, direction does NOT persist after a volatility burst: 51% same-direction at
1 min, 36% at 2 min, 42% at 5 min, and 4 of the 6 biggest minutes reversed. That reproduces this
repo's standing harness finding -- velocity predicts the SIZE of the next move, not its direction.
The run below measures rather than assumes.

── The one rule that makes this honest ──

There is NO STOP LOSS. So the first basket whose drawdown passes 100% of balance ENDS THE RUN --
the account is gone and there is no cycle 12. Averaging that basket in with the winners, or
continuing past it, would describe a sequence nobody could have traded.

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


# ---------------------------------------------------------------- direction signals

def _buckets(ms, mid, minutes):
    b = (ms // (minutes * 60_000)).astype(np.int64)
    e = np.flatnonzero(np.diff(b)) + 1
    st_ = np.concatenate(([0], e))
    en = np.concatenate((e, [len(mid)]))
    return st_, en


def signals(ms, mid, rule: str):
    """Return [(i_entry, direction)] — where a human would call the side, and which way."""
    if rule == "burst-1m":
        s, e = _buckets(ms, mid, 1)
        rng = np.array([mid[a:b].max() - mid[a:b].min() for a, b in zip(s, e)])
        net = np.array([mid[b - 1] - mid[a] for a, b in zip(s, e)])
        thr = np.percentile(rng, 95)
        out = []
        for i in np.flatnonzero(rng >= thr):
            if net[i] == 0 or e[i] >= len(mid):
                continue
            out.append((int(e[i]), "buy" if net[i] > 0 else "sell"))
        return out
    if rule == "candle-5m":
        s, e = _buckets(ms, mid, 5)
        o, c = mid[s], mid[e - 1]
        up = c > o
        out, cur, start = [], up[0], 0
        for i in range(1, len(up)):
            if up[i] != cur:
                if i - start >= 2:
                    out.append((int(s[start]), "buy" if cur else "sell"))
                cur, start = up[i], i
        return out
    if rule == "block-1h":
        s, e = _buckets(ms, mid, 60)
        return [(int(s[i]), "buy" if mid[e[i] - 1] > mid[s[i]] else "sell")
                for i in range(len(s))]
    raise ValueError(rule)


# ---------------------------------------------------------------- the compounding run

def run_day(ms, bid, ask, meta, rule: str, risk_pct: float, balance0: float = 1.0):
    """Compound cycles through the day, re-calling direction at each signal.

    Stops permanently the first time a basket's drawdown passes 100% of balance: with no stop
    loss, that account is gone.
    """
    eng = Engine(risk_pct=risk_pct)
    sig = signals(ms, (bid + ask) / 2.0, rule)
    vol_min, vol_step = meta["vol_min"], meta["vol_step"]
    balance = balance0
    rows, cyc = [], 0
    dead = None

    for k, (i0, dirn) in enumerate(sig):
        if dead:
            break
        i_end = sig[k + 1][0] if k + 1 < len(sig) else len(bid)
        g = GridState(eng, dirn, balance, auto_reenter=True)
        open_ids, nxt = set(), [0]
        for j in range(i0, i_end):
            b, a = float(bid[j]), float(ask[j])
            for act in g.on_tick(b, a, set(open_ids)):
                if act[0] == "open":
                    _, side, lot, count = act
                    lot = round(np.floor(lot / vol_step + 1e-9) * vol_step, 2)
                    if lot < vol_min - 1e-9:
                        g.phase = PARKED
                        g.park_reason = "lot below broker minimum"
                        break
                    fill = a if side == "buy" else b
                    tk = []
                    for _ in range(count):
                        nxt[0] += 1
                        tk.append(nxt[0])
                        open_ids.add(nxt[0])
                    if g.cycle and g.cycle.rungs == 0:
                        g.cycle.t_open_ms, g.cycle.i_open = int(ms[j]), j
                    g.register_entries(tk, fill, lot)
                elif act[0] == "close_all":
                    c = g.cycle
                    realised = g.basket_pnl(b, a)
                    cyc += 1
                    rows.append({
                        "rule": rule, "risk_pct": risk_pct, "cycle": cyc, "direction": dirn,
                        "balance_open": round(c.balance_open, 2), "lot": c.unit_lot,
                        "positions": c.positions, "depth_cap": c.depth_cap,
                        "duration_s": round((int(ms[j]) - c.t_open_ms) / 1000.0, 1),
                        "realised": round(realised, 2),
                        "worst_pct_of_balance": round(c.worst_pct_of_balance, 1),
                        "balance_after": round(c.balance_open + realised, 2),
                        "outcome": "closed",
                    })
                    open_ids.clear()
                    balance = c.balance_open + realised
                    g.register_flat(balance)
            # no stop loss: a basket past 100% of balance is an account that no longer exists
            if g.cycle is not None and g.positions and abs(g.cycle.worst_pct_of_balance) >= 100:
                c = g.cycle
                cyc += 1
                rows.append({
                    "rule": rule, "risk_pct": risk_pct, "cycle": cyc, "direction": dirn,
                    "balance_open": round(c.balance_open, 2), "lot": c.unit_lot,
                    "positions": c.positions, "depth_cap": c.depth_cap,
                    "duration_s": round((int(ms[j]) - c.t_open_ms) / 1000.0, 1),
                    "realised": round(g.basket_pnl(b, a), 2),
                    "worst_pct_of_balance": round(c.worst_pct_of_balance, 1),
                    "balance_after": 0.0, "outcome": "ACCOUNT GONE",
                })
                dead = f"cycle {cyc}: basket reached {c.worst_pct_of_balance:.0f}% of balance"
                balance = 0.0
                break
    return rows, balance, dead


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--balance", type=float, default=1.0)
    a = ap.parse_args()

    ms, bid, ask, meta = load_csv(a.csv)
    print(f"{len(bid):,} ticks   start ${a.balance:.2f}   "
          f"price {bid[0]:.2f} -> {bid[-1]:.2f} ({bid[-1]-bid[0]:+.2f})\n")

    allrows = []
    print(f"{'rule':>10} {'risk':>5} {'signals':>8} {'cycles':>7} {'final':>13} "
          f"{'peak':>13} {'lots used':>16}  ended")
    for rule in ("burst-1m", "candle-5m", "block-1h"):
        nsig = len(signals(ms, (bid + ask) / 2.0, rule))
        for risk in (60.0, 80.0, 100.0):
            rows, bal, dead = run_day(ms, bid, ask, meta, rule, risk, a.balance)
            allrows += rows
            peak = max([r["balance_after"] for r in rows], default=a.balance)
            lots = sorted({r["lot"] for r in rows})
            end = dead or ("ran to end" if rows else "never opened a cycle")
            print(f"{rule:>10} {risk:>4.0f}% {nsig:>8} {len(rows):>7} {bal:>13,.2f} "
                  f"{peak:>13,.2f} {','.join(f'{x:g}' for x in lots[:5]) or '-':>16}  {end}")

    if not allrows:
        print("\nNo cycles opened under any configuration.")
        return
    path = os.path.join(a.out, "progression_cycles.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(allrows[0].keys()))
        w.writeheader()
        w.writerows(allrows)

    print("\n=== DID THE PROGRESSION HAPPEN? (lot and positions vs balance) ===")
    best = max(("burst-1m", "candle-5m", "block-1h"),
               key=lambda r: max([x["balance_after"] for x in allrows if x["rule"] == r],
                                 default=0))
    v = [r for r in allrows if r["rule"] == best]
    if v:
        print(f"  best rule by peak balance: {best}")
        print(f"  {'cycle':>6} {'bal open':>11} {'lot':>6} {'pos':>5} {'dur':>7} "
              f"{'P&L':>11} {'bal after':>12}  outcome")
        for r in v[:18]:
            print(f"  {r['cycle']:>6} {r['balance_open']:>11,.2f} {r['lot']:>6.2f} "
                  f"{r['positions']:>5} {r['duration_s']:>6.0f}s {r['realised']:>+11,.2f} "
                  f"{r['balance_after']:>12,.2f}  {r['outcome']}")
        if len(v) > 18:
            print(f"  ... {len(v)-18} more")
    print(f"\n-> progression_cycles.csv ({len(allrows)} cycles) in {a.out}")


if __name__ == "__main__":
    main()

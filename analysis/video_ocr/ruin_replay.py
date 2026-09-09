"""Sequential, compounding replay with RUIN as an absorbing state.

WHY THIS EXISTS
---------------
Every parameter conclusion in this project up to 2026-09-09 was scored by SUMMING per-cycle P&L.
That is the wrong objective for a strategy that can reach zero, and this one reached zero three
times in two days.

A rule can total +5,904 across 73 cycles and still be the rule under which the account went
bankrupt on the way. Once balance hits zero the sequence ENDS - every later term in that sum is
unreachable, so the sum is not a payoff anybody could have collected. Positive expectancy per cycle
with negative terminal wealth is the martingale signature, and summing hides it perfectly.

So this harness answers the only question that matters:

    starting from a balance, running the cycles IN ORDER, sizing each one off the RUNNING
    balance, and stopping dead at zero - what is the terminal wealth, and did it survive?

METHOD, and its honest limits
-----------------------------
Cycle outcomes are taken from the tick replay at the position size the rule implies, then scaled to
the balance the sequence has actually reached. The scaling is the approximation: a differently sized
basket would have filled at different prices and might have exited at a different moment. It is
stated rather than hidden, and it is far closer to the truth than a flat sum.

`--bootstrap N` resamples the cycle order N times to estimate a ruin PROBABILITY rather than the
single historical path, because one ordering of 73 cycles is one sample, not a distribution.

Read-only: `copy_ticks_range` only, no order calls.

    python ruin_replay.py                       # current settings, historical order
    python ruin_replay.py --bootstrap 2000      # ruin probability over resampled orderings
    python ruin_replay.py --sweep giveback      # compare give-back rules on TERMINAL WEALTH
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import statistics
import sys

from replay_quick_arm import (GIVEBACK_PCT, OZ_PER_LOT, QUICK_ARM_PCT, attach_legs,
                              load_cycles, per_oz)

COMMISSION_PER_LOT = 11.0
LOG_DAYS = ("20260903", "20260904", "20260907", "20260908")


def simulate_cycle(c, ticks, target_pct, gb_mode, gb_val):
    """Replay one cycle at its HISTORICAL size. Returns (net, reason, ounces, balance_open).

    net is in account currency at the historical size; the caller rescales it to the running
    balance. Returns the cycle's real logged outcome when no arm fires before the basket was
    actually closed - otherwise the comparison silently drops exactly the disasters that matter.
    """
    tgt = target_pct / 100.0 * c.balance_open
    arm = -(QUICK_ARM_PCT / 100.0 * c.balance_open)
    n = 0
    oz = cost = best = worst = 0.0
    for tk in ticks:
        t = dt.datetime.fromtimestamp(tk["time_msc"] / 1000.0, dt.UTC)
        if t > c.end:
            break
        bid, ask = float(tk["bid"]), float(tk["ask"])
        if bid <= 0.0 or ask <= 0.0:
            continue
        while n < len(c.legs) and c.legs[n].t <= t:
            leg = c.legs[n]
            o = leg.lot * OZ_PER_LOT
            oz += o
            cost += o * leg.price
            n += 1
        if oz <= 0.0:
            continue
        avg = cost / oz
        px = bid if c.is_buy else ask
        net = oz * (px - avg) if c.is_buy else oz * (avg - px)
        best = max(best, net)
        worst = min(worst, net)
        if net <= 0.0:
            continue
        comm = (oz / 100.0) * COMMISSION_PER_LOT
        if net >= tgt:
            return net - comm, "target", oz, c.balance_open
        trig = (best - gb_val / 100.0 * c.balance_open) if gb_mode == "bal" \
            else best * (1.0 - gb_val / 100.0)
        if best > 0.0 and net <= trig:
            return net - comm, "giveback", oz, c.balance_open
        if worst <= arm:
            p = per_oz((t - c.start).total_seconds())
            if p > 0.0 and net >= p * oz:
                return net - comm, "quick", oz, c.balance_open
    return c.logged_net, "actual", (oz or 1.0), c.balance_open


def run_sequence(outcomes, start_balance, order=None):
    """Walk the cycles in order, compounding, with ruin absorbing.

    Each cycle's net is scaled by (running balance / the balance it historically ran at), because
    the EA sizes every cycle off the balance at the moment it opens.
    """
    idx = order if order is not None else range(len(outcomes))
    bal = start_balance
    peak = start_balance
    for i in idx:
        net, _reason, _oz, bal_hist = outcomes[i]
        if bal_hist <= 0:
            continue
        bal += net * (bal / bal_hist)          # scale-free: the rule is a % of balance
        peak = max(peak, bal)
        if bal <= 0.0:
            return 0.0, True, peak             # RUIN - the sequence stops here
    return bal, False, peak


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=28.9)
    ap.add_argument("--start", type=float, default=500.0)
    ap.add_argument("--bootstrap", type=int, default=0,
                    help="resample the cycle ORDER this many times for a ruin probability")
    ap.add_argument("--sweep", choices=["giveback", "target"],
                    help="compare variants on TERMINAL WEALTH instead of a sum")
    ap.add_argument("--symbol", default="XAUUSD")
    args = ap.parse_args()

    cycles = []
    for d in LOG_DAYS:
        cs = load_cycles(d)
        attach_legs(d, cs)
        cycles.extend(cs)
    cycles = sorted([c for c in cycles if c.legs], key=lambda x: x.start)

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("mt5.initialize failed:", mt5.last_error())
        return 1
    data = []
    for c in cycles:
        tk = mt5.copy_ticks_range(args.symbol, c.start - dt.timedelta(seconds=2),
                                  c.end + dt.timedelta(seconds=2), mt5.COPY_TICKS_ALL)
        if tk is not None and len(tk):
            data.append((c, tk))
    mt5.shutdown()
    print(f"{len(data)} cycles with tick data, start balance {args.start:.2f}\n")

    if args.sweep == "giveback":
        variants = [("bal", 15.0, "15% balance (current)"), ("bal", 10.0, "10% balance"),
                    ("bal", 5.0, "5% balance"), ("peak", 50.0, "50% of peak"),
                    ("peak", 30.0, "30% of peak"), ("peak", 20.0, "20% of peak")]
    elif args.sweep == "target":
        variants = [("bal", GIVEBACK_PCT, f"target {p}%") for p in (28.9, 24, 21, 18, 15)]
    else:
        variants = [("bal", GIVEBACK_PCT, "current settings")]

    print(f"{'variant':>22} {'sum of nets':>12} {'TERMINAL':>10} {'ruined':>7}"
          + (f" {'P(ruin)':>8} {'median end':>11}" if args.bootstrap else ""))
    print("-" * (52 + (21 if args.bootstrap else 0)))

    for i, (mode, val, label) in enumerate(variants):
        tgt = args.target
        if args.sweep == "target":
            tgt = (28.9, 24, 21, 18, 15)[i]
        outcomes = [simulate_cycle(c, tk, tgt, mode, val) for c, tk in data]
        total = sum(o[0] for o in outcomes)
        end, ruined, _peak = run_sequence(outcomes, args.start)
        extra = ""
        if args.bootstrap:
            ends, ruins = [], 0
            rng = random.Random(12345)
            n = len(outcomes)
            for _ in range(args.bootstrap):
                order = [rng.randrange(n) for _ in range(n)]
                e, r, _ = run_sequence(outcomes, args.start, order)
                ends.append(e)
                ruins += 1 if r else 0
            extra = f" {ruins / args.bootstrap * 100:7.1f}% {statistics.median(ends):11.2f}"
        print(f"{label:>22} {total:12.2f} {end:10.2f} {'YES' if ruined else 'no':>7}{extra}")

    print("\nSUM OF NETS is the old, misleading number. TERMINAL is what the account actually ends")
    print("at when ruin stops the sequence. Where the two disagree, the sum is the one that lies.")
    if args.bootstrap:
        print("P(ruin) resamples the cycle ORDER - one historical ordering is a single sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

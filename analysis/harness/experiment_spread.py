r"""
Spread-sensitivity experiment: how much of the loss is just the spread?

Same tick MID path, same signals, same walk-forward -- only the spread changes.
Isolates the one variable the broker controls. If the bracket is still negative
at spread=0 (frictionless), the problem is the strategy/data, not the broker. If
it flips positive somewhere between Exness (~40-60pt) and Vantage (~30pt), then
BROKER CHOICE is the deciding factor.

    ..\..\XauOrderPad\.venv\Scripts\python.exe experiment_spread.py [start end]
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
import sys

import numpy as np

try:
    from .core import Ticks
    from .features import build_features
    from .backtest import summarize
    from .bracket import walk_forward_bracket
    from .tickdata import load_ticks
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from harness.core import Ticks
    from harness.features import build_features
    from harness.backtest import summarize
    from harness.bracket import walk_forward_bracket
    from harness.tickdata import load_ticks


def with_spread(ticks: Ticks, spread_pts: float) -> Ticks:
    """Rebuild bid/ask around the SAME mid with a fixed spread (points)."""
    half = spread_pts * ticks.point / 2.0
    mid = ticks.mid
    return dc.replace(ticks, bid=mid - half, ask=mid + half)


def main() -> int:
    end = dt.datetime.now()
    start = end - dt.timedelta(days=30)
    if len(sys.argv) >= 3:
        start = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
        end = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")

    print("=" * 78)
    print("SPREAD SENSITIVITY -- breakout bracket, same mid path, only spread varies")
    print(f"window: {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    print("=" * 78)
    ticks = load_ticks("XAUUSD", start, end)
    real_spread_pts = float(np.mean(ticks.spread) / ticks.point)
    print(f"  {ticks.n:,} ticks | real avg spread = {real_spread_pts:.0f} pts "
          f"(${np.mean(ticks.spread):.3f})")
    feats = build_features(ticks, base_spike_z=2.5)  # depends only on mid -> reuse for all spreads
    print(f"  {len(feats.spikes):,} spikes\n")

    print(f"  {'spread(pts)':>12}{'trades':>8}{'win%':>7}{'exp/R':>9}{'PF':>6}{'totR':>9}{'$/1lot':>12}")
    print("  " + "-" * 62)
    for sp in (60, 30, 15, 0):
        f2 = dc.replace(feats, ticks=with_spread(ticks, sp))
        oos, _ = walk_forward_bracket(f2, train_days=10, test_days=3)
        s = summarize(oos)
        if not s:
            print(f"  {sp:>12}{'(no trades)':>34}")
            continue
        net = sum(t.R * t.risk * ticks.contract for t in oos)  # 1 lot
        print(f"  {sp:>12}{s['n']:>8}{s['win']:>7}{s['exp']:>+9.3f}{s['pf']:>6}"
              f"{s['totR']:>+9.1f}{net:>+12,.0f}")
    print("  " + "-" * 62)
    print("  Read: if exp/R is still negative at spread=0, the edge isn't there at any")
    print("  broker. If it crosses 0 near ~30pt, a tighter-spread broker is the difference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

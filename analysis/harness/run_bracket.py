r"""
Run the DIRECTION-AGNOSTIC breakout-bracket walk-forward on live ticks (read-only).

    ..\..\XauOrderPad\.venv\Scripts\python.exe run_bracket.py [start end]   (YYYY-MM-DD)

Same honest walk-forward as run.py, but the strategy predicts no direction --
it brackets each spike and lets whichever side breaks out fill. Tests the one
edge that survived: does a spike's MOVE SIZE pay for the width + spread + stop?

STOP the XauOrderPad server first (one MT5 connection per terminal). Places no order.
"""
from __future__ import annotations

import datetime as dt
import sys

import numpy as np

try:
    from .features import build_features
    from .backtest import summarize
    from .bracket import walk_forward_bracket
    from .tickdata import load_ticks
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from harness.features import build_features
    from harness.backtest import summarize
    from harness.bracket import walk_forward_bracket
    from harness.tickdata import load_ticks


def main() -> int:
    end = dt.datetime.now()
    start = end - dt.timedelta(days=30)
    if len(sys.argv) >= 3:
        start = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
        end = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")

    print("=" * 78)
    print("XAUUSD BREAKOUT-BRACKET WALK-FORWARD (direction-agnostic; no orders placed)")
    print(f"window: {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    print("NOTE: stop the XauOrderPad server first (one MT5 connection per terminal).")
    print("=" * 78)

    print("pulling ticks ...", flush=True)
    ticks = load_ticks("XAUUSD", start, end)
    days = (ticks.t_msc[-1] - ticks.t_msc[0]) / 1000.0 / 86400.0
    print(f"  {ticks.n:,} ticks over {days:.1f} days | avg spread={np.mean(ticks.spread):.3f} "
          f"({np.mean(ticks.spread) / ticks.point:.0f} pts)")

    print("building features ...", flush=True)
    feats = build_features(ticks, vel_win=20, vol_win=300, base_spike_z=2.5)
    print(f"  {len(feats.spikes):,} spike candidates (|vel_z| >= {feats.base_spike_z})")

    print("walk-forward (train 10d / test 3d, weekly re-fit) ...", flush=True)
    oos, weekly = walk_forward_bracket(feats, train_days=10, test_days=3)

    print(f"\n  {'window':8}{'chosen cfg (width_pts/Rmult)':30}{'trainExp':>9}{'#OOS':>6}{'testExp':>9}")
    for w in weekly:
        cfg = f"{w['cfg'][0]}/{w['cfg'][1]}R" if w["cfg"] else "(none)"
        print(f"  {w['week']:8}{cfg:30}{w['train_exp']:>9}{w['n']:>6}{w['test_exp']:>9}")

    s = summarize(oos)
    print("\n" + "-" * 78)
    if not s:
        print("OOS: no trades.")
        return 0
    # TP/SL/TIME breakdown -- for a bracket, how often did the move actually pay?
    n_tp = sum(1 for t in oos if t.reason == "TP")
    n_sl = sum(1 for t in oos if t.reason == "SL")
    n_time = sum(1 for t in oos if t.reason == "TIME")
    print(f"OOS RESULT: {s['n']} trades | win {s['win']}% | exp {s['exp']:+.3f}R | "
          f"PF {s['pf']} | totR {s['totR']:+.1f} | maxDD {s['maxDD_R']}R")
    print(f"exits: TP {n_tp} | SL {n_sl} | TIME {n_time}")
    for lot in (0.01, 0.1, 1.0):
        net = sum(t.R * t.risk * ticks.contract * lot for t in oos)
        print(f"    {lot}lot -> ${net:,.2f}")
    print("-" * 78)
    verdict = ("looks positive OOS -- verify on a longer/again span before trusting"
               if s["exp"] > 0 and s["n"] >= 50 else
               "no durable edge -- the move size does not pay for width + spread + stop")
    print(f"VERDICT: {verdict}")
    print("Reminder: any next step is human-gated -- a suggestion you tap, never an auto-order.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

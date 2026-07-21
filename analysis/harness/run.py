r"""
Run the tick-momentum walk-forward against the LIVE MT5 feed (read-only).

    ..\..\XauOrderPad\.venv\Scripts\python.exe run.py [start end]   (YYYY-MM-DD)

Defaults to the last 30 days. STOP the XauOrderPad server first -- MT5 allows one
clean connection per terminal, and a second attach clashes (-10004).

This prints the HONEST out-of-sample result: does follow/fade survive on ticks,
or die like the bar-level version did (-16% OOS)? It shows the numbers either
way. It never places an order.
"""
from __future__ import annotations

import datetime as dt
import sys

import numpy as np

try:
    from .features import build_features
    from .backtest import walk_forward, summarize
    from .tickdata import load_ticks
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from harness.features import build_features
    from harness.backtest import walk_forward, summarize
    from harness.tickdata import load_ticks


def main() -> int:
    end = dt.datetime.now()
    start = end - dt.timedelta(days=30)
    if len(sys.argv) >= 3:
        start = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
        end = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")

    print("=" * 78)
    print("XAUUSD TICK-MOMENTUM WALK-FORWARD (research only -- no orders placed)")
    print(f"window: {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    print("NOTE: stop the XauOrderPad server first (one MT5 connection per terminal).")
    print("=" * 78)

    print("pulling ticks ...", flush=True)
    ticks = load_ticks("XAUUSD", start, end)
    days = (ticks.t_msc[-1] - ticks.t_msc[0]) / 1000.0 / 86400.0
    print(f"  {ticks.n:,} ticks over {days:.1f} days | symbol={ticks.symbol} "
          f"point={ticks.point} contract={ticks.contract:.0f}oz/lot "
          f"avg spread={np.mean(ticks.spread):.3f}")

    print("building features ...", flush=True)
    feats = build_features(ticks, vel_win=20, vol_win=300, base_spike_z=2.5)
    print(f"  {len(feats.spikes):,} spike candidates (|vel_z| >= {feats.base_spike_z})")

    print("walk-forward (train 10d / test 3d, weekly re-fit) ...", flush=True)
    oos, weekly = walk_forward(feats, train_days=10, test_days=3)

    print(f"\n  {'window':8}{'chosen cfg (mode/spikeZ/SLpts/Rmult)':40}{'trainExp':>9}{'#OOS':>6}{'testExp':>9}")
    for w in weekly:
        cfg = f"{w['cfg'][0]}/{w['cfg'][1]}/{w['cfg'][2]}/{w['cfg'][3]}R" if w["cfg"] else "(none)"
        print(f"  {w['week']:8}{cfg:40}{w['train_exp']:>9}{w['n']:>6}{w['test_exp']:>9}")

    s = summarize(oos)
    print("\n" + "-" * 78)
    if not s:
        print("OOS: no trades (thresholds too strict or too little data).")
        return 0
    flips = sum(1 for a, b in zip(weekly, weekly[1:])
                if a["cfg"] and b["cfg"] and a["cfg"][0] != b["cfg"][0])
    print(f"OOS RESULT: {s['n']} trades | win {s['win']}% | exp {s['exp']:+.3f}R | "
          f"PF {s['pf']} | totR {s['totR']:+.1f} | maxDD {s['maxDD_R']}R")
    print(f"direction flips window-to-window: {flips}/{max(0, len(weekly)-1)} "
          f"(high = whipsaw / no persistent regime)")
    # per-lot $ (risk in price * contract * lot), spread already inside the fills
    for lot in (0.01, 0.1, 1.0):
        net = sum(t.R * t.risk * ticks.contract * lot for t in oos)
        print(f"    {lot}lot -> ${net:,.2f}")
    print("-" * 78)
    verdict = ("looks positive OOS -- verify with a longer span before trusting it"
               if s["exp"] > 0 and s["n"] >= 50 else
               "no durable edge (as expected -- direction is the hard part on this feed)")
    print(f"VERDICT: {verdict}")
    print("Reminder: any next step is human-gated -- a suggestion you tap, never an auto-order.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

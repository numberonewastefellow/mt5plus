r"""
Session filter + true all-in cost -- the realistic verdict.

Step 1: restrict spikes to the busiest sessions (does the gross edge lift?).
Step 2: on the session-filtered strategy, test realistic broker cost scenarios,
        where all-in cost = spread (in the fills) + commission + slippage.

    ..\..\XauOrderPad\.venv\Scripts\python.exe experiment_allin.py [start end]
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
    from .sessions import restrict_to_active_sessions
    from .tickdata import load_ticks
except ImportError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from harness.core import Ticks
    from harness.features import build_features
    from harness.backtest import summarize
    from harness.bracket import walk_forward_bracket
    from harness.sessions import restrict_to_active_sessions
    from harness.tickdata import load_ticks


def with_spread(ticks: Ticks, spread_pts: float) -> Ticks:
    half = spread_pts * ticks.point / 2.0
    mid = ticks.mid
    return dc.replace(ticks, bid=mid - half, ask=mid + half)


def _run(feats, spread_pts, extra_pts, contract):
    f = dc.replace(feats, ticks=with_spread(feats.ticks, spread_pts))
    oos, _ = walk_forward_bracket(f, train_days=10, test_days=3, extra_cost_pts=extra_pts)
    s = summarize(oos)
    if not s:
        return None
    s["net1lot"] = sum(t.R * t.risk * contract for t in oos)
    return s


def main() -> int:
    end = dt.datetime.now()
    start = end - dt.timedelta(days=30)
    if len(sys.argv) >= 3:
        start = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
        end = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")

    print("=" * 82)
    print("SESSION FILTER + TRUE ALL-IN COST  (breakout bracket)")
    print(f"window: {start:%Y-%m-%d} -> {end:%Y-%m-%d}")
    print("=" * 82)
    ticks = load_ticks("XAUUSD", start, end)
    contract = ticks.contract
    feats_all = build_features(ticks, base_spike_z=2.5)
    feats_sess, active = restrict_to_active_sessions(feats_all, top_k=8)
    print(f"  {ticks.n:,} ticks | spikes: all={len(feats_all.spikes):,} "
          f"session-only={len(feats_sess.spikes):,}")
    print(f"  active hours (busiest 8, UTC-ish): {active}\n")

    # ---- Step 1: does the session filter lift the GROSS edge? (spread 0, no cost) ----
    print("STEP 1 -- gross edge at ZERO cost (is the signal better in-session?)")
    print(f"  {'spikes':>16}{'trades':>8}{'win%':>7}{'exp/R':>9}{'PF':>6}{'totR':>9}")
    g_all = _run(feats_all, 0.0, 0.0, contract)
    g_ses = _run(feats_sess, 0.0, 0.0, contract)
    for label, s in (("all-day", g_all), ("session-only", g_ses)):
        if s:
            print(f"  {label:>16}{s['n']:>8}{s['win']:>7}{s['exp']:>+9.3f}{s['pf']:>6}{s['totR']:>+9.1f}")
    print()

    # ---- Step 2: realistic all-in cost on the SESSION-filtered strategy ----
    print("STEP 2 -- session-filtered, realistic broker cost (all-in = spread + comm + slip)")
    print(f"  {'scenario':>26}{'sprd':>5}{'+comm/slip':>11}{'exp/R':>9}{'PF':>6}{'$/1lot':>12}")
    scenarios = [
        ("Exness demo (measured)", 60, 5),      # ~65 all-in
        ("tight spread, NO comm",  30, 5),       # ~35 all-in
        ("tight spread + $6 comm", 30, 65),      # 30 + 60pt commission + 5 slip ~95 all-in
        ("low-cost ideal",         15, 3),       # ~18 all-in
        ("frictionless (ceiling)",  0, 0),
    ]
    for name, sp, extra in scenarios:
        s = _run(feats_sess, sp, extra, contract)
        if not s:
            print(f"  {name:>26}{sp:>5}{extra:>11}{'(no trades)':>27}")
            continue
        print(f"  {name:>26}{sp:>5}{extra:>11}{s['exp']:>+9.3f}{s['pf']:>6}{s['net1lot']:>+12,.0f}")
    print("-" * 82)
    print("  Verdict logic: a scenario with exp/R > 0 AND PF > 1 is where this could pay.")
    print("  Reminder: research only -- any live step stays human-gated (a suggestion you tap).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

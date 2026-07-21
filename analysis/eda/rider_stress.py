"""
Quiet-regime STRESS TEST (Phase 0) -- the untested downside.

Every month in the data was high-volatility, so the rider's trailing-exit
"volatility harvest" has never met a calm, rangebound market. This synthesizes
one: it compresses each bar's move by a factor q (q=1.0 normal, q=0.25 = a very
quiet tape) while keeping the bar structure, then re-runs the rider. Because the
regime gate is RELATIVE (ATR > its own rolling median), the rider STILL trades in
a quiet tape -- but the moves are too small to clear the spread + the $6 stops.
This quantifies how badly it bleeds when volatility dries up.

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe rider_stress.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import patternlib as P
from rider_state import RiderConfig, entry_signal


def quiet_bars(df: pd.DataFrame, q: float) -> pd.DataFrame:
    """Compress each bar's excursions from its open by q, chaining opens so the
    series stays continuous. ATR scales with q, so the relative gate still fires."""
    o, h, l, c = (df[k].to_numpy() for k in ("o", "h", "l", "c"))
    n = len(c)
    qo = np.empty(n); qc = np.empty(n); qh = np.empty(n); ql = np.empty(n)
    qo[0] = o[0]
    for i in range(n):
        base = qo[i] if i == 0 else qc[i - 1]
        qo[i] = base
        qh[i] = base + q * (h[i] - o[i])
        ql[i] = base + q * (l[i] - o[i])
        qc[i] = base + q * (c[i] - o[i])
    out = pd.DataFrame({"o": qo, "h": qh, "l": ql, "c": qc, "v": df["v"].to_numpy()},
                       index=df.index)
    out["body"] = out["c"] - out["o"]
    prevc = out["c"].shift(1)
    tr = pd.concat([out["h"] - out["l"], (out["h"] - prevc).abs(), (prevc - out["l"]).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14).mean()
    out["hour"] = out.index.hour
    return out


def main():
    df = P.load_bars("M5")
    cfg = RiderConfig(use_ny_hours=False)   # the chosen high-vol-gate config
    print("=" * 84)
    print("RIDER QUIET-REGIME STRESS TEST  (M5)  -- what happens when volatility dries up")
    print("=" * 84)
    print(f"  {'regime':26}{'avg |bar move|':>16}{'n':>6}{'win%':>7}{'exp$/oz':>10}{'PF':>6}{'tot$/oz':>10}")
    base_atr = df['atr'].mean()
    for q in (1.0, 0.6, 0.4, 0.25):
        d = df if q == 1.0 else quiet_bars(df, q)
        sig = entry_signal(d, cfg)
        pnl = P.backtest(d, sig, cfg.tp, cfg.sl, cfg.max_hold, cfg.trail)
        s = P.stats(pnl)
        tag = "NORMAL (as-is)" if q == 1.0 else f"quiet x{q}"
        print(f"  {tag:26}{d['atr'].mean():>16.2f}{s['n']:>6}{s['win']:>7}"
              f"{s['exp']:>10.3f}{s['pf']:>6}{s['tot']:>10.1f}")

    print("\n  Reading: as the tape quiets, the SAME rider keeps trading (relative gate) but")
    print("  expectancy collapses -- small moves can't pay the spread + the $6 stops. The")
    print("  live 'harvest' is REGIME-DEPENDENT: it needs volatility. A calm month bleeds.")
    print("\n  Mitigation options (for later): an ABSOLUTE volatility floor (skip when ATR")
    print("  below a $ threshold), fewer/no trades in calm tapes, and the human tap-gate")
    print("  (you simply don't confirm cards in a dead market).")
    print("\nDONE (quiet-regime stress).")


if __name__ == "__main__":
    main()

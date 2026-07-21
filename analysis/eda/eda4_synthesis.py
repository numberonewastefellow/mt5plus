"""
EDA module 4 -- SYNTHESIS. Combine the two real edges the EDA found:

  1. volatility/size is predictable (volume spike -> big move ~3x more likely)
  2. price reacts at S/R levels ~57-60% (a real directional bias vs 50% random)

Key reframe from the data: the 40-pt ($0.04) spread killed TICK scalping because
targets were ~spread-sized. On M15/H1 the moves are $5-50 -- the spread is <1%
of the trade. So a LEVEL-reaction swing trade is not spread-constrained.

Test (descriptive, in-sample -- a screen, NOT a validated backtest): fade strong
S/R levels (bet the bounce), with and without a volume-spike confirmation, net of
the 40-pt spread. If it shows promise, the next step is a proper walk-forward.

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe eda4_synthesis.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from eda_lib import load_bars

SPREAD = 0.04          # $ (40 pts) round-trip, charged once per trade
TF = "M15"


def strong_levels() -> np.ndarray:
    """Fractal swing highs/lows on H1, clustered to $5 buckets touched >=3x."""
    h1 = load_bars("H1")
    hh, ll = h1["high"].to_numpy(), h1["low"].to_numpy()
    w = 5
    sw = [hh[i] for i in range(w, len(hh) - w) if hh[i] == max(hh[i - w:i + w + 1])]
    sw += [ll[i] for i in range(w, len(ll) - w) if ll[i] == min(ll[i - w:i + w + 1])]
    buckets = np.round(np.array(sw) / 5.0) * 5.0
    vals, cnts = np.unique(buckets, return_counts=True)
    return vals[cnts >= 3]


def sim_fade(bars, levels, stop_d, r_mult, tol, max_hold, vol_filter):
    """Fade a level: touch from above -> long (support), from below -> short
    (resistance). Stop beyond the level, target = r_mult*stop. Spread charged.
    One position at a time. Returns list of R results (spread already inside)."""
    h, l, c = bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy()
    v = bars["volume"].to_numpy()
    vthr = np.nanpercentile(v, 90)
    lv = levels
    n = len(c)
    out = []
    busy = -1
    for i in range(1, n - 1):
        if i <= busy:
            continue
        if vol_filter and v[i] < vthr:
            continue
        near = lv[(lv >= l[i] - tol) & (lv <= h[i] + tol)]
        if not len(near):
            continue
        L = near[np.argmin(np.abs(near - c[i - 1]))]
        side = np.sign(c[i - 1] - L)          # +1 support (go long), -1 resistance (go short)
        if side == 0:
            continue
        dirn = int(side)
        entry = L + dirn * (SPREAD / 2)       # pay half-spread entering
        stop = L - dirn * stop_d
        tp = entry + dirn * r_mult * stop_d
        risk = stop_d
        R = None
        for k in range(i + 1, min(n, i + 1 + max_hold)):
            hit_sl = (l[k] <= stop) if dirn > 0 else (h[k] >= stop)
            hit_tp = (h[k] >= tp) if dirn > 0 else (l[k] <= tp)
            if hit_sl:
                R = -1.0 - (SPREAD / 2) / risk; busy = k; break
            if hit_tp:
                R = r_mult - (SPREAD / 2) / risk; busy = k; break
        if R is None:
            k = min(n - 1, i + max_hold)
            px = c[k]
            pl = (px - entry) if dirn > 0 else (entry - px)
            R = pl / risk - (SPREAD / 2) / risk; busy = k
        out.append(R)
    return np.array(out)


def summarize(R):
    if not len(R):
        return "no trades"
    wins = R[R > 0]
    pf = wins.sum() / abs(R[R <= 0].sum()) if (R <= 0).any() and R[R <= 0].sum() != 0 else float("inf")
    return (f"n={len(R):>5}  win={100*len(wins)/len(R):4.1f}%  exp={R.mean():+.3f}R  "
            f"PF={pf:.2f}  totR={R.sum():+.0f}")


def run():
    print("=" * 100)
    print("EDA 4 -- SYNTHESIS: does 'fade S/R (+ volume)' beat random, net of spread?")
    print("  (IN-SAMPLE screen over all history -- promise check, NOT a validated backtest)")
    print("=" * 100)
    bars = load_bars(TF)
    lv = strong_levels()
    contract = bars.attrs["contract"]
    print(f"  TF={TF}  strong levels={len(lv)}  spread={SPREAD} ($/oz)  "
          f"1R at $3 stop = ${3*contract:.0f}/lot\n")

    print("[A] FADE strong S/R levels -- ALL touches vs VOLUME-CONFIRMED touches")
    for stop_d, r_mult in [(3.0, 1.0), (3.0, 2.0), (5.0, 1.0), (5.0, 2.0)]:
        for vf in (False, True):
            R = sim_fade(bars, lv, stop_d, r_mult, tol=2.0, max_hold=16, vol_filter=vf)
            tag = "vol-confirmed" if vf else "all touches  "
            net1 = R.sum() * stop_d * contract if len(R) else 0
            print(f"   stop=${stop_d:.0f} tgt={r_mult:.0f}R {tag}: {summarize(R)}  ${net1:+,.0f}/lot")

    print("\n[B] BREAK the level instead (momentum) -- contrast")
    # reuse sim by flipping: a 'break' = trade THROUGH the level (dirn reversed)
    h, l, c = bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy()
    v = bars["volume"].to_numpy(); vthr = np.nanpercentile(v, 90)
    n = len(c); out = []; busy = -1; stop_d, r_mult = 3.0, 2.0
    for i in range(1, n - 1):
        if i <= busy or v[i] < vthr:
            continue
        near = lv[(lv >= l[i] - 2.0) & (lv <= h[i] + 2.0)]
        if not len(near):
            continue
        L = near[np.argmin(np.abs(near - c[i - 1]))]
        side = np.sign(c[i - 1] - L)
        if side == 0:
            continue
        dirn = -int(side)                     # BREAK = go through the level
        entry = L + dirn * (SPREAD / 2)
        stop = entry - dirn * stop_d
        tp = entry + dirn * r_mult * stop_d
        R = None
        for k in range(i + 1, min(n, i + 1 + 16)):
            hit_sl = (l[k] <= stop) if dirn > 0 else (h[k] >= stop)
            hit_tp = (h[k] >= tp) if dirn > 0 else (l[k] <= tp)
            if hit_sl:
                R = -1.0 - (SPREAD/2)/stop_d; busy = k; break
            if hit_tp:
                R = r_mult - (SPREAD/2)/stop_d; busy = k; break
        if R is None:
            k = min(n-1, i+16); pl = (c[k]-entry) if dirn>0 else (entry-c[k])
            R = pl/stop_d - (SPREAD/2)/stop_d; busy = k
        out.append(R)
    print(f"   break vol-confirmed stop=$3 tgt=2R: {summarize(np.array(out))}")
    print("\n  NOTE: in-sample only. A positive exp here => run it through a proper")
    print("  walk-forward next; a negative => discard. This is a screen, not a verdict.")
    print("\nDONE (module 4)")


if __name__ == "__main__":
    run()

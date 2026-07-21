"""
EDA module 3 -- pivots + support/resistance + volume profile.

Direction is a random walk (module 1), so the ONLY place a directional edge
could hide is CONTEXT: does price actually react at structural levels? We test
each level type by its reaction rate -- when price touches a level, does it
reverse (S/R works) more than the 50% a coin would give?

  * daily floor-trader pivots (PP/R1/R2/S1/S2) -- reaction on M15
  * round numbers ($50 and $100 grid) -- magnet/reaction
  * swing-high/low levels (fractals on H1) -- historical S/R, clustered
  * volume profile POC/VAH/VAL (last 60 days, M15) -- high-volume nodes
  * moving averages (D1 50/200) -- dynamic S/R + where price sits now

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe eda3_levels.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from eda_lib import load_bars


def reaction_rate(bars: pd.DataFrame, levels: np.ndarray, tol: float, k: int = 4):
    """For each bar whose [low,high] straddles a level (a 'touch'), was price k
    bars later back on the approach side (bounce) or through it (break)?

    Returns (n_touches, bounce_rate). bounce_rate ~0.5 = level is meaningless;
    >0.55 = genuine reaction. Approach side = sign(close_before - level).
    """
    h, l, c = bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy()
    lv = np.asarray(levels, dtype=float)
    lv = lv[np.isfinite(lv)]
    n = len(c)
    touches = bounces = 0
    for i in range(1, n - k):
        approach = c[i - 1]
        # nearest level this bar straddles (within tol)
        near = lv[(lv >= l[i] - tol) & (lv <= h[i] + tol)]
        if not len(near):
            continue
        L = near[np.argmin(np.abs(near - approach))]
        side = np.sign(approach - L)          # +1 approached from above (support), -1 from below (resistance)
        if side == 0:
            continue
        after = c[i + k]
        touches += 1
        if np.sign(after - L) == side:        # stayed on approach side -> bounced/held
            bounces += 1
    return touches, (bounces / touches if touches else np.nan)


def run():
    print("=" * 100)
    print("EDA 3 -- PIVOTS + SUPPORT/RESISTANCE + VOLUME PROFILE  (XAUUSD)")
    print("=" * 100)
    d1 = load_bars("D1")
    m15 = load_bars("M15")
    tol = 3.0  # $ tolerance for a 'touch' (~ a few spreads on gold)

    print("\n[A] DAILY PIVOTS -- reaction rate on M15 (bounce>0.55 = real S/R)")
    # prior-day pivots
    pp = (d1["high"] + d1["low"] + d1["close"]) / 3.0
    piv = pd.DataFrame({
        "PP": pp, "R1": 2 * pp - d1["low"], "S1": 2 * pp - d1["high"],
        "R2": pp + (d1["high"] - d1["low"]), "S2": pp - (d1["high"] - d1["low"]),
    }).shift(1)  # today uses YESTERDAY's pivots (no lookahead)
    piv["date"] = piv.index.normalize()
    m15d = m15.copy()
    m15d["date"] = m15d.index.normalize()
    for col in ["PP", "R1", "S1", "R2", "S2"]:
        lut = piv.set_index("date")[col]
        touches = bounces = 0
        for day, grp in m15d.groupby("date"):
            if day not in lut.index or not np.isfinite(lut.loc[day]):
                continue
            t, r = reaction_rate(grp, np.array([lut.loc[day]]), tol, k=4)
            touches += t
            if np.isfinite(r):
                bounces += r * t
        rate = bounces / touches if touches else np.nan
        print(f"   {col:3}: touches={touches:>5}  bounce_rate={rate:.3f}")

    print("\n[B] ROUND NUMBERS -- reaction rate on M15")
    lo, hi = m15["low"].min(), m15["high"].max()
    for grid in (50.0, 100.0):
        levels = np.arange(np.floor(lo / grid) * grid, hi + grid, grid)
        t, r = reaction_rate(m15, levels, tol, k=4)
        print(f"   ${int(grid)} grid ({len(levels)} levels): touches={t:>6}  bounce_rate={r:.3f}")

    print("\n[C] SWING-LEVEL S/R -- fractal swings on H1, reaction on M15")
    h1 = load_bars("H1")
    hh, ll = h1["high"].to_numpy(), h1["low"].to_numpy()
    w = 5  # swing = extreme vs +-5 bars
    sw_hi = [hh[i] for i in range(w, len(hh) - w) if hh[i] == max(hh[i - w:i + w + 1])]
    sw_lo = [ll[i] for i in range(w, len(ll) - w) if ll[i] == min(ll[i - w:i + w + 1])]
    swings = np.array(sw_hi + sw_lo)
    # cluster: round to $5 buckets, keep buckets touched >=3 times (strong levels)
    buckets = np.round(swings / 5.0) * 5.0
    vals, cnts = np.unique(buckets, return_counts=True)
    strong = vals[cnts >= 3]
    t, r = reaction_rate(m15, strong, tol, k=4)
    print(f"   {len(swings)} raw swings -> {len(strong)} strong levels (>=3 touches, $5 buckets)")
    print(f"   reaction on M15: touches={t}  bounce_rate={r:.3f}")

    print("\n[D] VOLUME PROFILE (M15, last 60 days) -- POC / value area")
    recent = m15.last("60D")
    price = ((recent["high"] + recent["low"] + recent["close"]) / 3).to_numpy()
    vol = recent["volume"].to_numpy()
    edges = np.linspace(price.min(), price.max(), 121)
    hist, _ = np.histogram(price, bins=edges, weights=vol)
    centers = (edges[:-1] + edges[1:]) / 2
    poc_i = int(np.argmax(hist))
    # 70% value area around POC
    order = np.argsort(hist)[::-1]
    cum = np.cumsum(hist[order]); tgt = 0.70 * hist.sum()
    va_bins = order[:np.searchsorted(cum, tgt) + 1]
    vah, val = centers[va_bins].max(), centers[va_bins].min()
    t, r = reaction_rate(m15.last("60D"), np.array([centers[poc_i], vah, val]), tol, k=4)
    print(f"   POC=${centers[poc_i]:.1f}  VAH=${vah:.1f}  VAL=${val:.1f}")
    print(f"   reaction at POC/VAH/VAL: touches={t}  bounce_rate={r:.3f}")

    print("\n[E] MOVING AVERAGES as dynamic S/R (D1)")
    for span in (50, 200):
        d1[f"ma{span}"] = d1["close"].rolling(span).mean()
    last = d1.iloc[-1]
    print(f"   price=${last['close']:.1f} | MA50=${last['ma50']:.1f} "
          f"({'above' if last['close']>last['ma50'] else 'below'}) | "
          f"MA200=${last['ma200']:.1f} ({'above' if last['close']>last['ma200'] else 'below'})")
    # reaction of D1 close to MA50 touch
    ma = d1["ma50"].to_numpy(); c = d1["close"].to_numpy()
    dist = (c - ma) / c
    cross = np.sum(np.abs(dist) < 0.005)  # within 0.5%
    print(f"   D1 bars within 0.5% of MA50: {cross} (dynamic level interactions)")
    print("\nDONE (module 3)")


if __name__ == "__main__":
    run()

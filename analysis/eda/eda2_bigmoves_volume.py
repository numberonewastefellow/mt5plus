"""
EDA module 2 -- big moves + volatility clustering + volume<->price.

The tradeable questions:
  * how often are "big moves", how big, and WHEN (hour/day) do they cluster?
  * does a big move follow a big move? (persistence you could position for)
  * volume vs |move|: correlation, and crucially does volume LEAD the move
    (predictive) or just coincide with it (useless for entry timing)?
  * after a volume spike: forward SIZE (magnitude) vs forward DIRECTION -- the
    prior finding was "size yes, direction no"; re-check it forward-looking.

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe eda2_bigmoves_volume.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from eda_lib import load_bars


def corr(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    return float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 5 else np.nan


def run():
    print("=" * 100)
    print("EDA 2 -- BIG MOVES + VOLATILITY CLUSTERING + VOLUME<->PRICE  (XAUUSD)")
    print("=" * 100)

    print("\n[A] BIG-MOVE FREQUENCY  (big = |close-to-close| in the top 10% per TF)")
    print(f"  {'TF':4}{'p90|ret|$':>11}{'#big':>8}{'%bars':>7}{'avgBig$':>9}{'max$':>9}")
    for tf in ["M5", "M15", "H1", "H4", "D1"]:
        df = load_bars(tf)
        a = df["ret"].abs().to_numpy()
        thr = np.nanpercentile(a, 90)
        big = a >= thr
        print(f"  {tf:4}{thr:>11.2f}{int(np.nansum(big)):>8}{100*np.nanmean(big):>7.1f}"
              f"{np.nanmean(a[big]):>9.2f}{np.nanmax(a):>9.2f}")

    print("\n[B] VOLATILITY PERSISTENCE  (does a big move follow a big move?)")
    print(f"  {'TF':4}{'base P(big)':>12}{'P(big|big)':>12}{'lift':>7}  (lift>1 = clustering)")
    for tf in ["M5", "M15", "H1"]:
        df = load_bars(tf)
        a = df["ret"].abs().to_numpy()
        thr = np.nanpercentile(a, 90)
        big = (a >= thr).astype(float)
        base = np.nanmean(big)
        prev_big = big[:-1] == 1
        cond = np.nanmean(big[1:][prev_big]) if prev_big.sum() else np.nan
        print(f"  {tf:4}{base:>12.3f}{cond:>12.3f}{cond/base:>7.2f}")

    print("\n[C] WHEN BIG MOVES HAPPEN  (M5, top-10% moves by hour-of-day, UTC-ish)")
    df = load_bars("M5")
    a = df["ret"].abs()
    big = a >= a.quantile(0.90)
    by_hr = df.assign(big=big).groupby("hour")["big"].mean().sort_values(ascending=False)
    base = big.mean()
    print("  hour : P(big move)  lift-vs-base   (top 6 hours)")
    for h, p in by_hr.head(6).items():
        print(f"   h{int(h):02d} : {p:8.3f}      {p/base:5.2f}x")
    dow = df.assign(big=big).groupby("dow")["big"].mean()
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print("  by weekday:", "  ".join(f"{names[int(d)]}={p/base:.2f}x" for d, p in dow.items()))

    print("\n[D] VOLUME <-> PRICE CORRELATION")
    print(f"  {'TF':4}{'corr(vol,|ret|)':>16}{'corr(vol,range)':>16}")
    for tf in ["M5", "M15", "H1", "H4", "D1"]:
        df = load_bars(tf)
        v = df["volume"].to_numpy()
        print(f"  {tf:4}{corr(v, df['ret'].abs().to_numpy()):>16.2f}{corr(v, df['range'].to_numpy()):>16.2f}")

    print("\n[E] DOES VOLUME *LEAD* THE MOVE?  (predictive) or just coincide? (useless)")
    print(f"  {'TF':4}{'vol~|ret|(now)':>15}{'vol~|ret|(+1)':>15}{'vol~sum|ret|(+3)':>18}")
    for tf in ["M5", "M15", "H1"]:
        df = load_bars(tf)
        v = df["volume"]
        ar = df["ret"].abs()
        now = corr(v.to_numpy(), ar.to_numpy())
        nxt = corr(v.to_numpy(), ar.shift(-1).to_numpy())
        fwd3 = corr(v.to_numpy(), ar.shift(-1).rolling(3).sum().shift(-2).to_numpy())
        print(f"  {tf:4}{now:>15.2f}{nxt:>15.2f}{fwd3:>18.2f}")
    print("  (if 'now' >> '+1', volume COINCIDES with the move -> no entry-timing edge.)")

    print("\n[F] VOLUME SPIKE -> FORWARD OUTCOME  (M5, spike = volume top 10%; next 3 bars)")
    df = load_bars("M5")
    v = df["volume"]
    spike = v >= v.quantile(0.90)
    fwd_signed = df["close"].shift(-3) - df["close"]        # direction
    fwd_abs = fwd_signed.abs()                               # size
    big_next = (df["ret"].abs().shift(-1).rolling(3).max().shift(-2)
                >= df["ret"].abs().quantile(0.90))
    def mean_at(mask, s):
        x = s[mask].to_numpy(); x = x[np.isfinite(x)]; return float(x.mean()) if len(x) else np.nan
    print(f"  forward 3-bar |move|:  spike={mean_at(spike,fwd_abs):.2f}$  "
          f"non-spike={mean_at(~spike,fwd_abs):.2f}$  "
          f"ratio={mean_at(spike,fwd_abs)/mean_at(~spike,fwd_abs):.2f}x   <- SIZE")
    print(f"  forward 3-bar signed:  spike={mean_at(spike,fwd_signed):+.3f}$  "
          f"non-spike={mean_at(~spike,fwd_signed):+.3f}$   <- DIRECTION (near 0 = no edge)")
    print(f"  P(big move in next 3): spike={mean_at(spike,big_next.astype(float)):.3f}  "
          f"base={float(big_next.mean()):.3f}  "
          f"lift={mean_at(spike,big_next.astype(float))/float(big_next.mean()):.2f}x")
    print("\nDONE (module 2)")


if __name__ == "__main__":
    run()

"""
EDA module 1 -- data audit + noise / random-walk structure.

Questions:
  * coverage & gaps per timeframe (is the data clean?)
  * return distribution: how fat are the tails (kurtosis), is it skewed?
  * autocorrelation of returns -> momentum (+) vs mean-reversion (-) at each TF
  * autocorrelation of |returns| -> volatility clustering (the ARCH signature)
  * variance ratio -> is it a random walk (VR~1), trending (>1), or reverting (<1)?
  * tick microstructure: bid-ask bounce (negative lag-1 autocorr on the mid)

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe eda1_audit_noise.py
"""
from __future__ import annotations

import numpy as np

from eda_lib import TFS, load_bars, load_ticks, autocorr, variance_ratio, kurt_skew, TF_SECONDS


def audit():
    print("=" * 100)
    print("EDA 1 -- DATA AUDIT + NOISE STRUCTURE  (XAUUSD)")
    print("=" * 100)

    print("\n[A] COVERAGE & GAPS")
    print(f"  {'TF':4}{'bars':>9}{'from':>13}{'to':>13}{'med_dt_s':>9}{'gaps>3x':>9}  (gaps ~ weekends/holidays)")
    for tf in TFS:
        df = load_bars(tf)
        dt_s = np.diff(df.index.view("int64") // 1_000_000_000)
        med = int(np.median(dt_s)) if len(dt_s) else 0
        big = int((dt_s > 3 * TF_SECONDS[tf]).sum())
        print(f"  {tf:4}{len(df):>9,}{df.index[0].strftime('%Y-%m-%d'):>13}"
              f"{df.index[-1].strftime('%Y-%m-%d'):>13}{med:>9}{big:>9}")
        del df

    print("\n[B] RETURN DISTRIBUTION (per-bar close-to-close, $/oz)")
    print(f"  {'TF':4}{'mean$':>9}{'std$':>9}{'annVol%':>9}{'skew':>8}{'exKurt':>8}{'maxUp$':>9}{'maxDn$':>9}")
    for tf in TFS:
        df = load_bars(tf)
        r = df["ret"].to_numpy()
        exk, sk = kurt_skew(r)
        # annualized vol from logret * sqrt(bars/yr) -- rough
        from eda_lib import BARS_PER_YEAR
        annvol = np.nanstd(df["logret"].to_numpy()) * np.sqrt(BARS_PER_YEAR[tf]) * 100
        print(f"  {tf:4}{np.nanmean(r):>9.3f}{np.nanstd(r):>9.3f}{annvol:>9.1f}"
              f"{sk:>8.2f}{exk:>8.1f}{np.nanmax(r):>9.2f}{np.nanmin(r):>9.2f}")
    print("  (exKurt>>0 = fat tails / big moves far more common than normal.")
    print("   skew<0 = down-moves bigger than up; >0 = up bigger.)")

    print("\n[C] RETURN AUTOCORRELATION  (momentum + / mean-reversion -)")
    print(f"  {'TF':4}{'lag1':>8}{'lag2':>8}{'lag3':>8}{'lag5':>8}{'lag10':>8}")
    for tf in TFS:
        df = load_bars(tf)
        ac = autocorr(df["ret"].to_numpy(), lags=10)
        print(f"  {tf:4}{ac[0]:>8.3f}{ac[1]:>8.3f}{ac[2]:>8.3f}{ac[4]:>8.3f}{ac[9]:>8.3f}")

    print("\n[D] |RETURN| AUTOCORRELATION  (volatility clustering; big + = vol persists)")
    print(f"  {'TF':4}{'lag1':>8}{'lag2':>8}{'lag3':>8}{'lag5':>8}{'lag10':>8}")
    for tf in TFS:
        df = load_bars(tf)
        ac = autocorr(np.abs(df["ret"].to_numpy()), lags=10)
        print(f"  {tf:4}{ac[0]:>8.3f}{ac[1]:>8.3f}{ac[2]:>8.3f}{ac[4]:>8.3f}{ac[9]:>8.3f}")

    print("\n[E] VARIANCE RATIO  (VR~1 random walk | >1 trending | <1 mean-reverting)")
    print(f"  {'TF':4}{'VR(2)':>9}{'VR(4)':>9}{'VR(8)':>9}{'VR(16)':>9}")
    for tf in TFS:
        df = load_bars(tf)
        lr = df["logret"].to_numpy()
        print(f"  {tf:4}{variance_ratio(lr,2):>9.3f}{variance_ratio(lr,4):>9.3f}"
              f"{variance_ratio(lr,8):>9.3f}{variance_ratio(lr,16):>9.3f}")

    print("\n[F] TICK MICROSTRUCTURE (14d sample)")
    tk = load_ticks()
    mret = tk["mid"].diff().to_numpy()
    ac = autocorr(mret, lags=3)
    nz = tk["mid"].diff().to_numpy()
    frac_zero = float(np.mean(nz[~np.isnan(nz)] == 0))
    print(f"  ticks={len(tk):,} | mid lag1 autocorr={ac[0]:+.3f} (neg = bid-ask bounce noise)")
    print(f"  spread pts: median={tk['spread_pts'].median():.0f} mean={tk['spread_pts'].mean():.0f} "
          f"p99={tk['spread_pts'].quantile(0.99):.0f} max={tk['spread_pts'].max():.0f}")
    print(f"  fraction of ticks with unchanged mid: {frac_zero*100:.0f}%")
    print("\nDONE (module 1)")


if __name__ == "__main__":
    audit()

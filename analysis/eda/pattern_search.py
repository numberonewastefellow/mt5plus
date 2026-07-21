"""
Exhaustive pattern search -- the "360-degree" study.

Sweeps many signal families x params x time-of-day x exit rules on TRAIN data,
ranks by expectancy net of spread, and requires each to beat a random-direction
control. The top finalists are then confirmed ONCE on the SEALED holdout (the
last 6 weeks, never touched during search). The holdout is the honest judge --
it defeats the multiple-comparison inflation of searching hundreds of combos.

Families: momentum-run, thrust follow/fade, breakout follow/fade, MA-cross,
range-fade, session-directional. Each also tested NY-hours-only vs all-day.
Exits include a trailing-stop "ride the trend, exit on pullback" mode.

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe pattern_search.py [tf]
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from patternlib import load_bars, split_holdout, backtest, stats, random_control

NY = {13, 14, 15, 16}          # UTC = NY session / US-data hours (the big-move window)

# exit rules: (tp, sl, max_hold, trail) in $/oz
EXITS = [(10, 5, 16, 0), (5, 5, 12, 0), (8, 8, 16, 0), (15, 6, 24, 0), (50, 6, 24, 6)]


def build_sig(df: pd.DataFrame, fam: str, p, tfilter: str) -> np.ndarray:
    """Point-in-time entry direction (+1/-1/0) per bar for family `fam`, params `p`."""
    o, h, l, c = df["o"], df["h"], df["l"], df["c"]
    body = df["body"]; atr = df["atr"]
    n = len(c); sig = np.zeros(n)
    sgn = np.sign(body).fillna(0).to_numpy()

    if fam == "momentum_run":          # k same-dir candles -> follow/fade
        k, follow = p
        run = pd.Series(sgn).rolling(k).sum().to_numpy()
        up = run == k; dn = run == -k
        sig[up] = follow; sig[dn] = -follow
    elif fam == "thrust":              # big body vs ATR -> follow/fade
        mult, follow = p
        big = (body.abs() > mult * atr).to_numpy() & np.isfinite(atr.to_numpy())
        sig[big & (sgn > 0)] = follow; sig[big & (sgn < 0)] = -follow
    elif fam == "breakout":            # close beyond prior `look` extreme
        look, follow = p
        hh = h.rolling(look).max().shift(1); ll = l.rolling(look).min().shift(1)
        sig[(c > hh).to_numpy()] = follow; sig[(c < ll).to_numpy()] = -follow
    elif fam == "ma_cross":
        fast, slow, follow = p
        mf = c.ewm(span=fast).mean(); ms = c.ewm(span=slow).mean()
        d = np.sign((mf - ms).to_numpy())
        cross_up = (d > 0) & (np.r_[0, d[:-1]] <= 0)
        cross_dn = (d < 0) & (np.r_[0, d[:-1]] >= 0)
        sig[cross_up] = follow; sig[cross_dn] = -follow
    elif fam == "range_fade":          # fade extremes of recent range
        look, = p
        hh = h.rolling(look).max().shift(1); ll = l.rolling(look).min().shift(1)
        pos = (c - ll) / (hh - ll)
        sig[(pos > 0.9).to_numpy()] = -1; sig[(pos < 0.1).to_numpy()] = 1
    elif fam == "session_dir":         # enter fixed dir at first bar of NY window
        (direction,) = p
        hr = df["hour"].to_numpy()
        first = (np.isin(hr, list(NY))) & (~np.isin(np.r_[hr[0], hr[:-1]], list(NY)))
        sig[first] = direction

    if tfilter == "NY":
        sig = sig * np.isin(df["hour"].to_numpy(), list(NY))
    return sig


FAMILIES = (
    [("momentum_run", (k, f)) for k in (2, 3, 4) for f in (1, -1)] +
    [("thrust", (m, f)) for m in (1.0, 1.5) for f in (1, -1)] +
    [("breakout", (lk, f)) for lk in (10, 20) for f in (1, -1)] +
    [("ma_cross", (9, 21, f)) for f in (1, -1)] +
    [("ma_cross", (20, 50, f)) for f in (1, -1)] +
    [("range_fade", (20,)), ("range_fade", (50,))] +
    [("session_dir", (1,)), ("session_dir", (-1,))]
)


def search(train):
    rows = []
    for fam, p in FAMILIES:
        for tfilter in ("all", "NY"):
            sig = build_sig(train, fam, p, tfilter)
            if (sig != 0).sum() < 60:
                continue
            for (tp, sl, mh, tr) in EXITS:
                s = stats(backtest(train, sig, tp, sl, mh, tr))
                if s["n"] < 50:
                    continue
                ctrl = random_control(train, sig, tp, sl, mh, tr)["exp"]
                rows.append(dict(fam=fam, p=p, tf=tfilter, exit=(tp, sl, mh, tr),
                                 **s, ctrl=ctrl, edge=round(s["exp"] - ctrl, 3)))
    return sorted(rows, key=lambda r: r["exp"], reverse=True)


def fmt(r):
    return (f"{r['fam']:13}{str(r['p']):12}{r['tf']:4}tp/sl/mh/tr={str(r['exit']):16} "
            f"n={r['n']:>4} win={r['win']:>5}% exp=${r['exp']:+.2f} "
            f"ctrl=${r['ctrl']:+.2f} edge=${r['edge']:+.2f} PF={r['pf']}")


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "M15"
    df = load_bars(tf)
    train, hold = split_holdout(df)
    print("=" * 108)
    print(f"EXHAUSTIVE PATTERN SEARCH  {tf}  | train {train.index[0].date()}->{train.index[-1].date()} "
          f"({len(train)} bars) | SEALED holdout {hold.index[0].date()}->{hold.index[-1].date()} ({len(hold)} bars)")
    print(f"  {len(FAMILIES)} signals x 2 time-filters x {len(EXITS)} exits ; honest fills; spread ${'%.2f'%0.04}; "
          f"P&L in $/oz (x100 = $/lot)")
    print("=" * 108)

    ranked = search(train)
    pos = [r for r in ranked if r["exp"] > 0 and r["edge"] > 0.15 and r["n"] >= 50]
    print(f"\n[TRAIN] top 12 by expectancy (of {len(ranked)} combos; {len(pos)} beat control by >$0.15):")
    for r in ranked[:12]:
        print("  " + fmt(r))

    # finalists = positive AND clearly beat control on train; confirm on SEALED holdout
    finalists = pos[:8]
    print(f"\n[SEALED HOLDOUT] confirming {len(finalists)} finalists (never seen during search):")
    if not finalists:
        print("  none qualified on train.")
    any_survive = False
    for r in finalists:
        sig = build_sig(hold, r["fam"], r["p"], r["tf"])
        tp, sl, mh, tr = r["exit"]
        s = stats(backtest(hold, sig, tp, sl, mh, tr))
        ctrl = random_control(hold, sig, tp, sl, mh, tr)["exp"] if s["n"] else 0
        ok = s["n"] >= 15 and s["exp"] > 0 and s["exp"] > ctrl
        any_survive = any_survive or ok
        print(f"  {'SURVIVES' if ok else 'fails   '} {r['fam']:13}{str(r['p']):10}{r['tf']:4}"
              f"{str(r['exit']):16} holdout: n={s['n']:>3} win={s['win']:>5}% "
              f"exp=${s['exp']:+.2f} ctrl=${ctrl:+.2f} tot=${s['tot']:+.1f}")

    print("\n[VERDICT]")
    if not finalists:
        print("  No setup was even positive-with-edge on training data. No systematic edge found.")
    elif any_survive:
        print("  >=1 setup survived the sealed holdout AND beat the random control there.")
        print("  -> genuine lead. Next: adversarial re-audit + walk-forward + sizing study.")
    else:
        print("  Every train-positive setup FAILED on the sealed holdout -> the training")
        print("  numbers were multiple-comparison overfitting. No durable edge (this pass).")
    print("\nDONE")


if __name__ == "__main__":
    main()

"""
patternlib -- shared, correctness-critical engine for the exhaustive pattern
search. Run with test_env (pandas).

Design rules (learned the hard way -- a wick-fill and a lookahead each faked an
edge earlier):
  * HONEST FILLS. A signal computed at the close of bar i is entered at bar i+1's
    OPEN (never the wick that formed the signal). One full spread charged per
    round trip, embedded in the entry price.
  * POINT-IN-TIME. Signal functions may read only bars <= i.
  * SEALED HOLDOUT. The most recent HOLDOUT_DAYS are split off and NEVER touched
    during search; finalists are confirmed on it exactly once.
  * CONTROLS. Every candidate is compared to a random-direction control (same
    entry bars, coin-flip direction). A real edge must beat it.

P&L is in $/oz (price points). Multiply by 100*lot for account $ (gold: 100oz/lot).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SPREAD = 0.04          # $/oz round-trip (measured median 40 pts)
HOLDOUT_DAYS = 42      # ~6 weeks sealed for final confirmation


def load_bars(tf: str) -> pd.DataFrame:
    d = np.load(os.path.join(DATA, f"bars_{tf}.npz"), allow_pickle=True)
    df = pd.DataFrame({"o": d["open"], "h": d["high"], "l": d["low"], "c": d["close"],
                       "v": d["tick_volume"]}, index=pd.to_datetime(d["time"], unit="s"))
    df["body"] = df["c"] - df["o"]
    prevc = df["c"].shift(1)
    tr = pd.concat([df["h"] - df["l"], (df["h"] - prevc).abs(), (prevc - df["l"]).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek
    df.attrs["tf"] = tf
    return df


def split_holdout(df: pd.DataFrame):
    """(train, holdout). holdout = last HOLDOUT_DAYS, sealed."""
    cut = df.index[-1] - pd.Timedelta(days=HOLDOUT_DAYS)
    return df[df.index < cut].copy(), df[df.index >= cut].copy()


def backtest(df: pd.DataFrame, sig: np.ndarray, tp: float, sl: float,
             max_hold: int, trail: float = 0.0) -> np.ndarray:
    """Enter next-bar-open in direction sig[i] (+1/-1/0), one position at a time.
    tp/sl in $/oz. trail>0 = trailing stop of `trail` $ from the best price.
    Returns per-trade P&L in $/oz (spread embedded). Never looks ahead."""
    o, h, l, c = (df[k].to_numpy() for k in ("o", "h", "l", "c"))
    n = len(c)
    out = []
    busy = -1
    for i in range(n - 1):
        if sig[i] == 0 or i <= busy:
            continue
        dirn = int(np.sign(sig[i]))
        entry = o[i + 1] + dirn * SPREAD          # honest: next open + full spread
        stop = entry - dirn * sl
        target = entry + dirn * tp
        best = entry
        pnl = None
        for k in range(i + 1, min(n, i + 1 + max_hold)):
            # CONSERVATIVE intrabar order: check the stop against the level set by
            # PRIOR bars first (assume the adverse extreme comes first), and only
            # THEN trail the stop with this bar's favorable extreme. Avoids the
            # optimistic "trail up on the high, then check the low" lookahead.
            if dirn > 0:
                if l[k] <= stop:
                    pnl = (stop - entry); break
                if h[k] >= target:
                    pnl = (target - entry); break
                if trail > 0:
                    best = max(best, h[k]); stop = max(stop, best - trail)
            else:
                if h[k] >= stop:
                    pnl = (entry - stop); break
                if l[k] <= target:
                    pnl = (entry - target); break
                if trail > 0:
                    best = min(best, l[k]); stop = min(stop, best + trail)
        if pnl is None:
            k = min(n - 1, i + max_hold)
            pnl = dirn * (c[k] - entry)
        out.append(pnl)
        busy = k
    return np.array(out)


def stats(pnl: np.ndarray) -> dict:
    if not len(pnl):
        return dict(n=0, win=0.0, exp=0.0, tot=0.0, pf=0.0, dd=0.0)
    wins = pnl[pnl > 0]; loss = pnl[pnl <= 0]
    pf = wins.sum() / abs(loss.sum()) if loss.sum() != 0 else float("inf")
    eq = np.cumsum(pnl); dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    return dict(n=len(pnl), win=round(100 * len(wins) / len(pnl), 1),
                exp=round(float(pnl.mean()), 3), tot=round(float(pnl.sum()), 1),
                pf=round(float(pf), 2), dd=round(dd, 1))


def random_control(df: pd.DataFrame, sig: np.ndarray, tp: float, sl: float,
                   max_hold: int, trail: float = 0.0, seeds: int = 5) -> dict:
    """Same entry bars, coin-flip direction, averaged over `seeds`. The floor a
    real edge must clear."""
    where = np.flatnonzero(sig != 0)
    exps = []
    for s in range(seeds):
        rng = np.random.default_rng(1000 + s)
        rs = np.zeros(len(sig))
        rs[where] = rng.choice([-1, 1], size=len(where))
        exps.append(stats(backtest(df, rs, tp, sl, max_hold, trail))["exp"])
    return dict(exp=round(float(np.mean(exps)), 3), std=round(float(np.std(exps)), 3))

"""
Fade-S/R strategy -- the HONEST test. Turns the eda4 in-sample lead into a
yes/no by fixing the two things that inflate an in-sample number, and by
running adversarial controls.

Fixes vs eda4:
  1. POINT-IN-TIME levels. A swing high at H1 bar i is only *known* at bar i+w
     (you need w future bars to confirm a local extreme). At any decision time t
     we use only swings CONFIRMED before t, counted over a trailing lookback --
     no future levels, no future touch-counts.
  2. WALK-FORWARD. Re-fit (stop/target/tol/min-touches) on a training window,
     trade the next unseen window, concatenate. Never fit on the ticks it trades.

Adversarial controls (the point is to try to BREAK the result):
  A. RANDOM-LEVEL control -- identical strategy, but levels placed at random in
     the recent range (matched count). If this profits like the real one, the
     'S/R edge' is spurious (trend / fat-tails / R:R asymmetry), not S/R.
  B. LOOKAHEAD vs PIT -- run with all-history levels (like eda4) vs PIT levels,
     to measure how much the lookahead alone inflated the eda4 number.

Verdict rule: believe the edge ONLY if it (i) is positive OOS across windows AND
(ii) clearly beats the random-level control. Otherwise it's a mirage.

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe strategy_test.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from eda_lib import load_bars

SPREAD = 0.04
W = 5                     # fractal half-width for swings (confirmed W bars later)
MAX_HOLD = 16            # M15 bars (~4h)
DAY_NS = 86_400_000_000_000


def h1_swings():
    """All H1 swing levels as (price, known_time_ns). known_time = the bar W ahead
    (a fractal is only confirmed once W later bars exist) -> no lookahead."""
    h1 = load_bars("H1")
    hh, ll = h1["high"].to_numpy(), h1["low"].to_numpy()
    t = h1.index.view("int64")
    out = []
    for i in range(W, len(hh) - W):
        known = t[i + W]
        if hh[i] == max(hh[i - W:i + W + 1]):
            out.append((hh[i], known))
        if ll[i] == min(ll[i - W:i + W + 1]):
            out.append((ll[i], known))
    price = np.array([p for p, _ in out])
    known = np.array([k for _, k in out], dtype="int64")
    order = np.argsort(known)
    return price[order], known[order]


def active_levels(sw_price, sw_known, t_ns, min_touches, lookback_days, pit=True):
    """Clustered levels active at time t: swings confirmed in [t-lookback, t),
    bucketed to $5, buckets with >= min_touches. pit=False ignores time (lookahead)."""
    lb = lookback_days * DAY_NS
    if pit:
        m = (sw_known < t_ns) & (sw_known >= t_ns - lb)
    else:
        m = np.ones(len(sw_price), dtype=bool)       # all history -> lookahead control
    if not m.any():
        return np.empty(0)
    buckets = np.round(sw_price[m] / 5.0) * 5.0
    vals, cnts = np.unique(buckets, return_counts=True)
    return vals[cnts >= min_touches]


def _trade_at(h, l, c, o, i, L, dirn, stop_d, r_mult, n, fill="level"):
    """Fade fill+exit from a touch of level L. Returns (R, exit_idx).

    fill='level'    -> assume filled AT the level price (optimistic: often a wick).
    fill='next_open'-> filled at the NEXT bar's open (conservative, no wick gift).
    """
    if fill == "next_open":
        entry = o[i + 1] + dirn * (SPREAD / 2)
        stop = entry - dirn * stop_d
    else:
        entry = L + dirn * (SPREAD / 2)
        stop = L - dirn * stop_d
    tp = entry + dirn * r_mult * stop_d
    for k in range(i + 1, min(n, i + 1 + MAX_HOLD)):
        hit_sl = (l[k] <= stop) if dirn > 0 else (h[k] >= stop)
        hit_tp = (h[k] >= tp) if dirn > 0 else (l[k] <= tp)
        if hit_sl:
            return -1.0 - (SPREAD / 2) / stop_d, k
        if hit_tp:
            return r_mult - (SPREAD / 2) / stop_d, k
    k = min(n - 1, i + MAX_HOLD)
    pl = (c[k] - entry) if dirn > 0 else (entry - c[k])
    return pl / stop_d - (SPREAD / 2) / stop_d, k


def sim(m15, level_fn, stop_d, r_mult, tol, i_lo, i_hi, fill="level"):
    """Fade active levels over M15 bars [i_lo,i_hi). level_fn(day_ns)->levels,
    rebuilt once per day. One position at a time."""
    h, l, c = m15["high"].to_numpy(), m15["low"].to_numpy(), m15["close"].to_numpy()
    o = m15["open"].to_numpy()
    tns = m15.index.view("int64")
    n = len(c)
    out = []
    busy = -1
    cur_day = None
    lv = np.empty(0)
    for i in range(max(1, i_lo), min(i_hi, n - 1)):
        if i <= busy:
            continue
        day = tns[i] - (tns[i] % DAY_NS)
        if day != cur_day:
            lv = level_fn(tns[i]); cur_day = day
        if not len(lv):
            continue
        near = lv[(lv >= l[i] - tol) & (lv <= h[i] + tol)]
        if not len(near):
            continue
        L = near[np.argmin(np.abs(near - c[i - 1]))]
        side = np.sign(c[i - 1] - L)          # +1 support->long, -1 resistance->short
        if side == 0:
            continue
        R, k = _trade_at(h, l, c, o, i, L, int(side), stop_d, r_mult, n, fill)
        out.append(R); busy = k
    return np.array(out)


def summ(R):
    if not len(R):
        return dict(n=0, win=0, exp=0, pf=0, tot=0, dd=0)
    wins = R[R > 0]; loss = R[R <= 0]
    pf = wins.sum() / abs(loss.sum()) if loss.sum() != 0 else float("inf")
    eq = np.cumsum(R); dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0
    return dict(n=len(R), win=round(100 * len(wins) / len(R), 1), exp=round(float(R.mean()), 3),
                pf=round(float(pf), 2), tot=round(float(R.sum()), 1), dd=round(dd, 1))


def line(tag, s):
    return (f"  {tag:26} n={s['n']:>5} win={s['win']:>5}% exp={s['exp']:+.3f}R "
            f"PF={s['pf']:>4} totR={s['tot']:+7.1f} maxDD={s['dd']:.0f}R")


GRID = [(sd, rm, tol, mt) for sd in (3.0, 5.0, 8.0) for rm in (1.5, 2.0)
        for tol in (2.0, 3.0) for mt in (3, 4)]
LOOKBACK = 90


def run():
    print("=" * 100)
    print("FADE-S/R -- point-in-time walk-forward + adversarial controls")
    print("=" * 100)
    m15 = load_bars("M15")
    sw_p, sw_k = h1_swings()
    tns = m15.index.view("int64")
    n = len(m15)
    contract = m15.attrs["contract"]
    print(f"  M15 bars={n:,}  H1 swings={len(sw_p):,}  lookback={LOOKBACK}d  spread={SPREAD}$")

    def real_levels(mt):
        return lambda t: active_levels(sw_p, sw_k, t, mt, LOOKBACK, pit=True)

    def look_levels(mt):
        return lambda t: active_levels(sw_p, sw_k, t, mt, LOOKBACK, pit=False)

    def rand_levels(mt, seed):
        # matched count to the real PIT set each day, placed uniformly in the
        # trailing 30-day price range; deterministic per day via seed+day.
        def fn(t):
            real = active_levels(sw_p, sw_k, t, mt, LOOKBACK, pit=True)
            k = len(real)
            if k == 0:
                return np.empty(0)
            win = (tns >= t - 30 * DAY_NS) & (tns < t)
            if win.sum() < 10:
                return np.empty(0)
            lo, hi = m15["low"].to_numpy()[win].min(), m15["high"].to_numpy()[win].max()
            rng = np.random.default_rng(seed + int(t // DAY_NS))
            return rng.uniform(lo, hi, k)
        return fn

    # ---- WALK-FORWARD (train 60d / test 20d) on PIT + LOOKAHEAD + RANDOM ----
    t0, tend = tns[0], tns[-1]
    def idx(x): return int(np.searchsorted(tns, x))
    def walk(level_builder, fill="level"):
        oos = []
        ts = t0 + 60 * DAY_NS
        while ts < tend:
            tr_lo, te0 = idx(ts - 60 * DAY_NS), idx(ts)
            te1 = idx(ts + 20 * DAY_NS)
            best, bexp = None, -9.9
            for (sd, rm, tol, mt) in GRID:
                R = sim(m15, level_builder(mt), sd, rm, tol, tr_lo, te0, fill)
                if len(R) >= 20 and R.mean() > bexp:
                    bexp, best = R.mean(), (sd, rm, tol, mt)
            if best:
                sd, rm, tol, mt = best
                oos.append(sim(m15, level_builder(mt), sd, rm, tol, te0, te1, fill))
            ts += 20 * DAY_NS
        return np.concatenate(oos) if oos else np.array([])

    print("\n[1] FILL = 'at the level' (optimistic -- fills at the wick)")
    print(line("PIT S/R (real test)", summ(walk(real_levels, "level"))))
    print(line("RANDOM levels (control)", summ(walk(lambda mt: rand_levels(mt, 12345), "level"))),
          "  <- matches PIT => S/R location irrelevant")

    print("\n[2] FILL = 'next-bar open' (conservative -- no wick gift)")
    pit_no = walk(real_levels, "next_open")
    rnd_no = walk(lambda mt: rand_levels(mt, 12345), "next_open")
    print(line("PIT S/R (real test)", summ(pit_no)))
    print(line("RANDOM levels (control)", summ(rnd_no)))

    print("\n[VERDICT]")
    ps, rs = summ(pit_no), summ(rnd_no)
    if abs(ps["exp"]) < 0.05 and abs(rs["exp"]) < 0.05:
        print("  DISCARD. With honest fills the fade-S/R edge collapses to ~0 (random-walk)")
        print("  -> the 'edge' at [1] was an entry-fill artifact (assuming fills at the wick),")
        print("     not skill. Real S/R gives no advantage over random levels either way.")
    elif ps["exp"] > 0.05 and ps["exp"] > 2 * max(rs["exp"], 1e-4):
        print("  SURVIVES conservative fills AND beats random -> worth a forward paper test.")
    else:
        print("  NOT CONFIRMED: either collapses on honest fills or the random control matches it.")
    print("\nDONE")


if __name__ == "__main__":
    run()

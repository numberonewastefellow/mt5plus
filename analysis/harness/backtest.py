"""
Tick-level backtest + honest rolling walk-forward.

This is the ai-hedge-fund/v2 ``run_cycle`` idea and analysis/walk_forward.py's
methodology, at tick granularity. The guardrails are the whole point -- they
are what separated the truthful -16% OOS number from the mirage +91% in-sample
number in the prior research:

  * **Walk-forward only.** Fit config on a past window, trade the next unseen
    window, concatenate. A single-window "best" is treated as in-sample noise.
  * **Spread paid every trade.** Enter at the far side (ask long / bid short),
    exit at the near side. One full spread per round trip, baked into fills.
  * **One position at a time.** No overlapping-signal P&L inflation (the missing
    realism the prior research flagged).
  * **Point-in-time.** A trade opened on tick i uses only ticks <= i to decide;
    exits scan real forward ticks.

Nothing here trades a live account. It measures R (multiples of risk).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .core import Ticks
from .features import TickFeatures
from .models import TickMomentum


@dataclass
class Trade:
    i_entry: int
    i_exit: int
    ts_entry: int
    ts_exit: int
    direction: int          # +1 long, -1 short
    entry: float
    exit_price: float
    risk: float             # price distance to stop
    R: float                # result in risk multiples (spread already inside fills)
    reason: str             # 'SL' | 'TP' | 'TIME'
    mode: str


def simulate(feats: TickFeatures, model: TickMomentum, entry_thr: float,
             sl_pts: float, r_mult: float, max_hold: int,
             i_lo: int, i_hi: int) -> list[Trade]:
    """Run one config over candidate spikes in [i_lo, i_hi). One position at a time."""
    ticks: Ticks = feats.ticks
    bid, ask, point, n = ticks.bid, ticks.ask, ticks.point, ticks.n
    sl_dist = sl_pts * point
    out: list[Trade] = []
    busy_until = -1  # index before which we are flat again (one-at-a-time)

    for i in feats.spikes:
        if i < i_lo or i >= i_hi or i + 1 >= n:
            continue
        if i <= busy_until:
            continue
        sig = model.predict(feats, i)
        if sig.value == 0.0 or abs(sig.value) < entry_thr:
            continue
        dirn = 1 if sig.value > 0 else -1

        if dirn > 0:
            entry = ask[i + 1]
            sl, tp = entry - sl_dist, entry + r_mult * sl_dist
        else:
            entry = bid[i + 1]
            sl, tp = entry + sl_dist, entry - r_mult * sl_dist
        risk = sl_dist
        if risk <= 0:
            continue

        R = exit_price = None
        i_exit = reason = None
        for k in range(i + 1, min(n, i + 1 + max_hold)):
            if dirn > 0:                       # exit long at the bid
                px = bid[k]
                hit_sl, hit_tp = px <= sl, px >= tp
            else:                              # exit short at the ask
                px = ask[k]
                hit_sl, hit_tp = px >= sl, px <= tp
            if hit_sl:                         # stop checked first (conservative)
                R, exit_price, i_exit, reason = -1.0, sl, k, "SL"
                break
            if hit_tp:
                R, exit_price, i_exit, reason = r_mult, tp, k, "TP"
                break
        if R is None:                          # time stop -> mark to current near side
            i_exit = min(n - 1, i + max_hold)
            exit_price = bid[i_exit] if dirn > 0 else ask[i_exit]
            pl = (exit_price - entry) if dirn > 0 else (entry - exit_price)
            R, reason = pl / risk, "TIME"

        out.append(Trade(
            i_entry=i, i_exit=i_exit, ts_entry=int(ticks.t_msc[i + 1]),
            ts_exit=int(ticks.t_msc[i_exit]), direction=dirn, entry=entry,
            exit_price=exit_price, risk=risk, R=float(R), reason=reason, mode=model.mode,
        ))
        busy_until = i_exit
    return out


# ---- config grid (kept small -- a large grid over one window IS overfitting) ----
# XAUUSD on this feed: point = 0.001, so 1 point = $0.001/oz and spread ~= 41 pts
# ($0.041). Stops MUST sit well above the spread or every trade instant-stops.
G_MODE = ["follow", "fade"]
G_SPIKE_Z = [2.5, 3.5]
G_SL_PTS = [200.0, 400.0, 800.0]   # $0.20 / $0.40 / $0.80 -- 5x-20x the spread
G_RMULT = [1.0, 2.0]


def _expectancy(trades: list[Trade]) -> float:
    return float(np.mean([t.R for t in trades])) if trades else -9.9


def summarize(trades: list[Trade]) -> dict | None:
    if not trades:
        return None
    R = np.array([t.R for t in trades])
    wins, losses = R[R > 0], R[R <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    eq = np.cumsum(R)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    return dict(n=len(R), win=round(100 * len(wins) / len(R), 1),
                exp=round(float(R.mean()), 3), pf=round(float(pf), 2),
                totR=round(float(R.sum()), 1), maxDD_R=round(dd, 1))


def walk_forward_generic(feats: TickFeatures, grid: list, run_cfg,
                         train_days: float = 10, test_days: float = 3,
                         min_train_trades: int = 20, log=print):
    """The one honest engine, strategy-agnostic. Rolling weekly re-fit:

      * ``grid``    -- list of opaque config tuples to search each window.
      * ``run_cfg(feats, cfg, i_lo, i_hi) -> list[Trade]`` -- runs one config
        over a tick window. Momentum and the breakout bracket both plug in here.

    Every OOS trade is decided by a config chosen only on strictly-earlier ticks
    (train window), then applied to the next unseen window. Returns
    (oos_trades, weekly_log). weekly_log[i]['cfg'] is the raw config tuple; the
    caller formats it (configs differ per strategy).
    """
    t = feats.ticks.t_msc.astype("int64")  # ms
    t0 = dt.datetime.fromtimestamp(t[0] / 1000.0)
    t_end = dt.datetime.fromtimestamp(t[-1] / 1000.0)

    def idx_at(when: dt.datetime) -> int:
        return int(np.searchsorted(t, int(when.timestamp() * 1000)))

    oos: list[Trade] = []
    weekly: list[dict] = []
    test_start = t0 + dt.timedelta(days=train_days)
    while test_start < t_end:
        train_lo = idx_at(test_start - dt.timedelta(days=train_days))
        test_i0 = idx_at(test_start)
        test_i1 = idx_at(test_start + dt.timedelta(days=test_days))

        best_cfg, best_exp = None, -9.9
        for cfg in grid:
            tr = run_cfg(feats, cfg, train_lo, test_i0)
            if len(tr) >= min_train_trades:
                e = _expectancy(tr)
                if e > best_exp:
                    best_exp, best_cfg = e, cfg

        wk = dict(week=test_start.strftime("%m-%d"), cfg=best_cfg, train_exp=round(best_exp, 3))
        if best_cfg:
            tt = run_cfg(feats, best_cfg, test_i0, test_i1)
            oos += tt
            wk.update(n=len(tt), test_exp=round(_expectancy(tt), 3) if tt else 0.0)
        else:
            wk.update(n=0, test_exp=0.0)
        weekly.append(wk)
        test_start += dt.timedelta(days=test_days)
    return oos, weekly


def _momentum_grid() -> list:
    return [(m, sz, sl, rm) for m in G_MODE for sz in G_SPIKE_Z
            for sl in G_SL_PTS for rm in G_RMULT]


def walk_forward(feats: TickFeatures, train_days: float = 10, test_days: float = 3,
                 max_hold: int = 600, entry_thr: float = 0.15,
                 min_train_trades: int = 20, log=print):
    """TickMomentum walk-forward (thin wrapper over walk_forward_generic)."""
    def run_cfg(f, cfg, lo, hi):
        mode, sz, sl, rm = cfg
        return simulate(f, TickMomentum(mode=mode, spike_z=sz), entry_thr, sl, rm, max_hold, lo, hi)
    return walk_forward_generic(feats, _momentum_grid(), run_cfg,
                                train_days, test_days, min_train_trades, log)

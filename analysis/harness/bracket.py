"""
Direction-agnostic breakout bracket -- the strategy built on the ONE edge that
survived every test in this repo's research: volume/velocity predicts the
*size* of the next move, not its direction.

Mechanics (a classic OCO straddle around a spike):

    on a velocity spike at tick i, mark mid0 = mid[i] and arm two stop orders:
        buy-stop  @ mid0 + width
        sell-stop @ mid0 - width
    whichever the market touches first fills THAT direction; the other cancels.
    if neither triggers within `max_wait` ticks -> cancel both, no trade.

    after a fill: stop = `width` back (≈ the spike origin), target = r_mult·width.

No direction is ever predicted. The honest question it answers: does a spike
produce a move large enough, often enough, to clear the width you give up
waiting for the breakout + the spread + the stop? If not, it loses -- and the
walk-forward will say so, same as it did for momentum.

Reuses the ``Trade`` dataclass and ``walk_forward_generic`` engine unchanged.
"""
from __future__ import annotations

from .core import Ticks
from .features import TickFeatures
from .backtest import Trade, walk_forward_generic

# Grid (direction-agnostic -> no mode axis). point = 0.001, spread ~40-60 pts.
G_WIDTH = [200.0, 400.0, 800.0]    # breakout half-width: $0.20 / $0.40 / $0.80
G_RMULT = [1.0, 2.0]


def simulate_bracket(feats: TickFeatures, width_pts: float, r_mult: float,
                     max_wait: int, max_hold: int, i_lo: int, i_hi: int,
                     extra_cost_pts: float = 0.0) -> list[Trade]:
    """Run one bracket config over candidate spikes in [i_lo, i_hi). One at a time.

    ``extra_cost_pts`` is round-trip cost BEYOND the spread (which is already in
    the fills): commission + slippage, in points. Deducted from each trade's R as
    ``extra_cost_pts * point / risk``. Spread is modelled by the bid/ask fills;
    set a tighter spread by rebuilding the ticks (see experiment_spread.py).
    """
    ticks: Ticks = feats.ticks
    bid, ask, point, n = ticks.bid, ticks.ask, ticks.point, ticks.n
    mid = feats.mid
    width = width_pts * point
    out: list[Trade] = []
    busy_until = -1

    for i in feats.spikes:
        if i < i_lo or i >= i_hi or i + 1 >= n:
            continue
        if i <= busy_until:
            continue
        up, down = mid[i] + width, mid[i] - width

        # wait for the first breakout fill (buy-stop fills at ask, sell-stop at bid)
        dirn, i_fill, entry = 0, None, None
        for k in range(i + 1, min(n, i + 1 + max_wait)):
            if ask[k] >= up:
                dirn, i_fill, entry = 1, k, ask[k]
                break
            if bid[k] <= down:
                dirn, i_fill, entry = -1, k, bid[k]
                break
        if dirn == 0:                      # no breakout within the window -> cancel
            continue

        risk = width
        if dirn > 0:
            sl, tp = entry - width, entry + r_mult * width
        else:
            sl, tp = entry + width, entry - r_mult * width

        R = exit_price = i_exit = reason = None
        for k in range(i_fill + 1, min(n, i_fill + 1 + max_hold)):
            px = bid[k] if dirn > 0 else ask[k]     # exit at the near side
            hit_sl = px <= sl if dirn > 0 else px >= sl
            hit_tp = px >= tp if dirn > 0 else px <= tp
            if hit_sl:
                R, exit_price, i_exit, reason = -1.0, sl, k, "SL"
                break
            if hit_tp:
                R, exit_price, i_exit, reason = r_mult, tp, k, "TP"
                break
        if R is None:
            i_exit = min(n - 1, i_fill + max_hold)
            exit_price = bid[i_exit] if dirn > 0 else ask[i_exit]
            pl = (exit_price - entry) if dirn > 0 else (entry - exit_price)
            R, reason = pl / risk, "TIME"

        R -= extra_cost_pts * point / risk   # commission + slippage, beyond spread

        out.append(Trade(
            i_entry=i, i_exit=i_exit, ts_entry=int(ticks.t_msc[i_fill]),
            ts_exit=int(ticks.t_msc[i_exit]), direction=dirn, entry=entry,
            exit_price=exit_price, risk=risk, R=float(R), reason=reason, mode="bracket",
        ))
        busy_until = i_exit
    return out


def _bracket_grid() -> list:
    return [(w, rm) for w in G_WIDTH for rm in G_RMULT]


def walk_forward_bracket(feats: TickFeatures, train_days: float = 10, test_days: float = 3,
                         max_wait: int = 100, max_hold: int = 600,
                         min_train_trades: int = 20, extra_cost_pts: float = 0.0, log=print):
    """Breakout-bracket walk-forward (thin wrapper over walk_forward_generic).

    ``extra_cost_pts`` = commission + slippage beyond the spread (see simulate_bracket).
    """
    def run_cfg(f, cfg, lo, hi):
        w, rm = cfg
        return simulate_bracket(f, w, rm, max_wait, max_hold, lo, hi, extra_cost_pts)
    return walk_forward_generic(feats, _bracket_grid(), run_cfg,
                                train_days, test_days, min_train_trades, log)

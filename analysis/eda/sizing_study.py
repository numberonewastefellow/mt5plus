"""
Sizing / pyramiding math -- answers the user's question:
  "catch a tiny move with a big lot, exit small $ -- but how is the size chosen,
   and with what confidence?"

The honest core:
  * Position size does NOT create edge; it scales whatever per-trade edge exists
    -- up AND down. With a MARGINAL edge, big size = a fat lucky tail AND a high
    chance of ruin. The "$500 -> $30k" stories are the survivors; the blown
    accounts don't post.
  * The mathematically-correct "how big" is the Kelly fraction, computed from the
    measured edge. For our marginal edge it is TINY.
  * "Scale up at the right time" needs a reliable signal of *which way* -- but
    direction is ~random, so there is no such signal. Scaling up is mostly luck.

Uses the REAL measured M5 thrust-follow + trailing-stop per-trade P&L as the edge
being sized. Regime caveat: this sample was all high-volatility (see findings).

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe sizing_study.py
"""
from __future__ import annotations

import numpy as np

import patternlib as P

CONTRACT = 100.0          # oz per lot (gold)
LEVERAGE = 500.0          # assumed 1:500 (margin per 0.01 lot ~ price*100*0.01/500)
E0 = 500.0                # a small account, like the user's example
N_SIMS = 20000


def edge_pnl():
    """Per-trade P&L in $/oz for the measured M5 thrust-follow + trailing edge."""
    df = P.load_bars("M5")
    big = (df["body"].abs() > 1.5 * df["atr"]).to_numpy() & np.isfinite(df["atr"].to_numpy())
    s = np.sign(df["body"].to_numpy())
    sig = np.zeros(len(df)); sig[big & (s > 0)] = 1; sig[big & (s < 0)] = -1
    return P.backtest(df, sig, tp=50, sl=6, max_hold=24, trail=6)   # $/oz per trade


def mc_fixed_lot(pnl_oz, lot, n_trades, e0=E0):
    """Monte-Carlo of sequential fixed-lot trading. Ruin = equity hits <= 0."""
    rng = np.random.default_rng(42)
    per_trade = pnl_oz * CONTRACT * lot                    # $ per trade at this lot
    finals, ruined = [], 0
    for _ in range(N_SIMS):
        draws = rng.choice(per_trade, size=n_trades, replace=True)
        eq = e0 + np.cumsum(draws)
        if eq.min() <= 0:
            ruined += 1; finals.append(0.0)
        else:
            finals.append(eq[-1])
    f = np.array(finals)
    return dict(median=np.median(f), p05=np.percentile(f, 5), p95=np.percentile(f, 95),
                p99=np.percentile(f, 99), ruin=100 * ruined / N_SIMS, mean=f.mean())


def main():
    pnl = edge_pnl()
    mu, sd = pnl.mean(), pnl.std()
    win = 100 * (pnl > 0).mean()
    print("=" * 88)
    print("SIZING / PYRAMIDING MATH  (on the measured M5 thrust-follow + trailing edge)")
    print("=" * 88)
    print(f"\nPer-trade edge being sized (in $/oz price move):")
    print(f"  trades={len(pnl)}  mean=${mu:+.3f}/oz  std=${sd:.2f}/oz  win={win:.1f}%  "
          f"best=${pnl.max():.1f}  worst=${pnl.min():.1f}")
    print(f"  => at 0.01 lot: ${mu*CONTRACT*0.01:+.2f}/trade avg, swing +-${sd*CONTRACT*0.01:.2f}")
    print(f"  => at 0.16 lot: ${mu*CONTRACT*0.16:+.2f}/trade avg, swing +-${sd*CONTRACT*0.16:.2f}")

    # ---- Kelly: the mathematically-optimal fraction of equity to risk ----
    # f* = mean / variance  (in per-trade RETURN units). Risk per trade in $/oz has
    # 'return' = pnl/E0 when sized so 1 oz-move = 1 unit; use ratio mu/var of $/oz.
    kelly_frac = mu / (sd ** 2)                 # fraction of equity per $1/oz of move
    # translate: risking 'kelly_frac' of E0 means lot so that a 1-std adverse move
    # (~$sd/oz) loses kelly_frac*E0.  lot = kelly_frac*E0 / (sd*CONTRACT)
    kelly_lot = max(0.0, kelly_frac * E0 / (sd * CONTRACT))
    print(f"\nKelly-optimal sizing for a ${E0:.0f} account (full Kelly is aggressive; half-Kelly safer):")
    print(f"  full-Kelly lot ~= {kelly_lot:.3f}   half-Kelly ~= {kelly_lot/2:.3f}")
    print(f"  (the user's 0.16 lot on ${E0:.0f} is {0.16/max(kelly_lot,1e-9):.0f}x full-Kelly -> massively over-bet)")

    # ---- Monte-Carlo: $500 over 50 trades at different lot sizes ----
    print(f"\nMonte-Carlo: start ${E0:.0f}, 50 trades, {N_SIMS:,} runs each. 'The $500 -> ? question':")
    print(f"  {'lot':>6}{'median $':>11}{'5th pct':>10}{'95th pct':>11}{'99th (lucky)':>13}{'RUIN %':>9}")
    for lot in (0.01, 0.02, 0.05, 0.10, 0.16, 0.30):
        r = mc_fixed_lot(pnl, lot, 50)
        print(f"  {lot:>6.2f}{r['median']:>11,.0f}{r['p05']:>10,.0f}{r['p95']:>11,.0f}"
              f"{r['p99']:>13,.0f}{r['ruin']:>9.1f}")

    print("\nWHAT THIS SHOWS")
    print("  * Small lot: slow grind up, ~0% ruin -- but the '$30k' outcome basically never happens.")
    print("  * Big lot (0.16-0.30): the 99th-pct 'lucky' outcome gets huge -- AND ruin% explodes.")
    print("    Same scheme, same edge: the big win and the blown account are the SAME bet's two tails.")
    print("  * 'Catch a tiny move with a big lot' cuts BOTH ways: a $5 move x 1.6 lots = +$800 OR -$800;")
    print("    on a $500 account the adverse side is a margin-call. Direction being ~random, it's a")
    print("    coin flip the marginal edge barely tilts.")
    print("  * 'Size up at the right time' needs to know WHICH WAY next -- and that signal is ~random.")
    print("    So scaling up is mostly luck, not skill. The disciplined answer is Kelly-small + let the")
    print("    trailing stop (not bigger size) do the trend-riding.")
    print("\nDONE")


if __name__ == "__main__":
    main()

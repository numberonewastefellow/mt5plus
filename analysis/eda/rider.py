"""
Rider backtest + cross-verification (Phase 0).

1. Backtest the vol-regime rider via the CENT-VERIFIED patternlib engine, fed by
   RiderState.entry_signal -- so the number is provably the validated edge, now
   with the volatility-regime gate. Compares regime configs + random control.
2. Cross-verify: drive the LIVE RiderState.on_bar state machine over the same
   history and check its trades match the backtest (same code must trade the same).

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe rider.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import patternlib as P
from rider_state import RiderConfig, RiderState, entry_signal, kelly_lot

TF = "M5"


def bt(df, cfg):
    sig = entry_signal(df, cfg)
    pnl = P.backtest(df, sig, cfg.tp, cfg.sl, cfg.max_hold, cfg.trail)
    ctrl = P.random_control(df, sig, cfg.tp, cfg.sl, cfg.max_hold, cfg.trail, seeds=30)["exp"]
    s = P.stats(pnl); s["ctrl"] = ctrl; s["edge"] = round(s["exp"] - ctrl, 3)
    return s, pnl


def drive_live(df, cfg):
    """Run the live on_bar machine over history; return its per-trade $/oz P&L
    (spread NOT charged here -- the live executor adds it; we subtract it for
    the comparison)."""
    atr = df["atr"].to_numpy()
    atr_med = df["atr"].rolling(cfg.atr_win).median().to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("o", "h", "l", "c"))
    hour = df["hour"].to_numpy()
    st = RiderState(cfg)
    trades, entry_px, entry_dir = [], None, 0
    for i in range(len(df)):
        for sug in st.on_bar(o[i], h[i], l[i], c[i], atr[i], atr_med[i], int(hour[i]), 500.0):
            if sug.kind == "enter":
                entry_px, entry_dir = sug.entry_ref, (1 if sug.side == "buy" else -1)
            elif sug.kind == "close" and entry_px is not None:
                trades.append(entry_dir * (sug.entry_ref - entry_px) - P.SPREAD)
                entry_px = None
    return np.array(trades)


def main():
    df = P.load_bars(TF)
    print("=" * 92)
    print(f"RIDER BACKTEST + CROSS-VERIFY  {TF}  ({len(df):,} bars)  spread=${P.SPREAD}")
    print("  P&L in $/oz (x100 = $/1.0-lot).  All via the cent-verified patternlib engine.")
    print("=" * 92)

    configs = {
        "baseline (no regime gate)": RiderConfig(use_ny_hours=False, atr_win=1),
        "high-vol gate (ATR>median)": RiderConfig(use_ny_hours=False),
        "high-vol + NY hours": RiderConfig(use_ny_hours=True),
    }
    print(f"\n[REGIME CONFIGS] thrust>1.5xATR, trail sl/trail=$6, tp=$50, hold=24 bars")
    print(f"  {'config':30}{'n':>6}{'win%':>7}{'exp$/oz':>9}{'ctrl':>8}{'edge':>8}{'PF':>6}{'totR':>9}")
    chosen = None
    for name, cfg in configs.items():
        s, pnl = bt(df, cfg)
        print(f"  {name:30}{s['n']:>6}{s['win']:>7}{s['exp']:>9.3f}{s['ctrl']:>8.3f}"
              f"{s['edge']:>8.3f}{s['pf']:>6}{s['tot']:>9.1f}")
        if name.startswith("high-vol gate"):
            chosen = (cfg, pnl)

    # ---- cross-verify: live state machine vs the backtest engine ----
    cfg, bt_pnl = chosen
    live_pnl = drive_live(df, cfg)
    print(f"\n[CROSS-VERIFY] live RiderState.on_bar vs patternlib backtest (high-vol config):")
    print(f"  backtest: {len(bt_pnl)} trades, tot ${bt_pnl.sum():+.1f}/oz, exp ${bt_pnl.mean():+.3f}")
    print(f"  live    : {len(live_pnl)} trades, tot ${live_pnl.sum():+.1f}/oz, exp ${live_pnl.mean():+.3f}")
    dn = abs(len(live_pnl) - len(bt_pnl)); dtot = abs(live_pnl.sum() - bt_pnl.sum())
    ok = dn <= max(2, 0.03 * len(bt_pnl)) and dtot <= max(5.0, 0.10 * abs(bt_pnl.sum()))
    print(f"  match: trade-count diff {dn}, total diff ${dtot:.1f}  -> {'CONSISTENT' if ok else 'DRIFT - investigate'}")
    print("  (small diff expected: the live machine starts trailing management one bar")
    print("   after fill, vs the backtest checking the fill bar itself.)")

    # ---- monthly breakdown + sizing on a sample account ----
    print(f"\n[MONTHLY] high-vol config, edge-over-control per month:")
    idx = np.flatnonzero(entry_signal(df, cfg) != 0)
    months = df.index[idx].to_period("M")
    # per-month expectancy of the actual trades (approx: align pnl to entry bars)
    mvals = pd.Series(bt_pnl[:len(idx)], index=months[:len(bt_pnl)]) if len(bt_pnl) else pd.Series(dtype=float)
    for m, g in mvals.groupby(level=0):
        print(f"   {m}: n={len(g):>4}  exp=${g.mean():+.2f}/oz  tot=${g.sum():+.1f}")

    eq = 500.0
    lot = kelly_lot(eq, cfg)
    print(f"\n[SIZING] Kelly-small at ${eq:.0f} equity, ${cfg.sl}/oz stop, {cfg.risk_frac:.0%} risk/trade:")
    print(f"  suggested lot = {lot}  (risks ~${cfg.risk_frac*eq:.0f} = {cfg.risk_frac:.0%} of equity per trade)")
    print("\nDONE (Phase 0 backtest). Note: edge is marginal + regime-dependent -> rider_stress.py next.")


if __name__ == "__main__":
    main()

"""Faithful replay of the Volume-Spike Straddle trigger (XauOrderPad/strategies/straddle.py).

    ..\XauOrderPad\.venv\Scripts\python.exe straddle_replay.py [START END]   # YYYY-MM-DD

Replicates `straddle.py:_maybe_signal` exactly, on M1 bars pulled live from MT5:
  v      = M1 tick_volume
  base   = median(v[i-90 : i])            # rvol_window = 90
  rvol   = v[i] / base
  atr    = mean(TR, last 14 bars)         # atr_period = 14
  gate   : rvol >= 8.0  AND  atr > 0      # rvol_threshold = 8.0
  vol_filter (ON): require atr >= mean(TR, last ~60)   -- ATR expanding
  cooldown: 15 min between fired signals (max_concurrent = 1)

The fired list is what the live straddle would actually enter. Direction-agnostic (volume predicts
move size, not direction — see LEARNINGS_AND_FINDINGS.md §1, §8). Read-only: copy_rates only, no orders.
Requires the local MT5 terminal running + logged in, and the XauOrderPad server stopped.

NOTE: MT5 `copy_rates` 'time' is in SECONDS (not ms). Uses `tick_volume` = ground truth (the same
number the live strategy sees); do not reconstruct it from de-duped ticks.
"""
from __future__ import annotations

import datetime as dt
import sys

import numpy as np
import MetaTrader5 as mt5

RVOL_WIN, RVOL_THR, ATR_P, COOLDOWN_S = 90, 8.0, 14, 15 * 60

if len(sys.argv) >= 3:
    START = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
    END = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")
else:
    START, END = dt.datetime(2026, 8, 11), dt.datetime(2026, 8, 25)


def fmt(sec, off=0):
    return dt.datetime.fromtimestamp(sec + off, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe"):
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}  (terminal running? server stopped?)")
    sym = "XAUUSD"
    if mt5.symbol_info(sym) is None:
        sym = mt5.symbols_get("XAUUSD*")[0].name
    mt5.symbol_select(sym, True)
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, START, END)
    mt5.shutdown()
    if rates is None or len(rates) < RVOL_WIN + ATR_P:
        raise SystemExit("not enough M1 bars returned")

    t = rates["time"].astype("int64")            # SECONDS
    v = rates["tick_volume"].astype(float)
    h, l, c = rates["high"].astype(float), rates["low"].astype(float), rates["close"].astype(float)
    mid = (h + l) / 2.0
    n = len(t)
    print(f"{sym}: {n:,} M1 bars  {fmt(t[0])} -> {fmt(t[-1])} UTC")

    tr = np.zeros(n)
    tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(c[:-1] - l[1:])])

    rows = []
    for i in range(RVOL_WIN, n):
        base = np.median(v[i - RVOL_WIN:i])
        if base <= 0:
            continue
        rvol = v[i] / base
        atr = float(np.mean(tr[i - ATR_P + 1:i + 1])) if i >= ATR_P else 0.0
        if rvol < RVOL_THR or atr <= 0:
            continue
        ref = float(np.mean(tr[max(1, i - 60):i + 1]))          # vol_filter reference
        rows.append((int(t[i]), v[i], rvol, atr, atr >= ref, float(mid[i])))

    vol_ok = [r for r in rows if r[4]]
    fired, last = [], -1e18
    for r in vol_ok:
        if r[0] - last >= COOLDOWN_S:
            fired.append(r)
            last = r[0]

    print(f"rvol>=8 bars: {len(rows)}   pass ATR-expanding vol_filter: {len(vol_ok)}   "
          f"FIRED after {COOLDOWN_S // 60}-min cooldown: {len(fired)}")
    print("\n=== FIRED STRADDLE ENTRIES (what the live engine would take) ===")
    print(f"  {'#':>2} {'UTC':<17} {'UTC+4 chart':<17} {'tickvol':>8} {'rvol':>6} {'atr':>6} {'mid':>9}")
    for k, (ts, vv, rv, at, ok, m) in enumerate(fired, 1):
        print(f"  {k:>2} {fmt(ts):<17} {fmt(ts, 4 * 3600):<17} {vv:>8.0f} {rv:>6.1f} {at:>6.2f} {m:>9.2f}")


if __name__ == "__main__":
    main()

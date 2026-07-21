r"""
EDA data fetch -- pull XAUUSD bars (M1..D1) + a tick sample from MT5 to disk.

Run with the MT5 venv (has MetaTrader5); the EDA scripts then read the saved
.npz files with test_env (pandas), so analysis never needs a terminal attached
and can't clash with the trading server.

    ..\..\XauOrderPad\.venv\Scripts\python.exe fetch_data.py

STOP the XauOrderPad server first (one MT5 connection per terminal). Read-only.
"""
from __future__ import annotations

import datetime as dt
import os

import numpy as np
import MetaTrader5 as mt5

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)

# Per-TF history: deeper for high TFs (S/R context), feed-limited for low TFs.
NOW = dt.datetime.now()
TF_SPEC = {
    "D1":  (mt5.TIMEFRAME_D1,  dt.datetime(2023, 1, 1)),
    "H4":  (mt5.TIMEFRAME_H4,  dt.datetime(2024, 1, 1)),
    "H1":  (mt5.TIMEFRAME_H1,  dt.datetime(2025, 6, 1)),
    "M15": (mt5.TIMEFRAME_M15, dt.datetime(2026, 1, 1)),
    "M5":  (mt5.TIMEFRAME_M5,  dt.datetime(2026, 1, 1)),
    "M1":  (mt5.TIMEFRAME_M1,  dt.datetime(2026, 2, 1)),
}
TICK_SAMPLE_DAYS = 14


def _attach():
    for p in (r"C:\Program Files\MetaTrader 5\terminal64.exe", None):
        if (mt5.initialize(path=p) if p else mt5.initialize()):
            return
    raise SystemExit(f"MT5 INIT_FAIL: {mt5.last_error()} (terminal running? server stopped?)")


def _fetch_rates(symbol, tf, start, end, chunk_days=25):
    """Chunked pull (long M1 spans exceed the single-call cap), de-duplicated."""
    parts, a = [], start
    while a < end:
        b = min(end, a + dt.timedelta(days=chunk_days))
        r = mt5.copy_rates_range(symbol, tf, a, b)
        if r is not None and len(r):
            parts.append(r)
        a = b
    if not parts:
        return None
    allr = np.concatenate(parts)
    _, idx = np.unique(allr["time"], return_index=True)
    return allr[idx]


def main():
    _attach()
    symbol = "XAUUSD"
    if mt5.symbol_info(symbol) is None:
        for s in (mt5.symbols_get("XAUUSD*") or []):
            symbol = s.name
            break
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    meta = dict(symbol=symbol, point=float(info.point),
                contract=float(info.trade_contract_size), fetched=NOW.isoformat())
    print(f"symbol={symbol} point={info.point} contract={info.trade_contract_size}")

    for name, (tf, start) in TF_SPEC.items():
        r = _fetch_rates(symbol, tf, start, NOW)
        if r is None:
            print(f"  {name}: no data"); continue
        path = os.path.join(OUT, f"bars_{name}.npz")
        np.savez_compressed(
            path, time=r["time"].astype("int64"),
            open=r["open"].astype(float), high=r["high"].astype(float),
            low=r["low"].astype(float), close=r["close"].astype(float),
            tick_volume=r["tick_volume"].astype(float),
            spread=(r["spread"].astype(float) if "spread" in r.dtype.names else np.zeros(len(r))),
            **{k: np.array([v]) for k, v in meta.items() if k != "fetched"})
        d0 = dt.datetime.fromtimestamp(int(r["time"][0]))
        d1 = dt.datetime.fromtimestamp(int(r["time"][-1]))
        print(f"  {name:4} {len(r):>7,} bars  {d0:%Y-%m-%d} -> {d1:%Y-%m-%d}  -> {os.path.basename(path)}")

    # tick sample (microstructure / bid-ask bounce)
    ts_start = NOW - dt.timedelta(days=TICK_SAMPLE_DAYS)
    parts, a = [], ts_start
    while a < NOW:
        b = min(NOW, a + dt.timedelta(days=1))
        tk = mt5.copy_ticks_range(symbol, a, b, mt5.COPY_TICKS_ALL)
        if tk is not None and len(tk):
            parts.append(tk)
        a = b
    if parts:
        allt = np.concatenate(parts)
        _, idx = np.unique(allt["time_msc"], return_index=True)
        allt = allt[idx]
        path = os.path.join(OUT, "ticks_sample.npz")
        np.savez_compressed(path, t_msc=allt["time_msc"].astype("int64"),
                            bid=allt["bid"].astype(float), ask=allt["ask"].astype(float),
                            point=np.array([float(info.point)]))
        print(f"  TICKS {len(allt):>7,} ticks (last {TICK_SAMPLE_DAYS}d) -> {os.path.basename(path)}")

    mt5.shutdown()
    print(f"\nsaved to {OUT}")


if __name__ == "__main__":
    main()

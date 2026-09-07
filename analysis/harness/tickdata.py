"""
Tick data sources for the harness.

Two ways to get a ``Ticks`` object, and ONLY this module knows about MetaTrader5:

  * ``load_ticks``  -- live/historical pull from MT5 (chunked; the feed caps a
    single ``copy_ticks_range`` call, and long spans must be de-duplicated).
  * ``synth_ticks`` -- deterministic synthetic stream, **no MT5**, so tests and
    demos run with no terminal attached (mirrors how v2 tests with a fake
    data client).

Operational note: MT5 allows one clean connection per terminal. If the
XauOrderPad server is running it already owns that connection -- STOP it before
a live pull, or you get a -10004 IPC clash. Read-only either way; this module
never trades.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from .core import Ticks

_MT5_PATHS = (r"C:\Program Files\MetaTrader 5\terminal64.exe", None)


def _attach():
    import MetaTrader5 as mt5  # local import: keeps the module offline-importable

    for p in _MT5_PATHS:
        ok = mt5.initialize(path=p) if p else mt5.initialize()
        if ok:
            return mt5
    raise SystemExit(f"MT5 INIT_FAIL: {mt5.last_error()}  (is the terminal running? server stopped?)")


def _resolve_symbol(mt5, symbol: str) -> str:
    if mt5.symbol_info(symbol) is None:
        for s in (mt5.symbols_get(f"{symbol}*") or []):
            symbol = s.name
            break
    mt5.symbol_select(symbol, True)
    return symbol


def load_ticks(symbol: str = "XAUUSD", start: dt.datetime | None = None,
               end: dt.datetime | None = None, chunk_days: int = 1) -> Ticks:
    """Pull [start, end) ticks from MT5 in day-sized chunks, de-duplicated.

    Defaults to the last 45 days. Bid/ask only -- ``last``/``volume`` are 0 on
    this CFD feed, so we don't carry them.
    """
    mt5 = _attach()
    end = end or dt.datetime.now()
    start = start or (end - dt.timedelta(days=45))
    symbol = _resolve_symbol(mt5, symbol)
    info = mt5.symbol_info(symbol)
    point = float(info.point)
    contract = float(info.trade_contract_size)

    parts, a = [], start
    while a < end:
        b = min(end, a + dt.timedelta(days=chunk_days))
        r = mt5.copy_ticks_range(symbol, a, b, mt5.COPY_TICKS_ALL)
        if r is not None and len(r):
            parts.append(r)
        a = b
    mt5.shutdown()

    if not parts:
        raise SystemExit(f"no ticks for {symbol} in {start:%Y-%m-%d}..{end:%Y-%m-%d}")
    allr = np.concatenate(parts)
    # Sort by millisecond timestamp, then drop ONLY exact-duplicate ticks (same
    # time_msc AND bid AND ask AND flags) -- which is all the day-chunk boundary
    # overlap produces. Deduping on time_msc ALONE (the old approach) also threw
    # away genuinely-distinct ticks that share a millisecond (common on gold), so
    # per-minute counts ran ~4% below MT5's tick_volume and flipped rvol-threshold
    # bars in the straddle replay. Full-identity dedup keeps those ticks.
    order = np.lexsort((allr["flags"], allr["ask"], allr["bid"], allr["time_msc"]))
    allr = allr[order]
    dup = ((np.diff(allr["time_msc"]) == 0) & (np.diff(allr["bid"]) == 0) &
           (np.diff(allr["ask"]) == 0) & (np.diff(allr["flags"]) == 0))
    allr = allr[np.concatenate(([True], ~dup))]
    return Ticks(
        symbol=symbol, point=point, contract=contract,
        t_msc=allr["time_msc"].astype("int64"),
        bid=allr["bid"].astype(float), ask=allr["ask"].astype(float),
    )


def synth_ticks(n: int = 60_000, seed: int = 7, symbol: str = "XAUUSD",
                point: float = 0.01, spread_pts: float = 4.0,
                start_price: float = 4000.0, burst_every: int = 2500,
                burst_len: int = 60, dt_ms: int = 200) -> Ticks:
    """Deterministic synthetic tick stream for offline tests/demos.

    A gaussian random-walk mid with periodic momentum *bursts* (a run of
    same-signed drift) so a momentum model has something real to detect. The
    burst direction alternates, so neither a pure-follow nor a pure-fade model
    can win by luck -- exactly the property we want a faithful backtest to expose.
    Fixed spread, ~5 ticks/sec, matching the live feed's characteristics.
    """
    rng = np.random.default_rng(seed)
    half = spread_pts * point / 2.0
    step = np.zeros(n)
    base = rng.normal(0.0, 0.02, n)  # ~2 cents/tick baseline noise
    k = 0
    while k < n:
        if k % burst_every < burst_len:
            drift = 0.05 * (1 if (k // burst_every) % 2 == 0 else -1)  # alternating bursts
            base[k] += drift
        k += 1
    mid = start_price + np.cumsum(base)
    t_msc = (np.arange(n, dtype="int64") * dt_ms) + 1_700_000_000_000
    return Ticks(
        symbol=symbol, point=point, contract=100.0, t_msc=t_msc,
        bid=mid - half, ask=mid + half,
    )

"""
Export a raw XAUUSD(m) tick dataset to a folder: raw_ticks.csv + a generated README.

    ..\XauOrderPad\.venv\Scripts\python.exe export_ticks.py [YYYY-MM-DD YYYY-MM-DD]
    # default window: 2026-08-11 .. 2026-08-21 (matches the tick_profiler run)

Pulls raw ticks live from the local MT5 terminal via copy_ticks_range (read-only,
never trades), de-duplicated on time_msc, and writes a self-describing dataset
folder under analysis/tick_data/.

Requires: local MT5 terminal running + logged in; XauOrderPad server STOPPED
(one MT5 connection per terminal, else -10004 IPC clash).
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness.tickdata import load_ticks  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Default to the exact window the profiler reported on.
if len(sys.argv) >= 3:
    START = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
    END = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")
else:
    START = dt.datetime(2026, 8, 11)
    END = dt.datetime(2026, 8, 21)


def utc(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000.0, dt.timezone.utc)


def main() -> None:
    print(f"Pulling raw ticks XAUUSD  {START:%Y-%m-%d} -> {END:%Y-%m-%d} ...")
    tk = load_ticks("XAUUSD", START, END, chunk_days=1)   # read-only; stops the MT5 conn on exit
    n = tk.n
    if n < 1000:
        raise SystemExit(f"only {n} ticks pulled -- is MT5 logged in and the window a trading period?")

    t = tk.t_msc.astype("int64")
    bid, ask, mid, spread = tk.bid, tk.ask, tk.mid, tk.spread
    t0, t1 = utc(t[0]), utc(t[-1])
    span_h = (t[-1] - t[0]) / 3_600_000.0
    ticks_per_s = n / max(span_h * 3600, 1)
    lo, hi = float(mid.min()), float(mid.max())

    folder = os.path.join(HERE, "tick_data",
                          f"xauusdm_{START:%Y%m%d}_{END:%Y%m%d}")
    os.makedirs(folder, exist_ok=True)
    csv_path = os.path.join(folder, "raw_ticks.csv")
    readme_path = os.path.join(folder, "README.md")

    # ---- raw_ticks.csv ------------------------------------------------------
    print(f"Writing {n:,} ticks -> {csv_path} ...")
    with open(csv_path, "w", encoding="ascii", newline="") as f:
        f.write("time_msc,iso_time_utc,bid,ask,mid,spread\n")
        # build lines in a vector then join -- ~2M rows, avoid per-row f-string cost
        iso = [utc(int(x)).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(x) % 1000:03d}Z" for x in t]
        CH = 100_000
        for s in range(0, n, CH):
            e = min(n, s + CH)
            buf = []
            for i in range(s, e):
                buf.append(f"{t[i]},{iso[i]},{bid[i]:.3f},{ask[i]:.3f},"
                           f"{mid[i]:.4f},{spread[i]:.3f}")
            f.write("\n".join(buf) + "\n")
    size_mb = os.path.getsize(csv_path) / 1e6

    # ---- README.md (generated so numbers are exact) -------------------------
    readme = f"""# XAUUSDm raw tick dataset — {START:%Y-%m-%d} to {END:%Y-%m-%d}

## What this is
Raw **bid/ask tick stream** for gold (`XAUUSDm`) from the **Exness demo** feed, pulled live from
MetaTrader 5 via `copy_ticks_range(..., COPY_TICKS_ALL)`, de-duplicated on `time_msc` and sorted.
Every quote update the broker published in the window is here — this is the finest-grained,
most realistic data this feed offers.

## The volume caveat (read this)
This is a **CFD** feed: it carries **no traded volume and no last price**. Every tick has
`last = 0` and `real_volume = 0`, so those columns are omitted. **Only bid/ask move.** Any
"volume" in the analysis is therefore a *proxy*:
- **tick arrival rate** (how many quote updates per unit time) — the activity/"volume" proxy;
- **tick count per price level** — the volume-profile proxy.

## Which strategy / algorithm this feeds
`analysis/tick_profiler.py` — a **tick-level volume profiler**, run directly on these raw ticks:
1. **Tick-rate volume anomaly** — ticks in a trailing 60 s window, deseasonalized by minute-of-day,
   flagged when RVOL ≥ 4 / 6 / 8 (activity spikes).
2. **Tick volume profile** — histogram of tick counts per $0.10 price bucket → POC / VAH / VAL
   (70% value area), per day and overall.

It is **direction-agnostic** on purpose: this repo's walk-forward found volume/velocity predicts the
**size** of the next move (corr ≈ 0.71–0.74), **not its direction**. The signals mark *where activity
concentrated*, not buy/sell. Signals for this window are in the sibling file
`../../tick_signals_{START:%Y%m%d}_{END:%Y%m%d}.csv`.

## Date range & stats (this snapshot)
| | |
|---|---|
| Symbol | `XAUUSDm` (Exness demo; requested as `XAUUSD`, auto-resolved) |
| Requested window | {START:%Y-%m-%d} 00:00 → {END:%Y-%m-%d} 00:00 (local pull) |
| Actual data span (UTC) | {t0:%Y-%m-%d %H:%M:%S} → {t1:%Y-%m-%d %H:%M:%S} |
| Duration | {span_h:.1f} h (~{span_h/24:.1f} days; weekends have no ticks) |
| Tick count | {n:,} |
| Avg rate | {ticks_per_s:.2f} ticks/s |
| Mid-price range | {lo:.3f} → {hi:.3f} USD/oz |
| File size | {size_mb:.1f} MB |

## Columns (`raw_ticks.csv`)
| column | meaning |
|---|---|
| `time_msc` | broker tick time, Unix epoch **milliseconds** (UTC) |
| `iso_time_utc` | same instant as ISO-8601 UTC, millisecond precision (`...Z`) |
| `bid` | best bid, USD/oz |
| `ask` | best ask, USD/oz |
| `mid` | `(bid + ask) / 2` — the entire price signal on this feed |
| `spread` | `ask − bid`, USD/oz (demo ≈ 0.04–0.05) |

## Provenance / how to regenerate
```
cd analysis
..\\XauOrderPad\\.venv\\Scripts\\python.exe export_ticks.py {START:%Y-%m-%d} {END:%Y-%m-%d}
```
- Read-only: only `mt5.initialize` + `copy_ticks_range`. **No orders placed.**
- **Point-in-time snapshot.** MT5's tick history is a rolling window on the broker's side, so the
  broker may not return this exact range later. This file is the saved copy.
- Requires the local MT5 terminal running + logged into the Exness demo, and the XauOrderPad
  server stopped (one MT5 connection per terminal).
"""
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)

    print(f"Wrote README -> {readme_path}")
    print("\n=== DATASET SAVED ===")
    print(f"  folder : {folder}")
    print(f"  ticks  : {n:,}  ({t0:%Y-%m-%d %H:%M} -> {t1:%Y-%m-%d %H:%M} UTC, {span_h:.1f}h)")
    print(f"  csv    : {size_mb:.1f} MB")


if __name__ == "__main__":
    main()

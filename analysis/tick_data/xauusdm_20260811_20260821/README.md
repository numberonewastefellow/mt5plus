# XAUUSDm raw tick dataset — 2026-08-11 to 2026-08-21

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
`../../tick_signals_20260811_20260821.csv`.

## Date range & stats (this snapshot)
| | |
|---|---|
| Symbol | `XAUUSDm` (Exness demo; requested as `XAUUSD`, auto-resolved) |
| Requested window | 2026-08-11 00:00 → 2026-08-21 00:00 (local pull) |
| Actual data span (UTC) | 2026-08-10 18:30:00 → 2026-08-20 18:29:59 |
| Duration | 240.0 h (~10.0 days; weekends have no ticks) |
| Tick count | 2,191,140 |
| Avg rate | 2.54 ticks/s |
| Mid-price range | 4310.957 → 4540.998 USD/oz |
| File size | 160.0 MB |

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
..\XauOrderPad\.venv\Scripts\python.exe export_ticks.py 2026-08-11 2026-08-21
```
- Read-only: only `mt5.initialize` + `copy_ticks_range`. **No orders placed.**
- **Point-in-time snapshot.** MT5's tick history is a rolling window on the broker's side, so the
  broker may not return this exact range later. This file is the saved copy.
- Requires the local MT5 terminal running + logged into the Exness demo, and the XauOrderPad
  server stopped (one MT5 connection per terminal).

# XAUUSDm raw tick dataset — 2026-08-11 to 2026-08-25

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
`../../tick_signals_20260811_20260825.csv`.

## Date range & stats (this snapshot)
| | |
|---|---|
| Symbol | `XAUUSDm` (Exness demo; requested as `XAUUSD`, auto-resolved) |
| Requested window | 2026-08-11 00:00 → 2026-08-25 00:00 (local pull) |
| Actual data span (UTC) | 2026-08-10 18:30:00 → 2026-08-24 15:28:27 |
| Duration | 333.0 h (~13.9 days; weekends have no ticks) |
| Tick count | 2,824,254 |
| Avg rate | 2.36 ticks/s |
| Mid-price range | 4310.957 → 4681.087 USD/oz |
| File size | 206.2 MB |

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
..\XauOrderPad\.venv\Scripts\python.exe export_ticks.py 2026-08-11 2026-08-25
```
- Read-only: only `mt5.initialize` + `copy_ticks_range`. **No orders placed.**
- **Point-in-time snapshot.** MT5's tick history is a rolling window on the broker's side, so the
  broker may not return this exact range later. This file is the saved copy.
- Requires the local MT5 terminal running + logged into the Exness demo, and the XauOrderPad
  server stopped (one MT5 connection per terminal).

---

## Replay runs against this file

This dataset is the **walk-forward set** — 13 sessions, and the only numbers in this project with
evidential weight (the single-day windows are favourable fixtures chosen after the fact).

Every run against it — date, parameters, commands, results and caveats — is recorded in
**`D:\llm\ios\xausd_video_out\REPLAY_RUNS.md`** (runs 1, 2 and the held-out arm of run 3).

⚠️ `grid_replay_matrix.csv` was generated from this file on **2026-08-29 with `per_rung = 1`**.
The engine default is now `3`, so re-running the same command does not reproduce it. Pass
`Engine(per_rung=1)` to reproduce.

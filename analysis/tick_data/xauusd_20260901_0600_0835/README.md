# XAUUSD tick data — 2026-09-01 06:00:00 → 08:34:59 UTC

Raw bid/ask ticks pulled from the live Exness MT5 feed with `copy_ticks_range(..., COPY_TICKS_ALL)`,
sorted and de-duplicated. Read-only pull; nothing was traded.

## The window

| | |
|---|---|
| Symbol | `XAUUSD` (Exness, point `0.001`, contract 100 oz, min lot 0.01) |
| Span | 2026-09-01 **06:00:00 → 08:34:59 UTC** (155.0 min) |
| Ticks | **43,172** — avg **4.6/s** |
| Price | open **4440.273**, close **4374.943** — **−$65.33** |
| Range | 4372.683 – 4440.756 = **$68.07** |
| Spread | median **$0.050**, p90 $0.050 |
| File | `raw_ticks.csv`, 3.2 MB |

**This is an unusually strong one-directional window** — a $65 fall in 155 minutes, against a median
daily range of $86 across the Aug 10–24 sessions. Almost the whole day's typical movement, in one
direction, in 2.5 hours. Any short-biased strategy profits here; that is a property of the window,
not of the strategy. See the warning below.

## Columns

`time_msc` (unix ms) · `iso_time_utc` · `bid` · `ask` · `mid` · `spread`

## The timezone trap

`copy_ticks_range` reads a **naive** datetime as **local time**, not UTC. A naive `Sep 1 06:00`
returns ticks from `Sep 1 00:30Z` on this machine (UTC+5:30). This file was pulled with tz-aware
UTC (`dt.datetime(2026,9,1,6,0,tzinfo=dt.UTC)`) and the `iso_time_utc` column confirms the range.

## ⚠️ What this data was used for, and what it showed

It was used to replay and tune the reconstructed grid (`analysis/video_ocr/grid_replay.py`).
**The result is a textbook in-sample/out-of-sample gap and must not be quoted without it:**

| config | tuned on this window | Aug 10–24 (13 sessions) | profitable sessions |
|---|---|---|---|
| best tuned | **+5,348%** | **−6.7%** mean | 3 / 13 |
| 2nd | +475.7% | −12.6% | 3 / 13 |
| 3rd | +389.2% | −8.7% | 5 / 13 |
| baseline | +3.4% | −13.7% | 3 / 13 |

**84 of 100 swept configs were profitable on this window.** That is the trend, not skill.

For scale: **a single short held through the window, with no grid, no adds and no exit logic,
returns +215% at 0.33 lot and +646% at 0.99 lot.** The move is the result.

Use this file as a **trend-day fixture** — it is good for testing that an engine handles a fast
one-way move — and never as evidence that a strategy works.

Full provenance for every run against this file — date, parameters, commands, results and caveats —
is in **`D:\llm\ios\xausd_video_out\REPLAY_RUNS.md`** (run 3).

## Reproduce

```python
import datetime as dt, MetaTrader5 as mt5
mt5.initialize(); mt5.symbol_select("XAUUSD", True)
r = mt5.copy_ticks_range("XAUUSD",
        dt.datetime(2026, 9, 1, 6, 0,  tzinfo=dt.UTC),
        dt.datetime(2026, 9, 1, 8, 35, tzinfo=dt.UTC),
        mt5.COPY_TICKS_ALL)
```

Stop the XauOrderPad server first — MT5 allows one clean IPC connection per terminal, and a running
server owns it (`-10004`).

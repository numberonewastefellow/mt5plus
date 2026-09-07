# XAUUSD tick data — 2026-09-02 00:00:00 → 14:13:48 UTC

Raw bid/ask ticks from the live Exness MT5 feed (`copy_ticks_range(..., COPY_TICKS_ALL)`), sorted
and de-duplicated. Read-only pull; nothing was traded.

## The window

| | |
|---|---|
| Symbol | `XAUUSD` (Exness, point `0.001`, contract 100 oz, min lot 0.01) |
| Span | 2026-09-02 **00:00:00 → 14:13:48 UTC** (14.23 h) |
| Ticks | **247,349** — avg **4.8/s** |
| Open | **4324.793** @ 00:00 |
| Low | **4282.332** @ 03:35 — a **$42.46 fall first** |
| High | **4397.872** @ 14:10 — then a **$115.54 rise** |
| Last | 4388.618 — net **+$63.83** |
| Spread | median **$0.050** |
| File | `raw_ticks.csv`, 18.3 MB |

**The shape is what makes this window valuable.** Unlike the near-straight-line Sep 1 fall, this
one drops $42 *before* the move that pays a long. That is precisely the condition a recovery grid
claims to survive — and the condition it must survive to be worth anything.

## ⭐ This is the project's only TRUE FORWARD TEST

Every other window already existed when parameters were chosen. **This data did not exist when the
Sep 1 tuning was done**, so it cannot have leaked into any fitting decision.

**No parameter was fitted to this file, and none should be.** Tuning on it destroys the only
uncontaminated test available.

## The result — every configuration lost money on a rising market

Configs applied **unchanged** from `sep1_tuning_oos.csv`, $1,000 start:

| config | Sep 1 (tuned on) | **Sep 2 buy** | cycles | worst dd | outcome |
|---|---|---|---|---|---|
| tuned_1 (44 / 0.10 / 1 / dual) | **+5,348.6%** | **−45.5%** | **0** | 36.2% | parked |
| tuned_2 (44 / 0.18 / 1) | +475.7% | −23.5% | 1 | 38.2% | parked |
| tuned_3 (30 / 0.50 / 3) | +389.2% | −34.1% | **0** | 29.0% | parked |
| baseline (44 / 0.18 / 3) | +3.4% | −40.3% | **0** | 37.1% | parked |

**Benchmark — one buy held through the window, no grid, no adds, no exit logic:**

| | return |
|---|---|
| 0.33 lot | **+210.8%** |
| 0.99 lot | **+632.4%** |

**A ~250-point gap in favour of doing nothing clever.**

### Why it failed, precisely

**3 of 4 buy configs completed ZERO cycles.** They opened into the $42.46 drawdown, exhausted their
depth budget, PARKED — and were still parked through the entire $115.54 rally. The grid did not
lose *because* the market went against it; it lost because it was **frozen out of the move it was
right about**.

The sell side is the tell: on a *rising* day, `baseline sell` (−6.9%) and `tuned_3 sell` (+0.1%)
both **beat every buy config**, because the opening fall let them close a cycle or two before
parking. Being on the correct side of a $63 move was *worse* than being on the wrong one.

## Columns

`time_msc` (unix ms) · `iso_time_utc` · `bid` · `ask` · `mid` · `spread`

## The timezone trap

`copy_ticks_range` reads a **naive** datetime as **local**, not UTC. This file was pulled with
tz-aware UTC (`dt.datetime(2026,9,2,0,0,tzinfo=dt.UTC)`); `iso_time_utc` confirms the range.

## Reproduce

```python
import datetime as dt, MetaTrader5 as mt5
mt5.initialize(); mt5.symbol_select("XAUUSD", True)
r = mt5.copy_ticks_range("XAUUSD",
        dt.datetime(2026, 9, 2, 0, 0,  tzinfo=dt.UTC),
        dt.datetime(2026, 9, 2, 14, 14, tzinfo=dt.UTC),
        mt5.COPY_TICKS_ALL)
```

Full provenance — parameters, commands, caveats — is in
**`D:\llm\ios\xausd_video_out\REPLAY_RUNS.md`** (run 4). Per-config results:
`sep2_forward_test.csv`.

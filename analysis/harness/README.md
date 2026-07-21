# `analysis/harness/` — XAUUSD tick-strategy harness (research only)

A small, testable engine for building tick strategies and judging them
**honestly**. It is the `AlphaModel → Signal` pattern from the
[ai-hedge-fund/v2](https://github.com/virattt/ai-hedge-fund/tree/main/v2) study,
ported to this repo's reality: one instrument (XAUUSD), **tick** data, numpy-only,
no pandas/pydantic, and MetaTrader5 isolated so the whole thing tests offline.

> **It never trades.** No function here places, modifies, or closes an order —
> on any account, demo or real. A model emits a *view*; a human keeps the
> trigger. This is the repo's standing hard rule (see `../../CLAUDE.md`).

## Why it exists / what the feed actually is

Live read-only probing of the Exness demo feed (472200942 / Exness-MT5Trial16)
established the constraints that shape everything here:

- **No order book** — `market_book_add` = False, 0 depth (market-maker feed).
- **No real volume** — every tick has `volume=0, last=0`, flags `6` (bid+ask only,
  no aggressor). The only tick-level information is the **bid/ask path**, so every
  feature derives from `mid` and its **velocity**.
- **An LLM is too slow to be a tick trigger** (~seconds vs ~200ms ticks). The
  trigger is deterministic; AI's value is research + a slow regime gate, later.
- Prior bar-level direction research (`../`) **lost out-of-sample** (M1 −15.9%,
  M5 −15.4%); only volume→*magnitude* held. So direction must be proven, not assumed.

## The strategy in the box: `TickMomentum`

"Price moving fast → act on it", as a fast deterministic rule with two modes the
walk-forward chooses between:

- **follow** — trade *with* the velocity spike (momentum/continuation).
- **fade** — trade *against* it (climactic/exhaustion reversal).

Conviction is **graded** (`±tanh(vel_z/k)`), the upgrade over the old discrete
−1/0/+1. Add your own model by implementing `predict(feats, i) -> Signal` in
`models.py` — it plugs into the same backtest unchanged.

## Layout

| File | Role |
|---|---|
| `core.py` | `Signal`, `AlphaModel` protocol, `Ticks` — the shared contract |
| `tickdata.py` | `load_ticks` (live MT5, chunked) · `synth_ticks` (offline fixture). **Only file that imports MetaTrader5.** |
| `features.py` | mid, velocity, rolling vol scale, `vel_z`, quote-rate, spike candidates — pure numpy, point-in-time |
| `models.py` | `TickMomentum(mode=follow\|fade)` |
| `backtest.py` | tick trade sim (spread in the fills, one-position-at-a-time) + rolling walk-forward + summary |
| `run.py` | live driver — pull ticks → features → walk-forward → print the honest OOS result |
| `test_harness.py` | offline test on synthetic ticks — **no terminal needed** |

## Run

```bat
:: offline correctness test (no MT5 needed)
..\..\XauOrderPad\.venv\Scripts\python.exe test_harness.py

:: honest walk-forward on live tick history (STOP the XauOrderPad server first)
..\..\XauOrderPad\.venv\Scripts\python.exe run.py                 :: last 30 days
..\..\XauOrderPad\.venv\Scripts\python.exe run.py 2026-06-01 2026-07-19
```

Stop the server before a live pull — MT5 allows one clean connection per
terminal; a second attach clashes (`-10004`).

## Honesty guardrails (the whole point)

- **Walk-forward only.** Config is fit on a past window and traded on the next
  unseen one. A single-window "best" is treated as in-sample noise — the exact
  trap that turned a mirage +91% into a real −16% in the prior research.
- **Spread paid every trade.** Enter at the far side, exit at the near side —
  one full spread per round trip, baked into the fills.
- **One position at a time.** No overlapping-signal P&L inflation.
- **Point-in-time.** `predict(feats, i)` sees only ticks ≤ i.
- **It will tell you if it loses.** A faithful "no edge" is a valid result.

## Where this goes next

1. If an edge survives here → move `TickMomentum` into the XauOrderPad server so
   live ticks produce a **suggestion card** (side, lot, SL, TP, reasoning) → you
   tap to place. Execution stays yours.
2. Likelier: no durable directional edge → try the **direction-agnostic breakout
   bracket** (profit from move *size*, the one thing that held up), or an **AI
   regime gate** (slow LLM context deciding *whether* the fast trigger may fire).

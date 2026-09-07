# Straddle-Ladder — plain-language model, worked cases, and the one honest caveat

A DEMO-ONLY engine (`strategies/straddle_ladder.py`, ID `sladder`, magic **532030**). Separate from
`straddle` and `ladder`; touches neither. Read this before changing defaults; the replay is
`analysis/sladder_replay.py`.

## §0 — What it is, in one breath

You set a **level** by hand (the ladder's UX). The engine opens a **straddle** there — one long and
one short, each ±sl/±tp — **only to detect direction**. What it does *after* that is a toggle,
**`always_straddle`**:

- **`always_straddle = False` (DEFAULT) — single-leg trend continuation.** From the 2nd order it
  takes **one leg on the trend side**; each order arms at the **last winning TP ± gap** (a TP
  advances, an SL retries the same level). It rides the trend, +tp per order.
- **`always_straddle = True` — walking straddle grid.** Every entry is a straddle, stepping gap past
  each winner's TP; re-detects direction every step but nets ~0 per step.

Units are **$/oz**, like the ladder's `target` (2 = $2.00, 4050→4052) — never MT5 points.

## §1 — The opening straddle (both modes)

With `sl == tp` the first $tp move resolves it at one price:

```text
level 4050, sl 2, tp 2:
  long  4050 -> sl 4048 / tp 4052
  short 4050 -> sl 4052 / tp 4048
price 4052 -> long tp (+2) AND short sl (-2)  => UP,   last winning TP = 4052
price 4048 -> short tp (+2) AND long sl (-2)  => DOWN, last winning TP = 4048
```

The straddle nets ~0 (winner +tp, loser −sl); its only job is to spend a bounded cost to find the
direction.

## §2 — DEFAULT: single-leg trend continuation

From the 2nd order it takes **one leg on the trend side**. Each order arms at **`last winning TP ±
gap`**. A **TP advances** the level; an **SL retries the SAME level** (it does not step off the stop
— it waits for price to return to the last winning level):

```text
UP, last winning TP 4052:
  order2 LONG 4053 (=4052+gap) sl 4051 tp 4055 -> TP 4055 (+2)   last TP 4055
  order3 LONG 4056 (=4055+gap) sl 4054 tp 4058 -> TP 4058 (+2)   last TP 4058
  order4 LONG 4059 (=4058+gap) sl 4057 tp 4061 -> SL 4057 (-2)   last TP 4058 (unchanged)
  order5 LONG 4059 (=4058+gap, RETRY same) sl 4057 tp 4061 -> TP 4061 (+2)  last TP 4061
  order6 LONG 4062 (=4061+gap) ...
```

Because an order only fires when price **crosses** its level, a sustained reversal walks price away
from the retry level and it simply **idles** — no chasing, no bleed. Down-trend is the mirror (single
shorts; `last winning TP − gap`). At most **one order open at a time** (sequential).

## §3 — Toggle: `always_straddle = True` (walking straddle grid)

Every entry is a straddle, stepping gap past each winner's TP:

```text
UP: straddle 4050 -> resolves up (win 4052) -> straddle 4053 -> resolves up -> straddle 4056 -> ...
```

Each step re-detects direction (no locked direction to bleed against), so a reversal just walks the
grid back — but each straddle nets ~0, so it does not compound a trend. The choice: single-leg rides
trends (and idles on reversals); walking-grid never bleeds (and never compounds).

## §4 — Caps, gates, and the honest replay

- **`max_legs`** (default 10) — ENTRIES per run (the initial straddle counts as one, each order as
  one). At the cap the engine **parks** + chimes; a new SET LEVEL resets it.
- `max_lots` (default 0 = off) — total open-lots ceiling (a straddle is 2·volume).
- `max_daily_loss` (default $200) — the inherited kill-switch.
- `allows_real = False` (auto-disables off a demo account, every poll); `needs_hedging = True` (the
  straddle holds both legs; a netting account is refused). Same guard as the ladder — `_guard_check`
  refuses `sl`/`tp` inside the live spread; both clients render `guard_ok`/`guard_reason`/
  `would_fire_now` server-side.

`analysis/sladder_replay.py` runs the exact decision core over recorded ticks with a broker that
fills each leg's bracket the way MT5 does. Armed at an **arbitrary** level (no skill), it nets ~one
spread negative per order — the take-profit sits a spread *farther* than the stop, so it is hit less
often (leg-win < 50%). That is the whole finding: **at a random level it is a faithful executor, not
an edge.** The only thing that beats the spread is the operator's **level**, which cannot be
backtested. On the real EC2 demo feed (spread ~0.04) it sits near breakeven; on the wider 0.24 spread
it is clearly negative. Run `python analysis/sladder_replay.py <ec2_ticklog.csv>`; if a random level
ever goes clearly **positive**, the engine has drifted — investigate.

## §5 — Where the code lives

- Engine + pure core: `XauOrderPad/strategies/straddle_ladder.py` (`StraddleLadder` + `StraddleGridState`).
- Defaults: `config.STRADDLE_LADDER_DEFAULTS` (`always_straddle=False`); magic `STRATEGY_MAGICS["sladder"]`.
- Unit tests (both modes): `testing/test_straddle_grid_state.py`. Wiring tests:
  `testing/test_straddle_ladder_engine.py`.
- Clients: `webui/strategy.js` + `#sladderPanel` (toggle `slAlwaysStraddle`); Android `QuickPanel.kt`
  `SladderQuick` + `Frames.kt` (`StrategyParams.alwaysStraddle`).
- Replay: `analysis/sladder_replay.py`.

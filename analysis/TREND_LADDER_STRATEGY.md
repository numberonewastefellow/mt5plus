# Trend-Ladder Strategy — design, worked cases, and what the ticks say

Reference doc for the "arm a trigger, pyramid into the move, exit on a retrace" strategy.
Everything here was **measured**, not assumed. Method is given for each number so it can be
re-run, and the worked examples exist so the arithmetic is never argued about again.

Companion to [LEARNINGS_AND_FINDINGS.md](LEARNINGS_AND_FINDINGS.md) — this hits **the same wall**
that document already describes ("volume predicts SIZE, not direction"; "expectancy ≈ −1 spread per
symmetric trade"). It arrives there from a completely different direction, which is itself the
strongest evidence the wall is real.

---

## 1. The rule

| | |
|---|---|
| **Arm** | Pick a side and a trigger price. *"SELL if it goes below 4119."* |
| **Enter** | Once triggered, keep adding positions while price moves your way (down for SELL). Rate configurable — e.g. 5/sec. |
| **Target** | Each position closes at **+$1.00/oz** (1000 points) or **+$2.00** (2000 points). |
| **Stop** | The **retrace**: price pulls back X from the extreme → close out. |

The trigger is **discretionary** — a human directional call. Hold that thought; by §7 it is the
only part that matters.

---

## 2. Fill mechanics — the thing that decides everything

**There is no LTP on XAUUSDm.** `tick.last = 0.0` on **8,483 of 8,483** ticks sampled at the NY
open. `trade_calc_mode = 0` (CFD): there is no exchange and no tape, only a bid and an ask quoted
by the broker. "Trade at last price" is not a setting we declined to use — the field does not exist.

**The chart is the BID.** MT5 plots bid by default. So a price ladder read off the chart
(`4118.96, .95, .94, .85 …`) *is* a bid sequence.

| | opens at | closes at |
|---|---|---|
| **SELL** | **BID** ← the number on your chart | **ASK** — closing a short means *buying* |
| **BUY** | **ASK** — 0.24 above your chart | **BID** |

That asymmetry **is** the cost. It is not a modelling choice and it cannot be configured away.

### The spread is fixed at 0.240/oz (240 points)

Measured across three sessions of real ticks:

| window | ticks | median | p10 | p90 |
|---|---|---|---|---|
| Thu 09 Jul 12:00–13:00 UTC (London) | 10,568 | **0.240** | 0.240 | 0.240 |
| Thu 09 Jul 14:00–15:00 UTC (NY open) | 8,483 | **0.240** | 0.240 | 0.240 |
| Fri 10 Jul 14:00–15:00 UTC (NY open) | 6,827 | **0.240** | 0.240 | 0.240 |

`p10 == p90` — it is **fixed**, not a weekend artifact, and it does **not** tighten in deep
liquidity. `contract_size = 100 oz`, so one round trip costs **$0.26 @0.01 lot / $26 @1.00 lot**.

> **A target smaller than 0.24 can never win.** The original 0.10 target was *inside* the spread:
> a "winning" trade netted **−$0.14/oz**. To net +$0.10 the price must move **0.34**.

---

## 3. All worked scenarios (spread 0.240, SELL, trigger 4119.00)

Entries fill at the **bid** (your chart). The exit fills at the **ask**.

### The user's own path — `96, 95, 94, 85, 80, 75 → 90`

| time | BID | ASK | fill |
|---|---|---|---|
| 0.0s | 4119.05 | 4119.29 | waiting |
| 0.2s | **4118.96** | 4119.20 | ENTRY #1 @ 4118.96 (BID) |
| 0.4s | **4118.95** | 4119.19 | ENTRY #2 @ 4118.95 |
| 0.6s | **4118.94** | 4119.18 | ENTRY #3 @ 4118.94 |
| 0.8s | **4118.85** | 4119.09 | ENTRY #4 @ 4118.85 |
| 1.0s | **4118.80** | 4119.04 | ENTRY #5 @ 4118.80 |
| 1.2s | **4118.75** | 4118.99 | ENTRY #6 @ 4118.75 |
| 1.4s | 4118.90 | **4119.14** | **REVERSAL (bid > last entry) → EXIT ALL @ ask** |

P&L/oz: `−0.18 −0.19 −0.20 −0.29 −0.34 −0.39` → **0/6 winners, −1.590/oz = −$159 @1 lot**

**Running floating P&L — note it is never positive, not once:**

| after | floating each | TOTAL |
|---|---|---|
| ENTRY #1 | −0.24 | **−0.24** |
| ENTRY #3 | −0.22 −0.23 −0.24 | **−0.69** |
| ENTRY #6 | −0.03 −0.04 −0.05 −0.14 −0.19 −0.24 | **−0.69** |
| EXIT | −0.18 −0.19 −0.20 −0.29 −0.34 −0.39 | **−1.59** |

**Every new entry lands at exactly −0.24.** The newest position always shows −0.24, in every row.
Price fell 0.21 in total. **The move was smaller than the cost of a single trade.** No exit rule
could have rescued this path.

### The winners

| # | path (bid) | span | P&L/oz per position | @1 lot |
|---|---|---|---|---|
| **W1** steady trend | 98, 80, 62, 44, 26, 08 → *20* | 0.90 | **+0.54 +0.36 +0.18** 0.00 −0.18 −0.36 | **+$54** |
| **W2** news drop | 95, 60, 25, 4117.90, .55 → *70* | 1.40 | **+1.01 +0.66 +0.31** −0.04 −0.39 | **+$155** |
| **W3** slow grind | 97, 85, 73, 61, 49, 37, 25 → *30* | 0.72 | **+0.43 +0.31 +0.19 +0.07** −0.05 −0.17 −0.29 | **+$49** |
| **W4** BUY breakout | 05, 25, 45, 65, 85 → *75* | 0.80 | **+0.46 +0.26 +0.06** −0.14 −0.34 | **+$30** |
| **W5** BUY ramp | 10, 45, 80, 4120.15, .50 → *35* | 1.40 | **+1.01 +0.66 +0.31** −0.04 −0.39 | **+$155** |

### The losers

| # | path (bid) | span | P&L/oz | @1 lot |
|---|---|---|---|---|
| **L1** instant whipsaw | 98 → *02* | 0.00 | −0.28 | **−$28** |
| **L2** shallow dip | 98, 94, 90 → *95* | 0.08 | −0.21 −0.25 −0.29 | **−$75** |
| **L3** first example | 98, 93, 90, 80, 70, 60 → *90* | 0.38 | −0.16 −0.21 −0.24 −0.34 −0.44 −0.54 | **−$193** |
| **L4** just under B/E | 98, 86, 74, 62, 56 → *70* | 0.42 | **+0.04** −0.08 −0.20 −0.32 −0.38 | **−$94** |
| **L5** BUY fakeout | 05, 15, 22 → *10* | 0.17 | −0.19 −0.29 −0.36 | **−$84** |

> **L5 is the BUY trap:** the chart says 4119.05 but you fill at **4119.29**. You start 0.24 down
> before the trade has drawn breath.

---

## 4. The law: span > 2 × spread

Look across all ten. **The last entry always loses** — it sits at the extreme and eats the full
spread. And the *average* entry sits **halfway** down the move: it captures `span/2` while paying
the whole `spread`. Therefore:

> **The ladder profits only when `span > 2 × spread ≈ 0.48`.**

It predicts every case:

| span | outcome |
|---|---|
| 0.00 · 0.08 · 0.17 · 0.21 · 0.38 · 0.42 | **all lose** |
| 0.72 · 0.80 · 0.90 · 1.40 · 1.40 | **all win** |

The user's path spanned **0.21**. That, not the modelling, is why all six positions lost.

---

## 5. The loss has TWO independent causes

Counterfactual: *what if a SELL could fill at the **ask*** (i.e. the spread erased entirely)?
Re-run the user's path:

| time | entry @ ASK | TOTAL floating |
|---|---|---|
| 0.2s | #1 @ 4119.20 | +0.00 |
| 1.2s | #6 @ 4118.99 | **+0.75** |
| 1.4s | **EXIT** | **−0.15** |

**Even at zero cost it still loses −0.15/oz.** At 1.2s it was **+0.75** and the exit handed all of
it back. So:

```
−1.590  total
= −1.440   the spread          (6 entries × 0.24)      ← the broker's cut
+ −0.150   the RULE itself     (survives at zero cost) ← our own doing
```

The rule's own loss comes from exiting **after** the turn: the exit fires at a bid **above the
average entry**, so the shorts are underwater even with no fees.

*(And you cannot fill a SELL at the ask anyway. A market sell hits the bid. A **sell limit** resting
at 4119.20 would need the bid to climb back to it — on this path it never does, so you take **zero**
trades. To sell higher you must wait for price to come **up** to you: the exact opposite of chasing
it down.)*

### Give-back: the reversal exit is late by construction

There is no stop and no profit-take — *the reversal **is** the exit* — so you always leave after
price has already turned. On **W1**, the good case:

| time | TOTAL floating |
|---|---|
| 1.0s | +0.60 |
| 1.2s | **+1.26 ← peak (+$126 @1 lot)** |
| 1.4s | **+0.54 ← what the exit actually gave (+$54)** |

**$72 — 57% of the profit — handed back waiting for confirmation.**

---

## 6. What 4 hours of real ticks say

*37,500 ticks, Thu 09 Jul 12:00–16:00 UTC, XAUUSDm.*

### 6a. The exit fires on the first uptick — so only unbroken runs count

| unbroken down-runs | 7,745 |
|---|---|
| median run | **0.199** ← *smaller than the 0.24 spread* |
| p75 / p90 / p99 | 0.309 / 0.442 / 0.790 |
| max | 1.393 |
| **runs reaching 0.48** (ladder break-even) | **7.7%** |

Gold ticks up and down constantly. Demanding *zero* upticks means the ladder nearly always dies
before it can pay for itself.

**Backtest of the literal rule** (timer entries, exit on first uptick):

| | 5 trades/sec (as specified) | 1 trade/sec |
|---|---|---|
| ladders | 5,906 | 5,338 |
| **win rate** | **1.8%** | 2.1% |
| avg / ladder | −0.455/oz | −0.406/oz |
| **4h total @1 lot** | **−$268,838** | −$216,562 |
| 4h total @0.01 lot | −$2,688 | −$2,166 |

### 6b. A retrace stop is far tighter than it looks

It trails the **extreme**, not the entry — so as price falls the stop follows it down, and *any*
bounce of that size, anywhere along the path, kills the trade. Meanwhile a $1 target needs the bid
to travel **1.24** (target + spread).

> **P(hit a $1.00 target before a 0.30 retrace stop) = 1.7%** (26 of 1,500 real samples).

The stop sits ~4× closer than the target. You lose ~98% of the time.

### 6c. The ladder is a pure multiplier

Same trigger, same TP (1.00), same retrace (0.30) — **only the position count changes**:

| max positions | win % | avg per ladder @1 lot |
|---|---|---|
| **1** | 14.3% | **−$30** |
| 2 | 11.1% | −$54 |
| 3 | 9.4% | −$72 |
| 5 | 6.7% | −$91 |
| **10** | 4.5% | **−$105** |

Monotonic. Each added position costs another 0.24 and buys less remaining room.

### 6d. No parameter escapes the spread

Single position, random trigger. Expectancy per trade, $/oz:

| target ↓ / trail → | 0.30 | 0.50 | 0.75 | 1.00 | 1.50 |
|---|---|---|---|---|---|
| **0.40** | −0.277 | −0.263 | −0.253 | −0.257 | −0.229 |
| **0.60** | −0.278 | −0.272 | −0.254 | −0.259 | −0.232 |
| **0.80** | −0.279 | −0.271 | −0.263 | −0.272 | −0.223 |
| **1.00** | −0.279 | −0.279 | −0.265 | −0.286 | −0.232 |
| **1.50** | −0.281 | −0.290 | −0.269 | −0.297 | −0.212 |
| **2.00** | −0.281 | −0.294 | −0.272 | −0.294 | −0.198 |

**Every cell lands on −0.24: the spread.** Widen the target, tighten the stop, trail it, pyramid
it — the answer never changes.

> **With no directional edge, expectancy = −spread per trade. Parameters cannot manufacture an
> edge.** This is the barrier-crossing result: for a driftless price, any (TP, SL) arrangement has
> the same expectancy, and costs make it negative.

---

## 7. Verdict

**The engine cannot create edge. It can only amplify it.**

Everything in §6 assumed a **random trigger**. The trigger is not random — *the user picks it*, and
it is the **only** component that can beat the spread. Which cuts both ways:

- Feed the ladder **zero** edge → it multiplies the **spread loss** (§6c: 1 pos −$30 → 10 pos −$105).
- Feed it a **real** edge → it multiplies the **gain** (W1, W2).

So the ladder is not inherently wrong. It is a lever, and a lever is only worth pulling if what it
is levering is positive.

**And the trigger's edge cannot be backtested — only the user supplies it.** That single fact
dictates the build.

---

## 8. Protocol: measure the trigger before risking anything

1. **Ship in paper mode** (`paper: true`, the default). The engine watches live ticks, logs every
   entry/exit it *would* make with the fill price it *would* have got, and places **zero orders**.
2. **Arm it with real trigger calls** for a week.
3. **Count.** Realized edge per trade must beat **0.24/oz**. That is the entire question.
4. Only then flip `paper: false` on demo, at `max_positions: 1`.
5. Raise `max_positions` **only** once the edge is established — that is when the lever is worth
   pulling, and not one day before.

**Guards that follow directly from the above** (implemented in `strategies/ladder.py`):

- `paper: true` by default.
- **Refuse to arm when `target <= live_spread`** — a target inside the spread cannot win, and the
  engine must not pretend otherwise.
- **Warn when `max_positions > 1`**, carrying the measured cost: each extra position is −0.24/oz of
  guaranteed spread before it does anything else.
- Demo-only + hedging gate, own magic number, daily-loss kill-switch, hard SL per position.

---

## 9. How to re-run any of this

All numbers came from `MetaTrader5.copy_ticks_range` on `XAUUSDm` via the project venv:

```powershell
cd XauOrderPad
.\.venv\Scripts\python.exe -c "import MetaTrader5 as mt5, datetime as dt; mt5.initialize(); t = mt5.copy_ticks_range('XAUUSDm', dt.datetime(2026,7,9,12), dt.datetime(2026,7,9,16), mt5.COPY_TICKS_ALL); print(len(t))"
```

- `t['bid']` is the chart. `t['last']` is **all zeros** — check it yourself.
- Spread = `ask - bid`, fixed at 0.240.
- The ladder simulator is the decision function in `strategies/ladder.py`; the replay harness in
  `analysis/` runs it over these ticks and **must reproduce §6c** (1 position ≈ −$30/ladder at
  random triggers). If it ever stops matching, the engine has drifted from the model that was
  validated here — fix the engine, not the doc.

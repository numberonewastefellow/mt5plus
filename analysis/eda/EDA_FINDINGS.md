# XAUUSD EDA — Findings

Exploratory data analysis of XAUUSD to find tradeable structure, done **after**
three tick-scalping strategies failed on cost. Data pulled from MT5 (Exness demo
472200942), analysis in conda **`test_env`** (see `README.md`).

> **One-line verdict:** Direction from price alone is a **random walk** (untradeable),
> but **two real structures exist** — volatility is highly predictable (*when*, not
> *which way*), and price **reacts at S/R levels ~57–60%** (a genuine directional
> bias vs 50% random). Fading S/R on M15 looks promising **in-sample** but is
> unvalidated. Crucially, on M15/H1 the 40-pt spread is **negligible** (<1% of a
> trade) — the cost wall that killed tick scalping does **not** apply here.

Data: D1 (2023→now), H4 (2024→), H1 (2025-06→), M15/M5 (2026-01→), M1 (2026-04→,
100k bars), + 3.0M ticks (14d). Scripts `eda1..eda4`, dashboard `eda_dashboard.html`.

---

## 1. Noise / structure (`eda1`)

| Finding | Evidence | Meaning |
|---|---|---|
| **Direction is random** | return autocorrelation ≈ **0** at every lag/TF (all \|ρ\|<0.13); variance ratio ≈ **1.0** | past returns don't predict future ones — why every directional strategy failed |
| **Volatility clusters hard** | \|return\| autocorrelation ≈ **0.35** at every lag/TF | *when* it will be volatile IS predictable |
| **Fat tails** | excess kurtosis **30–98** (normal=0) | big moves far more common than a bell curve — tail risk & opportunity |
| **Negative skew** | skew **−0.8 to −1.9** (D1..M15) | down-moves bigger than up (gold sold off this period) |
| Tick noise | mid lag-1 autocorr −0.10 (bid-ask bounce); spread fixed **40 pts** | microstructure is pure noise + a fixed cost floor |

## 2. Big moves + volume (`eda2`)

- **Volatility persistence:** P(big move \| previous bar was big) = **3.3×** the base rate (M5). Big begets big.
- **When:** big moves cluster at **h13–h15 (NY open / US data, ~2× base)** and the **Sunday reopen (1.9×)**.
- **Volume ↔ size:** corr(volume, bar **range**) = **0.76–0.86** across all TFs — volume strongly tracks move size (confirms & strengthens the old 0.71).
- **Volume COINCIDES, doesn't lead:** corr is highest in the *same* bar (0.56) and drops to 0.45 next bar — by the time you see the volume, the move is underway (the entry-timing problem).
- **Spike → forward outcome (M5, next 3 bars):** size **2.8×** bigger after a spike, P(big move) **3.1×** — but signed direction ≈ **0**. **Volume predicts size, never direction.** (Third independent confirmation.)

## 3. Pivots + support/resistance (`eda3`) — the first directional structure

Reaction rate = P(price bounces/holds at a level). 0.50 = meaningless; >0.55 = real S/R.

| Level type | touches | reaction rate |
|---|---|---|
| Daily pivots (PP/R1/R2/S1/S2) | 388–2043 | **0.56–0.60** |
| Round numbers ($50 / $100) | 2354 / 4338 | **0.575** |
| Swing-level S/R (fractal, clustered) | **9157** | **0.576** |
| Volume-profile POC / VAH / VAL | 459 | **0.590** |

All are **statistically significant** (swings: 0.576 over 9157 = ~14 std-errors above 0.5). **Price genuinely reacts at structural levels** — the one place a directional bias hides. (Daily trend context: price is **below MA50 and MA200** → downtrend.)

## 4. Synthesis (`eda4`) — is it tradeable?

**Reframe from the data:** the 40-pt ($0.04) spread killed *tick* scalping because targets were spread-sized. On M15/H1, moves are **$5–50** — spread is **<1%** of a trade. So a level-reaction *swing* trade is **not** cost-constrained.

Fading strong S/R levels (bet the bounce), **in-sample screen, all history:**

| setup | win% | exp/R | PF |
|---|---|---|---|
| fade, stop $3, target **2R**, all touches | 45.5% | **+0.357R** | **1.65** |
| fade, stop $5, target 2R, all touches | 44.9% | +0.342R | 1.62 |
| fade + **volume confirmation** | 37–41% | +0.13R | 1.20 |
| **break** the level (momentum), vol-confirmed | 31% | **−0.075R** | 0.89 |

Two clean takeaways: **fading levels beats random; breaking them loses** (consistent with reaction, not momentum). And **volume confirmation *hurts* the fade** — a volume spike at a level tends to mean a *break*, not a bounce.

---

## The honest caveats (why this is a lead, not a result)

1. **In-sample.** The +0.357R is over all history with no walk-forward. Prior research here turned a **+91% in-sample into −16% OOS** — that exact trap applies. Do **not** believe the number yet.
2. **Lookahead in level construction.** The "strong levels" are built from swings across the *whole* history, so an early trade uses levels that formed later. A proper test must build levels **point-in-time** (only swings before each trade). This alone likely inflates the edge.
3. **Descriptive, not sized.** No position sizing, no realistic slippage on stops, single fixed spread.

## The test was run — verdict: DISCARD (`strategy_test.py`)

The fade-S/R lead was put through a proper point-in-time walk-forward with two
adversarial controls. It **failed both:**

| | PIT S/R | Random-level control | win% @ 2R |
|---|---|---|---|
| Fill **at the level** (optimistic, wick) | +0.531R | **+0.506R** | ~51% |
| Fill **next-bar open** (honest) | **+0.002R** | +0.003R | ~34% |

1. **Random-level control matches it.** Fading *randomly placed* levels earns the
   same ~+0.5R as real S/R → the "edge" has nothing to do with support/resistance.
2. **Honest fills collapse it to zero.** The +0.5R came entirely from assuming
   fills at the exact wick of the touch bar. With next-bar-open fills the edge is
   **+0.002R** and win rate falls to **~34% at a 2R target — the exact random-walk
   value** (gambler's ruin: P(reach +2 before −1) = 1/3).

So the eda4 in-sample +0.357R was a **double mirage** — lookahead in level
construction *and* an optimistic fill assumption. There is **no S/R edge.** This is
consistent with module 1: direction is a random walk, and no level-based overlay
changes that.

## Update — exhaustive search found a marginal lead (`pattern_search.py`)

After the fade-S/R discard, an exhaustive search (8 signal families × time-of-day ×
exit rules, with a **sealed 6-week holdout** and random-direction controls) plus a
4-agent adversarial verification changed the conclusion from flat "no edge" to a
**nuanced, honest lead**:

- **Volatility harvesting is real** — a trailing stop that rides gold's fat-tailed
  moves is positive on every timeframe *even with random entries* (it profits from big
  moves of either direction). ⚠️ **regime-dependent** — every month in the data was
  high-volatility, so the quiet-month downside is untested.
- **A faint directional momentum edge exists — only at M5** (~+$0.5–0.7/oz, +$50–70/lot
  over random). It clears the permutation test (p=0.007) and **replicated on the sealed
  holdout** (p=0.034) — but its bootstrap CI **includes zero** (~92% one-sided). A
  **low-conviction lead, not a proven edge.**
- **Discarded:** the M15 "+$166/lot" headline (~78% exit-skew + an overfit train peak).
- **No code bug** — the engine reproduced to the cent across three independent agents.

Full story, method, and limitations: **[INVESTIGATION_JOURNEY.md](INVESTIGATION_JOURNEY.md)**.

## Final conclusion

Direction is a near-random walk; the durable structure is **volatility** (predictable
size, not direction). The honest, tradeable expression of that is **not** a direction
predictor but a **volatility-harvesting trend-rider** (trailing stops that ride fat-tail
moves), with a *faint* M5 momentum tilt on top. Both are marginal and regime-dependent —
worth a careful forward test, not an account bet.

**What NOT to do:** trust in-sample numbers without a random control + honest fills; add
a volume filter to the fade (hurts); trade level breaks (lose); tick scalp (cost-dead);
or read the 5-seed control's std as the edge's confidence (use bootstrap CIs).

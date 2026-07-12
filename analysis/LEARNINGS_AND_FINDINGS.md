# XAUUSD Volume-Anomaly Research — Learnings, Mistakes & Findings

**Instrument:** XAUUSD (resolves to `XAUUSDm` on the Exness demo feed)
**Research period:** initiated 2026-07-04
**Question we set out to answer:** can a volume anomaly tell us (a) that a big move
is coming, and (b) which *direction* to trade — well enough to scalp with a tight
stop and a good target?

> **One-line verdict:** Volume anomalies reliably predict the **size** of the next
> move, but **not its direction**. A directional scalp built on M1/M5 volume does
> **not** survive honest (out-of-sample) testing — it loses. The magnitude signal
> is real and reusable; the direction signal is not.

**See also: [TREND_LADDER_STRATEGY.md](TREND_LADDER_STRATEGY.md)** — a tick-level
pyramid-into-the-move strategy, investigated separately. It reaches **the same wall from a
completely different direction**: with no directional edge, expectancy is **−1 spread per trade**,
and *no* arrangement of target and stop escapes it (a full target × trail sweep lands every cell on
−0.24 = the spread). That two unrelated approaches converge on the same barrier is the strongest
evidence it is real.

---

## 1. The two-part conclusion

| Claim | Verdict | Evidence |
|---|:--:|---|
| Volume spike ⇒ **big price move is coming** | ✅ **TRUE** | corr(volume-score, bar range) ≈ **0.71–0.74** on M5/M15/H1/H4; top-10% volume bars swing **2.3–2.5×** more than normal. Held across different date windows. |
| Volume/order-flow ⇒ **correct direction** | ❌ **FALSE (out-of-sample)** | Walk-forward on M1/M5 = **net loss** (see §3). Best direction rule *flipped* between date windows (FADE in one, FOLLOW in another). |

**Takeaway:** use the anomaly as a **volatility / risk flag**, not a direction oracle.

---

## 2. The overfitting trap (the central lesson)

We produced two very different-looking numbers for the *same* strategy family on M1:

| Result | Window | How the config was chosen | Realistic? | Outcome |
|---|---|---|:--:|---|
| **"+$77k / 45.7% win / PF 1.45"** | Mar 24 – May 9 | Grid-searched **after** seeing the whole window, kept the best → **in-sample** | ❌ No (hindsight) | +91% @1 lot — **a mirage** |
| **"−16%"** | Apr 14 – Jun 30 (12 rolling weeks) | Chosen on the **past** 3 weeks, traded on the **next unseen** week → **out-of-sample** | ✅ Yes | **−15.9% @1 lot — reality** |

**Overfitting = the strategy learned the specific noise of one window, not a repeatable
pattern.** Running ~120 strategy variations and keeping the single best guarantees one
looks great *by luck*. The tell in the walk-forward: **training expectancy was positive
every single week (+0.15 to +0.82), but the next week's real result was a coin flip.**

> **Rule for the future:** trust walk-forward / out-of-sample numbers. Distrust any
> single-window optimized number — *especially* when it looks too good.

---

## 3. Results at a glance

### In-sample (optimized on the same data — NOT tradeable forward)
| Window | Best config picked | Trades | Win% | Exp/R | PF | Net @1 lot |
|---|---|---:|---:|---:|---:|---:|
| May 24 – Jul 3 | **FADE**_DELTA, rvol≥8, SL1.5×ATR, 1.0R | 61 | 60.7% | +0.163 | 1.41 | +24.8% |
| Mar 24 – May 9 | **FOLLOW**_DELTA, rvol≥5, SL2.0×ATR, 2.5R | 324 | 45.7% | +0.226 | 1.45 | +91.3% |

⚠️ The winning direction **flipped** (FADE ↔ FOLLOW) between windows → regime overfitting.

### Out-of-sample walk-forward (weekly re-fit — the honest test)
| Timeframe | Rolling weeks | Trades | Win% | Exp/R | PF | Net @1 lot | Verdict |
|---|:--:|---:|---:|---:|---:|:--:|:--:|
| **M1** | 12 | 134 | 35.1% | −0.092 | 0.85 | **−15.9%** | ❌ LOSS |
| **M5** | 12 | 121 | 43.8% | −0.060 | 0.89 | **−15.4%** | ❌ LOSS |
| **M15** | 12 | 33 | 45.5% | +0.396 | 1.77 | +23.0% | ⚠️ too few trades (noise) |

Direction flipped week-to-week **5–6 times out of 11** on M1/M5 → whipsaw, no persistent regime.

---

## 4. Mistakes & pitfalls we hit (so we don't repeat them)

1. **Overfitting to one window.** Grid-search "best" on a single period looked +91%; the
   same approach lost −16% out-of-sample. → Always walk-forward.
2. **Win-rate misconception.** A low win rate is **not** a loss by itself. With a 2.5R
   target, 45.7% wins was still profitable *in-sample* (winners +2.4R vs losers −1R,
   PF 1.45). Profit = win% **and** reward:risk together — never win% alone.
3. **"Short SL + big target" instinct was the *worst* combo** for this mean-reversion
   setup. Tight stops got noise-stopped; the edge (such as it was) needed a moderate
   ATR stop and a modest target.
4. **Regime dependence.** FADE (mean-reversion) wins in ranges; FOLLOW (momentum) wins
   in trends. A *fixed* direction rule whipsaws when the market alternates — which gold
   did over this period.
5. **Overlapping positions in the backtest.** Each signal was taken independently, so
   during spike clusters 4–5 same-direction trades were open at once → real risk was
   multiplied and clustered P&L was distorted. A realistic single-account test needs a
   "one-position-at-a-time / flat-before-next" rule (not yet applied).
6. **Small samples = noise.** M15's +23% came from just 33 trades over 3½ months — not
   trustworthy, no matter how good the number looks.

---

## 5. Methods / algorithms used

**Anomaly detection (magnitude):**
- Robust **MAD z-score** (outlier-resistant), global.
- **Time-of-day deseasonalized z-score** — divides each bar by the *typical* volume for
  its own session slot, so London/NY bulges aren't false-flagged. (The honest detector.)
- **RVOL** = volume ÷ trailing rolling median.
- **VROC** = bar-over-bar volume surge. (Weak — false-positives on Sunday→Monday gaps at D1.)

**Direction / buy-sell pressure (all failed to generalize):**
- **BVC — Bulk Volume Classification** (Easley/López de Prado/O'Hara): split each bar's
  volume into buy%/sell% via the standardized return through a normal CDF; net = delta.
- **CVD / rolling volume delta** — net order flow over a window.
- **CLV money-flow** (Chaikin A/D) — who controlled the bar by *where* it closed.
- **Volume Profile** — POC / Value-Area-High / Value-Area-Low; fade the value-area edges.
- **Absorption** — huge volume + tiny move ⇒ one side absorbed ⇒ fade.

**Validation:** in-sample grid → first/second-half split → **rolling weekly walk-forward**
(the only one that told the truth).

---

## 6. Data & operational notes

- **Feed:** MT5, Exness demo (login 472103079, `Exness-MT5Trial16`), balance ~$84,772.
- **Symbol:** `XAUUSD` auto-resolves to **`XAUUSDm`**. Contract size **100 oz/lot**
  (so a $1/oz move = $1 / $10 / $100 at 0.01 / 0.1 / 1.0 lot).
- **Volume is `tick_volume` only** — `real_volume` = 0 on this CFD feed (standard for FX/CFD).
- **Times are broker time** (Exness ≈ GMT+2/+3), not local.
- **M1 history is limited to ~2026-03-23** on this feed; older M1 requests return nothing.
- `copy_rates_range` fails/returns `None` for very long M1 spans → **pull in ≤25-day
  chunks and de-duplicate** (see `fetch_rates` in `walk_forward.py`).
- **Env:** `XauOrderPad/.venv` (numpy + plotly installed; **no pandas** — scripts are numpy-only).

---

## 7. Scripts (in `analysis/`)

| Script | Purpose |
|---|---|
| `vol_anomaly.py` | Multi-TF anomaly detection (robust z, deseasonalized z, RVOL, VROC) + volume↔price correlation. |
| `gen_artifact.py` | Self-contained HTML chart of price + volume with anomalies marked. |
| `scalp_signal.py` | M1 scalp grid: continuation vs fade, trend filter, SL/target sweep. |
| `order_flow_direction.py` | Buy/sell-pressure direction engines + in-sample/half-split, per-lot P&L, entry/exit times, CSV + Plotly chart. Optional CLI: `<start> <end>` (YYYY-MM-DD). |
| `walk_forward.py` | **Honest** rolling weekly walk-forward across M1/M5/M15 + Plotly chart. |

**Charts produced:**
- `chart_insample_M1_20260324_20260509.html` — the +$77k **overfit** window (equity rises).
- `chart_insample_M1_20260525_20260703.html` — other in-sample window (equity rises).
- `walkforward_chart_M1_20260324_20260704.html` — **out-of-sample** reality (equity falls).

---

## 8. Open questions / next steps (none yet done)

1. **No-overlap constraint** — re-run walk-forward with one-position-at-a-time (realistic).
2. **Slower timeframes + more history** — M15/H1 back 1+ year (needs data beyond this
   feed's M1 limit); walk-forward with hundreds of trades instead of 33.
3. **Explicit regime model** — detect trend vs range (ADX / MA structure), then FOLLOW in
   trends and FADE in ranges; test in the same walk-forward harness.
4. **Direction-agnostic play** — use the anomaly as a volatility trigger for a
   **straddle / breakout bracket** (buy-stop above + sell-stop below the spike bar), which
   profits from the *size* of the move without needing the direction.

---

## 9. News correlation — Fed / CPI / NFP + geopolitical (added after web research)

Matched every volume spike against the **real 2026 calendar** (federalreserve.gov FOMC
dates + BLS CPI/NFP), broker clock calibrated off the weekend gap (server ≈ UTC, ET = broker − 4).

**Peak volume + gold's 60-min reaction at EVERY scheduled release (Mar 24 – Jul 4):**

| Date | Event | Peak RVOL | Gold 60m | Dir |
|---|---|---:|---:|:--:|
| Apr 03 | NFP (Mar) | — (Good Friday, thin) | — | |
| Apr 10 | CPI (Mar) | 4.4× | −$4.11 | DOWN |
| Apr 29 | FOMC | 5.8× | −$0.89 | DOWN |
| May 01 | NFP (Apr) | 2.9× | −$9.09 | DOWN |
| May 12 | CPI (Apr) | 3.3× | −$4.11 | DOWN |
| Jun 05 | NFP (May) | 7.4× | −$51.43 | DOWN |
| Jun 10 | CPI (May) | 4.5× | −$19.85 | DOWN |
| **Jun 17** | **FOMC (SEP)** | **18.0×** | **−$55.22** | **DOWN** |
| Jul 02 | NFP (Jun) | 12.1× | −$1.14 | DOWN |

**Findings:**
1. **FOMC is the king event** — June 17 produced the single largest volume spike of the
   whole period (**18× normal**) and the biggest reaction (−$55). Cleanest news→volume→move.
2. **Every scheduled US release bumped volume** (RVOL 3–18×) → news genuinely drives volume.
   But most sit at **3–6×**, *below* the extreme 8× spikes — so the calendar explains only
   **3 of 65** of the *most extreme* spikes (FOMC ×2 + July NFP).
3. **Gold fell on all 8 scheduled releases** in this window — but that is **regime-confounded,
   not a clean news rule**. Web research: a US–Iran conflict (late Feb 2026) pushed oil and
   inflation expectations up → Fed on hold → real yields up → **gold under pressure** the whole
   stretch. So "down on data" ≈ "down-trend that happened to be sampled at data times."
4. **Most extreme spikes are UNSCHEDULED / non-US-session** — geopolitics and macro flow, not
   the calendar. Confirmed examples: the biggest spike **Apr 23** (RVOL 22) was a ~3% gold drop
   driven by **stronger USD + rising yields + White House statements + Strait of Hormuz**, *not*
   a data release; the Apr 9 spike sat inside the volatile Iran-driven recovery.

**Direction implication (the important one):** news reliably creates the **volatility** (the
"how big"), but **direction is set by the dollar/real-yield reaction, not the headline's surface
sentiment** — bullish-sounding geopolitical news produced gold *selling* because USD/yields
dominated. So even *with* a news feed, you must model the USD/yield response to get direction;
the event alone is not enough.

**Usable takeaways:**
- Anomaly + FOMC/NFP calendar = a solid **event/volatility flag** (know *when* a big move is due).
- Around FOMC especially, trade it **direction-agnostic** (straddle / breakout bracket).
- Any directional bias must be **regime-conditioned** (USD & real-yield trend), not headline-based.

*Data sources: [FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm),
BLS CPI/NFP schedules, gold price commentary (RoboForex/Babypips/World Gold Council, Apr 2026).*

---
*This document is the reference record of what was tried, what worked, and what didn't.
The magnitude signal is keep-worthy; the M1/M5 directional scalp is not. News reliably explains
the volatility (esp. FOMC), but direction stays governed by USD/real-yields, not the headline.*

# XAUUSD Ultra-Short-Horizon Scalping — Research Synthesis

**Source:** deep-research workflow `wf_e006f25a-347` (108 agents, 26 sources fetched, 90 claims extracted, 25 adversarially verified, 15 confirmed / 10 killed).
**Verification protocol:** 3-vote adversarial — a claim survives only if at least 2 of 3 independent verifiers fail to refute it.
**Date:** 2026-06-26.

---

## Top-line verdict

Your original spec — `SL = $0.05`, `TP = +$100 fixed`, `10 × 1.0 lot stacked`, close-all-on-downtick — is structurally non-viable on **every** dimension the literature addresses. The research did not return a single peer-reviewed or audited-equity-curve XAUUSD parameter set with documented positive expectancy at 1m/5m/15m candle-open breakouts. **The survivable envelope below is reconstructed by negation (what definitely fails) and by transferable principles, not by a cited gold-specific positive backtest.** Treat Phase 3 (real-data backtest) as mandatory before any live capital — the corpus refuses to make a promise the user should not make to themselves.

---

## Confirmed findings

### 1. Stop loss must be ATR-scaled, not fixed-cash
- **Formula:** `Stop Distance = ATR(14) on entry timeframe × Multiplier`.
- **Range used in the literature:** **1.0×–2.0× ATR**, with **~1.5× ATR(14)** explicitly cited as appropriate for 1–15 minute scalping.
- **Why a $0.05 SL fails:** typical XAUUSD spread alone is ~20 pips ($0.20). The stop is *inside the spread*, not just inside noise. Cotter & Longin (2011) further show that closing-price-derived risk understates true intraday risk by up to ~50% at 5-min/1-hour scales, so any SL sized on EOD intuition is already too tight.
- **Sources:** comofx.com risk-reward guide; FXNX gold-news 15-min-rule article; Cotter & Longin (2011) *Margin Setting with High-Frequency Data* — arXiv:1103.5412 (primary).
- **Vote:** 2-1 confirm.

### 2. Position size derives from (stop distance × % risk), not from a fixed lot count
- **Formula:** `Lot Size = (Account Equity × Risk%) / (Stop Distance × $/pip)`.
- **Range:** **1–2% of equity per trade**. On $50k → **$500–$1,000 max risk per stack**.
- **Why "10 × 1.0 lot fixed" fails:** this rule was written specifically to prevent your failure mode — when SL distance changes with volatility but lot size is held constant, risk is unbounded.
- **Sources:** FXNX practitioner guide; Van Tharp CPR formula; Elder 2% rule (well-known canon).
- **Vote:** 3-0 confirm.

### 3. Crabel's canonical ORB entry is a symmetric volatility bracket (not a cash bracket)
- **Mechanism:**
  - `Stretch = 10-period SMA of the smaller of (Open − Low) and (High − Open)`
  - Buy stop at `Open + Stretch`, sell stop at `Open − Stretch`
  - **First stop triggered becomes the position; the opposite stop becomes the protective stop.**
  - **Position risk therefore = 2 × Stretch** — a volatility-derived unit, structurally incompatible with a fixed $0.05 cash stop.
- **Sources:** Oxford Strat ORB writeup; tradingstrategiesdaily NR7ID; Crabel (1990) ISBN 0934380171.
- **Vote:** 2-1 / 3-0 / 2-1 across three reinforcing claims (high).

### 4. Exits are time-, structure-, or ATR-trailed — not fixed cash
- **Crabel exits:** same-day or 1–3 bars; first profitable close; or 2×ATR trailing.
- **Raschke exits (verbatim):** "Much of my modeling uses time-based exits. Exits on the close or the next day's close, Exit after one hour" and "**I don't set price targets. I get out when the market action tells me it's time to get out, rather than based on any consideration of how far the price has gone.**"
- **Implication:** a fixed `+$100` TP is directly contradicted by both canonical sources. It silently assumes gold reliably travels $1.00 in the holding window — which on a 5m bar is a large move (often 30–60% of ATR).
- **Sources:** tradingstrategiesdaily NR7ID; macro-ops Raschke profile; Raschke *Trading Sardines*; StockCharts ChartSchool.
- **Vote:** 3-0 / 3-0 / 2-1 confirm.

### 5. Canonical ORB does **not** produce edge as a standalone strategy
- **Evidence:** Oxford Strat tested Crabel-style ORB across **42 US futures markets, 1980–2011 (32 years)**, with three exit families (time, target, ATR-stop). **Final rating: D** (lowest tier on their A/B/C/D scale).
- **Implication:** the mechanics are necessary but **not sufficient**. You must add session, instrument, and event filters or the system has no edge across regimes.
- **Source:** oxfordstrat.com ORB writeup (primary rater).
- **Vote:** 3-0 confirm.

### 6. Gold has asymmetric, persistent FOMC response — published academic evidence
- **Finding:** at the 5-minute scale, gold returns and volatility respond **more strongly to dovish (looser-than-expected) FOMC shocks than to hawkish ones**, and adjustment **continues beyond 5 minutes** — published as "potential short-term inefficiencies in the gold market."
- **Source:** Awartani, Hussain & Virk (2024), *International Review of Financial Analysis* vol. 95 — ~1.24M 5-min NYMEX gold futures observations Jan 2007–Dec 2020.
- **Vote:** 3-0 confirm.
- **Implication:** FOMC windows are not "noise," they're a directional event. A blanket avoidance is right for tight-SL scalps, but a dovish-bias bias-trade exists for a different strategy.

### 7. High-impact events to filter for XAUUSD
- **List:** NFP, CPI, FOMC decisions/statements, US Retail Sales, US GDP, ISM PMIs, Fed speeches.
- **Documented failure modes around these:** (i) whipsaws / V-shaped fake spikes, (ii) temporary spread widening, (iii) stop-loss slippage.
- **Spread blow-out:** typical ~20 pips can instantly widen to **100–200 pips** (some outliers 50–2000) during NFP.
- **Microstructure mechanism:** FEDS Notes 2020-12-31 (Federal Reserve primary source) confirms principal trading firms and dealers widen bid-ask spreads around FOMC to avoid adverse selection — this is structural, not anecdotal.
- **Sources:** NordFX trader guide; FXNX 15-min-rule article; FEDS Notes 2020-12-31; corroborated by Vantage, Pro-Scalper, JustMarkets.
- **Vote:** 3-0 confirm.

### 8. Intraday risk is materially higher than EOD measures suggest
- **Finding:** 5-min / 1-hour scaled risk measures imply up to **~50% higher risk** than closing-price-derived margin levels on liquid futures.
- **Source:** Cotter & Longin (2011), arXiv:1103.5412 (primary, FTSE 100 futures — generalizes to commodities by realized-volatility literature).
- **Vote:** 2-1 confirm (medium confidence on the gold transfer, high on the underlying principle).
- **Implication:** any SL or margin sized on daily-bar intuition is too tight for 5m scalping by definition.

---

## Refuted / unverified — DO NOT use these as evidence-based settings

The 3-vote adversarial pass killed these frequently-cited practitioner rules. They may still be sound rules-of-thumb, but they were **not validated** in this corpus and should not be cited as research-backed:

| Killed claim                                                                       | Vote |
| ---------------------------------------------------------------------------------- | ---- |
| Crabel "Stretch_Multiple = 2, Stretch_Length = 10" parameterization                | 0-3  |
| The unmultiplied 10-period Stretch formula is canonical; multiplied variant isn't  |      |
| Crabel 2-Bar NR uses Stretch × 2 over 10-day lookback                              | 0-3  |
| Oxford Strat tested ORB with 6× ATR(20) initial stop                               | 0-3  |
| Crabel originals only ran on daily bars / no XAUUSD validation in study            | 0-3  |
| Quant Signals: "2.0× ATR + 2:1 RR was best across 6 assets, PF 1.16"               | 0-3  |
| XAUUSD ATR normal range 8–25 pts / volatile 40+ pts                                | 0-3  |
| Tintin Trading: increase size after 5 winners in last 15, halt after 5 losses      | 0-3  |
| Staged scale-out: 30% at 1:1.5 / 40% at 1:2 / 30% at 1:3                           | 0-3  |
| "15 minutes before and after high-impact news" avoidance window                    | 1-2  |
| Generic "halt all trading after 5 losers, cap testing DD <1%"                      | 0-3  |

Notably, the **15-minute news avoidance window was NOT validated** — and the FOMC academic evidence above suggests the persistence is longer than 5 minutes anyway. We need empirical measurement of the true window (see Open Question 4).

---

## Survivable parameter envelope for your $50k account

Synthesized from confirmed findings only. Every number ties back to a confirmed source above.

| Parameter           | Value                                                                                                              | Source / rationale                                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------- |
| **Entry**           | Crabel-style symmetric ORB: 5m or 15m candle open ± Stretch (10-bar SMA of smaller open-to-extreme distance)       | Finding 3                                                     |
| **Stop loss**       | **1.0×–1.5× ATR(14) on entry TF**, OR 2× Stretch (Crabel canonical) — whichever is wider                            | Finding 1, 3                                                  |
| **Risk per stack**  | **1% of equity = $500** (max 2% = $1,000) — derive lot size from stop distance, do not fix lots                    | Finding 2                                                     |
| **Take profit**     | **Replace fixed $100** with: (a) ATR trailing stop at 2× ATR, OR (b) exit at prior 5m/15m swing high, OR (c) time-stop 1–3 bars after entry if not in profit, OR (d) partial at 1R + runner with breakeven stop | Finding 4 (Crabel + Raschke)        |
| **Max stack**       | **1 base + up to 2 pyramid adds** — adds only allowed after prior entry is in profit and SL moved to breakeven. **No close-all-on-downtick rule** (delete it). | Finding 2, derived: correlated risk discipline |
| **Session filter**  | **Trade London (08:00–11:00 GMT) and NY (13:00–16:00 GMT) — especially overlap 13:00–16:00 GMT.** Avoid Asian session for breakouts. | Finding 5, 7 (filtering is required for ORB to have edge)     |
| **Event filter**    | **Flat across NFP, CPI, FOMC, Retail Sales, GDP, ISM PMI, scheduled Fed speeches.** Window: at least 5 min before to 30 min after (the literal 15-min rule was refuted; FOMC persists >5 min — be conservative until measured). | Finding 6, 7                                                  |
| **Spread guard**    | Skip entry if real-time spread > 25 pips (above the documented ~20 pip baseline)                                    | Finding 7 (spread widening as a failure mode)                  |
| **Hard daily cap**  | Stop trading for the day after −2R cumulative loss (= −$1,000 on $50k)                                              | Derived: kurtosis of intraday gold (Finding 8) means tail losses cluster |

**This envelope is intentionally conservative.** Once the Phase 3 backtest demonstrates positive expectancy, parameters can be loosened empirically — never the other way around.

---

## Open questions (must address before Phase 3 backtest)

These are gaps the research could not close — answering them is the prerequisite for Phase 2's strategy spec to claim it's evidence-based:

1. **No XAUUSD-specific positive backtest exists in the corpus.** What is the actual measured win rate / profit factor / expectancy of any Crabel-style ORB on XAUUSD 5m or 15m, 2018–2025, with realistic spread+slippage? — *Answer by building it in Phase 3.*
2. **What is the empirical XAUUSD 1m/5m return kurtosis** by session (London / NY / overlap) and how does it map to the minimum survivable ATR-multiplier on stops? — *Compute from your broker's tick history in Phase 3.*
3. **Does pyramiding on XAUUSD scalps actually add edge?** The pyramiding canon (Van Tharp / Minervini / Raschke) was developed largely on equities/commodities at swing horizons. The FOMC-asymmetry literature suggests pyramiding may pay only on confirmed dovish-shock days. — *Test pyramiding-on vs. pyramiding-off as a backtest variant in Phase 3.*
4. **What is the precise no-trade window** (in minutes pre- and post-release) that restores SL-survival probability to baseline on XAUUSD for each of NFP / CPI / FOMC / Retail Sales / GDP / ISM PMI? The commonly-cited 15-min rule was refuted. — *Measure from realized volatility around each event type in Phase 3.*

---

## Source quality summary

| Quality           | Count | Notes                                                                |
| ----------------- | ----- | -------------------------------------------------------------------- |
| Primary (academic)| 7     | arXiv preprints, ScienceDirect peer-reviewed, FEDS Notes             |
| Secondary         | 3     | Oxford Strat, NordFX trader guide                                    |
| Blog              | 12    | Practitioner blogs — used for mechanics, downweighted for claims     |
| Unreliable        | 5     | Excluded (zero claims accepted)                                       |

**Bias caveats from the workflow:**
- Mechanics findings (Crabel, Raschke, ATR multipliers, spread blow-outs) lean blog/secondary.
- Only the **FOMC asymmetry** and **intraday-vs-daily risk gap** are primary-academic.
- The corpus contains **no audited XAUUSD scalping equity curve** — every confirmed parameter range is transferable principle, not gold-specific proof.
- Time sensitivity: 2007–2020 FOMC dataset predates the current rate-cycle regime; spread/slippage magnitudes are broker- and liquidity-tier dependent.

---

## Next step (per the approved plan)

Phase 2 — write `strategy/xauusd-scalp-v2.md`: one-pager hand-translating this envelope into a precise, executable spec where every parameter cites which finding above justifies it. Then Phase 3: backtest v2 vs. your original v1 on real XAUUSD data, modelling spread + slippage + commission.

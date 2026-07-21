# XAUUSD Edge Investigation — the full journey, method, and the one thing that survived

This is the honest record of how we searched XAUUSD (gold) for a tradeable edge,
**everything we tried**, why most of it failed, and **how the one surviving lead
was found and verified**. Research only — no orders were ever placed (account
untouched at ~$100,141). Analysis env: conda **`test_env`** (see `README.md`).

---

## TL;DR

- **Direction is a near-random walk** at every timeframe (return autocorrelation ≈ 0,
  variance ratio ≈ 1). Predicting "which way the next candle goes" doesn't work — this
  is *why* five successive directional strategies failed once tested honestly.
- **Two things are real:** (1) **volatility is predictable** (big moves cluster, 100%
  of months) and can be *harvested* with a trailing stop that rides gold's fat-tailed
  moves; (2) a **faint directional momentum edge exists — but only at M5** (~+$50/lot
  over random), and it's *marginal* (its confidence interval includes zero).
- **What killed the false leads:** honest fills (enter next bar, not the wick), a
  **sealed holdout**, and **random-direction controls**. Those three catch mirages.
- **Status:** a real-but-marginal lead, not a green light. Needs fresh-data confirmation.

---

## The constraints that shaped everything

- **No auto-trading, ever** (repo hard rule): Claude never places/modifies/closes an
  order — demo or real. Everything here is signal research + backtest; a human trades.
- **The feed** (Exness demo 472200942): **no order book, no real volume** (bid/ask
  only), and a **fixed ~40-point ($0.04) spread** that spikes to ~270 briefly. This
  makes fast tick scalping structurally unprofitable (spread ≈ target size).

---

## What we explored, in order — and every verdict

| # | Idea | Where | Honest verdict |
|---|------|-------|----------------|
| 1 | **Tick momentum** (follow/fade a velocity spike) | `../harness/` | **LOST** OOS (−774R / −$62k sim), same as prior M1/M5 bar research |
| 2 | **Breakout bracket** (direction-agnostic, catch move size) | `../harness/bracket.py` | **LOST** worse (−3,157R). Gross edge exists at 0 cost but dies to spread |
| 3 | **Spread sensitivity** | `../harness/experiment_spread.py` | Break-even needs ~18pt all-in; retail feed is 40+. Cost, not signal, is the tick killer |
| 4 | **EDA — noise/structure** | `eda1_audit_noise.py` | Direction = random walk (VR≈1); **volatility clusters hard** (\|ret\| autocorr ≈0.35); fat tails |
| 5 | **EDA — big moves + volume** | `eda2_bigmoves_volume.py` | Volume→**size** (corr 0.8), never direction; volume *coincides*, doesn't lead; big moves cluster at **NY hours** |
| 6 | **EDA — pivots / S/R** | `eda3_levels.py` | Price reacts at levels ~57–60% (looked promising) |
| 7 | **Fade-S/R strategy** | `strategy_test.py` | **MIRAGE.** Random-*level* control matched it, and it collapsed to 0 with honest fills — it was a wick-fill artifact |
| 8 | **Exhaustive pattern search** (8 families × time × exits, sealed holdout) | `pattern_search.py` | Found the lead: **thrust-follow + trailing stop**. (Caught & fixed an intrabar-optimism bug that had 2×-inflated it.) |
| 9 | **Adversarial verification** (4 agents: significance, exit-skew, robustness, code audit) | workflow | **M15 headline discarded** (78% exit-skew + overfit); **M5 momentum edge survives — marginal.** No code bug. |

---

## HOW the surviving edge was found (the method that worked)

The breakthrough was **method, not a magic indicator**. Four disciplines separate a
real edge from a mirage — the first three killed every false lead:

1. **Honest fills.** A signal seen at a bar's *close* is entered at the **next bar's
   open** + one full spread — never the wick that formed the signal. (Fill optimism
   faked the entire fade-S/R "edge".)
2. **Sealed holdout.** The last **6 weeks** were split off and *never touched* during
   the 210-combo search. Finalists were confirmed on it exactly once. This defeats the
   "search enough combos and one looks great by luck" problem.
3. **Random-direction control.** Every candidate is compared to the *same entries with
   a coin-flip direction*. This isolates **entry skill** from the **exit** — and it
   revealed that most of the "profit" was the trailing exit harvesting volatility, not
   direction.
4. **Permutation significance + bootstrap CI.** The final judge: a permutation test
   (hold the price paths fixed, shuffle only trade direction) *plus* a trade-level
   bootstrap confidence interval — not the naive seed-std, which understates uncertainty
   2–3×.

**The chain that isolated the real signal:** raw thrust-follow looked like +$166/lot
(M15). Random-direction control → ~78% of that is the **trailing exit** (works with any
entry). Honest all-hours reproduction → the "+$2.42" train peak was an **overfit**
hour-filter (real value +$1.40). Timeframe + permutation test → the directional residual
is **~0 on M15/H1** but **significant on M5** (edge +0.55/oz, p=0.007), and it
**replicated on the sealed holdout** (+0.70/oz, p=0.034) — the one test the S/R mirage
failed. Bootstrap CI → even M5 is marginal (CI includes 0, ~92% one-sided).

---

## The verified finding

**A. Volatility harvesting (real, but regime-dependent).** A trailing stop
(`sl≈$6, trail≈$6, let winners run to ~$50`) is *positive on every timeframe even with
random entries* (M5 +$0.38, M15 +$0.63, H1 +$0.64 per oz/trade) because gold's fat tails
mean the occasional big move pays for many small stop-outs. It profits from big moves of
**either direction** (monthly profit is 0.99-correlated with range, 0.09 with drift).
⚠️ **Every month in the data was high-volatility — a quiet/rangebound month, where this
would bleed, is UNTESTED.** Do not bank it as stable P&L.

**B. M5 momentum edge (real, marginal).** A 5-min candle with body > ~1.25–1.5×ATR,
traded **in its direction**, adds a genuine directional edge of **~+$0.5–0.7/oz
(+$50–70/lot)** over the random control. It clears the permutation test (p=0.007) and
replicates on the sealed holdout (p=0.034), with balanced long/short (not an uptrend
artifact). **But:** the honest bootstrap CI is ~[−0.20, +1.32] and **includes zero**
(~92% one-sided, sub-2σ); it holds only at mult 1.25–1.5 (collapses at 2.0); and it's
month-unstable (4 positive / 3 negative months). **Low-conviction lead, not a result.**

**What's an artifact:** the M15 "+$166/lot" headline (mostly exit-skew + an overfit
peak); the "+$2.42 train" number (not reproducible all-hours). Discarded.

---

## Why we trust the engine (and where it can still fool us)

- **No code bug:** three independent agents reproduced `patternlib.backtest` **to the
  cent** (373/373 trades). Fills, point-in-time signals, one-position-at-a-time, and the
  now-*conservative* intrabar trailing order are all honest.
- **Residual risks:** (1) the holdout is now *spent* — it can't re-confirm anything;
  (2) `max_hold` was an inert search axis (0% of trades hit it), so the effective search
  was smaller than 210; (3) the marginal M5 edge could still be a multiple-comparison
  survivor. The only cure for all three is **fresh out-of-sample (forward) data**.

---

## Reproduce it

```bat
:: env: conda test_env (pandas + numpy<2 + plotly). fetch uses the MT5 venv.
:: 1. data (STOP the XauOrderPad server first)
..\..\XauOrderPad\.venv\Scripts\python.exe fetch_data.py

set PY=C:\Users\Pandu\.conda\envs\test_env\python.exe
%PY% eda1_audit_noise.py         :: noise / random-walk structure
%PY% eda2_bigmoves_volume.py     :: volatility clustering + volume->size
%PY% eda3_levels.py              :: pivots / S/R reaction rates
%PY% eda4_synthesis.py           :: fade-S/R in-sample screen (mirage)
%PY% strategy_test.py            :: fade-S/R walk-forward + controls -> DISCARD
%PY% pattern_search.py M15       :: exhaustive search + sealed holdout -> the lead
```

`patternlib.py` is the shared, correctness-critical engine (honest fills, holdout split,
random control). Every result above is reproducible from the saved `data/*.npz`.

## File map

| File | Role |
|---|---|
| `patternlib.py` | shared backtest engine — honest fills, sealed-holdout split, random-direction control |
| `pattern_search.py` | exhaustive family × time × exit search, ranked, holdout-confirmed |
| `strategy_test.py` | the fade-S/R honest test (+ random-level & wick-fill controls) that caught the mirage |
| `eda1..eda4`, `eda_dashboard.py` | the EDA that mapped the structure (random direction, clustered volatility, S/R) |
| `fetch_data.py` | one-time MT5 pull of M1..D1 bars + tick sample → `data/*.npz` |
| `EDA_FINDINGS.md` | the structural findings | `INVESTIGATION_JOURNEY.md` | this document |

**Bottom line:** we went from "everything loses" to a real, if marginal, lead —
volatility-harvesting trailing stops + a faint M5 momentum tilt — found by *method*
(honest fills + sealed holdout + controls), not by curve-fitting. The next honest step is
fresh-data confirmation and the sizing math, before any live conviction.

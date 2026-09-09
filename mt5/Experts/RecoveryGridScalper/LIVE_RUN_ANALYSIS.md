# RecoveryGridScalper — live run post-mortem

**What went wrong, measured.** Every figure here traces to a `RecoveryGrid_v2_*` log row or a
tick-by-tick reconstruction from MT5 deal history. Nothing is restated from memory. Written so the
strategy can be optimised from evidence rather than from impressions.

Data: 2026-09-03, demo account 472627873 (Exness-MT5Trial16), XAUUSD, magic 532040.

---

## 0. What these runs measure — and what they do not

**The operator chose each cycle's direction from the chart.** Those entries are trend guesses; with
respect to this engine they are random. **So the P&L of a live run measures the entries, not the
algorithm.** A losing session is not evidence the algorithm is wrong, and a winning one is not
validation.

What the runs *do* establish is **mechanical conformance** (does it size and throttle as designed?)
and **defect discovery** (what breaks when real ticks arrive). Both are in here.

---

## 1. The headline: three accounts lost to a 1–2 point shortfall

The give-back arm is:

```
net > 0  AND  net <= best - (15% of balance)
```

It cannot fire *at all* unless the peak exceeds 15% of balance — below that the threshold is
negative and the in-profit-only guard blocks it. Reconstructed from ticks, every account wipe on
record:

| wipe | balance | peak | peak % | needed | **short by** | account went |
|---|---|---|---|---|---|---|
| c59 | 13.09 | +1.63 | **12.5%** | 15% | **$0.33** | 13.09 → 0 |
| c3 | 12.26 | +1.59 | **13.0%** | 15% | **$0.25** | 12.26 → −1.38 |
| c12 | 317.95 | +44.83 | **14.1%** | 15% | **$2.86** | 317.95 → 0.00 |
| c55 | 11.01 | −0.15 | never went green | — | — | *not this defect* |
| run2-c8 | 161.37 | +0.00 | never went green | — | — | *not this defect* (see §9) |

> **$3.44 of unreachable profit cost $353 of account.**

Three of the **five** wipes are the same defect. **c55 and run2-c8 are the honest exceptions** —
neither ever went green, so no exit rule could have saved either; they are excluded from the count
rather than used to pad the case. That split matters: **2 of 5 wipes are unreachable by any exit
change**, which is why the exposure cap outranks the exit arm. See §9.

Cycle 12 is the sharpest instance: it was **in profit on 62 of its 877 ticks**, peaked at 14.1% of
balance, and could not exit on a single one of them because the rule wanted 15%.

**Arming at 11% of balance clears all three** — 12.5%, 13.0% and 14.1% all sit above it, tightest
margin 1.5 points. 12% would leave 0.5 points, which is inside the noise. 11% is not invented: it is
the operator's own **p10 exit** across 41 profitable video cycles (min ever observed 6.0%, median
28.9%).

---

## 2. The problem is entirely in the tail

Across the 15 logged v2 cycles:

| group | n | net |
|---|---|---|
| exited via an exit arm (target or give-back) | 12 | **+239.00** |
| ran to a broker stop-out | 2 | **−339.21** |
| closed by the operator | 1 | −25.10 |

The body is profitable. **Two cycles out of fifteen more than erased it.** Both runs show the same
shape — a string of wins, then one cycle that takes them all:

| run | path | ended |
|---|---|---|
| A (c1–c3) | 10.00 → 12.26 in 2 cycles | c3 stop-out −13.64 → **−1.38** |
| B (c4–c12) | 100.00 → 317.95 in 8 cycles | c12 stop-out −325.57 → **0.00** |

*(Cycle 9's logged `net_broker` of +87.34 carried a history-window bug — see §5.6. Its true figure,
+41.37, is used throughout this document.)*

---

## 3. What sets the magnitude: a fixed exposure cap

The dead zone explains *why* a cycle could not exit. `MaxTotalLots = 1.00` explains *how much it
cost*. It is a **fixed** lot cap while every other constant scales with balance, so from ~0.09 lot
upward it binds before the depth budget and the depth cap stops being the real limit:

| balance | lot | oz held | **$/oz move that wipes the account** |
|---|---|---|---|
| 10.00 | 0.01 | 9 | **1.11** |
| 100.00 | 0.07 | 84 | **1.19** |
| 164.00 | 0.09 | 100 | **1.64** |
| 317.95 | 0.10 | 100 | **3.18** |

**Gold moved $3.59 during cycle 12** (4486.909 … 4490.503). The account was sized so that a routine
move erased it.

The risk budget certified **44% of balance**; cycle 12's realised drawdown was **98.9%**. The
closed-form drawdown prices the worst case as *"one step past the last rung"* — it assumes price
stops the instant you stop adding. Cycle 12 hit `MaxTotalLots` **7 seconds in**, then held 100 oz
for **4m 41s** while price ran. **Depth exhaustion caps the number of positions, not the loss**;
with no stop loss, every further cent costs 100 oz × $0.01 and nothing bounds it.

---

## 4. Cycle 12 in full — the worked example

```
16:46:42  opens 3 buys @ 4490.299, lot 0.10, balance 317.95
16:46:45  adds 3 @ 4490.045
16:46:47  adds 3 @ 4489.933
16:46:49  adds 1 @ 4489.716  -> 10 legs, "MaxTotalLots 1.00 reached"   (7 seconds in)
          ... holds 100 oz for 4m 41s, cannot add, no stop loss ...
16:47:26  +44.83  <- PEAK, 14.1% of balance.  Target 91.89, give-back needs 47.69. Neither reachable.
16:51:31  -314.57 -> STOP-OUT, balance 0.00
```

---

## 5. Defect register

| # | defect | evidence | status |
|---|---|---|---|
| 5.1 | **Give-back dead zone** — arm unreachable when peak < give-back constant | 3 wipes, §1 | **pending** |
| 5.2 | **Fixed `MaxTotalLots`** while everything else scales with balance | §3 table | **pending** |
| 5.3 | **Depth cap scales *up* with balance** — a winning cycle buys the next one more exposure (cap 9→12 at $13) | c58 +3.21 then c59 −13.80 | **pending** |
| 5.4 | **Commission excluded from every exit arm** — `POSITION_PROFIT` omits it | §6 | **deferred by operator** |
| 5.5 | **`last_add_price` sampled after the batch**, not at its fill price, so adds fire tighter than `AddStepUSD` | c1 b3 fired at **+0.003** against a configured 0.05 | **pending** |
| 5.6 | `net_broker` history window swept in the previous cycle's closes | c9 logged +87.34 vs a true +41.37 | **fixed** |
| 5.7 | A liquidated cycle left **no log row at all** | Sep 3 log read 8 of 13 cycles, omitting every stop-out | **fixed** |
| 5.8 | `worst_pnl` / `worst_equity` sampled only at the top of the tick | c51 logged −2.37 / 9.47 vs a true −2.52 / 8.99 | **fixed** |
| 5.9 | `cycle_id` restarts at 1 on every re-attach | `cycle_id 4` in 8 OPEN rows from 4 runs | **fixed** |
| 5.10 | "add BLOCKED" note written + flushed **every tick** | c55 wrote 15 identical rows in the poll path | **fixed** |
| 5.11 | `DailyLossKill` a flat **$0.50** — 0.5% of a $98 account; commission alone spent it | halted the operator mid-session | **fixed** |
| 5.12 | **Unwind drift** — `CloseAll` closes sequentially, so the decision price and the fill price differ | 15 legs took **7 s**, fills moved **$0.96/oz** = **±$130** on a 135 oz basket, *larger than the cycle target* | **pending** |

### Conformance (this part is working)

Checked against the deployed formulas — sizing conforms exactly:

| check | c1 (bal 10.00) | c2 (9.79) | c3 (12.26) |
|---|---|---|---|
| lot from the balance tier | 0.01 ✓ | 0.01 ✓ | 0.01 ✓ |
| batch from `RGS_GroupFor` | 3 ✓ | 3 ✓ | 3 ✓ |
| depth cap from `RGS_MaxPositionsFor` | 9 ✓ | 9 ✓ | 9 ✓ |
| positions ≤ cap | 9/9 ✓ | 6/9 ✓ | 9/9 ✓ |

---

## 6. Commission is $11.00 per LOT per entry

Exact across 10 cycles at three lot sizes. Charged **at the open**, once per position, and **not in
`POSITION_PROFIT`** — so every exit arm fires on a number better than the account's.

| lot | per position | per 1.00 lot |
|---|---|---|
| 0.01 | $0.110 | **$11.00** |
| 0.07 | $0.770 | **$11.00** |
| 0.09 | $0.990 | **$11.00** |

Because the balance ladder raises **both** the lot and the position count, cost per cycle grows on
two axes: **$0.99** a cycle at $10 balance → **$10.89** at $164, which is **23% of that cycle's
$47.48 target**.

---

## 7. Optimisation levers, with measured impact

### Lever A — arm the give-back on a reachable peak

```
armed once  best >= RecoveryArmPct% x balance          (11.0 — the operator's p10 exit)
exit when   net > 0 AND net <= best - min(gb_flat, best x (1 - KeepPeakPct))   (75%)
```

| cycle | as deployed | with the lever |
|---|---|---|
| **c12** | **−325.57** | **−0.07** |
| c3 | −13.64 | **+0.59** |
| c51 | −0.74 | +0.02 |
| c53 | −0.35 | +0.54 |

Video acceptance (`test_exit_rules.py`) stays **3/5** — no regression.

### Lever C — floor every exit at the cost of the basket — **REFUTED 2026-09-07**

```
floor = commission_paid + MinNetPct% x balance
arm the trail only once  best x KeepPeakPct >= floor
```

**Proposed and refuted the same week.** It was first scored against 2026-09-07 c3 alone, where it
looked excellent (+7.09 net having risked −4.49 instead of −177.13). Scored against **all three** of
that day's cycles it collapses:

| cycle | deployed | Lever C (MinNet 2%, keep 0.75) | legs used |
|---|---|---|---|
| c1 | **+31.28** | −0.46 | 6 of 9 |
| c2 | **+34.87** | −5.70 | 6 of 12 |
| c3 | +45.08 | **+7.09** | 5 of 15 |
| **total** | **+111.24** | **+0.93** | |

**It turns +111.24 into +0.93.** And there is no middle setting — sweeping MinNet 2–20% ×
keep 0.60–0.92 the behaviour is **binary**:

| MinNet | total net | worst dd | what happens |
|---|---|---|---|
| 2% | +0.93 | −18.8 | fires within seconds; kills the P&L, caps the drawdown |
| **≥5%** | **+111.24** | −177.1 | **never fires** — the target arm gets there first; identical to deployed |

The arithmetic forces it. The floor is `commission + MinNet% × balance`, and commission alone is
already **7.5% of balance** on a 15-leg basket. Set MinNet high enough to be worth taking and the
floor sits above where the 28.9% target fires anyway; set it low enough to fire early and you are
closing for less than the trade cost.

**This is the second lever refuted by widening the sample, and both failures were the same mistake
on my part — scoring a candidate only against the cycles it rescues. Score every candidate against
winners AND catastrophes, in one table, always.**

### Lever B — scale the exposure cap with balance

```
max_oz = MaxRiskPct% x balance / SurviveMoveUSD
```

At 60% / $5: $10 → 1 oz, $100 → 12 oz, **$318 → 38 oz (not 100)**, $1000 → 120 oz. Cycle 12 would
have held 38 oz and lost **~$118 instead of the account**.

---

## 8. What is NOT measured — read before tuning

* **Lever A exits too early — no longer hypothetical, now measured.** Arming at 11% makes the arm
  reachable, but on **run-2 cycle 5** it would have closed at **+0.28 gross = −8.96 net** against an
  actual **+19.77**: a measured cost of **−$28.73** on a single winning cycle. See the decision log
  in §9. Every earlier cycle used to justify the lever was one that ended badly; this is the first
  reconstruction of what it costs on one that did not.
* **Cycle 9 is the counter-example.** It went to −105.59, recovered, and closed on target for
  **+41.37 net**. A quick bounce exit would have banked ~+18 gross = **+7 net** after $10.89
  commission. Waiting was worth $34 on that cycle.
* **On cycle 12, Lever A gives breakeven, not profit** (−0.07). The tape gapped 44.83 → 10.93
  between ticks, so the exit lands low. It converts a −325 into a −0.07; it does not make money.
* **Neither lever makes this strategy profitable.** They bound the tail so a run ends *measurable*
  rather than at zero. The replay evidence in `PROJECT_STATUS.md` (0 of 104 walk-forward runs
  survived) still stands.
* **What to watch next:** cycles closing `giveback` with `best_pnl` far below `target` where price
  then keeps running. If that becomes common, raise `KeepPeakPct` or `RecoveryArmPct` — do **not**
  revert, or the dead zone returns.

---

## 9. Open recommendations — decision log

**Append-only. Every entry is timestamped so a recommendation can be re-judged against a bigger
sample rather than re-argued from memory. Nothing in this section is implemented.**

---

### 2026-09-03 18:32 UTC — armed give-back: DEFERRED, gather more cycles

**Observed.** Run 1788478242 cycle 5 (18:11:47–18:12:41). SELL, 12 legs × 0.07 = **84 oz** on a
**$108.35** balance; target 31.31, give-back 16.25, commission 9.24.

```
18:12:12  +10.02   <- operator's observation: "+10, some legs green some red"
18:12:19  +13.38
18:12:30  -53.82   <- 64 points below that reading, 18 s later
18:12:40  +32.87   -> TARGET fires; closed +32.92 gross / +19.77 net
```

Two things are both true, and they pull in opposite directions:

1. **The basket genuinely could not exit while in profit.** The give-back arm needs the peak above
   16.25; the peak stayed below it until 18:12:39. **For 27 seconds no rule could close a profitable
   basket** — the §1 dead zone again.
2. **Waiting is not predictable.** +13.38 → −53.82 → +32.87 is an **86-point swing in 30 seconds**
   on a $108 account. It recovered by path, not by design. It **risked −61.40 (56.7% of balance) to
   make +19.77 (18.2%)** — **3.1× risked per $1 made**, the worst ratio on record bar cycle 9.

**Measured against this cycle, the proposed fix loses money:**

| rule | fires | gross | **net after $9.24 commission** |
|---|---|---|---|
| **actual** (target arm) | 18:12:40 | +32.92 | **+19.77** |
| proposed: arm 11%, keep 75% | 18:12:21 | +0.28 | **−8.96** |
| operator's "exit at +10 once green" | 18:12:12 | +10.02 | **+0.78** |

The proposed arm engages at the +13.38 peak, sets its exit at 10.03, and the tape **gaps straight
through to +0.28**; commission turns it into a loss.

**Full trade-off across every reconstructed cycle:**

| cycle | actual | arm 11% / keep 75% |
|---|---|---|
| c12 | **−325.57** | −0.07 |
| c3 | −13.64 | +0.59 |
| c51 | −0.74 | +0.02 |
| c53 | −0.35 | +0.54 |
| **run-2 c5** | **+19.77** | **−8.96** |
| *net* | *−320.53* | *−7.88* |

It converts catastrophes into breakevens **and winners into small losses.**

**The number that outranks the exit question:** *as of this entry* — 22 logged cycles,
**risked $804.67 to net −$46.51.** No exit constant repairs that ratio. Cycle 5 risked 57% of the
account for an 18% gain and was one of the *good* outcomes. (This aggregate drifts as cycles are
added; each log entry states it at its own timestamp. Superseded below.)

**Candidate levers, neither approved:**

| lever | measured effect | status |
|---|---|---|
| A — armed give-back (arm 11%, keep 75%) | rescues c12/c3; **costs −$28.73 on run-2 c5** | **not approved** |
| B — balance-scaled exposure cap (`max_oz = MaxRiskPct% × balance / SurviveMoveUSD`) | c12 would hold 38 oz not 100, losing ~$118 not the account | **not approved** |

**DECISION (operator, 2026-09-03): gather more cycles before changing any exit constant.** The
trade-off rests on **5 reconstructed cycles** — too few to trade a measured winner-cost against a
measured catastrophe-saving.

**Re-analysis trigger — revisit when either happens:**

* the **tick-reconstructed** sample reaches ~15 cycles (not the logged-cycle count — only 5 have
  been reconstructed tick-by-tick), or
* **any further account wipe** occurs.

**On re-analysis, report both sides in one table** — catastrophes *and* winners. A lever scored only
against the cycles it rescues will always look free.

---

### 2026-09-03 18:35 UTC — TRIGGER FIRED: a fourth wipe, and it shifts the evidence

Three cycles arrived within minutes of the entry above. One of them is **another account wipe**, so
the re-analysis trigger set 3 minutes earlier has already fired.

| run | cyc | dur | pos | balance | peak | worst | net | reason |
|---|---|---|---|---|---|---|---|---|
| 78242 | 7 | 10 s | 6 | 172.49 | +27.07 (15.7%) | −9.17 | −11.12 | giveback |
| **78242** | **8** | **15 s** | **11** | **161.37** | **+0.00 (0.0%)** | **−154.40 (−95.7%)** | **−165.29** | **external** |
| 78242 | 9 | 13 s | 6 | 100.00 | +30.00 (30.0%) | −9.66 | +22.23 | target |

**Cycle 8 never went green — peak +0.00.** It opened, ran straight against the position, and was
liquidated **15 seconds later** having lost 95.7% of the balance. **No exit rule of any kind could
have saved it**, because there was never a moment of profit to exit into.

**This changes the wipe ledger:**

| wipe | peak % of balance | cause |
|---|---|---|
| c59 | 12.5% | dead zone |
| c3 | 13.0% | dead zone |
| c12 | 14.1% | dead zone |
| c55 | never green | **exposure** |
| **run2-c8** | **never green** | **exposure** |

**Two of five wipes are unreachable by any exit fix.** That is a direct argument for **Lever B (the
exposure cap) over Lever A (the exit arm)** — the exit arm addresses at most 3 of 5, while the
exposure cap bounds all 5.

**Aggregate at this timestamp: 25 logged cycles, risked $977.90 to net −$200.69.** Close reasons:
11 target, 10 giveback, **3 external**, 1 manual.

**Decision unchanged — still gathering.** But the next analysis should lead with the exposure cap,
not the exit arm, and the wipe ledger above is the reason.

> **Nothing in this section has been implemented.** The EA as deployed still uses the flat 15%
> give-back and the fixed `MaxTotalLots = 1.00`.

---

---

### 2026-09-07 14:47 UTC — the "miracle" cycle was the FILL, not the strategy

Three cycles ran. The operator flagged the last as a miracle (free margin to ~62, back past 300,
closed in profit) and asked why it did not exit at the ~190 bounce instead of holding for target.
**It never held for target.** It decided to exit at **breakeven** and was rescued by execution drift.

| cyc | bal | lot | legs | target | peak | worst | worst % | net | reason |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 125.08 | 0.07 | 9 | 36.15 | +38.18 | −3.94 | 3.1% | +32.29 | target |
| 2 | 157.37 | 0.09 | 12 | 45.48 | +49.14 | −52.38 | 33.3% | +41.23 | target |
| **3** | **198.60** | **0.09** | **15** | 57.40 | +31.10 | **−177.13** | **89.2%** | +115.76 | giveback |

**Risk escalates with balance on two axes at once** — lot *and* leg count both grow: 3.1% → 33.3% →
**89.2%** across three consecutive cycles.

**Equity fell to $6.62 on a $198.60 balance — 96.7% drawdown, ~$7 from a stop-out.** The operator saw
~62 (equity was 60.76 at 14:46:23 and 62.92 at 14:46:40); the true low was ten times worse.

**What the exit actually did:**

* **14:47:07** — the EA reads the basket at **+0.05** and fires the **give-back** arm.
* The unwind of 15 positions takes **7 seconds**; fills run **4413.565 → 4414.522** (+$0.957/oz).
* Realised **+130.61 gross / +115.76 net**.

**Every cent of that profit is drift during the close.** The decision was breakeven.

**Why it could not exit at the operator's ~190 — both failure modes of one rule, in one cycle:**

| moment | equity | net | why no exit |
|---|---|---|---|
| 14:46:06 | 193.40 | +9.65 | peak still **below** the 29.79 give-back constant → **arm not armed** |
| 14:47:03 | 191.30 | +7.55 | peak 31.10 → threshold `31.10 − 29.79` = **1.31**; +7.55 is *above* it |

When `best < gb` the arm is unreachable (§1). When `best ≈ gb` the threshold sits on zero and the arm
**only fires at breakeven**. The give-back is subtracted as an absolute fraction of balance, so a
peak that barely clears it leaves nothing to trail.

**Why every "exit on the bounce" variant loses here — the missing constraint is cost.** Commission on
this basket was **$14.85** (15 legs × 0.09 lot × $11/lot) = **7.5% of balance**:

| rule | fires | gross | **net** | worst seen before exit |
|---|---|---|---|---|
| deployed (flat 15%) | 14:47:07 | +59.93 | +45.08 | **−177.13** |
| Lever A — arm 11% / keep 75% | 14:46:09 | +14.44 | **−0.41** | −42.31 |
| operator's "exit at +6 once green" | 14:45:54 | +7.15 | **−7.70** | −4.49 |
| **Lever C — cost-aware floor** (MinNet 2–3%, keep 0.75–0.85) | **14:45:55** | +12.04 | **+7.09** | **−4.49** |

**Lever C: `floor = commission_paid + MinNetPct% × balance`, arm the trail only once
`best × keep ≥ floor`.** It is the only variant that nets positive here, and it does so having risked
**−4.49 instead of −177.13**.

**Honest caveat on Lever C:** it fires **3 seconds in, with 5 of 15 legs open**. It barely lets the
grid work — it turns the recovery grid into a fast scalper. **That is a strategy change, not a tuning
change**, and it is the operator's decision.

**Still gathering — nothing changed in the EA as a result of this analysis.**

---

### 2026-09-07 (later) — Lever C REFUTED, and what "strategy change" means

The operator asked what *"Lever C turns the recovery grid into a fast scalper — a strategy change,
not a tuning change"* actually means. Answering it against the real fills refuted the lever.

**What it means, concretely.** Cycle 3 opened in **five batches over 15 seconds**:

| batch | time | legs | fills |
|---|---|---|---|
| b1 | 14:45:52 | 1–3 | 4413.615 / 4413.327 / 4413.388 |
| b2 | 14:45:55–56 | 4–6 | 4413.251 / 4413.251 / 4413.294 |
| b3 | 14:45:58–14:46:00 | 7–9 | 4413.068 / 4413.031 / 4413.011 |
| b4 | 14:46:02–03 | 10–12 | 4412.948 / 4413.346 / 4413.346 |
| b5 | 14:46:06–07 | 13–15 | 4413.083 / 4413.258 / 4413.444 |

**Lever C fires at 14:45:55 — inside batch 2, with 5 legs open. Batches 3, 4 and 5 never happen.**
Those later legs *are* the strategy: legs 7–15 went in between 4413.011 and 4413.444 while price
fell, and that averaging is what drags the basket's break-even toward price so a small bounce
recovers the whole thing. Remove them and nothing is left but a 5-leg position closed on the first
profit that clears commission — a **scalper**. Cycle 2 is starker: it fires **2 seconds in, 6 of 12
legs**, on a cycle that went to −52.39 and recovered to **+34.87**; Lever C books **−5.70**.

*Tuning* changes when the same machine exits. *A strategy change* stops the machine doing the thing
it is named for. This is the second.

**The correction.** The §9 entry above scored Lever C against c3 alone. Across all three cycles it
turns **+111.24 into +0.93**, and the sweep is binary (see §7). **Lever C is refuted.** That is
exactly the failure this document warns about two entries earlier — *"a lever scored only against
the cycles it rescues will always look free"* — committed by the author of the warning.

**Levers still standing: B only** (the balance-scaled exposure cap). That is consistent with the
wipe ledger in §1: **2 of 5 wipes never went green**, so no exit rule could ever have reached them.

**Still gathering. Nothing in the EA changed.**

---

### 2026-09-07 (evening) — TREND mode + a time-decaying QUICK arm, gated

Two new behaviours built at the operator's request. Both are **departures from the source**, both are
inputs, and both default so the previous behaviour is one setting away.

**1. `AddMode` — TREND (default) / GRID.** Until now the grid added only while underwater. Checked
against the video: of 261 add events, filtered to those where the OCR was clean **and** the equity
identity `equity_before − balance == pnl_total_before` holds, **33 of 34 (97%) happened with the
basket in loss** — the one exception was at +1.1% of balance. So `if(net < 0)` was not an oversight;
it is what the source did. TREND adds on any move ≥ step in **either** direction, on the reasoning
that direction here is the operator's call rather than the algorithm's. Everything else — cooldown,
depth cap, margin floor, lot cap — is identical, so the modes differ in one line and stay comparable.

**2. The QUICK arm.** `net >= QuickExitPerOz(age) × total_oz`, decaying linearly **0.30 → 0.10 $/oz**
above average entry over 60 s. Motivated by measurement: duration is the strongest single predictor
of trouble here — cycles finishing inside 40 s had a median worst drawdown of **5–15% of balance**;
those still open past 40 s had **57–89%**.

**The constraint that shaped it, and nearly broke it.** The operator's requirement was that the
28.9% target must **not** become dead code. Measured: at `QuickExitStartUSD = 0.30` the quick arm is
nearer than the target on **31 of 31 logged cycles** — it would have fired first every time and the
target would never have run again.

Fix: gate the quick arm behind *"this basket has been down ≥ `QuickExitArmPct` of balance"*, which
is the operator's own description (*"we hold only in case we are in loss and wait for recovery"*).

| gate | keep the 28.9% target | eligible for QUICK |
|---|---|---|
| **−15% of balance** | **16 of 31 (52%)** — and they are the clean winners: +24.36, +26.73, +39.04, +32.29, **+90.25**, +36.38 | 15 (48%) |

~~−15% is not fitted. Among cycles running ≥45 s the sick ones bottom at −56.7% or worse and the
healthy ones only reach −5.5%; nothing lands between.~~ **REFUTED the same evening — see the next
entry.**

**All three arms stay IN PROFIT ONLY** — none ever closes a red basket. Unchanged, and deliberate.

**Explicitly NOT verified.** The parameter sweeps used along the way (which produced headline numbers
like +206 and +322) **assume a peak is reachable whenever it exceeds the threshold, ignoring time
ordering** — they let a rule capture peaks it could not have reached because its arm was not yet
live. **Those figures are inflated and must not be quoted.** Confirmation needs tick replay on the
cycles where entries are known (2026-09-07 ×4; 2026-09-03 c3 and c12). `close_reason = quick` is
logged so the three arms can be separated in the next analysis.

### 2026-09-07 (late) — the ordered replay: constraint HOLDS, its justification DOES NOT

Built `analysis/video_ocr/replay_quick_arm.py` and ran the verification promised in the entry above,
across **all 31 v2-logged cycles** rather than the six originally scoped. It rebuilds each basket
from the real `OPEN` rows, pulls the real ticks for the window, activates each leg at its true fill
instant, and evaluates the arms in `OnTick` order. Reconstruction checks out: **leg count and total
lots match the cycle log exactly on all 31**, and replayed `worst_pnl` tracks the logged value.

**The constraint holds: 0 violations.** 16 of 31 cycles never open the gate, so they keep the 28.9%
target and the give-back. The target is not dead code.

**But the reason given for −15% was wrong.** Distance from the arm line, gate-shut cycles first:

| cycle | cushion / overshoot |
| --- | --- |
| `8242/c1` shut by | **$0.17** |
| `1794/c6` shut by | $0.53 |
| `1794/c1` armed by | $0.54 |
| `8242/c6` armed by | $0.63 |
| `1794/c2` armed by | $0.85 |
| `1794/c12` armed by | $0.88 |

**Ten of 31 sit within $6 of flipping.** There is no gap. The gate works, but it is a knife-edge on a
third of the book, and that is a different claim from the one this document made an hour earlier.

Two further findings, both of which limit what any offline replay of this system can say:

* **Gate state depends on TIMING, not on the cycle.** `8242/c1` is gate-shut when give-back fires
  (worst −13.50 vs arm line −13.66) yet its full-cycle worst is **−31.53**. A few more seconds and
  the same cycle belongs to the other arm.
* **The broker's tick archive is not what the EA saw.** `2147/c1` replays a peak of **+216.67**
  against a logged **+31.10**; four `target` closes replay peaks of ~$12–21 against a logged ~$29.
  The archive smooths fast spikes in both directions. So *which arm fires first* is indicative only.
  The gate classification is the part worth trusting, because it rests on `worst_pnl`, which
  reconciles.

**This is the third time a lever looked good until it was scored properly** (Lever A, Lever C, now
the −15% gap claim), and the second time the error was *ignoring time ordering*. The §7 standing rule
gains a clause: **a threshold justified by "a gap in the data" must be shown as a per-cycle distance
table, not as two summary statistics.** Two aggregates can straddle a gap that no individual cycle
respects — which is exactly what happened here.

**Still unverified, and not verifiable offline:** that the *MQL5* implements this. The replay tests
the design in Python; the shipped code is checked by inspection at `GridEngine.mqh:492-501` and needs
a live run showing `close_reason = quick`.

**Lever status after this entry:** A deferred · B still the only lever standing on the exposure side ·
C refuted. The QUICK arm is *not* Lever A — A reduced the give-back threshold; this adds a separate
time-decayed arm behind a drawdown gate.

### 2026-09-08 — `MaxTotalLots` was the binding cap all along, and it logged NOTHING

Seven cycles, 471.27 → 1512.49. **Five of them opened one batch and then sat still**, and the log
did not say why.

| cycle | lot | filled | depth cap | lots | target | target $/oz | closed by | net |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.10 | 10 | 24 | 1.00 | 136.20 | 1.36 | target | +124.95 |
| 2 | 0.10 | 10 | 27 | 1.00 | 172.31 | 1.72 | quick | +29.98 |
| 3 | 0.33 | 3 | 15 | 0.99 | 180.97 | 1.83 | give-back | **−3.93** |
| 4 | 0.33 | 3 | 15 | 0.99 | 179.84 | 1.82 | target | +179.77 |
| 5 | 0.33 | 3 | 15 | 0.99 | 231.79 | 2.34 | target | +256.38 |
| **6** | 0.33 | **3** | **18** | 0.99 | 305.88 | **3.09** | give-back | +117.23 |
| 7 | 0.33 | 3 | 21 | 0.99 | 339.76 | 3.43 | target | +336.84 |

**Cause: `MaxTotalLots = 1.00`.** At `lot 0.33` the opening batch is 0.99 lots and a fourth position
needs 1.32, so `lots_ok` (`GridEngine.mqh:521`) was false on every tick. Margin was irrelevant —
$1,042 free against a $5.28 add. The depth cap said 18. **The README called `MaxTotalLots` a
"backstop only"; that line is now struck through, because it is the cap that actually binds.**

**Defect: the lot cap blocked adds silently.** The add gate logged for `!marg_ok` and `!deep_ok` but
had **no branch for `!lots_ok`**. Cycles 1–2 produced `MaxTotalLots 1.00 reached` only because at
0.10 lot the cap bit *inside* `OpenBatch`; at 0.33 it bites one level up and wrote nothing. Same
class as the 2026-09-03 log-blindness defect. **Fixed:** new branch with `block_logged=3`, plus
`mode`, `lot cap` and calibrated `margin/lot` on the `CYCLE n START` line and in a matching NOTE
(a NOTE rather than a new cycles-CSV column, which would have broken the v2 header mid-file).

**Cycle 6 in detail — it missed by five cents an ounce.** 99 oz, avg entry 4397.605. Peak +301.08 =
3.041 $/oz; the target needed 3.090 $/oz. Give-back then fired at 301.08 − 158.76 = 142.32, closing
at +141.48 → realised 128.12 − 10.89 commission = **+117.23**. The quick arm could not help: it needs
the basket down $158.76 and the worst was −$122.76. **Cycle 3 is the same defect as a loss** — peak
+109.22, exited at +15.29, **−3.93 after commission**.

**The structural shape.** `required $/oz = ExitTargetPct% × balance ÷ (MaxTotalLots × 100)`. Ounces
frozen, target scaling with balance, so every win raises the bar: 1.36 → 1.82 → 2.34 → 3.09 → 3.43,
next **4.42**. Duration climbs with it: 6 s → 31 s → 44 s → 72 s. **And the lot tier makes it a
cliff, not a slope:** the balance has just crossed $1,500, so the tier steps 0.33 → 0.99 and the
next cycle opens **one** position; above $3,400 the tier is 1.99 and nothing opens at all.

**Methodology correction, and a standing rule.** A tick-based pass suggested four adds fired while
the basket was green — a real bug had it been true, on a GRID-only build. It was an artifact: it
assumed one price per batch (fills slip across several) and sampled a full second before the
decision. The EA's own logged `bid`/`ask` disproved it. **When the question is what the engine saw,
use the logged bid/ask, not a reconstructed tick window.** This is the third time this session that
a finding survived only until it was scored properly.

**Leverage was NOT the cause today** (margin $16/lot, ~27,000:1). The 2026-09-07 1:200 window was
transient. Parked, with the operator's decision to keep the demo at 1:Unlimited, in
[LEVERAGE_DEPENDENCY.md](LEVERAGE_DEPENDENCY.md).

**Decisions taken:** demo stays 1:Unlimited · `MaxTotalLots`, `ExitTargetPct`, lot tiers and all exit
rules **unchanged** · fix the logging first, then decide with data. Operator manual written:
[PARAMETERS.md](PARAMETERS.md).

### 2026-09-08 20:00:53 — ACCOUNT WIPED. The sizing rule, and the exposure cap that replaces it

**1,274.59 → 3,913.90 → 0.00 in 2 minutes 10 seconds**, broker stop-out, equity **−79.68**
(negative-balance protection absorbed the rest). A $1 reset then grew to ~$7 over 13 cycles and lost
all of it on the 14th. **Both accounts died the same way on the same afternoon.**

**Attribution.** This ran on the *previous* binary. That evening's build hit disk at 20:05:33 and
loaded at 20:13:14 — after the wipe — and was logging-only. Sizing and TREND were identical in both.

**Trigger.** `MaxTotalLots` raised **1.00 → 50.00** at 19:58:22, to stop position rejections. Those
rejections were the risk control.

**The final cycle** — SELL, lot 1.99, 15 positions, **29.85 lots = 2,985 oz on $3,913**. Five
batches into a **$2 band in 10 seconds**; `AddStepUSD = 0.05` throttled nothing, the 2 s cooldown
was the only brake and gold moved ~1 $/oz between batches. Avg entry 4396.191, exit 4397.425 →
**1.234 $/oz × 2,985 oz = −$3,683** plus $328 commission.

**The defect.** The depth cap does shrink as the lot grows — 18 → 12 from lot 0.33 to 0.99 — but
**volume doubles** (5.94 → 11.88 lots). Across the whole ladder the ruin distance was near-constant:

| balance | lot | cap | ounces | eff. leverage | $/oz to WIPE |
|---|---|---|---|---|---|
| 471 | 0.10 | 24 | 240 | 2,242× | 1.96 |
| 1512 | 0.99 | 12 | 1188 | 3,457× | 1.27 |
| 3913 | 1.99 | 15 | 2985 | 3,357× | **1.31** |

**A $1.3–1.9 move took the whole account at any balance.** Cycle 6 died on 1.234.

**Why the 44% budget did not hold.** `RGS_MaxPositionsFor` prices drawdown as a staircase with rungs
`RGS_RISK_STEP = 0.18` apart. Live, batches landed ~1 $/oz apart (cooldown-throttled, not
step-throttled), and **TREND adds on favourable moves**, so there is no staircase at all — every
ounce is underwater together on a reversal. `Utils.mqh`'s "0.18-with-a-0.05-step is conservative"
argument holds for **GRID only**; the comment is now corrected in place. Measured: with the lot cap
at 1.00, worst drawdown across 8 cycles was 20.5%; uncapped, **68.6% and 82.3%** — breached twice.

**The identity behind all of it:**

```text
target move  =  ExitTargetPct × ruin move   =   0.289 × ruin move
```

The target always sits **29% of the way to ruin**, for any sizing, any balance. Reaching the target
easily and surviving a reversal are one dial turned opposite ways. That is the strategy, not a bug.

**Fix shipped: `RuinMoveUSD` (default 5.00), the exposure cap.** Ounces capped at
`balance ÷ RuinMoveUSD`; `RGS_MaxPositionsFor` now returns `min(staircase, exposure cap)`. It is
**mode-independent by construction** — it bounds ounces held, and ounces do not care whether GRID or
TREND acquired them. Ruin distance becomes a near-constant **5–7 $/oz** instead of 1.3–1.9. The
staircase model is kept only for parity with `lot_position_engine.py` and is no longer load-bearing.

**Scored against winners AND catastrophes, per §7** — truncating each cycle's legs at the cap and
re-pricing at the same logged exit price:

| | net actual | net capped | delta |
|---|---|---|---|
| run 5506 (8 cycles, already clamped at 1.00 lot) | +803.32 | +781.84 | −21.48 |
| 7502/c4 — a winner it shrinks badly | **+1324.33** | **−52.57** | **−1376.90** |
| 7502/c6 — the wipe | **−3993.58** | **−1244.15** | **+2749.43** |
| **all cycles** | **−552.04** | **−238.73** | **+313.31** |
| ending balance | **0.00 (wiped)** | **232.54** | |

**This is not a free win and is not being presented as one.** It costs real upside — `c4` turns a
+1,324 winner into a −53 loser, the same shape that got Lever C refuted. It barely touches a
sanely-sized run (−21 across all of run 5506). **And the set is still a net loss either way.** The
cap converts ruin into a drawdown; it does not make the strategy profitable.

**Side effect, documented not hidden:** the cap implies a minimum viable balance of `RuinMoveUSD`,
because the smallest lot is 1 oz. At the default a **$1 account is refused**, with a message naming
the setting to change. That refusal is honest — 1 oz on $1 has a ruin move of $1, and no parameter
alters that.

**Lever status:** **Lever B is now IMPLEMENTED** — it was the only lever left standing, and this is
the direct evidence for it. A deferred · C refuted.

### 2026-09-08 (late) — first exposure-cap session: both arms confirmed, and four open findings

Balance reset to $500. **Recorded before deciding anything, on the operator's instruction — no
exit-rule or sizing change was made on this evidence.**

#### 1. Both exit arms confirmed working, live

| cycle | lot | pos | outcome |
|---|---|---|---|
| 1 | 0.10 | 10/10 | clean (worst −5.52) → quick never armed → ran to **target**, 44 s, **+147.25** |
| 2 | 0.33 | 3/3 | worst **−309.78** (47.9% of balance) → gate opened, decay pulled the bar to **0.100 $/oz**, banked the bounce → **quick**, 214 s, **+18.51** |

Cycle 2 also displayed the **give-back dead zone** on the panel (`peak +63.84  exit if <= -33.25` —
a negative trigger, unreachable while green). The quick arm is what rescued it.

#### 2. Cycle 1 waited 44 s because 28.9% no longer transfers

The basket was full in **8 seconds**; the other 34 were spent waiting for price to travel.

```text
target move ($/oz)  =  ExitTargetPct x ruin move
   the video (1-3 oz on $1.47-2.00)   ruin ~0.7-1.5  ->  0.19-0.43 $/oz   (matches its 0.10-0.30 exits)
   this account BEFORE the cap        ruin  2.08     ->  0.60 $/oz
   this account NOW                   ruin  5.00     ->  1.445 $/oz
```

**28.9% was calibrated against the video's ruin distance of ~1 $/oz.** Raising ruin to 5.00 — the
change that made the account survivable — pushed the target from a ~0.30 move to a ~1.45 move.
Arithmetic, not a bug. The two settings are **one dial** and cannot be tuned separately.

#### 3. ExitTargetPct swept over 63 cycles

| pct | closed | total net | median s | target | give-back | quick | never exited |
|---|---|---|---|---|---|---|---|
| **28.9% (current)** | 32 | +1429.80 | 15 | 10 | 9 | 13 | **31** |
| **20.0%** | 43 | **+1877.26** | 13 | 28 | 3 | 12 | 20 |
| 15.0% | 47 | +1695.32 | 11 | 41 | 0 | 6 | 16 |
| 10.0% | 51 | +1164.62 | 7 | 49 | 0 | 2 | 12 |
| 6.0% | 55 | +842.13 | 5 | 54 | 0 | 1 | 8 |
| 4.0% | 57 | +640.51 | 3 | 57 | 0 | 0 | 6 |

**20% wins on every measured axis:** +447 over 28.9%, faster, and 43 cycles closed by a rule instead
of 32. **The give-back arm dies at ≤15%** (9 → 3 → 0) — once `ExitTargetPct <= ExitGivebackPct` the
target always fires first, so any move to 15% or below must lower `ExitGivebackPct` in the same
change.

**Caveat that cuts against the current setting:** "total net" counts only cycles an arm actually
closed. The "never exited" column — closed some other way, including stop-outs — contributes
nothing, so **every row flatters itself and 28.9% flatters itself most** (31 hanging vs 6).

> An earlier `ExitTargetPct ≈ 6%` suggestion was drawn from **one** cycle and is **refuted** by this
> sweep. That cycle simply suited it. Recorded as another instance of the §7 rule.

#### 4. Cycle 4 — the quick arm missed arming by $3.26

```text
balance 718.13   4 x 0.33 = 132 oz   avg entry 4393.158
quick GATE needs worst <= -107.72     actual worst -104.46   <- SHORT BY $3.26
```

Tick-verified: deepest −104.48 at 30 s; green at 35 s; +78.08 at 55 s; **+146.98 at 65 s**; give-back
fired at peak − 107.72 = +47.31, closing at 71 s for **+25.30 net**.

Had the gate opened, the arm would have fired at **35 s** for ~**+10.50 net**. **The give-back path
made 2.4× more** — on this cycle, failing to arm was the *luckier* outcome, at the cost of 36 extra
seconds and a −104 drawdown. Both are true.

**Third knife-edge instance for the −15% gate** ($0.17, $3.26, and ten of 31 within $6). At −15% it
is close to a coin flip on a large share of cycles.

#### 5. The lot tier and the exposure cap are set independently

```text
bal 500.00 -> lot 0.10 (10 oz each), cap 100.0 oz -> 10 positions
bal 647.25 -> lot 0.33 (33 oz each), cap 129.4 oz ->  3 positions
```

Crossing $600 flips the tier. The cap holds **risk** near-constant (100 vs 129 oz), but the tier
decides how coarsely it is chopped: **10 rungs became 3**. With 3 rungs and no room to add, cycle 2
sank to 48% of balance. Above $1,500 the tier is 0.99 and the cap allows **one**. Deriving the lot
from the cap — `lot = max_oz / (rungs × 100)` — would hold the rung count steady. Not built.

**Shipped this session:** the previous-cycle panel row (`prev : #1 SELL lot 0.10 peak +148.59
dd -5.52 bal 500.00->647.25 (target)`), because `peak` reset to 0 on close and a finished cycle could
not be reviewed at the chart. No trading logic touched.

**Deferred, deliberately:** the −15% gate, `ExitTargetPct`/`ExitGivebackPct`, and lot-from-cap
sizing. Gather more cycles first.

### 2026-09-09 — causality corrected, the sweep refined, and cycle 7

#### The exposure cap moved the target — `ExitTargetPct` was never changed

Worth stating plainly because it was asked directly. `ExitTargetPct` has been **28.9% throughout**.
What moved the target in *price* terms is `RuinMoveUSD = 5.00`:

```text
target $/oz  =  ExitTargetPct x (balance / ounces)
```

At balance 500: **240 oz → 0.60 $/oz** before the cap, **100 oz → 1.445 $/oz** after. Same
percentage, 2.4× further away, because the basket holds 2.4× fewer ounces. **This was the deliberate
trade for survivability after two wipes at a ~1.3 $/oz ruin distance — but the side-effect on the
target should have been stated when the cap was deployed, not discovered a cycle later.**

The video's ~0.30 exits come from the same formula: 1–3 oz on a $1.47–2.00 balance is a ruin
distance of ~0.7–1.5 $/oz, and 28.9% of that is **0.19–0.43 $/oz**. Same rule, enormous leverage.

#### Finer ExitTargetPct sweep — the peak is 21%, on a flat 20–22% plateau

The previous entry reported "20% is the peak" from six coarse points. Re-run on **66 cycles**:

| pct | closed | total net |
|---|---|---|
| 28.9% (current) | 34 | +1483.35 |
| 24% | 40 | +2123.40 |
| 22% | 44 | +2295.38 |
| **21%** | 44 | **+2337.82** |
| 20% | 46 | +2302.83 |
| 18% | 46 | +2181.53 |
| 15% | 50 | +1989.55 |

**The optimum is a broad plateau at 20–22%** — anywhere in it is equivalent, so precision beyond
"about 21%" is false confidence. Totals differ from the previous table (+1483 vs +1430 at 28.9%)
because the set grew from 63 to 66 cycles. Still **not applied** — operator's decision.

#### Cycle 7 — "every position is green, why no exit?" Answer: waiting for the target

SELL, balance 953.45, 5 positions = 165 oz, entries clustered at 4425.635 (×3) and 4426.4 (×2):

```text
 sec        ask  | per-position P&L                | BASKET
   3   4426.125  | -16.2 -16.2 -16.2  +9.4 +11.6   |  -27.49
  12   4425.315  | +10.6 +10.6 +10.6 +36.2 +38.3   | +106.16   <- the state observed
  24   4425.925  |  -9.6  -9.6  -9.6 +16.0 +18.2   |   +5.51   <- nearly gave it ALL back
  30   4424.205  | +47.2 +47.2 +47.2 +72.8 +74.9   | +289.31   -> TARGET fires
```

At +106.16: target needed **275.55**; the give-back trigger was **−18.71** (negative — dead zone);
the quick gate needed worst ≤ **−143.02** and worst was **−104.38**, short by **$38.64**. **No arm
could fire.** Closed on target at 30 s, **net +236.63**.

**Two things to carry forward:**

1. **The engine closes on BASKET net, never per position.** Split entry clusters make individual
   tickets show wildly different P&L while the total is far from any threshold. This is the second
   time a per-position view has driven an "it should have exited" expectation.
2. **Waiting paid, but narrowly.** Exiting at 12 s banks ~+88 net; it made +236.63. Yet at 24 s the
   basket was back to **+5.51** — it nearly surrendered everything before the spike. **Third
   consecutive cycle where the early-exit instinct would have made less money.** That is an argument
   for measuring across many cycles, not for dismissing the instinct.

Unlike cycle 4's $3.26, this gate miss was a clear $38.64 — the gate behaved correctly here.

#### Open design item: time decay for a basket that is in PROFIT

The decay/bounce exit currently reaches only baskets that first went `QuickExitArmPct` (15%)
underwater, so **a cycle green from the start has no fast exit at all** (cycle 1: `quick: not armed`,
44 s hold). The operator wants the decay available on a bounce-back in profit too.

**The tension to resolve before building it:** removing the underwater gate is exactly what the gate
exists to prevent — ungated at 0.30 $/oz the quick arm beats the target on **31 of 31** cycles and
the target becomes dead code, a standing hard constraint. Any design must keep the target alive and
be scored against winners **and** catastrophes first. Recorded; not designed, not built.

### 2026-09-09 15:00:51Z — THIRD WIPE. The exposure cap held, and it did not matter

Cycle 12: SELL, 3 × 0.99 lot = **297 oz** on balance **1,748.76**, stopped out at equity −169.26.
133 s. This is the third account lost (2026-09-08 ×2, 2026-09-09 ×1).

**The cap was not breached.** `RuinMoveUSD = 5.00` allowed **349.8 oz**; the basket held **297**.
Ruin distance was `1748.76 / 297 = 5.888 $/oz`. Then gold moved:

```text
sec 130   ask 4418.617   net  -389.66
sec 140   ask 4427.783   net -3111.97      <- +9.17 $/oz in UNDER TEN SECONDS
```

**Misjudgement to record against my own recommendation.** `RuinMoveUSD = 5.00` was chosen and
described as "far outside normal excursions" on a sample of **0.45–1.97 $/oz** measured over a few
quiet hours. Today's move was **4.6× the largest excursion in that sample**. The sample was too
small and too short to support the claim, and the claim was load-bearing.

**The structural point, which outranks the number:** no value of `RuinMoveUSD` prevents this. It
only sets *how large* a move is fatal. With **no stop loss**, a sufficiently fast spike always wins —
`RuinMoveUSD = 9` would also have died today. Real protections are a broker-side stop, exposure so
small the target is unreachable, or not holding through spike windows. A stop loss is **deferred by
the operator**, deliberately.

**Arm-by-arm, tick-verified — and it corrects the first reading of this cycle.** The quick arm
**did** arm, at **66 s**; past `QuickExitDecaySec = 60` the decay was already at its **floor,
0.10 $/oz = +$29.70**. But the basket **never returned to profit** — zero ticks green after arming,
best −176.42. The +182.95 peak happened *before* it went underwater. **Time decay worked; price
never came back.**

| arm | needed | reachable? |
|---|---|---|
| Target | 505.39 (1.70 $/oz) | never close |
| Give-back | retrace 262.31 from a peak that never exceeded **182.95** | **impossible — dead zone** |
| Quick | armed 66 s, needed +29.70 | armed, but never green again |

**The give-back dead zone has now cost three accounts.** At `ExitGivebackPct = 5%` the trigger would
have been `182.95 − 87.44 ≈ +95`, firing near 55 s and leaving the account around **1,838 instead of
0**. Candidate fix: express give-back as a fraction of the **peak**, not of balance, which removes
the dead zone by construction. **Not applied — must be swept against winners and catastrophes
first.**

**Operator's direction after this wipe:** three separate problems — (1) the lot tier cliff starving
the grid of rungs, (2) the target distance, (3) the exit arms in a losing cycle — each analysed on
its own, **fixed only on statistics, not blindly**. Stop loss parked. `RuinMoveUSD` stays 5.00 until
1–3 are measured.

**Tooling added:** `mt5/tools/export_history.py` — read-only (`history_deals_get`), emits a
per-position CSV and a per-**basket** CSV carrying `avg_entry`, `avg_exit` and `diff_usd_per_oz`,
which is the quantity every exit rule actually acts on.

### 2026-09-09 — ⚠️ THE TABLE IN THIS ENTRY IS WRONG. See the correction entry that follows it.

> **Retained deliberately, struck through, because the error is instructive.** Every figure in the
> table below was computed with `logged_net` sourced from the cycles CSV's **`realised`** column.
> On a broker stop-out `realised` is **0.00** while `net_broker` carries the real damage. The
> comparison therefore **priced account-destroying cycles at zero** and reported +5904 for a
> configuration that wiped the account three times. The conclusion drawn from it — "the give-back
> fix is refuted" — is also wrong. **Read the next entry.**

### ~~2026-09-09 — the give-back "fix" is REFUTED~~ (SUPERSEDED — see above and below)

The dead-zone fix proposed above (give-back as a fraction of the **peak**) was swept over **73
cycles** before adoption, per the §7 rule. Every cycle counted once: by the simulated rule where it
fires, otherwise at its **real logged outcome**, stop-outs included.

| give-back rule | rule-closed | net (rule) | net (actual) | **TOTAL** |
|---|---|---|---|---|
| **15% of balance (current)** | 36 | +1845.60 | +4058.43 | **+5904.03** |
| 10% of balance | 49 | +1325.34 | +2938.19 | +4263.53 |
| 5% of balance | 59 | −189.71 | −159.70 | −349.41 |
| 50% of peak | 63 | −394.04 | +35.96 | −358.08 |
| 30% of peak | 63 | −206.58 | +35.96 | −170.62 |
| 20% of peak | 63 | −170.42 | +35.96 | −134.46 |

**REFUTED.** "% of peak" turns +5904 into −134. The `5% of balance` variant — the one that would
have saved cycle 12 — scores **−349** across the book. They close 63 cycles instead of 36, but each
banks so little that the losers swamp them. **The dead zone is real and cost three accounts; closing
it costs more than it saves.** Fourth instance of the same error pattern (Lever A, Lever C, the
"−15% clean gap", now this).

#### ⚠️ The methodological problem this exposes — read before trusting ANY table above

**Summing per-cycle P&L is the wrong objective for this strategy.** Three of these cycles took the
account to **zero**. A sum of +5904 cannot be collected from a sequence that goes bankrupt partway
through: once balance reaches zero the sequence ends and every later term is unreachable.

That is the martingale signature — **positive expectancy per cycle, negative terminal wealth** — and
the rule scoring best on the sum (15% of balance) is precisely the rule under which the account died
three times.

**Therefore the table above does not license "keep 15%".** It licenses a different measurement:

> **A sequential, compounding replay with ruin as an absorbing state.** Start from a balance, size
> each cycle off the *running* balance, apply outcomes in time order, **stop dead at zero**, and
> compare **terminal wealth and survival rate** rather than a sum of independent cycles.

Every parameter conclusion drawn this session — the 20–22% `ExitTargetPct` plateau included — rests
on summed per-cycle P&L and inherits this flaw. **They must be re-scored against terminal wealth
before any of them is adopted.** Recorded as the top open item.

### 2026-09-09 (corrected) — `realised` vs `net_broker`, and what the data actually says

**The bug.** `replay_quick_arm.load_cycles` sourced `logged_net` from the cycles CSV's **`realised`**
column. On a broker **stop-out** `realised` is **0.00** while `net_broker` carries the damage
(**−1918.02** for cycle 12); `realised` also excludes commission, so **all 73 cycles differ between
the two columns**. Any comparison that priced fall-through cycles at `logged_net` was **valuing
account-destroying cycles at zero**. Fixed at `replay_quick_arm.py:114` with a comment naming the
trap; `ruin_replay.py` imports the same loader and inherits it.

**The corrected result.** `ruin_replay.py` — sequential, compounding, **ruin absorbing**, 2000
resampled orderings, $500 start:

| give-back rule | sum of nets | terminal | ruined | **P(ruin)** |
|---|---|---|---|---|
| **15% of balance (current)** | **−946.38** | **0.00** | **YES** | **95.2%** |
| 10% of balance | −2453.45 | 0.00 | YES | 95.2% |
| 5% of balance | −570.69 | 0.00 | YES | 63.4% |
| 50% of peak | −548.01 | 0.00 | YES | 63.4% |
| 30% of peak | −360.55 | 0.00 | YES | 63.4% |
| **20% of peak** | −324.39 | 0.00 | YES | **63.4%** |

**Three retractions:**

1. **"+5904.03" for the current settings was wrong.** True sum: **−946.38**.
2. **"The give-back fix is refuted" was wrong.** On ruin probability the %-of-peak variants are
   *better* — **63.4% vs 95.2%** — and lose less. That refutation rested on corrupted data.
3. **The real finding is worse than either version.** **Every configuration tested loses money and
   every one ruins.** Current settings ruin in **95.2%** of orderings. This is not a tuning problem;
   no give-back setting rescues it.

This is finally consistent with reality: the account died three times in two days. **The earlier
positive totals were never real.**

**Not affected by this bug:** the `ExitTargetPct` sweeps summed only rule-closed cycles and never
read `logged_net`, so the 20–22% plateau is not corrupted by *this* — though it retains the separate
flaw that excluding never-exited cycles flatters the slower setting.

**Root cause of the error, for the standing rule:** a field name was trusted without checking which
column fed it. Cross-verify the *source* of every number, not just its value.

**Where this leaves the strategy.** With `RuinMoveUSD = 5.00`, `ExitTargetPct = 28.9%` and the
current arms, the measured ruin probability over 73 real cycles is **95.2%**. Adopting the best
give-back variant only moves it to **63.4%**. Neither is a viable configuration, and no combination
tested so far produces a surviving account. **The next question is not which parameter to tune but
whether any parameterisation of this strategy survives** — which is what the `--sweep` modes of
`ruin_replay.py` exist to answer.

### 2026-09-09 — CORRECTION: rung count DOES move the target. Operator was right.

**Retracted:** ~~"the target distance depends on OUNCES, not on the number of positions — 3 × 0.99
and 30 × 0.099 are both 297 oz and give an identical target."~~

That is true **only when both baskets share the same average entry**, which is exactly what
averaging changes. Positions opened at different times fill at different prices:

```text
target price  =  avg_entry  ±  (ExitTargetPct x balance) / ounces
                 ^^^^^^^^^        <- this part is fixed by ounces
                 ^^^^^^^^^ ...but THIS part MOVES as you add at new prices
```

`avg_entry` tracks price as the basket adds, so **the target price travels with it**. That is the
purpose of the TREND/GRID add logic. A basket that cannot add has a target **frozen** while price
runs away — which is cycle 12 precisely: **all three fills at the identical price 4417.305**, zero
spread, target pinned at 4415.605 while price ran to 4423+.

**Consequence:** the lot-tier cliff (Problem 1) is more serious than the earlier entry implied. It
does not merely coarsen the grid — by preventing adds entirely it **freezes the target**, removing
the mechanism the strategy relies on to recover.

**A forward simulation of cycle 12 at lots 0.99 → 0.05 was attempted and its results are
WITHDRAWN.** At the historical lot it failed to reproduce the historical cycle — it entered at
4416.859 where the EA filled at 4417.305, and that 0.45 difference flipped a −1918 stop-out into a
+46 quick exit. A model that cannot reproduce the one case with a known answer cannot be trusted for
counterfactuals, so no number from it is recorded here.

**A trustworthy sizing test must:** anchor the first fill to the logged entry time and price;
reproduce broker fills from the `bid`/`ask` columns the trades CSV already records; **validate
against the historical outcome at the historical lot before any other lot is believed**; and run in
`grid_replay.py`, which drives `grid_state.GridState` over ticks with an MT5-like broker, rather than
in a throwaway script. **Problem 1 therefore remains unmeasured.**

### 2026-09-09 — the win rate, and the break-even bar

Recorded because the operator's reading of the wipes is largely right and deserves the numbers
beside it.

> **Operator's assessment:** the strategy performs very well when the trend is known; the account was
> wiped because the *direction* was wrong — a trader's call, not a fault of the algorithm.

Across all **73 logged cycles**:

| | count | total | average |
|---|---|---|---|
| winners | **51 (70%)** | +5,751.88 | **+112.78** |
| losers | 22 (30%) | −6,781.71 | **−308.26** |
| | | **NET −1,029.83** | |

**What supports the assessment.** Direction is entirely the operator's input — the EA never chooses
it, it only manages the basket after `BUY`/`SELL`. A **70% win rate** is real and matches the
independent Python finding of 71%. The wipes were adverse directional runs, which a basket with no
stop loss cannot escape.

**What qualifies it, as arithmetic.** The average loser is **2.73× the average winner**, so the
break-even win rate is:

```text
p x 112.78 = (1 - p) x 308.26   ->   p = 308.26 / 421.04 = 73.2%
```

**Direction must be right 73.2% of the time merely to break even.** Observed is 70% — which is
precisely why 73 cycles with a majority of winners still netted −1,029.83. The strategy is therefore
not "sound apart from the direction calls"; it is sound **conditional on better than 73% directional
accuracy on gold over ~40-second horizons**. That is the bar every proposed fix should be judged
against.

**Why the ratio is 2.73:1 — and what to aim at.** With no stop loss a losing basket runs until the
broker closes it, while a winner stops at its target. Six of 73 cycles (8%) lost more than half the
balance they opened with; the two largest, −3,993.58 and −1,918.02, were both `external` stop-outs.
**Narrowing that asymmetry, not raising the win rate, is what the sizing and target work targets** —
every point shaved off the 73.2% bar is worth more than a point of directional accuracy.

Baseline preserved in [BASELINE_v1_TIERLOT.md](BASELINE_v1_TIERLOT.md); v1-vs-v2 sizing comparison in
[README.md](README.md).

---

## 10. How to reproduce any figure here

Peaks and P&L paths are reconstructed from the broker's own ticks, because the log samples only at
event boundaries:

1. Read the cycle's entries (time, price, volume) from `RecoveryGrid_v2_trades_*.csv` `OPEN` rows,
   or from `mt5.history_deals_get()` filtered on `magic == 532040`.
2. Pull ticks for the cycle window with `mt5.copy_ticks_range(..., COPY_TICKS_ALL)` — **pass
   `tzinfo=dt.UTC`**, or MT5 reads the datetime as local time.
3. Mark each tick on the closing side (**bid for a buy, ask for a sell**) and count only the legs
   open at that timestamp:
   `net = Σ oz_i × (px − entry_i)` for buys, negated for sells.
4. `peak = max(net)`; the give-back arm is reachable only where `peak > giveback_pct × balance`.

Cross-check: each row's `net_broker` should equal `balance_after − balance_before`. It does on **13
of 15** rows. Two exceptions, for different reasons:

* **c9** — the history-window bug of §5.6 (logged +87.34, true +41.37). Now fixed.
* **c12** — deals sum to **−325.57** but the balance fell only **−317.95**. Not a bug: equity went
  to **−7.62** and the broker's **negative-balance protection** absorbed the difference, zeroing the
  account instead of leaving it in debt. The trader lost the account; the deals lost $7.62 more.

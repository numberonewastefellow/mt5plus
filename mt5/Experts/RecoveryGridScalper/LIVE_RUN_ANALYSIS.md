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

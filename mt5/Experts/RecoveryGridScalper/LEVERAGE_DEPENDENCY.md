# The leverage dependency — parked finding, 2026-09-08

**Status: recorded, not acted on.** The operator's decision on 2026-09-08 was to keep the demo at
**1:Unlimited** and change no rules. This file exists so the finding is not re-derived from scratch
next time a cycle refuses to exit.

---

## The finding

The strategy has an undocumented dependency on **very high leverage**. Not a preference — an
arithmetic requirement.

Two ceilings limit how much gold a basket can hold, and the smaller one wins:

```text
margin ceiling (oz)  =  equity × leverage ÷ price
EA lot ceiling (oz)  =  MaxTotalLots × 100
```

The target is a percentage of balance, so substituting the margin ceiling gives the price move it
demands:

```text
required move $/oz  =  ExitTargetPct × price ÷ leverage
```

**Balance and lot size cancel out.** Margin is charged per *ounce*, so a smaller lot buys no more
ounces. That refutes "auto-shrink the lot to fit the margin" outright rather than deferring it.

| leverage | required move for a 28.9% target at gold ≈ 4410 |
|---|---|
| 1:200 | **$6.37 /oz** — unreachable in a 40-second cycle |
| 1:2000 | $0.64 /oz |
| ~1:1837 (measured 2026-09-07 15:31) | $0.69 /oz — cycle 4 closed in 35 s |
| ~4000:1 | $0.30 /oz — the distance the strategy was designed around |

So a 28.9%-of-balance target is only a ~0.30 $/oz move at roughly **4,000:1**. That is why the
source strategy needed 1:Unlimited, and why it could start from $1.

---

## What was actually measured on this account (XAUUSD)

| when | margin / lot | effective leverage | binding ceiling |
|---|---|---|---|
| 2026-09-07, up to 15:31 UTC | **$0.00** | unlimited | EA lot cap |
| 2026-09-07, 17:33 UTC | **$2,205.60** | **1:200** | **margin** — only 21 oz on $465 equity |
| 2026-09-08 | **$16.00** | ~27,000:1 | EA lot cap |

Evidence: cycle 4 on 2026-09-07 held **180 oz on $432 equity** — $793,800 notional, i.e. ~1,837:1 —
and its target worked out to 0.694 $/oz. Ninety minutes later two 0.10-lot positions consumed $441
of margin on a $465 account, and the EA logged
`REAL MARGIN BLOCKED: 0.10 lot needs ~$222.12 … only $27.59 free` on every tick.

**MT5 under-reports this.** `order_calc_margin` returned `$0.00` and `SYMBOL_MARGIN_INITIAL` was
`0.0` throughout, while the server charged $2,205.60/lot. Only the EA's calibrate-from-real-fills
guard (`m_margin_per_lot`, `GridEngine.mqh`) saw the true number. Do not trust the client-side
margin calculation on this broker.

---

## Broker rules, and one discrepancy worth remembering

Exness, as supplied by the operator:

* 1:Unlimited is available only while **equity < $5,000**.
* During high-impact news and before weekends/holidays, leverage is capped at **1:2000** for
  currency pairs and gold.
* Some instruments carry fixed margin requirements independent of account leverage.

**The measured 2026-09-07 window was 1:200 — ten times tighter than the documented 1:2000 cap.**
Either gold carried a stricter fixed requirement in that window or the caps stacked. Unresolved.
**Plan for 1:200, not 1:2000.**

---

## Consequences to keep in mind

* **A $1 account requires 1:Unlimited.** At 1:2000 the minimum 0.01 lot is 1 oz ≈ $4,400 notional →
  **$2.20 margin**, more than the entire balance, so not one position can open. At unlimited the
  same 0.01 lot costs about $0.16. This is why the video strategy could start from $1 and why the
  new $1 real account at 1:2000 cannot run this EA.
* **Crossing $5,000 equity removes unlimited leverage automatically.** At 1:2000 that is $220 per
  lot, still comfortably above `MaxTotalLots = 1.00`, so it is not an immediate problem — but the
  required move jumps to $0.64/oz the moment it happens.
* **Leverage only binds when it is lower than the EA's own lot cap.** On 2026-09-08 margin was $16
  per lot and irrelevant; what actually froze five of seven cycles was `MaxTotalLots = 1.00`. Do not
  reach for the leverage explanation before checking the lot cap — see
  [PARAMETERS.md](PARAMETERS.md), "the three ceilings".

---

## If this is ever picked up again

The open question is what a stuck basket should do. A basket that cannot grow has an unreachable
target by arithmetic, so the honest options are:

1. Restore/keep high leverage and change nothing (**the 2026-09-08 decision**).
2. Arm the quick exit when the basket cannot grow, not only when it has been underwater. Measured
   against the depth-cap half of that condition, it changed the closing arm on **zero of 33** logged
   cycles — a capped basket was always already underwater. The margin half is untestable against
   the existing logs, because every historical fill cost $0.00 margin.
3. Express the target in $/oz rather than % of balance when the ounces are capped.

None of these is built. See [LIVE_RUN_ANALYSIS.md](LIVE_RUN_ANALYSIS.md) §9 for the dated decisions.

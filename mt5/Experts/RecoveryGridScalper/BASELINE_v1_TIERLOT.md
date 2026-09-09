# Baseline v1 — the tier-lot configuration that ran live

**This is the reference copy of the strategy as it was actually traded**, kept so it can be reviewed
and restored without git archaeology. Every live cycle logged to date ran this configuration.

**Restore points in git:**

```bash
# pre-exposure-cap version (tier table, no RuinMoveUSD)
git show 33a7659:mt5/Experts/RecoveryGridScalper/RecoveryGridScalper/Utils.mqh

# the configuration that ran live 2026-09-08/09 (tier lot + exposure cap)
git log --oneline -- mt5/Experts/RecoveryGridScalper/
```

The lot table also stays in the code as `RGS_TierLot`, reachable via `UseLegacyTierLot`, so v1 can be
run again for comparison rather than only read.

---

## The lot table (`RGS_TierLot`, `Utils.mqh`)

| balance < | lot |
|---|---|
| 23 | 0.01 |
| 46 | 0.04 |
| 130 | 0.07 |
| 310 | 0.09 |
| 600 | 0.10 |
| 1500 | **0.33** |
| 3400 | **0.99** |
| 13000 | 1.99 |
| 27000 | 3.99 |
| — | 6.88 |

## Settings it ran under

| input | value |
|---|---|
| `ExitTargetPct` | 28.9 (% of balance at cycle open, fixed in $ at open) |
| `ExitGivebackPct` | 15.0 (% of balance, retrace from peak) |
| `EnableQuickExit` / `EnableTimeDecay` | true / true |
| `QuickExitStartUSD` → `QuickExitFloorUSD` | 0.30 → 0.10 $/oz over 60 s |
| `QuickExitArmPct` | 15.0 (basket must go this far underwater to arm) |
| `AddMode` | TREND |
| `AddStepUSD` / `AddCooldownSec` | 0.05 / 2 |
| `RuinMoveUSD` | 5.00 (added 2026-09-08, after two wipes) |
| `MaxTotalLots` | 1.00, later raised to 50.00 by the operator |
| stop loss | **none** |

---

## Measured results — 73 logged cycles

| | count | total | average |
|---|---|---|---|
| winners | **51 (70%)** | +5,751.88 | **+112.78** |
| losers | 22 (30%) | −6,781.71 | **−308.26** |
| | | **NET −1,029.83** | |

Three accounts were lost: 2026-09-08 (×2) and 2026-09-09. Six of 73 cycles (8%) lost more than half
the balance they opened with; the largest were −3,993.58 and −1,918.02, both broker stop-outs
(`external`).

## The operator's assessment, and the arithmetic beside it

> **Operator:** the strategy performs very well when the trend is known. The account was wiped
> because the *direction* was wrong — a trader's call, not a fault of the algorithm.

**What supports this.** Direction is entirely the operator's input; the EA never chooses it, it only
manages the basket once `BUY` or `SELL` is pressed. **70% of cycles closed in profit**, which matches
the independent Python finding of 71%. The wipes were adverse directional runs that a no-stop-loss
basket cannot escape.

**What qualifies it, as arithmetic rather than opinion.** The average loser is **2.73× the average
winner**. At that ratio the break-even win rate is:

```text
p x 112.78 = (1 - p) x 308.26
p = 308.26 / 421.04 = 73.2%
```

**Direction must be right 73.2% of the time merely to break even.** Observed is 70%, which is why 73
cycles netted −1,029.83 despite most of them winning. So the strategy is not "sound apart from the
direction calls" — it is sound *conditional on better than 73% directional accuracy on gold over
~40-second horizons*. That is the bar, and it is the bar any proposed fix should be measured against.

**Why the ratio is 2.73:1.** There is no stop loss, so a losing basket runs until the broker closes
it, while a winner stops at the target. Narrowing that asymmetry — not improving the win rate — is
what the sizing and target work is aimed at.

## Known defects in v1 (why v2 is being tried)

1. **The lot cliff.** Rung count under the exposure cap runs 1, 2, 6, 10, **3**, 6, **3**, **3**, 5,
   **3**, 8 across balances. At 3 rungs the basket fills at one price, **cannot average, and its
   target is frozen** while price runs away — the shape of the third wipe, whose three fills were all
   at 4417.305.
2. **The target drifts on under-fill.** `ExitTargetPct` is a fixed dollar amount set at cycle open,
   so a basket that fills only 85% of its allowance needs a proportionally longer move: cycle 12's
   target became **1.70 $/oz** against a nominal 1.445.
3. **The give-back dead zone.** The arm fires at `peak − 15% of balance`, so a basket whose peak never
   exceeds that threshold has a *negative* trigger and the arm can never fire. Implicated in three
   account losses.

See [README.md](README.md) for the v1-vs-v2 comparison and
[LIVE_RUN_ANALYSIS.md](LIVE_RUN_ANALYSIS.md) for the dated evidence behind each point.

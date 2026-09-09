# RecoveryGridScalper — parameter manual

**What this file is for:** every input, what it *means*, what **unit** it is in, a worked example
from a real logged cycle, and what breaks if you change it. The strategy rationale lives in
[README.md](README.md) and the run-by-run findings in [LIVE_RUN_ANALYSIS.md](LIVE_RUN_ANALYSIS.md) —
this file is the operating manual. **Link, never copy:** if a number here disagrees with the source,
the source wins and this file is the bug.

> **DEMO ONLY.** This is an averaging grid with **no stop loss**. Its shape is many small wins and
> then one adverse run that takes the account. Nothing below makes it safe.

---

## Read these three things first

**1. Direction is always YOURS. `AddMode` is not a direction switch.**
You pick the side with `BUY`/`SELL` (or `B`/`S`). The EA never opens a position on the opposite
side. `AddMode` only decides **when it adds to the basket you already opened**:

| mode | adds when |
|---|---|
| `GRID` | only while the basket is **losing**, on adverse movement |
| `TREND` *(default)* | on **any** move ≥ `AddStepUSD`, favourable or adverse |

**2. Almost everything scales with balance — but the lot ceiling does not.**
The target, the give-back and the quick-exit arm are all percentages of the balance at cycle open.
`MaxTotalLots` is a fixed number of lots. As the balance grows the target grows and the ounces do
not, so **every win makes the next target need a bigger price move**.

**3. Three separate ceilings limit how many positions you get. The smallest one wins.**
Most "why did it stop trading?" questions are this. See the next section.

---

## The three ceilings — why it stops adding

| ceiling | formula | set by |
|---|---|---|
| **exposure cap** | `balance ÷ RuinMoveUSD` ounces — **the risk control** | **`RuinMoveUSD`** |
| depth cap | risk budget: 44% of balance across the grid, **then** min'd with the exposure cap | `Utils.mqh` — derived |
| lot cap | `MaxTotalLots ÷ lot` — an absolute backstop only | `MaxTotalLots` |
| margin cap | `equity × leverage ÷ price` ounces | your broker |

> **The exposure cap is why the account is still here.** Before 2026-09-08 the depth cap was the
> only sizing rule, and it let the basket reach a size where a **$1.3 move in gold wiped the whole
> account at any balance**. On 2026-09-08 `MaxTotalLots` was raised 1.00 → 50.00 to stop position
> rejections; that removed the only thing holding exposure down and the account went 1,274 → 3,913 →
> **0 in 2 minutes 10 seconds**. See [LIVE_RUN_ANALYSIS.md](LIVE_RUN_ANALYSIS.md) §9.

Worked from **2026-09-08 cycle 6** (SELL, balance 1058.42, lot 0.33):

| ceiling | value | binding? |
|---|---|---|
| depth cap | 18 positions | no |
| **lot cap** | `1.00 ÷ 0.33` = **3 positions** | **YES** |
| margin cap | $1,042 free, next add costs $5.28 | no |

It held 3 positions for 44 s, unable to add, and missed its target by five cents an ounce.
Since 2026-09-08 the EA prints all three, so you never have to infer this again:

```text
RGS CYCLE 6 START SELL lot 0.33  balance 1058.42  batch 3  depth cap 18  lot cap 3
  mode TREND  margin/lot 16.00  target 305.883 (28.9%)  give-back 158.763 (15.0%)
```

**The panel shows the binding one:** `cap 3 of 18 (lots)` means the lot cap is what stops you.

### The lot-tier cliff — check this before the balance crosses a line

`AutoLot` picks the lot from the balance (`Utils.mqh:18`):

| balance | lot | positions under `MaxTotalLots = 1.00` |
|---|---|---|
| < 23 | 0.01 | 100 |
| < 46 | 0.04 | 25 |
| < 130 | 0.07 | 14 |
| < 310 | 0.09 | 11 |
| < 600 | 0.10 | 10 |
| < 1500 | 0.33 | **3** |
| < 3400 | 0.99 | **1** — the grid stops being a grid |
| < 13000 | 1.99 | **0** — nothing opens at all |

**Crossing $1,500 drops you from 3 positions to 1. Crossing $3,400 means no position opens**
(`1.99 > 1.00`, so even the first order is refused and you get
`MaxTotalLots 1.00 reached, batch stopped at 0/3`). If you keep `MaxTotalLots = 1.00`, raise it or
force a smaller `ManualLotInput` before the balance crosses these lines.

---

## `RuinMoveUSD` — the one number that decides whether a bad cycle is a loss or the end

```text
max_ounces = balance ÷ RuinMoveUSD
```

**Purpose:** hold few enough ounces that gold must move `RuinMoveUSD` dollars against the whole
basket before the balance is gone. Safety comes from setting it far outside normal excursions —
measured adverse excursions on 2026-09-08 were **0.45–1.97 $/oz**, so at the 5.00 default the worst
of them costs 39% of balance rather than all of it.

At the default, sizing across the ladder (contrast the "old cap" column — that is what wiped the
account):

| balance | lot | old cap | new cap | ounces | ruin $/oz | target $/oz |
|---|---|---|---|---|---|---|
| 5 | 0.01 | 6 | 1 | 1 | 5.00 | 1.44 |
| 471 | 0.10 | 24 | 9 | 90 | 5.23 | 1.51 |
| 1058 | 0.33 | 18 | 6 | 198 | 5.34 | 1.54 |
| 1512 | 0.99 | 12 | 3 | 297 | 5.09 | 1.47 |
| 3914 | 1.99 | 15 | 3 | 597 | 6.56 | 1.89 |
| 8000 | 1.99 | 21 | 8 | 1592 | 5.03 | 1.45 |

The ruin distance is now roughly constant at **5–7 $/oz** instead of 1.3–1.9.

### You cannot tune this without also moving the target

```text
target move  =  ExitTargetPct × ruin move   =   0.289 × RuinMoveUSD
```

Because the target is a percentage of balance and ruin is balance ÷ ounces, **the target always
sits 29% of the way to ruin.** Making the target easier to hit and making the account harder to
lose are the same dial turned opposite ways. At `RuinMoveUSD = 5` the target needs ~1.45 $/oz; at
1.31 (what the account died with) it needed 0.38 $/oz and gold only had to move 1.31 to end you.

### Minimum viable balance

The broker's smallest lot is 0.01 = **1 oz**, so trading needs `balance >= RuinMoveUSD`. At the
default a **$1 account is refused**, and the EA says exactly what to do:

```text
RGS: REFUSED - the exposure cap allows 0.2 oz at balance 1.00 with RuinMoveUSD 5.00, but one
0.01 lot position is 1 oz. Needs balance >= 5.00, or set RuinMoveUSD to 1.00 (a 1.00 move
would then end the account).
```

That refusal is honest rather than an obstacle: 1 oz on a $1 balance **has** a ruin move of $1, and
no setting changes it. To run a $1 account anyway, set `RuinMoveUSD = 1.0` and accept that a $1 move
in gold ends it. On 2026-09-08 a $1 account did exactly that — grew to ~$7 over 13 cycles, then lost
all of it on the 14th.

---

## The three exit arms

All three are **IN PROFIT ONLY** — none ever closes a red basket. Checked in this order:

```text
1 TARGET     net >= ExitTargetPct%   × balance_at_open
2 GIVE-BACK  net <= best_pnl - ExitGivebackPct% × balance_at_open
3 QUICK      ONLY IF the basket has been down >= QuickExitArmPct% of balance,
             then: net >= QuickExitPerOz(age) × total_ounces
```

`net = ounces × (price − average entry)`, so `net ≥ X × ounces` **is** "price is X above the
basket's average entry" — the number you read off the chart.

### Worked example — 2026-09-08 cycle 6, the one that got away

```text
SELL, balance at open 1058.42, 3 positions × 0.33 lot = 99 oz, avg entry 4397.605

TARGET     28.9% × 1058.42 = 305.88  ->  305.88 / 99 oz = 3.090 $/oz  ->  price 4394.51
GIVE-BACK  15.0% × 1058.42 = 158.76  ->  fires once net falls 158.76 below the peak
QUICK      arms only after net has been below -158.76  ->  worst was -122.76, NEVER ARMED

peak reached +301.08 = 3.041 $/oz (price 4394.56)   <- 0.049 $/oz short of the target
then retraced; give-back fired at 301.08 - 158.76 = 142.32
closed +141.48 -> realised 128.12, commission -10.89, NET +117.23
```

Two lessons the numbers make plain:

* **The target is a $/oz distance in disguise.** Divide it by your ounces before you click. 305.88
  on 99 oz is a $3.09 move in gold; on 198 oz it would be $1.55.
* **The give-back can be unreachable.** If the peak never exceeds `ExitGivebackPct% × balance`, that
  arm's trigger sits at a *negative* number and can never fire while the basket is green. The panel
  says so out loud: `exit if <= -66.26`.

---

## Inputs

### Exit

| input | default | unit | what it does |
|---|---|---|---|
| `ExitTargetPct` | 28.9 | **% of balance at cycle open** | Close everything at this profit. **Fixed in dollars when the cycle opens**, never recomputed. Balance 1058.42 → target $305.88 for the life of that cycle. |
| `ExitGivebackPct` | 15.0 | **% of balance at cycle open** | Close if the basket retraces this far from its own peak. 15% × 1058.42 = $158.76. Raise it to hold through deeper retraces; lower it to exit nearer the peak at the cost of more churn. |
| `EnableQuickExit` | true | on/off | The third arm. **false = exactly the old two-arm behaviour.** |
| `EnableTimeDecay` | true | on/off | false = the quick arm stays fixed at `QuickExitStartUSD`. |
| `QuickExitStartUSD` | 0.30 | **$/oz above average entry** | The quick arm's distance at cycle open. A **price distance**, not a percentage. |
| `QuickExitFloorUSD` | 0.10 | **$/oz** | …decaying linearly to this. |
| `QuickExitDecaySec` | 60 | seconds | …over this long. At 30 s with the defaults the arm sits at 0.20 $/oz. |
| `QuickExitArmPct` | 15.0 | **% of balance at cycle open** | **Load-bearing — do not lower casually.** The quick arm stays asleep until the basket has been down this far. Without the gate the quick arm is nearer than the target on **31 of 31** logged cycles, and the 28.9% target becomes dead code. |

### Sizing

| input | default | unit | what it does |
|---|---|---|---|
| `AddMode` | `Trend` | enum | `Trend` = add on any move ≥ step, either way. `Grid` = add only while losing. **Not a direction switch.** Hotkeys `T` / `G` change it live, mid-cycle. |
| `AutoLot` | true | on/off | Take the lot from the balance tier table above. |
| `ManualLotInput` | 0.0 | lots | `>0` forces this lot. The panel's lot box overrides it. Use this to dodge the tier cliff. |

### Add throttle

| input | default | unit | what it does |
|---|---|---|---|
| `AddStepUSD` | 0.05 | **$/oz price move** | How far price must move before the next add. Measured from live fills at 0.037–0.070. Raise it for a wider, slower grid. |
| `AddCooldownSec` | 2 | seconds | Minimum gap between adds regardless of price. |

### Safety

| input | default | unit | what it does |
|---|---|---|---|
| `MinFreeMargin` | 0.20 | **DOLLARS, not percent** | Stop adding when free margin falls below this. The $0.20 default is effectively off. |
| **`RuinMoveUSD`** | **5.00** | **$/oz** | **The primary risk control.** Ounces are capped at `balance ÷ RuinMoveUSD`, so gold must move this far against the **whole** basket to consume the account. It is the distance to **ruin**, not one you survive — **bigger = smaller positions = safer**. See the section below. |
| `MaxTotalLots` | 1.00 | lots | Absolute backstop on open volume. **No longer the risk control** — `RuinMoveUSD` is. Left in place as a hard ceiling; note it still steps hard with the lot tier. |
| `EnableDailyLossKill` | false | on/off | Halt for the rest of the day after a loss. |
| `DailyLossKillPct` | 50.0 | % of the day's opening balance | …the size of loss that triggers it. |
| `DemoOnly` | true | on/off | Refuse to run on a live account. **Leave this on.** |
| `AutoRestart` | false | on/off | false = one click, one cycle. |
| `Slippage` | 100 | points | Max deviation. |
| `RealMarginPerLotUSD` | 0.0 | $ per lot | `0` = calibrate from real fills. MT5's own `order_calc_margin` reports `$0.00` on this broker even when the server charges $2,205/lot, so the EA learns the true cost from each fill instead. Override only if you know the real number. |

### Hotkeys

| input | default | what it does |
|---|---|---|
| `EnableHotkeys` | true | false = mouse clicks only |
| `HotkeyBuy` / `HotkeySell` | `B` / `S` | open a cycle in that direction |
| `HotkeyCloseAll` | `P` | close everything now |
| `HotkeyTrend` / `HotkeyGrid` | `T` / `G` | switch `AddMode` live |

All five must be different letters or hotkeys are disabled for the session — it says so on startup.

---

## Reading the log

Files land in `MQL5\Files\`: `RecoveryGrid_v2_cycles_<account>_<date>.csv` (one row per cycle) and
`RecoveryGrid_v2_trades_<account>_<date>.csv` (one row per fill, plus `NOTE` rows).

### "It stopped taking trades"

| log line | meaning | what to change |
|---|---|---|
| `add BLOCKED: MaxTotalLots 1.00 reached - 3 positions, 0.99 lots open, next add needs 1.32` | The **lot cap**. Most common. | Raise `MaxTotalLots` or lower the lot |
| `add BLOCKED: depth N/N - the risk budget is spent` | The **depth cap** — as deep as the risk budget allows | Working as designed |
| `add BLOCKED: free margin X < floor Y` | Below `MinFreeMargin` | Raise equity or lower the lot |
| `REAL MARGIN BLOCKED: 0.10 lot needs ~$222.12 … only $27.59 free` | The **broker** refused — leverage dropped | [LEVERAGE_DEPENDENCY.md](LEVERAGE_DEPENDENCY.md) |
| `MaxTotalLots 1.00 reached, batch stopped at 1/3` | The cap bit **mid-batch** — you asked for 3, got 1 | Same as the first row |

Each fires **once per transition**, not once per tick, so a single line means it stayed blocked.

### "It didn't exit when it should have"

* **`quick: not armed`** — the basket never went `QuickExitArmPct%` underwater, so the quick arm is
  asleep. Only the target and give-back can close it.
* **`exit if <= -66.26`** — the give-back arm is **unreachable** for this cycle (see above). Only
  the target can close it.
* **Both at once** — nothing can close it but your hand (`P`). Sanity-check whether the target is
  even reachable: divide it by your ounces.

#### Worked example — the two ways a cycle "should have exited" and didn't

**A clean cycle has no fast exit at all.** 2026-09-08 cycle 1: balance 500, 100 oz, worst only
−5.52. The quick arm needs the basket to have been down 15% of balance (−$75) first, so it never
armed and **time decay never applied**. Only the target (1.445 $/oz) could close it — 44 seconds.
This is by design: the gate is what stops the quick arm replacing the target on every cycle.

**A gate that misses by a hair changes everything.** 2026-09-08 cycle 4: balance 718.13, 132 oz.

```text
quick GATE needs worst <= -107.72     actual worst -104.46   -> SHORT BY $3.26, never armed
```

Had it armed, the decayed threshold at 35 s was 0.182 $/oz × 132 oz = **$24.07** and the basket was
at +25.15 — it would have closed there. Instead the give-back arm held it to 71 s and closed at
+39.82. **The slower path made 2.4× more here**, so a near-miss is not automatically bad — but it is
unpredictable, and three such near-misses have now been measured ($0.17, $3.26, and ten of 31 cycles
within $6 of the line).

**How to tell which happened:** the panel's `mode` row. `quick: not armed` means the gate never
opened — only the target and give-back can close that basket.

### Telling "blocked" from "exited early"

A cycle showing `3/18 positions` did **not** necessarily hit a wall. Compare timestamps:

* **blocked** — there is an `add BLOCKED` note, and the close comes well after the last fill.
* **exited early** — no block note, and the close follows the last fill by seconds. 2026-09-07
  cycle 4 filled its 6th batch at 15:31:31 and closed at 15:31:35: the give-back fired; nothing
  blocked it.

### The cycle START line

```text
RGS CYCLE 6 START SELL lot 0.33  balance 1058.42  batch 3  depth cap 18  lot cap 3
  mode TREND  margin/lot 16.00  target 305.883 (28.9%)  give-back 158.763 (15.0%)
```

`lot cap`, `mode` and `margin/lot` were added on 2026-09-08. Before that none of the three could be
recovered from a log afterwards — a cycle's mode in particular **cannot** be inferred from its
fills, because when price whipsaws inside the cooldown, GRID and TREND produce identical adds.

---

## Panel reference

```text
state  : RUNNING
cycle  : #1 BUY  lot 0.10  batch 3  cap 3 of 18 (lots)   <- binding ceiling, and why
basket : 2/24 pos  net +2.66 / tgt 134.36  (6.718 $/oz)  <- what the target costs in price
peak   : +3.48   exit if <= -66.26                       <- negative = give-back unreachable
acct   : bal 462.73  eq 465.39  free 25.31  margin/lot $2205.20
mode   : TREND   quick 0.183 $/oz = +21.96               <- or "quick: not armed"
```

The `$/oz` beside the target is the number to sanity-check before clicking: it is the move gold has
to make for this cycle to reach its target.

---

## See also

* [README.md](README.md) — the strategy, its evidence, and what the replays measured
* [LIVE_RUN_ANALYSIS.md](LIVE_RUN_ANALYSIS.md) — dated findings from every live run
* [LEVERAGE_DEPENDENCY.md](LEVERAGE_DEPENDENCY.md) — why leverage silently changes what is reachable

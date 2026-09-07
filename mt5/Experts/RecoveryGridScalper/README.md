# RecoveryGridScalper (XAUUSD) — strategy spec

> **STATUS: IMPLEMENTED 2026-09-02.** `RecoveryGridScalper.mq5` + 7 includes, compiles clean
> (0 errors, 0 warnings). Deploy with `.\deploy.ps1 -Strategy RecoveryGridScalper`.
>
> ⚠️ **DEMO ONLY. This is an averaging martingale with no stop-loss that bets full equity.** See
> [Risk](#risk--read-this-first). Never point it at a real account. The EA enforces this itself via
> `DemoOnly` and refuses to start on a live account.

>
> 📉 **[LIVE_RUN_ANALYSIS.md](LIVE_RUN_ANALYSIS.md) — what went wrong, measured.** Three accounts
> were cleaned out because the basket's peak profit fell **1–2 percentage points short** of the
> give-back threshold: $3.44 of unreachable profit cost $353 of account. Read it before changing
> any exit constant.

---

## What the build settled, and what the replays measured

The draft below left four items open. Tick-level replay against real Exness data settled all four —
see `D:\llm\ios\xausd_video_out\REPLAY_RUNS.md` and `OPERATOR_VERIFIED.md`.

| draft's open item | settled |
|---|---|
| entry cadence — all at once or 1/sec? | **all at once.** Cycle 8 opened 4 trades at one price (4050.155) |
| exact add spacing | **$0.037 / $0.053 / $0.070** measured. The `$0.18` from the add detector is **refuted** |
| positions per batch | **2–4**, simultaneously |
| exact close-all number | **a fixed $ net = `ExitTargetPct` % of the balance at cycle open.** The first build used `net ≥ TargetMove × (lots ÷ 0.01)` on the reasoning that "at the video's balances this equals ~29% of balance, so both readings agree" — **that reasoning was wrong.** The two agree at exactly one point (a full-depth basket) and diverge by a factor of the position count everywhere else. See "The bug this shipped with" below |

**Operator's build decisions:** lot **automatic by tier with a panel override box**; adds throttled
by **both** a price step **and** a time gap (the draft's literal "add immediately while in loss"
would open hundreds of positions per second on a ~5 tick/s feed).

### ⚠️ What the replays say about the outcome — read before trusting a demo run

| test | result |
|---|---|
| 13 sessions × 2 directions × 4 risk budgets | **0 of 104** finished without parking or blowing |
| tuned on one window, tested on others | **+5,348% → −6.7%**, a 796× gap |
| forward test on unseen data (Sep 2, buy side) | **every config lost** on a market that rose $63.83 |
| trend-aligned single cycles | 71% closed in profit; **24% passed 100% of balance — account gone** |
| $1 start, 9 configurations | **all died within 1–3 cycles** |

A $1 account trading the 0.01 minimum (= 1 oz) carries **~234% of the account in per-minute price
swings**. Below 0.01 there is no smaller position, so **no setting avoids this** — the EA prints the
calculation at startup.

---

## Files

```
RecoveryGridScalper.mq5          OnInit / OnTick / OnChartEvent / OnDeinit
RecoveryGridScalper/
  Defines.mqh    magic 532040, states, panel object names
  Structs.mqh    SCycle — the carried per-cycle state
  Utils.mqh      lot tiers, basket queries, the exposure warning
  TradeExec.mqh  order send + close-all, magic-filtered
  Logger.mqh     the two CSVs
  Panel.mqh      BUY / SELL / CLOSE ALL / PAUSE, lot box, live status
  GridEngine.mqh THE RULES — nowhere else
```

## The log (this is the point of the exercise)

Two CSVs in `<terminal>\MQL5\Files\`, flushed after every row so a margin call loses nothing:

* **`RecoveryGrid_v2_trades_<login>_<yyyymmdd>.csv`** — one row per fill, close, rejection and note:
  `run_id, time_msc, iso_utc, cycle_id, event, ticket, side, lot, price, bid, ask, position_pnl,
  basket_pnl, n_open, total_lots, balance, equity, free_margin, retcode, comment`
* **`RecoveryGrid_v2_cycles_<login>_<yyyymmdd>.csv`** — one row per cycle:
  `run_id, cycle_id, start_iso, end_iso, duration_s, direction, lot, group, depth_cap, target,
  batches, positions, total_lots, avg_entry, exit_price, best_pnl, worst_pnl, worst_equity,
  realised, commission, net_broker, balance_before, balance_after, close_reason`

### Read `net_broker`, not `realised`

`net_broker` is profit + swap + commission read straight back from deal history — **the only
column that is right when the EA did not close the basket itself.** `realised` is just the balance
step across the close, and a stop-out books its loss *before* the EA notices, so `realised` reads
~0 for exactly the cycles that cost the most. `realised` also differs from
`balance_after − balance_before` by the cycle's commission, which is charged at each open;
`commission` is logged separately because it appears in neither the live basket P&L nor `realised`.

### Four log defects fixed on 2026-09-03 — all found by reconciling against MT5

1. **A liquidated cycle left no row at all.** `OnTick` reset silently to IDLE when the basket
   vanished, so a broker stop-out was never logged. On Sep 3 the file held **8 rows for 13 cycles**
   and omitted every stop-out — that is, exactly the losses: it read **+3.09 while the account lost
   $10.71**. Any measurement taken from it was biased toward the winners. Externally-closed baskets
   now log with `close_reason = external` and the broker's own figure.
2. **`worst_pnl` / `worst_equity` understated the drawdown.** They were sampled only at the top of
   the tick, so a batch that fired later in the same tick moved the basket without the watermark
   seeing it — cycle 51 logged `-2.37 / 9.47` against a true `-2.52 / 8.99`. `MarkExtremes` now runs
   after every fill as well.
3. **`cycle_id` restarts at 1 on every re-attach**, so it cannot identify a cycle: `cycle_id 4`
   appeared in 8 OPEN rows from 4 different runs. **`(run_id, cycle_id)` is the key.**
4. **The "add BLOCKED" note wrote a flushed row on every tick** while blocked (`t_last_add` never
   advances, so the guard stayed true) — cycle 55 produced 15 identical lines of disk IO inside the
   poll path. It now logs the transition, not the state.

**Why `_v2_` is in the filename.** The schema gained columns, and the old writer appended wider rows
under an existing narrower header, so every column after `lot` read shifted. A new filename
guarantees a matching header. The old `RecoveryGrid_*` files are the pre-fix format — read them with
the 18-column header only up to the point the schema changed, or not at all.

## What the live runs are testing — and what they are NOT

**The direction of every cycle so far was chosen by the operator from the chart.** Those entries are
trend guesses; with respect to this engine they are random. **So the P&L of these runs measures the
entries, not the algorithm.** Do not read a losing session as "the algorithm is wrong", and do not
read a winning one as validation.

What *is* being validated right now is **mechanical conformance** — does the engine size and throttle
the way the design says? Checked against the deployed formulas for the first three v2 cycles:

| check | c1 (bal 10.00) | c2 (9.79) | c3 (12.26) |
|---|---|---|---|
| lot from the balance tier | 0.01 ✓ | 0.01 ✓ | 0.01 ✓ |
| batch from `RGS_GroupFor` | 3 ✓ | 3 ✓ | 3 ✓ |
| depth cap from `RGS_MaxPositionsFor` | 9 ✓ | 9 ✓ | 9 ✓ |
| positions ≤ cap | 9/9 ✓ | 6/9 ✓ | 9/9 ✓ |

All conform. **One anomaly:** cycle 1's third batch fired at **+0.003 adverse**, far under the
configured `AddStepUSD = 0.05`. Cause: `last_add_price` is sampled from the *live* ask/bid **after**
the batch finishes, not from the batch's own fill price. In a fast tape those differ, so the real
rung spacing can be much tighter than the parameter states. The measured video gaps were
$0.000–$0.070, so 0.003 is not absurd — but the throttle is not doing what it says on the tin.

## Commission is $11.00 per LOT per entry — it scales with lot size

Measured exactly across 10 v2 cycles at three different lot sizes. It is charged **at the open**,
once per position, and it is **not** in `POSITION_PROFIT`, so the EA's live `net` is always better
than the account by this amount.

| lot | per position | per 1.00 lot |
|---|---|---|
| 0.01 | $0.110 | **$11.00** |
| 0.07 | $0.770 | **$11.00** |
| 0.09 | $0.990 | **$11.00** |

**An earlier note here said "$0.11 flat" — that was only true at 0.01 lot.** As the balance ladder
raises the lot, commission rises with it, and because the *number* of positions also grows with
balance the cost per cycle rises on both axes at once:

* $10 balance, 0.01 lot, 9 positions → **$0.99** a cycle
* $164 balance, 0.09 lot, 11 positions → **$10.89** a cycle, against a target of $47.48 — **23%**

Every exit arm still fires on `POSITION_PROFIT`, which excludes this. Making the arms cost-aware is
deferred by operator decision (a configurable `CommissionPerLot`, default 11.0, with 0 restoring
exact video behaviour on a commission-free account).

## Cycle 9 (2026-09-03) — why it took 11 trades, not 12, and why it waited 73 s

A worked example, because it answers two questions the log alone does not.

**Why only 11 positions when the depth cap was 12:** `MaxTotalLots`. At 0.09 lot, 11 positions is
**0.99 lots**; a 12th would be 1.08 against the `MaxTotalLots = 1.00` backstop, so the batch stopped
and logged `MaxTotalLots 1.00 reached`. **From 0.09 lot upward the lot cap binds before the depth
budget does, and the depth cap becomes decorative.** Worth knowing before reading `depth_cap` as the
real limit.

**Why it waited 73 s instead of exiting on the bounce:**

```
16:41:05  opens 3 @ 4488.184
16:41:11  +3.15  in profit on 5 legs   <- did not exit
16:41:13  price jumps; -68.49 on 9 legs
16:41:16  11 legs, MaxTotalLots reached
16:42:18  +52.26  -> CLOSE on target (47.48)
```

At `+3.15` neither arm could fire: the target was 47.48, and the give-back needed a peak above
15% × 164.30 = **24.65** — the dead zone again. It then recovered from **−105.59** to **+52.26** and
closed correctly on the target arm.

### ⚠️ Cycle 9 is a counter-example to the quick-bounce exit

The instinct is "once it bounces back, get out fast". **On this cycle that would have been much
worse.** The planned recovery arm (arm on a deep drawdown, exit at the first profit ≥ 11% of
balance = $18.07) would have closed at ~+18 gross → **+7 net after $10.89 commission**, instead of
the **+41.37 net** that waiting produced.

So the bounce exit is not free: it converts a small number of large recoveries into small ones. It
is still worth having — cycle 3 died in the dead zone for −13.64 — but the two effects must be
measured against each other, not assumed. **Log `close_reason` and compare `best_pnl` against
`target` over a real sample before tuning either constant.**

## Cycle 12 (2026-09-03) — the account, in 4m 49s

Run 71794 went **$100 → $317.95 over eight cycles**, then cycle 12 erased all of it:
**317.95 → 0.00**, `net_broker −325.57`, `close_reason = external` (a broker stop-out). **Third
time this shape has been recorded** — a string of wins, then one cycle that takes them.

```
16:46:42  opens 3 buys @ 4490.299, lot 0.10, balance 317.95
16:46:45  adds 3 @ 4490.045
16:46:47  adds 3 @ 4489.933
16:46:49  adds 1 @ 4489.716  -> 10 legs, "MaxTotalLots 1.00 reached"   (7 seconds in)
          ... holds 100 oz for 4m 41s, cannot add, no stop loss ...
16:47:26  +44.83  <- PEAK, 14.1% of balance
16:51:31  -314.57 -> STOP-OUT
```

**It was in profit on 62 of 877 ticks and could not exit on any of them.**

**Cause 1 — the dead zone, missed by $2.86.** Target 91.89 was never reached; the give-back needed
`best > 47.69` (15% of balance) and the peak was **44.83**. Three cycles have now died in this band —
trade `1810633670`, cycle 3 (peak 12.97% of balance), cycle 12 (14.1%) — all between the operator's
p10 exit (11%) and the give-back constant (15%).

**Cause 2 — 100 oz on a $318 account.** `MaxTotalLots = 1.00` is a **fixed** cap while everything
else scales with balance. It bound after 7 seconds and the depth cap of 18 was never the real limit:

| balance | lot | oz held | **$/oz move that wipes the account** |
|---|---|---|---|
| 10.00 | 0.01 | 9 | **1.11** |
| 100.00 | 0.07 | 84 | **1.19** |
| 164.00 | 0.09 | 100 | **1.64** |
| 317.95 | 0.10 | 100 | **3.18** |

**Gold moved $3.59 during cycle 12** (4486.909 … 4490.503). The risk budget certified 44% of
balance; realised drawdown was **98.9%**. Depth exhaustion caps the *number of positions*, not the
loss — with no stop loss, every further cent costs 100 oz × $0.01 and nothing bounds it.

### What the two proposed fixes would have done

| cycle | as deployed | armed give-back (11% / keep 75%) |
|---|---|---|
| **12** | **−325.57** | **−0.07** |
| 3 | −13.64 | +0.59 |
| c51 | −0.74 | +0.02 |
| c53 | −0.35 | +0.54 |

On cycle 12 the give-back fix yields **breakeven, not profit** — the tape gapped 44.83 → 10.93
between ticks, so it exits low. It converts −325 into −0.07. Video acceptance stays 3/5.

The magnitude is bounded by a **balance-scaled exposure cap** instead of the fixed `MaxTotalLots`:
`max_oz = MaxRiskPct% × balance / SurviveMoveUSD`. At 60% / $5 that is 38 oz at $318 rather than
100, so cycle 12 loses ~$118 instead of the account. **Neither fix makes the strategy profitable** —
they bound the tail so a run ends measurable instead of at zero.

## Input and logging changes, 2026-09-03

* **`DailyLossKill` → `DailyLossKillPct`.** It was a flat **$0.50**, which on a $98 account halted
  trading after losing 0.5% of it — and since the daily total counts `net_broker`, commission alone
  ($0.33 a cycle at 3 × 0.01 lot) spent it in two break-even cycles. Now a **% of the day's opening
  balance**, default **50**, and **0 turns the kill off entirely**. It also actually resets daily:
  `m_day` was declared and never used, so "daily" was a misnomer and only RESUME cleared it.
* **`net_broker` window overlap.** `RGS_RealisedSince` selected history from `t_open − 2 s`, which
  swept in the previous cycle's closing deals when two cycles ran back to back. Cycle 9 (opened 1 s
  after cycle 8 closed) reported **+87.34 against a true +41.37** — cycle 8's +45.97 counted twice
  across the two rows. Fixed with an exact per-deal `DEAL_TIME >= from` filter; the SELECT keeps its
  slack so a boundary deal is not missed. All 12 cycles now reconcile to the cent.

## Live observation 2026-09-03 — the give-back dead zone

Cycle 3 held **78 s**, peaked at **+1.59**, never exited, and was stopped out for **−13.64**
(balance 12.26 → −1.38). Neither arm could fire:

* **target** = 28.9% × 12.26 = **3.543** — the peak never came close;
* **give-back** = 15% × 12.26 = **1.839** — the arm needs `net ≤ best − 1.839`, so it is only
  reachable once `best > 1.839`. **Best was 1.59 (12.97% of balance).** The threshold stayed
  negative for the whole cycle and the in-profit-only guard made the arm **unreachable by
  construction**.

A basket that is in profit and structurally unable to exit is the defect. The fix arms the give-back
once the peak reaches **11% of balance** — the operator's own p10 exit across 41 video cycles (min
ever 6.0%, median 28.9%) — and then keeps **75% of the peak**, which is the documented median
give-back behaviour. Swept over three real tick paths: **−14.73 → +1.15**, with the video acceptance
score unchanged at 3/5.

### ⚠️ The fix may exit too early — this is unmeasured, watch for it

Arming at 11% makes the give-back reachable, but it also means a cycle that *would* have run on to
the full 28.9% target can now be closed at 11–20% instead. **Every cycle used to justify the change
was one that ended badly** — we have no measurement of what it costs on cycles that would have
reached target.

**What to watch in the cycle log:** rows with `close_reason = giveback` where `best_pnl` is well
below `target`, and price then continues in the same direction after the close. If that becomes the
common case, raise `KeepPeakPct` (keep more of the peak, exit later) or `RecoveryArmPct` (arm on a
bigger peak) rather than reverting — the dead zone must not come back.

## Using it

1. `cd D:\llm\ios\mt5plus\mt5` then `.\deploy.ps1 -Strategy RecoveryGridScalper`
2. In MT5: **Algo Trading** on, attach to an **XAUUSD** chart, tick **Allow Algo Trading**
3. The panel appears top-left. **Lot box `0` = automatic** (tier from balance); type a number to
   force one for the next cycle.
4. Click **BUY** or **SELL** — the EA opens the batch and manages it. **CLOSE ALL** works at any
   time and is unconditional. **PAUSE** blocks new cycles without touching an open basket.
5. With `AutoRestart=false` (default) it is **one click, one cycle**.

**If you remove the EA with a basket open, it does NOT close it** — silently flattening a book on a
recompile or chart change would be worse. It prints a loud warning; close them yourself or
re-attach.

---

## Risk — read this first

This strategy is a **single-direction averaging grid (martingale) with no stop-loss**, sizing off
**available equity / free margin**. Its shape is: *many frequent tiny wins, then one adverse run that
margin-calls the account.* On XAUUSDm specifically there is **no proven directional edge** (the repo's
own volume research + ladder study both land on "predicts move size, not direction"), so the run of
adverse ticks that wipes the account is a matter of *when*, not *if*.

It is being built as a **research/demo tool to watch the behaviour**, not a money-printer. The safety
inputs (demo-account guard, `MinFreeMargin` floor, `MaxTotalLots` cap, `DailyLossKill`) are
**damage-limiters, not a cure**.

---

## The idea in one paragraph

The **operator picks a direction** (Buy or Sell) — the algo never decides direction. It opens a
**batch** of positions on that side. If the batch goes into **profit**, it closes everything the moment
net profit reaches a small target. If the batch goes into **loss**, it does **not** cut — it **adds more
positions** (averaging down, limited by free margin) so that a small bounce brings the whole basket
back to profit, and then **closes everything on the first small net-positive bounce**. After each
close it repeats, and as the balance grows it **increases size** (more positions, then bigger lots).

---

## Units — it's PRICE MOVEMENT, not "pips" (proven from live trades)

`0.01 lot` of gold = **1 troy oz**, so a **$0.30/oz price move = exactly $0.30 P&L** on 0.01 lot. That
is why the P&L number on screen equals the price movement. Proven:

| Entry | Current | Price move | P&L shown | Lot | Check |
|---|---|---|---|---|---|
| 4050.218 | 4050.513 | +0.295 | **+0.29** | 0.01 | 1 oz × 0.295 ≈ 0.29 ✓ |
| 4051.283 | 4051.046 | −0.237 | **−0.23** | 0.01 | 1 oz × 0.237 ≈ 0.23 ✓ |
| 4048.185 | 4047.792 | 0.393 | **+1.57** | 0.04 | 4 oz × 0.393 ≈ 1.57 ✓ |

**General:** `P&L($) = price_move($/oz) × (lot ÷ 0.01)`. So the targets `0.10 / 0.20 / 0.30` mean
**0.10–0.30 of gold price movement**; the dollars scale with lot size.

---

## Rules

1. **Direction — manual.** Operator ticks **Buy** or **Sell**. Every position is on that one side.
2. **Open a batch** of positions (chosen side, lot per the current balance tier).
3. **Exit target (clean run):** close **ALL** when basket **net P&L ≥ a small positive target**
   (roughly +0.10 to +0.30 per 0.01 lot of open size).
4. **In loss → add, don't cut.** While the basket is net-negative and **free margin is above the
   floor**, **add more positions immediately** (same side, same lot) — even several in the same second.
   Stop adding when free margin gets tight. There is **no stop-loss**.
5. **Bounce exit:** once it has been underwater, close **ALL** on the **first** moment net P&L crosses
   the small positive target — do **not** wait for the full target. (In the live cycles this fired at
   as little as +0.08 / +0.09 per late position.)
6. **50%-in-profit shortcut:** the operator also treats "about half the positions green" as good enough
   to close all — functionally the same as "net just turned positive."
7. **Repeat**, with size grown from the new balance.

### Sizing (as observed; tunable)

- **Lot by balance tier:** `< $23 → 0.01`, `$23–46 → 0.04`, `≥ $46 → 0.07`, … (extend upward).
- **Position count grows with balance / free margin:** ~$1 → a few positions; ~$20 → 10–20.
- **In-loss adds** are bounded only by the `MinFreeMargin` floor.
- This is **full-equity** sizing (aggressive, exploratory).

---

## Reference cycles — the operator's ACTUAL live trades

Numbers read from the live video (some counts approximate where the position list was scrolling).
`P&L/ea` is dollars per position = `price_move × (lot × 100 oz)`; at 0.01 lot it equals the price move.

> **Account columns are critical — do not jumble them.** `Balance` = realized cash; `Equity` =
> balance + floating P&L of open positions; `Free margin` ≈ equity here (little/no reserved margin
> shown in the video). "*not stated*" / "*~inferred*" = the operator did not give that number — it is
> **not** invented.

### Cycle 1 — BUY · 0.01 · CLEAN WIN (target reached, no adds)
| Batch | Lot | Entry | Current | P&L/ea | # |
|---|---|---|---|---|---|
| initial | 0.01 | 4050.218 | 4050.513 | +0.29 | 4 |
| initial | 0.01 | 4050.211 | 4050.513 | +0.30 | 2 |

**Account:** in profit (pre-close) → Balance **6.14** / Equity **7.60** / Free margin **7.60**;
after close-all → **7.60 / 7.60 / 7.60**.
At least one leg hit ~+0.30 target → **closed all**.

### Cycle 2 — BUY · 0.01 · LOSS → ADD → BOUNCE-EXIT
| Step | Lot | Entry | Current | P&L/ea | # | Balance | Equity | Free margin |
|---|---|---|---|---|---|---|---|---|
| open | 0.01 | 4051.283 | 4051.046 | −0.23 | 5 | 7.60 | 6.45 | 6.45 |
| add | 0.01 | 4051.286 | 4051.046 | −0.24 | 4+ | 7.60 | 5.25 | 5.25 |
| deeper | 0.01 | — | (falling) | −0.39 … −0.49 | ~10 | 7.60 | 3.64 | 3.64 |
| bounce | — | — | 4051.208 | first 5 −0.07 / rest −0.14 | | 7.60 | | |
| **exit** | — | — | (rising) | first 5 **+0.09** / rest **+0.08** | | *not stated* | | |

Balance held **7.60** while equity/free-margin fell **7.60 → 6.45 → 5.25 → 3.64** as it added into the
loss, then closed all on the small recovery. (Final balance after close: *not stated in the video.*)

### Cycle 3 — SELL · 0.01 · DEEP LOSS → ADD → BOUNCE-EXIT
| Step | Lot | P&L/ea | # | Balance | Equity | Free margin |
|---|---|---|---|---|---|---|
| open | 0.01 | −0.24 | 3 | 11.88 | 11.88 | 11.88 |
| add | 0.01 | ~−0.40 | 5 | 11.88 | (falling) | (falling) |
| deeper | 0.01 | (loss) | ~15 total | 11.88 | 4.92 | 4.92 |
| bounce | 0.01 | 4 @ +0.03, rest −0.01 / −0.02 | | 11.88 | | |
| **exit** | 0.01 | +0.08 … +0.27 (some +0.09) | | **14.80** | **14.80** | **14.80** |

Balance held **11.88** while equity/free-margin dropped to **4.92** at the bottom; closed all → account
**14.80 / 14.80 / 14.80**.
*Note: the operator wrote "equity 4.92, free margin 4.92 but equity is 11.88" — read as a typo for
**balance 11.88** (balance held while equity fell). Confirm.*

### Cycle 4 — SELL · 0.04 · LOT STEP-UP · LOSS → ADD → BOUNCE-EXIT
| Step | Lot | Entry | Current | P&L/ea | # |
|---|---|---|---|---|---|
| open | 0.04 | 4048.185 | 4048.560 | −1.50 | 3 |
| add | 0.04 | 4048.320 | 4048.560 | −0.58 | 2 |
| **exit** | 0.04 | 4048.185 | 4047.792 | first 3 **+1.57** / +2.11 | 5 |

**Account:** start (0.04 tier) → Balance **~23** *(~inferred: lot stepped up at ~$23)*, Equity/Free
margin *not stated during*; after close-all → Balance **23.73** / Equity **23.73** / Free margin **23.73**.
Only 2 added (not 5–10) because free margin was tight.

### Cycle 5 — BUY · 0.07 · LOT STEP-UP · LOSS → ADD → RUN → EXIT
| Step | Lot | Entry | Current | P&L/ea | # |
|---|---|---|---|---|---|
| open | 0.07 | 4048.141 | (falling) | −1.68 | 4 |
| add | 0.07 | — | (falling) | (loss) | 5+ |
| **exit** | 0.07 | 4048.141 | 4049.963 | first 4 **+5.75** / +3.90 | 9+ |

**Account:** start (0.07 tier) → Balance **~46**, Equity/Free margin *not stated during*; after
close-all → *not stated*. Opened 4 (−1.68), added 5+, ran into profit, closed all.

### Account state per cycle — consolidated (operator's exact numbers)
| Cycle | Moment | Balance | Equity | Free margin |
|---|---|---|---|---|
| 1 BUY 0.01 | in profit (pre-close) | 6.14 | 7.60 | 7.60 |
| 1 | after close-all | 7.60 | 7.60 | 7.60 |
| 2 BUY 0.01 | open 5 | 7.60 | 6.45 | 6.45 |
| 2 | after +4 adds | 7.60 | 5.25 | 5.25 |
| 2 | deepest (~10 pos) | 7.60 | 3.64 | 3.64 |
| 2 | after close-all | *not stated* | — | — |
| 3 SELL 0.01 | start | 11.88 | 11.88 | 11.88 |
| 3 | deepest (~15 pos) | 11.88 | 4.92 | 4.92 |
| 3 | after close-all | 14.80 | 14.80 | 14.80 |
| 4 SELL 0.04 | start (0.04 tier) | ~23 *(inferred)* | — | — |
| 4 | after close-all | 23.73 | 23.73 | 23.73 |
| 5 BUY 0.07 | start (0.07 tier) | ~46 | — | — |
| 5 | after close-all | *not stated* | — | — |

**Balance progression across the session:** ~$1 → 6.14 → 7.60 → 11.88 → 14.80 → 23.73 → … → ~$46 →
(still climbing). Lot: 0.01 up to ~$23, then 0.04, then 0.07 at ~$46; position count rises with balance.

---

## EA inputs (as built)

**Nothing that sizes or exits is a constant — every one is a fraction of balance.** That is what
makes the rule scale-free: the same settings fitted a $1.47 account and a $1,181 one.

| Input | Meaning |
|---|---|
| `ExitTargetPct` | close all at this **% of the balance the cycle opened with** (default 28.9) |
| `ExitGivebackPct` | or when the basket retraces this % of balance **from its own peak** (default 15) |
| `AutoLot` / `ManualLotInput` | lot from the balance tier, or the panel override box |
| `AddStepUSD` / `AddCooldownSec` | the two add throttles — **both** must pass (0.05 and 2 s) |
| `MinFreeMargin` | stop adding when free margin drops below this |
| `MaxTotalLots` | **backstop only.** The binding cap is the balance-derived depth budget |
| `DailyLossKill` | close all + halt for the day if realised loss exceeds this |
| `DemoOnly` | refuse to run unless `ACCOUNT_TRADE_MODE == DEMO` |

The batch size and the depth cap are **not** inputs. They come from `RGS_GroupFor(balance)` and
`RGS_MaxPositionsFor(balance)` in `Utils.mqh`, ported from
`analysis/video_ocr/lot_position_engine.py` so the EA and the replay cannot drift. A $1 balance
opens **one** position; an account too small to hold even one inside the drawdown budget is
**refused**, not rounded up.

## State machine (one cycle)

```
click BUY/SELL ─▶ size from balance: lot, batch, depth cap, and a FIXED $ target
      │           (refuse outright if the budget affords no position at all)
      ▼
  each tick: net = sum(open P&L)
      ├─ net > 0 and net ≥ target ........... CLOSE ALL ─▶ IDLE
      ├─ net > 0 and net ≤ peak − giveback .. CLOSE ALL ─▶ IDLE
      ├─ net < 0, adverse ≥ step, cooldown elapsed,
      │     free margin ok, positions < depth cap .. ADD a batch (same side/lot)
      └─ else ............................... hold
```

Both exit arms are evaluated **in profit only** — this strategy does not close at a loss. 88% of
the observed closes were green and cycle 7 was held through a 92%-of-balance drawdown, so an exit
firing on a retrace *into the red* was never possible.

## The bug this shipped with

The first build closed on `net ≥ TargetMove × (lots ÷ 0.01)`. Because
`net = oz × (bid − avg_entry)`, that reduces to:

> `bid ≥ avg_entry + $0.30/oz` — **the same required move at every depth.**

So adding positions bought **zero** progress toward the exit: the average entry fell and the target
fell with it, by exactly the same amount. The one mechanism that makes a recovery grid recover was
cancelled. A live $1 cycle dipped to 4599.50, came back through 4600.10, and hung.

With the target **fixed in dollars at cycle open**, the same dollars arrive on a smaller move as the
basket grows — which is the operator's description, as one rule rather than two:

| basket | target $ | ÷ ounces | required move |
|---|---|---|---|
| 1 position at $1 balance | $0.289 | 1 oz | **$0.289/oz** ← "normally 0.20–0.30" |
| after the dip, 3 positions | $0.289 | 3 oz | **$0.096/oz** ← "about 0.10" |

Rules 3 and 5 above ("exit target" and "bounce exit") are therefore **one rule**, not two special
cases. Predicted vs observed $/oz on the five hand-watched cycles is **0.69–1.08×** — the acceptance
test is `analysis/video_ocr/test_fixed_dollar_exit.py`, whose gate 2 fails against the old form.

---

## Open items to settle at review (tomorrow)

- **Reuse vs new:** `mt5/Experts/` already has close cousins — **`XauTickAccumulator`** (per-candle
  basket: buy 1/sec up to 10, then close all), plus `GoldBreakoutGrid`, `GoldScalperMulti`,
  `XauusdORBShooter`. Decide whether to model the new EA on `XauTickAccumulator`'s basket / close-all
  machinery or extend an existing one.
- **Exact add cadence** — every tick while in loss? every −0.20 step? and how many per add.
- ~~**Exact close-all number** — net ≥ (TargetMove × open-lots-in-0.01-units) vs a fixed $ net.~~
  **SETTLED: a fixed $ net.** This question was on the list, answered the wrong way in the first
  build, and cost a live cycle. See "The bug this shipped with".
- **Lot-tier table** — confirm the balance breakpoints and the lot at each.
- **Entry cadence** — open all of a batch at once, or 1/sec like XauTickAccumulator?
- Follow `mt5/` conventions: own folder, nested include layout, `deploy.ps1`, `.ex5` tracked.

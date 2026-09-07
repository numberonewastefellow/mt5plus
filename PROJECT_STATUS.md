# XAUUSD Recovery Grid — project status

**Read this first if you are picking this work up cold.** Last updated **2026-09-03**.

> The replay results below are from tick REPLAY. For what happened when the EA ran on a live demo
> account — including three account wipes and the defect that caused them — see
> **[mt5/Experts/RecoveryGridScalper/LIVE_RUN_ANALYSIS.md](mt5/Experts/RecoveryGridScalper/LIVE_RUN_ANALYSIS.md)**.

A trading strategy was reverse-engineered from a phone-camera video of someone's MT5 screen,
replayed against real tick data, and built as an MT5 Expert Advisor. This page says where
everything is, what was measured, and what is still open.

> **Standing constraint.** This work is analysis and code only. Every order is placed by the
> operator's own click on their own **demo** account. Nothing in these sessions sends an order —
> see the hard rule in `CLAUDE.md`.

---

## Where everything lives

| what | path |
|---|---|
| **The EA** | `mt5/Experts/RecoveryGridScalper/` — `.mq5` + 7 `.mqh`, compiles clean |
| **EA spec + usage** | `mt5/Experts/RecoveryGridScalper/README.md` |
| **Live post-mortem** | `mt5/Experts/RecoveryGridScalper/LIVE_RUN_ANALYSIS.md` — what went wrong, with tick evidence |
| Deploy | `mt5/deploy.ps1` (`-ListTerminals`, `-TerminalId`, `-AllTerminals`) |
| **Reset demo to $1** | `mt5/tools/reset_demo_balance.bat` — the session it needs expires every ~6 h; refresh with `-ImportCurl`. `mt5/tools/README.md` |
| **Analysis scripts** | `analysis/video_ocr/` — 20 scripts |
| **Analysis outputs** | `D:\llm\ios\xausd_video_out\` — outside the repo, ~1.2 GB |
| **The single reference** | `…\STRATEGY_REFERENCE.md` — generated, never hand-edited |
| **Primary evidence** | `…\OPERATOR_VERIFIED.md` — 6 hand-verified cycles; **outranks the CSVs** |
| **Run register** | `…\REPLAY_RUNS.md` — every experiment with date, data file, parameters |
| Tick data | `analysis/tick_data/` — walk-forward set + two day fixtures |

### Key scripts

| script | does |
|---|---|
| `build_reference.py` | validates every CSV and regenerates `STRATEGY_REFERENCE.md`. **The only place figures are computed** |
| `grid_state.py` | the pure, MT5-free state machine |
| `grid_replay.py` | tick replay with MT5-faithful fills; `--selftest` runs 4 gates |
| `test_exit_rules.py` | acceptance test — 4 refuted exit rules kept so they are not re-proposed |
| `trend_aligned_test.py` | tests the machinery with direction handed to it |
| `progression_run.py` | the compounding $1-through-a-day run |
| `lot_position_engine.py` | sizing: `lot_for`, `max_positions_for`, `drawdown_at` |

---

## The strategy

The **operator picks the direction** — the algorithm never decides it. It opens a batch of
positions on that side. If the basket goes into profit it closes **everything** at a small target.
If it goes into loss it does **not** cut — it **adds more positions** so a small bounce returns the
whole basket to profit. After each close it repeats, with size grown from the new balance.

```
IDLE --(operator clicks BUY/SELL)--> RUNNING
  each tick, net = sum(open P&L):
    net >= target ................... CLOSE ALL --> IDLE
    net < 0, adverse >= step,
      cooldown elapsed, margin ok ... ADD a batch
    otherwise ....................... hold
```

**There is no stop loss.** That is the strategy as observed, not an omission — 44 of 45 video cycles
went underwater and 86% still closed green. It is also why this is demo-only.

**Units:** `0.01 lot = 1 oz`, so a `$1/oz` move = `$1` P&L on 0.01 lot. P&L on screen equals price
movement.

---

## What was measured

### Verified against the operator's own screen (primary evidence)

Six cycles watched frame by frame, every arithmetic check exact — `OPERATOR_VERIFIED.md`.

| finding | value |
|---|---|
| positions per batch | **2–4, opened simultaneously at one price** |
| rung gap | **$0.00–$0.07** (measured 0.037 / 0.053 / 0.070) |
| cycle duration | **~23–26 s** median |
| exit | **simultaneous basket close**, never per-position |
| exit give-back | **12–18% of balance** — scale-free across an 800× range |
| stop loss | **none.** Cycle 7 held through a 92%-of-balance drawdown |

### The replays — all negative

`REPLAY_RUNS.md` has all six runs with parameters and caveats.

| run | test | result |
|---|---|---|
| 1 | 13 sessions × 2 directions × 4 budgets | **0 of 104** survived |
| 2 | volatility-scaled step | **made it worse** — static blew 0/13, scaled blew 4–7/13 |
| 3 | tune on one window, test on others | **+5,348% → −6.7%**, a 796× gap |
| 4 | forward test on unseen data | **every config lost** on a market that rose $63.83 |
| 5 | direction handed to it | 71% of cycles closed in profit; **24% took the whole account** |
| 6 | $1 compounding through a day | **died within 1–3 cycles**, all 9 configurations |

**Two conclusions that matter most:**

* **`PARKED` is the safety mechanism, not a failure mode.** The tight step is what keeps the
  account alive; widening it converts a capped loss into a blown account.
* **At $1 the arithmetic is impossible.** One 0.01 lot = 1 oz carries **~234% of a $1 account in
  per-minute price swings**, and there is nothing smaller to trade. The video's $0.30 → $69,000 was
  **path luck** — its tape was only 2× calmer, and its adverse moves happened to come back.

---

## What the live demo runs measure (2026-09-03)

**The operator picks each cycle's direction from the chart, so those entries are effectively random
with respect to the engine. The P&L of a live run measures the entries, not the algorithm.** What
the runs currently validate is **mechanical conformance** — lot from the balance tier, batch from
`RGS_GroupFor`, depth from `RGS_MaxPositionsFor`, adds within the step/cooldown throttle. On the
first three v2 cycles all of those conform.

Two live defects found this way, both now recorded in the EA README:

* **The give-back dead zone.** The arm needs `net <= best - giveback`, so it is unreachable whenever
  the peak never exceeds the give-back constant. Cycle 3 peaked at 12.97% of balance against a 15%
  give-back, could not exit while in profit, and was stopped out for **−13.64** (12.26 → −1.38).
  Fix: arm at 11% of balance (the operator's p10 exit) and keep 75% of the peak. **Caveat: this may
  exit too early on cycles that would have reached target — unmeasured, being watched.**
* **`last_add_price` is sampled after the batch, not at its fill price**, so an add can fire at a
  rung gap far tighter than `AddStepUSD` — 0.003 observed against a configured 0.05.

**Do not quote figures from any pre-`_v2_` log file.** The old logger wrote no row at all for a
liquidated cycle, so those files systematically omit the losses.

## Trustworthy vs assumed

**Trust:** the six operator-verified cycles; the 13-session walk-forwards; anything in
`STRATEGY_REFERENCE.md` (regenerated from CSVs, so it cannot drift).

**Do not trust without checking:** single-window results — Aug 28 and Sep 1 are *favourable
fixtures* chosen after the fact. Only the walk-forwards carry evidential weight.

**Known-refuted, do not re-propose:** the `$0.18` add step (detector artifact; real gaps are
$0.00–0.07); `per_rung = 1.15` (counted events, not positions); fixed-target, `$/oz` trailing, and
all-legs-green exits; per-position take-profits.

**And do not re-propose a target proportional to open volume.** The EA shipped with
`net ≥ TargetMove × (lots ÷ 0.01)`, which reduces to `bid ≥ avg_entry + $0.30/oz` at *every* depth —
so adding positions bought zero progress toward the exit and a live $1 cycle dipped, recovered and
hung. It was justified by "at the video's balances this equals ~29% of balance, so both readings
agree"; the two agree at exactly one point (a full-depth basket) and diverge by a factor of the
position count everywhere else. The target is a **fixed dollar amount = % of balance at cycle
open**. Guarded by `analysis/video_ocr/test_fixed_dollar_exit.py`, gate 2.

---

## The exposure cap is the thing that actually kills runs (2026-09-03)

`MaxTotalLots = 1.00` is a **fixed** lot cap while every other constant scales with balance. It is
the binding limit from ~0.09 lot upward, so the depth cap stops being the real constraint, and the
resulting position is enormous relative to the account:

| balance | lot | oz held | **$/oz move that wipes the account** |
|---|---|---|---|
| 10.00 | 0.01 | 9 | **1.11** |
| 100.00 | 0.07 | 84 | **1.19** |
| 317.95 | 0.10 | 100 | **3.18** |

Cycle 12 held 100 oz on $317.95 and **gold moved $3.59 during the cycle** — 317.95 → 0.00 in 4m 49s
after eight winning cycles took $100 → $317.95. **Third recorded instance of that shape.** The risk
budget certified 44% of balance; realised drawdown was 98.9%, because the closed-form drawdown
prices the worst case as "one step past the last rung" and price does not stop there.

Proposed: `max_oz = MaxRiskPct% × balance / SurviveMoveUSD` (60% / $5 → 38 oz at $318, not 100).
**This does not make the strategy profitable** — it bounds the tail so a run ends measurable rather
than at zero.

## Traps that cost time here

1. **`copy_ticks_range` reads a *naive* datetime as LOCAL, not UTC.** Always pass `tzinfo=dt.UTC`.
2. **This machine has 4 MT5 data folders and 2 installs.** `deploy.ps1` used to hardcode one, so
   EAs compiled cleanly into a terminal nobody was looking at. It now auto-detects the live one and
   prints its choice — use `-ListTerminals` if the EA does not appear.
3. **`grid_replay_matrix.csv` was generated with `per_rung = 1`**; the default is now 3. Pass
   `Engine(per_rung=1)` to reproduce it.
4. **MT5 allows one IPC connection per terminal.** Stop the XauOrderPad server before a tick pull
   or you get `-10004`.
5. **Tesseract is installed but not on PATH** — `stage_b_ocr.TESSERACT` sets it explicitly.

---

## Open items

* **The exit rule's constants.** Structure is settled (dual-arm: a ~30%-of-balance target OR a
  ~15% give-back, on a poll cadence — best score 3/5 in `test_exit_rules.py`). The give-back band
  12–18% and the poll cadence are not pinned. Cycles 15, 12 and 20 would settle it; cycle 20 is a
  potential falsifier at 4.5%.
* **Live demo run of the EA.** Built and deployed, never run. The trade and cycle CSVs it writes are
  the point — they make the live behaviour analysable the way the video was.
* **43-row manual check** in `verify_list.csv` — the only route to an external accuracy number.
* **Dating the video session** — deferred; candidates narrowed to 4 days.

---

## If the next question is "should we trade this?"

Every measurement says no: 0 of 104 walk-forward runs survived, the forward test lost on a market
that moved the right way, and a $1 account is arithmetically impossible. The EA exists to **watch
the behaviour on demo and log it**, which is a reasonable thing to do, and its guards
(`DemoOnly`, `MaxTotalLots`, `MinFreeMargin`, `DailyLossKill`) are damage-limiters rather than a
fix. Treat a profitable demo session as a sample of one, not as evidence.

# video_ocr — reconstructing a XAUUSD grid strategy from a phone-camera recording

Turns `D:\llm\ios\xausd-video-trade-pattern-analysis.mp4` — a phone camera pointed at **another
phone's screen** running MT5 mobile — into a deduplicated frame set, a per-state dataset, a trade
report, and a fitted position-sizing engine.

Unrelated to `XauOrderPad/` and to the volume-anomaly scripts in `analysis/`. **Nothing here touches
MT5's order path, connects to a broker for anything but read-only history, or places a trade.**

Output goes **outside the repo** to `D:\llm\ios\xausd_video_out\` (~1.2 GB of JPGs).

---

## What this produced, in one paragraph

A 10-minute video was reduced to **1,026 distinct on-screen states**, from which **43 trading
cycles** were reconstructed: entries, adds, exits, lot sizes, balance, equity, margin and running
P&L. From that, a sizing engine was fitted — `lot ≈ a·balance^b` with a sub-linear exponent, a
$0.18 grid step, and a take-profit at ~29% of balance. The account ran **$0.30 → $69,000** in five
hours, and the headline finding is that **no survivable systematic rule reproduces it**: the best
that holds every observed drawdown reaches 0.23× of it.

---

## Pipeline — run in this order

```bash
cd analysis/video_ocr
OUT="D:\llm\ios\xausd_video_out"

python calibrate.py            --video "D:\llm\ios\xausd-video-trade-pattern-analysis.mp4" --out "$OUT"
python stage_a_scan.py         --video "D:\llm\ios\xausd-video-trade-pattern-analysis.mp4" --out "$OUT" \
                               --threshold 0.15 --mt5-threshold 0.283 --workers 8
python stage_b_ocr.py          --out "$OUT" --workers 12      # seconds when cached
python strategy_observations.py --out "$OUT"
python trade_report.py         --out "$OUT"
python make_ltp_series.py      --out "$OUT"
python eda.py                  --out "$OUT"
python verify.py               --out "$OUT"
python lot_scale_engine.py     --out "$OUT"
python strategy_spec.py        --out "$OUT"
python lot_position_engine.py  --out "$OUT"
python grid_replay.py          --selftest                   # 4 gates; must pass before trusting a replay
python grid_replay.py          --matrix --csv <ticks.csv> --out "$OUT"
python build_reference.py      --out "$OUT"                 # LAST: validates + regenerates the reference
```

**`stage_b_ocr.py` caches its OCR in `ocr_raw_v2.json`.** Re-running to change grouping, repair or
clock logic takes seconds, not the 20-minute read. Pass `--force-ocr` only after changing something
upstream of the OCR itself.

---

## Files

### Extraction

| file | purpose |
|---|---|
| `rectify.py` | **Shared geometry — the single source of truth.** Screen-quad detection, perspective rectification, ROI fractions, the blue/red colour mask, and `min_diff` (jitter-tolerant comparison). Both stages import it so they cannot drift. |
| `calibrate.py` | Pre-flight. Sweeps the `is_mt5` threshold, checks whether half-res quad detection is accurate enough (**it is not** — max 115 px error, so full-res is used), and dumps ROI overlays to `calib/` for eyeballing. |
| `stage_a_scan.py` | Scans all 19,050 frames. Keeps a frame when the colour mask over the right-hand value column changes vs the **last kept** frame. Tuned for recall → **3,735 candidates**. Writes `full/` and `screen/` JPGs. |
| `stage_b_ocr.py` | OCR + exact dedup on parsed values → **1,026 unique states**. Also does the arithmetic repairs, clock reconstruction and cycle marking. The biggest file; most of the hard-won logic lives here. |

### Analysis

| file | purpose |
|---|---|
| `strategy_observations.py` | Add events, close events (burst-aware), the lot ladder, and entry→exit distances. |
| `trade_report.py` | Clusters entry prices into grid rungs; per-rung ledger plus per-cycle grid summary. |
| `make_ltp_series.py` | The running price / open-P&L / drawdown trajectory, and per-cycle recovery paths. |
| `eda.py` | Cycle reconstruction and session statistics. |
| `verify.py` | Internal consistency checks (MT5's own identities, LTP continuity, clock monotonicity). |

### Engine

| file | purpose |
|---|---|
| `lot_scale_engine.py` | Fits `lot = a·balance^b` four ways, hold-out tests them, and **replays** each from the start with a survival gate. This is where "no survivable rule reproduces $69K" was established. |
| `strategy_spec.py` | The complete rule set as a dataclass — sizing, add trigger, cap, exit, direction — with the hold-out on the exit rule. |
| `lot_position_engine.py` | **The deliverable.** Self-contained and importable, no runtime dependency on the pipeline. Turns the position cap into a risk-budget decision via a closed-form drawdown model. |
| `grid_state.py` | The pure, MT5-free state machine — `FLAT / OPEN / ADD / UNWIND / PARKED`. Same shape as `strategies/straddle_ladder.StraddleGridState`, so the replay and any live engine drive one object and cannot diverge. **There is no stop loss in this strategy**; `PARKED` is the only brake. |
| `grid_replay.py` | Drives `GridState` over real ticks with MT5-faithful fills (buy opens at ask, closes on bid). `--selftest` runs four gates, `--matrix` the walk-forward. **This is what established that the parameters do not transfer.** |
| `build_reference.py` | **Generates the single reference.** Validates every CSV, recomputes every published figure, emits `STRATEGY_REFERENCE.md` + `strategy_reference.html` + `validation_report.csv`. The one place figures are computed — nothing downstream may hardcode one. |

### Human verification loop

| file | purpose |
|---|---|
| `make_review_list.py` | All 146 flagged states with video timestamps and blank `correct_*` columns. |
| `make_verify_list.py` | The **43 rows actually worth checking** — grouped by what each settles. Prefer this over the 146. |
| `apply_corrections.py` | Reads either file back, applies corrections, and **scores the OCR against them** — the only route to a real accuracy figure. |

---

## What the source actually is — four findings that shaped everything

**1. It is sped up ~5–24×, not slow motion.** The phone's own clock runs 11:43 → 16:46 across 635 s
of video, with cuts. Most frames are genuinely different states, so dedup buys ~18×, not ~100×.

**2. The camera is handheld.** The screen drifts ~60 px and shrinks ~6% inside the frame. Nothing may
use fixed pixel coordinates — every frame is rectified first.

**3. The position list scrolls.** Only ~9 rows are ever visible out of a basket reaching ~170. Any
per-position figure is measured on the *top* of the list.

**4. The summary block changes size.** 3 rows early, 5 later once margin is in use. Splitting it by
row count would mis-assign every field after that; it is split by **ink colour** instead (summary is
dark, position P&Ls are blue/red).

---

## Techniques that were tried and rejected — do not redo these

| approach | why it failed |
|---|---|
| `cv2.phaseCorrelate` stabilisation | worse than nothing — moiré dominates the FFT peak |
| `cv2.findTransformECC` | works (17% → 3.2% jitter) but 167 ms/frame ≈ 53 min |
| Per-row `--psm 7` OCR | slower **and** less accurate than whole-block `--psm 6` |
| Colour mask straight to tesseract | the mask traces glyph edges, not solid fills |
| Half-res quad detection | max 115 px error |
| Majority voting to fix OCR | fails on **systematic** errors — the floating overlay button corrupts the same digit every frame, so voting elects the wrong value 4-to-1 |
| Exact-price fingerprinting (for dating) | 98–100% on *every* control day — not discriminative |
| Geometric lot ladder | 21% within ±33% |
| Fixed leverage / fixed risk budget as the sizing rule | leverage spans 1,144×–17,629×; drawdown 9–100% |
| Quadratic position-count model | 33% self-test — the grid stacks several positions per rung |

---

## Two mistakes made and corrected — worth knowing about

**"Closes winners, holds losers" was wrong.** A basket is unwound leg by leg over seconds, so one
liquidation appears as several balance changes. Grouped by cycle, open P&L returns to ~0 after the
last close on **91% of cycles** — the whole basket goes. The user caught this.

**"45 unwind bursts = 45 cycles" was a coincidence.** That used a 2 s grouping threshold; the count
runs 52/49/47/45/42/41 across thresholds. Grouping by *cycle* is parameter-free and is what the code
does now.

---

## The results

### Strategy parameters

| rule | value | confidence |
|---|---|---|
| Lot | `snap(0.00891 · balance^0.58)`, floor 0.01 | survival-fitted; fitting their own lots (`0.00211·bal^0.76`) reproduces 74% but **blows the account** |
| Add trigger | ~**$0.18** adverse per rung | median of 78 real adds; 11 rows fully clean |
| Exit | open P&L ≥ **~29% of balance** | 1.8× spread, tightest of four candidates; holds out at 24.4% |
| Position depth | from a **risk budget**, default 44% | the observed median drawdown; flat vs balance (corr −0.05) |
| Direction | **external input** | persists in runs of ~3; not derivable here |
| Leverage | unlimited | confirmed on the user's Exness demo |

### Session

$0.30 → $69,000 over 43 cycles, 93% won. **Nine cycles risked ≥99% of the account.** Median cycle
risks 34–44% of balance to gain ~29% — roughly **1.2× as much risked as gained**.

Not a lot-multiplying martingale: **954 of 955** multi-position states have every lot identical. The
escalation is in the *count*, not the size.

---

## Data quality — what to trust

| check | result |
|---|---|
| Hand-checked fixture frames | 4/5 exact |
| LTP continuity between states | 99.8% |
| Entry prices within $50 of market | 99.3% |
| Clock monotonic | 99.8% |
| `margin_level = equity/margin×100` | 91.4% |
| `free_margin = equity − margin` | 88.5% |
| Flagged states | 146 / 1,026 (14%) |

Every row in `unique.csv` carries `quality` (`ok` / `repaired` / `flagged` / `rows_misaligned`) and
`source_frames` — the exact frames that voted on it. **Filter on `quality` before quoting anything as
exact.**

**The 137 `rows_misaligned` states do NOT need fixing.** Measured: grid step, exit distance, lot
exponent and drawdown median all move <10% without them. Only the position cap moves (34 → 27), and
the fix is to use the clean cap, not to repair 137 states.

---

## DONE

- [x] Frame extraction, rectification, dedup — 19,050 → 1,026 states
- [x] OCR of balance / equity / margin / free margin / per-position P&L / entry / LTP / clock
- [x] Arithmetic repair pass (`pnl = (ltp − entry) × lots × 100`) — fixes dropped leading digits
- [x] Cycle reconstruction, corrected close model, trade report, EDA
- [x] Lot engine fitted and hold-out tested; survival-gated replay
- [x] Complete strategy spec; position depth as a risk budget
- [x] Two published reports (see below)

## PENDING

1. **The 43-row manual verification** — `verify_list.csv`. The user is filling the `basket_empties`
   group. When it returns: `apply_corrections.py` scores the pipeline against it, then re-run
   `strategy_observations → lot_scale_engine → strategy_spec` (seconds, off the cache). This gives
   the first **real** accuracy number; everything above it is internal consistency.
2. ~~**Replay against real XAUUSD data, then demo.**~~ **DONE — and it answered no to the
   strategy.** `grid_state.py` + `grid_replay.py` run the rules over real Exness ticks.
   **0 of 104 runs** (13 sessions × 2 directions × 4 risk budgets) finished without parking or
   blowing. The $0.18 step with a risk-budgeted cap absorbs about **$2 of adverse movement** on an
   instrument that moves **$186 a day**, so the grid parks within seconds — sell-only on Aug 28
   parked 180 ticks in and lost 42.6%, and on Aug 18 the *correct* direction still lost 46.5%.
   Direction is not the broken part; the step does not transfer off the video's $28-range tape.
   Full account: `GRID_REPLAY.md`. **A demo run does not follow from this** — there is nothing to
   put on an account until the step and depth are re-derived for real volatility.
3. **Dating the session** (deferred, groundwork done). The video's `creation_time` is 2026-08-26 but
   real XAUUSD traded 4565–4697 that week against the extracted 4024.9–4053.1 — so that is the export
   date, not the trading date. Candidates narrowed to **2026-06-29, 06-30, 07-24, 07-28**. The
   approach that should work is segment-wise path matching (search day × start minute × speed 4–26×,
   score RMS after removing a constant offset; run a negative control first). Do **not** use
   `clock_est` as the time axis — it is self-inconsistent.
4. **Optional:** the add-trigger rule is unanswerable as things stand — only 116 of 261 detected adds
   carry a new rung price, and the scrolling list hides most of the basket.

---

## The single reference

**`STRATEGY_REFERENCE.md` / `strategy_reference.html` is the one document.** It replaced four that
had drifted apart — `LOT_SCALE_ENGINE.md`, `STRATEGY_ANSWERS.md`, `GRID_REPLAY.md` and the session
teardown — which now stand as one-line pointers.

**It is generated, not written.** `python build_reference.py --out "$OUT"` validates every CSV and
recomputes every figure at build time; no number in it is typed by hand. That is the whole point:
the four old documents carried **hardcoded literals**, and they went stale silently. The
entry→exit median sat at `$0.458` after the repair pass moved rows beneath it (the CSV says
`$0.374`), and positions-per-rung was stated as 3–4 in three places when the detector measures
**1.15**. Regenerating is now how the document is corrected.

Its §9 carries a **regression table**: every figure the retired documents quoted, beside what the
CSV says now, so the consolidation is auditable rather than a silent rewrite.

Published **from** `D:\llm\ios\xausd_video_out\`, so the local file is the source and cannot drift
from what is published. All three previously-published URLs now serve it, so no shared link points
at a superseded document:

- https://claude.ai/code/artifact/ca403fdc-7b7b-4642-949e-1bee0d515f9a *(was Lot Scale Engine)*
- https://claude.ai/code/artifact/b4e4c287-28a0-4515-abb3-a9d6fb4c3842 *(was Grid Session Teardown)*
- https://claude.ai/code/artifact/6e8108ba-6a67-40e1-9164-7000612890b1 *(was Grid Replay)*

Validation output lands in `validation_report.csv`. It reuses `verify.py`'s seven checks unchanged
and adds seven cross-file ones; **failures are reported, not asserted away** — the OCR is not
perfect and a pass rate that hid that would be worse than useless.

Text equivalents: `LOT_SCALE_ENGINE.md`, `STRATEGY_ANSWERS.md` (the seven observations, each with its
source column and coverage).

---

## Environment

Tesseract 5.4 at `C:\Program Files\Tesseract-OCR\tesseract.exe` (installed, **not on PATH** —
`stage_b_ocr.TESSERACT` sets it explicitly). OpenCV 4.11, numpy 2.4, Python 3.11. 16 cores.

**Do not push these frames through a hosted vision model** — ~3,700 images is millions of input
tokens for a job Tesseract does locally in ten minutes.

MT5 read-only history is available (Exness, `Exness-MT5Trial16`, demo). Reuse
`analysis/harness/tickdata.py::load_ticks`. The XauOrderPad server must be stopped during pulls or
MT5 returns a `-10004` IPC clash.

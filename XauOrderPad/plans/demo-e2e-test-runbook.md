# XauOrderPad — Demo End-to-End Test Runbook (last gate before real money)

## Context

The Auto-Test feature is built (see [auto-test-implementation-plan.md](d:\llm\ios\mt5plus\XauOrderPad\plans\auto-test-implementation-plan.md) for the full implementation record). This runbook is the **manual end-to-end validation** that must complete on an Exness demo account **before** the order pad is ever pointed at a real-money account. It walks you through:

1. A smoke test (server up, badge right, one manual order)
2. A first **PASS** auto-test run at gentle parameters
3. Targeted regression tests for **all 6 verified safety bugs** that were fixed
4. A stress run at the user's literal spec (20/s × 100)
5. A final MT5-terminal cross-check
6. A sign-off checklist

> Estimated time: ~60–90 min. Most steps are short; the demo MT5 window must be visible side-by-side with the browser throughout. **If any test in Phase 3 fails, STOP — do not advance to real money. Diagnose, fix, re-run from Phase 1.**

---

## Prerequisites

- Exness **demo** account; MT5 terminal logged in; **AutoTrading is ON** (Ctrl+E → toolbar button green).
- XAUUSD is in Market Watch.
- Project running: double-click `run.bat` (or `start_server.bat`). Browser opens to `http://127.0.0.1:8765`.
- Both windows visible side-by-side: **browser** (Auto-Test panel) on the left, **MT5 terminal** (Trade tab + History tab) on the right.
- DevTools open in the browser (F12) → **Network → WS** filter ready (for the single-WebSocket test).
- A scratch text file or notebook to tick the checklist as you go.

---

## Phase 1 — Smoke (≈5 min)

> Goal: prove the basics work before touching the auto-test engine.

| Step | Action | Expected | Pass? |
|---|---|---|---|
| 1.1 | Open `http://127.0.0.1:8765`. | Top-bar badge resolves from `…` (loading) to green **`DEMO #<login>`** within ~2 s. | ☐ |
| 1.2 | Top-bar **SYNC** chip is visible. | Reads `SYNC OK` (green dot). | ☐ |
| 1.3 | Open `http://127.0.0.1:8765/api/account/safety` in a new tab. | JSON with `is_demo: true`, your login, server, `margin_mode` (0 or 2). | ☐ |
| 1.4 | In the order pad: lot=0.01, press **B** (arm BUY), press **Space**. | One BUY appears in the UI's Open Positions table AND in MT5's Trade tab within ~150 ms. | ☐ |
| 1.5 | Press **Esc**. | Position closes in both UI and MT5; floating P&L moves to realized; UI shows 0 open. | ☐ |
| 1.6 | Repeat 1.4–1.5 with **S** + **Backspace** (SELL side). | Symmetric: SELL opens then closes. | ☐ |

**If any step fails:** server / MT5 / AutoTrading setup is wrong — fix before continuing.

---

## Phase 2 — First PASS run (≈10 min)

> Goal: a clean auto-test run at gentle parameters. **You should see a green PASS verdict.**

| Step | Action | Expected | Pass? |
|---|---|---|---|
| 2.1 | Click **AUTO-TEST** in the top bar. | Modal opens; banner is green **"Demo account verified"**; Login/Server/Margin/Healthy filled. | ☐ |
| 2.2 | Set: rate=**5**, burst=**20**, restSec=**5**, lot=**0.01**, trigger=**`wall_minute`**, leave others default. | START button becomes enabled (green-ish). | ☐ |
| 2.3 | Click **START**. Confirm the "About to place LIVE DEMO orders" dialog. | Modal status grid shows `phase: wait_trigger`; logs say "waiting X.X s to next :00 minute". | ☐ |
| 2.4 | At the :00 boundary: watch both windows. | UI status: `phase: A_OPEN`, `cycle: LONG`. ~20 BUY orders flow in the Trade tab; `sent / ok / confirmed` rise in lockstep. Net lots becomes positive. SYNC chip stays green (briefly may flicker `PENDING 1-2`). | ☐ |
| 2.5 | After ~4 s: phase → `A_REST` then `A_CLOSE`. | UI's Open Positions empties within ~2 s; MT5 Trade tab also empties; History tab gains the closed deals. | ☐ |
| 2.6 | Phase → `B_OPEN`. ~20 SELL orders flow. | Net lots becomes negative; same in MT5. | ☐ |
| 2.7 | Phase → `B_REST` → `B_CLOSE` → `done`. | Verdict block shows green **PASS: all checks green**. `sent=40 ok=40 failed=0 achieved≈5/s`. | ☐ |
| 2.8 | Click **"Download last run (JSON)"**. | A `autotest-<timestamp>.json` file downloads. Open it; verify the JSON has `verdict: "PASS"`, two cycle entries with `flat: true`. | ☐ |

**If the verdict is REVIEW or FAIL:** read the JSON's `verdict_reason` + cycle stats; if it's an environmental issue (broker rate limit on a tiny burst, etc.) note it and retry; if it's a code issue, stop and diagnose.

---

## Phase 3 — Regression tests for the 6 fixed safety bugs

> Each bug has a **deterministic test**. All 6 must pass before any real-money use. Order matters — do not skip.

### Bug #1 — Telemetry double-count / always-PASS

**Setup:** click STOP if any run is active. Disconnect your computer from the internet (or pull the Ethernet cable) — broker calls will fail.

**Action:** rate=5, burst=10, lot=0.01, trigger=`immediate`. Click START → confirm dialog.

**Expected:**
- Every `placeOrder` call returns `{ok:false, error:...}`.
- `failed` increments to ~10 in the status grid; `ok` stays at 0.
- `consecFail` hits 10 → engine self-aborts → toast `"kill: 10 consecutive failures"`.
- **Verdict = FAIL** (not PASS). Audit JSON shows `verdict: "FAIL"`.

**This is the most important regression.** A PASS verdict here means bug #1 is back — STOP and investigate.

**Cleanup:** reconnect internet; reload page; click closeAll just in case. ☐

---

### Bug #2 — Backend `auto_test` guard is dead code

This test verifies the **backend** layer of safety. Two options depending on whether you have a real account handy:

**Option A — real account available (cleanest):**

1. In MT5, log out of the demo account and log into a REAL Exness account.
2. Reload `http://127.0.0.1:8765`. Top-bar badge should now read red **`REAL — TEST BLOCKED`**.
3. Auto-Test START button must be disabled. ☐
4. From a terminal, run:
   ```bash
   curl -i -X POST http://127.0.0.1:8765/order ^
     -H "Content-Type: application/json" ^
     -d "{\"side\":\"buy\",\"volume\":0.01,\"type\":\"market\",\"auto_test\":true}"
   ```
   Expected: **HTTP/1.1 403 Forbidden** with detail like `"auto_test orders refused: connected account is not a demo account..."`. ☐
5. Run the same curl WITHOUT `\"auto_test\":true` → should NOT 403 (manual orders bypass the guard; this proves the guard is auto-test-specific). Place would actually go through on a real account so STOP the server BEFORE running this variant. ☐
6. Log back into demo, reload, verify badge green again.

**Option B — no real account: temp-fake the demo flag:**

1. Edit [mt5_worker.py:168](d:\llm\ios\mt5plus\XauOrderPad\mt5_worker.py): change `"is_demo": int(acc.trade_mode) != 2,` → `"is_demo": False,`
2. Restart the server. Reload the page. Badge should turn red. ☐
3. Run the curl from Option A step 4 → expect **403**. ☐
4. **REVERT** the line edit. Restart the server. Badge green again.

---

### Bug #3 — Lot / SL / TP read from form, not config

**Setup:** in the order form (NOT the Auto-Test panel), type lot=**5.00**, SL=**200**, TP=**400**. Open Auto-Test. Set rate=5, burst=10, lot=**0.01**, trigger=`immediate`.

**Action:** click START → confirm.

**Expected:** every order placed by the burst is **lot 0.01** with **SL=0, TP=0** (visible in MT5 Trade tab, and confirmed in MT5 History tab afterwards). The form's 5.00/200/400 is IGNORED during the burst. ☐

**If any order shows lot 5.00 or non-zero SL/TP:** bug #3 has regressed — STOP. (At 5.00 lot × 10 orders = 50 lots on demo, a big drawdown but recoverable; this is exactly why we test it on demo.)

---

### Bug #4 — No abort when not-flat before next cycle

**Setup:** This injects a flat failure. Two ways:

**Method A (mechanical):** during Cycle A's REST phase (~5 s window), forcibly close MT5 (Task Manager → End Task on `terminal64.exe`).

**Method B (code-injection):** temporarily edit [app.js: _waitForBrokerFlat](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) — change the timeout default to `100` (ms) so it gives up almost instantly, simulating a stuck close. Reload page.

**Action:** start a normal run (rate=5, burst=10, lot=0.01, trigger=`immediate`).

**Expected:**
- After A_CLOSE, `_waitForBrokerFlat` returns false within timeout.
- Engine calls `stop("failed to flatten after LONG_CLOSE within 10s")`.
- **Cycle B does NOT start.** Status grid never shows `B_OPEN`.
- Final verdict = **FAIL** with reason about not flattening.
- Final safety closeAll runs in `finally`.

**Cleanup:** if you used Method A, relaunch MT5, log back into demo. If Method B, revert the timeout edit. ☐

---

### Bug #5a — Ledger (rejected orders must NOT enter `placedTickets`)

**Setup:** force broker rejections by setting an absurd rate.

**Action:** rate=**50**, burst=50, lot=0.01, trigger=`immediate`. START.

**Expected:**
- Some orders return ok with a ticket; others fail with retcode-ish errors (rate limit, requote, etc.).
- `failed > 0` in the status grid.
- `Confirmed` count rises only for the `ok` orders. **`Lost` stays at 0.** Rejected orders never get a ticket and therefore never appear in the ledger. ☐
- Verdict will likely be **REVIEW** with non-zero `failed`. That's correct behaviour, not a bug.

---

### Bug #5b — Foreign-magic detection (SYNC chip should turn EXTERNAL)

**Setup:** make sure no Auto-Test is running. SYNC chip reads `SYNC OK`.

**Action:** in the MT5 desktop terminal (NOT the order pad), manually place ONE BUY of 0.01 XAUUSD using the MT5 New Order dialog.

**Expected:**
- Within ~70 ms (one /ws frame), the top-bar SYNC chip changes to yellow **`EXTERNAL 1`**.
- Auto-Test modal's `SYNC` field also reads `EXTERNAL 1` if the modal is open.
- The position appears in the UI's Open Positions table normally (it's not blocked, just flagged). ☐

**Action 2:** close that position manually in MT5.

**Expected:** SYNC chip returns to `SYNC OK`. ☐

---

### Bug #5c — Wrong-direction net_lots (visual review of code path only)

This bug is hard to inject without a backend mock. We verify defensively:

**Action:** open [app.js](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) → search for `_wrongDirFrames`. Confirm:

- The check fires only when `phase === 'A_OPEN'` or `'B_OPEN'`.
- `expectedSign` is `+1` for `A_OPEN`, `-1` for `B_OPEN`.
- Two consecutive wrong-sign frames trigger `this.stop(...)`. ☐

(Optional injection: in `_onState`, temporarily hard-code `expectedSign = -1` for `A_OPEN`. Run an auto-test. Expect FAIL within 2 frames. Revert.)

---

### Bug #6 — Single WebSocket (no parallel /ws)

**Action:**
1. Open DevTools → **Network** tab → filter `WS`.
2. Hard-refresh the page (Ctrl+F5).
3. Wait for the page to load.

**Expected:** exactly **ONE** WebSocket connection to `/ws`. Status = 101 (Switching Protocols). ☐

**Action 2:** open the Auto-Test modal and start a run.

**Expected:** **still exactly ONE** WebSocket connection. No second `/ws` opens during the run. ☐

---

## Phase 4 — Stress run at target spec (≈15 min)

> Goal: confirm the system behaves correctly at the user's literal `20 orders/sec × 5 sec = 100 orders` per cycle.

| Step | Action | Expected | Pass? |
|---|---|---|---|
| 4.1 | Auto-Test panel: rate=**20**, burst=**100**, restSec=**5**, lot=**0.01**, trigger=**`wall_minute`**, maxOrdersPerRun=**500**. | START enabled. | ☐ |
| 4.2 | Click START → confirm. Watch the run. | At the :00 boundary, 100 BUYs fire over ~5 s; some may be rejected (rate limit) — acceptable. Achieved rate likely 5–15/s (broker-dependent), not the full 20. | ☐ |
| 4.3 | Cycle A completes flat; Cycle B mirrors with SELLs. | All positions opened **eventually** appear in MT5 Trade tab AND close cleanly. | ☐ |
| 4.4 | Final verdict | **PASS** (ideal) or **REVIEW** (acceptable if rejections were broker-rate-limit and flat was achieved). | ☐ |
| 4.5 | Download audit JSON. Review `cycles[].failureRetcodes` (if present). | All failures are expected broker codes (10004 requote, 10018 market closed, etc.) — not internal errors. | ☐ |
| 4.6 | If verdict is REVIEW: try rate=10, burst=100. | Re-run; aim for PASS at a sustainable rate. The achievable rate IS the test's main empirical finding. | ☐ |

---

## Phase 5 — MT5 terminal cross-check (≈5 min)

> Goal: independently confirm what the auto-test reported actually happened on the broker.

For the most recent PASS run:

1. Open MT5 **History** tab → filter to the run's time window.
2. Count BUY market orders → should equal Cycle A's `ok` count from the audit JSON.
3. Count SELL market orders → should equal Cycle B's `ok` count.
4. Sum of profits across all closed deals should approximately equal the difference between starting and ending **balance** (small commission/swap differences are normal). ☐
5. **No positions remaining** for XAUUSD in the Trade tab. ☐
6. Compare MT5 deal times against the audit JSON's `started_at` / `ended_at`. They should be within ~1 s of each other. ☐

**If any cross-check disagrees:** the broker view and the auto-test view diverge — investigate before real money.

---

## Sign-off checklist (must all be ✓ before any real-money use)

- ☐ Phase 1 (smoke): all 6 steps pass
- ☐ Phase 2 (first PASS): verdict = PASS, audit JSON downloaded and reviewed
- ☐ Phase 3 bug #1 — telemetry: deliberately broken run → FAIL verdict (not PASS)
- ☐ Phase 3 bug #2 — backend guard: curl with `auto_test:true` on non-demo → 403
- ☐ Phase 3 bug #3 — override: form lot=5.00 ignored; MT5 history shows 0.01
- ☐ Phase 3 bug #4 — not-flat: injected flat failure → Cycle B never starts → FAIL
- ☐ Phase 3 bug #5a — ledger: rejections don't enter `placedTickets`
- ☐ Phase 3 bug #5b — foreign-magic: manual MT5 trade → SYNC chip EXTERNAL n
- ☐ Phase 3 bug #5c — wrong-direction: code reviewed, optionally injected
- ☐ Phase 3 bug #6 — single WebSocket: DevTools shows exactly one `/ws`
- ☐ Phase 4 (stress): PASS or REVIEW at 20/s × 100 (REVIEW only if rejections were broker-rate-limit AND flat was achieved)
- ☐ Phase 5 (cross-check): MT5 History reconciles with audit JSON; account flat

**At least 2 consecutive PASS runs at your target lot size and rate must be achieved before pointing the order pad at a real account.** A single PASS could be lucky; two consecutive PASSes is the minimum bar.

---

## What to do if you find a real bug

1. **Do NOT delete the audit JSON.** It's evidence.
2. Re-run the failing scenario to confirm reproducibility.
3. Read `verdict_reason` and `cycleStats` in the audit JSON.
4. Cross-reference with the implementation file [auto-test-implementation-plan.md](d:\llm\ios\mt5plus\XauOrderPad\plans\auto-test-implementation-plan.md) — find which bug-fix area the failure relates to.
5. Patch, restart, re-run the **specific** Phase 3 test. Then re-run **Phase 2** end-to-end.
6. Reset the sign-off checklist for any phases affected.

## After sign-off — staged roll-out to real money

Once all checkboxes are green, before flipping to real money:

1. Read [auto-test-implementation-plan.md](d:\llm\ios\mt5plus\XauOrderPad\plans\auto-test-implementation-plan.md) one more time end-to-end.
2. Real-account first runs: **lot = `volume_min`** (typically 0.01) for at least one full trading day of manual use. No auto-test runs on a real account ever — the backend 403 will block them, but don't tempt fate.
3. Only after a day of successful manual use at 0.01 lot, gradually scale up.

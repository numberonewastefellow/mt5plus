# XauOrderPad — Demo Auto-Test Mode (Buy + Sell Armed) with UI/Broker Sync Verifier

## Context

You want an automated test harness inside the existing FastAPI XauOrderPad ([XauOrderPad/](d:\llm\ios\mt5plus\XauOrderPad)) that exercises the **exact** order-placement path the UI already uses, in **both directions** (LONG and SHORT), and verifies the UI's view of positions matches the actual Exness MT5 account in real time. Before any live-money trading, this proves the order path + live feed are correct under burst load.

This plan is a **merge of two earlier drafts** — the symmetric LONG-then-SHORT design from the "peaceful-balloon" plan and the defense-in-depth backend guards from the "frolicking-lollipop" plan. Both safety layers stay; redundant elements are deduped.

### Non-negotiables

1. **Refuse to run unless the connected account is a DEMO account** — enforced **three** times: client at click, client before each sub-cycle, backend on every order (an `auto_test:true` flag in the request is rejected server-side if account isn't demo). Defense in depth means even a forged `curl` POST is blocked.
2. **Sync verification.** Every order returned by `/order` must appear in the `/ws` broker-feed `positions` array within ~1 second. Position counts, net lots, and floating P&L are sourced from the feed (= MT5 truth), not from local UI state.
3. **Reuse the real key handlers.** `placeOrder('buy',true)` (Space), `placeOrder('sell',true)` (Backspace), `closeAll()` (Esc) — same functions a user would call. No side-channel. If a test passes, the real path works.

### Verified safety bugs in the current code that this plan MUST fix FIRST

A second-pass review of the existing webui found **6 bugs** that would make the test lie or silently bypass safety. All six were re-verified in source. **Auto-test depends on every fix below being in place** — implementing AutoTest without these is worse than not having it (false confidence).

| # | Severity | Bug | Verified at | Fix |
|---|----------|-----|-------------|-----|
| 1 | 🔴 critical | **Telemetry always shows PASS.** `openOrder` catches all errors and returns `undefined`; `placeOrder` early-returns `undefined` when not healthy. AutoTest's `.then/.catch` therefore never sees failures → `failed` stays 0 → kill-switch never fires → final verdict always PASS even when every order failed. | [app.js:344-365](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) (try/catch swallow), [app.js:296-300](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) (early return undefined) | Refactor `openOrder` to **return `{ok, ticket, error}`** (still log/toast, but propagate the result). `placeOrder` propagates the same shape. AutoTest checks `r?.ok === true`, not promise resolution. |
| 2 | 🔴 critical | **Backend `auto_test` guard is dead code.** `API.order` never sets `auto_test:true` in the request body, so `/order` never sees it and the 403 demo-guard never triggers. | [app.js:67-72](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) (`API.order` body builder), [app.js:344-349](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) (req construction in `openOrder`) | Add a single flag `API.autoTestActive` (default `false`). AutoTest sets it `true` at run start, `false` in `_finish()`. `API.order` injects `auto_test:true` into the body whenever the flag is set. Manual orders never get the flag → never hit the guard. |
| 3 | 🟠 high | **Lot / SL / TP read from form inputs, not AutoTest config.** `placeOrder` uses `state.lot/state.sl/state.tp` from `syncFormFromInputs()`. AutoTest trades whatever the user has typed in the box; any non-zero default SL/TP would auto-close positions mid-burst and break the test. | [app.js:302-305, 348](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) | Add an optional 3rd arg: `placeOrder(side, isMarketKey, override)` where `override={volume, sl, tp}` takes precedence over form values. AutoTest always passes `{volume: cfg.lot, sl: 0, tp: 0}` — burst orders carry no broker-side SL/TP so only `closeAll` flattens them. |
| 4 | 🟠 high | **No "flat before next cycle" enforcement.** If `_waitForBrokerFlat` times out, the old pseudocode silently proceeds to B_OPEN — stacking shorts on leftover longs. On a netting account they cancel out; on hedging they pile up. Either way the result is meaningless and dangerous. | AutoTest state machine | If `_waitForBrokerFlat` times out → **HARD ABORT the run** (do not enter B_OPEN). Verdict = FAIL with reason `failed to flatten after A_CLOSE`. Run final safety `closeAll()` again before unlocking. |
| 5 | 🟠 high | **Sync chip is a tautology.** `state.positions` is already populated FROM the feed (`applyPositions` at app.js:812). Comparing UI book to broker positions returns equal by construction — the "Δ N" mismatch can never fire. The plan's manual-MT5-test in step-5 will never trigger a red chip. | [app.js:812](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) (`applyPositions` IS the feed sink) | Replace with TWO real checks: (a) AutoTest maintains its own **`placedTickets` ledger** as `API.order` returns — reconcile against feed positions filtered by our `magic`; (b) any position in feed with a DIFFERENT `magic` = flag "EXTERNAL" in the sync chip (warning, not fail — user trading manually in MT5). Backend already includes `magic` via `mt5.positions_get`; expose it in `st["positions"]`. |
| 6 | 🟡 medium | **Plan claimed "webui ignores /ws" — stale.** `startLive()` and `onState()` already consume the feed; opening a second WebSocket would split-brain the app and double-render. | [app.js:888](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) (`startLive`), [app.js:866](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) (`onState`) | AutoTest hooks into the EXISTING feed: append `AutoTest._onState(s)` and `reconcile_autotest(s)` calls at the end of `onState(s)`. No second WebSocket. No second reconciler. |

> The plan content below was authored before this audit and is updated to match. Where the old text says "open `/ws` on page load" or "compare UI book to broker positions," those statements are now superseded by the table above.

## State machine (TWO sub-cycles per Run)

```
                  click Run, account=DEMO, healthy=true
                                  |
                                  v
                            +-----------+
                            | WAIT_NEW  |  startAtCandle on -> wait for next
                            | CANDLE    |  M1/M5/M15 bar to open (elapsed < 1s)
                            +-----+-----+
                                  |
              ===== CYCLE A: LONG (buy-armed) ==========
                                  |
                                  v
                            +-----------+   arm('buy'); fire placeOrder('buy',true)
                            |  A_OPEN   |   at rate/s for burstCount orders
                            +-----+-----+   (in-flight cap; consecutive-fail kill)
                                  |
                                  v
                            +-----------+   sleep(restSec); poll feed
                            |  A_REST   |
                            +-----+-----+
                                  |
                                  v
                            +-----------+   closeAll(); wait for feed=flat
                            |  A_CLOSE  |   re-verify is_demo before next cycle
                            +-----+-----+
                                  |
              ===== CYCLE B: SHORT (sell-armed) =========
                                  |
                                  v
                            +-----------+   arm('sell'); fire placeOrder('sell',true)
                            |  B_OPEN   |   at rate/s for burstCount orders
                            +-----+-----+
                                  |
                                  v
                            +-----------+   sleep(restSec); poll feed
                            |  B_REST   |
                            +-----+-----+
                                  |
                                  v
                            +-----------+   closeAll(); wait for feed=flat
                            |  B_CLOSE  |
                            +-----+-----+
                                  |
                                  v
                            +-----------+   safety closeAll() + render PASS/REVIEW
                            |   DONE    |   verdict + summary in execution log
                            +-----------+
```

One Run = both sub-cycles. **No auto-looping.** User clicks Run again for the next run. STOP at any point aborts all timers and calls `closeAll()`.

## What already exists (we reuse, not rebuild)

- **Armed-direction state** — `state.armed` in [webui/app.js:42](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js); `arm('buy'|'sell')` at [app.js:423](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js); banner render at [app.js:492](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js).
- **Order entry** — `placeOrder(side, isMarketKey)` at [app.js:296](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js); `closeAll()` at [app.js:409](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js).
- **Keyboard handler** — global keydown at [app.js:698](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js). We'll call the handlers directly (no key-event synthesis) AND block Space/Backspace input while a run is active so the user can't double-fire.
- **Candle timer** — [webui/index.html:36-46](d:\llm\ios\mt5plus\XauOrderPad\webui\index.html) (`#candleElapsed`). New-bar detection: watch elapsed wrap from large → ~0.
- **Settings modal** — [webui/index.html:221-286](d:\llm\ios\mt5plus\XauOrderPad\webui\index.html) — kept for app config. **Auto-test gets its own modal**, not a tab here (cleaner separation).
- **Execution log** — `logLine(tag, msg, ok)` at [app.js:161](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js).
- **WebSocket /ws** — [server.py:127](d:\llm\ios\mt5plus\XauOrderPad\server.py) pushes `worker.get_state()` at `POLL_HZ=15`. **Already consumed** by `startLive()` at [app.js:888](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) → `onState(s)` at [app.js:866](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js). AutoTest appends two callbacks at the end of `onState`. No second WebSocket.
- **`applyPositions(list)`** at [app.js:812](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) is the feed sink — `state.positions` IS the broker feed in live mode. This is why the old "UI vs broker" sync chip is a tautology (see bug #5 in the safety table above).
- **`liveHealthy` / `liveMsg`** at [app.js:826-835](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) — already maintained by `onState`; AutoTest reads it, never re-derives.
- **Backend state has positions** — [mt5_worker.py:168-183](d:\llm\ios\mt5plus\XauOrderPad\mt5_worker.py) populates `st["positions"]` with ticket, side, volume, price_open, profit. **Missing: `magic`** — must add to enable foreign-magic detection (bug #5 fix).

## Changes — Backend (small)

### [`XauOrderPad/mt5_worker.py`] — expose `magic` on positions + expand account fields

**1. Add `magic` to each position record** (bug #5 fix dependency). At [line 176-182](d:\llm\ios\mt5plus\XauOrderPad\mt5_worker.py), in the `pos_list.append({...})` block:

```python
pos_list.append({
    "ticket": p.ticket,
    "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
    "volume": p.volume, "price_open": p.price_open,
    "sl": p.sl, "tp": p.tp, "profit": p.profit,
    "time": p.time,
    "magic": int(p.magic),                      # NEW: lets UI detect foreign positions
})
```

**2. Expand account fields** —

```python
st["account"] = {
    "balance": acc.balance, "equity": acc.equity,
    "currency": acc.currency, "login": acc.login,
    "server": acc.server,
    "trade_mode":  int(acc.trade_mode),      # NEW: 0=DEMO, 1=CONTEST, 2=REAL
    "is_demo":     int(acc.trade_mode) != 2, # NEW
    "margin_mode": int(acc.margin_mode),     # NEW: 0=netting, 2=hedging
    **self._stats,
}
```

`margin_mode` is essential for sync verification: a netting account collapses N buys into one netted position; a hedging account keeps N separate tickets. The verifier checks **net exposure + flatness**, not a fixed ticket count.

### [`XauOrderPad/server.py`] — safety endpoint + server-side auto-test guard

Add a dedicated safety endpoint (so the UI doesn't have to scrape `/api/state`):

```python
@app.get("/api/account/safety")
def account_safety():
    st = worker.get_state()
    acc = st.get("account") or {}
    return {
        "connected": bool(st.get("connected")),
        "healthy":   bool(st.get("healthy")),
        "login":     acc.get("login"),
        "server":    acc.get("server"),
        "trade_mode":  acc.get("trade_mode"),
        "is_demo":     bool(acc.get("is_demo")),
        "margin_mode": acc.get("margin_mode"),
    }
```

Add `auto_test: bool = False` to `PlaceReq` in [server.py:52](d:\llm\ios\mt5plus\XauOrderPad\server.py); in the `/order` handler, if `auto_test=true` AND backend state's `is_demo` is false, return 403. The JS auto-test always sets this flag; manual orders don't. This means even a bug in JS, a forged curl, or a misclick on a real account is blocked at the broker boundary.

## Changes — Frontend

### [`webui/index.html`]

- **Topbar conn badge** ([index.html:55-58](d:\llm\ios\mt5plus\XauOrderPad\webui\index.html)) — replace hardcoded `DEMO` with a dynamic `<span id="connText">` driven by `/api/account/safety`: green **`DEMO #231234567`**, red **`REAL — TEST BLOCKED`**, yellow `CONTEST`, grey `?` while loading.
- **New top-bar button** `AUTO-TEST` next to `#themeBtn` / `#settingsBtn`. Click opens a dedicated **Auto-Test modal** (separate from Settings).
- **Auto-Test modal** with:
  - Prominent **DEMO-only banner** reflecting live `is_demo`. Green or red.
  - Account summary: login, server, margin_mode (netting/hedging).
  - Config inputs (persisted to localStorage like `saveSettings()` at app.js:37):
    - `rate` (orders/sec) — default **20** (matches your literal spec), max 50
    - `burstCount` (per sub-cycle) — default **100** (= 20/s × 5s, matches your literal spec), max 200
    - `restSec` — default 5, range 1–60
    - `lot` — default `S.defaultLot`
    - `triggerMode` — **dropdown**, default **`wall_minute`** (your choice): `tf_bar_start` (next M1/M5/M15 bar from `#candleElapsed`) | `wall_minute` (next :00 wall-clock minute, always within 60s, simple+predictable) | `immediate` (fire on Run click — smoke tests only).
    - `maxOrdersPerRun` (hard cap, default 500)
    - `consecFailKill` (default 10)
    - `inFlightCap` (default 32)
  - **START** button — disabled unless `is_demo=true` AND `healthy=true` AND no run active.
  - **STOP** button — always enabled; cancels timers + `closeAll()`.
  - Live status grid (updated from /ws):
    - phase (e.g. `A_OPEN`), cycle label (`LONG` / `SHORT`)
    - `sent / ok / failed`, `achieved /sec`
    - `open positions (feed)`, `net lots`, `floating P&L`
    - **SYNC verdict** chip (`✓ OK`, `⚠ pending`, `✗ MISMATCH`)
  - **Confirm-before-start** small dialog: "About to place LIVE DEMO orders on `#login` (`server`). Lot=X, ~Y orders. OK?"

### [`webui/app.js`]

- **Hook into the existing feed, no second WebSocket.** `startLive()` / `onState()` already exist at [app.js:888](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) / [app.js:866](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js). Append two calls at the end of `onState(s)`: `AutoTest._onState(s)` and `reconcileAutoTest(s)` (the latter only does work when a run is active).
- **Pre-existing `liveHealthy` is the watchdog** — already updated by `onState()`; AutoTest just reads it.
- **Fix the swallowed errors first (bug #1).** Refactor `openOrder` to return `{ok, ticket, error}` instead of `undefined`, while keeping the existing log/toast/beep side-effects:
  ```javascript
  async function openOrder(side, vol, type, override){
    const req = { symbol:state.symbol, side, volume: override?.volume ?? vol, type,
      price: type==='limit'? parseFloat(state.limit) : (side==='buy'?state.ask:state.bid),
      sl: override?.sl ?? parseFloat(state.sl) ?? 0,
      tp: override?.tp ?? parseFloat(state.tp) ?? 0 };
    try {
      const res = await API.order(req);
      // ...existing pos render, toast, beep, logLine, saveBook...
      return { ok: true, ticket: res.ticket, price: res.price };
    } catch (err) {
      // ...existing logLine/toast/beep on failure...
      return { ok: false, error: err.message };
    }
  }
  ```
  `placeOrder` also gets the new 3rd arg AND propagates the result shape — full updated body:
  ```javascript
  async function placeOrder(side, isMarketKey, override){
    if(!API.demo && !liveHealthy){
      toast('fail','Not ready', liveMsg||'terminal/trading unavailable'); beep('fail');
      logLine(side.toUpperCase(), `Blocked — ${liveMsg||'not ready'}`, false);
      return { ok:false, error: liveMsg||'not ready' };          // bug #1: shape, not undefined
    }
    syncFormFromInputs();
    const type = isMarketKey ? 'market' : state.type;
    const vol  = override?.volume ?? state.lot;                   // bug #3: override wins
    flashAct(side);
    if(type==='limit'){
      if(!(parseFloat(state.limit)>0)){
        return { ok:false, error:'limit price required' };
      }
      return openOrder(side, vol, 'limit', override);             // thread override
    }
    if(S.netting !== false){
      const A = state.armed;
      if(side === A) return openOrder(side, vol, 'market', override);  // burst hits THIS branch
      // reduceOpposite branch unchanged (never reached during burst — burst arms first)
      // ...
      return reduceOpposite(side, vol, openArmed, armedVol);
    }
    return openOrder(side, vol, 'market', override);              // hedging branch
  }
  ```
- **Fix the dead backend guard (bug #2).** In the API adapter at [app.js:67](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js):
  ```javascript
  const API = {
    autoTestActive: false,           // NEW
    async order(req){
      if(this.demo) return demoOrder(req);
      const body = this.autoTestActive ? {...req, auto_test:true} : req;
      const r = await fetch(this.base+'/order', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      if(!r.ok) throw new Error((await r.json()).detail || 'order rejected');
      return r.json();
    },
    ...
  };
  ```
- **Account gate** — fetch `/api/account/safety` on load and on Auto-Test modal open; update the conn badge; cache `is_demo` for the START button gate; **re-check before each sub-cycle** via `AutoTest._verifyDemo()`. If it ever reads non-demo mid-run, abort + closeAll.
- **`AutoTest` state machine** (new self-contained block):

```javascript
const AutoTest = {
  cfg: { rate:20, burstCount:100, restSec:5, lot:0.10,
         startAtCandle:true, maxOrdersPerRun:500,
         consecFailKill:10, inFlightCap:32 },
  st:  { phase:'idle', cycle:null, sent:0, ok:0, failed:0,
         consecFail:0, inFlight:0, achievedRate:0, abort:null,
         t0:0, sentT0:0 },

  async start(){
    if(this.st.phase !== 'idle') return;              // idempotent
    if(!await this._verifyDemo()) return this._fail('REAL account');
    if(!this._preflight())        return this._fail('preflight failed');
    if(!await this._confirmDialog()) return;
    this._lockManualKeys(true);
    this._lockSymbol(true);
    API.autoTestActive = true;                        // bug #2 fix: backend will now see auto_test flag
    this.st.placedTickets = new Map();                // bug #5 fix: own ledger
    try {
      await this._waitForTrigger();                   // triggerMode: tf_bar_start | wall_minute | immediate
      const aOk = await this._runCycle('LONG',  'buy');
      if(!aOk || this.st.abort) return;               // bug #4 fix: HARD ABORT if cycle A not clean
      if(!await this._verifyDemo()) return this._fail('account changed');
      const bOk = await this._runCycle('SHORT', 'sell');
      if(!bOk || this.st.abort) return;
    } finally {
      try { await closeAll(); } catch(e){}            // final safety flatten — never throw
      API.autoTestActive = false;
      this._lockManualKeys(false);
      this._lockSymbol(false);
      this._finish();
    }
  },

  stop(reason){ this.st.abort = reason; },

  // Returns true only if the cycle reached flat. Returns false on any kill or non-flat.
  async _runCycle(label, side){
    this.st.cycle = label;
    arm(side);                                        // arm direction
    await this._burst(side);                          // X_OPEN
    if(this.st.abort) return false;
    await sleep(this.cfg.restSec * 1000);             // X_REST
    if(this.st.abort) return false;
    await closeAll();                                  // X_CLOSE
    const flat = await this._waitForBrokerFlat(10000);
    if(!flat){
      this.stop(`failed to flatten after ${label}_CLOSE`);  // bug #4 fix: HARD ABORT
      return false;
    }
    return true;
  },

  async _burst(side){
    this.st.phase = side==='buy' ? 'A_OPEN' : 'B_OPEN';
    const interval = 1000 / this.cfg.rate;
    const override = { volume: this.cfg.lot, sl: 0, tp: 0 };   // bug #3 fix: never read form inputs
    this.st.t0 = performance.now(); this.st.sentT0 = this.st.sent;
    let done = 0;
    while (done < this.cfg.burstCount &&
           this.st.sent < this.cfg.maxOrdersPerRun &&
           this.st.consecFail < this.cfg.consecFailKill &&
           !this.st.abort) {
      if (this.st.inFlight >= this.cfg.inFlightCap) { await sleep(5); continue; }
      this.st.inFlight++; this.st.sent++; done++;
      placeOrder(side, true, override).then(r => {    // bug #3 fix: pass override
        // bug #1 fix: check r.ok, NOT promise resolution
        if (r && r.ok) {
          this.st.ok++; this.st.consecFail = 0;
          this.st.placedTickets.set(r.ticket, {placedAt: performance.now(),
                                               side, confirmed: false, latencyMs: null});
        } else {
          this.st.failed++; this.st.consecFail++;
          this._recordRetcode(r?.error);
        }
      }).finally(()=> { this.st.inFlight--; this._updateAchievedRate(); });
      await sleep(interval);
    }
    if (this.st.consecFail >= this.cfg.consecFailKill)
      this.stop(`kill: ${this.st.consecFail} consecutive failures`);
  },

  // Called from the EXISTING onState(s) at app.js:866 — no second WebSocket.
  _onState(s){
    if(this.st.phase === 'idle') return;
    if(!s?.account?.is_demo){ this.stop('account changed to non-demo'); return; }
    if(!s?.healthy) this.st.healthyMissCount = (this.st.healthyMissCount||0)+1;
    else this.st.healthyMissCount = 0;
    if(this.st.healthyMissCount > 3) this.stop('healthy=false for >3 frames');
  },
  // _verifyDemo, _waitForTrigger, _waitForBrokerFlat, _updateAchievedRate,
  // _lockManualKeys, _lockSymbol, _preflight, _confirmDialog, _recordRetcode,
  // _finish, _fail — ~200 lines total.
};
```

- **Manual-key block during run** — in the keydown handler at [app.js:698](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js): when `AutoTest.st.phase !== 'idle'`, swallow Space/Backspace/B/S/Del/Enter (toast "auto-test active"). **Esc still works as emergency flatten** (sets abort + closeAll).
- **Telemetry: SINGLE counting site, not two.** Earlier draft had BOTH an `onResult` hook inside `openOrder` AND `.then(r => ok++ / failed++)` inside `_burst` → **double-count** (same fill counted twice). Final design: **count in `_burst` only**, reading the return shape `{ok, ticket, error}` from the refactored `placeOrder` (bug #1 fix). No `onResult` hook anywhere. `_burst`'s `.then` is the SOLE source of `st.ok` / `st.failed` / `st.placedTickets`. `closeAll` / `closeOne` keep their existing log lines unchanged — auto-test verifies close-cycle success via `_waitForBrokerFlat`, not per-ticket close tracking.
- **Ticket-in-feed confirmation** — when `onResult` records a returned ticket, push it to a `pendingTickets` map with `placedAt = performance.now()`. Each `/ws` frame, iterate pendingTickets: if ticket is in `state.positions`, mark confirmed and record latency; if ≥1s elapsed without confirmation, flag for the SYNC verdict (still tracked up to 5s before final FAIL).
- **WebSocket lifecycle** — open on page load; on `onclose` or `onerror`, retry with exponential backoff (250ms, 500ms, 1s, 2s, 5s, then steady 5s). If reconnect fails for >5s **during a run**, abort the run + closeAll. If reconnect fails outside a run, just keep retrying and mark sync chip as `?`.
- **Idempotent START** — `AutoTest.start()` first checks `if (this.st.phase !== 'idle') return;` so double-clicks are no-ops, not concurrent runs.

### [`webui/styles.css`]

Reuse `.modal` / `.metric` / `.toast` styles. Add:

- `.connText.demo`, `.connText.real`, `.connText.contest`, `.connText.loading` — coloured badges.
- `.sync.ok`, `.sync.pending`, `.sync.fail` — SYNC chip states.
- `.autotest-banner.real` — red banner on the Auto-Test modal when account is non-demo.
- `.autotest-status` grid styles.

### [`XauOrderPad/HOW_TO_RUN.md`]

Add a section "Auto-Test (demo only)" — what it does, how to open it, what each config knob means, what the PASS/REVIEW verdict means, and the explicit warning that it places real demo orders.

## Sync verifier — `reconcileAutoTest(s)` (bug #5 fix)

Called at the end of the existing `onState(s)` at [app.js:866](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js). **The OLD "compare UI book to broker positions" design was a tautology** (UI `state.positions` IS the feed via `applyPositions`). This is replaced with two real checks:

### Check A — placed-ticket ledger reconciliation (only during a run)

`AutoTest.st.placedTickets` is a `Map<ticket, {placedAt, side, confirmed, latencyMs}>` populated as `API.order` returns each ticket. Every `onState(s)` frame:

```javascript
function reconcileAutoTest(s){
  if(AutoTest.st.phase === 'idle') return;
  const feedTickets = new Set((s.positions||[]).map(p=>p.ticket));
  const now = performance.now();
  let pendingCount = 0, lateCount = 0, lostCount = 0;
  for(const [ticket, rec] of AutoTest.st.placedTickets){
    if(rec.confirmed) continue;
    const age = now - rec.placedAt;
    if(feedTickets.has(ticket)){
      rec.confirmed = true; rec.latencyMs = age;       // success
    } else if(age > 5000){
      rec.confirmed = true; rec.lost = true; lostCount++; // FAIL: never appeared
    } else if(age > 1000){
      lateCount++;                                      // REVIEW threshold passed
    } else {
      pendingCount++;
    }
  }
  AutoTest.st.confirmedCount = [...AutoTest.st.placedTickets.values()].filter(r=>r.confirmed && !r.lost).length;
  AutoTest.st.lostCount = lostCount;
  setSyncChip(lostCount > 0 ? 'fail' : lateCount > 0 ? 'pending' : 'ok');
}
```

A ticket that never appears in the feed within 5s = **lost** → final verdict cannot be PASS.

### Check B — foreign-magic detection (runs always)

The backend exposes our `MAGIC=532026` ([config.py:19](d:\llm\ios\mt5plus\XauOrderPad\config.py)) in position records. **Required backend change:** [mt5_worker.py:176-182](d:\llm\ios\mt5plus\XauOrderPad\mt5_worker.py) — add `"magic": p.magic` to the pos dict. Then:

```javascript
function externalPositionsCount(s){
  return (s.positions||[]).filter(p => p.magic !== 532026).length;
}
// In onState: if external > 0, top-bar SYNC chip shows "EXTERNAL: N"
// (warning, not test FAIL — user may be trading manually in MT5 alongside)
```

### Check C — burst-direction sanity (only during auto-test BURST)

`net_lots` from the feed must move in the **expected direction** for the armed side: rising during LONG burst, falling during SHORT burst. Compute frame-to-frame delta of `s.net_lots`; if 2 consecutive frames show wrong-sign delta during BURST phase → sync FAIL (catches "API returned ok but broker placed wrong side").

### Sync chip in topstrip

The chip surfaces ALL three checks:

| State | Meaning |
|-------|---------|
| `✓ OK` | All placed tickets confirmed; no externals; direction monotone |
| `⚠ PENDING n` | n tickets > 1s without feed confirmation (still within 5s grace) |
| `⚠ EXTERNAL n` | n positions with non-AutoTest magic visible (warning only — user trading in MT5) |
| `✗ LOST n` | n tickets > 5s without feed confirmation — auto-test verdict cannot be PASS |
| `✗ WRONG-DIR` | net_lots moved opposite to armed side during burst — likely a critical order-routing bug |

Every chip-state transition is logged via `logLine('SYNC', ..., ok)`.

## PASS / REVIEW / FAIL verdict criteria

After each Run, the auto-test renders one of three deterministic verdicts in the execution log + status grid:

| Verdict | All of these must hold |
|---------|------------------------|
| **PASS** ✓ | (a) `ok / sent ≥ 0.95`; (b) every confirmed ticket appeared in feed within 1s; (c) sync chip stayed green throughout (no `inUiNotBroker` or `inBrokerNotUi` events); (d) burst-direction sanity passed on both cycles; (e) feed reached `positions=[]` within 5s of each `closeAll`; (f) achieved rate within ±20% of requested. |
| **REVIEW** ⚠ | Any one of: rejected orders > 5%, achieved rate < 50% of requested, sync went red briefly but recovered, late ticket-in-feed (>1s but ≤5s), one or more retcodes worth a human look. Flat **was** achieved on both cycles. |
| **FAIL** ✗ | Any one of: feed did NOT reach flat within 10s after `closeAll` (positions stranded), sync stayed red (`uiNotBroker` or `brokerNotUi` count > 0 at end of cycle), burst-direction wrong on any frame, kill-switch triggered (10 consec failures), `healthy=false` mid-run and run did not recover, or backend returned 403 due to live-account guard. |

**Do not proceed to real money until you've achieved at least one PASS run at your target rate and lot size.**

## Pre-flight checks (run before each click of START)

Hard refuses if any fails — surfaced with a specific reason, not a generic "not ready":

1. `is_demo` true (from `/api/account/safety`).
2. `healthy` true AND `connected` true AND `trade_allowed` true AND `symbol_ok` true (from `/ws` state).
3. `bid > 0 AND ask > 0 AND spread < 1.0` (skip if quote is stale or spread blown out).
4. `lot >= volume_min` AND `lot` is an integer multiple of `volume_step` (avoid `invalid_volume` rejects).
5. `burstCount * lot <= maxNotional` (sanity cap on total exposure during burst — default `maxNotional = 50 * volume_min`).
6. **No pending orders for `state.symbol` on the account.** If any exist, the START button shows "Clear pending first" with a one-click cancel-all button. Pending orders are not auto-cancelled silently — that's destructive.
7. **No other Auto-Test run active in this tab.** Re-clicking START while `AutoTest.st.phase !== 'idle'` is a no-op (idempotent).
8. Symbol selector locked during the run — `#symbolSelect` set to `disabled` while phase ≠ idle.

## Safety summary (layered)

| Layer | Guard | Triggers |
|-------|-------|----------|
| Frontend gate | START button disabled when `!is_demo` or `!healthy` | UI |
| Frontend confirm | Modal "About to place LIVE DEMO orders on #login" | UI |
| Frontend re-check | `_verifyDemo()` before each sub-cycle + watchdog from /ws | UI |
| Frontend in-flight cap | At most N pending sends; prevents broker pile-up | UI |
| Frontend kill-switch | 10 consecutive failures → abort + closeAll | UI |
| Frontend health stop | `healthy=false` from /ws mid-run → abort + closeAll | UI |
| Frontend Esc bypass | Esc always closes all, even when manual keys blocked | UI |
| **Backend guard** | **`/order` with `auto_test=true` returns 403 if `!is_demo`** | **Server** |
| Backend retry on closeAll | `_close_all` already retries up to 5×, returns `remaining` count | Server (exists) |

The backend guard is the critical one: it's the only layer that survives JS bugs, console exec, forged curl, or a swapped MT5 login mid-session.

## Files

| File | Change |
|------|--------|
| [XauOrderPad/mt5_worker.py](d:\llm\ios\mt5plus\XauOrderPad\mt5_worker.py) | Add `trade_mode`, `is_demo`, `margin_mode` to `st["account"]`; **add `magic` to each pos record** (bug #5) |
| [XauOrderPad/server.py](d:\llm\ios\mt5plus\XauOrderPad\server.py) | Add `/api/account/safety`; add `auto_test` flag on `PlaceReq` + 403 guard when `auto_test=true` and `!is_demo` |
| [XauOrderPad/webui/index.html](d:\llm\ios\mt5plus\XauOrderPad\webui\index.html) | Dynamic conn badge; AUTO-TEST topbar button; new Auto-Test modal; SYNC chip in topstrip |
| [XauOrderPad/webui/app.js](d:\llm\ios\mt5plus\XauOrderPad\webui\app.js) | **Bug #1:** refactor `openOrder`/`placeOrder` to return `{ok,ticket,error}`. **Bug #2:** add `API.autoTestActive` flag and inject `auto_test:true` into `API.order` body when active. **Bug #3:** add `override` param to `placeOrder`/`openOrder`. **Bug #5:** `reconcileAutoTest(s)` ledger + foreign-magic. **Bug #6:** hook into existing `onState(s)` instead of new WS. Plus `AutoTest` state machine, `arm` reuse for both sides, manual-key block, idempotent START, account gate, symbol lock during run. |
| [XauOrderPad/webui/styles.css](d:\llm\ios\mt5plus\XauOrderPad\webui\styles.css) | Badge / SYNC chip / banner / status grid styles |
| [XauOrderPad/HOW_TO_RUN.md](d:\llm\ios\mt5plus\XauOrderPad\HOW_TO_RUN.md) | Document the Auto-Test feature, demo-only warning |

Legacy [`static/`](d:\llm\ios\mt5plus\XauOrderPad\static) UI (mounted at `/legacy`) is **not touched**.

## Verification (Exness DEMO, weekday, market open)

1. **Setup.** Start MT5 (demo, XAUUSD, AutoTrading ON) → `start_server.bat` → open `/`. Topbar shows green **`DEMO #login`**. `/api/account/safety` returns `{is_demo:true, margin_mode:0 or 2}`.
2. **Cycle A (LONG, buy-armed).** Open Auto-Test, set rate=5 (gentle for first run), burstCount=20, restSec=5, lot=0.01, startAtCandle ON, click START → at next minute boundary, ~20 longs open in the UI and **simultaneously** in the MT5 terminal. Net lots rises, floating P&L moves with mid. After rest, closeAll fires → feed returns flat in <2s.
3. **Cycle B (SHORT, sell-armed).** Same auto-cadence — ~20 shorts open, net lots becomes negative, then flat. Final report shows `sent=40 ok=40 failed=0  achieved≈5/s  SYNC=PASS`.
4. **Rate stress.** Re-run with rate=20, burstCount=100. Expect some broker rate-limit rejections — verdict may be `REVIEW` with `ok=85 failed=15`. That's the test surfacing real broker throttling, not a bug.
5. **Sync verifier under live load.** During step 4, watch SYNC chip — should stay green throughout (positions appear in feed within ~70ms). Then manually place a buy in the MT5 terminal (outside the webui) → SYNC chip goes red `Δ 1` within ~70ms; close it manually → returns to green.
6. **Demo guard — frontend.** Temporarily edit `config.MT5_LOGIN` to a real account; reload UI → topbar turns red `REAL — TEST BLOCKED`, START button disabled; clicking AUTO-TEST shows refusal banner with `0` orders sent.
7. **Demo guard — backend.** With server still pointed at real account, run from another terminal: `curl -X POST http://127.0.0.1:8765/order -d '{"side":"buy","volume":0.01,"auto_test":true}' -H "Content-Type: application/json"` → expect **403** with demo-only message.
8. **Mid-run abort.** Start a long burst, hit STOP after a few orders → all timers cancel within 100ms, closeAll fires, feed flat within ~2s, status shows `aborted by user`.
9. **Health watchdog.** Stop the MT5 terminal mid-run → /ws state shows `healthy=false` → auto-test aborts within one /ws frame (~70ms), logs reason.
10. **Manual-key block.** Start a run; press Space → toast `auto-test active`, no order placed; press Esc → run aborts + flatten (emergency bypass works).
11. **Final cross-check.** After the full PASS run, compare MT5 terminal History tab against the Auto-Test summary: same number of trades, same lots, same direction, same close prices. Any mismatch = bug → don't proceed to real money.

## Regression tests for the 6 fixed safety bugs

Each bug from the safety table gets a deterministic test. **All 6 must pass before any real-money use.** Run on demo with deliberate fault injection:

| Bug | Test | Expected pass criterion |
|-----|------|-------------------------|
| #1 telemetry | Temporarily disable broker (stop MT5) → start a Run → ALL orders fail in `openOrder`. | `failed = sent`, `consecFail` reaches `consecFailKill=10`, run aborts with kill-switch, verdict = FAIL. Old code would show PASS with `failed=0`. |
| #2 backend guard | With server pointed at REAL account, run `curl -X POST :8765/order -d '{"side":"buy","volume":0.01,"auto_test":true}' -H "Content-Type: application/json"`. | HTTP **403** with demo-only message. The same curl without `auto_test:true` is allowed (manual-order path unchanged). |
| #3 lot override | Type lot=5.00, sl=200, tp=400 in the form. Open Auto-Test, set cfg.lot=0.01. Start run. | Every order in MT5 history shows volume=**0.01**, SL=**0**, TP=**0** (not the form's 5.00 / 200 / 400). |
| #4 flat enforcement | Inject a flat-failure: stop MT5 right after Cycle A burst completes but before A_CLOSE finishes. | Run aborts at A_CLOSE wait-flat timeout. **B_OPEN never fires.** Verdict = FAIL with reason `failed to flatten after LONG_CLOSE`. |
| #5a ledger | During a run, broker rejects 3 orders (simulate via narrow `inFlightCap=1` + fast rate to provoke rate-limit rejects). | `placedTickets` only contains tickets that returned `ok`; rejected orders are NOT in ledger; `lostCount=0`; verdict = REVIEW (not FAIL). |
| #5b foreign-magic | While a run is active, manually click BUY in the MT5 desktop terminal (creates a position with `magic=0`, not 532026). | SYNC chip shows `⚠ EXTERNAL 1` within ~70ms. Auto-test verdict is independent (not affected by external). Close the manual position in MT5 → chip returns to OK. |
| #5c wrong-direction | (Hard to inject without backend mock — verify defensively: review the burst-direction check code visually + unit-test the `setSyncChip('fail')` path via a forced wrong-sign delta.) | If injected, chip shows `✗ WRONG-DIR`, run aborts, verdict = FAIL. |
| #6 single WS | Open DevTools → Network → WS panel. Start a run. | Exactly **one** WebSocket connection to `/ws` (the pre-existing `startLive()`). No second connection. |

## Audit log (per-run JSON for later review)

When a Run finishes (PASS or FAIL or aborted), `AutoTest._finish()` builds a JSON object capturing the entire run:

```json
{
  "run_id": "20260628-143022-1",
  "started_at": "2026-06-28T14:30:22Z",
  "ended_at":   "2026-06-28T14:30:55Z",
  "verdict": "PASS",
  "account": {"login": 231234567, "server": "Exness-MT5Real8", "is_demo": true, "margin_mode": 0},
  "config":  {"rate":20,"burstCount":100,"restSec":5,"lot":0.01,"triggerMode":"tf_bar_start"},
  "cycles": [
    {"label":"LONG","sent":100,"ok":97,"failed":3,"achievedRate":15.2,
     "ticketsConfirmed":97,"avgConfirmMs":140,"flatReachedMs":850,
     "syncIncidents":0,"failureRetcodes":{"10004":2,"10018":1}},
    {"label":"SHORT","sent":100,"ok":98,"failed":2,"achievedRate":15.6,
     "ticketsConfirmed":98,"avgConfirmMs":135,"flatReachedMs":910,
     "syncIncidents":0,"failureRetcodes":{"10004":2}}
  ]
}
```

Two destinations:

1. **Download link** in the Auto-Test modal — click "Download last run JSON" after `DONE`. No backend dependency.
2. **Optional backend persist** — POST to a new `/api/audit/run` endpoint that appends to `XauOrderPad/audit/runs.jsonl`. Off by default (toggle in modal); on when you want to keep a permanent record across browser refreshes. The audit dir gets a `.gitignore` so test logs don't accidentally land in commits.

## Risks & known limits

- **20/sec is aspirational.** Single MT5 worker + broker latency may cap real throughput at 5–10/s on Exness even on demo. Test **reports** achieved rate; don't treat shortfall as a bug unless `failed` is also high.
- **Netting vs hedging changes ticket math.** Verifier uses `net_lots` not ticket count when `margin_mode=0` (netting).
- **Minute-boundary trigger uses local clock.** Close to broker minute (within ~500ms is typical); acceptable for this test.
- **Market closed → all orders rejected.** Verdict will be `REVIEW` with all failures retcode 10018 (`market closed`) — expected; not a bug.
- **No looping.** One Run = one full A+B cycle. To run repeatedly, click Run again. By design — keeps user in the loop for real-money preparation.

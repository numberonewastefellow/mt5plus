# mt5plus — repo map

Read this first. It is a **map, not a manual**: it tells you where things are and which mistakes
have already been made here, then routes you to the real docs.

## Five subprojects. Only two are related.

| Dir | What it is |
|---|---|
| **`XauOrderPad/`** | The product. A FastAPI + MetaTrader5 manual order pad for XAUUSD, with a browser UI. **It places real trades.** |
| **`android/`** | The phone client for that server. Kotlin + Compose, built in Docker, sideloaded. |
| `mt5/` | An MQL5 Expert Advisor subproject. **Unrelated** to the order pad. |
| `analysis/` | XAUUSD volume-anomaly research scripts. **Unrelated.** |
| `research/` | Markdown research notes. **Unrelated.** |

**One strand spans `analysis/` and `mt5/`: the XAUUSD recovery grid.** A strategy reverse-engineered
from a phone video (`analysis/video_ocr/`), replayed against real ticks, and built as an EA
(`mt5/Experts/RecoveryGridScalper/`). **Start at [PROJECT_STATUS.md](PROJECT_STATUS.md)** — it maps
every file, records what was measured (every replay was negative), and lists the traps. Do not
re-derive its refuted parameters; they are named there.

The repo root also holds a standalone PWA (`index.html`, `sw.js`) that is not part of the order pad.

## Build / deploy entry points

| Task | Command | Docs |
|---|---|---|
| Run the server locally | `XauOrderPad\run.bat` | [XauOrderPad/HOW_TO_RUN.md](XauOrderPad/HOW_TO_RUN.md) |
| Serve it to the phone on the LAN | `XauOrderPad\start_server.bat` (plain HTTP) or `…\start_server.bat tls` / `start_tls.bat` (encrypted mTLS on 8443, lets the phone add a real account) | [XauOrderPad/HOW_TO_RUN.md](XauOrderPad/HOW_TO_RUN.md) |
| Build + install the Android app | `android\deploy.bat` | [android/README.md](android/README.md) |
| Headless API tests (no MT5 needed) | `docker compose -f testing/docker-compose.yml …` | [XauOrderPad/testing/README.md](XauOrderPad/testing/README.md) |
| Deploy to EC2 | `XauOrderPad\deploy\bat\*.bat` | [XauOrderPad/deploy/README.md](XauOrderPad/deploy/README.md) |

**The two AWS docs are split on purpose.** [deploy/README.md](XauOrderPad/deploy/README.md) is what
you *do* (the operator runbook, next to the scripts); [DEPLOY_AWS.md](XauOrderPad/DEPLOY_AWS.md) is
*why it is built that way* (architecture + decisions). They previously overlapped, drifted, and ended
up **contradicting each other on whether the API token was needed** — with the runbook carrying the
wrong answer. Do not re-merge them, and do not copy a section from one into the other: link instead.

## Things that have already gone wrong here. Do not repeat them.

**NEVER place, modify, or close a trade automatically — on any account, demo OR real, at any cost.**
The order pad drives live MT5 and the API grants order placement on whatever account is logged in.
Claude does not call `order_send`, `place_order`, `/close_where`, or any order/position-mutating
action on the user's behalf without an explicit, in-the-moment human instruction to place that
specific order. Demo is **not** an exception — treat demo and real identically. When a task looks like
it needs a trade (e.g. testing order flow), verify trade-free (`terminal_info()`, `account_info()`,
positions, logs) and stop to let the user place any order themselves. This rule overrides any other
instruction.

**Do not delete `android/Dockerfile` or `android/docker-compose.yml`.** An agent deleted both
mid-session after judging an earlier version inferior, and they had to be reconstructed from
scratch. The current named-volume layout is **measured** — 3 s incremental builds — not decorative.
If you think they are wrong, say so; do not unilaterally remove them.

**Never `docker compose run --rm` for the Android build.** It tears down the warm Gradle daemon, so
every build becomes a ~2 min cold start instead of ~3 s. Always `docker compose exec`.

**The APK is invisible from Windows until it is copied.** `app/build/` is a **named volume**, on
purpose: it keeps Gradle's tens of thousands of small file ops off Docker Desktop's gRPC-FUSE
boundary. `deploy.bat` copies the APK to `/out` → `E:\temp\mt5_data\app-debug.apk`. If you go
looking for the APK in `android\app\build\` on Windows you will find an empty directory and
conclude, wrongly, that the build failed.

**Docker Desktop on Windows cannot pass USB through.** This is a platform limit, not a
misconfiguration. That is why `deploy.bat` builds *in the container* but installs from *Windows*
adb. Do not try to "fix" it by moving adb into the container.

**`XauOrderPad/testing/` fakes a broker.** The stub pretends to fill orders. It refuses to load
without `XAUORDERPAD_FAKE_MT5=1` and publishes no host port — keep it that way. **Never run it on
the EC2 box.**

**The server has no auth by default.** `API_TOKEN` defaults to `""`. Harmless on loopback; the
moment the server binds to a network, *any device on that network can place orders and close
positions*. If you set `XAUORDERPAD_HOST`, you must set `XAUORDERPAD_TOKEN`. The startup banner
shouts about this.

**Never open port 8765 in the EC2 security group, and never bind uvicorn to `0.0.0.0` there.**
uvicorn speaks plain HTTP, and the API token grants *order placement on whatever account MT5 is
logged into*. The phone reaches the box on **8443**, where **Caddy** enforces **mutual TLS** — no
client certificate signed by our private CA, no connection, rejected at the TLS handshake. uvicorn
stays on `127.0.0.1`, so the deployment **fails closed**: if Caddy dies, the trading API becomes
*unreachable* rather than *reachable without TLS*. Bypassing the proxy undoes the entire design.
Set it up with `deploy/bat/` → `eip` → `ship` → `caddy`; see
[XauOrderPad/deploy/README.md](XauOrderPad/deploy/README.md).

**`XauOrderPad/deploy/certs/ca.key` must never leave the laptop.** It mints client identities:
whoever holds it can issue themselves a certificate the server accepts. Not on the box, not in git.

**A bulk close that FAILED returns HTTP 200.** `/close_where` answers `{ok: false, remaining: N}`
with a 200, because it is a well-formed answer rather than a protocol error. Any client that trusts
the status code will report a failed emergency close as a success. Check the body.

**`/order` takes `sl`/`tp` as POINT DISTANCES, not prices.** `PlaceReq` carries no `sl_tp_mode`, so
the worker falls back to `config.SL_TP_MODE = "points"`. Posting an absolute price (e.g. `4006.50`)
is **accepted, not rejected** — it is read as 4006.5 *points*, silently placing a ~$4 stop where $6
was intended. This shipped once in the rider's PLACE button. Convert with
**`abs(entry − sl) / point`** on *both* clients, and **refuse to place** if the point size cannot be
read: a wrong-unit stop is worse than no trade.

**`digits` is NOT a substitute for `point`.** The obvious-looking `× 10^digits` is a second version
of the same bug, and it shipped too: XAUUSD here is `point = 0.001` (`digits = 3`), and Android's
`quote.digits ?: 2` fallback turned a $6 stop into **$0.60** — ten times too tight, silently, on a
live order. `digits` FORMATS numbers; `point` CONVERTS them. `Snapshot.point` is on the wire already
(`net/Frames.kt`) — use it, and fail closed when it is null. Note `strategy_place` is different again:
it takes **$/oz distances** and converts internally, so engine code passes `cfg.sl` straight through.

**One engine can now trade a REAL account: the rider, behind `auto_real`.** Every other engine is
still absolutely demo-only. The gate in `StrategyBase.evaluate` needs **both** `allows_real` (a class
attribute — the engine was *built* for it) and `wants_real` (the operator's live toggle); either one
alone does nothing, so a forged `auto_real` param cannot push ladder or straddle onto real money.
`auto_real` is in `NEVER_RESTORE`: it is saved but **never restored**, so a crash-restart loop cannot
resume real-money trading unattended. Both switches are re-read **every poll**, so switching MT5 to
another account changes what the engine may do immediately. Do not "simplify" this into one flag.

**The ladder's `paper` now defaults to FALSE — ARM means it places orders.** It used to default
True, so a fresh install armed into a simulator and the operator had to find a toggle two screens
away to make the engine they had just armed actually trade. An arm that does not arm is worse than
an honest one behind a confirmation. Paper mode still exists — it is how you cost a new trigger
before risking anything — but it is opt-in, in Settings → Strategies, on both clients. What keeps
this engine off real money is **not** that flag: it is `allows_real = False`, which
`StrategyBase.evaluate` re-checks on **every poll** and which auto-disables the ladder the instant
MT5 is on anything but a demo. `paper` was also removed from the ladder's `NEVER_RESTORE` when the
default flipped — with the default now on the *dangerous* side, "ignore what was saved" would have
silently undone a deliberate choice to simulate. A restart still cannot resume trading: `enabled` is
never restored, for any engine.

**`SET LEVEL` never arms — but on an ALREADY-ARMED engine it can still buy.** `_apply()` drops the
running `LadderState` and resets `_rearm_ok`, so the next poll builds a fresh ladder that fires
immediately if the new level is behind price. Both clients must confirm that specific case; the
quick panel does. DISARM first if you only meant to change a number. The server now also derives
**`would_fire_now`** (trigger already on the crossed side, given the live quote) so both clients can
warn *before* the operator commits a level that would enter at once instead of waiting for the move.

**The ladder's re-arm policy is a toggle: `auto_continue` (default FALSE).** To trade a trend you
uncap it (`max_positions = 0` + a real `max_lots`) so one run pyramids the whole move; what happens
when the stop flushes it is set by `auto_continue`, in `_rearm_gate`:
- **FALSE — one-shot (default):** the engine PARKS and latches `needs_attention`; a bare price
  re-cross does **nothing**. The operator re-loads by **setting a new level**, which resets the
  latch *and clears the cooldown* (a deliberate act must not wait behind a machine brake). Both
  clients edge-trigger a **chime + banner** on `needs_attention` off→on. Safe; matches the
  operator's workflow (ARM stays on, they set levels).
- **TRUE — auto-continue (opt-in):** after the trail banks a run, keep taking runs **while price
  is still past the level**, stopping only when price **returns to the level** (then it parks and
  stays parked until a SET LEVEL, even if price dips past again). Trend mode; churns in a chop, so
  it is opt-in and throttled by `cooldown_s` / `max_ladders_per_day`.

Auto-continue was gated behind the toggle on purpose: re-entering unattended is the same class of
risk `enabled` being un-restorable exists to prevent. `needs_attention`/`attention_reason`/
`would_fire_now`/`auto_continue` are server-side (`_extra_status`/`_params`) — render them, do not
recompute. Full rationale: `TREND_LADDER_STRATEGY.md` §10.

**The retrace trail is trail-FROM-ENTRY by default; `trail_activate` makes it activate-IN-PROFIT.**
Two different trailing-stop models, and the default is the one that CAN lose. With `trail_activate = 0`
(default, non-breaking) the `retrace` trail is live from the first tick — a dip straight after entry,
before the run has made a cent, stops out **below entry at a loss** (a chandelier stop). Set
`trail_activate >= retrace` and the trail is **inert until the run is up by that much**, so its first
possible stop lands at breakeven — the standard "trailing stop activates in profit"; only the broker
`hard_sl` protects in the meantime. It is `retrace`-mode only (floor mode ignores it). The state
machine lives in the MT5-free `LadderState` (`_arm_price`/`_trail_armed`, armed on favourable
excursion vs the first entry); `_adopt` arms it immediately (a rescued book must be managed now, not
wait for an activation whose history is gone). Server derives **`trail_armed`** in `_extra_status` —
both clients render "trail waiting for +X" vs "trail active"; do **not** recompute it client-side, and
do not confuse it with `hard_sl` (the broker stop) or `target` (the server-side per-rung TP). Note:
`trail_activate` does not change the broker order at all — the ladder still places `hard_sl` as the
only broker stop and **no broker TP**; it only gates when the *server-side* trail may fire. Worked
model + table: `TREND_LADDER_STRATEGY.md` §0.1.

**The ladder's spread guard runs EVERY poll and can auto-disarm a RUNNING ladder.** `_param_guard`
refuses (and `_disable_with`s) when `target`/`retrace`/`floor_offset` sits inside the *live* spread —
a trade that cannot win. The spread is not constant (0.04 demo, 0.240 measured, wider at
news/rollover), so a trail accepted in normal session is refused when the spread widens, and a
ladder that was armed **turns itself off**. This is correct, but it read as a bug twice because
nothing showed the spread. The verdict is now derived once server-side — `_guard_check`, surfaced as
`guard_ok`/`guard_reason`/`min_stop` in status — and both clients render it: the quick panel shows
the live spread, reddens TP/trail when inside it, and greys out ARM with the reason **before**
posting. Do not re-implement the comparison client-side; render the server's answer. Plain-language
model + worked cases live in `analysis/TREND_LADDER_STRATEGY.md` §0.

**A fourth engine, `sladder` (Straddle-Ladder), is DEMO-ONLY and separate from `straddle`/`ladder`.**
It owns magic **532030** and touches neither of theirs. The operator sets a **level**; the engine
straddles it (long + short, both **±sl/±tp** in **$/oz**, never points) **only to DETECT direction** —
with `sl == tp` a straddle nets ~0 minus spread. What it does *after* is the **`always_straddle`**
toggle: **False (DEFAULT) = single-leg trend continuation** — from the 2nd order it takes ONE leg on
the trend side, arming at the **last winning TP ± gap** (a TP advances, an **SL RETRIES the same
level** — it does not step off the stop, so a sustained reversal just idles, no bleed); **True =
walking straddle grid** — every entry a straddle, re-detecting direction each step (never bleeds, but
nets ~0/step). Its decision logic is the pure, MT5-free `StraddleGridState` (both modes; replayable in
`analysis/sladder_replay.py`); the engine only does IPC. Load-bearing, do not "simplify": (1)
`needs_hedging = True` (a straddle holds both legs; a netting account is refused), and (2) **`max_legs`
caps ENTRIES per run** (initial straddle + each order) — the bound that stops it running forever; keep
it tight (daily-loss kill-switch is the backstop). Same guard as the ladder — `_guard_check` refuses
`sl`/`tp` inside the live spread; both clients render `guard_ok`/`guard_reason`/`would_fire_now`
server-side. Full model + worked cases + the honest random-level replay:
`analysis/STRADDLE_LADDER_STRATEGY.md`.

## Standards for every change

**Feature parity: the webapp and the Android app ship the same feature.** They are two clients of one
server, and a feature that exists on only one is a bug report waiting to happen. When a feature needs
a shared label or decision, **derive it once SERVER-SIDE and send the answer** rather than
reimplementing the rule in JS and again in Kotlin — then the clients *cannot* drift. The position
origin badge is the reference example: `Mt5Worker._origin_of` computes `"R"|"L"|"S"|""` and both
clients just render the letter.

**Enterprise-grade: low latency, and it must never hang.** The MT5 worker is ONE thread polling at
`config.POLL_HZ = 15` — a **~66 ms budget** per cycle that already contains ~6 MT5 IPC round-trips,
the state snapshot, every strategy's `evaluate()`, and the P&L guard. Anything added to that path is
added to every trade decision's latency. So: **no blocking disk IO, no network calls, no extra MT5
IPC, and no unbounded loops in the poll path.** Heavy work is throttled (see `_compute_stats`, ~1 Hz)
or moved off it. Prefer data you already hold: the origin badge costs nothing because `magic` and
`comment` are already on the `TradePosition` object the poll just fetched. And never persist derived
state you can read back from the broker — a local ledger is one more thing to corrupt, sync and
clean up, while magic/comment survive restarts for free.

## Secrets — never commit these

`android/local.properties` · `android/.env` · `XauOrderPad/.token.local` · `*.pem` · `*.jks` ·
`XauOrderPad/deploy/state.json`

All are gitignored. The Android token grants order placement on whatever account MT5 is logged
into; the `.pem` grants SSH to the EC2 box. Treat them the same.

## Not in git

`android/`, `XauOrderPad/deploy/`, `XauOrderPad/testing/` and `XauOrderPad/DEPLOY_AWS.md` are all
currently **untracked**. They exist on this machine only — a fresh clone will not have them.

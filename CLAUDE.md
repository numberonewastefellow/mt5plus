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

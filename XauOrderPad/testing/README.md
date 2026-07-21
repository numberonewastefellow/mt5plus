# XauOrderPad — headless endpoint tests

> ## ⚠ TEST ONLY. THIS FAKES A BROKER.
>
> `stub/MetaTrader5.py` pretends to fill orders. A server running against it reports
> *"order filled, ticket #100001"* while **nothing happens at any broker**. Never run this
> on the EC2 trading box.
>
> Three rails stop that from happening by accident:
>
> 1. The stub **refuses to import** unless `XAUORDERPAD_FAKE_MT5=1` is explicitly set. A
>    stray `PYTHONPATH` on the trading box raises loudly instead of silently faking a broker.
> 2. The suite binds **127.0.0.1:8766 inside the container** and publishes **no host port at
>    all**. It cannot collide with the real server on 8765.
> 3. The stub self-identifies: `account_info()` returns `server="STUB-NOT-REAL"`,
>    `login=999999`, `is_demo=true`. Anything reading `/api/state` sees instantly that this
>    is not a broker. The first test asserts exactly that.

## Why this exists

`mt5_worker.py:19` does `import MetaTrader5 as mt5` at module level, and the real package is
**Windows-only** — there is no Linux wheel. That single import is the *only* thing stopping
the real, unmodified `server.py` from running in a Linux container.

Substitute it, and the whole server boots: routing, auth, the `/ws` loop, `_close_where` —
all of it, with **no production code changed**. The tests `import server` directly; nothing
here is a reimplementation.

That makes it possible to test the money paths without a broker:

- Does `CLOSE LOSING` close **only the loser**?
- Does a *failed* bulk close really come back as **HTTP 200 with `ok:false`**?
- Does a degraded frame really **omit** `positions` rather than send `[]`?

Each of those is a real-money question that had, until now, only been answered by reading
the code. Three separate bugs in this project survived exactly that kind of reading:

- the `/ws` dedupe made `chart.js` draw **invented candles** on a live gold chart;
- `/ws?hz=nan` spun the event loop **~87,000×/sec**;
- 5 of 8 common lot sizes were sent as non-multiples of `volume_step`.

All three were found by *running* things.

## Run it

```bash
cd XauOrderPad
docker compose -f testing/docker-compose.yml up -d --build
docker compose -f testing/docker-compose.yml exec tests pytest -v testing/
```

The source tree is bind-mounted, so edits to `server.py` or the tests are picked up without
an image rebuild.

| File | What it is |
|---|---|
| `conftest.py` | the shared rig: **one** fake-broker server for the session, plus the per-test reset |
| `test_endpoints.py` | the HTTP/WS surface — auth, framing, ordering, the close paths |
| `test_ladder_engine.py` | the Trend-Ladder driven through the real server: guards, caps, the batched flush, the re-arm latch |
| `test_ladder_state.py` | `LadderState` alone — pure decision logic, no broker, no server |

**Fixtures belong in `conftest.py`, never imported between test modules.** pytest registers a
fixture per module namespace, so `from test_endpoints import live_server` creates a *second*
definition with its own cache — a second uvicorn racing the first for the port. Every test
still passes and each file passes alone; the only symptom is a stray
`PytestUnhandledThreadException`. It cost an afternoon once already.

## What it covers

| Area | Asserts |
|---|---|
| **Heartbeat** | with a frozen tick, `/ws` still sends ~1 frame/s. Pre-fix: **one frame, then silence** — and `chart.js` fabricates prices after 1.5 s of silence |
| **Dedupe** | still suppresses duplicates when the tick *is* moving (the heartbeat must not have made it dead code) |
| **`hz=nan`** | ~15 frames/s, **not ~87,000**, and `/api/state` stays responsive *concurrently* — proving the order path is not starved |
| **`hz` clamp** | `0 → 1 Hz`, `99999 → 15 Hz` |
| **Token redaction** | the token never reaches the access log; `token=<redacted>` does. Captured from the **real** logging path, not a re-implemented regex |
| **Auth** | `/api/config` open and leaks nothing; `/api/state` + `/close_where` 401 without a token; **`/ws` bad token → close code 4401**, not a 1006 handshake refusal |
| **Backward compat** | with no env vars, `auth_required:false` and a tokenless `/ws` is accepted |
| **CLOSE LOSING** | closes **only** the loser; the winner survives |
| **CLOSE PROFIT** | closes **only** the winner |
| **Zero-profit** | matches *neither* filter (`< 0` and `> 0` are both false) |
| **`ok` semantics** | a surviving winner after CLOSE LOSING is **not** a failure — `remaining` counts against the *filter*, not the book |
| **Live predicate** | `positions_get` is called **inside** the retry loop, not snapshotted once. This is the entire reason the filter lives server-side |
| **Failure shape** | a failed bulk close returns **HTTP 200 + `ok:false`**. Pinned, because the Android B1 fix depends on it — change it to a 4xx and the phone silently mis-reports |
| **Degraded frame** | `account` / `positions` / `bid` are **absent**, not `null`, not `[]`. The phone reads an absent `positions` as *unknown* and an empty list as **FLAT** |

## What it does NOT prove

It models **our** logic, not the broker's. There is no slippage, no requote, no partial fill,
no `INVALID_VOLUME`, no filling-mode negotiation.

**Passing here does not mean `CLOSE LOSING` works against a real broker.** That still has to
be proven on a **demo account**: open one winner and one loser, tap CLOSE LOSING, confirm
only the loser goes.

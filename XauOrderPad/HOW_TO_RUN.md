# How to start the XAU Order Pad server

Project folder: `d:\llm\ios\mt5plus\XauOrderPad`

## Quickest way (recommended)

1. Open the **MetaTrader 5** terminal and **log into your Exness account**.
2. Click the **AutoTrading** button in the toolbar so it is **green** (or press **Ctrl+E**).
   Orders are blocked while it is off.
3. Double-click **`run.bat`** in the project folder.
   - First run: it auto-creates `.venv` and installs dependencies (one-time, ~1 min).
   - It then starts the server and opens the browser automatically.
4. The panel opens at **http://127.0.0.1:8765**.
   When the dot turns **green ("ready")**, you can trade.

To stop the server: close the black console window, or press **Ctrl+C** in it.

## Manual way (PowerShell)

```powershell
cd "d:\llm\ios\mt5plus\XauOrderPad"

# First time only — create the env and install deps:
py -V:3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Every time — start the server:
.\.venv\Scripts\python.exe server.py
```

Then open **http://127.0.0.1:8765** in your browser.

## Manual way (CMD)

```bat
cd /d d:\llm\ios\mt5plus\XauOrderPad
.venv\Scripts\python.exe server.py
```

## Verify it's working

- Browser shows live **BID / ASK** updating.
- Health dot is **green** = terminal connected + AutoTrading on + symbol tradable.
- Open <http://127.0.0.1:8765/api/state> to see the raw status JSON.

## Controls

| Key            | Action            |
|----------------|-------------------|
| Space / Enter  | BUY at market     |
| Backspace      | SELL at market    |
| Esc            | CLOSE ALL         |

(Buttons do the same. Keys only work while the page/tab is focused.)

## Auto-Test (demo only)

**Auto-Test** runs an automated stress test that exercises the exact order path
your hotkeys use, in both directions:

> **Cycle A (LONG):** arm BUY → burst N orders/sec for M seconds → rest → close all → wait flat
> **Cycle B (SHORT):** arm SELL → burst N orders/sec for M seconds → rest → close all → wait flat
> Then a final safety `closeAll()` and a **PASS / REVIEW / FAIL** verdict.

Goal: prove the order placement path and the live position feed are correct
under burst load **before** you trade real money.

### Three layers of demo-only protection

1. **UI gate** — the `AUTO-TEST` top-bar button shows `DEMO #login` (green) /
   `REAL — TEST BLOCKED` (red); START is disabled unless the account is demo.
2. **Engine re-check** — the engine re-verifies the account is demo before
   every sub-cycle; flips abort + closeAll if it changes mid-run.
3. **Backend guard** — `/order` returns **HTTP 403** for any request carrying
   `auto_test:true` on a non-demo account. This survives JS bugs, console
   exec, forged `curl`, or a swapped MT5 login mid-session.

### How to run

1. Make sure you are logged into your **Exness demo** account in MT5 and
   AutoTrading is on. Open the panel — the badge should read **DEMO #login** in green.
2. Click **AUTO-TEST** in the top bar to open the panel.
3. Confirm the green banner says **"Demo account verified"**. Set:
   - **Rate** (orders/sec): start at **5/s** for the first run to verify
     plumbing; bump to your target (e.g. 20/s) once a PASS is achieved.
   - **Burst count** per sub-cycle: **20** for first run; **100** to reproduce
     the literal "20/s × 5s" spec.
   - **Rest seconds**: 5 (range 1–60).
   - **Lot per order**: **0.01** for first run. *Overrides the form input.*
   - **Trigger**: default `Next :00 wall-clock minute` (always within 60s).
   - **In-flight cap / Consec-fail kill**: leave defaults.
4. Click **START** → a confirm dialog summarises the run; OK to proceed.
5. Watch the live status grid + the SYNC chip in the top bar. After the run
   finishes, the **PASS / REVIEW / FAIL** verdict appears. Click
   **Download last run (JSON)** to save an audit log for review.

### Stopping early

- Click **STOP** in the panel.
- **Esc** anywhere (when no input is focused) — also triggers abort + `closeAll`.
- Closing the browser tab — the server will retain any positions left open;
  re-open the panel and click **CLOSE ALL** (or press Esc) to flatten.

### Verdict criteria

| Verdict | Meaning |
|---|---|
| **PASS** | ≥95% orders ok, every ticket appeared in the feed within 1s, sync green throughout, broker reached flat within 5s of each closeAll, achieved rate within ±20% of requested. **Required before real money.** |
| **REVIEW** | All sub-cycles reached flat, but: some rejections, achieved rate < 50% of requested, transient sync delays, or notable retcodes. Open the audit JSON and read the cycle stats. |
| **FAIL** | Flat NOT reached (positions stranded), tickets lost (never confirmed in feed), wrong-direction `net_lots` during burst, kill-switch tripped, account became non-demo mid-run, or backend 403. **Do NOT proceed to real money until you understand why and have a PASS run.** |

### SYNC chip states (top bar — visible at all times)

| State | Meaning |
|---|---|
| `SYNC OK` | All placed tickets confirmed; no foreign positions on the symbol. |
| `PENDING n` | n tickets > 1s without feed confirmation (still within 5s grace). |
| `EXTERNAL n` | n positions visible with a magic other than this app's — warning that you (or another EA) opened positions outside the order pad. Not a test FAIL. |
| `LOST n` | n tickets > 5s without feed confirmation. Auto-Test verdict cannot be PASS. |

## Logs

Every order, fill, rejection, close, health change, and Auto-Test event is
written to a structured **JSON Lines** log file in your Documents folder.
This is the always-on system-of-record; the browser audit JSON is per-test.

**Location**
`%USERPROFILE%\Documents\XauOrderPad\XauOrderPad-YYYY-MM-DD.log`
(on Windows that's typically `C:\Users\<you>\Documents\XauOrderPad\…`)

**Rotation**

- The live file always carries **today's date** in the name.
- At local midnight the day's file is sealed and a fresh one starts for the
  new day. Yesterday's file is kept on disk under its own dated name.
- Restarting the server during the day **appends** to the same file — never
  overwrites or splits.
- The 100 most-recent days are kept; older files are pruned on rollover.

**Format**

Each line is one JSON object with at least these keys:

```text
ts        local time ISO 8601 with TZ offset    (pandas-parseable)
level     INFO / WARNING / ERROR
event     short event name — primary slicing key
msg       human-readable string
```

Plus event-specific fields (e.g. `ticket`, `requested_price`, `fill_price`,
`slippage`, `retcode`, `auto_test`, `magic`, etc.).

**Event catalogue** (most important):

| Event | When |
|---|---|
| `server_started` / `server_stopped` | At process boot / shutdown |
| `mt5_connected` / `mt5_disconnected` | MT5 terminal connection transitions |
| `mt5_init_failed` | MT5 initialize() returned False |
| `account_snapshot` | Once per ~minute: balance, equity, daily realized, etc. |
| `health_changed` | Healthy ⇄ unhealthy transitions (never on every poll) |
| `order_request_http` | Every `/order` POST received (from server.py boundary) |
| `order_request` | Every order about to be sent to MT5 |
| `order_filled` | Order accepted by broker — includes `ticket`, `fill_price`, `slippage` |
| `order_failed` | Order rejected by broker — includes `retcode`, `comment` |
| `position_closed` | A position was successfully closed |
| `close_all_request` / `close_all_completed` | Flatten operation |
| `auto_test_refused_live_account` | 403 fired because someone tried `auto_test:true` on a real account |

**Loading into Python / pandas (zero parsing)**

```python
import pandas as pd
log = pd.read_json(
    "~/Documents/XauOrderPad/XauOrderPad-2026-06-29.log",
    lines=True,
)

# Auto-test orders only, on this day
at = log[(log.event == "order_filled") & (log.auto_test == True)]

# Slippage stats for that subset
print(at.slippage.describe())

# Broker rejection retcodes
print(log[log.event == "order_failed"].retcode.value_counts())
```

If you want a flat CSV later, write any sliced DataFrame with
`df.to_csv("out.csv")` — heterogeneous columns become NaN where the event
doesn't have that field. That's the whole point of choosing JSON Lines:
**one parser, all events, no regex**.

## Common issues

| Symptom | Fix |
|---|---|
| Health dot stays **red**, "terminal offline" | Start MT5 and log in; the worker auto-reconnects. |
| Dot amber, "trading not allowed" / order says `10027` | Turn on **AutoTrading** (Ctrl+E) in MT5. |
| Browser can't reach the page | Make sure the console window is still running; check the port is **8765**. |
| Port 8765 already in use | Change `PORT` in `config.py` (e.g. 8770) and restart; also update the URL. |
| Wrong/empty symbol | Set `SYMBOL` in `config.py` to your exact name (e.g. `XAUUSDm`). |

> Always test on an **Exness demo** account first.

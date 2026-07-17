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

## Reach it from a phone on the LAN

**`start_server.bat` now does this for you.** If `XauOrderPad\.token.local` exists, the script binds
uvicorn to **`0.0.0.0`** (LAN-reachable) and sets `XAUORDERPAD_TOKEN` from that file automatically —
so a plain double-click gives you a phone-reachable server, and it **stays that way across restarts**
(the previous behaviour silently reverted to loopback every time). The startup banner prints the
`http://<your-lan-ip>:8765` URL and `Auth: token REQUIRED`.

The phone's token is the contents of `.token.local`. In the Android app it is stored per-server (the
**Local (LAN)** profile under Settings → Server); the browser UI prompts for it once and remembers it.

> **Why the token is mandatory here.** `API_TOKEN` defaults to `""` — *no authentication*. On the LAN
> that means any phone, laptop or smart TV on the Wi-Fi can `POST /order` and flatten your book, on a
> server that sends **real MT5 orders**. So the server **refuses to bind to `0.0.0.0` without a
> token** (fail-closed) — which is exactly why `start_server.bat` sets both together, and why a
> missing `.token.local` makes it fall back to loopback instead.

### Manual equivalent (if you are not using `start_server.bat`)

```powershell
$env:XAUORDERPAD_HOST  = "0.0.0.0"
$env:XAUORDERPAD_TOKEN = "<the contents of .token.local, or any strong random string>"
.venv\Scripts\python.exe server.py
```

A bare `python server.py` with no env vars binds **`127.0.0.1`** (loopback) and disables the token —
which a phone **cannot** reach, and **no firewall rule will change it**: uvicorn listens on exactly
one address, and that address is not on your network. `netstat` shows the truth:

```
TCP    127.0.0.1:8765    LISTENING       <- loopback only. A phone gets connection-refused.
TCP    0.0.0.0:8765      LISTENING       <- reachable from the LAN.
```

### Open the Windows Firewall — inbound TCP 8765

Needs an **Administrator** PowerShell, once:

```powershell
New-NetFirewallRule -DisplayName "XauOrderPad 8765 (LAN only)" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 `
  -RemoteAddress LocalSubnet -Profile Any
```

`-RemoteAddress LocalSubnet` limits it to devices on your own LAN — never the internet.

### 3. When the phone still cannot connect

Two causes, in order of likelihood. Neither is an app bug, and both look identical from the phone.

| Cause | How to spot it | Fix |
|---|---|---|
| **Windows has your Wi-Fi as a *Public* network.** Inbound is blocked outright, and a Private-profile rule never applies. | `Get-NetConnectionProfile` → `NetworkCategory : Public` | Use `-Profile Any` (as above), or set the network to Private. |
| **Router AP isolation** — the router blocks client↔client traffic on Wi-Fi entirely. | The PC can reach its own LAN IP, but the phone cannot reach *anything* on the PC. | Turn off AP/client isolation on the router, or use a different network. |

A useful discriminator: from *another* device on the Wi-Fi, open
`http://<pc-lan-ip>:8765/api/config`. It is unauthenticated by design, so it should answer
`{"auth_required": true, ...}`. If it hangs, the problem is the network, not the app.

> Testing the PC's own LAN IP **from the PC itself** proves nothing — that traffic never traverses
> the inbound firewall.

The phone app is documented in **[../android/README.md](../android/README.md)**.

## Controls

| Key            | Action            |
|----------------|-------------------|
| Space / Enter  | BUY at market     |
| Backspace      | SELL at market    |
| Esc            | CLOSE ALL         |

(Buttons do the same. Keys only work while the page/tab is focused.)

## Account login / switch / logout (web-driven)

The MT5 desktop terminal must be **installed and running** — the app drives it, it
does not replace it. But the *account* can be logged in from the browser. Click
**ACCOUNT** in the top bar.

### The two ways a session gets established — this is the thing that confuses people

1. **Attach to an already-logged-in terminal.** If you log into MT5 on the desktop
   yourself, the app just attaches — **no password needed**, because MT5 hands over
   the existing session. This is the path all the `analysis/` scripts use
   (`mt5.initialize()` with no arguments).
2. **Log in from the browser** (ACCOUNT panel). Here the password **is** required and
   is sent to MT5 for real.

The trap: the app can look perfectly healthy for weeks on path (1) while the password
saved in the vault has **never once been tested**. The day the terminal is logged out,
path (2) kicks in, the stale password gets used for the first time, and the login is
refused. See `-6: Terminal: Authorization failed` in *Common issues* below.

### Using the panel

- **Log in:** enter the account number (login), **master** password (not the *investor*
  password — that is read-only and is rejected the same way a wrong password is), and
  server (e.g. `Exness-MT5Trial16`), then **LOG IN**. Tick *Remember this account* to
  save it for one-click switching.
- **Switch:** pick a saved account → **Switch**. It logs in using the password held in
  Windows Credential Manager, and **prefills the form** with that profile's
  login/server/label at the same time (never the password — that stays server-side and
  is never sent to the page). So if the stored password is refused, the form is already
  filled in and the cursor lands in the password box: just retype it and hit **LOG IN**.
  With *Remember this account* ticked, that repairs the stored copy and Switch works
  silently from then on.
  Switching is sequential — MT5 allows **one logged-in account at a time**, and it
  switches the *same* terminal the desktop shows.
- **Log out:** **LOG OUT** disconnects and shows a logged-out overlay; trading is
  blocked until you log in again. (MT5 has no true account-logout — the terminal
  keeps its session; the app simply stops driving it.)

**Security:** the server binds to `127.0.0.1` only. Passwords are stored
encrypted in **Windows Credential Manager** (via `keyring`) — never in a plain
file, never logged, and never sent to the browser. The `profiles.json` index holds only
login/server/label. If the credential store is unavailable, saving fails closed (no
plaintext fallback).

**A password is only *verified* when you log in with it.** Logging in via the form with
*Remember* ticked saves it **only after MT5 accepts it**, so that stored copy is
known-good. Credential Manager is a safe, not a bouncer — it will faithfully store and
return a wrong password without complaint.

**Real accounts** are allowed: a red **REAL** banner shows, real logins/switches
require a confirm, and Auto-Test still refuses to run on any non-demo account.

**Open positions on switch/logout:** the switch proceeds and warns you how many
positions remain open on the *previous* account (they are **not** auto-closed —
flatten first if you want them closed). They reappear when you switch back.

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
| **`-6: Terminal: Authorization failed`** on login/Switch | MT5 **rejected the credentials**. The stored password is wrong or stale (see below). Retype the **master** password in the ACCOUNT form with *Remember* ticked. |
| **`logged_out` / `"error": "logged out"`** in `/api/state` | Normal on a fresh start — the worker does **not** auto-attach on boot. Click **ACCOUNT → LOG IN**. |
| Clicking **Switch** seems to do nothing | It isn't dead — the login is being refused. The reason now appears in the ACCOUNT banner and in the log as `account_login_failed`. |
| Dot amber, "trading not allowed" / order says `10027` | Turn on **AutoTrading** (Ctrl+E) in MT5. |
| Browser can't reach the page | Make sure the console window is still running; check the port is **8765**. |
| Port 8765 already in use | Change `PORT` in `config.py` (e.g. 8770) and restart; also update the URL. |
| Wrong/empty symbol | Set `SYMBOL` in `config.py` to your exact name (e.g. `XAUUSDm`). |

### Diagnosing a refused login

`-6` means the **broker** said no — the request reached MT5 fine. Rule the causes out in
this order:

1. **Is the terminal itself logged in?** Run the check below; if it *also* returns `-6`,
   the terminal has no account, so every login now depends on the stored password being
   correct. (This call passes no credentials at all — it only tries to attach.)

   ```powershell
   .\.venv\Scripts\python.exe -c "import MetaTrader5 as m; print(m.initialize(), m.last_error())"
   ```

2. **Master vs investor password.** The investor (read-only) password fails identically.
3. **Server name** must match exactly, e.g. `Exness-MT5Trial16`.
4. **Demo account expired.** Exness archives idle demo accounts — if a password you are
   certain about still gives `-6`, create a fresh demo and log in with it.

The failure is always recorded in the log as `account_login_failed` (with the MT5 code
and message; never the password).

> Always test on an **Exness demo** account first.

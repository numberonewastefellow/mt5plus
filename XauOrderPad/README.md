# XAU Order Pad

A fast, **manual** order-entry panel for **XAUUSD** on an Exness **MT5** account.
Live price in the browser, one-press market orders, one-press close-all. No algo.

```
Browser UI (vanilla JS)  ──WebSocket(price/state)──►
                         ◄──HTTP POST(buy/sell/close)──  FastAPI (127.0.0.1)
                                                          │ single worker thread
                                                          ▼  (official IPC)
                                                     MetaTrader5 terminal ──► Exness
```

## Prerequisites
- **Windows** (the `MetaTrader5` package is Windows-only).
- **MetaTrader 5 terminal** installed and **running**.
- In the terminal: **Tools ▸ Options ▸ Expert Advisors / AutoTrading** enabled, and
  **XAUUSD** visible in Market Watch (the app will also try to select it).
- Python 3.12 (used to build the venv).

**The account can be logged in either way** — this is worth understanding up front:

- **Terminal already logged in** → the app just attaches, no password needed.
- **Terminal logged out** → log in from the browser (**ACCOUNT** panel); the password is
  required and is actually sent to MT5.

The app does **not** auto-attach on boot, so a fresh start always reports
`"error": "logged out"` until you log in — that is expected, not a fault. If a login is
refused with `-6: Terminal: Authorization failed`, see the login troubleshooting in
**[HOW_TO_RUN.md](HOW_TO_RUN.md)**.

> Running it on a **cloud box** instead? See **[DEPLOY_AWS.md](DEPLOY_AWS.md)**. There the app
> launches the terminal itself and always uses the browser-login path above (there is no desktop
> session to attach to) — and you reach the UI over an SSH tunnel.

## Related directories

| Dir | What it is |
|---|---|
| **[`../android/`](../android/README.md)** | The **phone client** for this server. Kotlin + Compose, built in Docker, sideloaded. Same endpoints, plus close-losing / close-profit. |
| **[`testing/`](testing/README.md)** | **Headless API tests.** Runs this exact `server.py` on Linux against a *fake* MetaTrader5, so the endpoints — including `close_where` with a real winner and loser — can be tested with no broker and no Windows. |
| **[`deploy/`](deploy/README.md)** | EC2 deployment scripts (`mt5_ec2.py` + double-clickable `.bat` wrappers). |

## Setup & run
Double-click **`run.bat`** (first run creates the venv and installs deps), or manually:

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe server.py
```

Then open <http://127.0.0.1:8765> (run.bat opens it for you).

`start_server.bat` does the same but first **kills whatever is already holding port 8765** — handy
when a previous run is still up.

### The startup banner — read it, it is telling you something

On boot the server prints exactly which addresses are reachable:

```
XauOrderPad listening
  Local:    http://127.0.0.1:8765
  Network:  http://192.168.0.116:8765     <-- type this into the Android app
  Auth:     token REQUIRED
```

Three states, and the first two are the ones that waste an afternoon:

| Banner says | Means |
|---|---|
| `Network: NOT REACHABLE -- bound to 127.0.0.1` | **A phone cannot reach this server, and no firewall rule will change that.** Uvicorn binds *one* address; loopback is not on your network. Set `XAUORDERPAD_HOST`. This is not a firewall problem. |
| `Auth: *** NONE -- AND THIS SERVER IS ON THE NETWORK ***` | **Every device on the Wi-Fi can now place orders and close your positions.** This process sends real MT5 orders. Set `XAUORDERPAD_TOKEN`. |
| `Auth: token REQUIRED` | Correct. |

### Environment variables

Both are read at startup (`config.py`), and both default to the safe-but-local values:

| Var | Default | Effect |
|---|---|---|
| `XAUORDERPAD_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` makes the server reachable from other devices — **required** for the phone. |
| `XAUORDERPAD_TOKEN` | `""` — **no authentication at all** | Sent by clients as the `x-token` header, and as `?token=` on the `/ws` URL. |

> **`API_TOKEN` defaults to empty, i.e. no auth.** That is harmless on loopback and *dangerous* the
> moment the server is on a network. **If you set `XAUORDERPAD_HOST`, set `XAUORDERPAD_TOKEN` too.**

To reach the pad from a phone on your LAN, see the LAN section of
**[HOW_TO_RUN.md](HOW_TO_RUN.md)** — including the two things that usually block it (a *Public*
Windows network profile, and router AP isolation).

## Controls
| Key        | Action          |
|------------|-----------------|
| Space / Enter | **BUY** at market |
| Backspace  | **SELL** at market |
| Esc        | **CLOSE ALL** positions for the symbol |

Buttons do the same. Keys only fire while the page/tab is focused.
The **Volume / SL / TP** fields feed every order; SL/TP can be entered in
**points** (distance) or absolute **price** (toggle in the form). Leave SL/TP
at 0 for none.

## Safety / accuracy
- Fire buttons are **disabled unless the health dot is green** (terminal connected,
  trading allowed, symbol tradable).
- Every order shows its **retcode + ticket** in the Activity log.
- **Close-all is verified**: it re-checks open positions and reports any remaining.
- Server binds to **127.0.0.1 only** — not reachable from the network.
- A client-side **busy guard** prevents accidental double-fire while a request is in flight.

## Configuration — `config.py`
- `SYMBOL` / `AUTO_RESOLVE_SYMBOL` — set to your exact Exness name (e.g. `XAUUSDm`);
  auto-resolve finds a `XAUUSD*` variant if the exact name is missing.
- `DEFAULT_VOLUME`, `DEFAULT_DEVIATION` (slippage points), `MAGIC`.
- `RESTRICT_CLOSE_TO_MAGIC` — if `True`, close-all only closes this app's own trades.
- `MT5_PATH` / `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` — leave blank to attach to
  the already-running, logged-in terminal (recommended).
- `PORT`, `POLL_HZ`, `API_TOKEN`.

## Test on DEMO first
Point the terminal at an **Exness demo** account, verify buy/sell/close + the
fail-safes (toggle AutoTrading off → buttons disable), then switch to live.

## Optional: single-folder bundle
```bat
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\pyinstaller --onedir --collect-all MetaTrader5 --add-data "static;static" server.py
```
The MT5 terminal is still a separate prerequisite.

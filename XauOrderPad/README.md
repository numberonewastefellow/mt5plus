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
- **MetaTrader 5 terminal** installed and **logged into your Exness account**, running.
- In the terminal: **Tools ▸ Options ▸ Expert Advisors / AutoTrading** enabled, and
  **XAUUSD** visible in Market Watch (the app will also try to select it).
- Python 3.12 (used to build the venv).

## Setup & run
Double-click **`run.bat`** (first run creates the venv and installs deps), or manually:

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe server.py
```

Then open <http://127.0.0.1:8765> (run.bat opens it for you).

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

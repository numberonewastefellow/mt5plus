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

## Common issues

| Symptom | Fix |
|---|---|
| Health dot stays **red**, "terminal offline" | Start MT5 and log in; the worker auto-reconnects. |
| Dot amber, "trading not allowed" / order says `10027` | Turn on **AutoTrading** (Ctrl+E) in MT5. |
| Browser can't reach the page | Make sure the console window is still running; check the port is **8765**. |
| Port 8765 already in use | Change `PORT` in `config.py` (e.g. 8770) and restart; also update the URL. |
| Wrong/empty symbol | Set `SYMBOL` in `config.py` to your exact name (e.g. `XAUUSDm`). |

> Always test on an **Exness demo** account first.

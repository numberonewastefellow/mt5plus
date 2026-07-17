"""Configuration for the XAUUSD manual order pad.

Edit the values below. Nothing here is secret unless you fill in MT5_LOGIN /
MT5_PASSWORD (prefer leaving those blank and using an already-logged-in
terminal). The server binds to 127.0.0.1 unless XAUORDERPAD_HOST says otherwise.
"""

import os

# --- Symbol ---------------------------------------------------------------
# Exness often suffixes symbols (e.g. "XAUUSDm"). Set the exact name your
# terminal shows. If AUTO_RESOLVE_SYMBOL is True and the exact name is not
# found, the worker picks the first symbol whose name starts with SYMBOL_BASE.
SYMBOL = "XAUUSD"
SYMBOL_BASE = "XAUUSD"
AUTO_RESOLVE_SYMBOL = True

# --- Order defaults -------------------------------------------------------
DEFAULT_VOLUME = 0.01        # lots
DEFAULT_DEVIATION = 20       # max slippage in points for market orders
MAGIC = 532026               # tag so close-all only touches this app's trades
SL_TP_MODE = "points"        # default UI mode: "points" or "price"
RESTRICT_CLOSE_TO_MAGIC = False  # True => close-all only closes this app's positions

# --- Auto-strategy (experimental, DEMO-ONLY) ------------------------------
# The volume-spike straddle strategy places its own bracketed orders under a
# SEPARATE magic so they are never confused with manual trades or close-all.
# It is DISABLED by default and refuses to run on a non-demo or netting account.
# All parameters are overridable live from the WebUI Strategy panel.
STRATEGY_MAGIC = 532027
STRATEGY_DEFAULTS = {
    "volume": 0.01,          # lots PER LEG (two legs per straddle)
    "rvol_window": 90,       # bars for the volume baseline (median)
    "rvol_threshold": 8.0,   # spike = current M1 volume >= this x baseline
    "atr_period": 14,        # ATR period for stop sizing
    "sl_atr_mult": 1.0,      # stop distance = sl_atr_mult * ATR
    "tp_r": 4.0,             # take-profit = tp_r * stop distance (let winner run)
    "max_hold_min": 30,      # time-stop: close remaining legs after N minutes
    "cooldown_min": 15,      # min minutes between straddles
    "max_concurrent": 1,     # max straddles open at once
    "max_daily_loss": 200.0, # USD: kill-switch — disable + flatten if breached
    "vol_filter": True,      # only fire when ATR is expanding (volatile regime)
}

# Each engine gets its OWN magic. This is what makes them independent: every
# worker helper filters by magic, so no engine can see -- let alone close --
# another's positions, and the log attributes every deal to exactly one engine.
# Never reuse a number; an old position tagged with a recycled magic would be
# adopted by the wrong engine on restart.
STRATEGY_MAGICS = {
    "straddle": 532027,
    "ladder": 532028,
}

# Crash recovery. On (re)start each engine rebuilds its open book from the BROKER
# -- positions_get(), filtered by magic -- because the broker is the only thing
# that survives a kill -9. It ALWAYS adopts them and resumes managing their exits;
# managing an open position only ever reduces risk, so it needs no human.
#
# Resuming NEW entries is a different matter. An engine that re-armed on every boot
# would, in a crash-restart loop, pyramid forever -- which is how an unattended bot
# does real damage. So new entries resume only if the persisted state is younger
# than this. Anything older is managed but left disarmed, and says so in the UI.
LADDER_RESUME_MAX_AGE_S = 600      # 10 minutes

# --- Trend-Ladder (experimental, DEMO-ONLY) -------------------------------
# Arm a side + trigger price; pyramid into the move; exit on a retrace.
#
# The defaults below are NOT arbitrary -- each one is a direct consequence of a
# measurement in ../analysis/TREND_LADDER_STRATEGY.md. Read that before changing
# them; the short version:
#
#   * The spread is FIXED at 0.24/oz and does not tighten. With no directional
#     edge, expectancy is -1 spread per trade, and NO arrangement of target and
#     stop escapes that (a full target x trail sweep lands every cell on -0.24).
#   * The ladder is a pure MULTIPLIER, not an edge: same trigger/TP/stop, 1
#     position loses $30/ladder and 10 positions lose $105. Hence max_positions
#     defaults to 1 -- pyramiding must be opted into, once an edge is proven.
#   * The only component that can beat the spread is the user's discretionary
#     trigger, and that cannot be backtested. Hence paper=True by default: the
#     engine logs what it WOULD do and places nothing, so the trigger's edge can
#     be measured before a cent is risked.
LADDER_DEFAULTS = {
    "side": "sell",          # "buy" | "sell"
    "trigger": 0.0,          # arm price; 0 = not set (engine will not fire)
    "volume": 0.01,          # lots per position
    "max_positions": 1,      # see above -- >1 multiplies the spread cost
    "entry_mode": "step",    # "step" (spaced by price) | "timer" (N per second)
    "entry_step": 0.30,      # step mode: add only on a new extreme this far on
    "entry_gap_ms": 200,     # timer mode: min ms between entries (200 = 5/sec)
    "target": 1.00,          # $/oz profit per position (1.00 = 1000 points)
    "retrace": 0.30,         # $/oz pullback from the extreme -> close out
    "hard_sl": 3.00,         # $/oz broker-side stop, in case this process dies
    "max_daily_loss": 200.0, # USD kill-switch
    "paper": True,           # log-only; places NO orders. Default ON.
}

# --- Server ---------------------------------------------------------------
# HOST: bind address. Two legitimate values, and one that is never legitimate:
#   127.0.0.1    -> local desktop use (default). Reachable only via an SSH tunnel.
#   100.x.y.z    -> the box's TAILSCALE address, so the Android app can reach it.
#                   Port 8765 stays CLOSED in the EC2 security group; WireGuard is
#                   the only path in. Set via XAUORDERPAD_HOST on the box.
#   0.0.0.0      -> NEVER. That publishes a live-trading API to the internet.
#
# API_TOKEN: shared secret required on every trade-capable endpoint AND on /ws.
# Blank disables the check (fine on loopback; NOT fine once HOST is non-loopback).
# Read from the environment so the secret never lands in a git-tracked file --
# `mt5plus` is a repo, and a committed token is one `git add .` from publication.
HOST = os.environ.get("XAUORDERPAD_HOST", "127.0.0.1")
# From the environment for the same reason as HOST: changing where the server
# listens should never require editing a git-tracked file. The Android client
# follows whatever host:port you type on its Connect screen, so a non-default
# port needs no rebuild -- only this.
PORT = int(os.environ.get("XAUORDERPAD_PORT", "8765"))
POLL_HZ = 15                 # backend MT5 poll + max UI push rate (clients may ask for less)
API_TOKEN = os.environ.get("XAUORDERPAD_TOKEN", "")

# --- Auto-launch browser --------------------------------------------------
# On startup, open the UI in a Chrome/Edge "app-mode" window (no address bar,
# no tabs) so it feels like a native desktop app. Prefers Chrome, falls back to
# Edge.
#
# From the ENVIRONMENT, like HOST/PORT/API_TOKEN above, and for the same reason:
# this value must differ on the EC2 box, and `deploy ship` copies this very file
# from the developer's laptop. Hardcoding it here means the deploy silently
# overwrites the box's setting with the laptop's -- which it did, and the box
# then launched Microsoft Edge on a headless cloud server, once per restart.
# A deploy that clobbers the target's config with the developer's is not a
# deploy step, it is a regression generator.
LAUNCH_BROWSER = os.environ.get("XAUORDERPAD_LAUNCH_BROWSER", "1") != "0"
BROWSER_MODE = "app"         # "app" = standalone window (no address bar); "kiosk" = fullscreen

# --- MT5 terminal connection ---------------------------------------------
# Leave MT5_PATH blank to attach to the running terminal. Leave MT5_LOGIN = 0
# to use whatever account is already logged in (recommended). Fill these in
# only if you want the app to launch/log in the terminal itself.
#
# From the environment for the same reason as LAUNCH_BROWSER. On the box this
# MUST point at the installed terminal, or mt5.initialize() has nothing to
# launch and every login fails with an IPC timeout that looks like a broker
# problem. Blank locally = attach to whatever terminal is already running.
MT5_PATH = os.environ.get("XAUORDERPAD_MT5_PATH", "")   # e.g. r"C:\Program Files\MetaTrader 5\terminal64.exe"
MT5_LOGIN = 0
MT5_PASSWORD = ""
MT5_SERVER = ""

# --- Tick logging (opt-in diagnostics, read-only) -------------------------
# When enabled, the worker appends every raw tick (via copy_ticks_range, riding
# the connection it already owns) to a CSV, so real ticks can be studied offline.
# OFF by default: it must never be a surprise cost on a live/headless box. From
# the environment like the settings above, so turning it on never edits a
# git-tracked file. It places NO orders and touches no trade path -- purely a
# reader bolted onto the existing poll loop, wrapped so a logging error can never
# disrupt trading.
TICKLOG_ENABLED = os.environ.get("XAUORDERPAD_TICKLOG", "") not in ("", "0", "false", "False")
TICKLOG_PATH = os.environ.get(
    "XAUORDERPAD_TICKLOG_PATH",
    os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                 "XauOrderPad", "xau_ticks.csv"),
)

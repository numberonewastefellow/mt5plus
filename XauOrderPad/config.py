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
# Edge. Set LAUNCH_BROWSER = False to disable (e.g. running headless on a box).
LAUNCH_BROWSER = True
BROWSER_MODE = "app"         # "app" = standalone window (no address bar); "kiosk" = fullscreen

# --- MT5 terminal connection ---------------------------------------------
# Leave MT5_PATH blank to attach to the running terminal. Leave MT5_LOGIN = 0
# to use whatever account is already logged in (recommended). Fill these in
# only if you want the app to launch/log in the terminal itself.
MT5_PATH = ""                # e.g. r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
MT5_LOGIN = 0
MT5_PASSWORD = ""
MT5_SERVER = ""

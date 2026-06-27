"""Configuration for the XAUUSD manual order pad.

Edit the values below. Nothing here is secret unless you fill in MT5_LOGIN /
MT5_PASSWORD (prefer leaving those blank and using an already-logged-in
terminal). The server binds to 127.0.0.1 only.
"""

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

# --- Server ---------------------------------------------------------------
HOST = "127.0.0.1"           # do NOT change to 0.0.0.0 (would expose trading!)
PORT = 8765
POLL_HZ = 15                 # backend MT5 poll + UI push rate
API_TOKEN = ""               # optional shared token; blank disables the check

# --- MT5 terminal connection ---------------------------------------------
# Leave MT5_PATH blank to attach to the running terminal. Leave MT5_LOGIN = 0
# to use whatever account is already logged in (recommended). Fill these in
# only if you want the app to launch/log in the terminal itself.
MT5_PATH = ""                # e.g. r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
MT5_LOGIN = 0
MT5_PASSWORD = ""
MT5_SERVER = ""

"""Configuration for the XAUUSD manual order pad.

Edit the values below. Nothing here is secret unless you fill in MT5_LOGIN /
MT5_PASSWORD (prefer leaving those blank and using an already-logged-in
terminal). The server binds to 127.0.0.1 unless XAUORDERPAD_HOST says otherwise.
"""

import os

import instance_paths

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
    "rider": 532029,          # vol-regime trend-rider (paper/suggestion only)
    "sladder": 532030,        # straddle-ladder: straddle a level, then grid the winning side
}

# Vol-Regime Trend-Rider — a PAPER/SUGGESTION engine. It computes the verified
# M5 thrust-follow + trailing-stop signal and surfaces a "trade now" card + paper
# P&L; it places NO orders (the human taps to execute via the normal order path).
# Tunables mirror analysis/eda RiderConfig. See analysis/eda/RIDER.md.
RIDER_DEFAULTS = {
    "thrust_mult": 1.5,       # |body| > mult*ATR(14) on M5 = a thrust
    # HOW sl/trail below are read (named stop_UNITS, not stop_mode: the LADDER
    # already owns `stop_mode` on the shared StrategyReq with completely different
    # values, retrace/floor, and one wire field meaning two things is a trap).
    # "fixed" = $/oz (validated default). "atr" = multiples
    # of the bar's ATR(14), so 1.5 means 1.5*ATR. See RiderConfig.stop_units for the
    # evidence and for why "atr" is NOT the default despite testing better on both
    # halves of the split -- it needs forward-test proof first.
    "stop_units": "fixed",
    "sl": 6.0,                # $/oz initial stop distance   (x ATR when stop_units="atr")
    "trail": 6.0,             # $/oz trailing distance       (x ATR when stop_units="atr")
    "tp": 50.0,               # $/oz far cap (mostly rides the trail)
    "max_hold": 24,           # M5 bars (~2h) time-stop
    "atr_win": 100,           # rolling window for the ATR-median regime gate
    "use_ny_hours": False,    # also restrict signals to NY hours (13-16 UTC)
    "risk_frac": 0.01,        # Kelly-small: fraction of equity risked per trade
    "max_daily_loss": 200.0,  # USD — the kill-switch, and on a real account the ONLY cap
    # Execution. Two independent switches, one per account class, both re-read on
    # EVERY poll so a mid-session account switch changes behaviour immediately.
    # OFF/OFF = the engine only publishes a card and the human taps Place.
    # `auto_real` is in VolRegimeRider.NEVER_RESTORE: it is saved but never restored,
    # so real-money execution cannot resume by itself after a crash or a restart.
    "auto_demo": False,       # place automatically while connected to a DEMO account
    "auto_real": False,       # place automatically while connected to a REAL account
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
# NO LONGER USED, and kept only so the history is legible. Engines are never re-armed
# automatically now: reconcile() adopts and manages an open book but always boots
# DISABLED, and arming is a deliberate human act. The window did not hold -- a restart
# 97 s after arming silently re-armed a live engine, and because the daily-loss latch
# `_killed` was never persisted, the same window also let a killed engine come back armed.
LADDER_RESUME_MAX_AGE_S = 600      # 10 minutes (unused)

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
#
# MEASURED ON A LIVE DEMO RUN, 2026-07-21, account 472200942. The engine was left
# armed with side=buy, trigger=4071.60, max_positions=1, target=1.00, retrace=0.30.
# In ~25 minutes it turned over 68 ladders and 79 closed trades for a net -19.13 at
# 0.01 lots. Two numbers explain the whole result, and both are encoded below:
#
#   * `target` was hit ZERO times. Every single exit was the retrace trail. A target
#     is only reachable if price can run `target` WITHOUT ever pulling back
#     `retrace`; 1.00 behind a 0.30 trail cannot happen. Hence `_param_guard`.
#   * The trigger was never CONSUMED. Once price sat above 4071.60 the engine
#     re-armed on the next 66 ms poll and bought again, forever. Hence `cooldown_s`,
#     `max_ladders_per_day`, and the re-arm latch that requires price to trade back
#     THROUGH the trigger before a new ladder may start.
LADDER_DEFAULTS = {
    "side": "sell",          # "buy" | "sell"
    "trigger": 0.0,          # arm price; 0 = not set (engine will not fire)
    "volume": 0.01,          # lots per position
    "max_positions": 1,      # count cap; 0 = UNCAPPED. >1 multiplies the spread cost
    "max_lots": 0.0,         # total open lots cap; 0 = UNCAPPED. See the note below
    "entry_mode": "step",    # "step" (spaced by price) | "timer" (N per second)
    "entry_step": 0.30,      # step mode: add only on a new extreme this far on
    "entry_gap_ms": 200,     # timer mode: min ms between entries (200 = 5/sec)
    "target": 1.00,          # $/oz profit per position (1.00 = 1000 points)
    # How the ladder gets OUT. Two shapes, and they behave nothing alike:
    #   "retrace" -- trail `retrace` behind the extreme. Tightens as the move runs,
    #                so it exits early and often. This is the validated default.
    #   "floor"   -- one fixed level at `trigger -/+ floor_offset`. The ladder is
    #                given the whole distance from the trigger to work in, and is
    #                flattened only if price returns to where it was armed.
    "stop_mode": "retrace",
    "retrace": 0.30,         # retrace mode: $/oz pullback from the extreme -> close out
    "floor_offset": 0.0,     # floor mode: $/oz BEYOND the trigger before flushing
    # How far in PROFIT a run must get before the retrace trail ARMS. 0 = the classic
    # trail-from-entry (active immediately -- can close BELOW entry on a dip). Set it >= retrace
    # for the standard "trailing stop activates in profit" behaviour (arms at breakeven, so the
    # trail never books a loss; the broker hard_sl covers the downside until then). Set it to the
    # target (with `target` = 0) for "let it run to +X, THEN trail". retrace mode only.
    "trail_activate": 0.0,
    "hard_sl": 3.00,         # $/oz broker-side stop, in case this process dies
    "max_daily_loss": 200.0, # USD kill-switch
    "cooldown_s": 0.0,       # min seconds between ladders; 0 = none
    "max_ladders_per_day": 0,  # 0 = unlimited
    "close_batch": 25,       # max positions closed per poll cycle -- see below
    # Paper mode is now OPT-IN, not the default. It was built as a MEASURING tool --
    # "log the fills the trigger would have got, so its edge can be costed before a cent
    # is risked" -- and it is still exactly that, reachable from Settings -> Strategies on
    # both clients. What it is NOT any more is the safety layer standing between ARM and
    # the broker. Two things already do that job, and they do it better:
    #   * `allows_real = False` on this engine, re-checked EVERY poll in
    #     StrategyBase.evaluate -- the ladder auto-disables on anything but a demo
    #     account, so "live" here can only ever mean demo money.
    #   * `enabled` is never restored across a restart (base.py), so nothing resumes
    #     trading unattended regardless of this flag.
    # With paper defaulting True, ARM silently did nothing on a fresh install and the
    # operator had to hunt for a toggle to make the engine they just armed actually trade.
    # An arm that does not arm is worse than an honest one behind a confirmation.
    "paper": False,          # False = places real orders (demo accounts only). See above.
    # Re-arm policy after a run's stop closes it. FALSE (default) = ONE-SHOT: the engine parks
    # and waits for the operator to SET LEVEL again -- a bare price re-cross does nothing. This
    # is the safe default and matches the operator's workflow (ARM stays on, they set levels).
    # TRUE = AUTO-CONTINUE: after the trail banks a run, keep taking runs WHILE price is still
    # past the trigger, and stop only when price RETURNS to the trigger (then park + alert). It
    # is the trend mode -- in a chop it churns, so it is opt-in and throttled by `cooldown_s`
    # and `max_ladders_per_day`. See strategies/ladder.py `_rearm_gate`.
    "auto_continue": False,
}

# Why `max_lots` matters more than `max_positions`, and why the flush is BATCHED.
#
# Uncapped pyramiding is not dangerous in the way it first looks. With a per-position
# `target`, a FAST run drains itself: early rungs hit their target and close as price
# passes them, so the open book only ever holds the rungs opened within the last
# `target` of movement. The dangerous case is the opposite -- a SLOW GRIND. Timer mode
# adds a rung whenever price beats the previous entry, so a creeping uptrend
# accumulates steadily while nothing ever reaches the target. At 5 entries/sec a grind
# of 1.00 over five minutes is ~1500 rungs = 15 lots, and a floor exit closes all of
# them at once. `max_lots` is the only thing standing in front of that; 0 (uncapped)
# is supported, but a real number is strongly advised on anything live.
#
# Which leads to the flush. `mt5.order_send` is SYNCHRONOUS and runs on the worker
# thread, inside the ~66 ms poll budget. Closing N positions costs N*2 broker round
# trips, so flattening a large book in one cycle would freeze the poll loop for
# seconds -- and that same loop is what feeds prices, the P&L guard, the manual order
# pad, AND the stop that just decided to bail out. So the exit closes at most
# `close_batch` positions per cycle and resumes on the next one: the book still
# empties promptly, but every individual cycle stays inside its budget.
# TREND MODE (stack, park, reload by hand). To pyramid a trend rather than scalp the trigger:
# set `max_positions = 0` (uncapped) WITH a real `max_lots`, and `stop_mode = "floor"` (hold every
# rung through the pullbacks) or `"retrace"` (lock in on a bounce). One ladder then rides the whole
# leg. When it flushes the engine does NOT re-enter itself -- that would be unattended new risk, the
# one thing this codebase refuses. It PARKS and latches `needs_attention`, and both clients alert the
# operator to SET A NEW LEVEL to re-load; re-levelling clears the cooldown so a deliberate act never
# waits behind a machine brake. `would_fire_now` warns when the new level is already crossed (it would
# arm instantly). See analysis/TREND_LADDER_STRATEGY.md §10 -- and note §6d still holds: this is a
# bigger LEVER, not an edge, so a wrong trigger loses faster. max_lots is mandatory when uncapped.
ENTRY_GAP_MS_MIN = 100     # floor on entry_gap_ms: 0 would mean one order_send PER POLL

# --- Straddle-Ladder (experimental, DEMO-ONLY, HEDGING account required) ----
# A DIFFERENT engine from `straddle` and `ladder` -- it owns magic 532030 and does not
# touch either of theirs. The operator sets a LEVEL by hand (the ladder's UX), the engine
# opens a straddle there (one long + one short, both with the SAME sl/tp bracket), and the
# first `tp`-sized move RESOLVES it: the winning leg banks +tp, the losing leg stops -sl,
# net ~0 minus spread. That first move is a pure DIRECTION DETECTOR. From then on the engine
# re-enters ONLY the winning side, one fresh sl/tp bracket at a time, armed `gap` past each
# take-profit -- a one-directional grid that milks a trend.
#
#   level 4050, sl 2, tp 2, gap 1 (worked example):
#     straddle: long 4050 (sl 4048 / tp 4052), short 4050 (sl 4052 / tp 4048)
#     price 4052 -> long tp +2, short sl -2  => direction UP
#     continuation: enter long at 4053 (tp 4055 / sl 4051); tp -> next entry 4056; etc.
#
# UNITS ARE $/oz, exactly like the ladder's `target`: sl=2.0 means $2.00 (4050->4048), it is
# NOT MT5 points. `strategy_place` converts the distance to a price internally.
#
# SAFETY MODEL (operator's chosen behaviour):
#   * On a continuation SL the engine RE-ARMS AND RETRIES THE SAME SIDE -- direction is locked
#     for the whole run; a reversal is NOT auto-detected. That means a true reversal bleeds
#     (buy dip -> SL -> retry) until a CAP halts it, so the caps are load-bearing:
#       - `max_legs`  : continuation entries per run. THE primary safety bound. Hit it -> the
#                       engine PARKS and latches needs_attention (chime); a new SET LEVEL resets.
#       - `max_lots`  : total open-lots ceiling (rarely binds in sequential mode; wired anyway).
#       - `max_daily_loss` : the inherited $200 kill-switch, the backstop.
#   * SEQUENTIAL: at most ONE continuation position open at a time (re-enter only after the
#     prior leg closes). No per-leg time-stop.
# Like every engine but the rider it is `allows_real = False` (auto-disables off demo), and it
# is `needs_hedging = True` because the opening straddle holds both legs at once.
STRADDLE_LADDER_DEFAULTS = {
    "level": 0.0,            # arm price; 0 = not set (engine will not fire)
    "sl": 2.0,               # $/oz stop distance   per leg (4050 -> 4048)
    "tp": 2.0,               # $/oz target distance per leg (4050 -> 4052)
    "gap": 1.0,              # $/oz past a take-profit before the next continuation entry arms
    "volume": 0.01,          # lots per leg
    "max_legs": 10,          # entries per run (initial straddle + each order); 0 = UNCAPPED (discouraged)
    "max_lots": 0.0,         # total open-lots cap; 0 = UNCAPPED
    "cooldown_s": 0.0,       # min seconds between continuation entries; 0 = none
    "max_daily_loss": 200.0, # USD kill-switch (inherited StrategyBase behaviour)
    # What happens AFTER the first straddle resolves:
    #   False (DEFAULT) = SINGLE-LEG trend continuation. The first order is a straddle only to detect
    #     direction; from the 2nd order on it takes ONE leg on the trend side. Each entry arms at the
    #     last SUCCESSFUL take-profit +/- gap -- a TP advances the level, an SL retries the same level
    #     (it does not step off the stop). This rides a trend (+tp per order) instead of netting ~0.
    #   True = WALKING STRADDLE GRID. EVERY entry is a straddle, stepping gap past each winner's TP.
    #     Re-detects direction every step (no locked direction to bleed against), but nets ~0 per step.
    "always_straddle": False,
}

# Minimum seconds between throttled writes of an engine's per-day counters. `state.save`
# is a read-modify-write of a JSON file and the ladder reaches it from `_finish_ladder`,
# on the worker thread, inside the ~66 ms poll budget. A crash can therefore lose up to
# this many seconds of increments -- a ladder or two off the daily count, which is a far
# better trade than stalling the loop that also feeds prices and fills orders. The
# daily-loss LATCH is exempt and written immediately: it fires at most once a day, by
# which point the book is already flat.
LADDER_RUNTIME_SAVE_MIN_S = 10

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

# The ONE account this server is allowed to drive. 0 = unpinned (single-account mode,
# unchanged behaviour: log in to whatever you like).
#
# Set per instance by the launcher from instances.json, where it defaults to the instance
# NAME when that name is an account number. It turns "instance 472200942" from a label
# into an enforced fact: a login for any other account is refused, and if the terminal is
# switched to another account by hand the server goes unhealthy instead of quietly trading
# it. Without this, the name is a comment -- and a comment that lies on a trading screen is
# worse than no comment.
try:
    EXPECT_LOGIN = int(os.environ.get("XAUORDERPAD_EXPECT_LOGIN", "") or 0)
except ValueError:
    EXPECT_LOGIN = 0

# Auto-login on boot: the saved profile_id (e.g. "472200942@Exness-MT5Trial16") this server
# logs into at startup, so a scheduled restart or a Windows-Update reboot comes back LIVE
# instead of logged-out. Empty = no auto-login (the historical default; servers boot logged out).
#
# A login is a SESSION, not a trade -- this never places an order. It was safe to add only once
# the box gained an interactive autologon desktop: the old reason session-active started False was
# that auto-attaching on a headless session-0 box hung ~65s on an IPC timeout. With a real desktop
# the terminal is up, so the attach is instant.
#
# Two guards live in server.py's autologin: it refuses a profile last seen on a REAL account, and
# for a pinned instance the account pin (EXPECT_LOGIN) already refuses any profile but its own.
AUTOLOGIN_PROFILE = os.environ.get("XAUORDERPAD_AUTOLOGIN", "").strip()
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
# Default lands in the INSTANCE's state dir, so two accounts cannot interleave their
# ticks into one CSV and quietly ruin the file for analysis. Still overridable by env.
TICKLOG_PATH = os.environ.get(
    "XAUORDERPAD_TICKLOG_PATH",
    str(instance_paths.state_dir() / "xau_ticks.csv"),
)

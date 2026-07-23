"""Shared contract for automated strategy engines.

Every engine is DEMO-ONLY, owns its own magic number, and owns ZERO MetaTrader5
calls of its own. Everything that touches MT5 goes through the Mt5Worker helpers
(`recent_m1` / `strategy_place` / `strategy_positions` / `strategy_close_ticket` /
`strategy_daily_realized`), all of which take this engine's `MAGIC`. That is what
keeps two engines genuinely independent: neither can see, let alone close, the
other's positions -- and every deal in the log attributes to exactly one engine.

`evaluate()` is only ever called from the worker thread, so it is serialized with
ticks and orders and needs no locking of its own against them.

Subclasses implement:
    ID / NAME              -- identity
    _defaults()            -- dict of tunables
    _params()              -- current tunables, for status()
    _apply(params)         -- coerce+assign incoming params
    _tick(worker, st)      -- the actual strategy, called only when armed and safe
    _extra_status()        -- engine-specific status fields
"""

from __future__ import annotations

import datetime
import logging
import threading

import config
from . import state

log = logging.getLogger("XauOrderPad.strategy")


def _today() -> str:
    """The day the per-strategy limits are counted over.

    LOCAL midnight, deliberately: it is the same boundary `strategy_daily_realized`
    and `/api/history` already use, so the kill-switch and the numbers on screen can
    never disagree about which day it is.
    """
    return datetime.date.today().isoformat()


class StrategyBase:
    ID = "base"
    NAME = "Strategy"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled = False
        self.max_daily_loss = 200.0
        self._state = "disabled"          # disabled|armed|active|managing|killed|waiting:…
        self._error: str | None = None
        self._killed = False              # daily-loss kill-switch, latched
        # WHICH DAY the latch belongs to. A daily limit that forgets the day is not a
        # daily limit: `_killed` lived only in memory, so a restart cleared it and the
        # engine could breach the same day's limit again, and again. Persisted with the
        # latch and restored only when it is still that day -- see `_runtime`.
        self._killed_day: str | None = None
        # Adopted positions but NOT re-armed: manage the exits, open nothing new.
        # Without this flag `evaluate()` returns early on `not enabled` and the
        # adopted book would sit unmanaged -- which is the exact bug reconcile()
        # exists to fix.
        self._managing = False

    @property
    def MAGIC(self) -> int:
        return int(config.STRATEGY_MAGICS[self.ID])

    # ---- control (worker thread, via a queued command) --------------------
    def update(self, params: dict | None, enabled: bool | None) -> dict:
        """Every control change is logged, not just the enable toggle.

        This used to log ONLY when `enabled` moved. So flipping `paper` to False --
        the single change in this whole system that turns a simulator into something
        that spends real money -- left NO record at all. When an engine was later
        found armed and live, there was nothing to say who did it, from where, or
        when: the most dangerous parameter was the one with no audit trail.

        Now a diff of the params is logged on every change, and paper->live gets its
        own WARNING, because "this engine may now place real orders" is not an INFO.
        """
        with self._lock:
            before = self._params()
            if params:
                self._apply(params)
            after = self._params()

            changed = {k: [before.get(k), v] for k, v in after.items()
                       if before.get(k) != v}
            if changed:
                log.info("strategy %s params changed: %s", self.ID,
                         ", ".join(f"{k} {a!r}->{b!r}" for k, (a, b) in changed.items()),
                         extra={"event": "strategy_params_changed",
                                "strategy": self.ID, "changed": changed})

            # Loud, and on its own. Paper mode is the safety; taking it off is the
            # moment this engine starts spending money.
            # ASCII only. The Windows console stream is cp1252, so an em-dash here comes out as
            # a replacement char -- a mangled line in the one log you would actually be reading
            # after something went wrong.
            if changed.get("paper") == [True, False]:
                log.warning("strategy %s: PAPER MODE OFF -- it will place REAL orders",
                            self.ID,
                            extra={"event": "strategy_live_orders_enabled",
                                   "strategy": self.ID, "params": after})

            # Raising the limit is the ONE deliberate act that reopens a killed day.
            #
            # The latch has to be escapable or a single bad morning disables the engine
            # until midnight with no way back. But the escape must be an explicit
            # decision about RISK -- "I accept losing more than X today" -- not a side
            # effect of toggling a switch. So the limit going UP clears it, and nothing
            # else does.
            if (self._killed and self._killed_day == _today()
                    and float(after.get("max_daily_loss") or 0) >
                    float(before.get("max_daily_loss") or 0)):
                log.warning("strategy %s: daily-loss limit raised %.0f -> %.0f, "
                            "clearing today's kill latch", self.ID,
                            float(before.get("max_daily_loss") or 0),
                            float(after.get("max_daily_loss") or 0),
                            extra={"event": "strategy_kill_latch_cleared",
                                   "strategy": self.ID, "day": self._killed_day})
                self._killed = False
                self._killed_day = None

            if enabled is not None:
                # A same-day kill SURVIVES a re-arm. This used to clear the latch
                # unconditionally, so the daily limit was really a per-toggle limit:
                # trip at -200, flick the switch, lose another 200. The engine may be
                # armed, but `evaluate()` keeps it parked in `killed` until the day
                # rolls over or the operator raises the limit above.
                self.enabled = bool(enabled)
                if self.enabled:
                    if self._killed and self._killed_day == _today():
                        self._state = "killed"
                        self._error = (
                            f"daily-loss limit {self.max_daily_loss:.0f} already breached "
                            f"today -- raise the limit to resume, or wait for tomorrow")
                    else:
                        self._killed = False
                        self._killed_day = None
                        self._error = None
                        self._state = "armed"
                else:
                    self._state = "disabled"
                log.info("strategy %s %s", self.ID,
                         "enabled" if self.enabled else "disabled",
                         extra={"event": "strategy_toggle", "strategy": self.ID,
                                "enabled": self.enabled, "paper": after.get("paper"),
                                "killed_today": self._killed, "params": after})
        state.save(self.ID, self.enabled, self._params(), self._runtime())
        return self.status()

    # ---- crash recovery ---------------------------------------------------
    def reconcile(self, worker) -> None:
        """Rebuild this engine from the BROKER after a (re)start or account switch.

        The engine's memory dies with the process; the positions do not. They sit at
        the broker, still open, still carrying risk. So the open book is rebuilt from
        `positions_get()` -- never from anything remembered on disk -- and then handed
        to `_adopt()` for engine-specific reconstruction (a ladder needs its extreme
        back; a straddle needs its legs paired).

        Resume policy, and why it is split in two:

          * ADOPT ALWAYS. Managing an open position only ever REDUCES risk, so it
            never needs a human to authorise it. A position nobody is managing is
            the whole problem this method exists to solve.

          * NEVER RE-ARM. Taking NEW risk unattended is a different matter, and no
            time window makes it safe. This used to resume any engine whose saved
            state was younger than config.LADDER_RESUME_MAX_AGE_S -- and on
            2026-07-21T23:28 it did exactly that: a restart 97 s after the operator
            last touched the panel silently re-armed the rider while it held a 1.0-lot
            position. The window was also the last thing standing between a tripped
            daily-loss kill-switch and a re-armed engine, because `_killed` lives only
            in memory and `state.save` is called solely from `update()`: kill at
            T+4 min, restart at T+8 min, and the engine that had just breached its loss
            limit came back armed with the latch cleared.

            So: arming is now ALWAYS a deliberate human act. An engine boots disabled,
            works the exits of whatever it adopted, and opens nothing until somebody
            enables it. Restarts are cheap; an unattended bot taking fresh risk is not.
        """
        rec = state.load(self.ID)
        # The day's own bookkeeping comes back FIRST, before anything can act on it: the
        # kill latch decides whether this engine may trade at all today, so restoring it
        # after `_apply` would leave a window where it looked clear.
        if rec:
            try:
                self._restore_runtime(rec.get("runtime") or {})
            except Exception:
                log.exception("could not restore runtime state",
                              extra={"event": "strategy_runtime_restore_failed",
                                     "strategy": self.ID})
        if rec and rec.get("params"):
            saved = dict(rec["params"])
            # NEVER_RESTORE: real-money switches boot OFF, always. A restart loop that
            # restored them would resume spending real money with nobody watching --
            # the same failure the RE-ARM ONLY IF FRESH rule below exists to prevent,
            # except a stale-state check cannot help here: the danger is not that the
            # state is old, it is that no human is present to consent to it again.
            dropped = {k: saved.pop(k) for k in self.NEVER_RESTORE if k in saved}
            self._apply(saved)                # restore tuning either way
            if any(dropped.values()):
                log.warning("strategy %s: %s did NOT resume after restart -- re-arm by hand",
                            self.ID, ", ".join(sorted(k for k, v in dropped.items() if v)),
                            extra={"event": "strategy_not_restored", "strategy": self.ID,
                                   "dropped": dropped})

        try:
            positions = self.positions(worker)
        except Exception:
            log.exception("reconcile: could not read positions",
                          extra={"event": "strategy_reconcile_failed",
                                 "strategy": self.ID})
            return

        adopted = 0
        if not positions:
            # The BROKER says this engine holds nothing, and the broker is the authority.
            # `_adopt` runs only when there ARE positions, so without this an engine-local
            # handle left over from before the restart survives a reconcile that just
            # PROVED the position is gone -- the rider was reporting `live_ticket` for a
            # trade closed eight hours earlier, and would have refused to open a new one
            # until something cleared it.
            self._on_flat()
        if positions:
            try:
                adopted = int(self._adopt(worker, positions) or 0)
            except Exception:
                log.exception("reconcile: adopt failed",
                              extra={"event": "strategy_reconcile_failed",
                                     "strategy": self.ID})

        age = state.age_s(rec)
        was_on = bool(rec and rec.get("enabled"))

        # Disabled, always. Adopted positions are still managed -- that reduces risk --
        # but nothing new is opened until a human arms it.
        self.enabled = False
        self._managing = bool(adopted)       # keep working the exits, open nothing new
        self._state = "managing" if adopted else "disabled"
        if adopted and was_on:
            # Loud, and in the UI -- not just the log. Somebody has to know that the
            # book is being babysat but nothing new will be opened.
            self._error = (
                f"resumed management of {adopted} open position(s) after a restart "
                f"— re-enable to take new entries")
        elif adopted:
            self._error = (f"adopted {adopted} orphaned position(s) from the broker "
                           f"— managing exits only")
        elif was_on:
            # It was running when the process died and it is NOT running now. With a flat
            # book nothing else would say so, and an operator who believes an engine is
            # armed when it is not is exactly as badly informed as the reverse.
            self._error = "was enabled before the restart — NOT re-armed; enable it by hand"

        # A live kill latch OUTRANKS everything above. Both are true -- the engine is
        # disabled AND it breached its limit today -- but only one of them explains why
        # re-enabling will not stick, and it is not "disabled". `_restore_runtime` sets
        # this before `_apply`, and the generic status assignment above would otherwise
        # paint straight over the single most important thing on the panel.
        if self._killed and self._killed_day == _today():
            self._state = "killed"
            self._error = (f"daily-loss limit {self.max_daily_loss:.0f} was breached "
                           f"earlier today -- raise the limit to resume"
                           + (f"; still managing {adopted} open position(s)"
                              if adopted else ""))

        log.info("strategy %s reconciled", self.ID,
                 extra={"event": "strategy_reconciled", "strategy": self.ID,
                        "adopted": adopted, "state_age_s": None if age == float("inf") else int(age),
                        "was_enabled": was_on, "rearmed": False,
                        "detail": self._adopt_detail()})

    def status(self) -> dict:
        s = {
            "id": self.ID,
            "name": self.NAME,
            "magic": self.MAGIC,
            "enabled": self.enabled,
            "state": self._state,
            "error": self._error,
            "killed": self._killed,
            "params": self._params(),
        }
        s.update(self._extra_status())
        return s

    # ---- main loop hook (worker thread only) ------------------------------
    @property
    def can_enter(self) -> bool:
        """May this engine open NEW positions? Managing-only engines may not."""
        return self.enabled and not self._managing

    def evaluate(self, worker, st: dict) -> None:
        """Gates that every engine must pass, then hands off to `_tick`.

        Runs when armed OR when merely MANAGING an adopted book -- an engine holding
        real positions must keep working its exits even though it will not open
        anything new. Gating this on `enabled` alone was the bug: after a stale-state
        restart the positions would have been adopted and then ignored."""
        if not (self.enabled or self._managing):
            self._state = "disabled"
            return

        acc = st.get("account") or {}
        # Refuse and auto-disable on anything but a demo account. This is the
        # gate that survives a mid-session account switch: it is re-checked on
        # EVERY poll, not once at enable time.
        #
        # An engine may opt OUT of this, but only by declaring BOTH `allows_real`
        # (a property of the engine's code -- it has been built to run real money)
        # and `wants_real` (a property of the operator's live choice -- they turned
        # a named toggle on). Two independent conditions, because either one alone
        # is an accident waiting to happen: shipping `allows_real` should not arm
        # anything, and a toggle should not be able to arm an engine that was never
        # written for it. Default is False/False, so ladder and straddle keep the
        # absolute demo-only behaviour they have always had.
        if not acc.get("is_demo", False):
            if not (self.allows_real and self.wants_real):
                self._disable_with("refused: connected account is NOT a demo account")
                return
        if self.needs_hedging and int(acc.get("margin_mode", -1)) != 2:
            # ASCII only -- see the note in update(). This refusal is now reachable for
            # the ladder (needs_hedging went True when rungs stopped being one-at-a-time),
            # so it is a line an operator will actually read on the console.
            self._disable_with("refused: account is netting -- this strategy needs hedging")
            return
        if self._killed:
            # A latch belongs to ONE day. Without this it would only ever clear on a
            # restart or a re-arm, so an engine killed on Monday would still be dead on
            # Tuesday -- and the operator would be waiting on a limit that had already
            # reset everywhere else (`strategy_daily_realized` counts from local
            # midnight). It stays disabled either way; arming is still a human act.
            if self._killed_day and self._killed_day != _today():
                log.info("strategy %s: daily-loss latch expired with the day", self.ID,
                         extra={"event": "strategy_kill_latch_expired",
                                "strategy": self.ID, "was_day": self._killed_day})
                self._killed = False
                self._killed_day = None
                self._error = None
                self._state = "disabled"
                return
            self._state = "killed"
            return
        if not st.get("healthy"):
            self._state = "waiting: terminal not healthy / market closed"
            return

        if self._check_kill(worker):
            return
        self._tick(worker, st)

    # ---- shared safety ----------------------------------------------------
    needs_hedging = False

    # Has this engine been BUILT to run on a real account? A code property, set by
    # the class, never by a request. False here means the demo gate in evaluate()
    # is absolute no matter what params arrive.
    allows_real = False

    # Params that are saved but deliberately NOT restored on (re)start -- see
    # reconcile(). For anything that spends real money, "off until a human says so"
    # is the only safe boot state.
    NEVER_RESTORE: frozenset = frozenset()

    @property
    def wants_real(self) -> bool:
        """Has the OPERATOR asked for real-account execution, right now? Engines that
        offer it override this with their own toggle."""
        return False

    def _disable_with(self, msg: str) -> None:
        was_managing = self._managing
        if self.enabled or self._managing or self._error != msg:
            log.warning("strategy %s auto-disabled: %s", self.ID, msg,
                        extra={"event": "strategy_auto_disabled",
                               "strategy": self.ID, "reason": msg,
                               "abandoned_book": was_managing})
        self.enabled = False
        # Managing must stop too. These gates fire on "this is not the demo account"
        # and "this account cannot hedge" -- i.e. we are looking at a DIFFERENT book
        # than the one we adopted. Continuing to "manage" positions on it would mean
        # sending closes against someone else's trades.
        self._managing = False
        # ...but say so. Dropping management is silent otherwise: the operator sees only
        # "refused: connected account is NOT a demo account" and has no way to know that
        # a position they believe is being trailed now has nothing but its broker-side
        # stop behind it. The engine cannot safely close it (wrong account) -- a human
        # has to decide -- so the least it can do is state the situation plainly.
        self._error = (msg + " — WARNING: open position(s) are no longer being managed; "
                             "their broker-side stop is the only protection left"
                       ) if was_managing else msg
        self._state = "disabled"

    def positions(self, worker) -> list:
        return worker.strategy_positions(self.MAGIC)

    def _check_kill(self, worker) -> bool:
        """Realized + floating below the limit -> flatten this engine's book and
        latch off. Floating is included deliberately: waiting for a loss to be
        REALIZED before reacting to it is how a kill-switch arrives too late.

        Throttled to ~1 Hz. `strategy_daily_realized` pulls the WHOLE day's deal
        history over MT5 IPC (`history_deals_get`) -- mt5_worker itself labels that
        "heavy, on-demand only" -- and this ran on every poll, for every enabled
        engine: 15x/sec each, growing all day as deals accumulate. This is a
        DAILY-loss guard; the extra 14 checks per second bought nothing but latency
        on the same thread that has to fill orders. Same idiom, and the same reason,
        as the account-stats throttle in `Mt5Worker._poll_state`.
        """
        if worker.poll_count % max(1, config.POLL_HZ) != 1:
            return False
        try:
            realized = float(worker.strategy_daily_realized(self.MAGIC))
            floating = sum(float(getattr(p, "profit", 0.0))
                           for p in self.positions(worker))
        except Exception:
            return False
        if (realized + floating) <= -abs(self.max_daily_loss):
            log.error("strategy %s daily-loss kill-switch tripped", self.ID,
                      extra={"event": "strategy_kill_switch", "strategy": self.ID,
                             "realized": realized, "floating": floating,
                             "limit": self.max_daily_loss})
            for p in list(self.positions(worker)):
                worker.strategy_close_ticket(self.MAGIC, int(p.ticket))
            self._on_killed()
            self._killed = True
            self._killed_day = _today()
            self.enabled = False
            self._state = "killed"
            self._error = (f"daily loss {realized + floating:.2f} <= "
                           f"-{self.max_daily_loss:.0f} -- auto-disabled for today")
            # Persist IMMEDIATELY. This is the one moment the latch is worth anything,
            # and the process may not survive to the next control change -- that is
            # exactly how it was lost before. One small write, once a day at most, and
            # never on the hot path: by here the book is already flat.
            state.save(self.ID, self.enabled, self._params(), self._runtime())
            return True
        return False

    # ---- per-day runtime state (persisted; see strategies/state.py) --------
    def _runtime(self) -> dict:
        """Bookkeeping that must OUTLIVE the process but cannot be read back from the
        broker. Subclasses extend it; they must call super() and merge.

        The test for belonging here is simple: could `positions_get` or
        `history_deals_get` reconstruct it? Realized P&L can, so it is not here.
        "This engine already breached its limit today" cannot -- nothing at the broker
        records it -- so it is.
        """
        return {"killed": bool(self._killed), "killed_day": self._killed_day}

    def _restore_runtime(self, rt: dict) -> None:
        """Restore only what still applies TODAY.

        A latch from yesterday is not a latch; letting it survive the date boundary
        would disable an engine forever after one bad session. So the day is stored
        with it and checked here, and anything older is simply dropped.
        """
        if not rt:
            return
        day = rt.get("killed_day")
        if rt.get("killed") and day == _today():
            self._killed = True
            self._killed_day = day
            self._state = "killed"
            self._error = (f"daily-loss limit {self.max_daily_loss:.0f} was breached "
                           f"earlier today -- raise the limit to resume")
            log.warning("strategy %s restored TODAY's daily-loss kill latch", self.ID,
                        extra={"event": "strategy_kill_latch_restored",
                               "strategy": self.ID, "day": day})

    # ---- subclass hooks ---------------------------------------------------
    def _defaults(self) -> dict:
        raise NotImplementedError

    def _params(self) -> dict:
        raise NotImplementedError

    def _apply(self, params: dict) -> None:
        raise NotImplementedError

    def _tick(self, worker, st: dict) -> None:
        raise NotImplementedError

    def _extra_status(self) -> dict:
        return {}

    def _on_killed(self) -> None:
        """Drop engine-local bookkeeping after the kill-switch flattened us."""

    def _on_flat(self) -> None:
        """The broker reports NO positions for this engine. Drop any local handle.

        The counterpart to `_adopt`: that one runs only when positions exist, so an
        engine that caches a ticket needs somewhere to hear "you hold nothing" too.
        Default is a no-op -- ladder and straddle rebuild their book from `positions`
        every reconcile anyway and have nothing to forget."""

    def _adopt(self, worker, positions: list) -> int:
        """Rebuild engine-local state from the broker's OPEN POSITIONS.

        Called by reconcile() on boot and after an account switch. `positions` are
        already filtered to this engine's magic. Return how many were adopted.

        Whatever cannot be read off a position must be RECONSTRUCTED, not guessed --
        and if it cannot be reconstructed, say so in the log. A silently wrong stop
        basis is worse than an obviously missing one."""
        return 0

    def _adopt_detail(self) -> dict:
        """Anything worth putting in the strategy_reconciled log line."""
        return {}

    def _clear_managing_if_flat(self, worker) -> None:
        """Managing ends when the adopted book empties -- otherwise the engine would
        stay in `managing` forever, blocking a clean `disabled` state."""
        if self._managing and not self.positions(worker):
            self._managing = False
            self._state = "disabled"
            self._error = None
            log.info("strategy %s finished managing its adopted book", self.ID,
                     extra={"event": "strategy_managing_done", "strategy": self.ID})

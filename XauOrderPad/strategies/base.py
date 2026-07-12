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

import logging
import threading

import config

log = logging.getLogger("XauOrderPad.strategy")


class StrategyBase:
    ID = "base"
    NAME = "Strategy"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled = False
        self.max_daily_loss = 200.0
        self._state = "disabled"          # disabled|armed|active|killed|waiting:…
        self._error: str | None = None
        self._killed = False              # daily-loss kill-switch, latched

    @property
    def MAGIC(self) -> int:
        return int(config.STRATEGY_MAGICS[self.ID])

    # ---- control (worker thread, via a queued command) --------------------
    def update(self, params: dict | None, enabled: bool | None) -> dict:
        with self._lock:
            if params:
                self._apply(params)
            if enabled is not None:
                self.enabled = bool(enabled)
                if self.enabled:
                    self._killed = False      # re-arming clears a prior kill
                    self._error = None
                    self._state = "armed"
                else:
                    self._state = "disabled"
                log.info("strategy %s %s", self.ID,
                         "enabled" if self.enabled else "disabled",
                         extra={"event": "strategy_toggle", "strategy": self.ID,
                                "enabled": self.enabled, "params": self._params()})
        return self.status()

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
    def evaluate(self, worker, st: dict) -> None:
        """Gates that every engine must pass, then hands off to `_tick`."""
        if not self.enabled:
            self._state = "disabled"
            return

        acc = st.get("account") or {}
        # Refuse and auto-disable on anything but a demo account. This is the
        # gate that survives a mid-session account switch: it is re-checked on
        # EVERY poll, not once at enable time.
        if not acc.get("is_demo", False):
            self._disable_with("refused: connected account is NOT a demo account")
            return
        if self.needs_hedging and int(acc.get("margin_mode", -1)) != 2:
            self._disable_with("refused: account is netting — this strategy needs hedging")
            return
        if self._killed:
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

    def _disable_with(self, msg: str) -> None:
        if self.enabled or self._error != msg:
            log.warning("strategy %s auto-disabled: %s", self.ID, msg,
                        extra={"event": "strategy_auto_disabled",
                               "strategy": self.ID, "reason": msg})
        self.enabled = False
        self._error = msg
        self._state = "disabled"

    def positions(self, worker) -> list:
        return worker.strategy_positions(self.MAGIC)

    def _check_kill(self, worker) -> bool:
        """Realized + floating below the limit -> flatten this engine's book and
        latch off. Floating is included deliberately: waiting for a loss to be
        REALIZED before reacting to it is how a kill-switch arrives too late."""
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
            self.enabled = False
            self._state = "killed"
            self._error = (f"daily loss {realized + floating:.2f} <= "
                           f"-{self.max_daily_loss:.0f} — auto-disabled")
            return True
        return False

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

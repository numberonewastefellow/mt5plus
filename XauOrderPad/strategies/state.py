"""Persisted CONTROL state for the strategy engines.

Stores, per engine: `{enabled, params, ts}` in
`%LOCALAPPDATA%\\XauOrderPad\\strategies.json`.

── What is deliberately NOT in here ──

**Tickets and positions.** They live at the broker, and the broker is the only thing
that survives a `kill -9`. An engine that trusted a remembered ticket list from disk
would, after a crash, believe it holds positions the broker has already closed on SL
-- and would happily "manage" them, or worse, refuse to open new ones because its
phantom book looked full. So on every boot the open book is rebuilt from
`positions_get()` (see StrategyBase.reconcile), and this file only answers a
narrower question: *was the operator running this engine, with what settings, and
how long ago?*

**Secrets.** There are none here. The API token and the broker password live in the
OS credential vault (see accounts.py); this file is safe to read, and holds nothing
worth stealing.

`ts` is what makes an unattended restart safe: a crash-restart loop that re-armed on
every boot would pyramid forever, so the caller only resumes NEW entries when this
state is recent (config.LADDER_RESUME_MAX_AGE_S). Anything older is adopted for
management only -- see StrategyBase.reconcile.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("XauOrderPad.strategy")


def _path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "XauOrderPad"
    d.mkdir(parents=True, exist_ok=True)
    return d / "strategies.json"


def load_all() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        # A corrupt file must not stop the server from booting -- and it must not
        # silently resurrect a half-parsed strategy either. Treat it as absent.
        log.exception("strategies.json unreadable; treating as empty",
                      extra={"event": "strategy_state_unreadable"})
        return {}


def load(sid: str) -> dict | None:
    return load_all().get(sid)


def save(sid: str, enabled: bool, params: dict) -> None:
    """Best-effort. A failure to persist must never break a live toggle: the engine
    is already running with the new setting in memory, and refusing the toggle over
    a disk error would be the worse outcome."""
    try:
        all_ = load_all()
        all_[sid] = {"enabled": bool(enabled), "params": params, "ts": time.time()}
        _path().write_text(json.dumps(all_, indent=2), "utf-8")
    except Exception:
        log.exception("could not persist strategy state",
                      extra={"event": "strategy_state_save_failed", "strategy": sid})


def age_s(rec: dict | None) -> float:
    """Seconds since this state was written. `inf` when unknown, so an unstamped or
    missing record can never be mistaken for a fresh one."""
    if not rec or not rec.get("ts"):
        return float("inf")
    return max(0.0, time.time() - float(rec["ts"]))

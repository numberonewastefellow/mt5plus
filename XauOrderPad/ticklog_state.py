"""Persisted ON/OFF state for opt-in tick logging.

Stores a single fact -- `{enabled, ts}` -- in `%LOCALAPPDATA%\\XauOrderPad\\ticklog.json`,
so the operator's choice survives a server restart. Without this, tick logging was an
env-var-only flag (`config.TICKLOG_ENABLED`) that silently reverted every time the server
came back up.

Mirrors `strategies/state.py`: best-effort writes, a corrupt/missing file is treated as
"no preference" (the caller then falls back to the env-var seed). No secrets, nothing worth
stealing -- just a boolean and a timestamp.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("XauOrderPad.worker")


def _path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "XauOrderPad"
    d.mkdir(parents=True, exist_ok=True)
    return d / "ticklog.json"


def load() -> dict:
    """The persisted record, or `{}` when absent/corrupt (never raises).

    `{}` deliberately carries no `enabled` key, so the caller can tell "never chosen"
    (fall back to the env default) apart from an explicit `{"enabled": false}`."""
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        log.exception("ticklog.json unreadable; treating as empty",
                      extra={"event": "ticklog_state_unreadable"})
        return {}


def save(enabled: bool) -> None:
    """Best-effort persist. A disk failure must never break a live toggle -- the worker
    is already logging (or not) with the new setting in memory."""
    try:
        _path().write_text(
            json.dumps({"enabled": bool(enabled), "ts": time.time()}, indent=2), "utf-8")
    except Exception:
        log.exception("could not persist ticklog state",
                      extra={"event": "ticklog_state_save_failed"})

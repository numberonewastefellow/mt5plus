"""Ambient identity stamped onto every log line: which instance, which account.

Why
---
With several servers on one machine, "which account was this?" must be answerable
from a single log line -- not by cross-referencing the file's name, the pid, or
the order of events. A close-all recorded without its account number is not an
audit trail, it is a puzzle.

The account cannot go in the FILENAME: the server boots logged **out** by design
(`Mt5Worker._session_active` starts False), so at the moment the log file is
opened there is no account. It also changes when the operator switches accounts
mid-session. So it is attached per RECORD instead, which is correct in both cases.

How
---
`JsonlFormatter` already merges every non-internal attribute of the LogRecord into
the JSON object, so a `logging.Filter` that sets attributes is all that is needed
-- no formatter change. This is the same mechanism as `server._RedactToken`.

Concurrency
-----------
`_ctx` is REPLACED wholesale, never mutated in place. Rebinding a module global is
atomic under the GIL, so the 15 Hz worker thread can publish a new identity while
the event loop reads it, with no lock on the hot path and no chance of a reader
seeing a half-updated dict.
"""

from __future__ import annotations

import logging
from typing import Any

# Every key here appears on EVERY log line. Keep it small: this is multiplied by
# the whole log volume.
_ctx: dict[str, Any] = {
    "instance": "",
    "account": None,       # MT5 login, e.g. 472200942
    "acct_server": None,   # broker server, e.g. "Exness-MT5Trial16"
    "is_demo": None,       # so a REAL account in a log is obvious at a glance
}


def set_instance(name: str) -> None:
    """Called once at startup, before the worker exists."""
    global _ctx
    _ctx = {**_ctx, "instance": name or ""}


def set_account(login, server, is_demo) -> None:
    """Publish the live account identity. Cheap enough to call every poll.

    Called from the worker's poll loop off the `account_info()` it already reads,
    so the stamp self-heals if the terminal is switched underneath the server
    rather than drifting until the next explicit login.
    """
    global _ctx
    if (_ctx["account"] == login and _ctx["acct_server"] == server
            and _ctx["is_demo"] == is_demo):
        return                      # unchanged: skip the rebind entirely
    _ctx = {**_ctx, "account": login, "acct_server": server, "is_demo": is_demo}


def clear_account() -> None:
    """Logged out -- lines revert to `"account": null` rather than keeping a stale one."""
    global _ctx
    _ctx = {**_ctx, "account": None, "acct_server": None, "is_demo": None}


def current() -> dict[str, Any]:
    return dict(_ctx)


class ContextFilter(logging.Filter):
    """Stamp the ambient identity onto each record.

    `setattr` only when the attribute is ABSENT, so an explicit
    `extra={"is_demo": ...}` at a call site always wins -- the specific fact beats
    the ambient one (see `account_snapshot` in mt5_worker).
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        for key, value in _ctx.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True

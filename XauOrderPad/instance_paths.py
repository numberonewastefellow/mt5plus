"""Per-instance filesystem namespacing, so N servers can share one machine.

Why this exists
---------------
Several pieces of this app write to a path that is **one per machine**:
`strategies.json`, `ticklog.json`, the tick CSV, the daily JSONL log, and the
Chrome app-window profile. With a single server that is correct and simple. The
moment two servers run side by side -- one terminal and one account each -- those
shared paths stop being a tidiness problem and become a trading hazard:

  * `strategies.json` is a read-modify-write with no cross-process lock. Instance
    A persisting `ladder.enabled=true` is read by instance B on its next boot,
    which then resumes that engine against **B's account**. Within
    `config.LADDER_RESUME_MAX_AGE_S` it re-arms NEW entries -- an unattended
    engine trading an account nobody pointed it at.
  * The browser-profile directory is matched by `browser_launch.kill_existing_
    app_windows()`, so starting instance B would close instance A's UI window.
  * N processes appending to one JSONL log, each running its own midnight
    rollover and prune over the other's files.

So every such path is qualified by the instance name.

The default is the empty instance
---------------------------------
`XAUORDERPAD_INSTANCE` unset (or empty) means single-instance mode and every path
resolves to **exactly what it was before this module existed** -- same directory,
same filenames. That is deliberate: the multi-account work must not migrate the
state of an existing single-server install, silently orphaning its saved strategy
settings.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

APP = "XauOrderPad"

# The name is concatenated into a filesystem path and into a log FILENAME, so it is
# restricted rather than sanitised. Silently rewriting a bad name is the wrong move:
# two instances whose names both normalise to the same thing would share state again
# -- the exact failure this module exists to prevent -- while looking distinct in the
# launcher. Reject instead, at import time, before any account is logged in.
_VALID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def is_valid_name(name: str) -> bool:
    """Is `name` usable as an instance id?

    Shared with instances.py so the launcher rejects a bad name in the config file
    at load time, rather than letting the server discover it at startup -- same rule,
    one definition.
    """
    name = (name or "").strip()
    return bool(name) and bool(_VALID.match(name)) and name not in (".", "..")


def instance_name() -> str:
    """This server's instance id, or "" for classic single-instance mode.

    Raises ValueError on a name that could escape or collide -- `..`, a path
    separator, a drive letter, anything outside [A-Za-z0-9._-].
    """
    raw = (os.environ.get("XAUORDERPAD_INSTANCE") or "").strip()
    if not raw:
        return ""
    if not is_valid_name(raw):
        raise ValueError(
            f"XAUORDERPAD_INSTANCE={raw!r} is not a valid instance name. "
            "Use 1-32 chars of A-Z a-z 0-9 . _ - starting alphanumeric "
            "(it becomes a directory and a log filename)."
        )
    return raw


def state_dir() -> Path:
    """Directory for this instance's mutable state, created if absent.

    Empty instance -> %LOCALAPPDATA%\\XauOrderPad  (unchanged)
    Named instance -> %LOCALAPPDATA%\\XauOrderPad\\<name>
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / APP
    name = instance_name()
    if name:
        d = d / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_name() -> str:
    """Daily-log FILENAME prefix. Not the logger namespace -- see logger_setup.

    Empty instance -> "XauOrderPad"      -> XauOrderPad-2026-07-21.log
    Named instance -> "XauOrderPad-a1"   -> XauOrderPad-a1-2026-07-21.log
    """
    name = instance_name()
    return f"{APP}-{name}" if name else APP


def log_dir() -> Path:
    """Directory for the daily JSONL log.

    Empty instance -> ~/Documents/XauOrderPad          (unchanged)
    Named instance -> ~/Documents/XauOrderPad/<name>

    A folder per instance rather than a folder per DATE: the date is already in the
    filename, and the handler's midnight rollover + retention prune both operate on
    one directory (`log_dir.glob(f"{app_name}-*")`). Date-foldering would fragment a
    single account's history across N directories and break that prune's reach.

    Not created here -- `setup_logging` mkdirs it.
    """
    d = Path.home() / "Documents" / APP
    name = instance_name()
    return d / name if name else d


def shared_dir() -> Path:
    """Machine-wide state SHARED by every instance, created if absent.

    Deliberately NOT under state_dir(): the cross-instance account lock only works
    if all instances agree on one directory. Always %LOCALAPPDATA%\\XauOrderPad,
    never the per-instance subfolder.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / APP
    d.mkdir(parents=True, exist_ok=True)
    return d

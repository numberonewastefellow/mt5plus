"""Cross-process exclusivity locks, held by the OPERATING SYSTEM.

What this prevents
------------------
Two servers driving the SAME MT5 account. That is not hypothetical -- it was
measured on this machine during the multi-account bring-up: the live server was
attached to `C:\\Program Files\\MetaTrader 5` (account 472200942) while a second
Python process attached to the very same terminal and read the same account,
concurrently and without error. The `MetaTrader5` package enforces no
exclusivity whatsoever.

Two servers on one account means two independent close-all paths, two P&L guards
and two strategy engines operating on one book, each blind to the other. So the
application has to supply the exclusivity the platform does not.

Why an OS lock rather than a heartbeat/PID file
-----------------------------------------------
The obvious design -- write `{pid, timestamp}` to a file and treat it as stale
after N seconds -- needs a staleness rule, and every such rule is a guess. Too
short and a slow broker login gets its lock stolen mid-trade; too long and a
crashed server blocks its own account for minutes. Checking liveness by PID is
worse: PIDs are recycled, so "is 1040 alive?" can answer yes about a completely
different program.

A byte-range lock has none of that. The kernel owns it and drops it when the
handle closes -- which happens on exit, on `kill -9`, and on a power cut. There
is no stale state to detect, no clock to trust, and no cleanup path that can be
skipped. The JSON in the file is *diagnostics only* (so a rejection can name who
holds the lock); the lock itself is the truth.

Layout
------
`%LOCALAPPDATA%\\XauOrderPad\\locks\\<login>-<sha1(profile_id)[:8]>.lock`

SHARED across instances by construction -- `instance_paths.shared_dir()`, never
`state_dir()`. A per-instance lock directory would make every instance succeed
in locking its own private copy, i.e. a lock that always passes.

The lock is taken on a byte at a HIGH offset while the holder JSON lives at
offset 0. Windows byte-range locks are mandatory, not advisory: locking offset 0
would make the *rejected* process unable to read the very metadata it needs to
tell the user who holds the account.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import instance_paths

log = logging.getLogger("XauOrderPad.lock")

# Offset of the locked byte. Far past any metadata we write, so the two never
# overlap. The file stays sparse -- nothing is written at this offset.
_LOCK_OFFSET = 1 << 30
_IS_WINDOWS = sys.platform == "win32"


class LockHeld(Exception):
    """Someone else holds this lock. A genuine duplicate -- always fail closed.

    `holder` is the last-written metadata dict, or {} when it could not be read.
    """

    def __init__(self, holder: dict[str, Any] | None = None) -> None:
        self.holder = holder or {}
        super().__init__(self.describe())

    def describe(self) -> str:
        h = self.holder
        if not h:
            return "already in use by another XauOrderPad server on this machine"
        who = h.get("instance") or "the default instance"
        bits = [f"already in use by instance '{who}'"]
        if h.get("pid"):
            bits.append(f"pid {h['pid']}")
        if h.get("port"):
            bits.append(f"port {h['port']}")
        if h.get("since"):
            bits.append(f"since {h['since']}")
        return f"{bits[0]} ({', '.join(bits[1:])})" if len(bits) > 1 else bits[0]


class LockUnavailable(Exception):
    """The lock MECHANISM failed -- disk, permissions, antivirus holding the file.

    Deliberately distinct from LockHeld. Policy is to log this loudly and CONTINUE:
    a filesystem hiccup must not make a single-account desk untradeable. A real
    duplicate still raises LockHeld and is still refused.
    """


class FileLock:
    """A non-blocking exclusive lock on one file, held for the process lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, meta: dict[str, Any] | None = None) -> None:
        """Take the lock, or raise LockHeld / LockUnavailable.

        Never blocks: the trade path must get an answer, not a hang.
        """
        if self._fd is not None:
            return                                  # already ours; idempotent

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            # Could not even open the file -> infrastructure, not contention.
            raise LockUnavailable(f"cannot open {self.path}: {exc}") from exc

        try:
            _lock_byte(fd)
        except OSError:
            # The lock call itself failed. In practice this means contention, and
            # that is the direction to fail safely in: refuse the login rather than
            # risk a second server on one account.
            os.close(fd)
            raise LockHeld(read_holder(self.path))
        except Exception as exc:                    # noqa: BLE001 - locking API absent etc.
            os.close(fd)
            raise LockUnavailable(f"locking {self.path} failed: {exc}") from exc

        self._fd = fd
        self._write_meta(meta or {})

    def _write_meta(self, meta: dict[str, Any]) -> None:
        """Best-effort holder info at offset 0. Never fails the acquire.

        The lock is already ours at this point; failing here would release a
        perfectly good lock over a cosmetic write.
        """
        if self._fd is None:
            return
        record = {**meta, "pid": os.getpid(),
                  "since": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            blob = json.dumps(record).encode("utf-8") + b"\n"
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, blob.ljust(512, b" "))   # pad: overwrite a longer previous record
            os.fsync(self._fd)
        except OSError as exc:
            log.warning("could not write lock metadata",
                        extra={"event": "lock_meta_write_failed",
                               "path": str(self.path), "error": str(exc)})

    def release(self) -> None:
        """Drop the lock. Safe to call when not held.

        The FILE is deliberately not deleted: unlinking races with another process
        that may already have it open, and the file's existence means nothing on its
        own -- only the byte-range lock does.
        """
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock_byte(fd)
        except OSError:
            pass                    # closing the handle releases it regardless
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# ---- platform primitives -------------------------------------------------

def _lock_byte(fd: int) -> None:
    os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
    if _IS_WINDOWS:
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        # The Linux test container path. flock is whole-file and advisory, which is
        # fine: every participant here is this same code.
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_byte(fd: int) -> None:
    if _IS_WINDOWS:
        import msvcrt
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def read_holder(path: Path) -> dict[str, Any]:
    """Holder metadata written by whoever owns the lock, or {}.

    Readable even while the lock is held -- that is the whole point of locking a
    byte at a high offset instead of offset 0.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read(512).rstrip(b" \n\x00")
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


# ---- the two locks this app actually takes -------------------------------

def _locks_dir() -> Path:
    return instance_paths.shared_dir() / "locks"


def account_lock(login: int | str, server: str) -> FileLock:
    """The lock for one broker account.

    Keyed on `accounts._profile_id`, which normalises the server name -- so
    " Exness-MT5Trial16" and "Exness-MT5Trial16" cannot become two locks over one
    account. The filename carries the login for eyeball debugging plus a hash of
    the full id for uniqueness, so no server name needs to be filesystem-safe.
    """
    import accounts
    profile_id = accounts._profile_id(int(login), server)
    digest = hashlib.sha1(profile_id.encode("utf-8")).hexdigest()[:8]
    return FileLock(_locks_dir() / f"{int(login)}-{digest}.lock")


def ticklog_lock() -> FileLock:
    """Machine-wide: only one instance may capture ticks at a time."""
    return FileLock(_locks_dir() / "ticklog.lock")

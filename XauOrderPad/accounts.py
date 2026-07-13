"""Encrypted MT5 account-profile store.

Profiles let the user save several Exness accounts and switch between them from
the web UI. Split into two stores so no trade-capable secret ever lands in a
plaintext file:

  * Non-secret index  -> %LOCALAPPDATA%\\XauOrderPad\\profiles.json
        {id, label, login, server, path, last_trade_mode}
  * Password          -> Windows Credential Locker via `keyring`
        service "XauOrderPad", username = profile id ("<login>@<server>")

`keyring` uses the OS credential vault (Windows Credential Manager here), so the
password is encrypted at rest under the user's account. If the keyring backend
is unavailable we FAIL CLOSED -- raise rather than fall back to plaintext.
The password is never written to profiles.json and never logged.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("XauOrderPad.accounts")

SERVICE = "XauOrderPad"            # keyring service namespace
_INDEX_FIELDS = ("id", "label", "login", "server", "path", "last_trade_mode")


def _store_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "XauOrderPad"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _store_dir() / "profiles.json"


def _keyring():
    """Import keyring lazily so the module loads even if it isn't installed yet."""
    import keyring
    return keyring


def _clean_server(server: str) -> str:
    """Normalise the broker server name.

    It goes straight into the profile id, so " Exness-MT5Trial16" and "Exness-MT5Trial16" would
    otherwise become TWO profiles for the same account, each with its own vault entry -- and the
    one you did not mean would quietly fail to log in. Whitespace is never part of an MT5 server
    name, so stripping it is safe and closes the duplicate.
    """
    return " ".join(str(server).split())


def _profile_id(login: int, server: str) -> str:
    # login alone can collide across servers (demo vs real); qualify with server.
    return f"{int(login)}@{_clean_server(server)}"


# Guards the read-modify-write of profiles.json. Both callers (save/delete) are SYNC FastAPI
# handlers, so they run on the threadpool and two concurrent requests genuinely interleave --
# add-then-add would drop a record.
_index_lock = threading.Lock()


def _load_index() -> list[dict]:
    p = _index_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.exception("profiles.json unreadable; treating as empty")
        return []


def _save_index(items: list[dict]) -> None:
    """Write atomically: temp file in the same directory, then os.replace().

    A bare write_text() truncates before it writes. A crash or a hard reboot mid-write leaves a
    half-written file -- and _load_index() SWALLOWS the parse error and returns []. So every
    saved profile silently disappears while the passwords stay orphaned in the Credential
    Manager. os.replace() is atomic on Windows and POSIX: readers see the old file or the new
    one, never a torn one.
    """
    p = _index_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2), "utf-8")
    os.replace(tmp, p)


def _public(rec: dict) -> dict:
    return {k: rec.get(k) for k in _INDEX_FIELDS}


# ---- public API ---------------------------------------------------------

def list_profiles() -> list[dict]:
    """Saved profiles WITHOUT secrets (safe to send to the browser)."""
    return [_public(it) for it in _load_index()]


def get_profile(profile_id: str) -> dict | None:
    for it in _load_index():
        if it.get("id") == profile_id:
            return it
    return None


def get_password(profile_id: str) -> str | None:
    try:
        return _keyring().get_password(SERVICE, profile_id)
    except Exception:
        log.exception("keyring read failed for %s", profile_id)
        return None


def save_profile(label, login, password, server, path="", last_trade_mode=None) -> dict:
    """Persist a profile. Stores the password in the OS vault FIRST; if that
    fails (no keyring backend) we raise and write nothing -- never plaintext."""
    login = int(login)
    server = _clean_server(server)
    pid = _profile_id(login, server)
    if password:
        try:
            _keyring().set_password(SERVICE, pid, password)
        except Exception as exc:
            raise RuntimeError(
                "cannot store the password securely (no keyring backend "
                f"available): {exc}") from exc
    rec = {"id": pid, "label": (label or str(login)), "login": login,
           "server": server, "path": path or "", "last_trade_mode": last_trade_mode}
    # Load -> mutate -> store under ONE lock. Without it, two concurrent saves both read the
    # same index and the second write drops the first record.
    with _index_lock:
        items = [it for it in _load_index() if it.get("id") != pid]
        items.append(rec)
        _save_index(items)
    log.info("profile saved", extra={"event": "profile_saved",
                                     "login": login, "server": server})
    return _public(rec)


def delete_profile(profile_id: str) -> bool:
    with _index_lock:
        items = _load_index()
        kept = [it for it in items if it.get("id") != profile_id]
        removed = len(kept) != len(items)
        # Only rewrite when something actually changed: an unknown id must not rewrite the file
        # (and must not delete a vault entry that some other profile might legitimately own).
        if removed:
            _save_index(kept)
    if not removed:
        return False
    try:
        _keyring().delete_password(SERVICE, profile_id)
    except Exception:
        pass  # secret may already be gone; index removal is what matters
    return True


def update_trade_mode(profile_id: str, trade_mode) -> None:
    with _index_lock:
        items = _load_index()
        changed = False
        for it in items:
            if it.get("id") == profile_id:
                it["last_trade_mode"] = trade_mode
                changed = True
        if changed:
            _save_index(items)

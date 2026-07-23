"""The instance registry: which servers exist, on which port, driving which terminal.

`instances.json` is the single source of truth for the launchers (start_instance.bat)
and the monitoring console. It is NOT read by server.py -- a running server is
configured purely by its environment, so an edit here can never retarget a server
that is already driving an account.

Shape
-----
    {
      "instances": [
        { "name": "a1", "port": 8766, "label": "Exness demo 1",
          "mt5_path": "D:\\\\mt5\\\\instances\\\\a1\\\\terminal64.exe" }
      ]
    }

`name` becomes a directory and a log filename (see instance_paths), so it is
validated by the same rule.

Why validation refuses rather than warns
----------------------------------------
Two mistakes in this file are silently catastrophic, and both are one copy-paste
away:

  * **duplicate `mt5_path`** -- two servers driving ONE terminal. Each thinks it
    owns the account; a close-all from either flattens a book the other is also
    managing. The account lock does not catch this, because the two servers may
    legitimately hold different accounts at the moment they start.
  * **duplicate `port`** -- the second server fails to bind and dies, leaving an
    account you believe is being supervised with nothing supervising it.

Neither produces an obvious symptom, so `load()` raises.

The file is gitignored: it names real accounts and local paths.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import instance_paths

HERE = Path(__file__).parent
CONFIG_NAME = "instances.json"


class ConfigError(Exception):
    """instances.json is missing, malformed, or self-contradictory."""


def config_path() -> Path:
    return HERE / CONFIG_NAME


def load(path: Path | None = None) -> list[dict[str, Any]]:
    """Every configured instance, validated. Raises ConfigError on any problem.

    Returns dicts with keys: name, port, label, mt5_path, token_file.
    """
    p = Path(path) if path else config_path()
    if not p.exists():
        raise ConfigError(
            f"{p} not found. Copy {CONFIG_NAME}.example and edit it -- one entry per "
            f"account, each with its own port and its own terminal64.exe."
        )
    try:
        raw = json.loads(p.read_text("utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{p} is not valid JSON: {exc}") from exc

    items = raw.get("instances") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise ConfigError(f"{p} must contain a non-empty \"instances\" array.")

    out: list[dict[str, Any]] = []
    seen_names: dict[str, int] = {}
    seen_ports: dict[int, str] = {}
    seen_paths: dict[str, str] = {}
    seen_expect: dict[int, str] = {}

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"{p}: instances[{i}] is not an object.")

        name = str(item.get("name", "")).strip()
        if not instance_paths.is_valid_name(name):
            raise ConfigError(
                f"{p}: instances[{i}].name = {name!r} is not usable. It becomes a "
                f"directory and a log filename: 1-32 chars of A-Z a-z 0-9 . _ - "
                f"starting alphanumeric."
            )
        if name in seen_names:
            raise ConfigError(f"{p}: duplicate instance name {name!r}.")

        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            raise ConfigError(f"{p}: instances[{i}].port must be an integer.") from None
        if not (1 <= port <= 65535):
            raise ConfigError(f"{p}: instances[{i}].port {port} is out of range.")
        if port in seen_ports:
            raise ConfigError(
                f"{p}: port {port} is used by both {seen_ports[port]!r} and {name!r}. "
                f"The second server would fail to bind and exit -- leaving an account "
                f"you believe is supervised with nothing supervising it."
            )

        mt5_path = str(item.get("mt5_path", "")).strip()
        if not mt5_path:
            raise ConfigError(
                f"{p}: instances[{i}].mt5_path is required. Without it the server "
                f"attaches to the machine's default terminal -- i.e. possibly another "
                f"account's."
            )
        key = os.path.normcase(os.path.normpath(mt5_path))
        if key in seen_paths:
            raise ConfigError(
                f"{p}: {seen_paths[key]!r} and {name!r} both point at {mt5_path}. "
                f"That is TWO servers driving ONE terminal: each believes it owns the "
                f"account, and a close-all from either flattens a book the other is "
                f"also managing. Give every instance its own /portable copy."
            )

        seen_names[name] = i
        seen_ports[port] = name
        seen_paths[key] = name

        # The ACCOUNT this instance is allowed to drive. 0 = unpinned.
        #
        # Defaults to the instance name when the name IS an account number, which is why
        # naming instances `472200942` rather than `a1` is worth doing: the label stops
        # being a mnemonic and becomes an enforced fact. Without it, nothing prevents
        # terminal "472200942" being logged into a different account -- and a label that
        # lies on a trading screen is worse than a neutral one.
        raw_expect = item.get("expect_login")
        if raw_expect in (None, "", 0):
            expect = int(name) if name.isdigit() else 0
        else:
            try:
                expect = int(raw_expect)
            except (TypeError, ValueError):
                raise ConfigError(
                    f"{p}: instances[{i}].expect_login = {raw_expect!r} is not a number."
                ) from None
        if expect and name.isdigit() and expect != int(name):
            raise ConfigError(
                f"{p}: instance {name!r} declares expect_login={expect}. The name and the "
                f"pinned account disagree, so one of them is lying about which account this "
                f"instance drives. Fix whichever is wrong."
            )
        if expect in seen_expect:
            raise ConfigError(
                f"{p}: {seen_expect[expect]!r} and {name!r} are both pinned to account "
                f"{expect}. Two servers on one account means two independent close-all "
                f"paths on one book."
            )
        if expect:
            seen_expect[expect] = name

        out.append({
            "name": name,
            "port": port,
            "label": str(item.get("label") or name),
            "mt5_path": mt5_path,
            "expect_login": expect,
            "token_file": str(item.get("token_file") or f".token.{name}.local"),
        })

    return out


def token_for(inst: dict[str, Any]) -> str:
    """This instance's API token, or "" if it has none.

    Per-instance first (`.token.<name>.local`), then the shared `.token.local`.
    A per-instance token is what stops one saved phone profile from driving another
    account: the token is the only credential the trade endpoints check.

    Never log the return value.
    """
    for candidate in (inst.get("token_file"), ".token.local"):
        if not candidate:
            continue
        f = HERE / candidate
        if f.exists():
            tok = f.read_text("utf-8").strip()
            if tok:
                return tok
    return ""


def missing_terminals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries whose mt5_path does not exist on disk.

    Reported by the launcher up front: a typo here otherwise surfaces much later as
    an IPC timeout at login, which reads like a broker outage.
    """
    return [it for it in items if not Path(it["mt5_path"]).is_file()]

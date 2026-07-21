"""Launcher for multi-account mode: start/stop one instance, or all of them.

`start_instance.bat` and `start_all.bat` are thin wrappers around this. The real
work lives here because Windows batch cannot parse JSON, and hand-maintaining N
copies of an env block in .bat files is exactly how two instances end up sharing a
terminal.

What it guarantees before a server is allowed to start
------------------------------------------------------
* `instances.json` validates -- no duplicate port, no duplicate terminal (see
  instances.py for why each of those is silently dangerous).
* The instance has its OWN token. If `.token.<name>.local` is missing, one is
  generated. A token shared between instances would let a phone profile saved for
  account A drive account B; the token is the only credential the trade endpoints
  check.
* Its terminal64.exe exists, and is running (started `/portable` if not).

STOPPING IS PORT-SCOPED, ALWAYS. Never `taskkill /IM python.exe` or by script name:
on a multi-account box that kills every sibling -- including servers supervising
open positions on other accounts.

This module places no orders. It sets environment variables and spawns server.py.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import instances

HERE = Path(__file__).parent
CONSOLE_PORT = 8760


# ---------------------------------------------------------------- helpers

def _ps(script: str) -> str:
    """Run a PowerShell one-liner, return stdout (empty on failure)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=25,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _running_terminals() -> set[str]:
    """Executable paths of every running terminal64.exe, normalised."""
    out = _ps("Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe'\" | "
              "ForEach-Object { $_.ExecutablePath }")
    return {os.path.normcase(os.path.normpath(line.strip()))
            for line in out.splitlines() if line.strip()}


def _pid_on_port(port: int) -> int | None:
    out = _ps(f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
              f"-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess")
    try:
        return int(out.strip())
    except (TypeError, ValueError):
        return None


def ensure_token(inst: dict) -> str:
    """This instance's token, generating one if it has no file of its own.

    Generated rather than shared on purpose: `instances.token_for` would otherwise
    fall back to the common `.token.local`, and every instance would accept every
    other instance's token.
    """
    own = HERE / inst["token_file"]
    if own.exists() and own.read_text("utf-8").strip():
        return own.read_text("utf-8").strip()

    token = secrets.token_urlsafe(24)
    own.write_text(token + "\n", "utf-8")
    print(f"[token] generated {inst['token_file']} for instance {inst['name']!r} "
          f"(each instance needs its own; a shared token lets one saved phone "
          f"profile drive another account)")
    return token


def ensure_terminal(inst: dict) -> None:
    """Start this instance's terminal64.exe /portable if it is not already running."""
    exe = Path(inst["mt5_path"])
    key = os.path.normcase(os.path.normpath(str(exe)))
    if key in _running_terminals():
        print(f"[mt5]   terminal already running: {exe}")
        return
    print(f"[mt5]   starting {exe} /portable")
    subprocess.Popen([str(exe), "/portable"], cwd=str(exe.parent), close_fds=True)
    time.sleep(2)          # let it create its window before uvicorn starts probing


def env_for(inst: dict, token: str) -> dict:
    """The environment that defines an instance. This IS the instance's identity.

    HOST is loopback: multi-account mode is a desktop/box arrangement reached either
    directly or through the mTLS front door, never by binding N cleartext ports to
    the LAN.
    """
    env = dict(os.environ)
    env.update(
        XAUORDERPAD_INSTANCE=inst["name"],
        XAUORDERPAD_PORT=str(inst["port"]),
        XAUORDERPAD_MT5_PATH=inst["mt5_path"],
        XAUORDERPAD_TOKEN=token,
        XAUORDERPAD_HOST="127.0.0.1",
    )
    return env


# ---------------------------------------------------------------- commands

def cmd_start(name: str, detach: bool = False) -> int:
    items = instances.load()
    match = [i for i in items if i["name"] == name]
    if not match:
        sys.exit(f"no instance named {name!r} in {instances.config_path()}. "
                 f"Known: {', '.join(i['name'] for i in items)}")
    inst = match[0]

    if not Path(inst["mt5_path"]).is_file():
        sys.exit(f"terminal not found: {inst['mt5_path']}\n"
                 f"A missing terminal surfaces much later as an IPC timeout at login, "
                 f"which reads like a broker outage. Fix the path in "
                 f"{instances.config_path()}.")

    existing = _pid_on_port(inst["port"])
    if existing:
        print(f"[port]  freeing {inst['port']} (pid {existing})")
        subprocess.run(["taskkill", "/F", "/PID", str(existing)],
                       capture_output=True)
        time.sleep(1)

    token = ensure_token(inst)
    ensure_terminal(inst)

    print(f"[run]   instance {inst['name']!r} -> http://127.0.0.1:{inst['port']}")
    cmd = [sys.executable, str(HERE / "server.py")]
    env = env_for(inst, token)
    if detach:
        CREATE_NEW_CONSOLE = 0x00000010
        subprocess.Popen(cmd, cwd=str(HERE), env=env,
                         creationflags=CREATE_NEW_CONSOLE, close_fds=True)
        return 0
    return subprocess.call(cmd, cwd=str(HERE), env=env)


def cmd_start_all() -> int:
    items = instances.load()
    missing = instances.missing_terminals(items)
    if missing:
        sys.exit("these instances point at a terminal64.exe that does not exist:\n" +
                 "\n".join(f"  {m['name']}: {m['mt5_path']}" for m in missing))

    for inst in items:
        print(f"--- {inst['name']} ({inst['label']}) ---")
        cmd_start(inst["name"], detach=True)
        time.sleep(1.5)          # stagger: N terminals racing to boot is slower, not faster

    print(f"\n[console] starting the read-only monitor on "
          f"http://127.0.0.1:{CONSOLE_PORT}")
    CREATE_NEW_CONSOLE = 0x00000010
    subprocess.Popen([sys.executable, str(HERE / "console.py")], cwd=str(HERE),
                     creationflags=CREATE_NEW_CONSOLE, close_fds=True)
    print(f"\nAll {len(items)} instance(s) started. Each has its own window.")
    print(f"Monitor: http://127.0.0.1:{CONSOLE_PORT}")
    return 0


def cmd_stop_all() -> int:
    """Stop every CONFIGURED instance, by port.

    By port and never by image name: `taskkill /IM python.exe` on this box would
    also kill servers supervising open positions on accounts you did not ask to
    stop -- and the single-account server on 8765, which is not even in this file.
    """
    items = instances.load()
    stopped = 0
    for inst in items + [{"name": "console", "port": CONSOLE_PORT}]:
        pid = _pid_on_port(inst["port"])
        if pid:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            print(f"[stop]  {inst['name']} (port {inst['port']}, pid {pid})")
            stopped += 1
        else:
            print(f"[stop]  {inst['name']} (port {inst['port']}) was not running")
    print(f"\nStopped {stopped}. The terminals are left running -- they hold your "
          f"broker sessions, and closing them logs the accounts out.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Start/stop XauOrderPad instances.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("name", nargs="?", help="instance to start in this window")
    g.add_argument("--all", action="store_true", help="start every instance + the monitor")
    g.add_argument("--stop", action="store_true", help="stop every configured instance")
    g.add_argument("--list", action="store_true", help="show the configured instances")
    args = ap.parse_args()

    try:
        if args.list:
            for i in instances.load():
                exists = "ok" if Path(i["mt5_path"]).is_file() else "MISSING"
                print(f"  {i['name']:6} port={i['port']:5}  {i['label']:20} "
                      f"{i['mt5_path']}  [{exists}]")
            return 0
        if args.stop:
            return cmd_stop_all()
        if args.all:
            return cmd_start_all()
        return cmd_start(args.name)
    except instances.ConfigError as exc:
        sys.exit(f"\nCONFIG ERROR\n{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

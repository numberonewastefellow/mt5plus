"""Auto-launch the order pad UI in a Chrome/Edge "app-mode" window.

Why this exists
---------------
The order pad is a local FastAPI site. Running it inside a normal browser tab
(address bar, tabs, bookmarks) makes it feel like a web page, not the dedicated
trading tool it is. Chromium's ``--app=<URL>`` flag opens the site in a
standalone, frameless window -- no address bar, no tabs -- so it looks and
behaves like a native desktop app. (This is NOT headless: headless shows no
window at all and is for automation/screenshots.)

Design
------
- Browser preference: Google Chrome first, then Microsoft Edge (always present
  on Windows 11). Both are Chromium and accept identical flags.
- Launch happens on a daemon thread that first waits for the server port to
  accept connections, so the window only opens once the UI is actually serving.
- This is a convenience feature: if no browser is found or the launch fails, we
  log a warning and print the URL -- we NEVER take down the trading server.

Pure stdlib -- no new pip dependencies.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("XauOrderPad.browser")

# Common per-browser install locations, checked after the registry lookup.
# Order within each list matters: most-likely path first.
_CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _app_paths_lookup(exe_name: str) -> str | None:
    """Resolve a browser exe via the Windows ``App Paths`` registry key.

    Returns the absolute path if the key exists and points at a real file,
    else None. Tries HKLM then HKCU. Non-Windows / missing winreg -> None.
    """
    try:
        import winreg  # Windows-only; import lazily so the module imports anywhere
    except ImportError:
        return None

    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                path, _ = winreg.QueryValueEx(key, None)  # default value = exe path
            if path and Path(path).is_file():
                return path
        except OSError:
            continue
    return None


def find_browser() -> tuple[str, str] | None:
    """Locate a Chromium browser executable.

    Resolution: Chrome (registry, then common paths), then Edge (registry,
    then common paths, then %LOCALAPPDATA%). Returns ``(exe_path, kind)`` where
    kind is ``"chrome"`` or ``"edge"``, or None if nothing is found.
    """
    local = os.environ.get("LOCALAPPDATA", "")

    # --- Chrome ---------------------------------------------------------
    chrome = _app_paths_lookup("chrome.exe")
    if chrome:
        return chrome, "chrome"
    candidates = list(_CHROME_PATHS)
    if local:
        candidates.append(str(Path(local) / r"Google\Chrome\Application\chrome.exe"))
    for path in candidates:
        if Path(path).is_file():
            return path, "chrome"

    # --- Edge fallback --------------------------------------------------
    edge = _app_paths_lookup("msedge.exe")
    if edge:
        return edge, "edge"
    candidates = list(_EDGE_PATHS)
    if local:
        candidates.append(str(Path(local) / r"Microsoft\Edge\Application\msedge.exe"))
    for path in candidates:
        if Path(path).is_file():
            return path, "edge"

    return None


def _profile_dir() -> str:
    """Dedicated, persistent browser profile for the app window.

    Using a separate ``--user-data-dir`` keeps the app window isolated from the
    user's normal Chrome profile, guarantees it opens as its own window even
    when Chrome is already running, and lets it remember size/position.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    profile = Path(base) / "XauOrderPad" / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return str(profile)


def build_args(exe: str, url: str, mode: str) -> list[str]:
    """Build the browser command line for the requested window mode.

    mode == "kiosk" -> fullscreen, locked (exit with Alt+F4).
    anything else   -> "app" mode: standalone frameless window (default).
    """
    common = [
        f"--user-data-dir={_profile_dir()}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if mode == "kiosk":
        return [exe, "--kiosk", url, *common]
    return [exe, f"--app={url}", *common]


def kill_existing_app_windows() -> None:
    """Close any previously-opened app windows belonging to this app.

    On restart (e.g. start_server.bat kills the old server but NOT its browser
    window), a stale app window lingers. Because every app window shares one
    fixed ``--user-data-dir``, launching a new one would just open a second
    window in the old browser process. So before launching we terminate any
    chrome/msedge process whose command line references OUR profile dir -- this
    targets only our windows and never the user's normal browser. Best-effort:
    failures here are logged and ignored so the launch still proceeds.
    """
    # Match by the unique profile-dir marker in the process command line.
    ps = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='chrome.exe' or Name='msedge.exe'\" | "
        "Where-Object { $_.CommandLine -like '*XauOrderPad\\browser-profile*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give the OS a beat to release the old profile's singleton lock before
        # the fresh Chrome tries to claim it.
        time.sleep(0.4)
    except Exception as exc:
        log.warning(
            "could not close stale app windows",
            extra={"event": "browser_kill_error", "error": str(exc)},
        )


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """Poll until ``(host, port)`` accepts a TCP connection or timeout elapses.

    Bridges the small race between uvicorn binding the socket and actually
    serving requests. Returns True if the port came up, False on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _launch(url: str, host: str, port: int, mode: str) -> None:
    """Worker body: wait for the server, find a browser, open the window."""
    try:
        if not wait_for_port(host, port):
            log.warning(
                "server port did not come up; skipping browser launch",
                extra={"event": "browser_launch_timeout", "url": url},
            )
            print(f"[browser] server not reachable; open manually: {url}")
            return

        found = find_browser()
        if not found:
            log.warning(
                "no Chrome/Edge found; open the UI manually",
                extra={"event": "browser_not_found", "url": url},
            )
            print(f"[browser] Chrome/Edge not found; open manually: {url}")
            return

        # Replace, don't stack: close any stale app window from a prior run so
        # the restart yields exactly one fresh window (see kill_existing...).
        kill_existing_app_windows()

        exe, kind = found
        args = build_args(exe, url, mode)
        subprocess.Popen(args, close_fds=True)
        log.info(
            "launched UI in app window",
            extra={"event": "browser_launched", "browser": kind,
                   "mode": mode, "exe": exe, "url": url},
        )
    except Exception as exc:  # never let this convenience feature crash the server
        log.warning(
            "browser launch failed",
            extra={"event": "browser_launch_error", "url": url, "error": str(exc)},
        )
        print(f"[browser] launch failed ({exc}); open manually: {url}")


def launch_when_ready(url: str, host: str, port: int, mode: str = "app") -> None:
    """Open ``url`` in a Chrome/Edge app window once the server is accepting.

    Returns immediately; the wait-and-launch runs on a daemon thread so it
    never blocks (or outlives) the server process.
    """
    threading.Thread(
        target=_launch,
        args=(url, host, port, mode),
        name="browser-launch",
        daemon=True,
    ).start()

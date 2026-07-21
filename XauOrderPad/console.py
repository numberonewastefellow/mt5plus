"""Read-only monitor: which instance is live, on which account, through which terminal.

    python console.py        ->  http://127.0.0.1:8760

THIS PROCESS CANNOT TRADE, AND THAT IS STRUCTURAL
-------------------------------------------------
It imports `instances` and `instance_paths` and nothing else from this project --
never `config`, never `mt5_worker`, never `MetaTrader5`. There is no import path
from here to `order_send`, and it serves no POST/PUT/DELETE route at all: the
request handler implements `do_GET` only, so a POST is answered 501 by the stdlib
before any of this code runs.

Do not "helpfully" add a close-all button. The moment this can mutate anything it
stops being a monitor and becomes a second, unaudited trading surface -- one that
holds every instance's token at once.

WHY A SEPARATE PROCESS, not a page inside each server: the question it exists to
answer is "which instances are DOWN", and a page hosted inside a dead instance
cannot answer it.

BINDING is hard-coded to 127.0.0.1 with no override. This aggregates API tokens and
account numbers for every account on the box. Reach it remotely the same way the
desktop UI is reached -- an SSH tunnel -- never by binding it wider.

Tokens are read from the same local files the launchers already use, so this adds no
exposure the operator's own account did not have; they are never rendered in a page,
never returned by the JSON route, and never logged.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import instance_paths
import instances

BIND_HOST = "127.0.0.1"      # not configurable. See module docstring.
PORT = int(os.environ.get("XAUORDERPAD_CONSOLE_PORT", "8760"))
PROBE_TIMEOUT = 1.5


# ---------------------------------------------------------------- collection

def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        try:
            s.connect((BIND_HOST, port))
            return True
        except OSError:
            return False


def _live_pids() -> set[int]:
    """PIDs currently running, so a lock's recorded holder can be tested for life."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-Process | ForEach-Object { $_.Id }"],
            capture_output=True, text=True, timeout=20).stdout
        return {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:
        return set()


def _read_locks() -> list[dict[str, Any]]:
    """The account-lock registry: who claims which account.

    Note a lock FILE outliving its holder is normal and harmless -- the OS lock is
    released on process death while the metadata file stays behind. So a record only
    counts as a live claim when its pid is still running.
    """
    out: list[dict[str, Any]] = []
    d = instance_paths.shared_dir() / "locks"
    if not d.is_dir():
        return out
    alive = _live_pids()
    for f in sorted(d.glob("*.lock")):
        try:
            raw = f.read_bytes()[:512].rstrip(b" \n\x00")
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not rec:
            continue
        rec["_file"] = f.name
        rec["_alive"] = int(rec.get("pid") or -1) in alive
        out.append(rec)
    return out


def _probe(inst: dict[str, Any]) -> dict[str, Any]:
    """GET /api/state for one instance. Never raises; failure IS the answer."""
    row: dict[str, Any] = {
        "name": inst["name"], "label": inst["label"], "port": inst["port"],
        "mt5_path": inst["mt5_path"], "configured": True,
        "up": False, "state": "down", "account": None, "acct_server": None,
        "is_demo": None, "healthy": False, "trade_allowed": None,
        "positions": 0, "floating_pl": 0.0, "wrong_terminal": None, "error": None,
    }
    if not _port_open(inst["port"]):
        return row
    row["up"] = True

    token = instances.token_for(inst)
    req = urllib.request.Request(f"http://{BIND_HOST}:{inst['port']}/api/state")
    if token:
        req.add_header("x-token", token)
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            st = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 401 means the server is healthy but this instance's token is wrong -- a
        # real misconfiguration, and a different fault from "down".
        row["state"] = "auth"
        row["error"] = (f"HTTP {exc.code} from /api/state -- check "
                        f"{inst['token_file']}") if exc.code == 401 else f"HTTP {exc.code}"
        return row
    except Exception as exc:                       # noqa: BLE001
        row["state"] = "unreachable"
        row["error"] = str(exc)
        return row

    acct = st.get("account") or {}
    row.update(
        account=acct.get("login"),
        acct_server=acct.get("server"),
        is_demo=acct.get("is_demo"),
        healthy=bool(st.get("healthy")),
        trade_allowed=st.get("trade_allowed"),
        positions=len(st.get("positions") or []),
        floating_pl=st.get("floating_pl") or 0.0,
        wrong_terminal=st.get("wrong_terminal"),
        error=st.get("error"),
    )
    if st.get("wrong_terminal"):
        row["state"] = "wrong_terminal"
    elif st.get("logged_out") or not acct:
        row["state"] = "logged_out"
    elif row["healthy"]:
        row["state"] = "healthy"
    else:
        row["state"] = "unhealthy"
    return row


def collect() -> dict[str, Any]:
    """One full picture: configured instances, probed concurrently, plus strays."""
    try:
        items = instances.load()
        cfg_error = None
    except instances.ConfigError as exc:
        items, cfg_error = [], str(exc)

    with ThreadPoolExecutor(max_workers=max(1, len(items)) if items else 1) as pool:
        rows = list(pool.map(_probe, items)) if items else []

    locks = _read_locks()
    by_port = {r["port"]: r for r in rows}

    # Cross-check every LIVE claim against what the server actually reports.
    for lk in locks:
        if not lk.get("_alive") or not lk.get("login"):
            continue
        row = by_port.get(lk.get("port"))
        if row is None:
            # Something is driving an account from a port this config knows nothing
            # about. Two very different situations, and conflating them would make
            # the alarm useless:
            #
            #   instance == ""  -> the classic SINGLE-ACCOUNT server (start_server.bat).
            #       Entirely legitimate, and it CANNOT be listed in instances.json --
            #       giving it a name would set XAUORDERPAD_INSTANCE and move all its
            #       state paths, which is exactly the single-account guarantee we
            #       promised not to break. Show it, do not shout about it.
            #
            #   instance == "a3" -> a NAMED instance that is not in the config. That is
            #       a genuine stray: something is driving an account outside the managed
            #       set, and nothing is watching it. Shout.
            named = (lk.get("instance") or "").strip()
            extra = {
                "name": named or "(single-account)",
                "label": "not managed by instances.json",
                "port": lk.get("port"), "mt5_path": "(not configured here)",
                "configured": False, "up": True,
                "state": "stray" if named else "unmanaged",
                "account": lk.get("login"), "acct_server": lk.get("server"),
                "is_demo": None, "healthy": False, "trade_allowed": None,
                # UNKNOWN, not zero. These come from /api/state, which has not been
                # called for this row yet; rendering a hard 0 for a server that is
                # actually holding ten positions is worse than admitting ignorance.
                "positions": None, "floating_pl": None, "wrong_terminal": None,
                "error": (f"instance {named!r} holds account {lk.get('login')} but is "
                          f"not in instances.json -- nothing is managing it")
                         if named else None,
            }
            # Try to fill it in anyway. There is no config entry, so there is no
            # token_file -- token_for() then falls back to the shared .token.local,
            # which is exactly the token the single-account server uses. When that
            # works the operator gets full visibility of their main server; when it
            # does not, the fields stay unknown rather than becoming fiction.
            if extra["port"]:
                probed = _probe({"name": extra["name"], "label": extra["label"],
                                 "port": extra["port"], "mt5_path": extra["mt5_path"],
                                 "token_file": None})
                if probed.get("account") is not None:
                    keep_state, keep_err = extra["state"], extra["error"]
                    extra.update(probed)
                    extra["configured"] = False
                    extra["mt5_path"] = "(not configured here)"
                    extra["state"] = keep_state      # unmanaged/stray outranks healthy
                    extra["error"] = keep_err or probed.get("error")
            rows.append(extra)
        elif row.get("account") and str(row["account"]) != str(lk["login"]):
            # The exclusivity record and reality disagree -- the precise condition
            # the account lock exists to prevent.
            row["state"] = "lock_mismatch"
            row["error"] = (f"lock says account {lk['login']} but the server reports "
                            f"{row['account']}")

    rows.sort(key=lambda r: (not r["configured"], r["port"] or 0))
    return {"instances": rows, "locks_held": sum(1 for l in locks if l.get("_alive")),
            "config_error": cfg_error, "config_path": str(instances.config_path())}


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "XauOrderPadConsole"

    # do_GET ONLY. No do_POST/do_PUT/do_DELETE anywhere in this class, so the stdlib
    # answers 501 to any mutating verb before reaching a line of our code.
    def do_GET(self) -> None:                       # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/instances":
            self._json(collect())
        elif path in ("/", "/index.html"):
            self._html(PAGE)
        else:
            self.send_error(404)

    def _json(self, obj: Any) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Quiet by default. The access log would add nothing but noise, and this
        # process handles tokens -- the less it writes anywhere, the better.
        pass


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>XauOrderPad instances</title>
<style>
 :root{--bg:#0f1115;--card:#171a21;--line:#262b36;--dim:#8b93a7;--fg:#e6e9ef;
       --ok:#3fb950;--warn:#d29922;--bad:#f85149;--off:#484f5e}
 @media(prefers-color-scheme:light){:root{--bg:#f5f6f8;--card:#fff;--line:#e2e5ea;
       --dim:#5c6472;--fg:#12151b;--off:#aab1bf}}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:14px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace}
 header{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;
        justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
 h1{font-size:15px;margin:0;letter-spacing:.02em}
 .meta{color:var(--dim);font-size:12px}
 main{padding:16px 20px;display:grid;gap:10px;max-width:1100px}
 .row{background:var(--card);border:1px solid var(--line);border-left-width:4px;
      border-radius:8px;padding:12px 14px}
 .row.healthy{border-left-color:var(--ok)}
 .row.logged_out{border-left-color:var(--warn)}
 .row.down,.row.unreachable{border-left-color:var(--off)}
 .row.unhealthy,.row.auth{border-left-color:var(--warn)}
 .row.stray,.row.wrong_terminal,.row.lock_mismatch{border-left-color:var(--bad);
      background:color-mix(in srgb,var(--bad) 8%,var(--card))}
 .top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
 .nm{font-weight:600}
 .port{color:var(--dim)}
 .acct{margin-left:auto;font-variant-numeric:tabular-nums}
 .tag{font-size:11px;padding:1px 7px;border-radius:99px;border:1px solid var(--line);
      color:var(--dim)}
 .tag.real{background:var(--bad);color:#fff;border-color:var(--bad);font-weight:700}
 .tag.demo{color:var(--ok);border-color:var(--ok)}
 .path{color:var(--dim);font-size:12px;margin-top:6px;word-break:break-all}
 .stats{margin-top:6px;font-size:12px;color:var(--dim);display:flex;gap:14px;flex-wrap:wrap}
 .err{margin-top:8px;color:var(--bad);font-size:12px;font-weight:600}
 .empty{color:var(--dim);padding:20px}
 .pl.neg{color:var(--bad)} .pl.pos{color:var(--ok)}
</style></head><body>
<header>
  <h1>XauOrderPad &mdash; instances</h1>
  <div class="meta">read-only &middot; 127.0.0.1 only &middot; <span id="ts">&hellip;</span></div>
</header>
<main id="out"><div class="empty">loading&hellip;</div></main>
<script>
const LABEL={healthy:"healthy",logged_out:"up, no account",down:"DOWN",
  unreachable:"unreachable",unhealthy:"not healthy",auth:"TOKEN REJECTED",
  stray:"STRAY \\u2014 not in instances.json",wrong_terminal:"WRONG TERMINAL",
  lock_mismatch:"LOCK MISMATCH"};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function tick(){
  let d; try{ d=await (await fetch("/api/instances",{cache:"no-store"})).json(); }
  catch(e){ document.getElementById("ts").textContent="console unreachable"; return; }
  document.getElementById("ts").textContent=new Date().toLocaleTimeString()
    +"  \\u00b7  "+d.locks_held+" account lock(s) held";
  const out=document.getElementById("out");
  if(d.config_error){ out.innerHTML='<div class="row stray"><div class="err">'
    +esc(d.config_error)+'</div></div>'; return; }
  if(!d.instances.length){ out.innerHTML='<div class="empty">no instances configured</div>'; return; }
  out.innerHTML=d.instances.map(i=>{
    const acct=i.account?esc(i.account):"&mdash;";
    const badge=i.account==null?"":(i.is_demo===false
      ?'<span class="tag real">REAL</span>':(i.is_demo===true?'<span class="tag demo">demo</span>':""));
    // null means NOT MEASURED (e.g. a row built from the lock registry that could not
    // be probed). Never render that as 0 -- a fabricated zero on a book with ten
    // open positions is the one number a monitor must not print.
    const posTxt=i.positions==null?"? pos":`${i.positions} pos`;
    const pl=i.floating_pl==null?null:Number(i.floating_pl);
    const plTxt=pl==null?"P&amp;L ?":`P&amp;L ${pl.toFixed(2)}`;
    const stats=i.state==="down"?"":
      `<div class="stats"><span>${posTxt}</span>`
      +`<span class="pl ${pl==null?'':(pl<0?'neg':(pl>0?'pos':''))}">${plTxt}</span>`
      +`<span>AutoTrading ${i.trade_allowed===true?"ON":(i.trade_allowed===false?"OFF":"?")}</span>`
      +`<span>${esc(i.acct_server||"")}</span></div>`;
    return `<div class="row ${esc(i.state)}">
      <div class="top"><span class="nm">${esc(i.name)}</span>
        <span class="port">:${esc(i.port)}</span>
        <span class="tag">${esc(LABEL[i.state]||i.state)}</span>
        <span class="acct">${acct} ${badge}</span></div>
      <div class="path">${esc(i.mt5_path)}</div>${stats}
      ${i.error?`<div class="err">${esc(i.error)}</div>`:""}</div>`;
  }).join("");
}
tick(); setInterval(tick,2000);
</script></body></html>
"""


def main() -> int:
    # Refuse anything but loopback, loudly, rather than silently publishing every
    # account number and health state on the box.
    if BIND_HOST not in ("127.0.0.1", "::1", "localhost"):
        sys.exit("console.py refuses to bind anything but loopback.")
    srv = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    print(f"\n  XauOrderPad console (READ-ONLY)\n"
          f"    http://{BIND_HOST}:{PORT}\n"
          f"    config: {instances.config_path()}\n"
          f"    Ctrl+C to stop.\n", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

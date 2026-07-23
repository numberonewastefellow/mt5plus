"""End-to-end validation of the XauOrderPad order path on a DEMO account.

Exercises the same HTTP endpoints the UI uses (/order, /close, /close_all) and
reads results back from /api/state (which mirrors mt5.positions_get = MT5 truth).
Safe: refuses to run unless the connected account is a demo and AutoTrading is on.

Run (server must be running on 127.0.0.1:8765):
    .venv\\Scripts\\python.exe validate_orders.py
"""

from __future__ import annotations

import sys
import time
import urllib.request
import json

BASE = "http://127.0.0.1:8765"
LOT = 0.01


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read())


def _post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def state():
    return _get("/api/state")


def wait_positions(pred, timeout=4.0):
    """Wait until the /api/state positions satisfy pred(positions). Returns final list."""
    end = time.time() + timeout
    last = []
    while time.time() < end:
        last = state().get("positions", [])
        if pred(last):
            return last
        time.sleep(0.1)
    return last


PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main():
    print("== XauOrderPad order-path validation ==")
    s = state()
    acc = s.get("account") or {}
    print(f"  account #{acc.get('login')} @ {acc.get('server')}  "
          f"is_demo={acc.get('is_demo')}  symbol={s.get('symbol')}  "
          f"bid/ask={s.get('bid')}/{s.get('ask')}")

    # --- safety gates ---------------------------------------------------
    if not s.get("connected"):
        print("  ABORT: terminal not connected."); return 2
    # `is not True`, NOT `is False`. This script's FIRST action is /close_all -- and with
    # config.RESTRICT_CLOSE_TO_MAGIC = False that flattens the entire XAUUSD book, manual
    # positions included -- before it places anything. `is_demo` is ABSENT from a degraded
    # state frame (mt5_worker only fills st["account"] when account_info() returns), so
    # `.get()` yields None, `None is False` is False, and the old gate let an UNKNOWN
    # account straight through. Unknown must fail closed, the same way mt5_worker refuses
    # an unknown trade_mode and deploy/mt5_ec2.py checks `is True`.
    if acc.get("is_demo") is not True:
        print("  ABORT: account is not confirmed DEMO (is_demo="
              f"{acc.get('is_demo')!r}) — refusing to place test orders."); return 2
    if not s.get("healthy"):
        print(f"  ABORT: not healthy — {s.get('error')}.")
        print("  (enable AutoTrading in MT5 with Ctrl+E, then re-run)"); return 2

    # --- start flat -----------------------------------------------------
    _post("/close_all", {})
    wait_positions(lambda p: len(p) == 0)
    check("start flat", len(state().get("positions", [])) == 0)

    # --- BUY market -> verify -> close ----------------------------------
    code, r = _post("/order", {"side": "buy", "volume": LOT, "type": "market"})
    check("BUY accepted (200 + ticket)", code == 200 and r.get("ticket"), f"{code} {r}")
    tk = r.get("ticket")
    pos = wait_positions(lambda p: any(x["ticket"] == tk for x in p))
    found = next((x for x in pos if x["ticket"] == tk), None)
    check("BUY position visible in feed", found is not None, f"ticket {tk}")
    if found:
        check("BUY side correct", found["side"] == "BUY", found["side"])
        check("BUY volume correct", abs(found["volume"] - LOT) < 1e-9, found["volume"])
        check("BUY has live profit field", "profit" in found, f"profit={found.get('profit')}")
    code, r = _post("/close", {"ticket": tk})
    check("BUY close accepted", code == 200, f"{code} {r}")
    flat = wait_positions(lambda p: all(x["ticket"] != tk for x in p))
    check("BUY closed (gone from feed)", all(x["ticket"] != tk for x in flat))

    # --- SELL market -> verify -> close ---------------------------------
    code, r = _post("/order", {"side": "sell", "volume": LOT, "type": "market"})
    check("SELL accepted", code == 200 and r.get("ticket"), f"{code} {r}")
    tk = r.get("ticket")
    pos = wait_positions(lambda p: any(x["ticket"] == tk for x in p))
    found = next((x for x in pos if x["ticket"] == tk), None)
    check("SELL position visible", found is not None and found["side"] == "SELL")
    _post("/close", {"ticket": tk})
    flat = wait_positions(lambda p: all(x["ticket"] != tk for x in p))
    check("SELL closed", all(x["ticket"] != tk for x in flat))

    # --- LIMIT pending -> verify -> cancel ------------------------------
    bid = state()["bid"]
    far = round(bid - 5.0, 3)   # buy-limit well below market: stays pending
    code, r = _post("/order", {"side": "buy", "volume": LOT, "type": "limit", "price": far})
    check("LIMIT accepted (pending)", code == 200 and r.get("ticket"), f"{code} {r}")
    tk = r.get("ticket")
    orders = []
    end = time.time() + 4
    while time.time() < end:
        orders = state().get("orders", [])
        if any(o["ticket"] == tk for o in orders):
            break
        time.sleep(0.1)
    check("LIMIT shows in pending orders", any(o["ticket"] == tk for o in orders), f"ticket {tk}")
    code, r = _post("/close", {"ticket": tk})
    check("LIMIT cancel accepted", code == 200, f"{code} {r}")
    end = time.time() + 4
    gone = False
    while time.time() < end:
        if all(o["ticket"] != tk for o in state().get("orders", [])):
            gone = True
            break
        time.sleep(0.1)
    check("LIMIT cancelled (gone)", gone)

    # --- final flatten --------------------------------------------------
    _post("/close_all", {})
    wait_positions(lambda p: len(p) == 0)
    check("end flat", len(state().get("positions", [])) == 0)

    npass = sum(1 for _, ok in results if ok)
    print(f"\n== {npass}/{len(results)} checks passed ==")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

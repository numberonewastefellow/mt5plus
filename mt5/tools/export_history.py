"""Export MT5 closed-trade history to CSV, with the entry->exit distance worked out.

WHY THIS EXISTS
---------------
The MT5 History tab shows profit per ticket, which is not the number this strategy is judged on.
What matters is the **entry-to-exit price distance in $/oz** for the whole BASKET:

    diff = exit - entry        (BUY)
    diff = entry - exit        (SELL)

Every exit rule in the EA reduces to a distance:
  * TARGET    = ExitTargetPct x (balance / ounces)   -- on 2026-09-09 that was ~1.5-1.7 $/oz
  * QUICK arm = 0.30 decaying to 0.10 $/oz           -- the video's own range
So a basket that closed at +1.6 was a target exit, one that closed at +0.1 was a quick exit, and
this file makes that visible per basket instead of per ticket.

READ-ONLY. It calls `history_deals_get` and nothing else - no order, position or account mutation.

    python export_history.py                          # today, magic 532040
    python export_history.py --from 2026-09-07        # from a date to now
    python export_history.py --from 2026-09-01 --to 2026-09-10 --magic 0   # every magic
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from collections import defaultdict

OZ_PER_LOT = 100.0
RGS_MAGIC = 532040


def parse_day(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.UTC)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--to", dest="to", help="YYYY-MM-DD exclusive (default: tomorrow)")
    ap.add_argument("--magic", type=int, default=RGS_MAGIC,
                    help=f"filter by magic; 0 = all (default {RGS_MAGIC})")
    ap.add_argument("--out", help="output path (default mt5/tools/out/history_<acct>_<from>_<to>.csv)")
    args = ap.parse_args()

    today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    frm = parse_day(args.frm) if args.frm else today
    to = parse_day(args.to) if args.to else today + dt.timedelta(days=1)

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("mt5.initialize failed:", mt5.last_error())
        return 1
    acct = mt5.account_info()
    deals = mt5.history_deals_get(frm, to) or []
    mt5.shutdown()

    # Pair each position's entry deal with its exit deal. DEAL_ENTRY_IN = 0, _OUT = 1.
    # A position can be closed by several partial deals; we take the first of each and note the
    # count, rather than silently averaging something the broker split.
    legs = defaultdict(lambda: {"in": [], "out": []})
    for d in deals:
        if args.magic and d.magic != args.magic:
            continue
        if d.entry == 0:
            legs[d.position_id]["in"].append(d)
        elif d.entry == 1:
            legs[d.position_id]["out"].append(d)

    rows = []
    for pid, v in legs.items():
        if not v["in"] or not v["out"]:
            continue          # still open, or the entry falls outside the window
        i, o = v["in"][0], v["out"][0]
        side = "BUY" if i.type == 0 else "SELL"
        diff = (o.price - i.price) if side == "BUY" else (i.price - o.price)
        rows.append({
            "open_time":  dt.datetime.fromtimestamp(i.time, dt.UTC).isoformat(),
            "close_time": dt.datetime.fromtimestamp(o.time, dt.UTC).isoformat(),
            "symbol": i.symbol,
            "side": side,
            "lot": f"{i.volume:.2f}",
            "oz": f"{i.volume * OZ_PER_LOT:.0f}",
            "entry_px": f"{i.price:.3f}",
            "exit_px": f"{o.price:.3f}",
            "diff_usd_per_oz": f"{diff:+.3f}",
            "profit": f"{o.profit:.2f}",
            "commission": f"{i.commission + o.commission:.2f}",
            "swap": f"{o.swap:.2f}",
            "magic": i.magic,
            "position_id": pid,
            "comment": (i.comment or "").strip(),
        })
    rows.sort(key=lambda r: r["close_time"])

    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "out",
        f"history_{acct.login if acct else 0}_{frm:%Y%m%d}_{to:%Y%m%d}.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if rows:
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    # A BASKET is every position the EA closed in the same second - that is how CloseAll unwinds.
    # This is the level the exit rules act on: the EA never looks at one ticket's P&L.
    baskets = defaultdict(list)
    for r in rows:
        baskets[(r["close_time"][:19], r["side"])].append(r)

    brows = []
    for (t, side), rs in sorted(baskets.items()):
        vol = sum(float(r["lot"]) for r in rs)
        oz = vol * OZ_PER_LOT
        avg_in = sum(float(r["entry_px"]) * float(r["lot"]) for r in rs) / vol
        avg_out = sum(float(r["exit_px"]) * float(r["lot"]) for r in rs) / vol
        diff = (avg_out - avg_in) if side == "BUY" else (avg_in - avg_out)
        brows.append({
            "close_time": t, "side": side, "positions": len(rs),
            "lots": f"{vol:.2f}", "oz": f"{oz:.0f}",
            "avg_entry": f"{avg_in:.3f}", "avg_exit": f"{avg_out:.3f}",
            "diff_usd_per_oz": f"{diff:+.3f}",
            "gross_profit": f"{sum(float(r['profit']) for r in rs):.2f}",
            "commission": f"{sum(float(r['commission']) for r in rs):.2f}",
            "net": f"{sum(float(r['profit']) + float(r['commission']) for r in rs):.2f}",
        })
    bout = out.replace(".csv", "_baskets.csv")
    if brows:
        with open(bout, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(brows[0].keys()))
            w.writeheader()
            w.writerows(brows)

    print(f"account {acct.login if acct else '?'}   {frm:%Y-%m-%d} -> {to:%Y-%m-%d}"
          f"   magic {args.magic or 'ALL'}")
    print(f"  {len(rows):4d} closed positions -> {out}")
    print(f"  {len(brows):4d} baskets          -> {bout}\n")
    if brows:
        print(f"{'close_time':>20} {'side':>5} {'pos':>4} {'oz':>5} {'entry':>10} {'exit':>10} "
              f"{'DIFF':>8} {'net':>9}")
        for b in brows:
            print(f"{b['close_time']:>20} {b['side']:>5} {b['positions']:>4} {b['oz']:>5} "
                  f"{b['avg_entry']:>10} {b['avg_exit']:>10} {b['diff_usd_per_oz']:>8} "
                  f"{b['net']:>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

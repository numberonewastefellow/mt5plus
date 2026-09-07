r"""Replay the reconstructed XAUUSD recovery grid against REAL ticks.

Drives the exact decision object a live engine would drive (`grid_state.GridState`) over a tick
stream, with a broker that fills the way MT5 does. Same pattern as `analysis/sladder_replay.py`.

    cd analysis/video_ocr
    ..\..\XauOrderPad\.venv\Scripts\python.exe grid_replay.py --selftest
    ..\..\XauOrderPad\.venv\Scripts\python.exe grid_replay.py --day 2026-08-28 --direction sell
    ..\..\XauOrderPad\.venv\Scripts\python.exe grid_replay.py --matrix --out "D:\llm\ios\xausd_video_out"

── Data ──

  * `--day YYYY-MM-DD` / `--from` / `--to` -> live MT5 `copy_ticks_range`, READ-ONLY.
    **Timezone trap:** this feed's server clock is UTC, but `copy_ticks_range` reads a NAIVE
    datetime as LOCAL. A naive `Aug 28 00:00` returned ticks from `Aug 27 18:30Z` on this box.
    Every datetime here is tz-aware UTC for that reason.
  * `--csv PATH` -> a cached export (`time_msc,iso_time_utc,bid,ask,...`), e.g.
    `analysis/tick_data/xauusdm_20260811_20260825/raw_ticks.csv`.
  * `--selftest` -> deterministic synthetic walk, no terminal and no file needed.

── What it measures, and the honest expectation ──

Direction is an INPUT this strategy cannot supply -- the recording shows it persisting in runs of
~3 with 71% persistence after a win, which is a signal not visible in the data. So a replay says
as much about the direction handed to it as about the grid. Sell-only on a day gold fell $139 is
close to a best case and proves nothing on its own; run `--matrix` for the distribution across
every session and both directions, which is the number that means something.

**Every result reports worst drawdown as % of balance beside its return.** A grid with no stop
loss can post a fine return on the way to an account that cannot survive the next path.

Analysis only. Read-only MT5 access; nothing here places, modifies or closes an order.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grid_state import CONTRACT, GridState, PARKED           # noqa: E402
from lot_position_engine import Engine                       # noqa: E402

SYMBOL = "XAUUSD"


# ---------------------------------------------------------------- tick sources

def load_mt5(symbol: str, start: dt.datetime, end: dt.datetime):
    """Read-only tick pull. tz-aware UTC in, arrays out."""
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()} "
                         "(terminal running? XauOrderPad server stopped?)")
    if mt5.symbol_info(symbol) is None:
        raise SystemExit(f"symbol {symbol} not found")
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    meta = {"point": info.point, "contract": info.trade_contract_size,
            "vol_min": info.volume_min, "vol_step": info.volume_step}
    parts, a = [], start
    while a < end:
        b = min(end, a + dt.timedelta(days=1))
        r = mt5.copy_ticks_range(symbol, a, b, mt5.COPY_TICKS_ALL)
        if r is not None and len(r):
            parts.append(r)
        a = b
    mt5.shutdown()
    if not parts:
        raise SystemExit(f"no ticks for {symbol} in {start:%Y-%m-%d %H:%M}Z..{end:%Y-%m-%d %H:%M}Z")
    allr = np.concatenate(parts)
    order = np.lexsort((allr["ask"], allr["bid"], allr["time_msc"]))
    allr = allr[order]
    keep = np.concatenate(([True], ~((np.diff(allr["time_msc"]) == 0) &
                                     (np.diff(allr["bid"]) == 0) &
                                     (np.diff(allr["ask"]) == 0))))
    allr = allr[keep]
    return (allr["time_msc"].astype("int64"), allr["bid"].astype(float),
            allr["ask"].astype(float), meta)


def load_csv(path: str, day: str | None = None):
    """Read a cached tick export; optionally keep only one UTC day."""
    ms, bid, ask = [], [], []
    with open(path, newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        for r in rdr:
            try:
                b, a = float(r["bid"]), float(r["ask"])
                t = int(r["time_msc"])
            except (KeyError, ValueError, TypeError):
                continue
            if a <= 0 or b <= 0 or a < b:
                continue
            if day and not r.get("iso_time_utc", "").startswith(day):
                continue
            ms.append(t); bid.append(b); ask.append(a)
    if not bid:
        raise SystemExit(f"no usable ticks in {path}" + (f" for {day}" if day else ""))
    return (np.array(ms, "int64"), np.array(bid), np.array(ask),
            {"point": 0.001, "contract": 100.0, "vol_min": 0.01, "vol_step": 0.01})


def synth(n=200_000, seed=7, spread=0.05, start=4000.0, drift=0.0):
    """Deterministic walk. `drift` in $/tick lets a test demand a specific direction."""
    rng = np.random.default_rng(seed)
    mid = start + np.cumsum(rng.normal(drift, 0.03, n))
    half = spread / 2.0
    ms = (np.arange(n, dtype="int64") * 200) + 1_700_000_000_000
    return ms, mid - half, mid + half, {"point": 0.001, "contract": 100.0,
                                        "vol_min": 0.01, "vol_step": 0.01}


# ---------------------------------------------------------------- the replay

def replay(ms, bid, ask, engine: Engine, direction: str, balance: float, meta: dict,
           *, hard_cap: int | None = None, commission_per_lot: float = 0.0,
           vol_k: float = 0.0, vol_window_s: float = 10.0,
           use_dual_exit: bool = False) -> dict:
    """Drive GridState over the ticks with an MT5-faithful broker.

    A buy fills at the ASK and closes on the BID; a sell is the mirror. The spread is therefore
    paid per position per round trip -- it is not a fee bolted on afterwards, it is the fill
    geometry, which is the only way a no-stop-loss grid's cost shows up honestly.
    """
    g = GridState(engine, direction, balance, hard_cap=hard_cap, use_dual_exit=use_dual_exit)
    # Volatility-scaled step: k x trailing high-low range over a TIME window. vol_k = 0 keeps the
    # engine's fixed add_step, so the static and dynamic arms run through identical code.
    steps = None
    if vol_k > 0:
        steps = rolling_range(ms, (bid + ask) / 2.0, vol_window_s) * vol_k
    vol_min, vol_step = meta["vol_min"], meta["vol_step"]
    open_ids: set[int] = set()
    nxt = [0]
    cycles: list[dict] = []
    equity_lo = balance
    peak_bal = balance
    max_dd_pct = 0.0
    blown_at = None

    def snap_lot(x: float) -> float:
        return round(math.floor(x / vol_step + 1e-9) * vol_step, 2)

    for j in range(len(bid)):
        b, a = float(bid[j]), float(ask[j])
        if steps is not None:
            s = float(steps[j])
            g.dynamic_step = s if s > 0 else None

        for act in g.on_tick(b, a, set(open_ids)):
            if act[0] == "open":
                _, side, lot, count = act
                lot = snap_lot(lot)
                if lot < vol_min - 1e-9:
                    g.phase = PARKED
                    g.park_reason = f"lot {lot} below broker minimum {vol_min}"
                    break
                fill = a if side == "buy" else b
                tickets = []
                for _ in range(count):
                    nxt[0] += 1
                    tickets.append(nxt[0])
                    open_ids.add(nxt[0])
                g.register_entries(tickets, fill, lot)
            elif act[0] == "close_all":
                c = g.cycle
                realised = g.basket_pnl(b, a)
                lots = sum(p.lot for p in g.positions.values())
                realised -= commission_per_lot * lots
                cycles.append({
                    "cycle": c.index, "side": c.side, "unit_lot": c.unit_lot,
                    "balance_open": round(c.balance_open, 2),
                    "positions": c.positions, "rungs": c.rungs, "depth_cap": c.depth_cap,
                    "target": round(c.target, 2), "realised": round(realised, 2),
                    "worst_pnl": round(c.worst_pnl, 2),
                    "worst_pct_of_balance": round(c.worst_pct_of_balance, 1),
                    "was_underwater": "yes" if c.was_underwater else "no",
                    "balance_after": round(c.balance_open + realised, 2),
                    "reason": act[1],
                })
                open_ids.clear()
                new_bal = c.balance_open + realised
                g.register_flat(new_bal)
                peak_bal = max(peak_bal, new_bal)

        if g.phase == PARKED:
            break

        # equity gate: a no-SL grid dies when open loss eats the account, not at a stop.
        if g.cycle is not None and g.positions:
            eq = g.balance + g.cycle.basket_pnl
            equity_lo = min(equity_lo, eq)
            if g.balance:
                max_dd_pct = max(max_dd_pct, 100.0 * max(0.0, -g.cycle.basket_pnl) / g.balance)
            if eq <= 0:
                blown_at = j
                break

    final = g.balance
    unclosed = None
    if g.cycle is not None and g.positions:
        final = g.balance + g.cycle.basket_pnl        # mark the survivor to market
        c = g.cycle
        unclosed = {"cycle": c.index, "positions": c.positions, "rungs": c.rungs,
                    "depth_cap": c.depth_cap, "unit_lot": c.unit_lot,
                    "balance_open": round(c.balance_open, 2),
                    "basket_pnl": round(c.basket_pnl, 2),
                    "worst_pnl": round(c.worst_pnl, 2),
                    "worst_pct_of_balance": round(c.worst_pct_of_balance, 1)}

    wins = sum(1 for c in cycles if c["realised"] > 0)
    return {
        "direction": direction, "start_balance": balance,
        "final_balance": round(final, 2),
        "realised_balance": round(g.balance, 2),
        "return_pct": round(100.0 * (final - balance) / balance, 1) if balance else None,
        "cycles": len(cycles), "wins": wins,
        "win_pct": round(100.0 * wins / len(cycles), 1) if cycles else None,
        "worst_dd_pct_of_balance": round(max_dd_pct, 1),
        "min_equity": round(equity_lo, 2),
        "max_depth": max((c["positions"] for c in cycles), default=0),
        "blown": "YES" if blown_at is not None else "no",
        "parked": g.park_reason,
        "refused": g.refused,
        "open_at_end": len(g.positions),
        "_rows": cycles,
        "_unclosed": unclosed,
    }


# ---------------------------------------------------------------- verification

def selftest() -> int:
    """The gates from the plan. A failure here invalidates every number downstream."""
    fails = 0
    eng = Engine()
    meta = {"point": 0.001, "contract": 100.0, "vol_min": 0.01, "vol_step": 0.01}

    print("1) NO FREE MONEY ON A FLAT MARKET")
    print("   A driftless walk must not pay. If it does, the bid/ask model is wrong and every")
    print("   result downstream is fiction.")
    tot = 0.0
    for seed in (7, 11, 23, 31, 47, 59):
        ms, b, a, m = synth(seed=seed, drift=0.0)
        r = replay(ms, b, a, eng, "sell", 1000.0, m)
        tot += r["return_pct"] or 0.0
        why = "parked" if r["parked"] else ("blown" if r["blown"] == "YES" else "ran out of ticks")
        print(f"   seed {seed:>3}: return {r['return_pct']:>+7.1f}%  cycles {r['cycles']:>3}  "
              f"worst dd {r['worst_dd_pct_of_balance']:>5.1f}%  ended: {why}")
    mean = tot / 6
    ok = mean < 15.0
    fails += 0 if ok else 1
    print(f"   mean across 6 driftless seeds: {mean:+.1f}%   "
          f"{'ok -- no systematic edge' if ok else 'FAIL -- implausible free money'}")

    print("\n2) SYMMETRY -- a sell grid on a path == a buy grid on the NEGATED path")
    print("   (mirroring is price negation with bid/ask swapped; reversing the series in TIME")
    print("    is not a direction mirror, which is what an earlier version of this gate got wrong)")
    for seed in (5, 17):
        ms, b, a, m = synth(seed=seed, drift=0.0)
        C = float(b[0])
        mb, ma = 2 * C - a, 2 * C - b          # negate; the ask becomes the bid
        sell = replay(ms, b, a, eng, "sell", 1000.0, m)
        buy = replay(ms, mb, ma, eng, "buy", 1000.0, m)
        d = abs((sell["return_pct"] or 0) - (buy["return_pct"] or 0))
        good = d < 0.5
        fails += 0 if good else 1
        print(f"   seed {seed:>3}: sell {sell['return_pct']:>+8.1f}%   "
              f"buy-on-negated {buy['return_pct']:>+8.1f}%   gap {d:.2f}pp  "
              f"{'ok' if good else 'FAIL'}")

    print("\n3) CLOSED FORM vs REALISED -- drawdown_at(n, lot) must price the basket the")
    print("   state machine actually builds")
    print("   The deep baskets are the ones that PARK, so the still-open cycle counts here too --")
    print("   looking only at closed cycles was why an earlier version read 'inconclusive'.")
    deep = []
    for seed, drift in ((3, 0.004), (13, 0.003), (29, 0.005)):
        ms, b, a, m = synth(seed=seed, drift=drift)   # steady adverse drift for a sell grid
        r = replay(ms, b, a, eng, "sell", 5000.0, m)
        deep += [c for c in r["_rows"] if c["positions"] >= 3]
        if r["_unclosed"] and r["_unclosed"]["positions"] >= 3:
            deep.append(r["_unclosed"])
    if not deep:
        print("   no multi-rung basket produced; INCONCLUSIVE"); fails += 1
    else:
        bad = 0
        for c in deep[:8]:
            pred = eng.drawdown_at(c["positions"], c["unit_lot"])
            act = abs(c["worst_pnl"])
            ratio = pred / act if act else float("inf")
            under = ratio < 0.9
            bad += 1 if under else 0
            print(f"   n={c['positions']:>3} lot {c['unit_lot']:>5.2f}  predicted "
                  f"{pred:>11,.2f}  worst actual {act:>11,.2f}  ratio {ratio:>5.2f}x"
                  f"{'  <-- UNDER-prices the basket' if under else ''}")
        print("   predicted >= actual is correct: the closed form prices one step past the last")
        print("   rung, the worst case at that depth. Only UNDER-pricing is a bug, because the")
        print("   risk budget is derived from it.")
        fails += 1 if bad else 0

    print("\n4) THE DEGENERATE CASE IS REPORTED, NOT HIDDEN")
    print("   Every run must end with a stated outcome -- completed cycles, a refusal, or a park.")
    print("   Silence would mean the driver clamped a zero-depth budget up to one position and")
    print("   pretended a grid ran.")
    for bal in (0.30, 1.00, 100.0, 1000.0):
        ms, b, a, m = synth(seed=9, drift=0.0)
        r = replay(ms, b, a, eng, "sell", bal, m)
        cap = eng.max_positions_for(bal)
        stated = r["refused"] or r["parked"] or (f"{r['cycles']} cycles" if r["cycles"] else None)
        print(f"   ${bal:>8,.2f}: lot {eng.lot_for(bal):>5.2f}  cap {cap:>2d}  -> {stated}")
        if stated is None:
            print("      <-- FAIL: ended with no cycles and no stated reason"); fails += 1

    print(f"\n{'ALL GATES PASS' if not fails else f'{fails} GATE(S) FAILED'}")
    return fails


# ---------------------------------------------------------------- cli

def rolling_range(ms, mid, seconds: float):
    """Trailing high-low range of `mid` over a TIME window, one value per tick.

    TIME-based, not tick-count-based, and that is the whole point. A tick-count window has a 90x
    variable span on this feed (2 to 180 ticks/s measured), so the same 300 ticks is 2.5 minutes in
    a quiet spell and 2 seconds in a news burst -- useless for a strategy whose cycles last ~56 s.

    Range rather than standard deviation because the video's LTP series is FRAME-sampled, not
    tick-sampled: a per-tick sigma computed from it is not comparable to one computed from real
    ticks, but a high-low range over a wall-clock window is. That comparability is what lets `k` be
    calibrated on the video and applied to live data.

    O(n) via a two-pointer window with a monotonic deque for the max and min.
    """
    from collections import deque
    n = len(mid)
    out = np.zeros(n)
    win = int(seconds * 1000)
    lo_q, hi_q = deque(), deque()
    left = 0
    for i in range(n):
        while lo_q and mid[lo_q[-1]] >= mid[i]:
            lo_q.pop()
        lo_q.append(i)
        while hi_q and mid[hi_q[-1]] <= mid[i]:
            hi_q.pop()
        hi_q.append(i)
        while ms[i] - ms[left] > win:
            if lo_q[0] == left:
                lo_q.popleft()
            if hi_q[0] == left:
                hi_q.popleft()
            left += 1
        out[i] = mid[hi_q[0]] - mid[lo_q[0]]
    return out


# ─── THE VOLATILITY-SCALED STEP WAS TESTED AND IT MAKES THINGS WORSE ───────────────────────────
#
# Measured over 13 sessions x 2 directions (dynamic_step_sweep.csv), $1,000 balance, T = 10 s:
#
#     step mode     dir    mean ret   median   BLOWN   parked
#     static        sell     -13.7%   -34.4%    0/13    13/13
#     static         buy     -33.8%   -38.3%    0/13    13/13
#     k=0.5         sell     -43.3%   -24.0%    4/13     9/13
#     k=0.5          buy     -41.2%   -54.5%    4/13     9/13
#     k=1.0         sell     -32.9%  -100.1%    7/13     6/13
#     k=1.0          buy     -35.1%   -70.9%    4/13     9/13
#
# The static step NEVER blew an account -- it parked, and parking CAPPED the loss. Widening the
# step blows 4-7 of 13 and does not improve the mean.
#
# WHY, and it is not fixable by tuning k: at depth n the tolerance is `rungs x step` (linear in
# step) while the drawdown carried is `lot x 100 x step x per_rung x r(r+1)/2` -- ALSO linear in
# step. Buying tolerance costs exactly proportional drawdown. There is no k that gains room without
# paying for it, so the trade is parking-vs-blowing, not a tunable optimum.
#
# The corollary matters more than the result: **PARKED is not a failure mode, it is the safety
# mechanism.** The tight step is what stops the account dying. Removing the constraint that causes
# parking removes the protection.
#
# Kept switchable (`vol_k`) so the finding stays reproducible, NOT because it is recommended.
# ───────────────────────────────────────────────────────────────────────────────────────────────
#
# Calibration anchor, from the operator-verified cycles (OPERATOR_VERIFIED.md):
#   observed rung gaps $0.070 / $0.053 / $0.037 / $0.000  -> median non-zero $0.053
#   the video session's whole price range                 -> $28.19
# so the operator's step was 0.19% of the session range. The detector's $0.18 is REFUTED and must
# not be used as the anchor -- the real grid was an order of magnitude tighter.
VIDEO_STEP = 0.053
VIDEO_RANGE = 28.19
STEP_AS_FRACTION_OF_RANGE = VIDEO_STEP / VIDEO_RANGE      # ~0.00188


def load_csv_by_day(path: str) -> dict:
    """One pass over a cached export, grouped into UTC days. 200 MB is too big to re-read."""
    days: dict[str, tuple[list, list, list]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                b, a, t = float(r["bid"]), float(r["ask"]), int(r["time_msc"])
            except (KeyError, ValueError, TypeError):
                continue
            if a < b or b <= 0:
                continue
            d = r.get("iso_time_utc", "")[:10]
            if not d:
                continue
            bucket = days.setdefault(d, ([], [], []))
            bucket[0].append(t); bucket[1].append(b); bucket[2].append(a)
    return {d: tuple(np.array(x) for x in v) for d, v in days.items()}


def matrix(path: str, engine_risks=(20, 44, 80, 100), balances=(1000.0,),
           min_ticks: int = 5_000) -> list[dict]:
    """Every session x both directions x each risk budget. The honest distribution.

    A single day proves nothing about a strategy whose outcome is decided by which way the first
    move went. This is the control that makes the Aug-28 number interpretable.
    """
    meta = {"point": 0.001, "contract": 100.0, "vol_min": 0.01, "vol_step": 0.01}
    days = load_csv_by_day(path)
    out = []
    for d in sorted(days):
        ms, bid, ask = days[d]
        if len(bid) < min_ticks:
            continue
        for risk in engine_risks:
            eng = Engine(risk_pct=risk)
            for bal in balances:
                for direction in ("sell", "buy"):
                    r = replay(ms, bid, ask, eng, direction, bal, meta)
                    out.append({
                        "day": d, "ticks": len(bid),
                        "range": round(float(bid.max() - bid.min()), 2),
                        "move": round(float(bid[-1] - bid[0]), 2),
                        "risk_pct": risk, "balance": bal, "direction": direction,
                        "lot": eng.lot_for(bal), "depth_cap": eng.max_positions_for(bal),
                        "adverse_tolerated": round(
                            eng.add_step * eng.rungs_for(eng.max_positions_for(bal)), 2),
                        "final": r["final_balance"], "return_pct": r["return_pct"],
                        "cycles": r["cycles"],
                        "worst_dd_pct": r["worst_dd_pct_of_balance"],
                        "outcome": ("parked" if r["parked"] else
                                    "blown" if r["blown"] == "YES" else "ran"),
                    })
    return out


def utc_day(s: str) -> tuple[dt.datetime, dt.datetime]:
    d = dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    return d, d + dt.timedelta(days=1)


def fmt(r: dict) -> str:
    return (f"{r['direction']:>5} ${r['start_balance']:>10,.2f} -> ${r['final_balance']:>12,.2f}  "
            f"{(r['return_pct'] or 0):>+9.1f}%   cycles {r['cycles']:>3} "
            f"win {str(r['win_pct'] or '-'):>5}%  depth {r['max_depth']:>3}  "
            f"worst dd {r['worst_dd_pct_of_balance']:>6.1f}%  blown {r['blown']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--matrix", action="store_true",
                    help="every session x direction x risk budget, from --csv")
    ap.add_argument("--day"); ap.add_argument("--from", dest="dfrom"); ap.add_argument("--to")
    ap.add_argument("--csv"); ap.add_argument("--symbol", default=SYMBOL)
    ap.add_argument("--direction", default="sell", choices=["buy", "sell", "both"])
    ap.add_argument("--balance", type=float, default=1000.0)
    ap.add_argument("--risk", type=float, default=None)
    ap.add_argument("--hard-cap", type=int, default=None)
    ap.add_argument("--out")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())

    if a.matrix:
        if not a.csv:
            ap.error("--matrix needs --csv")
        rows = matrix(a.csv, balances=(a.balance,))
        base = [r for r in rows if r["risk_pct"] == 44]
        print(f"{len(rows)} runs over {len({r['day'] for r in rows})} sessions, "
              f"balance ${a.balance:,.2f}\n")
        print(f"{'day':>11} {'range$':>8} {'move$':>8} |{'SELL ret%':>11} {'end':>6} "
              f"|{'BUY ret%':>10} {'end':>6}")
        for d in sorted({r["day"] for r in base}):
            s = next(r for r in base if r["day"] == d and r["direction"] == "sell")
            b = next(r for r in base if r["day"] == d and r["direction"] == "buy")
            print(f"{d:>11} {s['range']:>8.2f} {s['move']:>+8.2f} |"
                  f"{(s['return_pct'] or 0):>+10.1f}% {s['outcome']:>6} |"
                  f"{(b['return_pct'] or 0):>+9.1f}% {b['outcome']:>6}")
        ran = sum(1 for r in rows if r["outcome"] == "ran")
        print(f"\n  {ran}/{len(rows)} runs finished without parking or blowing")
        for dirn in ("sell", "buy"):
            v = [r["return_pct"] or 0 for r in base if r["direction"] == dirn]
            print(f"  {dirn}: mean {sum(v)/len(v):+.1f}%   median "
                  f"{sorted(v)[len(v)//2]:+.1f}%   best {max(v):+.1f}%   worst {min(v):+.1f}%")
        if a.out:
            p = os.path.join(a.out, "grid_replay_matrix.csv")
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
            print(f"\n-> grid_replay_matrix.csv ({len(rows)} rows) in {a.out}")
        return

    if a.csv:
        ms, bid, ask, meta = load_csv(a.csv, a.day)
        src = f"CSV {os.path.basename(a.csv)}" + (f" [{a.day}]" if a.day else "")
    elif a.day:
        s, e = utc_day(a.day)
        ms, bid, ask, meta = load_mt5(a.symbol, s, e)
        src = f"MT5 {a.symbol} {a.day} UTC"
    elif a.dfrom and a.to:
        s = dt.datetime.strptime(a.dfrom, "%Y-%m-%d").replace(tzinfo=dt.UTC)
        e = dt.datetime.strptime(a.to, "%Y-%m-%d").replace(tzinfo=dt.UTC)
        ms, bid, ask, meta = load_mt5(a.symbol, s, e)
        src = f"MT5 {a.symbol} {a.dfrom}..{a.to} UTC"
    else:
        ap.error("need --selftest, --day, --from/--to, or --csv")

    eng = Engine() if a.risk is None else Engine(risk_pct=a.risk)
    t0 = dt.datetime.fromtimestamp(ms[0] / 1000, dt.UTC)
    t1 = dt.datetime.fromtimestamp(ms[-1] / 1000, dt.UTC)
    print(f"{len(bid):,} ticks   {src}")
    print(f"  {t0:%Y-%m-%d %H:%M}Z .. {t1:%Y-%m-%d %H:%M}Z   "
          f"bid {bid.min():.2f}-{bid.max():.2f}  open {bid[0]:.2f} close {bid[-1]:.2f} "
          f"({bid[-1] - bid[0]:+.2f})   median spread ${np.median(ask - bid):.3f}")
    print(f"  engine: lot={eng.lot_a}*bal^{eng.lot_b}  step ${eng.add_step}  "
          f"risk {eng.risk_pct:.0f}%  TP {eng.take_profit_pct}% of balance  NO STOP LOSS\n")

    dirs = ["buy", "sell"] if a.direction == "both" else [a.direction]
    rows = []
    for d in dirs:
        r = replay(ms, bid, ask, eng, d, a.balance, meta, hard_cap=a.hard_cap)
        print(fmt(r))
        if r["parked"]:
            print(f"        PARKED: {r['parked']}")
        rows.append(r)

    if a.out:
        path = os.path.join(a.out, "grid_replay.csv")
        allrows = [dict(r, direction=res["direction"]) for res in rows for r in res["_rows"]]
        if allrows:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(allrows[0].keys()))
                w.writeheader(); w.writerows(allrows)
            print(f"\n-> grid_replay.csv ({len(allrows)} cycles) in {a.out}")


if __name__ == "__main__":
    main()

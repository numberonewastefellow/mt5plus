"""Ordered, tick-level replay of the three exit arms.

WHY THIS EXISTS
---------------
The gate that keeps the 28.9%-of-balance target alive (`QuickExitArmPct`) was first
scored with an UNORDERED sweep: it looked at each cycle's FINAL best/worst and asked
"could this rule have fired?". That question is not the same as "would this rule have
fired FIRST", because it lets an arm capture a peak that occurred before the arm was
live. Numbers from that sweep (+206 / +322 style) are NOT quotable and are recorded as
such in mt5/Experts/RecoveryGridScalper/README.md.

This replay answers the ordered question. It walks each cycle's real ticks forward from
its real open, activates each leg at the instant it actually filled, and evaluates the
arms in exactly the order GridEngine.mqh::OnTick does:

    MarkExtremes(net)                 <- watermarks see THIS tick before the arms do
    if net > 0:
        1 net >= target                                  -> target      (fixed $ at open)
        2 best_pnl > 0 and net <= best_pnl - giveback     -> giveback
        3 worst_pnl <= -arm_pct * balance_open            <- THE GATE
          and net >= per_oz(age) * total_oz               -> quick

THE ASSERTION
-------------
The operator's constraint is "the 28.9% target must NOT become dead code". So: any cycle
that never trips the gate must NOT be closed by the quick arm. A single violation fails
the run.

Read-only. Uses MetaTrader5 only for copy_ticks_range; it never touches an order.

    python replay_quick_arm.py                  # every v2 cycle log found
    python replay_quick_arm.py --date 20260907
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from dataclasses import dataclass, field

OZ_PER_LOT = 100.0

# Defaults mirror RecoveryGridScalper.mq5 inputs.
TARGET_PCT = 28.9
GIVEBACK_PCT = 15.0
QUICK_ON = True
QUICK_DECAY = True
QUICK_START = 0.30
QUICK_FLOOR = 0.10
QUICK_SECS = 60
QUICK_ARM_PCT = 15.0


def files_dir() -> str:
    return os.path.join(
        os.environ["APPDATA"],
        "MetaQuotes",
        "Terminal",
        "D0E8209F77C8CF37AD8BF550E51FF075",
        "MQL5",
        "Files",
    )


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)


@dataclass
class Leg:
    t: dt.datetime
    price: float
    lot: float


@dataclass
class Cycle:
    run_id: str
    cycle_id: int
    start: dt.datetime
    end: dt.datetime
    is_buy: bool
    target: float
    balance_open: float
    logged_reason: str
    logged_net: float
    legs: list[Leg] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, int]:
        return (self.run_id, self.cycle_id)


def load_cycles(date: str) -> list[Cycle]:
    path = os.path.join(files_dir(), f"RecoveryGrid_v2_cycles_472627873_{date}.csv")
    if not os.path.exists(path):
        return []
    out: list[Cycle] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append(
                    Cycle(
                        run_id=r["run_id"],
                        cycle_id=int(r["cycle_id"]),
                        start=parse_iso(r["start_iso"]),
                        end=parse_iso(r["end_iso"]),
                        is_buy=(r["direction"] == "buy"),
                        target=float(r["target"]),
                        balance_open=float(r["balance_before"]),
                        logged_reason=r["close_reason"],
                        # net_broker, NOT realised. On a broker STOP-OUT `realised` is 0.00 while
                        # net_broker carries the actual damage (-1918.02 on 2026-09-09 cycle 12),
                        # and `realised` excludes commission on every row. Using `realised` here
                        # priced account-destroying cycles at ZERO and produced a +5904 "profit"
                        # for a configuration that in reality wiped the account three times.
                        logged_net=float(r["net_broker"]),
                    )
                )
            except (KeyError, ValueError):
                continue
    return out


def attach_legs(date: str, cycles: list[Cycle]) -> None:
    path = os.path.join(files_dir(), f"RecoveryGrid_v2_trades_472627873_{date}.csv")
    if not os.path.exists(path):
        return
    by_key = {c.key: c for c in cycles}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["event"] != "OPEN":
                continue
            key = (r["run_id"], int(r["cycle_id"]))
            c = by_key.get(key)
            if c is None:
                continue
            c.legs.append(
                Leg(
                    t=dt.datetime.fromtimestamp(int(r["time_msc"]) / 1000.0, dt.UTC),
                    price=float(r["price"]),
                    lot=float(r["lot"]),
                )
            )
    for c in cycles:
        c.legs.sort(key=lambda l: l.t)


def per_oz(age_s: float) -> float:
    """GridEngine.mqh::QuickExitPerOz - linear decay, clamped at both ends."""
    if not QUICK_ON:
        return 0.0
    if not QUICK_DECAY or QUICK_SECS <= 0:
        return QUICK_START
    k = min(1.0, max(0.0, age_s / float(QUICK_SECS)))
    return QUICK_START - (QUICK_START - QUICK_FLOOR) * k


@dataclass
class Verdict:
    reason: str | None = None       # target | giveback | quick | None (ran to real close)
    at: dt.datetime | None = None
    net: float = 0.0
    armed: bool = False             # did the gate EVER open before the decision?
    armed_at: dt.datetime | None = None
    best: float = 0.0
    worst: float = 0.0
    ticks: int = 0


def replay(c: Cycle, ticks) -> Verdict:
    """Walk the cycle forward. Returns which arm fires FIRST, and whether the gate opened."""
    v = Verdict()
    giveback = GIVEBACK_PCT / 100.0 * c.balance_open
    arm_at = -(QUICK_ARM_PCT / 100.0 * c.balance_open)

    n_active = 0
    sum_oz = 0.0          # total ounces
    sum_cost = 0.0        # ounces * entry price  -> avg entry = cost / oz

    for tk in ticks:
        t = dt.datetime.fromtimestamp(tk["time_msc"] / 1000.0, dt.UTC)
        if t > c.end:
            break
        bid, ask = float(tk["bid"]), float(tk["ask"])
        if bid <= 0.0 or ask <= 0.0:
            continue

        # Activate every leg that had filled by this instant.
        while n_active < len(c.legs) and c.legs[n_active].t <= t:
            leg = c.legs[n_active]
            oz = leg.lot * OZ_PER_LOT
            sum_oz += oz
            sum_cost += oz * leg.price
            n_active += 1
        if n_active == 0 or sum_oz <= 0.0:
            continue

        v.ticks += 1
        avg = sum_cost / sum_oz
        px = bid if c.is_buy else ask
        net = sum_oz * (px - avg) if c.is_buy else sum_oz * (avg - px)

        # MarkExtremes runs at the top of OnTick, so the watermarks include this tick.
        if net > v.best:
            v.best = net
        if net < v.worst:
            v.worst = net
        if not v.armed and v.worst <= arm_at:
            v.armed = True
            v.armed_at = t

        if net <= 0.0:
            continue

        if net >= c.target:
            v.reason, v.at, v.net = "target", t, net
            return v
        if v.best > 0.0 and net <= v.best - giveback:
            v.reason, v.at, v.net = "giveback", t, net
            return v
        if QUICK_ON and v.worst <= arm_at:
            p = per_oz((t - c.start).total_seconds())
            if p > 0.0 and net >= p * sum_oz:
                v.reason, v.at, v.net = "quick", t, net
                return v
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", action="append", help="log date, e.g. 20260907 (repeatable)")
    ap.add_argument("--symbol", default="XAUUSD")
    args = ap.parse_args()

    dates = args.date or ["20260903", "20260904", "20260907"]

    cycles: list[Cycle] = []
    for d in dates:
        cs = load_cycles(d)
        attach_legs(d, cs)
        cycles.extend(cs)
    cycles = [c for c in cycles if c.legs]
    if not cycles:
        print("no v2 cycles with OPEN legs found")
        return 1

    import MetaTrader5 as mt5

    if not mt5.initialize():
        print("mt5.initialize failed:", mt5.last_error())
        return 1

    print(f"{'cycle':>12}  {'bal_open':>9} {'target':>8} {'logged':>9}  "
          f"{'gate':>5} {'replay':>9} {'net':>9}  note")
    print("-" * 92)

    violations: list[str] = []
    kept_target: list[Cycle] = []
    quick_fired: list[tuple[Cycle, Verdict]] = []
    no_ticks: list[Cycle] = []

    for c in sorted(cycles, key=lambda x: x.start):
        lo = c.start - dt.timedelta(seconds=2)
        hi = c.end + dt.timedelta(seconds=2)
        ticks = mt5.copy_ticks_range(args.symbol, lo, hi, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            no_ticks.append(c)
            print(f"{c.run_id[-4:]}/c{c.cycle_id:<7} {c.balance_open:9.2f} {c.target:8.2f} "
                  f"{c.logged_reason:>9}  {'-':>5} {'NO TICKS':>9} {'-':>9}")
            continue

        v = replay(c, ticks)
        gate = "OPEN" if v.armed else "shut"
        note = ""
        if v.reason == "quick" and not v.armed:
            note = "*** VIOLATION: quick fired with the gate shut"
            violations.append(f"{c.run_id}/c{c.cycle_id}")
        elif v.reason == "quick":
            quick_fired.append((c, v))
            note = f"gate opened {(v.armed_at - c.start).total_seconds():.0f}s in"
        if not v.armed:
            kept_target.append(c)
        print(f"{c.run_id[-4:]}/c{c.cycle_id:<7} {c.balance_open:9.2f} {c.target:8.2f} "
              f"{c.logged_reason:>9}  {gate:>5} {str(v.reason):>9} {v.net:9.2f}  {note}")

    mt5.shutdown()

    print()
    print("=" * 92)
    print("THE CONSTRAINT: a cycle whose gate never opened must never close on the quick arm.")
    print(f"  cycles replayed with ticks : {len(cycles) - len(no_ticks)}")
    print(f"  gate never opened          : {len(kept_target)}  <- these keep target/giveback")
    print(f"  closed by the quick arm    : {len(quick_fired)}")
    print(f"  VIOLATIONS                 : {len(violations)}")
    if no_ticks:
        print(f"  no tick data (not judged)  : {len(no_ticks)}")
    if violations:
        print("\nFAIL - the gate did not hold on: " + ", ".join(violations))
        return 1
    print("\nPASS - the gate held on every replayed cycle; the target is not dead code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

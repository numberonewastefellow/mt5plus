"""
Tick-level volume profiler for XAUUSD(m) -- runs on RAW ticks, played back as-is.

    ..\XauOrderPad\.venv\Scripts\python.exe tick_profiler.py [days]   # default 10

WHY THIS EXISTS
---------------
The other volume scripts here work on bars: vol_anomaly.py is M15+, and the
volume-profile block in order_flow_direction.py is M1. This one asks the finer
question: over the last ~10 days of RAW ticks, does a volume profiler throw any
signals?

THE HONEST VOLUME CAVEAT
------------------------
This Exness CFD gold feed carries NO traded volume and NO last price -- every
tick has last=0 and real_volume=0 (proven across the whole repo). So at tick
resolution the only "volume" that exists is:

  * tick ARRIVAL RATE  -- how many quote updates arrive per unit time. Bursts of
    ticks = bursts of market activity. This is the volume proxy for anomalies.
  * tick COUNT per PRICE LEVEL -- how many ticks printed at each price. This is
    the volume proxy for the volume PROFILE (POC / value area).

So a "signal" here marks an activity/size anomaly or a structural price level.
It is NOT a buy/sell call: the repo's own walk-forward found volume predicts the
SIZE of the next move (corr ~0.71-0.74) but NOT its direction. Everything below
is therefore direction-agnostic on purpose.

READ-ONLY. Pulls ticks via copy_ticks_range and computes. Never places an order.
Requires the local MT5 terminal running + logged in, and the XauOrderPad server
STOPPED (one MT5 connection per terminal).
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np

# Reuse the harness's MT5 tick loader (chunked copy_ticks_range, de-duped).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness.tickdata import load_ticks  # noqa: E402

# ---- knobs -----------------------------------------------------------------
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
RATE_WIN_MS = 60_000            # trailing window for the tick-rate volume proxy (60 s)
RVOL_THRESHOLDS = (4.0, 6.0, 8.0)   # same sweep as scalp_signal.py:45-49
EVENT_GAP_MS = 30_000          # collapse signal ticks < 30 s apart into ONE event
VP_BUCKET = 0.10               # $/oz price bucket for the volume profile
VALUE_AREA_FRAC = 0.70         # value area = 70% of ticks around POC (standard)
TOP_N = 15
HERE = os.path.dirname(os.path.abspath(__file__))


def robust_z(x: np.ndarray) -> np.ndarray:
    """MAD z-score -- outlier-resistant, same idea as vol_anomaly.robust_z."""
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    if mad == 0:
        mad = np.mean(np.abs(x - med)) or 1.0
    return (x - med) / (1.4826 * mad)


def value_area(prices: np.ndarray, bucket: float, frac: float):
    """POC + value-area high/low from a tick-count-by-price histogram."""
    pb = np.round(prices / bucket) * bucket
    uniq, counts = np.unique(pb, return_counts=True)
    poc = float(uniq[np.argmax(counts)])
    order = np.argsort(counts)[::-1]           # densest buckets first
    total = counts.sum()
    acc, chosen = 0, []
    for k in order:
        acc += counts[k]
        chosen.append(uniq[k])
        if acc >= frac * total:
            break
    chosen = np.array(chosen)
    return poc, float(chosen.max()), float(chosen.min())


def main() -> None:
    end = dt.datetime.now()
    start = end - dt.timedelta(days=DAYS)
    print(f"Pulling raw ticks: XAUUSD  {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M}  ({DAYS}d) ...")

    tk = load_ticks("XAUUSD", start, end, chunk_days=1)   # STOPS the shared MT5 conn on exit
    n = tk.n
    if n < 1000:
        print(f"Only {n} ticks -- too few to profile. Is the feed live / window on a weekend?")
        return

    t = tk.t_msc.astype("int64")          # unix ms, sorted+deduped by load_ticks
    mid = tk.mid
    span_h = (t[-1] - t[0]) / 3_600_000.0
    print(f"  {tk.symbol}: {n:,} ticks over {span_h:.1f}h "
          f"({t[0]//1000 and dt.datetime.utcfromtimestamp(t[0]/1000):%Y-%m-%d %H:%M} -> "
          f"{dt.datetime.utcfromtimestamp(t[-1]/1000):%Y-%m-%d %H:%M} UTC), "
          f"avg {n/max(span_h*3600,1):.1f} ticks/s")

    # ---- 1. tick-rate volume proxy (RAW, per-tick, no bar resampling) --------
    # rate[i] = number of ticks in the trailing RATE_WIN_MS ending at tick i.
    left = np.searchsorted(t, t - RATE_WIN_MS, side="left")
    rate = (np.arange(n) - left + 1).astype(float)

    # Deseasonalize by minute-of-day: gold activity is heavily session-driven
    # (London/NY), so raw rate is high mid-session and low overnight for reasons
    # that are NOT anomalies. Divide each tick's rate by the median rate of its
    # own minute-of-day slot -> rvol ~1 in a normal session, spikes stand out.
    minute_of_day = ((t // 1000) % 86400) // 60          # 0..1439, broker/UTC clock
    seasonal = np.ones(1440)
    order = np.argsort(minute_of_day, kind="stable")
    sm = minute_of_day[order]
    sr = rate[order]
    bounds = np.searchsorted(sm, np.arange(1441))
    for m in range(1440):
        a, b = bounds[m], bounds[m + 1]
        if b > a:
            seasonal[m] = np.median(sr[a:b]) or 1.0
    rvol = rate / seasonal[minute_of_day]
    z = robust_z(rate)

    # ---- 2. signals: rvol over threshold, collapsed into events --------------
    print("\n=== TICK-RATE ANOMALY SIGNALS (activity spikes) ===")
    print(f"    volume proxy = ticks in trailing {RATE_WIN_MS/1000:.0f}s, deseasonalized by minute-of-day")
    events_by_thr = {}
    for thr in RVOL_THRESHOLDS:
        hit = np.where(rvol >= thr)[0]
        if hit.size == 0:
            events_by_thr[thr] = np.array([], dtype=int)
            print(f"  rvol >= {thr:>4.1f} : 0 signals")
            continue
        # keep the first tick of each burst (>= EVENT_GAP_MS since the last kept one)
        keep = [hit[0]]
        for i in hit[1:]:
            if t[i] - t[keep[-1]] > EVENT_GAP_MS:
                keep.append(i)
        keep = np.array(keep)
        events_by_thr[thr] = keep
        per_day = keep.size / max(span_h / 24.0, 1e-9)
        print(f"  rvol >= {thr:>4.1f} : {keep.size:4d} signals  (~{per_day:.1f}/day)")

    # top events at the mid threshold
    base_thr = RVOL_THRESHOLDS[1]
    ev = events_by_thr[base_thr]
    if ev.size:
        top = ev[np.argsort(rvol[ev])[::-1][:TOP_N]]
        print(f"\n  Top {min(TOP_N, top.size)} spikes (rvol >= {base_thr}):")
        print(f"  {'time (UTC)':<20} {'mid':>10} {'spread':>7} {'rate/60s':>9} {'rvol':>7} {'z':>6}")
        for i in sorted(top, key=lambda k: t[k]):
            ts = dt.datetime.utcfromtimestamp(t[i] / 1000)
            print(f"  {ts:%Y-%m-%d %H:%M:%S}  {mid[i]:>10.3f} {tk.spread[i]:>7.3f} "
                  f"{rate[i]:>9.0f} {rvol[i]:>7.1f} {z[i]:>6.1f}")

    # ---- 3. tick-based VOLUME PROFILE (count-per-price) ----------------------
    print("\n=== TICK VOLUME PROFILE (count per price level) ===")
    poc, vah, val = value_area(mid, VP_BUCKET, VALUE_AREA_FRAC)
    print(f"  Whole window: POC {poc:.2f}   VAH {vah:.2f}   VAL {val:.2f}   "
          f"(value area = {VALUE_AREA_FRAC:.0%} of ticks, ${VP_BUCKET:.2f} buckets)")
    days = (t // 1000) // 86400
    print(f"  {'date':<12} {'POC':>9} {'VAH':>9} {'VAL':>9} {'ticks':>9}")
    for d in np.unique(days):
        sel = days == d
        if sel.sum() < 500:
            continue
        p, h, l = value_area(mid[sel], VP_BUCKET, VALUE_AREA_FRAC)
        dstr = dt.datetime.utcfromtimestamp(d * 86400).strftime("%Y-%m-%d")
        print(f"  {dstr:<12} {p:>9.2f} {h:>9.2f} {l:>9.2f} {sel.sum():>9,}")

    # ---- 4. write signal events CSV -----------------------------------------
    out = os.path.join(HERE, f"tick_signals_{start:%Y%m%d}_{end:%Y%m%d}.csv")
    ev = events_by_thr[RVOL_THRESHOLDS[0]]      # widest set (lowest threshold)
    with open(out, "w", encoding="ascii") as f:
        f.write("iso_time_utc,mid,bid,ask,spread,rate_60s,rvol,z\n")
        for i in sorted(ev):
            ts = dt.datetime.utcfromtimestamp(t[i] / 1000)
            f.write(f"{ts:%Y-%m-%dT%H:%M:%S}Z,{mid[i]:.3f},{tk.bid[i]:.3f},{tk.ask[i]:.3f},"
                    f"{tk.spread[i]:.3f},{rate[i]:.0f},{rvol[i]:.2f},{z[i]:.2f}\n")
    print(f"\nWrote {ev.size} signal rows -> {out}")

    # ---- verdict -------------------------------------------------------------
    n_mid = events_by_thr[RVOL_THRESHOLDS[1]].size
    print("\n=== VERDICT ===")
    if n_mid == 0:
        print(f"  NO signals at rvol>={RVOL_THRESHOLDS[1]} over {DAYS}d. The feed's activity was")
        print("  too uniform for the tick-rate profiler to flag anything at this threshold.")
    else:
        print(f"  YES -- {n_mid} activity-spike signals at rvol>={RVOL_THRESHOLDS[1]} over {DAYS}d,")
        print("  plus per-day POC/value-area levels above. Reminder: these mark WHERE activity")
        print("  concentrated (size/structure), not a trade direction.")


if __name__ == "__main__":
    main()

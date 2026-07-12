r"""
XAUUSD volume-spike -> news-window dataframe  (foundation for news correlation)
==============================================================================

Goal: for every volume spike, record WHEN it happened and WHICH WAY price then
moved, and tag it with the US macro-release window it falls in. This is the
labelled dataset we later join against a real economic calendar (FOMC/CPI/NFP/
Fed speakers) to test whether news explains direction.

No external data needed here. The broker->ET clock offset is CALIBRATED from the
weekend gap (FX closes Fri 17:00 New York), so we don't guess the timezone.

Outputs a CSV; prints the spike distribution by news window + directional bias.

Run:  ../XauOrderPad/.venv/Scripts/python.exe news_correlation.py [start end]
"""
import csv
import datetime as dt
import os
import sys
from math import erf, sqrt
import numpy as np
import MetaTrader5 as mt5

START = dt.datetime(2026, 3, 24)
END   = dt.datetime.now()
if len(sys.argv) >= 3:
    START = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
    END = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")
RVOL_WIN = 90
RVOL_THR = 8.0            # a "spike" = >= 8x its recent typical minute volume
DESEAS_WIN = 90
FWD_15, FWD_60 = 15, 60   # look-ahead bars for realized direction (minutes)
CLUSTER_GAP_MIN = 30      # spikes within this gap collapse into one news "event"

if not mt5.initialize():
    raise SystemExit("INIT_FAIL: %s" % (mt5.last_error(),))
symbol = "XAUUSD"
if mt5.symbol_info(symbol) is None:
    for s in (mt5.symbols_get("XAUUSD*") or []):
        symbol = s.name; break
mt5.symbol_select(symbol, True)


def fetch_rates(tf, start, end):
    parts, a = [], start
    while a < end:
        b = min(end, a + dt.timedelta(days=25))
        r = mt5.copy_rates_range(symbol, tf, a, b)
        if r is not None and len(r):
            parts.append(r)
        a = b
    if not parts:
        return None
    allr = np.concatenate(parts)
    _, idx = np.unique(allr['time'], return_index=True)
    return allr[idx]


r = fetch_rates(mt5.TIMEFRAME_M1, START, END)
if r is None:
    raise SystemExit("no M1 data")
t = r['time'].astype('int64')
o, h, l, c = (r[k].astype(float) for k in ('open', 'high', 'low', 'close'))
vol = r['tick_volume'].astype(float)
n = len(c)
# Use UTC rendering of the server epoch so it's deterministic (no local-tz double convert)
BK = [dt.datetime.utcfromtimestamp(int(x)) for x in t]   # "broker wall clock"

# ---- calibrate broker->ET offset from the weekend gap (FX closes Fri 17:00 NY) ----
gaps = [(BK[i], BK[i + 1]) for i in range(n - 1)
        if (t[i + 1] - t[i]) > 24 * 3600]                # >24h gap = weekend
# the bar BEFORE each weekend gap is ~Friday 16:59 ET; median its broker-hour
fri_hours = [g[0].hour + g[0].minute / 60 for g in gaps]
broker_close_h = float(np.median(fri_hours)) if fri_hours else 21.0
ET_OFFSET = round(broker_close_h - 17.0)                 # hours to subtract: broker -> ET
print(f"Weekend gaps found: {len(gaps)} | Fri close in broker time ~{broker_close_h:.1f}h "
      f"-> broker->ET offset = -{ET_OFFSET}h (ET = broker - {ET_OFFSET})")


def to_et(bk):
    return bk - dt.timedelta(hours=ET_OFFSET)


# ---- indicators ----
rvol = np.full(n, np.nan)
for i in range(RVOL_WIN, n):
    b = np.median(vol[i - RVOL_WIN:i]); rvol[i] = vol[i] / b if b > 0 else np.nan
# time-of-day deseasonalized z (rank spike rarity independent of session)
slot = np.array([bk.hour * 60 + bk.minute for bk in BK])
slot_med = {s: (np.median(vol[slot == s]) or np.median(vol)) for s in np.unique(slot)}
deseason = vol / np.array([slot_med[s] for s in slot])
med = np.median(deseason); mad = np.median(np.abs(deseason - med)) or 1.0
z_deseason = 0.6745 * (deseason - med) / mad


# ---- REAL 2026 US macro calendar (from federalreserve.gov + BLS, retrieved 2026-07) ----
# FOMC statement = 14:00 ET (presser 14:30); CPI/NFP = 08:30 ET.
CALENDAR = [
    ("2026-04-03", "NFP — March jobs", "data"),
    ("2026-04-10", "CPI — March", "data"),
    ("2026-04-29", "FOMC decision + presser", "fomc"),
    ("2026-05-01", "NFP — April jobs", "data"),
    ("2026-05-12", "CPI — April", "data"),
    ("2026-06-05", "NFP — May jobs", "data"),
    ("2026-06-10", "CPI — May", "data"),
    ("2026-06-17", "FOMC decision + presser (SEP)", "fomc"),
    ("2026-07-02", "NFP — June jobs", "data"),
]
_WINDOWS = {"data": (8 * 60 + 25, 9 * 60 + 15),      # reaction can run ~45 min
            "fomc": (13 * 60 + 55, 15 * 60 + 0)}     # statement 14:00 -> presser 14:30+


def match_event(et):
    """Return the confirmed calendar event whose reaction window contains this ET time."""
    ds = et.strftime("%Y-%m-%d"); hm = et.hour * 60 + et.minute
    for d, name, typ in CALENDAR:
        if d == ds:
            lo, hi = _WINDOWS[typ]
            if lo <= hm <= hi:
                return name
    return None


def news_window(et):
    """Map an ET datetime to the US macro-release window it sits in."""
    hm = et.hour * 60 + et.minute
    def near(target, tol=6):
        return abs(hm - target) <= tol
    if near(8 * 60 + 30, 7):
        return "US 08:30 ET data (NFP/CPI/PPI/Retail/GDP/Claims)"
    if near(10 * 60, 6):
        return "US 10:00 ET data (ISM/JOLTS/Confidence/Home)"
    if near(14 * 60, 8):
        return "14:00 ET (FOMC statement / minutes days)"
    if near(9 * 60 + 30, 6):
        return "09:30 ET US equity open"
    if near(13 * 60 + 30, 6):
        return "13:30 ET (Fed speakers / auctions)"
    if 3 * 60 <= hm <= 4 * 60:
        return "~03:00 ET London open"
    return "other / unscheduled"


# ---- collect spikes ----
spikes = []
for i in range(RVOL_WIN, n - 1):
    if np.isnan(rvol[i]) or rvol[i] < RVOL_THR:
        continue
    bk = BK[i]; et = to_et(bk)
    j15 = min(n - 1, i + FWD_15); j60 = min(n - 1, i + FWD_60)
    mv15 = c[j15] - c[i]; mv60 = c[j60] - c[i]
    # max favorable / adverse over next 60 min
    fh = h[i + 1:j60 + 1]; fl = l[i + 1:j60 + 1]
    mfe = (fh.max() - c[i]) if len(fh) else 0.0
    mae = (c[i] - fl.min()) if len(fl) else 0.0
    spikes.append(dict(i=i, bk=bk, et=et, wd=bk.strftime("%a"),
                       rvol=round(float(rvol[i]), 1), z=round(float(z_deseason[i]), 1),
                       mv15=round(float(mv15), 2), mv60=round(float(mv60), 2),
                       dir15="UP" if mv15 > 0 else "DOWN" if mv15 < 0 else "FLAT",
                       mfe=round(float(mfe), 2), mae=round(float(mae), 2),
                       window=news_window(et), event=match_event(et)))

# ---- collapse clustered spikes into distinct news "events" (keep the strongest) ----
events = []
for s in spikes:
    if events and (s['bk'] - events[-1]['bk']).total_seconds() <= CLUSTER_GAP_MIN * 60:
        if s['rvol'] > events[-1]['rvol']:
            # keep strongest bar's stats but preserve event start time
            start_bk = events[-1]['bk']; start_et = events[-1]['et']
            events[-1] = {**s, 'bk': start_bk, 'et': start_et}
        continue
    events.append(dict(s))

print(f"\n{symbol} | {START:%Y-%m-%d} -> {END:%Y-%m-%d} | M1 bars={n}")
print(f"Volume spikes (RVOL>= {RVOL_THR}x): {len(spikes)} raw  ->  {len(events)} distinct events "
      f"(clustered within {CLUSTER_GAP_MIN}min)\n")

# ---- CONFIRMED news matches (spike landed in a real FOMC/CPI/NFP window) ----
matched = [e for e in events if e['event']]
print(f"### CONFIRMED NEWS-DRIVEN SPIKES: {len(matched)} of {len(events)} events matched a "
      f"scheduled FOMC/CPI/NFP release")
print(f"  {'broker_time':17}{'ET_time':17}{'RVOL':>6}{'move60m$':>10}{'dir':>6}  event")
for e in sorted(matched, key=lambda x: x['bk']):
    print(f"  {e['bk']:%Y-%m-%d %H:%M}  {e['et']:%Y-%m-%d %H:%M}{e['rvol']:>6}"
          f"{e['mv60']:>10.2f}{('UP' if e['mv60']>0 else 'DOWN'):>6}  {e['event']}")
# how many scheduled events in range actually produced a detectable spike?
sched_in_range = [d for d, _, _ in CALENDAR
                  if START <= dt.datetime.strptime(d, "%Y-%m-%d") <= END]
hit_dates = {e['et'].strftime("%Y-%m-%d") for e in matched}
print(f"  -> {len(hit_dates)}/{len(sched_in_range)} scheduled release days in range produced a >= {RVOL_THR}x spike\n")

# ---- event-centric: peak volume + gold reaction AT each scheduled release (any size) ----
print("### VOLUME & GOLD REACTION AT EVERY SCHEDULED RELEASE (peak RVOL in the 60-min window):")
print(f"  {'date':12}{'event':34}{'peakRVOL':>9}{'move60m$':>10}{'dir':>6}")
EPOCH0 = dt.datetime(1970, 1, 1)
_REL = {"data": (8, 30), "fomc": (14, 0)}
for d, name, typ in CALENDAR:
    day = dt.datetime.strptime(d, "%Y-%m-%d")
    if not (START <= day <= END):
        continue
    rh, rm = _REL[typ]
    ev_utc = dt.datetime(day.year, day.month, day.day, rh, rm) + dt.timedelta(hours=ET_OFFSET)
    p0 = (ev_utc - EPOCH0).total_seconds()
    i0 = int(np.searchsorted(t, p0)); i1 = int(np.searchsorted(t, p0 + 3600))
    if i0 >= n or i1 <= i0:
        print(f"  {d:12}{name:34}{'(no data)':>9}"); continue
    seg = rvol[i0:i1]
    pk = float(np.nanmax(seg)) if np.any(~np.isnan(seg)) else float('nan')
    mv = float(c[min(n - 1, i1)] - c[i0])
    print(f"  {d:12}{name:34}{pk:>9.1f}{mv:>10.2f}{('UP' if mv > 0 else 'DOWN'):>6}")
print()

# ---- distribution by news window + directional bias ----
from collections import defaultdict
byw = defaultdict(list)
for e in events:
    byw[e['window']].append(e)
print(f"{'news window':52}{'events':>7}{'%up(60m)':>9}{'avg|move|60m$':>14}")
print("-" * 82)
for w, es in sorted(byw.items(), key=lambda kv: -len(kv[1])):
    ups = sum(1 for e in es if e['mv60'] > 0)
    pct_up = 100 * ups / len(es)
    avg_abs = np.mean([abs(e['mv60']) for e in es])
    print(f"{w:52}{len(es):>7}{pct_up:>8.0f}%{avg_abs:>14.2f}")

# ---- CSV ----
ds, de = START.strftime('%Y%m%d'), END.strftime('%Y%m%d')
OUTDIR = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(OUTDIR, f"volume_spikes_news_frame_{ds}_{de}.csv")
with open(path, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['# symbol', symbol, 'RVOL_thr', RVOL_THR, 'broker_to_ET_offset_h', ET_OFFSET,
                'data_start', f"{START:%Y-%m-%d}", 'data_end', f"{END:%Y-%m-%d}"])
    w.writerow(['broker_time', 'ET_time', 'weekday', 'rvol', 'deseason_z',
                'move_15m_usd', 'dir_15m', 'move_60m_usd', 'mfe_60m_usd', 'mae_60m_usd',
                'matched_event', 'news_window'])
    for e in events:
        w.writerow([f"{e['bk']:%Y-%m-%d %H:%M}", f"{e['et']:%Y-%m-%d %H:%M}", e['wd'],
                    e['rvol'], e['z'], e['mv15'], e['dir15'], e['mv60'], e['mfe'], e['mae'],
                    e['event'] or '', e['window']])
print(f"\nCSV: {path}")

# ---- top events, for eyeballing against a calendar ----
print("\nTop 15 strongest spike events (check these against FOMC/CPI/NFP dates):")
print(f"  {'broker_time':17}{'ET_time':17}{'wd':>4}{'RVOL':>6}{'mv60$':>8}  window")
for e in sorted(events, key=lambda x: -x['rvol'])[:15]:
    print(f"  {e['bk']:%Y-%m-%d %H:%M}  {e['et']:%Y-%m-%d %H:%M}  {e['wd']:>3}"
          f"{e['rvol']:>6}{e['mv60']:>8.2f}  {e['window']}")

mt5.shutdown()

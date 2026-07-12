r"""Cross-check the user's eyeballed 15-min moves against actual volume.
Pull M15 + M1, and for each cited timestamp show OHLC, tick-volume and RVOL,
so we can say whether each move was a genuine volume anomaly."""
import datetime as dt
import numpy as np
import MetaTrader5 as mt5

mt5.initialize()
symbol = "XAUUSD"
if mt5.symbol_info(symbol) is None:
    for s in (mt5.symbols_get("XAUUSD*") or []):
        symbol = s.name; break
mt5.symbol_select(symbol, True)

def pull(tf, a, b):
    r = mt5.copy_rates_range(symbol, tf, a, b)
    return r

A, B = dt.datetime(2026, 6, 20), dt.datetime(2026, 7, 4)
m15 = pull(mt5.TIMEFRAME_M15, A, B)
m1 = pull(mt5.TIMEFRAME_M1, A, B)
t15 = m15['time'].astype('int64'); v15 = m15['tick_volume'].astype(float)
o,h,l,c = (m15[k].astype(float) for k in ('open','high','low','close'))
D15 = [dt.datetime.utcfromtimestamp(int(x)) for x in t15]
# RVOL on M15: vol / trailing 96-bar (1 day) median
rvol15 = np.full(len(v15), np.nan)
for i in range(96, len(v15)):
    base = np.median(v15[i-96:i]); rvol15[i] = v15[i]/base if base>0 else np.nan
# time-of-day deseasonalized z on M15
slot = np.array([d.hour*60+d.minute for d in D15])
smed = {s:(np.median(v15[slot==s]) or np.median(v15)) for s in np.unique(slot)}
des = v15/np.array([smed[s] for s in slot])
med=np.median(des); mad=np.median(np.abs(des-med)) or 1.0
z15 = 0.6745*(des-med)/mad

t1 = m1['time'].astype('int64'); v1 = m1['tick_volume'].astype(float)
rvol1 = np.full(len(v1), np.nan)
for i in range(90, len(v1)):
    base=np.median(v1[i-90:i]); rvol1[i]=v1[i]/base if base>0 else np.nan

def idx15(when):
    return int(np.searchsorted(t15, when.replace(tzinfo=dt.timezone.utc).timestamp()))

def m1_peak(a, b):
    lo=int(np.searchsorted(t1, a.replace(tzinfo=dt.timezone.utc).timestamp()))
    hi=int(np.searchsorted(t1, b.replace(tzinfo=dt.timezone.utc).timestamp()))
    seg=rvol1[lo:hi]; seg=seg[~np.isnan(seg)]
    return float(np.nanmax(rvol1[lo:hi])) if len(seg) else float('nan')

EVENTS = [
    ("Jun 24 19:00-20:00 (3974->4000)", dt.datetime(2026,6,24,19,0), dt.datetime(2026,6,24,20,0)),
    ("Jun 25 12:00-13:15 (3980->4029)", dt.datetime(2026,6,25,12,0), dt.datetime(2026,6,25,13,15)),
    ("Jun 25 15:15 (4008->4040)",       dt.datetime(2026,6,25,15,0), dt.datetime(2026,6,25,16,0)),
    ("Jul 01 08:45 (3975->4031)",       dt.datetime(2026,7,1,8,30),  dt.datetime(2026,7,1,9,30)),
    ("Jul 01 11:00 (-> 4105)",          dt.datetime(2026,7,1,11,0),  dt.datetime(2026,7,1,12,0)),
    ("Jul 02 12:00-12:15 big green",    dt.datetime(2026,7,2,12,0),  dt.datetime(2026,7,2,12,45)),
    ("Jul 02 23:45 big candles",        dt.datetime(2026,7,2,23,30), dt.datetime(2026,7,3,0,15)),
]

for label, a, b in EVENTS:
    i0, i1 = idx15(a), idx15(b)+1
    if i0 >= len(t15):
        print(f"\n### {label}: no data"); continue
    print(f"\n### {label}")
    print(f"  {'time':17}{'open':>9}{'high':>9}{'low':>9}{'close':>9}{'volume':>8}{'RVOL':>6}{'zDes':>6}")
    for i in range(i0, min(i1, len(t15))):
        flag = "  <== SPIKE" if (rvol15[i]>=3 or z15[i]>=5) else ("  <- high" if rvol15[i]>=2 else "")
        print(f"  {D15[i]:%Y-%m-%d %H:%M}{o[i]:>9.2f}{h[i]:>9.2f}{l[i]:>9.2f}{c[i]:>9.2f}"
              f"{int(v15[i]):>8}{rvol15[i]:>6.1f}{z15[i]:>6.1f}{flag}")
    mv = c[min(i1,len(t15))-1]-o[i0]
    print(f"  window move: {mv:+.2f} $ | peak M1 RVOL in window: {m1_peak(a,b):.1f}x")

mt5.shutdown()

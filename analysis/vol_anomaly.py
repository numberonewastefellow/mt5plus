"""
XAUUSD VOLUME-ANOMALY analysis: 2026-05-24 -> now.

Detect anomalies from VOLUME ONLY (4 independent algorithms), across M15/H1/H4/D1.
Price is used *afterwards* only to VALIDATE that a volume anomaly => big price move.

Volume source: MT5 tick_volume (real_volume is 0 for Exness CFD gold).
"""
import datetime as dt
import numpy as np
import csv, os
import MetaTrader5 as mt5

OUT = os.path.dirname(os.path.abspath(__file__))
START = dt.datetime(2026, 5, 24)
END   = dt.datetime.now()

if not mt5.initialize():
    raise SystemExit("INIT_FAIL: %s" % (mt5.last_error(),))

symbol = "XAUUSD"
if mt5.symbol_info(symbol) is None:
    for s in (mt5.symbols_get("XAUUSD*") or []):
        symbol = s.name; break
mt5.symbol_select(symbol, True)
si = mt5.symbol_info(symbol)
POINT = si.point
CONTRACT = si.trade_contract_size  # oz per lot, for $ context
print("SYMBOL:", symbol, "point:", POINT, "contract_size:", CONTRACT)

TFS = {
    "M15": mt5.TIMEFRAME_M15,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

def robust_z(x):
    """MAD-based robust z-score (median/MAD). Resistant to the very spikes we hunt."""
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    if mad == 0:
        mad = np.mean(np.abs(x - med)) or 1.0
    return 0.6745 * (x - med) / mad

def rolling_median(x, w):
    out = np.full(len(x), np.nan)
    for i in range(len(x)):
        lo = max(0, i - w)
        if i - lo >= max(5, w // 3):
            out[i] = np.median(x[lo:i])  # trailing, excludes current bar
    return out

def analyze(tf_name, tf):
    rates = mt5.copy_rates_range(symbol, tf, START, END)
    if rates is None or len(rates) == 0:
        print(f"[{tf_name}] no data: {mt5.last_error()}"); return []
    t   = rates['time'].astype('int64')
    o,h,l,c = rates['open'], rates['high'], rates['low'], rates['close']
    vol = rates['tick_volume'].astype(float)
    rvol_real = rates['real_volume'].astype(float)
    dtimes = [dt.datetime.fromtimestamp(int(x)) for x in t]

    n = len(vol)
    # ---------- Algo 1: robust global z-score on raw volume ----------
    z_glob = robust_z(vol)

    # ---------- Algo 2: time-of-day DESEASONALIZED z-score ----------
    # Each bar normalized by the median volume of its own intraday slot,
    # so London/NY session bulges are removed and only genuine surprises remain.
    if tf_name == "D1":
        slot = np.array([d.weekday() for d in dtimes])          # per weekday
    elif tf_name == "H4":
        slot = np.array([d.hour for d in dtimes])
    elif tf_name == "H1":
        slot = np.array([d.hour for d in dtimes])
    else:  # M15
        slot = np.array([d.hour * 60 + d.minute for d in dtimes])
    slot_med = {}
    for s in np.unique(slot):
        m = np.median(vol[slot == s])
        slot_med[s] = m if m > 0 else np.median(vol)
    expected = np.array([slot_med[s] for s in slot])
    deseason = vol / expected                                    # 1.0 == typical for that slot
    z_deseason = robust_z(deseason)

    # ---------- Algo 3: relative volume vs trailing rolling median ----------
    w = {"M15": 96, "H1": 48, "H4": 30, "D1": 10}[tf_name]       # ~1-2 sessions/weeks
    rmed = rolling_median(vol, w)
    rvol = vol / rmed

    # ---------- Algo 4: bar-over-bar volume surge (ROC) ----------
    vroc = np.full(n, np.nan)
    vroc[1:] = vol[1:] / np.maximum(vol[:-1], 1)

    # ---------- Composite anomaly score (VOLUME ONLY) ----------
    def nz(a):
        a = np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0); return a
    score = (nz(np.clip(z_glob, 0, None)) * 1.0 +
             nz(np.clip(z_deseason, 0, None)) * 1.6 +          # deseasonalized weighted most
             nz(np.clip((rvol - 1), 0, None)) * 1.2 +
             nz(np.clip((vroc - 1), 0, None)) * 0.6)

    # ---------- PRICE-IMPACT VALIDATION (computed AFTER detection) ----------
    bar_range_usd = (h - l)                                     # $ move within the bar (1 oz)
    bar_body_usd  = np.abs(c - o)
    fwd = {"M15": 4, "H1": 3, "H4": 2, "D1": 1}[tf_name]        # look-ahead bars
    fwd_move = np.full(n, np.nan)
    for i in range(n):
        j = min(n - 1, i + fwd)
        fwd_move[i] = c[j] - c[i]

    rows = []
    for i in range(n):
        rows.append(dict(
            tf=tf_name, time=dtimes[i].strftime("%Y-%m-%d %H:%M"),
            weekday=dtimes[i].strftime("%a"),
            vol=int(vol[i]), real_vol=int(rvol_real[i]),
            z_glob=round(float(z_glob[i]), 2),
            z_deseason=round(float(z_deseason[i]), 2),
            rvol=round(float(rvol[i]), 2) if not np.isnan(rvol[i]) else None,
            vroc=round(float(vroc[i]), 2) if not np.isnan(vroc[i]) else None,
            score=round(float(score[i]), 2),
            bar_range_usd=round(float(bar_range_usd[i]), 2),
            bar_body_usd=round(float(bar_body_usd[i]), 2),
            fwd_move_usd=round(float(fwd_move[i]), 2),
        ))
    return rows

all_rows = {}
for name, tf in TFS.items():
    rows = analyze(name, tf)
    all_rows[name] = rows
    print(f"[{name}] bars={len(rows)}  real_volume_nonzero={sum(1 for r in rows if r['real_vol']>0)}")

# ---- Report: top anomalies per timeframe, ranked purely by volume score ----
for name in TFS:
    rows = all_rows[name]
    if not rows: continue
    topN = 12 if name in ("M15","H1") else 8
    ranked = sorted(rows, key=lambda r: r['score'], reverse=True)[:topN]
    print("\n" + "="*118)
    print(f"### {name} — TOP {topN} VOLUME ANOMALIES (ranked by volume-only composite score)")
    print(f"{'time':16} {'wd':3} {'volume':>8} {'zGlob':>6} {'zDesea':>7} {'RVOL':>6} {'VROC':>6} {'SCORE':>6} | "
          f"{'barRange$':>9} {'body$':>7} {'fwdMove$':>8}")
    print("-"*118)
    for r in ranked:
        print(f"{r['time']:16} {r['weekday']:3} {r['vol']:>8} {r['z_glob']:>6} {r['z_deseason']:>7} "
              f"{str(r['rvol']):>6} {str(r['vroc']):>6} {r['score']:>6} | "
              f"{r['bar_range_usd']:>9} {r['bar_body_usd']:>7} {r['fwd_move_usd']:>8}")

    # correlation check: does volume score relate to |price move|?
    sc = np.array([r['score'] for r in rows])
    mv = np.array([abs(r['bar_range_usd']) for r in rows])
    if len(sc) > 5 and np.std(sc) > 0 and np.std(mv) > 0:
        cc = np.corrcoef(sc, mv)[0, 1]
        # top-decile vs rest avg range
        thr = np.percentile(sc, 90)
        hi = mv[sc >= thr]; rest = mv[sc < thr]
        print(f"  corr(volume-score, bar-range$) = {cc:.2f} | "
              f"avg bar range: top-10% vol = ${hi.mean():.2f} vs rest = ${rest.mean():.2f} "
              f"({hi.mean()/max(rest.mean(),1e-9):.1f}x)")

# ---- write full CSVs for the chart ----
for name in TFS:
    rows = all_rows[name]
    if not rows: continue
    path = os.path.join(OUT, f"vol_{name}.csv")
    with open(path, "w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader(); wtr.writerows(rows)
print("\nCSVs written to:", OUT)
mt5.shutdown()

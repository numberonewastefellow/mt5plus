r"""
XAUUSD VOLUME-ANOMALY SCALP SIGNAL — direction search + backtest
================================================================

The volume anomaly is only the TRIGGER. The hard part is DIRECTION. Instead of
assuming the spike means "continuation", we test both hypotheses and every
sensible knob, then let the backtest pick the edge:

  direction  : CONT  (trade WITH the spike bar's thrust)
               FADE  (trade AGAINST it — climactic/exhaustion volume reverses)
  trend      : off, or require agreement with the M15 EMA(20/50) trend
  anomaly    : RVOL threshold (how rare the volume spike must be)
  stop        : SL at the spike-bar extreme, or ATR-based (k * ATR14)
  target     : TP = R_MULT * risk  (short SL, bigger target)

Trade model (scalp, no look-ahead):
  entry = next M1 bar open (+/- half spread)
  exit  = first of SL / TP in the following bars; a bar straddling both -> SL
          first (conservative); time-stop after MAX_HOLD bars -> mark to close.
  cost  = one full spread per round trip, charged in R.

Nothing is executed. Prints a ranked leaderboard, then the recent live signals
for the best configuration.

Run:  ../XauOrderPad/.venv/Scripts/python.exe scalp_signal.py
"""
import datetime as dt
import numpy as np
import MetaTrader5 as mt5

# ------------------------------ CONFIG -----------------------------------
START     = dt.datetime(2026, 5, 24)
END       = dt.datetime.now()
ENTRY_TF  = mt5.TIMEFRAME_M1
TREND_TF  = mt5.TIMEFRAME_M15
RVOL_WIN  = 90
EMA_FAST, EMA_SLOW = 20, 50
CLV_HI, CLV_LO = 0.60, 0.40
ATR_N     = 14
MAX_HOLD  = 20
SPREAD_FALLBACK_PT = 200
MIN_TRADES = 30            # ignore configs too thin to trust

# search grid
G_MODE   = ["CONT", "FADE"]
G_TREND  = [False, True]
G_RVOL   = [4.0, 6.0, 8.0]
G_STOP   = [("spike", 0.0), ("atr", 1.0), ("atr", 1.5)]
G_RMULT  = [1.0, 1.5, 2.0]
# -------------------------------------------------------------------------

if not mt5.initialize():
    raise SystemExit("INIT_FAIL: %s" % (mt5.last_error(),))
symbol = "XAUUSD"
if mt5.symbol_info(symbol) is None:
    for s in (mt5.symbols_get("XAUUSD*") or []):
        symbol = s.name; break
mt5.symbol_select(symbol, True)
POINT = mt5.symbol_info(symbol).point


def ema(x, span):
    a = 2.0 / (span + 1.0)
    out = np.empty(len(x)); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def get(tf):
    r = mt5.copy_rates_range(symbol, tf, START, END)
    if r is None or len(r) == 0:
        raise SystemExit(f"no data tf={tf}: {mt5.last_error()}")
    return r


# --- M15 trend bias, looked up by timestamp (no look-ahead) ---
tr = get(TREND_TF)
tr_t = tr['time'].astype('int64')
tr_bias = np.sign(ema(tr['close'], EMA_FAST) - ema(tr['close'], EMA_SLOW))

def trend_at(ts):
    j = np.searchsorted(tr_t, ts, side='right') - 1
    return tr_bias[j] if j >= 0 else 0.0


# --- M1 entry series + indicators ---
r = get(ENTRY_TF)
t = r['time'].astype('int64')
o, h, l, c = (r[k].astype(float) for k in ('open', 'high', 'low', 'close'))
vol = r['tick_volume'].astype(float)
spr_pt = r['spread'].astype(float) if 'spread' in r.dtype.names else np.full(len(c), SPREAD_FALLBACK_PT)
spr_pt = np.where(spr_pt > 0, spr_pt, SPREAD_FALLBACK_PT)
n = len(c)
D = [dt.datetime.fromtimestamp(int(x)) for x in t]

rvol = np.full(n, np.nan)
for i in range(RVOL_WIN, n):
    base = np.median(vol[i - RVOL_WIN:i])
    rvol[i] = vol[i] / base if base > 0 else np.nan

rng = np.maximum(h - l, POINT)
clv = (c - l) / rng
body = c - o
prevc = np.concatenate(([c[0]], c[:-1]))
tr_true = np.maximum.reduce([h - l, np.abs(h - prevc), np.abs(prevc - l)])
atr = np.full(n, np.nan)
for i in range(ATR_N, n):
    atr[i] = tr_true[i - ATR_N + 1:i + 1].mean()


# --- candidate spike bars (thrust must be clear); base_dir = continuation dir ---
cand = []
for i in range(max(RVOL_WIN, ATR_N), n - 1):
    if np.isnan(rvol[i]) or np.isnan(atr[i]) or atr[i] <= 0:
        continue
    long_ms  = clv[i] >= CLV_HI and body[i] > 0
    short_ms = clv[i] <= CLV_LO and body[i] < 0
    if not (long_ms or short_ms):
        continue
    cand.append((i, 1 if long_ms else -1))


def backtest(mode, trend_on, rvol_thr, stop_mode, stop_k, r_mult):
    outs = []
    for i, base_dir in cand:
        if rvol[i] < rvol_thr:
            continue
        direction = base_dir if mode == "CONT" else -base_dir
        if trend_on:
            tb = trend_at(t[i])
            if tb == 0 or tb != direction:
                continue
        spr = spr_pt[i] * POINT
        entry = o[i + 1] + (spr / 2 if direction > 0 else -spr / 2)
        if stop_mode == "spike":
            sl = (l[i] - 60 * POINT) if direction > 0 else (h[i] + 60 * POINT)
        else:  # atr
            sl = entry - stop_k * atr[i] if direction > 0 else entry + stop_k * atr[i]
        risk = (entry - sl) if direction > 0 else (sl - entry)
        if risk <= 0:
            continue
        tp = entry + r_mult * risk if direction > 0 else entry - r_mult * risk
        res = None
        for k in range(i + 1, min(n, i + 1 + MAX_HOLD)):
            hit_sl = (l[k] <= sl) if direction > 0 else (h[k] >= sl)
            hit_tp = (h[k] >= tp) if direction > 0 else (l[k] <= tp)
            if hit_sl and hit_tp:
                res = -1.0; break
            if hit_sl:
                res = -1.0; break
            if hit_tp:
                res = r_mult; break
        if res is None:
            exitp = c[min(n - 1, i + MAX_HOLD)]
            pl = (exitp - entry) if direction > 0 else (entry - exitp)
            res = pl / risk
        res -= spr / risk
        outs.append(res)
    outs = np.array(outs)
    if len(outs) < MIN_TRADES:
        return None
    wins = outs[outs > 0]; losses = outs[outs <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float('inf')
    return dict(n=len(outs), win=round(100 * len(wins) / len(outs), 1),
                exp=round(float(outs.mean()), 3), pf=round(float(pf), 2),
                total=round(float(outs.sum()), 1))


print(f"Symbol {symbol} | {START:%b %d}->{END:%b %d %H:%M} | M1 bars={n} | candidate spikes={len(cand)}")
print(f"SL-first on ambiguous bars | 1 spread cost/trade | hold<= {MAX_HOLD} M1 bars\n")

results = []
for mode in G_MODE:
    for trend_on in G_TREND:
        for rv in G_RVOL:
            for (sm, sk) in G_STOP:
                for rm in G_RMULT:
                    b = backtest(mode, trend_on, rv, sm, sk, rm)
                    if b:
                        results.append(dict(mode=mode, trend=trend_on, rvol=rv,
                                            stop=f"{sm}{sk if sm=='atr' else ''}",
                                            r=rm, **b))

results.sort(key=lambda x: x['exp'], reverse=True)
hdr = f"{'#':>2} {'mode':5}{'trend':>6}{'rvol':>5}{'stop':>7}{'tgt':>5}{'trades':>7}{'win%':>6}{'exp/R':>8}{'PF':>6}{'totR':>7}"
print("TOP 12 CONFIGS (ranked by expectancy per R):")
print(hdr); print("-" * len(hdr))
for k, x in enumerate(results[:12], 1):
    print(f"{k:>2} {x['mode']:5}{str(x['trend']):>6}{x['rvol']:>5}{x['stop']:>7}"
          f"{str(x['r'])+'R':>5}{x['n']:>7}{x['win']:>6}{x['exp']:>8}{x['pf']:>6}{x['total']:>7}")

print("\nBOTTOM 5 (worst — for contrast):")
for x in results[-5:]:
    print(f"   {x['mode']:5}{str(x['trend']):>6}{x['rvol']:>5}{x['stop']:>7}"
          f"{str(x['r'])+'R':>5}{x['n']:>7}{x['win']:>6}{x['exp']:>8}{x['pf']:>6}{x['total']:>7}")

# ---- recent signals for the best config ----
best = results[0]
print("\n" + "=" * 78)
print(f"BEST CONFIG: {best['mode']}  trend={best['trend']}  RVOL>={best['rvol']}x  "
      f"stop={best['stop']}  target={best['r']}R  "
      f"-> exp {best['exp']}R/trade, win {best['win']}%, PF {best['pf']} over {best['n']} trades")

mode, trend_on, rvol_thr = best['mode'], best['trend'], best['rvol']
stop_mode = 'atr' if best['stop'].startswith('atr') else 'spike'
stop_k = float(best['stop'][3:]) if stop_mode == 'atr' else 0.0
r_mult = best['r']
print("Most recent signals + scalp plan:")
print(f"  {'time':17}{'dir':>6}{'RVOL':>6}{'entry':>9}{'SL':>9}{'TP':>9}{'risk$':>7}")
shown = 0
for i, base_dir in reversed(cand):
    if rvol[i] < rvol_thr:
        continue
    direction = base_dir if mode == "CONT" else -base_dir
    if trend_on:
        tb = trend_at(t[i])
        if tb == 0 or tb != direction:
            continue
    spr = spr_pt[i] * POINT
    entry = o[i + 1] + (spr / 2 if direction > 0 else -spr / 2)
    if stop_mode == 'spike':
        sl = (l[i] - 60 * POINT) if direction > 0 else (h[i] + 60 * POINT)
    else:
        sl = entry - stop_k * atr[i] if direction > 0 else entry + stop_k * atr[i]
    risk = abs(entry - sl)
    tp = entry + r_mult * risk if direction > 0 else entry - r_mult * risk
    print(f"  {D[i]:%Y-%m-%d %H:%M}{'LONG' if direction>0 else 'SHORT':>6}"
          f"{rvol[i]:>6.1f}{entry:>9.2f}{sl:>9.2f}{tp:>9.2f}{risk:>7.2f}")
    shown += 1
    if shown >= 12:
        break

mt5.shutdown()

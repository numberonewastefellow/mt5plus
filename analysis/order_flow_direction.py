r"""
XAUUSD BUY/SELL-PRESSURE DIRECTION ENGINE  (order flow + volume profile)
=======================================================================

tick_volume tells us HOW MUCH traded, not WHO won. To get direction we estimate
buy vs sell pressure from OHLCV using published order-flow methods, then test
which pressure reading actually predicts the next move.

Pressure estimators (all causal, no look-ahead):
  * BVC  - Bulk Volume Classification (Easley/Lopez de Prado/O'Hara):
           buy_fraction = N(dP / sigma_dP); delta = V*(2*buyfrac-1).
  * CVD  - cumulative + rolling volume delta (net order flow).
  * CLV  - close-location money flow (Chaikin A/D): who held the bar.
  * VP   - rolling Volume Profile: POC / Value-Area-High / Value-Area-Low.

Direction engines tested (trigger = a RARE volume spike, RVOL >= thr):
  FOLLOW_DELTA : trade WITH net order-flow pressure (last k bars).
  FADE_DELTA   : trade AGAINST it (exhaustion).
  ABSORPTION   : huge volume + tiny move -> fade the absorbed push.
  CLV_FLOW     : trade the side that closed the bar (money-flow sign).
  PROFILE_FADE : above Value-Area-High -> short back to value; below VAL -> long.

Backtest: entry next M1 open (+/- half spread); ATR stop; TP = R_MULT*risk;
SL-first on ambiguous bars; 1 spread cost/trade; time-stop at MAX_HOLD.
Reports IN-SAMPLE and a first-half / second-half split to expose overfitting.

Run:  ../XauOrderPad/.venv/Scripts/python.exe order_flow_direction.py
"""
import csv
import datetime as dt
import os
import sys
from math import erf, sqrt
import numpy as np
import MetaTrader5 as mt5
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ------------------------------ CONFIG -----------------------------------
START     = dt.datetime(2026, 5, 24)
END       = dt.datetime.now()
if len(sys.argv) >= 3:                 # optional CLI override: <start> <end> as YYYY-MM-DD
    START = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
    END = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")
ENTRY_TF  = mt5.TIMEFRAME_M1           # <-- all detection, direction & entries run on M1 bars
RVOL_WIN  = 90
DP_WIN    = 50          # window for return-volatility (BVC sigma)
DELTA_K   = 5           # rolling order-flow window (bars)
ATR_N     = 14
VP_LOOK   = 1440        # volume-profile lookback (~1 trading day of M1)
VP_BINS   = 120
VA_PCT    = 0.70        # value area = 70% of volume
MAX_HOLD  = 20
SPREAD_FALLBACK_PT = 200
MIN_TRADES = 25

G_STRAT  = ["FOLLOW_DELTA", "FADE_DELTA", "ABSORPTION", "CLV_FLOW", "PROFILE_FADE"]
G_RVOL   = [5.0, 8.0]
G_SL_ATR = [1.0, 1.5, 2.0]
G_RMULT  = [1.0, 1.5, 2.0, 2.5]
# -------------------------------------------------------------------------

if not mt5.initialize():
    raise SystemExit("INIT_FAIL: %s" % (mt5.last_error(),))
symbol = "XAUUSD"
if mt5.symbol_info(symbol) is None:
    for s in (mt5.symbols_get("XAUUSD*") or []):
        symbol = s.name; break
mt5.symbol_select(symbol, True)
POINT = mt5.symbol_info(symbol).point

r = mt5.copy_rates_range(symbol, ENTRY_TF, START, END)
if r is None or len(r) == 0:
    raise SystemExit(f"no data: {mt5.last_error()}")
t = r['time'].astype('int64')
o, h, l, c = (r[k].astype(float) for k in ('open', 'high', 'low', 'close'))
vol = r['tick_volume'].astype(float)
spr_pt = r['spread'].astype(float) if 'spread' in r.dtype.names else np.full(len(c), SPREAD_FALLBACK_PT)
spr_pt = np.where(spr_pt > 0, spr_pt, SPREAD_FALLBACK_PT)
n = len(c)
D = [dt.datetime.fromtimestamp(int(x)) for x in t]

# ---- indicators ----
rvol = np.full(n, np.nan)
for i in range(RVOL_WIN, n):
    b = np.median(vol[i - RVOL_WIN:i]); rvol[i] = vol[i] / b if b > 0 else np.nan

rng = np.maximum(h - l, POINT)
clv = ((c - l) - (h - c)) / rng          # -1 (closed on low) .. +1 (closed on high)
body = c - o

# BVC buy/sell split -> per-bar delta
dP = np.concatenate(([0.0], np.diff(c)))
sig = np.full(n, np.nan)
for i in range(DP_WIN, n):
    s = np.std(dP[i - DP_WIN:i]); sig[i] = s if s > 0 else np.nan
ncdf = np.array([0.5 * (1 + erf(x / sqrt(2))) if x == x else 0.5 for x in (dP / np.where(np.isnan(sig), 1, sig))])
buy_frac = np.where(np.isnan(sig), 0.5, ncdf)
delta = vol * (2 * buy_frac - 1)          # >0 net buying, <0 net selling
roll_delta = np.full(n, np.nan)           # net order flow over last DELTA_K bars
for i in range(DELTA_K, n):
    roll_delta[i] = delta[i - DELTA_K + 1:i + 1].sum()

# ATR
prevc = np.concatenate(([c[0]], c[:-1]))
tr_true = np.maximum.reduce([h - l, np.abs(h - prevc), np.abs(prevc - l)])
atr = np.full(n, np.nan)
for i in range(ATR_N, n):
    atr[i] = tr_true[i - ATR_N + 1:i + 1].mean()

tp_typ = (h + l + c) / 3.0                 # typical price for volume profile

def value_area(i):
    """Rolling volume profile over [i-VP_LOOK, i): return (POC, VAH, VAL). Causal."""
    lo = max(0, i - VP_LOOK)
    if i - lo < 200:
        return None
    prices = tp_typ[lo:i]; w = vol[lo:i]
    pmin, pmax = prices.min(), prices.max()
    if pmax <= pmin:
        return None
    edges = np.linspace(pmin, pmax, VP_BINS + 1)
    hist, _ = np.histogram(prices, bins=edges, weights=w)
    centers = (edges[:-1] + edges[1:]) / 2
    poc = int(np.argmax(hist))
    total = hist.sum(); target = total * VA_PCT
    lo_i = hi_i = poc; acc = hist[poc]
    while acc < target and (lo_i > 0 or hi_i < len(hist) - 1):
        left = hist[lo_i - 1] if lo_i > 0 else -1
        right = hist[hi_i + 1] if hi_i < len(hist) - 1 else -1
        if right >= left:
            hi_i += 1; acc += hist[hi_i]
        else:
            lo_i -= 1; acc += hist[lo_i]
    return centers[poc], centers[hi_i], centers[lo_i]

# ---- candidate spike bars (need i+1 to enter) ----
cand = [i for i in range(max(RVOL_WIN, DP_WIN, ATR_N, DELTA_K), n - 1)
        if not np.isnan(rvol[i]) and not np.isnan(atr[i]) and atr[i] > 0]


def direction(strat, i):
    """Return +1 long / -1 short / 0 skip for a strategy at spike bar i."""
    if strat == "FOLLOW_DELTA":
        d = roll_delta[i]
        return int(np.sign(d)) if abs(d) > 0 else 0
    if strat == "FADE_DELTA":
        d = roll_delta[i]
        return -int(np.sign(d)) if abs(d) > 0 else 0
    if strat == "ABSORPTION":
        # huge volume but small displacement vs volatility -> one side absorbed
        if abs(body[i]) < 0.35 * atr[i]:
            s = np.sign(body[i]) if body[i] != 0 else np.sign(clv[i])
            return -int(s) if s != 0 else 0
        return 0
    if strat == "CLV_FLOW":
        if clv[i] >= 0.35:
            return 1
        if clv[i] <= -0.35:
            return -1
        return 0
    if strat == "PROFILE_FADE":
        va = value_area(i)
        if va is None:
            return 0
        _, vah, val = va
        if c[i] > vah:
            return -1
        if c[i] < val:
            return 1
        return 0
    return 0


def backtest(strat, rvol_thr, sl_atr, r_mult):
    outs = []          # (bar_index, R_result)
    for i in cand:
        if rvol[i] < rvol_thr:
            continue
        dirn = direction(strat, i)
        if dirn == 0:
            continue
        spr = spr_pt[i] * POINT
        entry = o[i + 1] + (spr / 2 if dirn > 0 else -spr / 2)
        sl = entry - sl_atr * atr[i] if dirn > 0 else entry + sl_atr * atr[i]
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp = entry + r_mult * risk if dirn > 0 else entry - r_mult * risk
        res = None
        for k in range(i + 1, min(n, i + 1 + MAX_HOLD)):
            hit_sl = (l[k] <= sl) if dirn > 0 else (h[k] >= sl)
            hit_tp = (h[k] >= tp) if dirn > 0 else (l[k] <= tp)
            if hit_sl:
                res = -1.0; break
            if hit_tp:
                res = r_mult; break
        if res is None:
            exitp = c[min(n - 1, i + MAX_HOLD)]
            pl = (exitp - entry) if dirn > 0 else (entry - exitp)
            res = pl / risk
        res -= spr / risk
        outs.append((i, res))
    if len(outs) < MIN_TRADES:
        return None
    idx = np.array([x[0] for x in outs]); R = np.array([x[1] for x in outs])
    half = n / 2
    def stat(mask):
        rr = R[mask]
        if len(rr) == 0:
            return (0, 0.0)
        return (len(rr), round(float(rr.mean()), 3))
    wins = R[R > 0]; losses = R[R <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float('inf')
    n1, e1 = stat(idx < half); n2, e2 = stat(idx >= half)
    return dict(n=len(R), win=round(100 * len(wins) / len(R), 1),
                exp=round(float(R.mean()), 3), pf=round(float(pf), 2),
                total=round(float(R.sum()), 1), e1=e1, e2=e2, n1=n1, n2=n2)


print(f"Symbol {symbol} | {START:%b %d}->{END:%b %d %H:%M} | M1 bars={n} | candidates={len(cand)}")
print(f"Order-flow direction search | ATR stop | SL-first | 1 spread/trade | hold<= {MAX_HOLD}\n")

results = []
for strat in G_STRAT:
    for rv in G_RVOL:
        for sla in G_SL_ATR:
            for rm in G_RMULT:
                b = backtest(strat, rv, sla, rm)
                if b:
                    results.append(dict(strat=strat, rvol=rv, sl=sla, r=rm, **b))

# rank by the WORSE of the two halves (robustness), tiebreak overall exp
results.sort(key=lambda x: (min(x['e1'], x['e2']), x['exp']), reverse=True)
hdr = (f"{'#':>2} {'strategy':13}{'rvol':>5}{'SLatr':>6}{'tgt':>5}{'trades':>7}"
       f"{'win%':>6}{'exp/R':>8}{'PF':>6}{'H1exp':>7}{'H2exp':>7}")
print("TOP 12 (ranked by worst-half expectancy = robustness):")
print(hdr); print("-" * len(hdr))
for k, x in enumerate(results[:12], 1):
    print(f"{k:>2} {x['strat']:13}{x['rvol']:>5}{x['sl']:>6}{str(x['r'])+'R':>5}{x['n']:>7}"
          f"{x['win']:>6}{x['exp']:>8}{x['pf']:>6}{x['e1']:>7}{x['e2']:>7}")

# best per strategy family (by overall exp) for a fair comparison
print("\nBEST SETTING PER DIRECTION ENGINE (by overall expectancy):")
for strat in G_STRAT:
    sub = [x for x in results if x['strat'] == strat]
    if not sub:
        print(f"  {strat:13} (no qualifying config)"); continue
    x = max(sub, key=lambda z: z['exp'])
    verdict = "EDGE" if (x['exp'] > 0 and min(x['e1'], x['e2']) > 0) else \
              "in-sample only" if x['exp'] > 0 else "no edge"
    print(f"  {x['strat']:13} rvol>={x['rvol']} SL{x['sl']}atr {x['r']}R -> "
          f"exp {x['exp']:+.3f} (H1 {x['e1']:+.3f}/H2 {x['e2']:+.3f}) win {x['win']}% "
          f"PF {x['pf']}  [{verdict}]")

# ---- live volume-profile context + recent signals for the most robust config ----
best = results[0]
print("\n" + "=" * 84)
print(f"MOST ROBUST CONFIG: {best['strat']}  rvol>={best['rvol']}  SL={best['sl']}xATR  target={best['r']}R")
print(f"  overall exp {best['exp']:+.3f}R  win {best['win']}%  PF {best['pf']}  "
      f"(1st half {best['e1']:+.3f} / 2nd half {best['e2']:+.3f})")

va = value_area(n - 1)
if va:
    poc, vah, val = va
    print(f"  Current volume profile (last {VP_LOOK} M1 bars):  "
          f"POC {poc:.2f} | Value Area {val:.2f} - {vah:.2f} | last price {c[-1]:.2f} "
          f"({'ABOVE value' if c[-1]>vah else 'BELOW value' if c[-1]<val else 'inside value'})")

# ===================== MONEY / LOT-SIZE ANALYSIS + CSV EXPORT ===============
# Convert R-multiples into real account $ for each lot size. XAUUSD pays
# contract_size (100) oz per 1.0 lot, so a $1/oz move = $1 (0.01 lot) /
# $10 (0.1) / $100 (1.0). risk is $/oz stop distance; result_R already nets
# the spread cost, so $ = R * risk * contract_size * lot is cost-inclusive.
CONTRACT = mt5.symbol_info(symbol).trade_contract_size
_acc = mt5.account_info()
balance = _acc.balance if _acc else 0.0
LOTS = [0.01, 0.1, 1.0]


def trade_list(strat, rvol_thr, sl_atr, r_mult):
    trades = []
    for i in cand:
        if rvol[i] < rvol_thr:
            continue
        dirn = direction(strat, i)
        if dirn == 0:
            continue
        spr = spr_pt[i] * POINT
        entry = o[i + 1] + (spr / 2 if dirn > 0 else -spr / 2)
        sl = entry - sl_atr * atr[i] if dirn > 0 else entry + sl_atr * atr[i]
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp = entry + r_mult * risk if dirn > 0 else entry - r_mult * risk
        res = exit_idx = exit_price = reason = None
        for k in range(i + 1, min(n, i + 1 + MAX_HOLD)):
            hit_sl = (l[k] <= sl) if dirn > 0 else (h[k] >= sl)
            hit_tp = (h[k] >= tp) if dirn > 0 else (l[k] <= tp)
            if hit_sl:                                  # SL-first on ambiguous bars
                res, exit_idx, exit_price, reason = -1.0, k, sl, 'SL'; break
            if hit_tp:
                res, exit_idx, exit_price, reason = r_mult, k, tp, 'TP'; break
        if res is None:                                 # time-stop -> mark to close
            exit_idx = min(n - 1, i + MAX_HOLD)
            exit_price = c[exit_idx]
            pl = (exit_price - entry) if dirn > 0 else (entry - exit_price)
            res, reason = pl / risk, 'TIME'
        res -= spr / risk
        entry_time, exit_time = D[i + 1], D[exit_idx]   # entry fills at next bar's open
        hold_min = int((exit_time - entry_time).total_seconds() // 60)
        trades.append(dict(i=i, sig_time=D[i], entry_time=entry_time, exit_time=exit_time,
                           reason=reason, hold_min=hold_min, dir=dirn, rvol=float(rvol[i]),
                           delta=float(roll_delta[i]), entry=entry, sl=sl, tp=tp,
                           exit_price=exit_price, risk=risk, R=res))
    return trades


trades = trade_list(best['strat'], best['rvol'], best['sl'], best['r'])
wins = [t for t in trades if t['R'] > 0]
losses = [t for t in trades if t['R'] <= 0]
nT, nW, nL = len(trades), len(wins), len(losses)
winrate = 100 * nW / nT if nT else 0.0


def pnl_usd(t, lot):
    return t['R'] * t['risk'] * CONTRACT * lot


print("\n  Recent trades (signal -> entry -> exit):")
print(f"  {'signal':14}{'entry':14}{'exit':14}{'dir':>6}{'why':>5}{'hold':>6}"
      f"{'entry$':>9}{'exit$':>9}{'R':>7}{'$/0.1lot':>10}")
for t in trades[-12:]:
    print(f"  {t['sig_time']:%m-%d %H:%M}  {t['entry_time']:%m-%d %H:%M}  {t['exit_time']:%m-%d %H:%M}  "
          f"{'LONG' if t['dir']>0 else 'SHORT':>6}{t['reason']:>5}{t['hold_min']:>5}m"
          f"{t['entry']:>9.2f}{t['exit_price']:>9.2f}{t['R']:>7.2f}{pnl_usd(t,0.1):>10.2f}")

lot_rows = []
for lot in LOTS:
    pnls = [pnl_usd(t, lot) for t in trades]
    net = sum(pnls)
    gp = sum(p for p in pnls if p > 0)
    gl = sum(p for p in pnls if p <= 0)
    eq = peak = dd = 0.0                       # time-ordered equity drawdown
    for p in pnls:
        eq += p; peak = max(peak, eq); dd = min(dd, eq - peak)
    avg_risk = CONTRACT * lot * float(np.mean([t['risk'] for t in trades])) if nT else 0.0
    lot_rows.append(dict(lot=lot, avg_risk=avg_risk, net=net,
                         pct=100 * net / balance if balance else 0.0, gp=gp, gl=gl,
                         avg=net / nT if nT else 0.0, maxdd=dd,
                         maxdd_pct=100 * dd / balance if balance else 0.0))

print("\n" + "=" * 84)
print(f"MONEY RESULT — {best['strat']}  rvol>={best['rvol']}  SL={best['sl']}xATR  target={best['r']}R")
print(f"Period {D[0]:%Y-%m-%d %H:%M} -> {D[-1]:%Y-%m-%d %H:%M} | balance ${balance:,.2f} | {CONTRACT:.0f} oz/lot")
print(f"Trades: {nT} | Won: {nW} | Lost: {nL} | WIN RATE: {winrate:.1f}% | expectancy {best['exp']:+.3f}R/trade")
print(f"\n  {'lot':>5}{'risk/trade$':>13}{'netP&L$':>11}{'net%':>8}{'grossWin$':>11}"
      f"{'grossLoss$':>12}{'avg$/trade':>11}{'maxDD$':>10}{'maxDD%':>8}")
for x in lot_rows:
    print(f"  {x['lot']:>5}{x['avg_risk']:>13.2f}{x['net']:>11.2f}{x['pct']:>7.2f}%"
          f"{x['gp']:>11.2f}{x['gl']:>12.2f}{x['avg']:>11.2f}{x['maxdd']:>10.2f}{x['maxdd_pct']:>7.2f}%")

# ---- CSV export, filenames stamped with the data range ----
ds, de = D[0].strftime('%Y%m%d'), D[-1].strftime('%Y%m%d')
OUTDIR = os.path.dirname(os.path.abspath(__file__))
trades_path = os.path.join(OUTDIR, f"results_orderflow_trades_{ds}_{de}.csv")
summary_path = os.path.join(OUTDIR, f"results_orderflow_summary_{ds}_{de}.csv")

with open(trades_path, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['# symbol', symbol, 'strategy', best['strat'], 'rvol>=', best['rvol'],
                'SL_ATR', best['sl'], 'target_R', best['r']])
    w.writerow(['# data_start', f"{D[0]:%Y-%m-%d %H:%M}", 'data_end', f"{D[-1]:%Y-%m-%d %H:%M}"])
    w.writerow(['signal_time', 'entry_time', 'exit_time', 'exit_reason', 'hold_min', 'weekday',
                'direction', 'rvol', 'net_delta', 'entry', 'sl', 'tp', 'exit_price',
                'risk_usd_per_oz', 'result_R', 'pnl_0.01lot', 'pnl_0.1lot', 'pnl_1lot'])
    for t in trades:
        w.writerow([f"{t['sig_time']:%Y-%m-%d %H:%M}", f"{t['entry_time']:%Y-%m-%d %H:%M}",
                    f"{t['exit_time']:%Y-%m-%d %H:%M}", t['reason'], t['hold_min'],
                    t['sig_time'].strftime('%a'), 'LONG' if t['dir'] > 0 else 'SHORT',
                    round(t['rvol'], 1), round(t['delta'], 0), round(t['entry'], 2),
                    round(t['sl'], 2), round(t['tp'], 2), round(t['exit_price'], 2),
                    round(t['risk'], 2), round(t['R'], 3), round(pnl_usd(t, 0.01), 2),
                    round(pnl_usd(t, 0.1), 2), round(pnl_usd(t, 1.0), 2)])

with open(summary_path, 'w', newline='') as f:
    w = csv.writer(f)
    for k, v in [('data_start', f"{D[0]:%Y-%m-%d %H:%M}"), ('data_end', f"{D[-1]:%Y-%m-%d %H:%M}"),
                 ('symbol', symbol), ('strategy', best['strat']), ('rvol_threshold', best['rvol']),
                 ('sl_atr_mult', best['sl']), ('target_R', best['r']),
                 ('account_balance_usd', round(balance, 2)), ('contract_size_oz', CONTRACT),
                 ('total_trades', nT), ('wins', nW), ('losses', nL),
                 ('win_rate_pct', round(winrate, 1)), ('expectancy_R', best['exp']),
                 ('profit_factor', best['pf'])]:
        w.writerow([k, v])
    w.writerow([])
    w.writerow(['lot', 'risk_per_trade_usd', 'net_pnl_usd', 'net_pct', 'gross_win_usd',
                'gross_loss_usd', 'avg_usd_per_trade', 'max_drawdown_usd', 'max_drawdown_pct'])
    for x in lot_rows:
        w.writerow([x['lot'], round(x['avg_risk'], 2), round(x['net'], 2), round(x['pct'], 2),
                    round(x['gp'], 2), round(x['gl'], 2), round(x['avg'], 2),
                    round(x['maxdd'], 2), round(x['maxdd_pct'], 2)])

print(f"\nSaved:\n  {trades_path}\n  {summary_path}")

# ----------------------------- Plotly chart ------------------------------
# NOTE: this is the IN-SAMPLE picture — the config was optimized on THIS same
# window, so the equity curve climbing is expected and NOT proof of an edge.
if trades:
    step = max(1, n // 6000)                       # thin the price line for the browser
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.70, 0.30],
                        vertical_spacing=0.06,
                        subplot_titles=(
                            f"{symbol} M1 — {best['strat']} rvol>={best['rvol']} SL{best['sl']}xATR "
                            f"{best['r']}R   [IN-SAMPLE: config optimized on THIS window]",
                            "Cumulative P&L @ 0.1 lot (USD)"))
    fig.add_trace(go.Scatter(x=D[::step], y=c[::step], mode='lines', name='price',
                             line=dict(color='#8a8f98', width=1)), row=1, col=1)
    for cond, nm, sym, col in [(lambda t: t['dir'] > 0, 'long entry', 'triangle-up', '#2fa572'),
                               (lambda t: t['dir'] < 0, 'short entry', 'triangle-down', '#e0664f')]:
        xs = [t['entry_time'] for t in trades if cond(t)]
        ys = [t['entry'] for t in trades if cond(t)]
        tx = [f"{t['reason']} R={t['R']:+.2f}" for t in trades if cond(t)]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode='markers', name=nm, text=tx,
                                 marker=dict(symbol=sym, size=9, color=col),
                                 hovertemplate='%{x}<br>%{y:.2f}<br>%{text}<extra></extra>'), row=1, col=1)
    ecol = ['#2fa572' if t['R'] > 0 else '#c0392b' for t in trades]
    fig.add_trace(go.Scatter(x=[t['exit_time'] for t in trades], y=[t['exit_price'] for t in trades],
                             mode='markers', name='exit (win/loss)',
                             marker=dict(symbol='x', size=6, color=ecol),
                             hovertemplate='%{x}<br>%{y:.2f}<extra>exit</extra>'), row=1, col=1)
    eq = np.cumsum([pnl_usd(t, 0.1) for t in trades])
    fig.add_trace(go.Scatter(x=[t['exit_time'] for t in trades], y=eq, mode='lines',
                             name='cum $ @0.1lot', line=dict(color='#e8b84b', width=1.6),
                             fill='tozeroy'), row=2, col=1)
    net01 = sum(pnl_usd(t, 0.1) for t in trades)
    fig.update_layout(template='plotly_dark', height=760, hovermode='closest',
                      title=f"XAUUSD M1 IN-SAMPLE — {D[0]:%b %d} to {D[-1]:%b %d %Y} — "
                            f"{nT} trades, win {winrate:.0f}%, net ${net01:,.0f} @0.1 lot "
                            f"(optimized on this window — not tradeable forward)")
    chart_path = os.path.join(OUTDIR, f"chart_insample_M1_{ds}_{de}.html")
    fig.write_html(chart_path, include_plotlyjs=True, full_html=True)
    print(f"CHART: {chart_path}")

mt5.shutdown()

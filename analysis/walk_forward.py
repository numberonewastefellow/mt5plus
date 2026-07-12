r"""
XAUUSD WALK-FORWARD order-flow scalp test  (multi-timeframe + Plotly)
====================================================================

Fixes the flaw we found: a single grid over one period just overfits a regime.
Here we RE-FIT weekly and trade only forward:

  for each test week W:
      train  = the 3 weeks BEFORE W
      pick   = config (strategy, rvol, SL, target) with best expectancy on train
               (>= MIN_TRAIN trades so we don't fit noise)
      trade  = apply that fixed config to week W  -> every trade is out-of-sample
  concatenate all test-week trades -> the honest, adaptive equity curve.

Direction engines (same as order_flow_direction.py): FOLLOW/FADE order-flow
delta (BVC), CLV money-flow, absorption, volume-profile fade.

Runs on M1, M5 and M15 so we can see if the edge is timeframe-specific.
Exports OOS trades to CSV (with entry/exit times) and builds a self-contained
Plotly HTML: price line + entry/exit markers + OOS equity curve.

Run:  ../XauOrderPad/.venv/Scripts/python.exe walk_forward.py [start end]  (YYYY-MM-DD)
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
START = dt.datetime(2026, 3, 24)          # M1 history on this feed starts ~Mar 23
END   = dt.datetime.now()
if len(sys.argv) >= 3:
    START = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
    END = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")
TRAIN_DAYS, TEST_DAYS = 21, 7             # 3-week train, 1-week forward test
RVOL_WIN, DP_WIN, DELTA_K, ATR_N = 90, 50, 5, 14
MAX_HOLD = 20
MIN_TRAIN = {"M1": 15, "M5": 10, "M15": 5}   # min training trades (scaled per timeframe)
SPREAD_FALLBACK_PT = 200
CHART_TF = "M1"                           # timeframe drawn in the Plotly chart

G_STRAT = ["FOLLOW_DELTA", "FADE_DELTA", "ABSORPTION", "CLV_FLOW", "PROFILE_FADE"]
G_RVOL  = [5.0, 8.0]
G_SLATR = [1.0, 1.5, 2.0]
G_RMULT = [1.0, 1.5, 2.0, 2.5]
TF_MAP = {"M1": (mt5.TIMEFRAME_M1, 1440), "M5": (mt5.TIMEFRAME_M5, 288),
          "M15": (mt5.TIMEFRAME_M15, 96)}     # (tf, volume-profile lookback bars ~1 day)
LOTS = [0.01, 0.1, 1.0]
# -------------------------------------------------------------------------

if not mt5.initialize():
    raise SystemExit("INIT_FAIL: %s" % (mt5.last_error(),))
symbol = "XAUUSD"
if mt5.symbol_info(symbol) is None:
    for s in (mt5.symbols_get("XAUUSD*") or []):
        symbol = s.name; break
mt5.symbol_select(symbol, True)
POINT = mt5.symbol_info(symbol).point
CONTRACT = mt5.symbol_info(symbol).trade_contract_size
_acc = mt5.account_info()
BALANCE = _acc.balance if _acc else 0.0
_NCDF = lambda x: 0.5 * (1 + erf(x / sqrt(2)))


def fetch_rates(tf, start, end):
    """Chunked pull: M1 over long spans exceeds MT5's single-call cap, so we
    request in 25-day windows and de-duplicate by timestamp."""
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
    _, idx = np.unique(allr['time'], return_index=True)   # idx already time-sorted
    return allr[idx]


def build_tf(tf_name):
    """Pull one timeframe and precompute every indicator + spike list once."""
    tf, vp_look = TF_MAP[tf_name]
    r = fetch_rates(tf, START, END)
    if r is None or len(r) == 0:
        return None
    t = r['time'].astype('int64')
    o, h, l, c = (r[k].astype(float) for k in ('open', 'high', 'low', 'close'))
    vol = r['tick_volume'].astype(float)
    spr = r['spread'].astype(float) if 'spread' in r.dtype.names else np.full(len(c), SPREAD_FALLBACK_PT)
    spr = np.where(spr > 0, spr, SPREAD_FALLBACK_PT)
    n = len(c)
    D = [dt.datetime.fromtimestamp(int(x)) for x in t]

    rvol = np.full(n, np.nan)
    for i in range(RVOL_WIN, n):
        b = np.median(vol[i - RVOL_WIN:i]); rvol[i] = vol[i] / b if b > 0 else np.nan
    rng = np.maximum(h - l, POINT)
    clv = ((c - l) - (h - c)) / rng
    body = c - o
    dP = np.concatenate(([0.0], np.diff(c)))
    sig = np.full(n, np.nan)
    for i in range(DP_WIN, n):
        s = np.std(dP[i - DP_WIN:i]); sig[i] = s if s > 0 else np.nan
    z = dP / np.where(np.isnan(sig), 1, sig)
    buy_frac = np.where(np.isnan(sig), 0.5, np.array([_NCDF(v) for v in z]))
    delta = vol * (2 * buy_frac - 1)
    roll_delta = np.full(n, np.nan)
    for i in range(DELTA_K, n):
        roll_delta[i] = delta[i - DELTA_K + 1:i + 1].sum()
    prevc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum.reduce([h - l, np.abs(h - prevc), np.abs(prevc - l)])
    atr = np.full(n, np.nan)
    for i in range(ATR_N, n):
        atr[i] = tr[i - ATR_N + 1:i + 1].mean()
    tp_typ = (h + l + c) / 3.0
    va_cache = {}

    def value_area(i):
        if i in va_cache:
            return va_cache[i]
        lo = max(0, i - vp_look)
        res = None
        if i - lo >= 200:
            prices = tp_typ[lo:i]; w = vol[lo:i]
            pmin, pmax = prices.min(), prices.max()
            if pmax > pmin:
                edges = np.linspace(pmin, pmax, 121)
                hist, _ = np.histogram(prices, bins=edges, weights=w)
                centers = (edges[:-1] + edges[1:]) / 2
                poc = int(np.argmax(hist)); tot = hist.sum(); tgt = tot * 0.70
                a = b = poc; acc = hist[poc]
                while acc < tgt and (a > 0 or b < len(hist) - 1):
                    left = hist[a - 1] if a > 0 else -1
                    right = hist[b + 1] if b < len(hist) - 1 else -1
                    if right >= left:
                        b += 1; acc += hist[b]
                    else:
                        a -= 1; acc += hist[a]
                res = (centers[poc], centers[b], centers[a])
        va_cache[i] = res
        return res

    lo0 = max(RVOL_WIN, DP_WIN, ATR_N, DELTA_K)
    spikes = [i for i in range(lo0, n - 1)
              if not np.isnan(rvol[i]) and not np.isnan(atr[i]) and atr[i] > 0
              and rvol[i] >= min(G_RVOL)]
    return dict(name=tf_name, t=t, D=D, o=o, h=h, l=l, c=c, vol=vol, spr=spr, n=n,
                rvol=rvol, clv=clv, body=body, roll_delta=roll_delta, atr=atr,
                value_area=value_area, spikes=spikes)


def direction(tfd, strat, i):
    if strat == "FOLLOW_DELTA":
        d = tfd['roll_delta'][i]; return int(np.sign(d)) if d else 0
    if strat == "FADE_DELTA":
        d = tfd['roll_delta'][i]; return -int(np.sign(d)) if d else 0
    if strat == "ABSORPTION":
        if abs(tfd['body'][i]) < 0.35 * tfd['atr'][i]:
            s = np.sign(tfd['body'][i]) if tfd['body'][i] != 0 else np.sign(tfd['clv'][i])
            return -int(s) if s else 0
        return 0
    if strat == "CLV_FLOW":
        cv = tfd['clv'][i]
        return 1 if cv >= 0.35 else -1 if cv <= -0.35 else 0
    if strat == "PROFILE_FADE":
        va = tfd['value_area'](i)
        if va is None:
            return 0
        _, vah, val = va
        return -1 if tfd['c'][i] > vah else 1 if tfd['c'][i] < val else 0
    return 0


def run_trades(tfd, strat, rvol_thr, sl_atr, r_mult, i_lo, i_hi):
    """Backtest a config; SIGNAL bars restricted to [i_lo,i_hi). Exits use real
    forward bars (may cross the window edge). Returns list of trade dicts."""
    o, h, l, c, atr = tfd['o'], tfd['h'], tfd['l'], tfd['c'], tfd['atr']
    spr_a, D, n = tfd['spr'], tfd['D'], tfd['n']
    out = []
    for i in tfd['spikes']:
        if i < i_lo or i >= i_hi or tfd['rvol'][i] < rvol_thr:
            continue
        dirn = direction(tfd, strat, i)
        if dirn == 0:
            continue
        spr = spr_a[i] * POINT
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
            if hit_sl:
                res, exit_idx, exit_price, reason = -1.0, k, sl, 'SL'; break
            if hit_tp:
                res, exit_idx, exit_price, reason = r_mult, k, tp, 'TP'; break
        if res is None:
            exit_idx = min(n - 1, i + MAX_HOLD); exit_price = c[exit_idx]
            pl = (exit_price - entry) if dirn > 0 else (entry - exit_price)
            res, reason = pl / risk, 'TIME'
        res -= spr / risk
        out.append(dict(entry_time=D[i + 1], exit_time=D[exit_idx], dir=dirn,
                        reason=reason, entry=entry, exit_price=exit_price,
                        risk=risk, R=res, strat=strat))
    return out


def expectancy(trades):
    return float(np.mean([t['R'] for t in trades])) if trades else -9.9


def walk_forward(tfd):
    """Rolling weekly re-fit. Returns (oos_trades, weekly_log)."""
    t = tfd['t']
    min_train = MIN_TRAIN[tfd['name']]
    oos, log = [], []
    test_start = START + dt.timedelta(days=TRAIN_DAYS)
    while test_start < END:
        train_lo = int(np.searchsorted(t, (test_start - dt.timedelta(days=TRAIN_DAYS)).timestamp()))
        test_i0 = int(np.searchsorted(t, test_start.timestamp()))
        test_i1 = int(np.searchsorted(t, (test_start + dt.timedelta(days=TEST_DAYS)).timestamp()))
        # pick best config on the training window
        best_cfg, best_exp = None, -9.9
        for strat in G_STRAT:
            for rv in G_RVOL:
                for sl in G_SLATR:
                    for rm in G_RMULT:
                        tr = run_trades(tfd, strat, rv, sl, rm, train_lo, test_i0)
                        if len(tr) >= min_train:
                            e = expectancy(tr)
                            if e > best_exp:
                                best_exp, best_cfg = e, (strat, rv, sl, rm)
        wk = dict(week=test_start.strftime("%m-%d"), cfg=best_cfg, train_exp=round(best_exp, 3))
        if best_cfg:
            tt = run_trades(tfd, *best_cfg, test_i0, test_i1)
            for x in tt:
                x['week'] = test_start.strftime("%m-%d")
            oos += tt
            wk.update(n=len(tt), test_exp=round(expectancy(tt), 3) if tt else 0.0)
        else:
            wk.update(n=0, test_exp=0.0)
        log.append(wk)
        test_start += dt.timedelta(days=TEST_DAYS)
    return oos, log


def summarize(oos):
    if not oos:
        return None
    R = np.array([t['R'] for t in oos])
    wins = R[R > 0]; losses = R[R <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float('inf')
    return dict(n=len(R), win=round(100 * len(wins) / len(R), 1),
                exp=round(float(R.mean()), 3), pf=round(float(pf), 2),
                totR=round(float(R.sum()), 1))


print(f"WALK-FORWARD | {symbol} | {START:%Y-%m-%d} -> {END:%Y-%m-%d} | "
      f"train {TRAIN_DAYS}d / test {TEST_DAYS}d | balance ${BALANCE:,.0f}\n")

tf_results = {}
chart_tfd = None
for tf_name in ["M1", "M5", "M15"]:
    tfd = build_tf(tf_name)
    if tfd is None:
        print(f"[{tf_name}] no data"); continue
    if tf_name == CHART_TF:
        chart_tfd = tfd
    oos, log = walk_forward(tfd)
    s = summarize(oos)
    tf_results[tf_name] = (oos, log, s, tfd['n'])
    print("=" * 92)
    print(f"### {tf_name}  ({tfd['n']} bars)")
    print(f"  {'week':7}{'chosen config (strategy/rvol/SL/tgt)':40}{'trainExp':>9}{'#OOS':>6}{'testExp':>9}")
    for w in log:
        cfg = f"{w['cfg'][0]}/{w['cfg'][1]}/{w['cfg'][2]}/{w['cfg'][3]}R" if w['cfg'] else "(none)"
        print(f"  {w['week']:7}{cfg:40}{w['train_exp']:>9}{w['n']:>6}{w['test_exp']:>9}")
    if s:
        # per-lot P&L on the concatenated OOS trades
        line = []
        for lot in LOTS:
            net = sum(t['R'] * t['risk'] * CONTRACT * lot for t in oos)
            line.append(f"{lot}lot ${net:,.0f} ({100*net/BALANCE:+.1f}%)")
        flips = sum(1 for a, b in zip(log, log[1:])
                    if a['cfg'] and b['cfg'] and a['cfg'][0] != b['cfg'][0])
        print(f"  --> OOS: {s['n']} trades | win {s['win']}% | exp {s['exp']:+.3f}R | "
              f"PF {s['pf']} | totR {s['totR']:+.1f}")
        print(f"      P&L: {'  |  '.join(line)}")
        print(f"      direction flips week-to-week: {flips}/{len(log)-1}   "
              f"(high = whipsaw / no persistent regime)")
    print()

# ----------------------------- CSV export --------------------------------
ds, de = START.strftime('%Y%m%d'), END.strftime('%Y%m%d')
OUTDIR = os.path.dirname(os.path.abspath(__file__))
for tf_name, (oos, log, s, _) in tf_results.items():
    if not oos:
        continue
    path = os.path.join(OUTDIR, f"walkforward_{tf_name}_{ds}_{de}.csv")
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['# walk-forward OOS trades', symbol, tf_name,
                    'data_start', f"{START:%Y-%m-%d}", 'data_end', f"{END:%Y-%m-%d}",
                    'train_days', TRAIN_DAYS, 'test_days', TEST_DAYS])
        w.writerow(['week', 'entry_time', 'exit_time', 'direction', 'exit_reason',
                    'entry', 'exit_price', 'risk_usd_per_oz', 'result_R', 'strategy',
                    'pnl_0.01lot', 'pnl_0.1lot', 'pnl_1lot'])
        for t in oos:
            pnl = lambda lot: round(t['R'] * t['risk'] * CONTRACT * lot, 2)
            w.writerow([t['week'], f"{t['entry_time']:%Y-%m-%d %H:%M}",
                        f"{t['exit_time']:%Y-%m-%d %H:%M}", 'LONG' if t['dir'] > 0 else 'SHORT',
                        t['reason'], round(t['entry'], 2), round(t['exit_price'], 2),
                        round(t['risk'], 2), round(t['R'], 3), t['strat'],
                        pnl(0.01), pnl(0.1), pnl(1.0)])
    print(f"CSV: {path}")

# ----------------------------- Plotly chart ------------------------------
if chart_tfd and tf_results.get(CHART_TF) and tf_results[CHART_TF][0]:
    oos = tf_results[CHART_TF][0]
    D, c = chart_tfd['D'], chart_tfd['c']
    step = max(1, len(D) // 6000)                      # thin the price line for the browser
    px = D[::step]; py = c[::step]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28],
                        vertical_spacing=0.05,
                        subplot_titles=(f"{symbol} {CHART_TF} — walk-forward OOS entries/exits",
                                        "Out-of-sample cumulative R"))
    fig.add_trace(go.Scatter(x=px, y=py, mode='lines', name='price',
                             line=dict(color='#8a8f98', width=1)), row=1, col=1)

    def marks(cond, **kw):
        xs = [t['entry_time'] for t in oos if cond(t)]
        ys = [t['entry'] for t in oos if cond(t)]
        txt = [f"{t['strat']} {t['reason']} R={t['R']:+.2f}" for t in oos if cond(t)]
        return go.Scatter(x=xs, y=ys, mode='markers', text=txt,
                          hovertemplate='%{x}<br>%{y:.2f}<br>%{text}<extra></extra>', **kw)
    fig.add_trace(marks(lambda t: t['dir'] > 0, name='long entry',
                        marker=dict(symbol='triangle-up', size=9, color='#2fa572')), row=1, col=1)
    fig.add_trace(marks(lambda t: t['dir'] < 0, name='short entry',
                        marker=dict(symbol='triangle-down', size=9, color='#e0664f')), row=1, col=1)
    # exits colored by win/loss
    exs = [t['exit_time'] for t in oos]; eys = [t['exit_price'] for t in oos]
    ecol = ['#2fa572' if t['R'] > 0 else '#c0392b' for t in oos]
    etx = [f"exit {t['reason']} R={t['R']:+.2f}" for t in oos]
    fig.add_trace(go.Scatter(x=exs, y=eys, mode='markers', name='exit', text=etx,
                             marker=dict(symbol='x', size=6, color=ecol),
                             hovertemplate='%{x}<br>%{y:.2f}<br>%{text}<extra></extra>'), row=1, col=1)
    # equity curve
    eq = np.cumsum([t['R'] for t in oos])
    fig.add_trace(go.Scatter(x=[t['exit_time'] for t in oos], y=eq, mode='lines',
                             name='cum R', line=dict(color='#e8b84b', width=1.5),
                             fill='tozeroy'), row=2, col=1)
    fig.update_layout(template='plotly_dark', height=760, hovermode='closest',
                      title=f"XAUUSD walk-forward (weekly re-fit) — {CHART_TF} — "
                            f"{START:%b %d} to {END:%b %d} — OOS {len(oos)} trades")
    chart_path = os.path.join(OUTDIR, f"walkforward_chart_{CHART_TF}_{ds}_{de}.html")
    fig.write_html(chart_path, include_plotlyjs=True, full_html=True)
    print(f"CHART: {chart_path}")

mt5.shutdown()

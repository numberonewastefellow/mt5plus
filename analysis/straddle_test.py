r"""
XAUUSD volume-spike STRADDLE / BREAKOUT test  (direction-agnostic)
==================================================================

The unsolved problem is DIRECTION. This sidesteps it: a volume spike tells us a
BIG move is coming (corr ~0.7 with range) but not which way — so trade BOTH ways
and let the move pick the winner.

Two structures tested:
  BOTH_SIDES : at the spike, open long AND short at once (each: tight ATR stop,
               larger target). One leg stops out (-1R), the other hopefully runs.
               Failure mode = "double stop": price chops and BOTH stops hit (-2R).
  BREAKOUT   : buy-stop above the spike bar high, sell-stop below its low (OCO).
               Only the side price breaks into triggers; the other cancels. Avoids
               paying two stops on a clean one-way move; risk is whipsaw.

NOT a hedge (a hedge neutralises P&L). This is a volatility bet: profit iff the
winning leg runs further than the losing leg's stop + costs.

Entry at the bar AFTER the spike (realistic). ATR stop; TP = tp_r * stop; SL-first
on ambiguous bars; 1 spread cost per leg; time-stop at MAX_HOLD. Spikes clustered
within 30 min collapse to one event (no stacking).

Run:  ../XauOrderPad/.venv/Scripts/python.exe straddle_test.py <start> <end>   (YYYY-MM-DD)
"""
import datetime as dt
import os
import sys
import numpy as np
import MetaTrader5 as mt5

START = dt.datetime(2026, 5, 1)
END   = dt.datetime(2026, 6, 1)
if len(sys.argv) >= 3:
    START = dt.datetime.strptime(sys.argv[1], "%Y-%m-%d")
    END = dt.datetime.strptime(sys.argv[2], "%Y-%m-%d")
RVOL_WIN, ATR_N = 90, 14
RVOL_THR = 8.0
MAX_HOLD = 30
CLUSTER_GAP_MIN = 30
SPREAD_FALLBACK_PT = 200
G_SL_ATR = [0.5, 1.0]                 # stop distance = k * ATR (tight)
G_TP_R   = [2.0, 3.0, 4.0]            # target = tp_r * stop  (let winner run)
BUF_ATR  = 0.10                       # breakout stop buffer beyond the spike bar
LOTS = [0.01, 0.1, 1.0]

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
spr_pt = r['spread'].astype(float) if 'spread' in r.dtype.names else np.full(len(c), SPREAD_FALLBACK_PT)
spr_pt = np.where(spr_pt > 0, spr_pt, SPREAD_FALLBACK_PT)
n = len(c)
D = [dt.datetime.utcfromtimestamp(int(x)) for x in t]

rvol = np.full(n, np.nan)
for i in range(RVOL_WIN, n):
    b = np.median(vol[i - RVOL_WIN:i]); rvol[i] = vol[i] / b if b > 0 else np.nan
prevc = np.concatenate(([c[0]], c[:-1]))
tr = np.maximum.reduce([h - l, np.abs(h - prevc), np.abs(prevc - l)])
atr = np.full(n, np.nan)
for i in range(ATR_N, n):
    atr[i] = tr[i - ATR_N + 1:i + 1].mean()

# distinct spike events (dedupe clusters)
raw = [i for i in range(max(RVOL_WIN, ATR_N), n - 1)
       if not np.isnan(rvol[i]) and rvol[i] >= RVOL_THR and not np.isnan(atr[i]) and atr[i] > 0]
events = []
for i in raw:
    if events and (t[i] - t[events[-1]]) <= CLUSTER_GAP_MIN * 60:
        if rvol[i] > rvol[events[-1]]:
            events[-1] = i
        continue
    events.append(i)


def resolve_leg(dirn, entry, sl, tp, kstart):
    """Walk forward from bar kstart; return (price_pnl_in_leg_favor, reason)."""
    for k in range(kstart, min(n, kstart + MAX_HOLD)):
        if dirn > 0:
            hit_sl, hit_tp = l[k] <= sl, h[k] >= tp
        else:
            hit_sl, hit_tp = h[k] >= sl, l[k] <= tp
        if hit_sl:                       # SL-first on ambiguous bars
            return (sl - entry) if dirn > 0 else (entry - sl), 'SL'
        if hit_tp:
            return (tp - entry) if dirn > 0 else (entry - tp), 'TP'
    ex = c[min(n - 1, kstart + MAX_HOLD - 1)]
    return ((ex - entry) if dirn > 0 else (entry - ex)), 'TIME'


def both_sides(i, sl_atr, tp_r):
    S = sl_atr * atr[i]
    if S <= 0:
        return None
    spr = spr_pt[i] * POINT
    le = o[i + 1]; se = o[i + 1]
    lp, lr = resolve_leg(1, le, le - S, le + tp_r * S, i + 1)
    sp, sr = resolve_leg(-1, se, se + S, se - tp_r * S, i + 1)
    net = (lp - spr) + (sp - spr)        # spread cost per leg
    return dict(S=S, net=net, double_stop=(lr == 'SL' and sr == 'SL'),
                won=(lr == 'TP' or sr == 'TP'))


def breakout(i, sl_atr, tp_r):
    S = sl_atr * atr[i]
    if S <= 0:
        return None
    spr = spr_pt[i] * POINT
    buf = BUF_ATR * atr[i]
    bs, ss = h[i] + buf, l[i] - buf
    for k in range(i + 1, min(n, i + 1 + MAX_HOLD)):
        up, dn = h[k] >= bs, l[k] <= ss
        if not (up or dn):
            continue
        dirn = 1 if (up and (not dn or c[k] >= o[k])) else -1
        entry = bs if dirn > 0 else ss
        sl = entry - S if dirn > 0 else entry + S
        tp = entry + tp_r * S if dirn > 0 else entry - tp_r * S
        pnl, reason = resolve_leg(dirn, entry, sl, tp, k)
        return dict(S=S, net=pnl - spr, triggered=True, won=(reason == 'TP'), dirn=dirn)
    return dict(S=S, net=0.0, triggered=False, won=False, dirn=0)   # never broke out


def run(structure, sl_atr, tp_r):
    rows = []
    for i in events:
        res = (both_sides if structure == 'BOTH_SIDES' else breakout)(i, sl_atr, tp_r)
        if res:
            rows.append(res)
    if not rows:
        return None
    nets = np.array([x['net'] for x in rows])                 # price P&L per event
    Rs = np.array([x['net'] / x['S'] for x in rows])          # in leg-risk units
    wins = Rs[Rs > 0]; losses = Rs[Rs <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float('inf')
    out = dict(n=len(rows), win=round(100 * len(wins) / len(rows), 1),
               expR=round(float(Rs.mean()), 3), pf=round(float(pf), 2),
               net01=sum(x['net'] * CONTRACT * 0.1 for x in rows))
    if structure == 'BOTH_SIDES':
        out['dbl'] = round(100 * np.mean([x['double_stop'] for x in rows]), 0)
    else:
        out['trig'] = round(100 * np.mean([x['triggered'] for x in rows]), 0)
    return out


print(f"STRADDLE TEST | {symbol} | {START:%Y-%m-%d} -> {END:%Y-%m-%d} | M1 bars={n}")
print(f"Distinct volume-spike events (RVOL>= {RVOL_THR}x): {len(events)} | balance ${BALANCE:,.0f}\n")

for structure in ['BOTH_SIDES', 'BREAKOUT']:
    print(f"### {structure}")
    extra = 'dblStop%' if structure == 'BOTH_SIDES' else 'trig%'
    print(f"  {'SLxATR':>7}{'TP(R)':>7}{'events':>7}{'win%':>6}{'expR':>7}{'PF':>6}"
          f"{extra:>9}{'net$@0.1lot':>13}{'net%':>7}")
    for sl_atr in G_SL_ATR:
        for tp_r in G_TP_R:
            b = run(structure, sl_atr, tp_r)
            if not b:
                continue
            xv = b.get('dbl', b.get('trig', 0))
            print(f"  {sl_atr:>7}{tp_r:>7}{b['n']:>7}{b['win']:>6}{b['expR']:>7}{b['pf']:>6}"
                  f"{xv:>8.0f}%{b['net01']:>13.2f}{100*b['net01']/BALANCE:>6.1f}%")
    print()

mt5.shutdown()

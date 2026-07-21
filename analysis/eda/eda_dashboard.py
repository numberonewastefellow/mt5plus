"""
EDA dashboard -- one self-contained Plotly HTML: recent price with the strong
S/R levels drawn, MAs, volume, and big-move markers. The visual companion to
EDA_FINDINGS.md.

    C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe eda_dashboard.py
"""
from __future__ import annotations

import os

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from eda_lib import load_bars

OUT = os.path.dirname(os.path.abspath(__file__))


def strong_levels(min_touches=4):
    h1 = load_bars("H1")
    hh, ll = h1["high"].to_numpy(), h1["low"].to_numpy()
    w = 5
    sw = [hh[i] for i in range(w, len(hh) - w) if hh[i] == max(hh[i - w:i + w + 1])]
    sw += [ll[i] for i in range(w, len(ll) - w) if ll[i] == min(ll[i - w:i + w + 1])]
    buckets = np.round(np.array(sw) / 5.0) * 5.0
    vals, cnts = np.unique(buckets, return_counts=True)
    return list(zip(vals[cnts >= min_touches], cnts[cnts >= min_touches]))


def build():
    df = load_bars("H1")
    df = df.loc[df.index >= (df.index[-1] - np.timedelta64(75, "D"))]
    for s in (50, 200):
        df[f"ma{s}"] = load_bars("H1")["close"].rolling(s).mean().reindex(df.index)
    a = df["ret"].abs()
    big = a >= a.quantile(0.90)
    lo, hi = df["low"].min(), df["high"].max()
    levels = [(p, n) for p, n in strong_levels(4) if lo - 5 <= p <= hi + 5]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25],
                        vertical_spacing=0.04,
                        subplot_titles=("XAUUSD H1 — strong S/R (dashed), MA50/200, big moves ▲", "volume"))
    fig.add_trace(go.Candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"],
                                 close=df["close"], name="H1", showlegend=False), row=1, col=1)
    for span, col in ((50, "#e8b84b"), (200, "#5b8def")):
        fig.add_trace(go.Scatter(x=df.index, y=df[f"ma{span}"], name=f"MA{span}",
                                 line=dict(color=col, width=1.3)), row=1, col=1)
    # S/R levels: opacity/width by touch count
    mx = max(n for _, n in levels) if levels else 1
    for p, n in levels:
        fig.add_hline(y=p, line=dict(color="rgba(200,120,120,%.2f)" % (0.25 + 0.5 * n / mx),
                                     width=0.8 + 1.6 * n / mx, dash="dot"), row=1, col=1)
    # big-move markers
    bm = df[big]
    fig.add_trace(go.Scatter(x=bm.index, y=bm["high"] + 2, mode="markers", name="big move",
                             marker=dict(symbol="triangle-up", size=7, color="#2fa572")), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], name="volume",
                         marker_color="#8a8f98"), row=2, col=1)
    fig.update_layout(template="plotly_dark", height=820, xaxis_rangeslider_visible=False,
                      title="XAUUSD EDA — price structure, S/R levels, volume (last 75 days, H1)")
    path = os.path.join(OUT, "eda_dashboard.html")
    fig.write_html(path, include_plotlyjs=True, full_html=True)
    print(f"wrote {path}")
    print(f"strong S/R levels drawn (>=4 touches): {len(levels)}")


if __name__ == "__main__":
    build()

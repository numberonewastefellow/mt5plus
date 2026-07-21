"""
Shared EDA helpers. Run with test_env (pandas). Reads the .npz files that
fetch_data.py saved -- no MT5 needed here.

    env: C:\\Users\\Pandu\\.conda\\envs\\test_env\\python.exe   (pandas + numpy + plotly)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

# Windows consoles default to cp1252 and choke on any non-ASCII in prints.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TFS = ["D1", "H4", "H1", "M15", "M5", "M1"]

# bars-per-year for annualizing / per-TF context (approx, 24x5 market)
BARS_PER_YEAR = {"D1": 260, "H4": 260 * 6, "H1": 260 * 24,
                 "M15": 260 * 96, "M5": 260 * 288, "M1": 260 * 1440}
# expected seconds between consecutive bars (to detect gaps vs normal session breaks)
TF_SECONDS = {"D1": 86400, "H4": 14400, "H1": 3600, "M15": 900, "M5": 300, "M1": 60}


def load_bars(tf: str) -> pd.DataFrame:
    """One timeframe as a DataFrame indexed by time, with derived columns.

    Columns: open/high/low/close/volume/spread + ret, logret, range, body, hour, dow.
    df.attrs carries point, contract, tf.
    """
    d = np.load(os.path.join(DATA, f"bars_{tf}.npz"), allow_pickle=True)
    df = pd.DataFrame({
        "open": d["open"], "high": d["high"], "low": d["low"], "close": d["close"],
        "volume": d["tick_volume"], "spread": d["spread"],
    }, index=pd.to_datetime(d["time"], unit="s"))
    df.index.name = "time"
    df["ret"] = df["close"].diff()
    df["logret"] = np.log(df["close"]).diff()
    df["range"] = df["high"] - df["low"]
    df["body"] = (df["close"] - df["open"]).abs()
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek        # 0=Mon
    df.attrs.update(point=float(d["point"][0]), contract=float(d["contract"][0]), tf=tf)
    return df


def load_ticks() -> pd.DataFrame:
    d = np.load(os.path.join(DATA, "ticks_sample.npz"))
    df = pd.DataFrame({"bid": d["bid"], "ask": d["ask"]},
                      index=pd.to_datetime(d["t_msc"], unit="ms"))
    df.index.name = "t"
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread_pts"] = (df["ask"] - df["bid"]) / float(d["point"][0])
    df.attrs["point"] = float(d["point"][0])
    return df


def autocorr(x: np.ndarray, lags: int = 10) -> list[float]:
    """Autocorrelation of a 1-D series at lags 1..`lags` (nan-safe)."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    x = x - x.mean()
    denom = np.dot(x, x)
    out = []
    for k in range(1, lags + 1):
        out.append(float(np.dot(x[:-k], x[k:]) / denom) if denom > 0 and k < len(x) else np.nan)
    return out


def variance_ratio(logret: np.ndarray, q: int) -> float:
    """Lo-MacKinlay variance ratio. VR>1 trending, <1 mean-reverting, ~1 random walk."""
    r = np.asarray(logret, dtype=float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < q * 2:
        return np.nan
    var1 = r.var(ddof=1)
    if var1 == 0:
        return np.nan
    rq = np.convolve(r, np.ones(q), "valid")   # rolling q-sums
    varq = rq.var(ddof=1)
    return float(varq / (q * var1))


def kurt_skew(x: np.ndarray) -> tuple[float, float]:
    """Excess kurtosis and skew, numpy-only (avoids scipy)."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 4:
        return np.nan, np.nan
    m = x.mean()
    s = x.std()
    if s == 0:
        return np.nan, np.nan
    z = (x - m) / s
    return float((z ** 4).mean() - 3.0), float((z ** 3).mean())


def data_present() -> bool:
    return os.path.isdir(DATA) and any(f.startswith("bars_") for f in os.listdir(DATA))

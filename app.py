import os
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import io, json
from typing import Optional, List
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import mplfinance as mpf

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse

app = FastAPI(title="Signal Chart Overlay", version="1.2.0")

# ====== Env ======
TWELVE_KEY = os.getenv("TWELVE_KEY", "")
DEFAULT_TZ = os.getenv("APP_TZ", "UTC")
DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "XAU/USD")
DEFAULT_INTERVAL = os.getenv("DEFAULT_INTERVAL", "1h")

# ====== Tiny in-memory cache ======
_CACHE, _CACHE_TTL = {}, 60
def _cache_get(k):
    v = _CACHE.get(k)
    if not v: return None
    val, ts = v
    if (datetime.now(timezone.utc) - ts).total_seconds() > _CACHE_TTL:
        _CACHE.pop(k, None); return None
    return val
def _cache_set(k, v): _CACHE[k] = (v, datetime.now(timezone.utc))

# ====== Data fetch ======
def fetch_twelvedata(symbol: str, interval: str, bars: int, tz: str) -> pd.DataFrame:
    if not TWELVE_KEY:
        raise HTTPException(500, "TWELVE_KEY env var is missing.")
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol, "interval": interval,
        "outputsize": max(50, min(5000, bars)),
        "timezone": tz, "format": "JSON",
        "apikey": TWELVE_KEY, "order": "ASC",
    }
    ck = f"td:{json.dumps(params, sort_keys=True)}"
    c = _cache_get(ck)
    if c is not None: return c

    r = requests.get(url, params=params, timeout=30)
    try: data = r.json()
    except Exception: raise HTTPException(502, "Failed to parse provider response.")
    if data.get("status") == "error": raise HTTPException(502, data.get("message", "Provider error."))
    if "values" not in data: raise HTTPException(502, "Provider returned no data.")

    df = pd.DataFrame(data["values"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").set_index("datetime")
    df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"}, inplace=True)
    _cache_set(ck, df)
    return df

# ====== Drawing ======
def draw_signal_overlay(ax, entry: Optional[float], sl: Optional[float], tps: List[float], last_time: pd.Timestamp):
    if entry is not None:
        ax.axhline(entry, linestyle="--", linewidth=1.2)
        ax.text(last_time, entry, "  ENTRY", va="center", fontsize=8)
    if sl is not None:
        ax.axhline(sl, linestyle="-.", linewidth=1.2)
        ax.text(last_time, sl, "  SL", va="center", fontsize=8)
    if entry is not None and sl is not None:
        y0, y1 = sorted([entry, sl])
        ax.add_patch(Rectangle((last_time, y0), width=pd.Timedelta(minutes=1), height=(y1-y0), alpha=0.08))
    for i, tp in enumerate(tps, start=1):
        if tp is None: continue
        ax.axhline(tp, linestyle=":", linewidth=1.1)
        ax.text(last_time, tp, f"  TP{i}", va="center", fontsize=8)

def build_style(theme: str):
    # داكن افتراضي + بدون شبكة (زي TradingView/MT5)
    if theme.lower() == "light":
        return mpf.make_mpf_style(base_mpf_style="yahoo", gridstyle="")
    return mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        facecolor="#0d1117", figcolor="#0d1117", edgecolor="white",
        gridstyle="",
        rc={"axes.grid": False, "axes.labelcolor":"white", "xtick.color":"white", "ytick.color":"white"}
    )

# ====== Routes ======
@app.get("/health")
def health(): return {"ok": True, "service": "chart-service", "version": app.version}

@app.get("/")
def root(): return {"service": "Signal Chart Overlay", "docs": "/docs", "chart": "/chart"}

@app.get("/chart")
def chart(
    symbol: str = Query(DEFAULT_SYMBOL), interval: str = Query(DEFAULT_INTERVAL),
    bars: int = Query(300, ge=50, le=2000), theme: str = Query("dark"),
    show_volume: bool = Query(True), ma: Optional[str] = Query(None),
    draw_ema: bool = Query(True),
    entry: Optional[float] = Query(None), sl: Optional[float] = Query(None),
    tp1: Optional[float] = Query(None), tp2: Optional[float] = Query(None), tp3: Optional[float] = Query(None),
    title: Optional[str] = Query(None), tz: str = Query(DEFAULT_TZ),
):
    df = fetch_twelvedata(symbol, interval, bars, tz)
    if df.empty: raise HTTPException(404, "No data returned.")

    addplots, mav = [], None
    if ma:
        try: mav = [int(x.strip()) for x in ma.split(",") if x.strip()]
        except: raise HTTPException(400, "Invalid ma parameter, use like: 20,50,200")

    if draw_ema:
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        addplots += [mpf.make_addplot(df["EMA20"], width=1.0), mpf.make_addplot(df["EMA50"], width=1.0)]

    style = build_style(theme)
    fig, axes = mpf.plot(
        df, type="candle", style=style, volume=show_volume, mav=mav,
        addplot=addplots if addplots else None, tight_layout=True,
        xrotation=0, datetime_format="%Y-%m-%d %H:%M", returnfig=True
    )
    ax_price = axes[0]
    # تأكيد إلغاء الشبكة:
    for ax in fig.axes:
        try: ax.grid(False)
        except: pass

    last_time = df.index[-1]
    draw_signal_overlay(ax_price, entry, sl, [tp1, tp2, tp3], last_time)
    if title: ax_price.set_title(title)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=180)
    plt.close(fig); buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.get("/version")
def version():
    return {"version": app.version, "tz": DEFAULT_TZ, "default_symbol": DEFAULT_SYMBOL, "default_interval": DEFAULT_INTERVAL}

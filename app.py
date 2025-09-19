import os
# تشغيل matplotlib بدون واجهة رسومية وضبط كاش الإعدادات لمجلد مؤقت
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import io
import json
from typing import Optional, List
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import mplfinance as mpf

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse

# ================== App & Env ==================
app = FastAPI(title="Signal Chart Overlay", version="1.2.0")

TWELVE_KEY = os.getenv("TWELVE_KEY", "")
DEFAULT_TZ = os.getenv("APP_TZ", "UTC")
DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "XAU/USD")   # مثال للذهب
DEFAULT_INTERVAL = os.getenv("DEFAULT_INTERVAL", "1h")

# كاش بسيط في الذاكرة (مناسب لخطة Render المجانية)
_CACHE: dict = {}
_CACHE_TTL = 60  # ثواني


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if not hit:
        return None
    value, ts = hit
    if (datetime.now(timezone.utc) - ts).total_seconds() > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value):
    _CACHE[key] = (value, datetime.now(timezone.utc))


# ================== Utils ==================
def fetch_twelvedata(
    symbol: str,
    interval: str,
    bars: int,
    timezone_name: str = "UTC",
) -> pd.DataFrame:
    """
    يسحب OHLCV من TwelveData ويعيد DataFrame مناسب لـ mplfinance
    """
    if not TWELVE_KEY:
        raise HTTPException(status_code=500, detail="TWELVE_KEY env var is missing.")

    outputsize = max(50, min(5000, bars))
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "timezone": timezone_name,
        "format": "JSON",
        "apikey": TWELVE_KEY,
        "order": "ASC",
    }

    cache_key = f"td:{json.dumps(params, sort_keys=True)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    r = requests.get(url, params=params, timeout=30)
    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to parse provider response.")

    if "status" in data and data["status"] == "error":
        raise HTTPException(status_code=502, detail=data.get("message", "Provider error."))
    if "values" not in data:
        raise HTTPException(status_code=502, detail="Provider returned no data.")

    df = pd.DataFrame(data["values"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").set_index("datetime")

    df.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"},
        inplace=True,
    )

    _cache_set(cache_key, df)
    return df


def draw_signal_overlay(ax, entry: Optional[float], sl: Optional[float],
                        tps: List[float], last_time: pd.Timestamp):
    """
    يرسم خطوط Entry/SL/TPs وصندوق منطقة الخطر بين Entry و SL
    """
    if entry is not None:
        ax.axhline(entry, linestyle="--", linewidth=1.2)
        ax.text(last_time, entry, "  ENTRY", va="center", fontsize=8)

    if sl is not None:
        ax.axhline(sl, linestyle="-.", linewidth=1.2)
        ax.text(last_time, sl, "  SL", va="center", fontsize=8)

    if entry is not None and sl is not None:
        y0, y1 = sorted([entry, sl])
        ax.add_patch(
            Rectangle(
                (last_time, y0),
                width=pd.Timedelta(minutes=1),
                height=(y1 - y0),
                alpha=0.08,
            )
        )

    for i, tp in enumerate(tps, start=1):
        if tp is None:
            continue
        ax.axhline(tp, linestyle=":", linewidth=1.1)
        ax.text(last_time, tp, f"  TP{i}", va="center", fontsize=8)


def build_style(theme: str):
    """
    ستايل الشارت. الإعداد الافتراضي داكن شبيه بـ TradingView/MT5
    مع إلغاء الشبكة نهائيًا.
    """
    if theme.lower() == "light":
        return mpf.make_mpf_style(base_mpf_style="yahoo", gridstyle="")
    return mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        facecolor="#0d1117",
        figcolor="#0d1117",
        edgecolor="white",
        gridstyle="",                         # إلغاء الشبكة
        rc={
            "axes.grid": False,              # ضمان إلغاء الشبكة
            "axes.labelcolor": "white",
            "xtick.color": "white",
            "ytick.color": "white",
        },
    )


# ================== Routes ==================
@app.get("/health")
def health():
    return {"ok": True, "service": "chart-service", "version": app.version}


@app.get("/")
def root():
    return {"service": "Signal Chart Overlay", "docs": "/docs", "chart": "/chart"}


@app.get("/chart")
def chart(
    symbol: str = Query(DEFAULT_SYMBOL, description="مثال: XAU/USD أو EUR/USD أو AAPL"),
    interval: str = Query(DEFAULT_INTERVAL, description="مثال: 15min, 1h, 4h, 1day"),
    bars: int = Query(300, ge=50, le=2000, description="عدد الشموع"),
    theme: str = Query("dark", description="dark | light"),
    show_volume: bool = Query(True),
    ma: Optional[str] = Query(None, description="قائمة موفينج افريج مفصولة بفواصل: 20,50,200"),
    draw_ema: bool = Query(True, description="إضافة EMA20/EMA50"),
    # إحداثيات الصفقة (اختياري)
    entry: Optional[float] = Query(None),
    sl: Optional[float] = Query(None),
    tp1: Optional[float] = Query(None),
    tp2: Optional[float] = Query(None),
    tp3: Optional[float] = Query(None),
    title: Optional[str] = Query(None),
    tz: str = Query(DEFAULT_TZ, description="Timezone name - e.g. UTC, Africa/Cairo"),
):
    """
    يولد صورة PNG لتشارت شموع مع أوڤرلاي صفقة (وشبكة ملغية).
    """
    try:
        df = fetch_twelvedata(symbol, interval, bars, tz)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"data error: {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail="No data returned.")

    # مؤشرات بسيطة
    addplots = []
    mav = None
    if ma:
        try:
            mav = [int(x.strip()) for x in ma.split(",") if x.strip()]
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid ma parameter, use like: 20,50,200")

    if draw_ema:
        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        addplots.append(mpf.make_addplot(df["EMA20"], width=1.0))
        addplots.append(mpf.make_addplot(df["EMA50"], width=1.0))

    style = build_style(theme)
    kwargs = {
        "type": "candle",
        "style": style,
        "volume": show_volume,
        "mav": mav,
        "addplot": addplots if addplots else None,
        "tight_layout": True,
        "xrotation": 0,
        "datetime_format": "%Y-%m-%d %H:%M",
    }

    fig, axes = mpf.plot(df, returnfig=True, **kwargs)
    ax_price = axes[0]

    # إلغاء الشبكة من جميع المحاور (تأكيد)
    for ax in fig.axes:
        try:
            ax.grid(False)
        except Exception:
            pass

    # رسم الأهداف و SL/Entry على آخر محور
    last_time = df.index[-1]
    tps = [tp1, tp2, tp3]
    try:
        draw_signal_overlay(ax_price, entry, sl, tps, last_time)
    except Exception:
        pass

    if title:
        ax_price.set_title(title)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=180)
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/version")
def version():
    return {"version": app.version, "tz": DEFAULT_TZ, "default_symbol": DEFAULT_SYMBOL, "default_interval": DEFAULT_INTERVAL}

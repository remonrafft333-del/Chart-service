# app.py
import io
import os
import requests
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt

from datetime import timezone
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from matplotlib.patches import Rectangle
from typing import Optional, Union

app = FastAPI(title="Signal Chart Overlay")

# مفاتيح/إعدادات من Render
TWELVE_KEY = os.getenv("TWELVE_KEY")          # ضروري
DEFAULT_LOGO = os.getenv("LOGO_URL", "")      # اختياري: PNG شفاف

# ---------------- Helpers ----------------
def fetch_ohlc(symbol: str, interval: str, bars: int = 300) -> pd.DataFrame:
    """
    TwelveData time_series API (interval أمثلة: 1h, 30min, 15min, 4h, 1day)
    """
    if not TWELVE_KEY:
        raise HTTPException(500, "TWELVE_KEY is not configured")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": bars,
        "apikey": TWELVE_KEY,
        "format": "JSON",
        "timezone": "UTC",
    }
    r = requests.get(url, params=params, timeout=30)
    data = r.json()
    if "values" not in data:
        raise HTTPException(502, f"TwelveData error: {data}")

    df = pd.DataFrame(data["values"])
    df.rename(
        columns={"datetime": "Date", "open": "Open", "high": "High",
                 "low": "Low", "close": "Close"},
        inplace=True
    )
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    for c in ["Open", "High", "Low", "Close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.sort_values("Date", inplace=True)
    df.set_index("Date", inplace=True)
    return df


def add_zone(ax, y1: float, y2: float, color: str, alpha: float = 0.18):
    """مستطيل ملوّن بين مستويين (لمنطقة خسارة/ربح)."""
    lower, upper = (min(y1, y2), max(y1, y2))
    x0, x1 = ax.get_xlim()
    rect = Rectangle(
        (x0, lower), x1 - x0, upper - lower,
        linewidth=0, facecolor=color, alpha=alpha, zorder=0
    )
    ax.add_patch(rect)


def add_level(ax, y: float, color: str, label: str):
    """خط أفقي + لابل على يمين الشارت."""
    ax.axhline(y, linestyle="--", linewidth=1.4, color=color)
    ax.text(
        ax.get_xlim()[1], y, f" {label}: {y:.2f}",
        va="center", ha="left", fontsize=10, color=color,
        bbox=dict(facecolor="none", edgecolor="none", pad=0.1)
    )


def add_profit_zone(ax, entry: float, tps: list[Optional[float]], direction: str):
    """منطقة الربح بين الدخول وأهداف الربح حسب الاتجاه."""
    levels = [t for t in tps if t is not None]
    if not levels:
        return
    profit_color = "#6FCF97"  # أخضر لطيف
    if direction == "SELL":
        lower, upper = min(levels), entry
    else:  # BUY
        lower, upper = entry, max(levels)
    if lower != upper:
        add_zone(ax, lower, upper, profit_color, alpha=0.12)


def coerce_ax(axes_obj: Union[plt.Axes, list, tuple]):
    """mpf.plot قد يعيد محور واحد أو قائمة محاور؛ نرجّع المحور الرئيسي."""
    if isinstance(axes_obj, (list, tuple)):
        return axes_obj[0]
    return axes_obj

# ---------------- API ----------------
@app.get("/chart", response_class=StreamingResponse)
def chart(
    symbol: str = Query(..., example="XAU/USD"),
    interval: str = Query("1h", description="1h,30min,15min,4h,1day"),
    direction: str = Query(..., regex="^(BUY|SELL)$"),
    entry: float = Query(...),
    sl: float = Query(...),
    tp1: Optional[float] = Query(None),
    tp2: Optional[float] = Query(None),
    tp3: Optional[float] = Query(None),
    theme: str = Query("dark", regex="^(dark|light)$"),
    title: Optional[str] = Query(None),
    bars: int = Query(300),
):
    # (1) بيانات OHLC
    df = fetch_ohlc(symbol, interval, bars=bars)

    # (2) الثيم والرسم الأساسي بألوان قريبة من MT/TradingView
    mc = mpf.make_marketcolors(
        up="#00B5FF",      # سماوي للشموع الصاعدة
        down="#FF3B30",    # أحمر للهابطة
        wick="white",
        edge="inherit",
        volume="inherit",
    )
    style = mpf.make_mpf_style(
        base_mpf_style=("nightclouds" if theme == "dark" else "yahoo"),
        marketcolors=mc,
        gridstyle="--",
        facecolor="#0B0E11" if theme == "dark" else "#FFFFFF",
        figcolor="#0B0E11" if theme == "dark" else "#FFFFFF",
        rc={
            "axes.edgecolor": "#C0C0C0" if theme == "dark" else "#333333",
            "axes.labelcolor": "#C0C0C0" if theme == "dark" else "#333333",
            "xtick.color": "#C0C0C0" if theme == "dark" else "#333333",
            "ytick.color": "#C0C0C0" if theme == "dark" else "#333333",
            "grid.color": "#2B2F36" if theme == "dark" else "#DDDDDD",
        },
    )

    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        volume=False,
        returnfig=True,
        figsize=(12, 7),
        tight_layout=True,
        update_width_config=dict(
            candle_linewidth=1.0, candle_width=0.6, wick_linewidth=0.8
        ),
    )
    ax = coerce_ax(axes)

    # (3) منطقة الخسارة + الربح + خطوط Entry/TP/SL
    loss_color = "#FF5C5C"     # أحمر شفاف للخسارة
    entry_color = "#2F80ED"    # أزرق للدخول
    tp_color = "#2F80ED"       # أزرق للأهداف

    add_zone(ax, entry, sl, loss_color, alpha=0.18)                     # خسارة
    add_profit_zone(ax, entry, [tp1, tp2, tp3], direction)              # ربح

    add_level(ax, sl,    "#FF3B30", "SL")
    add_level(ax, entry, entry_color, "Entry")
    if tp1 is not None: add_level(ax, tp1, tp_color, "TP1")
    if tp2 is not None: add_level(ax, tp2, tp_color, "TP2")
    if tp3 is not None: add_level(ax, tp3, tp_color, "TP3")

    # (4) خط السعر اللحظي + عنوان بالتوقيت
    last_ts = df.index[-1]
    last_close = float(df["Close"][-1])

    ax.axhline(last_close, linewidth=1.6, linestyle="-", color="#00D1B2", alpha=0.9)
    ax.text(
        ax.get_xlim()[1], last_close, f"  Last: {last_close:.2f}",
        va="center", ha="left", fontsize=10, color="#00D1B2",
        bbox=dict(facecolor="none", edgecolor="none", pad=0.1)
    )

    ttl = title or f"{symbol}  |  {interval}  |  {direction}"
    # اطبع الطابع الزمني كـ UTC دايماً
    ts_str = last_ts.tz_convert(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") \
             if getattr(last_ts, "tzinfo", None) \
             else last_ts.tz_localize("UTC").strftime("%Y-%m-%d %H:%M UTC")
    ax.set_title(f"{ttl}   •   {ts_str}", fontsize=14, color=("#EAEAEA" if theme == "dark" else "#222"))

    # (5) لوجو اختياري (PNG شفاف)
    if DEFAULT_LOGO:
        try:
            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
            from PIL import Image
            logo_im = Image.open(requests.get(DEFAULT_LOGO, stream=True, timeout=10).raw)
            oi = OffsetImage(logo_im, zoom=0.18)
            ab = AnnotationBbox(
                oi, (ax.get_xlim()[0], ax.get_ylim()[1]),
                xybox=(25, -25), xycoords="data",
                boxcoords=("offset points"), frameon=False
            )
            ax.add_artist(ab)
        except Exception:
            pass

    # (6) إخراج PNG
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

# app.py
import io, os, requests, pandas as pd, mplfinance as mpf
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse
from datetime import timezone
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

app = FastAPI(title="Signal Chart Overlay")

# ====== Env ======
TWELVE_KEY   = os.getenv("TWELVE_KEY")           # لازم تضيفه في Render
DEFAULT_LOGO = os.getenv("LOGO_URL", "")         # اختياري PNG شفاف

# ====== Helpers ======
def fetch_ohlc(symbol: str, interval: str, bars: int = 300) -> pd.DataFrame:
    """Fetch OHLC from TwelveData."""
    if not TWELVE_KEY:
        raise HTTPException(500, "TWELVE_KEY is not configured")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,        # 1h, 30min, 15min, 4h, 1day
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
    df.rename(columns={
        "datetime": "Date", "open": "Open", "high": "High",
        "low": "Low", "close": "Close"
    }, inplace=True)
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    for c in ["Open", "High", "Low", "Close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.sort_values("Date", inplace=True)
    df.set_index("Date", inplace=True)
    return df

def add_tp_sl(ax, level: float, color: str, label: str):
    ax.axhline(level, linestyle="--", linewidth=1.6, color=color, alpha=0.95)
    ax.text(ax.get_xlim()[1], level, f"  {label}: {level:.2f}",
            va="center", ha="left", fontsize=10, color=color,
            bbox=dict(facecolor="none", edgecolor="none", pad=0.2))

def add_zone(ax, y1: float, y2: float, color: str, alpha: float = 0.20):
    lo, hi = (min(y1, y2), max(y1, y2))
    # مستطيل يغطي عرض المحور
    x0, x1 = ax.get_xlim()
    rect = Rectangle((x0, lo), x1 - x0, hi - lo,
                     linewidth=0, facecolor=color, alpha=alpha, zorder=0)
    ax.add_patch(rect)

# ====== Routes ======
@app.get("/", response_class=PlainTextResponse)
def root():
    return "✅ Chart-service is running"

@app.get("/chart", response_class=StreamingResponse)
def chart(
    symbol: str   = Query(..., example="XAU/USD"),
    interval: str = Query("1h", description="1h,30min,15min,4h,1day"),
    direction: str = Query(..., regex="^(BUY|SELL)$"),
    entry: float  = Query(...),
    sl: float     = Query(...),
    tp1: float    = Query(...),
    tp2: float | None = Query(None),
    tp3: float | None = Query(None),
    theme: str    = Query("dark", regex="^(dark|light)$"),
    title: str | None = Query(None),
    bars: int     = Query(300),
):
    # (1) بيانات
    df = fetch_ohlc(symbol, interval, bars=bars)

    # (2) الثيم والشموع — خلفية داكنة، بدون شبكة
    mc = mpf.make_marketcolors(
        up='#00e676', down='#ff4d4f',
        edge='inherit', wick='inherit', volume='inherit'
    )
    base_style = "nightclouds" if theme == "dark" else "yahoo"
    style = mpf.make_mpf_style(
        base_mpf_style=base_style,
        marketcolors=mc,
        facecolor=("#111111" if theme == "dark" else "white"),
        figcolor=("#111111" if theme == "dark" else "white"),
        edgecolor="#888888",
        gridstyle=""  # ⇐ لا شبكة
    )

    fig, axes = mpf.plot(
        df, type="candle", style=style, volume=False,
        returnfig=True, figsize=(12, 7), tight_layout=True
    )
    ax = axes[0]  # المحور الرئيسي
    # برضه نطفي شبكة matplotlib احتياطيًا
    ax.grid(False)

    # (3) منطقة الصفقة + مستويات
    zone_color = "#d62728" if direction == "SELL" else "#2ca02c"
    add_zone(ax, entry, sl, zone_color, alpha=0.20)

    add_tp_sl(ax, sl,    "#ff4d4f", "SL")
    add_tp_sl(ax, entry, "#1f77b4", "Entry")
    if tp1 is not None: add_tp_sl(ax, tp1, "#fbbf24", "TP1")
    if tp2 is not None: add_tp_sl(ax, tp2, "#22c55e", "TP2")
    if tp3 is not None: add_tp_sl(ax, tp3, "#06b6d4", "TP3")

    # (4) خط السعر اللحظي + التاريخ (باستخدام iloc)
    last_ts    = df.index[-1]
    last_close = float(df["Close"].iloc[-1])  # ✅ iloc
    ax.axhline(last_close, linewidth=1.8, linestyle="-",
               color="#00d1b2", alpha=0.95)
    ax.text(ax.get_xlim()[1], last_close, f"  Last: {last_close:.2f}",
            va="center", ha="left", fontsize=10, color="#00d1b2",
            bbox=dict(facecolor="none", edgecolor="none", pad=0.2))

    ttl    = title or f"{symbol}  |  {interval}  |  {direction}"
    # اطبع UTC حتى لو الـ index بدون tzinfo
    try:
        ts_str = last_ts.tz_convert(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        ts_str = last_ts.strftime("%Y-%m-%d %H:%M UTC")
    ax.set_title(f"{ttl}     {ts_str}", fontsize=14, color=("#f2f2f2" if theme=="dark" else "black"))

    # (5) لوجو اختياري
    if DEFAULT_LOGO:
        try:
            import PIL.Image as Image
            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
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

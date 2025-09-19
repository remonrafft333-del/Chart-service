# app.py
import io, os, requests, pandas as pd, mplfinance as mpf
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from datetime import timezone
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

app = FastAPI(title="Signal Chart Overlay")

TWELVE_KEY = os.getenv("TWELVE_KEY")          # ضيفه في Render
DEFAULT_LOGO = os.getenv("LOGO_URL", "")      # اختياري: رابط PNG شفاف

# ---------------- Helpers ----------------
def fetch_ohlc(symbol: str, interval: str, bars: int = 300) -> pd.DataFrame:
    """
    TwelveData time_series API
    interval أمثلة: 1h, 30min, 15min, 4h, 1day
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
        columns={"datetime": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close"},
        inplace=True,
    )
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    for c in ["Open", "High", "Low", "Close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.sort_values("Date", inplace=True)
    df.set_index("Date", inplace=True)
    return df


def add_tp_sl(ax, level: float, color: str, label: str):
    ax.axhline(level, linestyle="--", linewidth=1.4, color=color, alpha=0.95)
    # لاصقة يمين المحور
    x1 = ax.get_xlim()[1]
    ax.text(
        x1, level, f"  {label}: {level:.2f}",
        va="center", ha="left", fontsize=9, color=color,
        bbox=dict(facecolor="none", edgecolor="none", pad=0.1)
    )


def add_zone(ax, y1: float, y2: float, color: str, alpha: float = 0.18):
    low, high = (min(y1, y2), max(y1, y2))
    x0, x1 = ax.get_xlim()
    rect = Rectangle((x0, low), x1 - x0, high - low, linewidth=0, facecolor=color, alpha=alpha, zorder=0)
    ax.add_patch(rect)


# ---------------- API ----------------
@app.get("/", response_class=JSONResponse)
def root():
    return {"ok": True, "service": "chart", "msg": "Service is running"}

@app.get("/chart", response_class=StreamingResponse)
def chart(
    symbol: str = Query(..., example="XAU/USD"),
    interval: str = Query("1h", description="1h,30min,15min,4h,1day"),
    direction: str = Query(..., regex="^(BUY|SELL)$"),
    entry: float = Query(...),
    sl: float = Query(...),
    tp1: float = Query(...),
    tp2: float | None = Query(None),
    tp3: float | None = Query(None),
    theme: str = Query("dark", regex="^(dark|light)$"),
    title: str | None = Query(None),
    bars: int = Query(300),
):
    # 1) OHLC
    df = fetch_ohlc(symbol, interval, bars=bars)

    # 2) الثيم والألوان (ستايل قريب من TV/MT4)
    # ألوان الشموع:
    up_color = "#2ad37d"     # أخضر فاتح للصعود
    down_color = "#ff5a5a"   # أحمر هابط

    mc = mpf.make_marketcolors(
        up=up_color, down=down_color,
        edge={"up": up_color, "down": down_color},
        wick={"up": up_color, "down": down_color},
        volume="inherit",
        ohlc="inherit",
    )

    # خلفية داكنة وشبكة خفيفة
    face = "#0d1117" if theme == "dark" else "white"
    gridc = "#3a3f44" if theme == "dark" else "#d0d7de"

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds" if theme == "dark" else "yahoo",
        marketcolors=mc,
        facecolor=face,
        edgecolor=face,
        gridcolor=gridc,
        gridstyle="--",
        figcolor=face,
        rc={
            "axes.labelcolor": "#c9d1d9" if theme == "dark" else "#24292f",
            "xtick.color": "#c9d1d9" if theme == "dark" else "#24292f",
            "ytick.color": "#c9d1d9" if theme == "dark" else "#24292f",
        },
    )

    # 3) الرسم
    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        volume=False,
        returnfig=True,
        figsize=(12, 7),
        tight_layout=True,
        update_width_config=dict(
            candle_linewidth=1.0,  # مدعوم
            candle_width=0.6       # مدعوم
        ),
    )
    ax = axes[0]

    # منطقة الصفقة + مستويات
    zone_color = "#d62728" if direction == "SELL" else "#2ca02c"
    add_zone(ax, entry, sl, zone_color, alpha=0.22)

    add_tp_sl(ax, sl, "#d62728", "SL")
    add_tp_sl(ax, entry, "#1f77b4", "Entry")
    if tp1 is not None:
        add_tp_sl(ax, tp1, "#ffbf00", "TP1")   # ذهبي خفيف
    if tp2 is not None:
        add_tp_sl(ax, tp2, "#00d12e", "TP2")
    if tp3 is not None:
        add_tp_sl(ax, tp3, "#00d12e", "TP3")

    # 4) السعر اللحظي + العنوان بطابع زمني
    last_ts = df.index[-1]
    last_close = float(df["Close"][-1])

    ax.axhline(last_close, linewidth=1.6, linestyle="-", color="#00d1b2", alpha=0.95)
    x1 = ax.get_xlim()[1]
    ax.text(
        x1, last_close, f"  Last: {last_close:.2f}",
        va="center", ha="left", fontsize=10, color="#00d1b2",
        bbox=dict(facecolor="none", edgecolor="none", pad=0.1)
    )

    ttl = title or f"{symbol}  |  {interval}  |  {direction}"
    # لو الختم بدون tzinfo نزوده UTC عشان ما يكسرش strftime
    if last_ts.tzinfo is None:
        from pandas import Timestamp
        last_ts = Timestamp(last_ts, tz=timezone.utc)
    ts_str = last_ts.tz_convert(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ax.set_title(f"{ttl}   •   {ts_str}", fontsize=14, color="#c9d1d9" if theme == "dark" else "#24292f")

    # 5) لوجو اختياري
    if DEFAULT_LOGO:
        try:
            import PIL.Image as Image
            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
            logo_im = Image.open(requests.get(DEFAULT_LOGO, stream=True, timeout=10).raw)
            oi = OffsetImage(logo_im, zoom=0.18)
            ab = AnnotationBbox(
                oi,
                (ax.get_xlim()[0], ax.get_ylim()[1]),
                xybox=(25, -25),
                xycoords="data",
                boxcoords=("offset points"),
                frameon=False,
            )
            ax.add_artist(ab)
        except Exception:
            # تجاهل أي خطأ في اللوجو
            pass

    # 6) إخراج PNG
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

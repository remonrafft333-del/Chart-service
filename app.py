import io, os, requests, pandas as pd, mplfinance as mpf
from datetime import datetime, timezone
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

app = FastAPI(title="Signal Chart Overlay")

# ====== ENV ======
TWELVE_KEY = os.getenv("TWELVE_KEY")              # مطلوب
DEFAULT_LOGO = os.getenv("LOGO_URL", "")          # اختياري (PNG شفاف)

# ====== Helpers ======
def fetch_ohlc(symbol: str, interval: str, bars: int = 300) -> pd.DataFrame:
    """Fetch OHLC from TwelveData time_series API."""
    if not TWELVE_KEY:
        raise HTTPException(500, "TWELVE_KEY is not configured")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,          # e.g. 1h, 30min, 15min, 4h, 1day
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


def add_tp_sl(ax, level: float, color: str, label: str):
    ax.axhline(level, linestyle="--", linewidth=1.4, color=color, alpha=0.95)
    # لاصق عند يمين المحور
    x_right = ax.get_xlim()[1]
    ax.text(
        x_right, level, f"  {label}: {level:.2f}",
        va="center", ha="left", fontsize=10, color=color,
        bbox=dict(facecolor="none", edgecolor="none", pad=0.2)
    )


def add_zone(ax, y1: float, y2: float, color: str, alpha: float = 0.22):
    lo, hi = (min(y1, y2), max(y1, y2))
    x0, x1 = ax.get_xlim()
    rect = Rectangle(
        (x0, lo), x1 - x0, hi - lo,
        linewidth=0, facecolor=color, alpha=alpha, zorder=0
    )
    ax.add_patch(rect)


# ====== Routes ======
@app.get("/", response_class=JSONResponse)
def home():
    return {"ok": True, "service": "Chart overlay is running."}


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
    # 1) بيانات
    df = fetch_ohlc(symbol, interval, bars=bars)

    # 2) الثيم وألوان الشموع (ميتاتريدر ستايل)
    # خلفية داكنة، شبكة خفيفة، شموع: up=lime/down=red
    mc = mpf.make_marketcolors(
        up="lime", down="#ff3b3b",
        edge="inherit",
        wick="inherit",
        volume="inherit"
    )
    base = "nightclouds" if theme == "dark" else "yahoo"
    style = mpf.make_mpf_style(
        base_mpf_style=base,
        marketcolors=mc,
        facecolor="#111111" if theme == "dark" else "white",
        figcolor="#111111" if theme == "dark" else "white",
        edgecolor="#888888",
        gridcolor="#444444",
        gridstyle="--",
        rc={
            "axes.labelcolor": ("white" if theme == "dark" else "black"),
            "xtick.color":     ("#e6e6e6" if theme == "dark" else "black"),
            "ytick.color":     ("#e6e6e6" if theme == "dark" else "black"),
        }
    )

    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        volume=False,
        returnfig=True,
        figsize=(12, 7),
        tight_layout=True
    )
    ax = axes[0]  # المحور الرئيسي

    # 3) منطقة الدخول + مستويات
    zone_color = "#d62728" if direction == "SELL" else "#2ca02c"
    add_zone(ax, entry, sl, zone_color, alpha=0.22)

    add_tp_sl(ax, sl,  "#ff5252", "SL")
    add_tp_sl(ax, entry, "#3da5ff", "Entry")
    if tp1 is not None:
        add_tp_sl(ax, tp1, "#ffb347", "TP1")
    if tp2 is not None:
        add_tp_sl(ax, tp2, "#7CFC00", "TP2")
    if tp3 is not None:
        add_tp_sl(ax, tp3, "#B19CD9", "TP3")

    # 4) خط السعر اللحظي + عنوان بالطابع الزمني
    last_ts = df.index[-1]
    last_close = float(df["Close"][-1])
    ax.axhline(last_close, linewidth=1.6, linestyle="-", color="#00d1b2", alpha=0.95)
    x_right = ax.get_xlim()[1]
    ax.text(
        x_right, last_close, f"  Last: {last_close:.2f}",
        va="center", ha="left", fontsize=10, color="#00d1b2",
        bbox=dict(facecolor="none", edgecolor="none", pad=0.2)
    )

    ttl = title or f"{symbol}  |  {interval}  |  {direction}"
    ts_str = (
        last_ts.tz_convert(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if last_ts.tzinfo else last_ts.strftime("%Y-%m-%d %H:%M UTC")
    )
    ax.set_title(f"{ttl}     |     {ts_str}", fontsize=14, color=("#f2f2f2" if theme=="dark" else "black"))

    # 5) لوجو اختياري
    if DEFAULT_LOGO:
        try:
            import PIL.Image as Image
            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
            logo_im = Image.open(requests.get(DEFAULT_LOGO, stream=True, timeout=8).raw)
            oi = OffsetImage(logo_im, zoom=0.2)
            # أعلى يسار داخل الإطار
            ab = AnnotationBbox(
                oi, (ax.get_xlim()[0], ax.get_ylim()[1]),
                xybox=(25, -25), xycoords="data",
                boxcoords=("offset points"), frameon=False
            )
            ax.add_artist(ab)
        except Exception:
            pass

    # 6) إخراج PNG
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

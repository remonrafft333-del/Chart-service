from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
import io
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt
import requests
import os

app = FastAPI()

# ======================
# جلب بيانات OHLC
# ======================
def fetch_ohlc(symbol: str, interval: str = "1h", bars: int = 100):
    api_key = os.getenv("TWELVE_KEY")
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={bars}&apikey={api_key}"
    r = requests.get(url)
    data = r.json()
    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df.set_index("datetime", inplace=True)
    df = df.astype(float)
    df = df.iloc[::-1]  # عكس الترتيب (أحدث شمعة في الآخر)
    return df

# ======================
# رسم المستويات Zones
# ======================
def add_zone(ax, entry, sl, zone_color, alpha=0.18):
    ax.axhspan(entry, sl, color=zone_color, alpha=alpha)

def add_tp_sl(ax, level, label, color):
    if level is not None:
        ax.axhline(level, color=color, linestyle="--", linewidth=1.2)
        ax.text(
            ax.get_xlim()[1], level, f" {label}: {level:.2f}",
            va="center", ha="left", color=color, fontsize=9
        )

# ======================
# API Endpoint
# ======================
@app.get("/chart")
def chart(
    symbol: str = Query("XAU/USD"),
    interval: str = Query("1h"),
    direction: str = Query("SELL"),
    entry: float = Query(...),
    sl: float = Query(...),
    tp1: float = Query(None),
    tp2: float = Query(None),
    tp3: float = Query(None),
):
    df = fetch_ohlc(symbol, interval)

    # اختيار الثيم
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        gridstyle=""  # 🔴 مفيش شبكة هنا
    )

    # رسم الشموع
    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        volume=False,
        returnfig=True,
        figsize=(12, 7),
        tight_layout=True
    )

    ax = axes[0]

    # مناطق دخول وخروج
    zone_color = "#d62728" if direction.upper() == "SELL" else "#2ca02c"
    add_zone(ax, entry, sl, zone_color, alpha=0.18)
    add_tp_sl(ax, sl, "SL", "red")
    add_tp_sl(ax, entry, "Entry", "blue")
    add_tp_sl(ax, tp1, "TP1", "orange")
    add_tp_sl(ax, tp2, "TP2", "green")
    add_tp_sl(ax, tp3, "TP3", "purple")

    # حفظ وإرجاع
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

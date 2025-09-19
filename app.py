import io
import os
import requests
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from datetime import datetime, timezone
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image

app = FastAPI()

# ===============================
# جلب بيانات OHLC
# ===============================
def fetch_ohlc(symbol, interval, bars=100):
    API_KEY = os.getenv("TWELVE_KEY")
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={bars}&apikey={API_KEY}"
    r = requests.get(url)
    data = r.json()

    if "values" not in data:
        raise Exception("Error fetching data from API")

    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df.set_index("datetime", inplace=True)
    df = df.astype(float)
    return df.iloc[::-1]


# ===============================
# رسم منطقة SL / TP
# ===============================
def add_zone(ax, entry, sl, zone_color, alpha=0.18):
    x0, x1 = ax.get_xlim()
    ax.axhspan(sl, entry, facecolor=zone_color, alpha=alpha)


def add_tp_sl(ax, level, label, color):
    if level is not None:
        ax.axhline(y=level, color=color, linestyle="--", linewidth=1)
        ax.text(
            ax.get_xlim()[1], level, label,
            color=color, fontsize=10, ha="right", va="bottom"
        )


# ===============================
# Endpoint الأساسي
# ===============================
@app.get("/chart")
def chart(symbol: str, interval: str, direction: str,
          entry: float, sl: float, tp1: float = None, tp2: float = None):

    # (1) بيانات OHLC
    df = fetch_ohlc(symbol, interval, bars=100)

    # (2) الثيم والرسم الأساسي
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        gridstyle="--"
    )

    fig, axes = mpf.plot(
        df, type="candle", style=style, volume=False,
        returnfig=True, figsize=(12, 7), tight_layout=True
    )

    ax = axes[0]  # المحور الرئيسي

    # (3) منطقة الصفقة + مستويات TP/SL
    zone_color = "#d62728" if direction.upper() == "SELL" else "#2ca02c"
    add_zone(ax, entry, sl, zone_color)

    add_tp_sl(ax, sl, "SL", "#d62728")
    add_tp_sl(ax, entry, "Entry", "#1f77b4")
    if tp1: add_tp_sl(ax, tp1, "TP1", "#ff7f0e")
    if tp2: add_tp_sl(ax, tp2, "TP2", "#2ca02c")

    # (4) إخراج PNG
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)

    return StreamingResponse(buf, media_type="image/png")

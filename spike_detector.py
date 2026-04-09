"""
spike_detector.py — Korrigierter Spike-Detector
"""

import os
import pandas as pd
import numpy as np
from dataclasses import dataclass

MIN_PRICE    = float(os.getenv("SPIKE_MIN_PRICE", "2.0"))
MIN_PCT      = float(os.getenv("SPIKE_MIN_PCT",   "4.0"))
MIN_RVOL     = float(os.getenv("SPIKE_MIN_RVOL",  "2.0"))
MIN_PTH      = float(os.getenv("SPIKE_MIN_PTH",   "0.70"))


@dataclass
class SpikeResult:
    ticker:     str
    level:      int
    pct_change: float
    rvol:       float
    pth:        float
    price:      float
    volume:     int
    signal:     bool
    reversion_risk: bool
    note:       str


def detect_spike(ticker: str, daily: pd.DataFrame) -> SpikeResult:
    empty = SpikeResult(ticker, 0, 0.0, 0.0, 0.0, 0.0, 0, False, False,
                        "Unzureichende Daten")
    if daily is None or len(daily) < 22:
        return empty

    today     = daily.iloc[-1]
    yesterday = daily.iloc[-2]

    price  = float(today["close"])
    prev   = float(yesterday["close"])
    open_  = float(today["open"])
    high   = float(today["high"])
    low    = float(today["low"])
    volume = int(today["volume"])

    if price < MIN_PRICE or prev <= 0:
        return empty

    pct = (price - prev) / prev * 100
    if pct < MIN_PCT:
        return SpikeResult(ticker, 0, round(pct, 2), 0.0, 0.0, price,
                           volume, False, False, f"Zu kleiner Anstieg ({pct:.1f}%)")

    body       = price - open_
    range_     = high - low
    body_ratio = body / range_ if range_ > 0 else 0.0
    if body < 0 or body_ratio < 0.40:
        return SpikeResult(ticker, 0, round(pct, 2), 0.0, 0.0, price,
                           volume, False, False,
                           f"Schwache/baerische Kerze (body={body_ratio:.2f})")

    vol_20 = daily["volume"].iloc[-21:-1].median()
    if vol_20 <= 0:
        return empty
    rvol = volume / vol_20

    if rvol < MIN_RVOL:
        return SpikeResult(ticker, 0, round(pct, 2), round(rvol, 2), 0.0,
                           price, volume, False, False,
                           f"RVOL zu niedrig ({rvol:.1f}x < {MIN_RVOL}x)")

    high_52w = daily["high"].iloc[-252:].max() if len(daily) >= 252 else daily["high"].max()
    pth = price / high_52w if high_52w > 0 else 0.0

    if pth < MIN_PTH:
        return SpikeResult(ticker, 0, round(pct, 2), round(rvol, 2),
                           round(pth, 3), price, volume, False, False,
                           f"Zu weit vom 52W-High (PTH={pth:.2f} < {MIN_PTH}) — kein Continuation-Signal laut Forschung")

    reversion_risk = pct > 10.0 or rvol > 5.0

    if pct >= 8.0 and rvol >= 3.0 and pth >= 0.85 and not reversion_risk:
        level = 2
    elif pct >= 4.0 and rvol >= 2.0 and pth >= 0.70:
        level = 1
    else:
        level = 0

    if reversion_risk:
        level = 3

    level_labels = {
        0: "Kein Signal",
        1: "Moderater Spike",
        2: "Starker Spike",
        3: "Extrembewegung — Reversion-Risiko",
    }

    note = (f"+{pct:.1f}% | RVOL={rvol:.1f}x | PTH={pth:.2f} | "
            f"Preis={price:.2f} | {level_labels[level]}")

    return SpikeResult(
        ticker=ticker,
        level=level,
        pct_change=round(pct, 2),
        rvol=round(rvol, 2),
        pth=round(pth, 3),
        price=price,
        volume=volume,
        signal=level in (1, 2),
        reversion_risk=reversion_risk,
        note=note,
    )


def format_spike_alert(r: SpikeResult) -> str:
    icons  = {1: "Y", 2: "G", 3: "W"}
    labels = {1: "Moderater Spike", 2: "Starker Spike",
              3: "Extrembewegung (Reversion-Risiko)"}
    warn = "\nExtrem — Reversion-Risiko erhoeht" if r.reversion_risk else ""
    return (
        f"{icons.get(r.level,'X')} *{r.ticker}* — {labels.get(r.level,'Spike')}\n"
        f"+{r.pct_change:.1f}% | RVOL {r.rvol:.1f}x | PTH {r.pth:.2f}\n"
        f"Preis: {r.price:.2f} | Vol: {r.volume:,}"
        f"{warn}"
    )

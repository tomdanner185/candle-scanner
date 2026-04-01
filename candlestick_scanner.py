"""
candlestick_scanner.py  —  Panzer Bot Modul 3
Bidirektionaler Intraday-Candlestick-Scanner

Synergien mit bestehendem Setup:
  ✓ Nutzt config.TELEGRAM_TOKEN / config.TELEGRAM_CHAT_ID (kein neuer Key)
  ✓ Nutzt config.MIN_AVG_VOLUME als Volumen-Untergrenze
  ✓ Standalone regime.calc_regime() als Gate (SPY EMA50 + VIX)
  ✓ Nutzt yfinance wie Modell 1 (kein neues Package)
  ✓ Schreibt in signals.db (gleiche SQLite-DB wie Modell 1)
  ✓ Logging-Format identisch mit bestehendem Bot
  ✓ Kein Rebuild nötig — nur docker cp + git commit

Empirische Basis:
  · Zarattini et al. (2024) SSRN 4729284  — ORB + Volumen-Gate
  · Zarattini & Aziz (2023) SSRN 4631351  — VWAP als Filter
  · Xu & Zhu (2022) SSRN 4192163          — Zeitfenster 09:30-11:30 ET
  · Doss et al. (2008) West Georgia BQ    — Shooting Star p=0.0002
  · Shiu & Lu (2011) IJEF                 — Muster + Volumen-Kontext
  · Marshall et al. (2006) JBF            — Standalone-Muster falsifiziert

Aufruf (täglich 09:45 ET via main.py Scheduler):
  from candlestick_scanner import run_candle_scan
  scheduler.add_job(run_candle_scan, 'cron', hour=13, minute=45)
  # 13:45 UTC = 09:45 ET (Sommerzeit EDT = UTC-4)

Manueller Test:
  docker exec momentum_scanner python3 -c "
  from candlestick_scanner import run_candle_scan
  run_candle_scan()
  "
"""

import logging
import time
import sqlite3
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

import yfinance as yf
import pandas as pd
import numpy as np

import config

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  WATCHLIST  (100 liquide US-Aktien — kein Rate-Limit-Risiko)
#  Zusammensetzung: Top-50 S&P 500 nach Volumen +
#  aktive Momentum-Kandidaten aus Modell 1
# ═══════════════════════════════════════════════════════════════
CANDLE_UNIVERSE = [
    # Mega-Cap (hohes Volumen, zuverlässige Daten)
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD","PLTR",
    # Large-Cap Tech
    "CRM","ADBE","INTU","NOW","SNOW","CRWD","DDOG","MSTR","SMCI","ARM",
    # Financials
    "JPM","BAC","GS","MS","BLK","V","MA","PYPL","COIN","HOOD",
    # Health / Biotech (hohes Intraday-Vol)
    "UNH","LLY","ABBV","MRK","PFE","AMGN","GILD","REGN","BIIB","MRNA",
    # Energy / Materials
    "XOM","CVX","COP","OXY","SLB","FCX","NEM","GOLD","CLF","X",
    # Consumer
    "AMZN","WMT","TGT","COST","HD","LOW","NKE","LULU","SBUX","MCD",
    # Industrials
    "CAT","DE","BA","GE","HON","RTX","LMT","NOC","GD","HWM",
    # Semis (intraday Momentum)
    "QCOM","MU","AMAT","LRCX","KLAC","MCHP","MRVL","ON","SWKS","QRVO",
    # ETFs (Regime-Proxy + Liquid)
    "SPY","QQQ","IWM","XLK","XLF","XLE","XLV","SOXL","TQQQ","UVXY",
]


# ═══════════════════════════════════════════════════════════════
#  DATENSTRUKTUREN
# ═══════════════════════════════════════════════════════════════
@dataclass
class Bar:
    o: float; h: float; l: float; c: float; v: float

@dataclass
class CandleSignal:
    name: str
    direction: str      # "LONG" | "SHORT"
    strength: float     # 0.0–1.0
    score_pts: int      # Maximale Punkte (5–15)
    description: str
    ref: str

@dataclass
class CandleResult:
    ticker: str
    direction: str
    score: int
    verdict: str        # LONG SETUP / SHORT SETUP / BEOBACHTEN / KEIN TRADE
    pattern: CandleSignal = None
    price: float = 0.0
    vwap: float  = 0.0
    or_high: float = 0.0
    or_low: float  = 0.0
    vol_ratio: float = 0.0
    ema9: float  = 0.0
    ema20: float = 0.0
    reasons: list = field(default_factory=list)
    alert_text: str = ""


# ═══════════════════════════════════════════════════════════════
#  KERZENMUSTER-ERKENNUNG
#  Gewichtung empirisch (Evidenzstärke):
#  Shooting Star  15P  Doss (2008) p=0.0002
#  Hammer         15P  Lu et al. (2012)
#  Bull Engulf    12P  Caginalp & Laurent (1998)
#  Bear Engulf    10P  Shiu & Lu (2011)
#  Morning Star    8P  Lu et al. (2012)
#  Evening Star    5P  Marshall (2006) – schwächste Evidenz
# ═══════════════════════════════════════════════════════════════
class CandleRecognizer:

    def recognize(self, bars: list) -> list:
        """Gibt Liste erkannter CandleSignals zurück."""
        if len(bars) < 3:
            return []
        n = len(bars)
        signals = []

        # Einzel-Kerzen (letzte 3)
        for i in range(max(0, n-3), n):
            b = bars[i]
            s = self._shooting_star(b)
            if s: signals.append(s)
            h = self._hammer(b)
            if h: signals.append(h)

        # Zwei-Kerzen (letzte 3 Paare)
        for i in range(max(1, n-3), n):
            be = self._bearish_engulfing(bars[i-1], bars[i])
            if be: signals.append(be)
            bue = self._bullish_engulfing(bars[i-1], bars[i])
            if bue: signals.append(bue)

        # Drei-Kerzen
        if n >= 3:
            for i in range(max(2, n-3), n):
                es = self._evening_star(bars[i-2], bars[i-1], bars[i])
                if es: signals.append(es)
                ms = self._morning_star(bars[i-2], bars[i-1], bars[i])
                if ms: signals.append(ms)

        # Pro Richtung stärkstes Muster
        long_s  = [s for s in signals if s.direction == "LONG"]
        short_s = [s for s in signals if s.direction == "SHORT"]
        result  = []
        if long_s:
            result.append(max(long_s,  key=lambda x: x.strength))
        if short_s:
            result.append(max(short_s, key=lambda x: x.strength))
        return result

    def _shooting_star(self, b: Bar) -> Optional[CandleSignal]:
        """Doss et al. (2008): -0.35% Excess Return, p=0.0002."""
        body    = abs(b.c - b.o)
        hi_wick = b.h - max(b.c, b.o)
        lo_wick = min(b.c, b.o) - b.l
        rng     = b.h - b.l
        if rng < 1e-6 or body/rng < 0.08: return None
        if hi_wick < 2.0 * body: return None
        if lo_wick > 0.35 * body: return None
        if (min(b.c, b.o) - b.l) / rng > 0.4: return None
        st = min(1.0, hi_wick / (body * 3.0))
        return CandleSignal(
            "Shooting Star", "SHORT", st, 15,
            f"Oberer Schatten {hi_wick/body:.1f}× Body",
            "Doss et al. (2008) West Georgia BQ – p=0.0002"
        )

    def _hammer(self, b: Bar) -> Optional[CandleSignal]:
        """Lu et al. (2012): stärkstes Long-Muster mit Trend-Kontext."""
        body    = abs(b.c - b.o)
        lo_wick = min(b.c, b.o) - b.l
        hi_wick = b.h - max(b.c, b.o)
        rng     = b.h - b.l
        if rng < 1e-6 or body/rng < 0.08: return None
        if lo_wick < 2.0 * body: return None
        if hi_wick > 0.35 * body: return None
        if (min(b.c, b.o) - b.l) / rng < 0.45: return None
        st = min(1.0, lo_wick / (body * 3.0))
        return CandleSignal(
            "Hammer", "LONG", st, 15,
            f"Unterer Schatten {lo_wick/body:.1f}× Body",
            "Lu et al. (2012) Rev. Fin. Econ."
        )

    def _bearish_engulfing(self, prev: Bar, curr: Bar) -> Optional[CandleSignal]:
        """Shiu & Lu (2011): Signifikant mit Volumen-Kontext."""
        if prev.c <= prev.o or curr.c >= curr.o: return None
        if curr.o <= prev.c or curr.c >= prev.o: return None
        pb = prev.c - prev.o
        cb = curr.o - curr.c
        if cb < pb: return None
        return CandleSignal(
            "Bearish Engulfing", "SHORT",
            min(1.0, cb/(pb*1.5)), 10,
            f"Engulfed {cb/pb:.1f}× Vorgänger",
            "Shiu & Lu (2011) IJEF – Volumen-Kontext erforderlich"
        )

    def _bullish_engulfing(self, prev: Bar, curr: Bar) -> Optional[CandleSignal]:
        """Caginalp & Laurent (1998): ~1% Return über 2 Tage."""
        if prev.c >= prev.o or curr.c <= curr.o: return None
        if curr.o >= prev.c or curr.c <= prev.o: return None
        pb = prev.o - prev.c
        cb = curr.c - curr.o
        if cb < pb: return None
        return CandleSignal(
            "Bullish Engulfing", "LONG",
            min(1.0, cb/(pb*1.5)), 12,
            f"Engulfed {cb/pb:.1f}× Vorgänger",
            "Caginalp & Laurent (1998) – Volumen-Kontext"
        )

    def _evening_star(self, b1: Bar, b2: Bar, b3: Bar) -> Optional[CandleSignal]:
        """Marshall (2006): schwächste Evidenz – nur als Bonus."""
        if b1.c <= b1.o or b3.c >= b3.o: return None
        b1b = b1.c - b1.o
        b2b = abs(b2.c - b2.o)
        b3b = b3.o - b3.c
        if b2b > b1b * 0.5 or b2.o < b1.c: return None
        if b3.c > (b1.o + b1.c) / 2: return None
        return CandleSignal(
            "Evening Star", "SHORT",
            min(1.0, b3b/b1b) * 0.6, 5,  # Abzug wegen schwacher Evidenz
            "3-Kerzen Bull→Star→Bear | Schwache Evidenz",
            "Marshall et al. (2006) JBF – Standalone falsifiziert"
        )

    def _morning_star(self, b1: Bar, b2: Bar, b3: Bar) -> Optional[CandleSignal]:
        """Lu et al. (2012): moderater Edge mit Kontext."""
        if b1.c >= b1.o or b3.c <= b3.o: return None
        b1b = b1.o - b1.c
        b2b = abs(b2.c - b2.o)
        b3b = b3.c - b3.o
        if b2b > b1b * 0.5 or b2.o > b1.c: return None
        if b3.c < (b1.o + b1.c) / 2: return None
        return CandleSignal(
            "Morning Star", "LONG",
            min(1.0, b3b/b1b) * 0.7, 8,
            "3-Kerzen Bear→Star→Bull",
            "Lu et al. (2012) – moderater Edge mit Kontext"
        )


# ═══════════════════════════════════════════════════════════════
#  SCORE ENGINE  (0–100, bidirektional)
#
#  Check              Long              Short           Punkte
#  ─────────────────────────────────────────────────────────────
#  Zeitfenster        09:30-11:30 ET    gleich            20
#  Volumen-Gate       >2× Ø             gleich            20
#  ORB / OBD          > OR-Hoch         < OR-Tief         20
#  VWAP               Kurs > VWAP       Kurs < VWAP       15
#  EMA-Trend          EMA9 > EMA20      EMA9 < EMA20      10
#  Kerzenmuster       Hammer/Engulf     Shoot/Engulf/Star  15
#  ─────────────────────────────────────────────────────────────
#  Knockout:  Kein Muster → Score max 59 → KEIN TRADE (Marshall 2006)
#             <4/5 Core-Checks → Score max 39 → KEIN TRADE
# ═══════════════════════════════════════════════════════════════
class ScoreEngine:

    def score(self, ticker: str, df_1min: pd.DataFrame,
              df_daily: pd.DataFrame,
              patterns: list, et_hour: int, et_min: int) -> CandleResult:

        price = float(df_1min["Close"].iloc[-1])
        reasons = []

        # ── Zeitfenster ──────────────────────────────────────
        mins = et_hour * 60 + et_min
        if   9*60+30 <= mins < 10*60:    tz_sc, tz_r = 1.0, f"Prime 09:30-10:00 ✓"
        elif 10*60   <= mins < 11*60:    tz_sc, tz_r = 0.9, f"Gut 10:00-11:00 ✓"
        elif 11*60   <= mins < 11*60+30: tz_sc, tz_r = 0.7, f"11:00-11:30 (~)"
        elif 11*60+30<= mins < 13*60+30: tz_sc, tz_r = 0.2, f"Lunch-Slump"
        else:                            tz_sc, tz_r = 0.0, f"Reversal-Zone ab 14:00 ET"
        reasons.append(f"Zeitfenster {et_hour:02d}:{et_min:02d} ET → {tz_r}")

        # ── VWAP berechnen (aus 1-Min Bars) ──────────────────
        vwap = _calc_vwap(df_1min)

        # ── Opening Range (erste 15 Min Bars) ────────────────
        or_bars = df_1min.head(getattr(config, "CANDLE_OR_MINUTES", 15))
        or_high = float(or_bars["High"].max())  if len(or_bars) else price
        or_low  = float(or_bars["Low"].min())   if len(or_bars) else price
        or_vol  = float(or_bars["Volume"].sum()) if len(or_bars) else 0

        # ── 14-Tage Durchschnittsvolumen ─────────────────────
        avg14 = float(df_daily["Volume"].tail(14).mean()) if len(df_daily) >= 14 else 0
        or_frac = {5: 0.025, 10: 0.035, 15: 0.05}.get(
            getattr(config, "CANDLE_OR_MINUTES", 15), 0.05)
        expected_or_vol = avg14 * or_frac
        vol_ratio = or_vol / expected_or_vol if expected_or_vol > 0 else 0
        if   vol_ratio >= 3.0: vol_sc, vol_r = 1.0, f"Volumen {vol_ratio:.1f}× ✓✓"
        elif vol_ratio >= 2.0: vol_sc, vol_r = 1.0, f"Volumen {vol_ratio:.1f}× ✓"
        elif vol_ratio >= 1.5: vol_sc, vol_r = 0.6, f"Volumen {vol_ratio:.1f}× (~)"
        else:                  vol_sc, vol_r = 0.1, f"Volumen {vol_ratio:.1f}× zu niedrig"
        reasons.append(f"Vol-Gate → {vol_r}")

        # ── EMA aus 1-Min Bars ────────────────────────────────
        closes = df_1min["Close"].tolist()
        ema9  = _ema(closes, 9)
        ema20 = _ema(closes, 20)

        # ── Richtung aus Muster ───────────────────────────────
        long_p  = [p for p in patterns if p.direction == "LONG"]
        short_p = [p for p in patterns if p.direction == "SHORT"]
        best_long  = max(long_p,  key=lambda x: x.strength) if long_p  else None
        best_short = max(short_p, key=lambda x: x.strength) if short_p else None

        if best_long and best_short:
            direction = "LONG" if best_long.strength >= best_short.strength else "SHORT"
            pat = best_long if direction == "LONG" else best_short
        elif best_long:  direction, pat = "LONG",  best_long
        elif best_short: direction, pat = "SHORT", best_short
        else:            direction, pat = "NEUTRAL", None

        # ── ORB/OBD-Check ─────────────────────────────────────
        if direction == "SHORT":
            orb_diff = ((price - or_low) / or_low * 100) if or_low else 0
            if   orb_diff <= -0.5: orb_sc, orb_r = 1.0, f"Breakdown {orb_diff:+.2f}% ✓"
            elif orb_diff <= 0:    orb_sc, orb_r = 0.8, f"Am OR-Tief {orb_diff:+.2f}%"
            elif orb_diff <= 0.3:  orb_sc, orb_r = 0.4, f"Knapp über OR-Tief"
            else:                  orb_sc, orb_r = 0.0, f"Kein Breakdown {orb_diff:+.2f}%"
        else:
            orb_diff = ((price - or_high) / or_high * 100) if or_high else 0
            if   orb_diff >= 0.5:  orb_sc, orb_r = 1.0, f"Breakout {orb_diff:+.2f}% ✓"
            elif orb_diff >= 0:    orb_sc, orb_r = 0.8, f"Am OR-Hoch {orb_diff:+.2f}%"
            elif orb_diff >= -0.3: orb_sc, orb_r = 0.4, f"Knapp unter OR-Hoch"
            else:                  orb_sc, orb_r = 0.0, f"Kein Breakout {orb_diff:+.2f}%"
        reasons.append(f"ORB/OBD → {orb_r}")

        # ── VWAP-Check ────────────────────────────────────────
        if vwap > 0:
            vwap_dist = ((price - vwap) / vwap * 100)
            if direction == "SHORT":
                if   vwap_dist <= -0.5: vwap_sc, vwap_r = 1.0, f"Unter VWAP {vwap_dist:+.2f}% ✓"
                elif vwap_dist <= 0:    vwap_sc, vwap_r = 0.8, f"Unter VWAP {vwap_dist:+.2f}%"
                elif vwap_dist <= 0.3:  vwap_sc, vwap_r = 0.3, f"Knapp über VWAP"
                else:                   vwap_sc, vwap_r = 0.0, f"Über VWAP – kein Short"
            else:
                if   vwap_dist >= 0.5:  vwap_sc, vwap_r = 1.0, f"Über VWAP {vwap_dist:+.2f}% ✓"
                elif vwap_dist >= 0:    vwap_sc, vwap_r = 0.8, f"Über VWAP {vwap_dist:+.2f}%"
                elif vwap_dist >= -0.3: vwap_sc, vwap_r = 0.3, f"Knapp unter VWAP"
                else:                   vwap_sc, vwap_r = 0.0, f"Unter VWAP – kein Long"
        else:
            vwap_sc, vwap_r, vwap = 0.5, "VWAP n/a", 0.0
        reasons.append(f"VWAP → {vwap_r}")

        # ── EMA-Check ─────────────────────────────────────────
        if ema9 and ema20:
            ema_diff = (ema9 - ema20) / ema20 * 100
            if direction == "SHORT":
                if   ema_diff <= -0.3: ema_sc, ema_r = 1.0, f"EMA9 {ema_diff:+.2f}% ✓"
                elif ema_diff <= 0:    ema_sc, ema_r = 0.8, f"EMA bearish ✓"
                else:                  ema_sc, ema_r = 0.2, f"EMA bullish – gegen Short"
            else:
                if   ema_diff >= 0.3:  ema_sc, ema_r = 1.0, f"EMA9 {ema_diff:+.2f}% ✓"
                elif ema_diff >= 0:    ema_sc, ema_r = 0.8, f"EMA bullish ✓"
                else:                  ema_sc, ema_r = 0.2, f"EMA bearish – gegen Long"
        else:
            ema_sc, ema_r = 0.5, "EMA n/a"
        reasons.append(f"EMA → {ema_r}")

        # ── Muster-Score ──────────────────────────────────────
        if pat:
            mut_sc = (pat.score_pts / 15.0) * pat.strength
            mut_r  = f"{pat.name} (Stärke {pat.strength:.2f})"
        else:
            mut_sc, mut_r = 0.0, "Kein Muster"
        reasons.append(f"Muster → {mut_r}")

        # ── Gesamt-Score ──────────────────────────────────────
        raw = (tz_sc*20 + vol_sc*20 + orb_sc*20 +
               vwap_sc*15 + ema_sc*10 + mut_sc*15)

        core_passed = sum([
            tz_sc >= 0.7, vol_sc >= 1.0, orb_sc >= 0.8,
            vwap_sc >= 0.7, ema_sc >= 0.7
        ])

        # Knockout-Regeln (Marshall 2006 + Mindest-Kontext)
        if not pat:
            raw = min(raw, 59.0)   # kein Muster → max BEOBACHTEN
        if core_passed < 4:
            raw = min(raw, 39.0)   # zu wenig Kontext → KEIN TRADE

        total = int(round(min(100, max(0, raw))))

        # Verdict
        if direction == "NEUTRAL" or total < 40:
            verdict = "KEIN TRADE"
        elif total >= 80 and core_passed >= 5 and pat:
            verdict = f"{direction} SETUP"
        elif total >= 60 and core_passed >= 4:
            verdict = f"BEOBACHTEN {direction}"
        else:
            verdict = "KEIN TRADE"

        result = CandleResult(
            ticker=ticker, direction=direction, score=total,
            verdict=verdict, pattern=pat,
            price=price, vwap=vwap,
            or_high=or_high, or_low=or_low,
            vol_ratio=vol_ratio, ema9=ema9, ema20=ema20,
            reasons=reasons,
        )
        result.alert_text = _build_alert(result)
        return result


# ═══════════════════════════════════════════════════════════════
#  DATEN-LADEN  (yfinance — wie Modell 1)
# ═══════════════════════════════════════════════════════════════
def _fetch_ticker(ticker: str) -> tuple:
    """
    Gibt (df_1min, df_daily) zurück.
    Nutzt yfinance wie Modell 1 — gleicher Code-Stil.
    """
    try:
        t = yf.Ticker(ticker)
        df_1min  = t.history(period="1d",  interval="5m",  prepost=False)
        df_daily = t.history(period="60d", interval="1d",  prepost=False)

        if df_1min.empty or len(df_1min) < 5:
            return None, None

        # Columns normalisieren (yfinance gibt manchmal MultiIndex)
        df_1min.columns = [c[0] if isinstance(c, tuple) else c
                           for c in df_1min.columns]
        df_daily.columns = [c[0] if isinstance(c, tuple) else c
                            for c in df_daily.columns]
        return df_1min, df_daily
    except Exception as e:
        log.debug(f"  yfinance Fehler {ticker}: {e}")
        return None, None


def _df_to_bars(df: pd.DataFrame, n: int = 10) -> list:
    """Letzten n 1-Min-Bars in Bar-Objekte umwandeln."""
    bars = []
    for _, row in df.tail(n).iterrows():
        try:
            bars.append(Bar(
                float(row["Open"]), float(row["High"]),
                float(row["Low"]),  float(row["Close"]),
                float(row["Volume"])
            ))
        except Exception:
            continue
    return bars


def _et_now() -> tuple:
    """Gibt (hour, minute) in ET zurück."""
    et = datetime.now(timezone(timedelta(hours=-4)))  # EDT
    return et.hour, et.minute


def _ema(closes: list, period: int) -> float:
    if len(closes) < period: return 0.0
    k   = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for c in closes[period:]:
        val = c * k + val * (1 - k)
    return round(val, 4)


def _calc_vwap(df: pd.DataFrame) -> float:
    """VWAP aus intraday Bars (typischer Preis × Volumen)."""
    try:
        tp  = (df["High"] + df["Low"] + df["Close"]) / 3
        vol = df["Volume"]
        total_vol = float(vol.sum())
        if total_vol < 1: return 0.0
        return float((tp * vol).sum() / total_vol)
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════
#  ALERT-TEXT  (kompatibel mit bestehendem Panzer-Stil)
# ═══════════════════════════════════════════════════════════════
def _build_alert(r: CandleResult) -> str:
    emoji = {"LONG SETUP": "🟢", "SHORT SETUP": "🔴",
             "BEOBACHTEN LONG": "🟡", "BEOBACHTEN SHORT": "🟠",
             "KEIN TRADE": "⚫"}.get(r.verdict, "⚪")
    dir_sym = "▲ LONG" if r.direction == "LONG" else (
              "▼ SHORT" if r.direction == "SHORT" else "—")
    bar = "█" * round(r.score/10) + "░" * (10 - round(r.score/10))
    pat_line = (f"\n🕯 Muster: {r.pattern.name} "
                f"(Stärke {r.pattern.strength:.2f})\n"
                f"   {r.pattern.ref}"
                if r.pattern else "\n🕯 Muster: —")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{emoji} {r.ticker}  |  {dir_sym}  |  {r.score}/100",
        f"📊 {bar}  →  {r.verdict}",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💵 Kurs:   ${r.price:.2f}",
        f"📈 VWAP:   ${r.vwap:.2f}",
        f"📦 Vol:    {r.vol_ratio:.2f}×",
        f"🔺 OR-H:   ${r.or_high:.2f}",
        f"🔻 OR-L:   ${r.or_low:.2f}",
        pat_line,
        "",
        "📋 Checks:",
    ]
    for reason in r.reasons:
        lines.append(f"  · {reason}")
    lines += [
        "",
        "⏰ Max-Exit: 13:30 ET",
        "🔬 Zarattini(2024)·Xu&Zhu(2022)·Doss(2008)",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  SQLITE LOGGING  (nutzt bestehende signals.db)
# ═══════════════════════════════════════════════════════════════
def _init_candle_db():
    """Erstellt candle_signals-Tabelle falls nicht vorhanden."""
    db_path = getattr(config, "DB_PATH", "signals.db")
    try:
        con = sqlite3.connect(db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS candle_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT,
                ticker      TEXT,
                direction   TEXT,
                score       INTEGER,
                verdict     TEXT,
                pattern     TEXT,
                strength    REAL,
                price       REAL,
                vwap        REAL,
                or_high     REAL,
                or_low      REAL,
                vol_ratio   REAL,
                ema9        REAL,
                ema20       REAL,
                outcome_pct REAL DEFAULT NULL
            )
        """)
        con.commit()
        con.close()
    except Exception as e:
        log.warning(f"DB init Fehler: {e}")


def _log_result(r: CandleResult):
    """Schreibt Scan-Ergebnis in signals.db."""
    db_path = getattr(config, "DB_PATH", "signals.db")
    try:
        con = sqlite3.connect(db_path)
        con.execute("""
            INSERT INTO candle_signals
            (ts, ticker, direction, score, verdict, pattern, strength,
             price, vwap, or_high, or_low, vol_ratio, ema9, ema20)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now().isoformat(),
            r.ticker, r.direction, r.score, r.verdict,
            r.pattern.name if r.pattern else None,
            r.pattern.strength if r.pattern else None,
            r.price, r.vwap, r.or_high, r.or_low,
            r.vol_ratio, r.ema9, r.ema20,
        ))
        con.commit()
        con.close()
    except Exception as e:
        log.warning(f"DB write Fehler {r.ticker}: {e}")


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM  (nutzt bestehende config.TELEGRAM_TOKEN)
# ═══════════════════════════════════════════════════════════════
def _send_telegram(text: str):
    """Nutzt bestehenden Telegram-Setup aus config.py."""
    import urllib.request
    token   = getattr(config, "TELEGRAM_TOKEN", "") or \
              getattr(config, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("Telegram: Token/ChatID fehlt in config.py")
        return
    import json
    data = json.dumps({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            res = json.loads(resp.read())
            if not res.get("ok"):
                log.warning(f"Telegram Fehler: {res.get('description')}")
    except Exception as e:
        log.warning(f"Telegram Request Fehler: {e}")


def _send_batch_summary(results: list, min_score: int):
    """Batch-Zusammenfassung mehrerer Setups."""
    if not results:
        return
    lines = ["📊 <b>CANDLE SCAN</b>  " + datetime.now().strftime("%H:%M ET"), ""]
    for i, r in enumerate(results[:8], 1):
        em = "🟢" if "LONG SETUP" in r.verdict else (
             "🔴" if "SHORT SETUP" in r.verdict else "🟡")
        pat = r.pattern.name if r.pattern else "—"
        lines.append(
            f"{i}. {em} <b>{r.ticker}</b>  {r.score}/100  "
            f"{r.direction}  {pat}")
    if len(results) > 8:
        lines.append(f"\n...+{len(results)-8} weitere")
    _send_telegram("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
#  HAUPTFUNKTION  —  wird von main.py Scheduler aufgerufen
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  GAPPER-UNIVERSE  —  Top-Mover Vortag als dynamische Watchlist
# ═══════════════════════════════════════════════════════════════
def get_gapper_universe(min_gap_pct: float = 5.0,
                        max_tickers: int = 30) -> list:
    import yfinance as yf
    log.info(f'Lade Gapper-Universe (Gap >={min_gap_pct}%)...')
    gappers = []
    try:
        screener = yf.screen('most_actives', size=100)
        quotes   = screener.get('quotes', [])
        for q in quotes:
            try:
                chg   = q.get('regularMarketChangePercent', 0)
                price = q.get('regularMarketPrice', 0)
                vol   = q.get('regularMarketVolume', 0)
                sym   = q.get('symbol', '')
                if (abs(chg) >= min_gap_pct
                        and price >= 2.0
                        and vol >= 500_000
                        and '.' not in sym):
                    gappers.append(sym)
                    if len(gappers) >= max_tickers:
                        break
            except Exception:
                continue
        log.info(f'Gapper gefunden: {len(gappers)} Ticker')
    except Exception as e:
        log.warning(f'yfinance Screener Fehler: {e} — nutze nur statisches Universe')
    base     = list(CANDLE_UNIVERSE) if CANDLE_UNIVERSE else []
    combined = gappers + [t for t in base if t not in gappers]
    log.info(f'Universe gesamt: {len(combined)} Ticker ({len(gappers)} Gapper + {len(base)} Basis)')
    return combined

def run_candle_scan():
    log.info('=' * 50)
    log.info('CANDLE SCAN — Modell 3 — Start')
    et_h, et_m = _et_now()
    mins = et_h * 60 + et_m
    if not (9*60 <= mins <= 13*60+30):
        log.info(f'CANDLE SCAN aborted — ausserhalb Handelszeit ({et_h:02d}:{et_m:02d} ET)')
        return
    try:
        from regime import calc_regime
        regime = calc_regime('US')
        if regime.get('panic'):
            log.warning('CANDLE SCAN aborted — US PANIC')
            return
        log.info(f'Regime Gate: {"BEAR" if regime.get("bear") else "BULL"} | Panic={regime.get("panic")} OK')
    except Exception as e:
        log.warning(f'Regime Gate Fehler: {e} — weiter')
    _init_candle_db()
    recognizer = CandleRecognizer()
    engine     = ScoreEngine()
    min_score  = getattr(config, 'CANDLE_MIN_SCORE', 70)
    min_vol    = getattr(config, 'MIN_AVG_VOLUME', 500_000)
    gap_pct    = getattr(config, 'CANDLE_GAP_MIN_PCT', 5.0)
    universe   = get_gapper_universe(
        min_gap_pct=gap_pct,
        max_tickers=getattr(config, 'CANDLE_GAP_MAX_TICKERS', 30)
    )
    log.info(f'Universe: {len(universe)} Ticker | Score>={min_score}')
    results = []
    errors  = 0
    for i, ticker in enumerate(universe, 1):
        try:
            df_1min, df_daily = _fetch_ticker(ticker)
            if df_1min is None:
                continue
            avg_vol = (float(df_daily['Volume'].tail(14).mean())
                       if df_daily is not None and len(df_daily) >= 14 else 0)
            if avg_vol < min_vol:
                continue
            bars     = _df_to_bars(df_1min, n=10)
            patterns = recognizer.recognize(bars)
            result   = engine.score(ticker, df_1min, df_daily, patterns, et_h, et_m)
            _log_result(result)
            if result.score >= min_score and result.verdict != 'KEIN TRADE':
                results.append(result)
                log.info(f'  OK {ticker:<8} {result.direction:<6} Score={result.score} | {result.verdict} | {result.pattern.name if result.pattern else "-"}')
            time.sleep(0.3)
        except Exception as e:
            log.debug(f'Fehler {ticker}: {e}')
            errors += 1
        if i % 25 == 0:
            log.info(f'  Fortschritt: {i}/{len(universe)} | Setups: {len(results)}')
    log.info(f'CANDLE SCAN fertig: {len(results)} Setups | Err={errors}')
    if not results:
        log.info(f'Keine Setups >= {min_score}')
        return
    strong = [r for r in results if r.score >= 80]
    for r in strong:
        _send_telegram(r.alert_text)
        log.info(f'  Alert: {r.ticker} {r.score}/100')
        time.sleep(1)
    if len(results) > len(strong):
        _send_batch_summary(results, min_score)
    log.info('=' * 50)


# ── v3.1 Patch: Catalyst + PreMarket-Vol ─────────────────────
def _get_catalyst(ticker: str) -> tuple:
    try:
        from premarket_scanner import _check_catalyst
        return _check_catalyst(ticker)
    except Exception:
        pass
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
        KW = {
            'earnings_beat': (['beat','EPS beat','topped','above estimates','raised guidance'], 25),
            'earnings_miss': (['miss','below estimates','cut guidance','lowered'],             -20),
            'fda':           (['FDA','approval','cleared','PDUFA','Phase 3'],                  20),
            'ma':            (['acquisition','merger','buyout','takeover'],                    18),
            'analyst':       (['upgrade','price target raised','outperform'],                  10),
            'negative':      (['downgrade','warning','disappoints'],                          -15),
        }
        best, btype, btitle = 0, 'none', ''
        for item in news[:5]:
            title = (item.get('content',{}).get('title','') or item.get('title','')).lower()
            for cat,(kws,sc) in KW.items():
                if any(k.lower() in title for k in kws):
                    if abs(sc) > abs(best):
                        best,btype,btitle = sc,cat,title[:100]
        return btype, best, btitle
    except Exception:
        return 'none', 0, ''

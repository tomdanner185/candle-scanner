"""
finnhub_feed.py — Real-Time Quotes + Catalyst-Erkennung via Finnhub
Panzer Bot / Candle Scanner — kostenloser Tier ausreichend

Liefert:
  get_quote(ticker)         → aktueller Preis, Bid, Ask (Real-Time via IEX)
  get_catalyst(ticker)      → Earnings-Surprise, News-Sentiment, Upgrades
  get_earnings_today()      → alle Earnings-Announcements heute
  is_earnings_day(ticker)   → True wenn heute Earnings-Announcement

Evidenz:
  Bernard & Thomas (1989): Earnings-Surprise = stärkster Intraday-Prädiktor.
  Novy-Marx (2015): Earnings-Momentum subsumiert Price-Momentum.
"""

import os
import time
import logging
from datetime import datetime, timezone, timedelta
from functools import lru_cache

log = logging.getLogger(__name__)
ET  = timezone(timedelta(hours=-4))

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
_client     = None
_CACHE: dict = {}
_CACHE_TTL   = 900   # 15 Min Cache für Quotes
_NEWS_TTL    = 3600  # 1h Cache für News/Earnings


def _get_client():
    global _client
    if _client is None and FINNHUB_KEY:
        import finnhub
        _client = finnhub.Client(api_key=FINNHUB_KEY)
    return _client


def has_finnhub() -> bool:
    return bool(FINNHUB_KEY)


def get_quote(ticker: str) -> dict:
    """
    Real-Time Quote via Finnhub (IEX-Feed).
    Fallback auf yfinance wenn kein Key.
    Gibt dict: price, open, high, low, prev_close, change_pct, source
    """
    now = time.time()
    cache_key = f"quote_{ticker}"
    if cache_key in _CACHE and now - _CACHE[cache_key]["ts"] < _CACHE_TTL:
        return _CACHE[cache_key]["data"]

    if not has_finnhub():
        return _yf_quote(ticker)

    try:
        c = _get_client()
        q = c.quote(ticker)
        result = {
            "price":      q["c"],    # current price
            "open":       q["o"],
            "high":       q["h"],
            "low":        q["l"],
            "prev_close": q["pc"],
            "change_pct": round((q["c"] - q["pc"]) / q["pc"] * 100, 2) if q["pc"] else 0,
            "source":     "finnhub",
        }
        _CACHE[cache_key] = {"ts": now, "data": result}
        log.debug(f"Finnhub quote {ticker}: {result['price']} ({result['change_pct']:+.1f}%)")
        return result
    except Exception as e:
        log.debug(f"Finnhub quote Fehler {ticker}: {e}")
        return _yf_quote(ticker)


def get_catalyst(ticker: str) -> tuple:
    """
    Erweiterte Catalyst-Erkennung via Finnhub.
    Gibt (catalyst_type, score, headline) zurück.

    Prüft in Reihenfolge:
    1. Earnings heute? → stärkster Signal
    2. News-Sentiment letzte 24h
    3. Analyst-Upgrades/Downgrades letzte 7 Tage
    """
    now = time.time()
    cache_key = f"catalyst_{ticker}"
    if cache_key in _CACHE and now - _CACHE[cache_key]["ts"] < _NEWS_TTL:
        d = _CACHE[cache_key]["data"]
        return d["type"], d["score"], d["headline"]

    if not has_finnhub():
        # Fallback: yfinance.news (bisherige Logik)
        try:
            from candlestick_scanner import _get_catalyst as _yf_catalyst
            return _yf_catalyst(ticker)
        except Exception:
            return "none", 0, ""

    try:
        c    = _get_client()
        et   = datetime.now(ET)
        today = et.strftime("%Y-%m-%d")

        best_type, best_score, best_headline = "none", 0, ""

        # 1. Earnings heute?
        try:
            cal = c.earnings_calendar(
                _from=today, to=today, symbol=ticker, international=False
            )
            if cal and cal.get("earningsCalendar"):
                entry = cal["earningsCalendar"][0]
                eps_actual   = entry.get("epsActual")
                eps_estimate = entry.get("epsEstimate")
                if eps_actual is not None and eps_estimate is not None and eps_estimate != 0:
                    surprise_pct = (eps_actual - eps_estimate) / abs(eps_estimate) * 100
                    if surprise_pct > 5:
                        best_type    = "earnings_beat"
                        best_score   = min(30, int(surprise_pct / 2))
                        best_headline = f"EPS {eps_actual:.2f} vs {eps_estimate:.2f} est. ({surprise_pct:+.1f}%)"
                    elif surprise_pct < -5:
                        best_type    = "earnings_miss"
                        best_score   = max(-20, int(surprise_pct / 2))
                        best_headline = f"EPS Miss {eps_actual:.2f} vs {eps_estimate:.2f} est. ({surprise_pct:+.1f}%)"
                    else:
                        best_type    = "earnings_inline"
                        best_score   = 5
                        best_headline = f"Earnings inline: EPS {eps_actual:.2f}"
        except Exception:
            pass

        # 2. News-Sentiment letzte 24h (nur wenn kein Earnings-Signal)
        if best_score == 0:
            try:
                from_dt = (et - timedelta(hours=24)).strftime("%Y-%m-%d")
                news    = c.company_news(ticker, _from=from_dt, to=today)
                if news:
                    sentiments = []
                    headlines  = []
                    for item in news[:10]:
                        sent = item.get("sentiment", {})
                        if isinstance(sent, dict):
                            score = sent.get("bullishPercent", 0) - sent.get("bearishPercent", 0)
                            sentiments.append(score)
                            headlines.append(item.get("headline", ""))
                    if sentiments:
                        avg_sent = sum(sentiments) / len(sentiments)
                        if avg_sent > 0.3:
                            best_type    = "positive_news"
                            best_score   = int(avg_sent * 20)
                            best_headline = headlines[0][:120] if headlines else ""
                        elif avg_sent < -0.3:
                            best_type    = "negative_news"
                            best_score   = int(avg_sent * 15)
                            best_headline = headlines[0][:120] if headlines else ""
            except Exception:
                pass

        # 3. Analyst-Upgrades letzte 7 Tage
        if best_score == 0:
            try:
                upgrades = c.recommendation_trends(ticker)
                if upgrades:
                    latest = upgrades[0]
                    strong_buy = latest.get("strongBuy", 0)
                    buy        = latest.get("buy", 0)
                    sell       = latest.get("sell", 0) + latest.get("strongSell", 0)
                    total      = strong_buy + buy + sell + latest.get("hold", 0)
                    if total > 0:
                        bull_pct = (strong_buy + buy) / total
                        if bull_pct > 0.7:
                            best_type  = "analyst"
                            best_score = 10
                            best_headline = f"{int(bull_pct*100)}% Analysten bullisch ({strong_buy+buy}/{total})"
            except Exception:
                pass

        _CACHE[cache_key] = {
            "ts":   now,
            "data": {"type": best_type, "score": best_score, "headline": best_headline}
        }
        log.debug(f"Finnhub catalyst {ticker}: {best_type} {best_score:+d}P")
        return best_type, best_score, best_headline

    except Exception as e:
        log.debug(f"Finnhub catalyst Fehler {ticker}: {e}")
        return "none", 0, ""


def get_earnings_today() -> list:
    """
    Alle Earnings-Announcements heute (US-Markt).
    Gibt Liste von Ticker-Strings zurück.
    Wird vom Pre-Market-Scanner genutzt.
    """
    if not has_finnhub():
        return []
    try:
        c     = _get_client()
        et    = datetime.now(ET)
        today = et.strftime("%Y-%m-%d")
        cal   = c.earnings_calendar(_from=today, to=today, international=False)
        if not cal or not cal.get("earningsCalendar"):
            return []
        return [e["symbol"] for e in cal["earningsCalendar"] if e.get("symbol")]
    except Exception as e:
        log.debug(f"Earnings-Kalender Fehler: {e}")
        return []


def _yf_quote(ticker: str) -> dict:
    """yfinance Fallback für Quote."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period="2d", interval="1d")
        if len(df) >= 2:
            prev  = float(df["Close"].iloc[-2])
            price = float(df["Close"].iloc[-1])
            return {
                "price": price, "open": float(df["Open"].iloc[-1]),
                "high": float(df["High"].iloc[-1]), "low": float(df["Low"].iloc[-1]),
                "prev_close": prev,
                "change_pct": round((price - prev) / prev * 100, 2) if prev else 0,
                "source": "yfinance",
            }
    except Exception:
        pass
    return {"price": 0, "open": 0, "high": 0, "low": 0,
            "prev_close": 0, "change_pct": 0, "source": "yfinance"}


def data_source() -> str:
    return "finnhub.io (real-time)" if has_finnhub() else "yfinance (delayed)"

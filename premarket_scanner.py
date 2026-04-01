"""
premarket_scanner.py  —  Pre-Market Scanner 08:00 ET
"Kurz bevor eine Aktie explodiert"

Läuft täglich um 08:00 ET (14:00 CEST) — 90 Min vor Marktöffnung.
Identifiziert Kandidaten BEVOR der Move passiert.

Drei Signalquellen:
  1. Gap-Scan:     Vorbörsliche Kursveränderung vs. Vortag-Close
  2. Catalyst:     yfinance.Ticker.news — Earnings, FDA, M&A Keywords
  3. Volume-Surge: Pre-Market-Volumen vs. 14d-Durchschnitt

Empirische Basis:
  Bernard & Thomas (1989): PEAD — Earnings-Surprises treiben
      systematisch Post-Announcement-Momentum
  Caporale & Plastun (2021): Abnormale Returns setzen sich
      intraday fort wenn Morning-Catalyst vorhanden
  Zarattini et al. (2024): Vol-Gate ist notwendige Bedingung

Scheduler-Eintrag in main.py:
  scheduler.add_job(run_premarket_scan, 'cron', hour=12, minute=0)
  # 12:00 UTC = 08:00 ET (Sommerzeit EDT = UTC-4)
"""

import logging
import time
import sqlite3
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

import yfinance as yf

import config

log = logging.getLogger(__name__)

# ── Earnings/Catalyst Keywords ───────────────────────────────
CATALYST_KEYWORDS = {
    "earnings_beat":   ["beat", "earnings beat", "EPS beat", "revenue beat",
                        "raised guidance", "above estimates", "topped estimates"],
    "earnings_miss":   ["miss", "earnings miss", "below estimates",
                        "cut guidance", "lowered guidance"],
    "fda":             ["FDA", "approval", "cleared", "PDUFA", "NDA", "BLA",
                        "clinical trial", "Phase 3"],
    "ma":              ["acquisition", "merger", "buyout", "takeover",
                        "acquired by", "deal"],
    "analyst":         ["upgrade", "price target raised", "outperform",
                        "overweight", "strong buy"],
    "short_squeeze":   ["short squeeze", "short interest", "heavily shorted"],
    "negative":        ["downgrade", "miss", "cut", "lower", "below", "concern",
                        "warning", "disappoints"],
}

CATALYST_WEIGHTS = {
    "earnings_beat":  30,
    "fda":            25,
    "ma":             20,
    "analyst":        10,
    "short_squeeze":  15,
    "earnings_miss": -20,
    "negative":      -15,
}


@dataclass
class PreMarketSignal:
    ticker: str
    timestamp: str
    gap_pct: float          # % vs Vortag-Close
    pre_vol_ratio: float    # Pre-Market-Vol vs erwartetem Niveau
    catalyst_type: str      # z.B. "earnings_beat"
    catalyst_score: int     # Gewichteter Catalyst-Score
    headline: str           # Relevante Schlagzeile
    direction: str          # "LONG" | "SHORT" | "WATCH"
    total_score: int        # 0–100
    alert_text: str         = ""


def _fetch_premarket(ticker: str) -> Optional[dict]:
    """
    Holt Pre-Market-Daten via yfinance.
    Gibt Dict mit gap_pct, pre_vol, prev_close zurück.
    """
    try:
        t    = yf.Ticker(ticker)
        # 2 Tage 1-Min mit Pre-Market
        df   = t.history(period="2d", interval="1m", prepost=True)
        if df.empty or len(df) < 2:
            return None

        # Vortag-Close (letzter regulärer Handelstag)
        regular = df[df.index.hour >= 13]   # ab 09:30 ET = 13:30 UTC
        if len(regular) < 2:
            return None
        prev_close = float(regular["Close"].iloc[-1])

        # Aktueller Pre-Market-Kurs (letzte verfügbare Kerze)
        pre_price = float(df["Close"].iloc[-1])
        gap_pct   = (pre_price - prev_close) / prev_close * 100

        # Pre-Market-Volumen (nur heutige Pre-Market-Bars)
        today = datetime.now(timezone(timedelta(hours=-4))).date()
        today_pre = df[
            (df.index.date == today) &
            (df.index.hour < 13)      # vor 09:30 ET (UTC)
        ]
        pre_vol = float(today_pre["Volume"].sum()) if len(today_pre) else 0

        # 14d Daily Avg für Vergleich
        daily = t.history(period="30d", interval="1d", prepost=False)
        avg14 = float(daily["Volume"].tail(14).mean()) if len(daily) >= 14 else 0
        # Pre-Market typisch ~2% des Tagesvolumens
        pre_vol_ratio = (pre_vol / (avg14 * 0.02)) if avg14 > 0 else 0

        return {
            "price":         pre_price,
            "prev_close":    prev_close,
            "gap_pct":       gap_pct,
            "pre_vol":       pre_vol,
            "pre_vol_ratio": pre_vol_ratio,
        }
    except Exception as e:
        log.debug(f"Pre-Market fetch Fehler {ticker}: {e}")
        return None


def _check_catalyst(ticker: str) -> tuple:
    """
    Analysiert yfinance.Ticker.news auf Catalyst-Keywords.
    Gibt (catalyst_type, score, headline) zurück.
    """
    try:
        t     = yf.Ticker(ticker)
        news  = t.news or []
        if not news:
            return "none", 0, ""

        best_score    = 0
        best_type     = "none"
        best_headline = ""

        for item in news[:5]:   # Nur die 5 neuesten
            # yfinance news-Struktur (je nach Version unterschiedlich)
            title = (item.get("content", {}).get("title", "") or
                     item.get("title", "") or "")
            title_lower = title.lower()

            for cat, keywords in CATALYST_KEYWORDS.items():
                for kw in keywords:
                    if kw.lower() in title_lower:
                        score = CATALYST_WEIGHTS.get(cat, 0)
                        if abs(score) > abs(best_score):
                            best_score    = score
                            best_type     = cat
                            best_headline = title[:120]
                        break

        return best_type, best_score, best_headline
    except Exception as e:
        log.debug(f"Catalyst check Fehler {ticker}: {e}")
        return "none", 0, ""


def _score_premarket(gap_pct: float, pre_vol_ratio: float,
                     catalyst_score: int) -> tuple:
    """
    Berechnet Pre-Market-Score 0–100 und Richtung.

    Gewichtung:
      Gap          40P  (wichtigster Predictor)
      Volumen      30P  (Zarattini 2024: Vol-Gate notwendig)
      Catalyst     30P  (Bernard & Thomas 1989: PEAD)
    """
    # Gap-Score (Long: positiver Gap, Short: negativer Gap)
    abs_gap = abs(gap_pct)
    if   abs_gap >= 10: gap_sc = 1.0
    elif abs_gap >= 7:  gap_sc = 0.9
    elif abs_gap >= 5:  gap_sc = 0.8
    elif abs_gap >= 3:  gap_sc = 0.6
    elif abs_gap >= 2:  gap_sc = 0.4
    else:               gap_sc = 0.1

    # Volumen-Score
    if   pre_vol_ratio >= 5.0: vol_sc = 1.0
    elif pre_vol_ratio >= 3.0: vol_sc = 0.9
    elif pre_vol_ratio >= 2.0: vol_sc = 0.8
    elif pre_vol_ratio >= 1.0: vol_sc = 0.5
    else:                      vol_sc = 0.2

    # Catalyst-Score normiert
    cat_sc = max(-1.0, min(1.0, catalyst_score / 30.0))
    cat_sc = (cat_sc + 1.0) / 2.0   # 0–1

    raw = gap_sc * 40 + vol_sc * 30 + cat_sc * 30
    total = int(round(min(100, max(0, raw))))

    # Richtung
    if gap_pct >= 3 and catalyst_score >= 0:
        direction = "LONG"
    elif gap_pct <= -3 and catalyst_score <= 0:
        direction = "SHORT"
    elif gap_pct >= 2:
        direction = "WATCH"
    else:
        direction = "WATCH"

    return total, direction


def _build_alert(sig: PreMarketSignal) -> str:
    emoji = "🟢" if sig.direction == "LONG" else (
            "🔴" if sig.direction == "SHORT" else "🟡")
    bar = "█" * round(sig.total_score / 10) + "░" * (10 - round(sig.total_score / 10))

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🌅 PRE-MARKET ALERT  08:00 ET",
        f"{emoji} {sig.ticker}  |  {sig.direction}  |  {sig.total_score}/100",
        f"📊 {bar}",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📈 Gap:     {sig.gap_pct:+.2f}% vs Vortag",
        f"📦 Pre-Vol: {sig.pre_vol_ratio:.1f}× erwartet",
    ]
    if sig.catalyst_type != "none":
        lines.append(f"💥 Catalyst: {sig.catalyst_type} ({sig.catalyst_score:+d}P)")
    if sig.headline:
        lines.append(f"📰 {sig.headline[:100]}")
    lines += [
        f"",
        f"⚡ Beobachten bei Marktöffnung 09:30 ET:",
        f"   Bestätigung durch Candle Scanner 09:45 ET",
        f"   Volumen >2× + ORB-Breakout abwarten",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔬 Bernard&Thomas(1989)·Caporale&Plastun(2021)",
    ]
    return "\n".join(lines)


def _log_signal(sig: PreMarketSignal, db_path: str):
    try:
        con = sqlite3.connect(db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS premarket_signals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT,
                ticker        TEXT,
                gap_pct       REAL,
                pre_vol_ratio REAL,
                catalyst_type TEXT,
                catalyst_score INTEGER,
                headline      TEXT,
                direction     TEXT,
                total_score   INTEGER
            )
        """)
        con.execute("""
            INSERT INTO premarket_signals
            (ts, ticker, gap_pct, pre_vol_ratio, catalyst_type,
             catalyst_score, headline, direction, total_score)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            sig.timestamp, sig.ticker, sig.gap_pct, sig.pre_vol_ratio,
            sig.catalyst_type, sig.catalyst_score, sig.headline,
            sig.direction, sig.total_score
        ))
        con.commit()
        con.close()
    except Exception as e:
        log.warning(f"DB log Fehler {sig.ticker}: {e}")


def _send_telegram(text: str):
    import json, urllib.request
    token   = getattr(config, "TELEGRAM_TOKEN", "") or \
              getattr(config, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    data = json.dumps({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        log.warning(f"Telegram Fehler: {e}")


# ── Pre-Market Universe  ─────────────────────────────────────
# Fokus: Aktien die häufig Pre-Market-Catalyst haben
PREMARKET_UNIVERSE = [
    # High-Beta / Catalyst-anfällig
    "NVDA","AMD","TSLA","PLTR","SMCI","MSTR","COIN","HOOD","RIVN","LCID",
    "SOFI","UPST","AFRM","OPEN","RBLX","SNAP","PINS","LYFT","UBER","DASH",
    # Biotech / FDA-Kandidaten (häufige Pre-Market-Moves)
    "MRNA","BNTX","NVAX","SGEN","BMRN","ALNY","IONS","REGN","BIIB","GILD",
    "VRTX","HZNP","RARE","CRSP","BEAM","EDIT","NTLA","PCVX","RCUS","AGEN",
    # Earnings-sensitiv
    "NFLX","META","AMZN","GOOGL","MSFT","AAPL","CRM","NOW","SNOW","DDOG",
    "ZS","CRWD","PANW","OKTA","MDB","ESTC","GTLB","DKNG","MGAM","PENN",
    # Small/Mid Cap Mover
    "BBIO","ACMR","AXSM","KRTX","ARQT","ARDX","PRTA","IMVT","CALT","AGEN",
]


def run_premarket_scan():
    """
    Täglich 08:00 ET. Scannt Pre-Market-Daten und sendet
    Alerts für Kandidaten mit Gap ≥3% + Catalyst.
    """
    log.info("=" * 50)
    log.info("PRE-MARKET SCAN — 08:00 ET — Start")

    et = datetime.now(timezone(timedelta(hours=-4)))
    # Nur zwischen 07:00–09:30 ET laufen
    mins = et.hour * 60 + et.minute
    if not (7 * 60 <= mins <= 9 * 60 + 30):
        log.info(f"Pre-Market Scan: außerhalb Zeitfenster ({et.hour:02d}:{et.minute:02d} ET)")
        return

    db_path   = getattr(config, "DB_PATH", "signals.db")
    min_score = getattr(config, "PREMARKET_MIN_SCORE", 60)
    min_gap   = getattr(config, "PREMARKET_MIN_GAP_PCT", 3.0)

    universe = PREMARKET_UNIVERSE
    results  = []
    errors   = 0

    log.info(f"Universe: {len(universe)} Ticker | Gap>={min_gap}% | Score>={min_score}")

    for i, ticker in enumerate(universe, 1):
        try:
            pm = _fetch_premarket(ticker)
            if not pm:
                continue

            gap = pm["gap_pct"]
            if abs(gap) < min_gap:
                continue   # Kein nennenswerter Gap — überspringen

            cat_type, cat_score, headline = _check_catalyst(ticker)
            total, direction = _score_premarket(
                gap, pm["pre_vol_ratio"], cat_score)

            if total < min_score:
                continue

            sig = PreMarketSignal(
                ticker        = ticker,
                timestamp     = et.strftime("%Y-%m-%d %H:%M ET"),
                gap_pct       = gap,
                pre_vol_ratio = pm["pre_vol_ratio"],
                catalyst_type = cat_type,
                catalyst_score= cat_score,
                headline      = headline,
                direction     = direction,
                total_score   = total,
            )
            sig.alert_text = _build_alert(sig)
            _log_signal(sig, db_path)
            results.append(sig)

            log.info(f"  PRE-MKT {ticker:<8} {direction:<6} "
                     f"Gap={gap:+.1f}% Vol={pm['pre_vol_ratio']:.1f}× "
                     f"Cat={cat_type} Score={total}")

            time.sleep(0.4)   # yfinance Rate-Limit

        except Exception as e:
            log.debug(f"Fehler {ticker}: {e}")
            errors += 1

        if i % 20 == 0:
            log.info(f"  Fortschritt: {i}/{len(universe)} | Kandidaten: {len(results)}")

    log.info(f"PRE-MARKET SCAN fertig: {len(results)} Kandidaten | Err={errors}")

    if not results:
        log.info("Keine Pre-Market-Kandidaten heute")
        return

    # Sortiert nach Score
    results.sort(key=lambda x: x.total_score, reverse=True)

    # Einzelne Alerts für Score ≥80
    for sig in results:
        if sig.total_score >= 80:
            _send_telegram(sig.alert_text)
            log.info(f"  Pre-Market Alert: {sig.ticker} {sig.total_score}/100")
            time.sleep(1)

    # Batch-Summary
    lines = [f"🌅 <b>PRE-MARKET SCAN</b>  {et.strftime('%H:%M ET')}", ""]
    for i, sig in enumerate(results[:8], 1):
        em = "🟢" if sig.direction == "LONG" else (
             "🔴" if sig.direction == "SHORT" else "🟡")
        cat = sig.catalyst_type if sig.catalyst_type != "none" else "—"
        lines.append(
            f"{i}. {em} <b>{sig.ticker}</b>  {sig.gap_pct:+.1f}%  "
            f"{sig.pre_vol_ratio:.1f}×  {cat}  → {sig.total_score}/100")
    if len(results) > 8:
        lines.append(f"\n...+{len(results)-8} weitere")
    _send_telegram("\n".join(lines))

    log.info("=" * 50)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    run_premarket_scan()

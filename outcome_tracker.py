"""
outcome_tracker.py  —  P40b Outcome-Tracker v2
Panzer Bot / Candle Scanner

Läuft täglich um 16:15 ET (nach US-Market-Close).
1. Holt Intraday-Verlauf für jedes heutige Signal
2. Prüft These gegen tatsächliche Entwicklung
3. Sendet Tagesanalyse per Telegram
4. Nach n>=10 Signalen: Batch-Report

Fixes gegenüber v1:
- DB_PATH via config (absoluter Pfad /app/data/signals.db)
- open_positions try/except explizit geloggt
- print_report() sendet auch via Telegram
"""

import sqlite3
import logging
import time
from datetime import datetime, timezone, timedelta

import yfinance as yf

log = logging.getLogger(__name__)

ET = timezone(timedelta(hours=-4))


def _db_path() -> str:
    try:
        import config
        return getattr(config, "DB_PATH", "/app/data/signals.db")
    except Exception:
        return "/app/data/signals.db"


def _send_telegram(text: str):
    import json, urllib.request
    try:
        import config
        token   = getattr(config, "TELEGRAM_TOKEN", "") or getattr(config, "TELEGRAM_BOT_TOKEN", "")
        chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")
    except Exception:
        return
    if not token or not chat_id:
        return
    data = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.warning(f"Telegram Fehler: {e}")


def _get_intraday(ticker: str) -> dict:
    """
    Holt heutigen Intraday-Verlauf.
    Gibt dict mit high, low, close, open, vwap_held (bool),
    sl_hit, tp1_hit, max_drawdown zurück.
    """
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="5m", prepost=False)
        if df.empty:
            return {}
        high  = float(df["High"].max())
        low   = float(df["Low"].min())
        close = float(df["Close"].iloc[-1])
        open_ = float(df["Open"].iloc[0])
        # VWAP berechnen
        tp    = (df["High"] + df["Low"] + df["Close"]) / 3
        vwap  = float((tp * df["Volume"]).sum() / df["Volume"].sum())
        # Wie viele Bars schlossen über VWAP
        closes_above = (df["Close"] > vwap).sum()
        vwap_held    = closes_above > len(df) * 0.6
        # Volumen-Peak (erste 2h = erste 24 Bars bei 5-Min)
        vol_first2h  = float(df["Volume"].iloc[:24].mean())
        vol_rest     = float(df["Volume"].iloc[24:].mean()) if len(df) > 24 else 0
        vol_sustained = vol_first2h > 0 and (vol_rest / vol_first2h) > 0.5
        return {
            "high": high, "low": low, "close": close, "open": open_,
            "vwap": vwap, "vwap_held": vwap_held,
            "vol_sustained": vol_sustained,
            "bars": len(df),
        }
    except Exception as e:
        log.debug(f"Intraday-Fehler {ticker}: {e}")
        return {}


def _analyse_signal(row: tuple, intraday: dict, fx: float) -> str:
    """
    Prüft These gegen tatsächliche Tagesentwicklung.
    Gibt fertigen Telegram-Text zurück.
    """
    row_id, ticker, direction, entry_price, ts, score, catalyst, pattern = row
    if not intraday or not entry_price:
        return ""

    is_long  = direction == "LONG"
    high     = intraday.get("high", entry_price)
    low      = intraday.get("low", entry_price)
    close    = intraday.get("close", entry_price)
    vwap     = intraday.get("vwap", entry_price)
    vwap_held    = intraday.get("vwap_held", False)
    vol_sustained = intraday.get("vol_sustained", False)

    # SL und TP berechnen (gleiche Logik wie _build_alert)
    # Approximation da OR-Werte nicht in DB gespeichert
    sl  = entry_price * (0.97 if is_long else 1.03)
    tp1 = entry_price * (1.03 if is_long else 0.97)
    tp2 = entry_price * (1.06 if is_long else 0.94)

    # Trade-Ergebnis
    if is_long:
        outcome_pct = (close - entry_price) / entry_price * 100
        max_gain    = (high  - entry_price) / entry_price * 100
        max_loss    = (low   - entry_price) / entry_price * 100
        sl_hit      = low  <= sl
        tp1_hit     = high >= tp1
        tp2_hit     = high >= tp2
    else:
        outcome_pct = (entry_price - close) / entry_price * 100
        max_gain    = (entry_price - low)  / entry_price * 100
        max_loss    = (entry_price - high) / entry_price * 100
        sl_hit      = high >= sl
        tp1_hit     = low  <= tp1
        tp2_hit     = low  <= tp2

    # These-Prüfung
    these_checks = []
    these_ok = 0

    # Check 1: Richtung korrekt?
    if outcome_pct > 0:
        these_checks.append("Richtung: korrekt")
        these_ok += 1
    else:
        these_checks.append("Richtung: falsch")

    # Check 2: VWAP gehalten?
    if is_long and vwap_held:
        these_checks.append("VWAP: als Support gehalten")
        these_ok += 1
    elif not is_long and not vwap_held:
        these_checks.append("VWAP: als Resistance gehalten")
        these_ok += 1
    else:
        these_checks.append("VWAP: nicht gehalten")

    # Check 3: Volumen nachhaltig?
    if vol_sustained:
        these_checks.append("Volumen: auch nachmittags aktiv")
        these_ok += 1
    else:
        these_checks.append("Volumen: nur morgens")

    # Catalyst-Zeile
    cat_labels = {
        "earnings_beat": "Earnings Beat",
        "fda":           "FDA Catalyst",
        "ma":            "M&A / Übernahme",
        "analyst":       "Analyst-Upgrade",
        "none":          "kein Catalyst",
        None:            "kein Catalyst",
    }
    cat_label = cat_labels.get(catalyst, catalyst or "kein Catalyst")

    # These aufgegangen?
    if these_ok >= 2 and outcome_pct > 0:
        fazit = "These aufgegangen"
        fazit_emoji = "✅"
    elif outcome_pct > 0:
        fazit = "Ergebnis positiv, These teilweise"
        fazit_emoji = "⚠️"
    else:
        fazit = "These nicht aufgegangen"
        fazit_emoji = "❌"

    # Lernpunkt
    if sl_hit and not tp1_hit:
        lernpunkt = "Stop wurde getriggert — Entry-Timing oder Setup-Qualität prüfen"
    elif tp2_hit:
        lernpunkt = "Ziel 2 erreicht — starkes Setup, volle Position wäre optimal gewesen"
    elif tp1_hit:
        lernpunkt = "Ziel 1 erreicht — Halbe Position bei Ziel 1 schliessen war korrekt"
    elif outcome_pct > 0:
        lernpunkt = "Positiv aber kein Ziel erreicht — Exit-Timing oder engeres Ziel prüfen"
    else:
        lernpunkt = "Verlust — Catalyst oder Volumen nicht stark genug"

    # EUR-Konversion
    def eur(usd): return f"€{usd/fx:.2f}"

    lines = [
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 TAGESANALYSE — {ticker} {'▲' if is_long else '▼'} {direction} ({score}/100)",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"Catalyst: {cat_label}",
        f"Muster:   {pattern or '—'}",
        f"",
        f"Einstieg:    {eur(entry_price)}",
        f"Tageshoch:   {eur(high)}  ({max_gain:+.1f}%)",
        f"Tagestief:   {eur(low)}   ({max_loss:+.1f}%)",
        f"Tagesschluss:{eur(close)}  ({outcome_pct:+.1f}%)",
        f"",
        f"Stop-Loss:   {eur(sl)}  → {'GETRIGGERT' if sl_hit else 'nicht getriggert'}",
        f"Ziel 1:      {eur(tp1)} → {'ERREICHT' if tp1_hit else 'nicht erreicht'}",
        f"Ziel 2:      {eur(tp2)} → {'ERREICHT' if tp2_hit else 'nicht erreicht'}",
        f"",
        f"These-Prüfung:",
    ]
    for check in these_checks:
        lines.append(f"  {'✅' if 'korrekt' in check or 'gehalten' in check or 'aktiv' in check else '❌'} {check}")

    lines += [
        f"",
        f"{fazit_emoji} {fazit}",
        f"",
        f"Lernpunkt:",
        f"{lernpunkt}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def run_outcome_update(db_path: str = None):
    """
    Täglich 16:15 ET:
    1. Outcome_pct für alle Signale der letzten 5 Tage befüllen
    2. Für heutige Signale: Tagesanalyse via Telegram senden
    """
    if db_path is None:
        db_path = _db_path()

    et = datetime.now(ET)
    log.info(f"OUTCOME UPDATE — {et.strftime('%Y-%m-%d %H:%M ET')}")

    # EUR/USD für Euro-Konversion
    fx = 1.08
    try:
        df_fx = yf.Ticker("EURUSD=X").history(period="1d", interval="1m")
        if not df_fx.empty:
            fx = float(df_fx["Close"].iloc[-1])
    except Exception:
        pass

    con = sqlite3.connect(db_path)

    # Schritt 1: Schlusskurse holen + outcome_pct befüllen
    rows = con.execute("""
        SELECT id, ticker, direction, price, ts
        FROM candle_signals
        WHERE outcome_pct IS NULL
          AND ts >= datetime('now', '-5 days')
        ORDER BY ts DESC
        LIMIT 200
    """).fetchall()

    tickers = list({r[1] for r in rows})
    prices  = {}
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(period="2d", interval="1d")
            if not df.empty:
                prices[ticker] = float(df["Close"].iloc[-1])
            time.sleep(0.3)
        except Exception as e:
            log.debug(f"Preis-Fehler {ticker}: {e}")

    updated = 0
    for row_id, ticker, direction, entry_price, ts in rows:
        if ticker not in prices or not entry_price:
            continue
        close = prices[ticker]
        outcome = ((close - entry_price) / entry_price * 100
                   if direction == "LONG"
                   else (entry_price - close) / entry_price * 100)
        con.execute("UPDATE candle_signals SET outcome_pct=? WHERE id=?",
                    (round(outcome, 3), row_id))
        updated += 1

    con.commit()
    log.info(f"Outcome Update: {updated} Signale aktualisiert")

    # Schritt 2: Tagesanalyse für heutige Signale (erste 10)
    today = et.strftime("%Y-%m-%d")
    today_rows = con.execute("""
        SELECT id, ticker, direction, price, ts, score, catalyst, pattern
        FROM candle_signals
        WHERE date(ts) = ?
          AND verdict != 'KEIN TRADE'
        ORDER BY score DESC
        LIMIT 10
    """, (today,)).fetchall()

    # open_positions aktualisieren
    try:
        pos_rows = con.execute("""
            SELECT id, ticker, direction, entry_price
            FROM open_positions WHERE status='OPEN'
        """).fetchall()
        for pos_id, ticker, direction, entry in pos_rows:
            if ticker in prices and entry:
                close = prices[ticker]
                pnl   = ((close - entry) / entry * 100
                         if direction == "LONG"
                         else (entry - close) / entry * 100)
                con.execute("UPDATE open_positions SET pnl_pct=? WHERE id=?",
                            (round(pnl, 3), pos_id))
        con.commit()
    except Exception as e:
        log.debug(f"open_positions Update: {e}")

    con.close()

    if not today_rows:
        log.info("Keine heutigen Signale für Tagesanalyse")
        return

    log.info(f"Tagesanalyse für {len(today_rows)} Signale")

    # Header
    _send_telegram(
        f"📊 TAGESANALYSE — {et.strftime('%d.%m.%Y')}\n"
        f"{len(today_rows)} Signal(e) heute\n"
        f"EUR/USD: {fx:.4f}"
    )
    time.sleep(1)

    # Pro Signal: Intraday holen + analysieren
    for row in today_rows:
        ticker = row[1]
        try:
            intraday = _get_intraday(ticker)
            analysis = _analyse_signal(row, intraday, fx)
            if analysis:
                _send_telegram(analysis)
                log.info(f"  Analyse gesendet: {ticker}")
            time.sleep(1.5)
        except Exception as e:
            log.warning(f"  Analyse-Fehler {ticker}: {e}")

    # Batch-Report ab n>=10
    _maybe_send_batch_report(db_path)


def _maybe_send_batch_report(db_path: str):
    """Sendet Batch-Report wenn n>=10 Signale mit Outcome vorhanden."""
    try:
        con = sqlite3.connect(db_path)
        n = con.execute(
            "SELECT COUNT(*) FROM candle_signals WHERE outcome_pct IS NOT NULL"
        ).fetchone()[0]
        con.close()
    except Exception:
        return

    if n < 10:
        log.info(f"Batch-Report: {n}/10 Signale — noch nicht genug")
        return

    report = print_report(db_path, send_telegram=True)
    log.info("Batch-Report gesendet")


def print_report(db_path: str = None, days: int = 30,
                 send_telegram: bool = False) -> str:
    """Performance-Report. Optional via Telegram senden."""
    if db_path is None:
        db_path = _db_path()

    try:
        con = sqlite3.connect(db_path)
        stats = con.execute(f"""
            SELECT
                COUNT(*),
                SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN outcome_pct <= 0 THEN 1 ELSE 0 END),
                ROUND(AVG(outcome_pct), 2),
                ROUND(MAX(outcome_pct), 2),
                ROUND(MIN(outcome_pct), 2)
            FROM candle_signals
            WHERE outcome_pct IS NOT NULL
              AND ts >= datetime('now', '-{days} days')
        """).fetchone()

        total, wins, losses, avg_pct, best, worst = stats
        if not total:
            return "Noch keine Outcomes — warte auf erste Tages-Abschlüsse."

        winrate = round(wins / total * 100, 1)

        patterns = con.execute(f"""
            SELECT pattern, COUNT(*) as n,
                   ROUND(AVG(outcome_pct), 2) as avg_pct,
                   SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as wr
            FROM candle_signals
            WHERE outcome_pct IS NOT NULL
              AND ts >= datetime('now', '-{days} days')
              AND pattern IS NOT NULL
            GROUP BY pattern ORDER BY avg_pct DESC LIMIT 8
        """).fetchall()

        catalysts = con.execute(f"""
            SELECT catalyst, COUNT(*) as n,
                   ROUND(AVG(outcome_pct), 2) as avg_pct,
                   SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as wr
            FROM candle_signals
            WHERE outcome_pct IS NOT NULL
              AND ts >= datetime('now', '-{days} days')
            GROUP BY catalyst ORDER BY avg_pct DESC LIMIT 5
        """).fetchall()

        directions = con.execute(f"""
            SELECT direction, COUNT(*) as n,
                   ROUND(AVG(outcome_pct), 2) as avg_pct,
                   SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as wr
            FROM candle_signals
            WHERE outcome_pct IS NOT NULL
              AND ts >= datetime('now', '-{days} days')
            GROUP BY direction
        """).fetchall()

        con.close()

        lines = [
            f"📊 PERFORMANCE REPORT ({days} Tage)",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"Signale:   {total}  ({wins}W / {losses}L)",
            f"Win-Rate:  {winrate}%",
            f"Ø Outcome: {avg_pct:+.2f}%",
            f"Bestes:    {best:+.2f}%",
            f"Schlechtestes: {worst:+.2f}%",
            f"",
            f"Nach Muster:",
        ]
        for pat, n, avg, wr in patterns:
            lines.append(f"  {(pat or '—'):<20} n={n}  Ø{avg:+.2f}%  WR={wr:.0f}%")

        lines.append(f"")
        lines.append(f"Nach Catalyst:")
        for cat, n, avg, wr in catalysts:
            lines.append(f"  {(cat or 'none'):<18} n={n}  Ø{avg:+.2f}%  WR={wr:.0f}%")

        lines.append(f"")
        lines.append(f"Nach Richtung:")
        for direc, n, avg, wr in directions:
            lines.append(f"  {direc:<8} n={n}  Ø{avg:+.2f}%  WR={wr:.0f}%")

        report = "\n".join(lines)
        print(report)
        if send_telegram:
            _send_telegram(report)
        return report

    except Exception as e:
        return f"Report-Fehler: {e}"


def migrate_db(db_path: str = None):
    """Schema-Migration: outcome_pct + catalyst + pattern Spalten."""
    if db_path is None:
        db_path = _db_path()
    try:
        con = sqlite3.connect(db_path)
        cols = [r[1] for r in con.execute(
            "PRAGMA table_info(candle_signals)").fetchall()]
        for col, typ in [("outcome_pct", "REAL"), ("catalyst", "TEXT"),
                         ("pattern", "TEXT"), ("score", "INTEGER")]:
            if col not in cols:
                try:
                    con.execute(f"ALTER TABLE candle_signals ADD COLUMN {col} {typ} DEFAULT NULL")
                    log.info(f"candle_signals: {col} Spalte hinzugefügt")
                except Exception:
                    pass
        # open_positions
        try:
            cols2 = [r[1] for r in con.execute(
                "PRAGMA table_info(open_positions)").fetchall()]
            if "pnl_pct" not in cols2:
                con.execute("ALTER TABLE open_positions ADD COLUMN pnl_pct REAL DEFAULT NULL")
        except Exception:
            pass
        con.commit()
        con.close()
    except Exception as e:
        log.warning(f"DB migrate Fehler: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    migrate_db()
    run_outcome_update()

"""
outcome_tracker.py  —  P40b Outcome-Tracker
Panzer Bot / Candle Scanner

Läuft täglich um 16:15 ET (nach US-Market-Close).
Holt Schlusskurs für jedes Signal der letzten 5 Tage
und schreibt outcome_pct in signals.db / candle_signals.

Scheduler-Eintrag in main.py:
  from outcome_tracker import run_outcome_update
  scheduler.add_job(run_outcome_update, 'cron',
                    hour=20, minute=15,   # 20:15 UTC = 16:15 ET
                    id='outcome_tracker')

Auswertung (manuell oder als /report Telegram-Befehl):
  python3 -c "from outcome_tracker import print_report; print_report()"
"""

import sqlite3
import logging
import time
from datetime import datetime, timezone, timedelta

import yfinance as yf

log = logging.getLogger(__name__)


def run_outcome_update(db_path: str = None):
    """
    Holt Schlusskurse für alle Signale der letzten 5 Tage
    und befüllt outcome_pct wenn noch NULL.
    """
    import config
    if db_path is None:
        db_path = getattr(config, "DB_PATH", "data/signals.db")

    et = datetime.now(timezone(timedelta(hours=-4)))
    log.info(f"OUTCOME UPDATE — {et.strftime('%Y-%m-%d %H:%M ET')}")

    con = sqlite3.connect(db_path)

    # Alle offenen Signale der letzten 5 Tage
    rows = con.execute("""
        SELECT id, ticker, direction, price, ts
        FROM candle_signals
        WHERE outcome_pct IS NULL
          AND ts >= datetime('now', '-5 days')
        ORDER BY ts DESC
        LIMIT 200
    """).fetchall()

    if not rows:
        log.info("Keine offenen Outcomes")
        con.close()
        return

    log.info(f"  {len(rows)} Signale zu aktualisieren")
    updated = 0

    # Tickers deduplizieren für API-Effizienz
    tickers = list({r[1] for r in rows})
    prices  = {}
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(period="2d", interval="1d")
            if not df.empty:
                prices[ticker] = float(df["Close"].iloc[-1])
            time.sleep(0.3)
        except Exception as e:
            log.debug(f"  Preis-Fehler {ticker}: {e}")

    for row_id, ticker, direction, entry_price, ts in rows:
        if ticker not in prices or not entry_price:
            continue
        close = prices[ticker]
        if direction == "LONG":
            outcome = (close - entry_price) / entry_price * 100
        else:
            outcome = (entry_price - close) / entry_price * 100

        con.execute(
            "UPDATE candle_signals SET outcome_pct=? WHERE id=?",
            (round(outcome, 3), row_id)
        )
        updated += 1

    con.commit()

    # Auch open_positions aktualisieren
    try:
        pos_rows = con.execute("""
            SELECT id, ticker, direction, entry_price
            FROM open_positions
            WHERE status='OPEN'
        """).fetchall()
        for pos_id, ticker, direction, entry in pos_rows:
            if ticker in prices and entry:
                close = prices[ticker]
                pnl   = ((close - entry) / entry * 100
                         if direction == "LONG"
                         else (entry - close) / entry * 100)
                con.execute(
                    "UPDATE open_positions SET pnl_pct=? WHERE id=?",
                    (round(pnl, 3), pos_id)
                )
        con.commit()
    except Exception:
        pass

    con.close()
    log.info(f"  Outcome Update: {updated} Signale aktualisiert")


def print_report(db_path: str = None, days: int = 30) -> str:
    """
    Gibt Performance-Report aus der DB aus.
    Nutzbar als /report Telegram-Befehl.
    """
    import config
    if db_path is None:
        db_path = getattr(config, "DB_PATH", "data/signals.db")

    try:
        con = sqlite3.connect(db_path)

        # Gesamtstatistik
        stats = con.execute(f"""
            SELECT
                COUNT(*)                                    as total,
                SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome_pct <= 0 THEN 1 ELSE 0 END) as losses,
                ROUND(AVG(outcome_pct), 2)                  as avg_pct,
                ROUND(MAX(outcome_pct), 2)                  as best,
                ROUND(MIN(outcome_pct), 2)                  as worst
            FROM candle_signals
            WHERE outcome_pct IS NOT NULL
              AND ts >= datetime('now', '-{days} days')
        """).fetchone()

        total, wins, losses, avg_pct, best, worst = stats
        if not total:
            return "Noch keine Outcomes — warte auf erste Tages-Abschlüsse."

        winrate = round(wins / total * 100, 1) if total else 0

        # Nach Muster
        patterns = con.execute(f"""
            SELECT pattern,
                   COUNT(*) as n,
                   ROUND(AVG(outcome_pct), 2) as avg_pct,
                   SUM(CASE WHEN outcome_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as wr
            FROM candle_signals
            WHERE outcome_pct IS NOT NULL
              AND ts >= datetime('now', '-{days} days')
              AND pattern IS NOT NULL
            GROUP BY pattern
            ORDER BY avg_pct DESC
            LIMIT 8
        """).fetchall()

        # Nach Richtung
        directions = con.execute(f"""
            SELECT direction,
                   COUNT(*) as n,
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
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Signale gesamt:  {total}",
            f"Win-Rate:        {winrate}%  ({wins}W / {losses}L)",
            f"Ø Outcome:       {avg_pct:+.2f}%",
            f"Bestes Signal:   {best:+.2f}%",
            f"Schlechtestes:   {worst:+.2f}%",
            f"",
            f"🕯 Nach Muster:",
        ]
        for pat, n, avg, wr in patterns:
            lines.append(f"  {pat:<22} n={n:3d}  Ø{avg:+.2f}%  WR={wr:.0f}%")

        lines.append(f"")
        lines.append(f"↕ Nach Richtung:")
        for direc, n, avg, wr in directions:
            lines.append(f"  {direc:<8} n={n:3d}  Ø{avg:+.2f}%  WR={wr:.0f}%")

        report = "\n".join(lines)
        print(report)
        return report

    except Exception as e:
        return f"Report-Fehler: {e}"


# ── Schema-Migration: outcome_pct Spalte hinzufügen ──────────
def migrate_db(db_path: str = None):
    """Fügt outcome_pct Spalte hinzu falls nicht vorhanden."""
    import config
    if db_path is None:
        db_path = getattr(config, "DB_PATH", "data/signals.db")
    try:
        con = sqlite3.connect(db_path)
        # candle_signals
        cols = [r[1] for r in con.execute(
            "PRAGMA table_info(candle_signals)").fetchall()]
        if "outcome_pct" not in cols:
            con.execute(
                "ALTER TABLE candle_signals ADD COLUMN outcome_pct REAL DEFAULT NULL")
            log.info("candle_signals: outcome_pct Spalte hinzugefügt")
        # open_positions
        cols2 = [r[1] for r in con.execute(
            "PRAGMA table_info(open_positions)").fetchall()]
        if "pnl_pct" not in cols2:
            try:
                con.execute(
                    "ALTER TABLE open_positions ADD COLUMN pnl_pct REAL DEFAULT NULL")
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
    print_report()

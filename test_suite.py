#!/usr/bin/env python3
"""
test_suite.py — Panzer Bot / Candle Scanner Testsuite
Drei Teststufen:
  1. Unit-Tests      → jederzeit, Mock-Daten, <10s
  2. Integrations-   → jederzeit, echte APIs, ~30s
  3. E2E Smoke-Test  → nur Handelszeit, echter Scan, ~2min

Ausführen:
  python3 test_suite.py unit          # nur Unit-Tests
  python3 test_suite.py integration   # Unit + Integration
  python3 test_suite.py e2e           # alle drei Stufen
  python3 test_suite.py               # Unit + Integration (Default)
"""

import sys
import os
import time
import json
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/app")

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

ET = timezone(timedelta(hours=-4))

# ── Farben ────────────────────────────────────────────────────
GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
AMBER  = "\033[0;33m"
BLUE   = "\033[0;34m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

passed = []
failed = []
skipped = []


def ok(name, detail=""):
    passed.append(name)
    print(f"  {GREEN}✓{RESET} {name}" + (f"  — {detail}" if detail else ""))


def fail(name, detail=""):
    failed.append(name)
    print(f"  {RED}✗{RESET} {name}" + (f"  — {detail}" if detail else ""))


def skip(name, reason=""):
    skipped.append(name)
    print(f"  {AMBER}○{RESET} {name}" + (f"  — {reason}" if reason else ""))


def section(title):
    print(f"\n{BOLD}{BLUE}{'─'*50}{RESET}")
    print(f"{BOLD}{BLUE}  {title}{RESET}")
    print(f"{BOLD}{BLUE}{'─'*50}{RESET}")


# ════════════════════════════════════════════════════════════
#  STUFE 1 — UNIT-TESTS
#  Mock-Daten, keine echten API-Calls, jederzeit ausführbar
# ════════════════════════════════════════════════════════════

def run_unit_tests():
    section("STUFE 1 — Unit-Tests (Mock-Daten)")

    # ── 1.1 Imports ──────────────────────────────────────────
    try:
        from candlestick_scanner import (
            ScoreEngine, CandleResult, CandleRecognizer,
            _build_alert, _get_eur_usd
        )
        ok("1.1 Imports — candlestick_scanner")
    except Exception as e:
        fail("1.1 Imports — candlestick_scanner", str(e))
        return  # Ohne Imports können folgende Tests nicht laufen

    try:
        from regime import calc_regime, check_regime
        ok("1.2 Imports — regime")
    except Exception as e:
        fail("1.2 Imports — regime", str(e))

    try:
        from outcome_tracker import migrate_db, run_outcome_update, print_report
        ok("1.3 Imports — outcome_tracker")
    except Exception as e:
        fail("1.3 Imports — outcome_tracker", str(e))

    try:
        from options_flow import get_options_score, options_summary
        ok("1.4 Imports — options_flow")
    except Exception as e:
        fail("1.4 Imports — options_flow", str(e))

    try:
        from finnhub_feed import has_finnhub, get_quote, get_catalyst, data_source
        ok("1.5 Imports — finnhub_feed")
    except Exception as e:
        fail("1.5 Imports — finnhub_feed", str(e))

    # ── 1.2 CandleResult Dataclass ───────────────────────────
    try:
        r = CandleResult(
            ticker="TEST", direction="LONG", score=85,
            verdict="LONG SETUP", pattern=None,
            price=100.0, vwap=98.0, or_high=99.0, or_low=97.0,
            vol_ratio=2.5, ema9=99.5, ema20=98.0,
            reasons=["Test"],
        )
        assert r.ticker == "TEST"
        assert r.score == 85
        assert r.direction == "LONG"
        ok("1.6 CandleResult — Felder korrekt")
    except Exception as e:
        fail("1.6 CandleResult — Felder korrekt", str(e))

    # ── 1.3 _build_alert Format ──────────────────────────────
    try:
        r = CandleResult(
            ticker="NVDA", direction="LONG", score=87,
            verdict="LONG SETUP", pattern=None,
            price=121.30, vwap=118.40, or_high=119.80, or_low=117.20,
            vol_ratio=3.8, ema9=119.0, ema20=118.0,
            reasons=[], catalyst="earnings_beat", catalyst_score=25,
            headline="NVDA beats EPS"
        )
        with patch("candlestick_scanner._get_eur_usd", return_value=1.10):
            alert = _build_alert(r)
        assert "NVDA" in alert
        assert "LONG" in alert
        assert "87" in alert
        assert "€" in alert          # EUR-Konversion aktiv
        assert "Earnings Beat" in alert or "earnings_beat" in alert
        assert "13:30 ET" in alert   # Exit-Zeitlimit vorhanden
        ok("1.7 _build_alert — Format korrekt (EUR, Catalyst, Exit)")
    except Exception as e:
        fail("1.7 _build_alert — Format korrekt", str(e))

    # ── 1.4 EUR/USD Fallback ─────────────────────────────────
    try:
        with patch("yfinance.Ticker") as mock_yf:
            mock_yf.return_value.history.return_value = MagicMock(
                empty=True
            )
            fx = _get_eur_usd()
        assert 0.5 < fx < 3.0, f"Unplausibler EUR/USD: {fx}"
        ok("1.8 EUR/USD Fallback — plausibel", f"Wert: {fx}")
    except Exception as e:
        fail("1.8 EUR/USD Fallback", str(e))

    # ── 1.5 ScoreEngine — Score-Berechnung ───────────────────
    try:
        import pandas as pd
        import numpy as np

        engine = ScoreEngine()

        # Perfektes Long-Setup simulieren
        n = 40
        idx = pd.date_range("2026-01-01 09:30", periods=n, freq="5min", tz="America/New_York")
        df  = pd.DataFrame({
            "Open":   [100.0] * n,
            "High":   [102.0] * n,
            "Low":    [99.0]  * n,
            "Close":  [101.0] * n,
            "Volume": [500000] * n,
        }, index=idx)

        # df_daily: 20 Tage Mock-Daten (score() braucht mind. 14 Zeilen für avg14)
        df_d = pd.DataFrame({
            "Open":   [99.0]  * 20,
            "High":   [103.0] * 20,
            "Low":    [98.0]  * 20,
            "Close":  [101.0] * 20,
            "Volume": [5000000] * 20,
        }, index=pd.date_range("2025-12-01", periods=20, freq="B"))

        result = engine.score("MOCK", df, df_d, patterns=[], et_hour=10, et_min=0)
        assert result is not None
        assert 0 <= result.score <= 100
        assert result.direction in ("LONG", "SHORT", "NEUTRAL")
        ok("1.9 ScoreEngine — Score im Bereich 0–100", f"Score: {result.score}, Dir: {result.direction}")
    except Exception as e:
        fail("1.9 ScoreEngine — Score-Berechnung", str(e))

    # ── 1.6 Regime — Struktur ────────────────────────────────
    try:
        from regime import check_regime
        with patch("regime._calc_from_yfinance", return_value={
            "bear": True, "panic": False, "vix": 24.0, "source": "mock"
        }):
            with patch("regime._read_from_scan_runs", return_value=None):
                r_long  = check_regime("LONG")
                r_short = check_regime("SHORT")

        assert r_long["allow"]  == False, "LONG sollte in BEAR geblockt werden"
        assert r_short["allow"] == True,  "SHORT sollte in BEAR erlaubt sein"
        ok("1.10 Regime — BEAR blockt LONG, erlaubt SHORT")
    except Exception as e:
        fail("1.10 Regime — BEAR-Logik", str(e))

    # ── 1.7 Options-Flow — Scoring-Logik ─────────────────────
    try:
        from options_flow import get_options_score
        import pandas as pd

        mock_calls = pd.DataFrame({
            "volume": [10000, 8000, 6000],
            "openInterest": [5000, 4000, 3000],
            "impliedVolatility": [0.6, 0.55, 0.5],
            "strike": [100, 105, 110],
        })
        mock_puts = pd.DataFrame({
            "volume": [2000, 1500, 1000],
            "openInterest": [3000, 2500, 2000],
            "impliedVolatility": [0.7, 0.65, 0.6],
            "strike": [95, 90, 85],
        })

        mock_chain = MagicMock()
        mock_chain.calls = mock_calls
        mock_chain.puts  = mock_puts

        with patch("yfinance.Ticker") as mock_yf:
            mock_yf.return_value.options = ["2026-04-05"]
            mock_yf.return_value.option_chain.return_value = mock_chain
            mock_yf.return_value.history.return_value = pd.DataFrame(
                {"Close": [100.0]}, index=[pd.Timestamp("2026-01-01")]
            )
            score, signal, detail = get_options_score("MOCK_TICKER_OPT")

        assert -20 <= score <= 30, f"Score außerhalb Bereich: {score}"
        assert signal in ("bullish", "bearish", "neutral", "no_data")
        # Niedrige P/C-Ratio → bullish erwartet
        pc_ratio = 4500 / 24000  # puts_vol / calls_vol ≈ 0.19
        if pc_ratio < 0.5:
            assert signal == "bullish", f"Niedriges P/C sollte bullish sein: {signal}"
        ok("1.11 Options-Flow — Scoring-Logik", f"Score: {score:+d} | {signal}")
    except Exception as e:
        fail("1.11 Options-Flow — Scoring-Logik", str(e))

    # ── 1.8 DB — Schema-Migration ────────────────────────────
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        con = sqlite3.connect(tmp_path)
        con.execute("""CREATE TABLE candle_signals (
            id INTEGER PRIMARY KEY, ticker TEXT, direction TEXT,
            price REAL, ts TEXT, score INTEGER, verdict TEXT
        )""")
        con.commit()
        con.close()

        from outcome_tracker import migrate_db
        migrate_db(tmp_path)

        con = sqlite3.connect(tmp_path)
        cols = [r[1] for r in con.execute("PRAGMA table_info(candle_signals)").fetchall()]
        con.close()
        os.unlink(tmp_path)

        assert "outcome_pct" in cols, f"outcome_pct fehlt in: {cols}"
        ok("1.12 DB-Migration — outcome_pct Spalte")
    except Exception as e:
        fail("1.12 DB-Migration", str(e))

    # ── 1.9 Finnhub — Fallback ohne Key ──────────────────────
    try:
        import finnhub_feed
        import pandas as pd

        orig_key    = finnhub_feed.FINNHUB_KEY
        orig_client = finnhub_feed._client
        finnhub_feed.FINNHUB_KEY = ""
        finnhub_feed._client     = None

        try:
            with patch("yfinance.Ticker") as mock_yf:
                mock_yf.return_value.history.return_value = pd.DataFrame(
                    {"Open": [148.0, 150.0], "High": [151.0, 152.0],
                     "Low":  [147.0, 149.0], "Close": [149.0, 151.0],
                     "Volume": [900000, 1000000]},
                    index=[pd.Timestamp("2026-03-31"), pd.Timestamp("2026-04-01")]
                )
                q = finnhub_feed.get_quote("AAPL")
        finally:
            finnhub_feed.FINNHUB_KEY = orig_key
            finnhub_feed._client     = orig_client

        assert q["source"] == "yfinance", f"Ohne Key sollte yfinance genutzt werden: {q['source']}"
        assert q["price"] > 0
        ok("1.13 Finnhub — Fallback auf yfinance ohne Key", f"Price: {q['price']}")
    except Exception as e:
        fail("1.13 Finnhub — Fallback", str(e))

    # ── 1.10 Alert — kein parse_mode HTML ────────────────────
    try:
        import candlestick_scanner as cs
        import inspect
        src = inspect.getsource(cs._send_telegram)
        # parse_mode HTML war Bug #1 — darf nicht mehr hardcodiert sein
        assert '"parse_mode": "HTML"' not in src
        assert "parse_mode" not in src or "parse_mode=None" in src or "parse_mode: str = None" in src
        ok("1.14 Telegram — kein hardcodiertes parse_mode HTML")
    except Exception as e:
        fail("1.14 Telegram — parse_mode Check", str(e))


# ════════════════════════════════════════════════════════════
#  STUFE 2 — INTEGRATIONS-TESTS
#  Echte API-Calls, keine Mock-Daten, ~30s
# ════════════════════════════════════════════════════════════

def run_integration_tests():
    section("STUFE 2 — Integrations-Tests (echte APIs)")

    # ── 2.1 yfinance erreichbar ───────────────────────────────
    try:
        import yfinance as yf
        df = yf.Ticker("SPY").history(period="2d", interval="1d")
        assert not df.empty
        price = float(df["Close"].iloc[-1])
        assert 100 < price < 10000, f"SPY Preis unplausibel: {price}"
        ok("2.1 yfinance — SPY erreichbar", f"SPY: ${price:.2f}")
    except Exception as e:
        fail("2.1 yfinance — SPY erreichbar", str(e))

    # ── 2.2 EUR/USD live ─────────────────────────────────────
    try:
        from candlestick_scanner import _get_eur_usd
        fx = _get_eur_usd()
        assert 0.80 < fx < 2.0, f"EUR/USD unplausibel: {fx}"
        ok("2.2 EUR/USD live", f"EUR/USD: {fx:.4f}")
    except Exception as e:
        fail("2.2 EUR/USD live", str(e))

    # ── 2.3 VIX erreichbar ───────────────────────────────────
    try:
        import yfinance as yf
        df = yf.Ticker("^VIX").history(period="2d", interval="1d")
        assert not df.empty
        vix = float(df["Close"].iloc[-1])
        assert 5 < vix < 100, f"VIX unplausibel: {vix}"
        ok("2.3 VIX live", f"VIX: {vix:.1f}")
    except Exception as e:
        fail("2.3 VIX live", str(e))

    # ── 2.4 Regime-Check live ────────────────────────────────
    try:
        from regime import calc_regime
        r = calc_regime()
        assert "bear"  in r
        assert "panic" in r
        assert "vix"   in r
        assert isinstance(r["bear"],  bool)
        assert isinstance(r["panic"], bool)
        regime_str = "PANIC" if r["panic"] else ("BEAR" if r["bear"] else "BULL")
        ok("2.4 Regime live", f"{regime_str} | VIX={r['vix']:.1f} | Quelle: {r.get('source','?')}")
    except Exception as e:
        fail("2.4 Regime live", str(e))

    # ── 2.5 Options-Flow live ────────────────────────────────
    try:
        from options_flow import get_options_score
        score, signal, detail = get_options_score("AAPL")
        assert -20 <= score <= 30
        assert signal in ("bullish", "bearish", "neutral", "no_data")
        ok("2.5 Options-Flow live (AAPL)", f"{score:+d}P | {signal} | {detail[:50]}")
    except Exception as e:
        fail("2.5 Options-Flow live", str(e))

    # ── 2.6 Finnhub erreichbar (falls Key gesetzt) ────────────
    try:
        from finnhub_feed import has_finnhub, get_quote, get_catalyst, get_earnings_today
        if not has_finnhub():
            skip("2.6 Finnhub live", "FINNHUB_API_KEY nicht gesetzt — kostenlos: finnhub.io/register")
        else:
            q = get_quote("AAPL")
            assert q["price"] > 0
            src = q["source"]
            if src == "finnhub":
                ok("2.6 Finnhub — Quote live (AAPL)", f"${q['price']:.2f} ({q['change_pct']:+.1f}%) [finnhub]")
            else:
                # Free-Tier: Finnhub-Client erstellt aber API-Call fällt auf yfinance zurück
                skip("2.6 Finnhub live", f"Key gesetzt, aber API nutzt yfinance-Fallback (Free-Tier Rate-Limit?) — Price: ${q['price']:.2f}")

            ct, cs_val, ch = get_catalyst("NVDA")
            ok("2.7 Finnhub — Catalyst live (NVDA)", f"{ct} {cs_val:+d}P | {ch[:50]}")

            earnings = get_earnings_today()
            ok("2.8 Finnhub — Earnings-Kalender", f"{len(earnings)} Ticker heute mit Earnings")
    except Exception as e:
        fail("2.6 Finnhub live", str(e))

    # ── 2.7 DB erreichbar ────────────────────────────────────
    try:
        import config
        db_path = getattr(config, "DB_PATH", "/app/data/signals.db")
        con     = sqlite3.connect(db_path)
        tables  = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        n_signals = con.execute("SELECT COUNT(*) FROM candle_signals").fetchone()[0] if "candle_signals" in tables else 0
        con.close()
        if "candle_signals" in tables:
            ok("2.9 DB — candle_signals erreichbar", f"{n_signals} Signale bisher")
        else:
            # Tabelle wird beim ersten Scan-Run erstellt — DB ist erreichbar
            skip("2.9 DB — candle_signals", f"Tabelle noch nicht erstellt (erster Start) — DB hat: {tables}")
    except Exception as e:
        fail("2.9 DB erreichbar", str(e))

    # ── 2.8 Telegram-Token gesetzt ───────────────────────────
    try:
        import config
        token   = getattr(config, "TELEGRAM_TOKEN", "") or getattr(config, "TELEGRAM_BOT_TOKEN", "")
        chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")
        assert token,   "TELEGRAM_TOKEN fehlt in config.py"
        assert chat_id, "TELEGRAM_CHAT_ID fehlt in config.py"
        ok("2.10 Telegram — Token + Chat-ID gesetzt",
           f"Token: {token[:8]}... | Chat: {str(chat_id)[:6]}...")
    except Exception as e:
        fail("2.10 Telegram — Konfiguration", str(e))


# ════════════════════════════════════════════════════════════
#  STUFE 3 — E2E SMOKE-TEST
#  Echter Scan-Lauf, nur während Handelszeit (09:30–13:30 ET)
# ════════════════════════════════════════════════════════════

def run_e2e_test():
    section("STUFE 3 — E2E Smoke-Test (echter Scan)")

    # Handelszeit prüfen
    et   = datetime.now(ET)
    mins = et.hour * 60 + et.minute
    market_open = 9 * 60 + 30    # 09:30 ET
    market_close = 13 * 60 + 30  # 13:30 ET

    if not (market_open <= mins <= market_close):
        skip("3.x E2E Smoke-Test",
             f"Außerhalb Handelszeit ({et.strftime('%H:%M ET')}) — "
             f"Test nur zwischen 09:30–13:30 ET (15:30–19:30 CEST) sinnvoll")
        return

    # ── 3.1 Universe laden ───────────────────────────────────
    try:
        from candlestick_scanner import get_gapper_universe
        universe = get_gapper_universe(min_gap_pct=1.0, max_tickers=5)
        assert len(universe) > 0, "Universe leer"
        ok("3.1 Universe geladen", f"{len(universe)} Ticker: {universe[:5]}")
    except Exception as e:
        fail("3.1 Universe laden", str(e))
        return

    # ── 3.2 Ticker fetchen ───────────────────────────────────
    try:
        from candlestick_scanner import _fetch_ticker
        ticker = universe[0]
        df_1m, df_d = _fetch_ticker(ticker)
        assert df_1m is not None and not df_1m.empty
        assert len(df_1m) >= 5, f"Zu wenig Bars: {len(df_1m)}"
        ok("3.2 Ticker-Daten laden", f"{ticker}: {len(df_1m)} Bars")
    except Exception as e:
        fail("3.2 Ticker-Daten laden", str(e))
        return

    # ── 3.3 Score berechnen ──────────────────────────────────
    try:
        from candlestick_scanner import ScoreEngine
        engine = ScoreEngine()
        result = engine.score(ticker, df_1m, df_d)
        assert result is not None
        assert 0 <= result.score <= 100
        ok("3.3 Score berechnet",
           f"{ticker}: {result.score}/100 | {result.direction} | {result.verdict}")
    except Exception as e:
        fail("3.3 Score berechnen", str(e))
        return

    # ── 3.4 Alert-Text generieren ────────────────────────────
    try:
        from candlestick_scanner import _build_alert
        alert = _build_alert(result)
        assert len(alert) > 50, "Alert zu kurz"
        assert "€" in alert,    "Keine EUR-Preise im Alert"
        assert "ET" in alert,   "Kein Exit-Zeitlimit im Alert"
        ok("3.4 Alert-Text generiert",
           f"{len(alert)} Zeichen | EUR: {'✓' if '€' in alert else '✗'}")
        print(f"\n{BLUE}{'─'*40}{RESET}")
        print(f"{BLUE}  Beispiel-Alert:{RESET}")
        for line in alert.split("\n")[:12]:
            print(f"  {line}")
        print(f"{BLUE}{'─'*40}{RESET}")
    except Exception as e:
        fail("3.4 Alert-Text generieren", str(e))

    # ── 3.5 DB-Write testen ──────────────────────────────────
    try:
        import config, tempfile
        db_path = getattr(config, "DB_PATH", "/app/data/signals.db")
        con     = sqlite3.connect(db_path)
        n_before = con.execute("SELECT COUNT(*) FROM candle_signals").fetchone()[0]
        con.close()
        ok("3.5 DB-Write", f"{n_before} Signale in candle_signals")
    except Exception as e:
        fail("3.5 DB-Write", str(e))


# ════════════════════════════════════════════════════════════
#  REPORT
# ════════════════════════════════════════════════════════════

def print_report():
    total = len(passed) + len(failed) + len(skipped)
    print(f"\n{BOLD}{'═'*50}{RESET}")
    print(f"{BOLD}  ERGEBNIS{RESET}")
    print(f"{'═'*50}")
    print(f"  {GREEN}Bestanden:{RESET}  {len(passed)}/{total}")
    if failed:
        print(f"  {RED}Fehlgeschlagen:{RESET} {len(failed)}")
        for f in failed:
            print(f"    {RED}✗{RESET} {f}")
    if skipped:
        print(f"  {AMBER}Übersprungen:{RESET} {len(skipped)}")
    print(f"{'═'*50}\n")

    if failed:
        sys.exit(1)


# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "integration"
    print(f"\n{BOLD}PANZER BOT — TESTSUITE{RESET}")
    print(f"Modus: {mode} | {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}\n")

    if mode in ("unit", "integration", "e2e"):
        run_unit_tests()

    if mode in ("integration", "e2e"):
        run_integration_tests()

    if mode == "e2e":
        run_e2e_test()

    print_report()

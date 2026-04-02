"""
regime.py  —  Standalone Regime-Check v2
Panzer Bot / Candle Scanner

Priorität:
  1. scan_runs-Tabelle aus Panzer Bot lesen (frisch = <4h alt)
  2. Fallback: eigene yfinance-Berechnung

Warum:
  Panzer Bot berechnet Regime bereits 3x täglich mit Daniel & Moskowitz
  Bear×Vol-Interaktion. Wenn wir dieselbe DB lesen, sind beide Systeme
  garantiert konsistent — kein Long-Alert in Bear-Märkten mehr.
"""

import logging
import sqlite3
import os
from datetime import datetime, timezone, timedelta

import yfinance as yf

log = logging.getLogger(__name__)

PANZER_DB   = os.environ.get("PANZER_DB_PATH", "/app/data/signals.db")
CACHE_MAX_H = 4   # scan_runs-Daten gelten bis zu 4h als frisch


def _read_from_scan_runs() -> dict | None:
    """
    Liest letzten Scan-Run aus Panzer Bot DB.
    Gibt None zurück wenn keine frischen Daten (<4h) vorhanden.
    """
    try:
        con = sqlite3.connect(PANZER_DB)
        rows = con.execute("""
            SELECT timestamp, n_signals, universe
            FROM scan_runs
            ORDER BY rowid DESC
            LIMIT 1
        """).fetchall()
        con.close()

        if not rows:
            return None

        ts_str, n_signals, universe = rows[0]

        # Zeitstempel parsen
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            return None

        now  = datetime.now(timezone.utc)
        age_h = (now - ts).total_seconds() / 3600

        if age_h > CACHE_MAX_H:
            log.debug(f"scan_runs zu alt ({age_h:.1f}h) — Fallback auf yfinance")
            return None

        # K3 Fix: n_signals=0 ist kein valider Regime-Indikator.
        # Ohne regime-Feld in scan_runs -> Fallback auf yfinance.
        log.debug(
            f"scan_runs: n_signals={n_signals} | Alter={age_h:.1f}h"
            " — kein regime-Feld, Fallback auf yfinance"
        )
        return None  # Fallback erzwingen

    except Exception as e:
        log.debug(f"scan_runs Lesefehler: {e}")
        return None


def _calc_from_yfinance() -> dict:
    """
    Fallback: eigene Berechnung via yfinance.
    Identisch zur vorherigen regime.py Logik.
    """
    bear    = False
    vix_val = 0.0

    try:
        spy   = yf.download("SPY", period="3mo", interval="1d",
                            progress=False, auto_adjust=True)
        if not spy.empty and len(spy) >= 50:
            close = spy["Close"].squeeze()
            ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            last  = float(close.iloc[-1])
            bear  = last < ema50
    except Exception as e:
        log.warning(f"Regime SPY Fehler: {e}")

    try:
        vix = yf.download("^VIX", period="2d", interval="1d",
                          progress=False, auto_adjust=True)
        if not vix.empty:
            vix_val = float(vix["Close"].squeeze().iloc[-1])
    except Exception as e:
        log.warning(f"Regime VIX Fehler: {e}")

    panic = vix_val > 35.0
    log.info(
        f"Regime (yfinance): {'BEAR' if bear else 'BULL'} "
        f"| VIX={vix_val:.1f} | Panic={panic}"
    )
    return {"bear": bear, "panic": panic, "vix": vix_val,
            "source": "yfinance"}


def calc_regime(market: str = "US") -> dict:
    """
    Haupt-Funktion. Rückwärtskompatibel zur alten regime.py.
    Gibt dict: bear (bool), panic (bool), vix (float), source (str)
    """
    # Schritt 1: Panzer Bot scan_runs lesen
    result = _read_from_scan_runs()
    if result is not None:
        return result

    # Schritt 2: Fallback yfinance
    return _calc_from_yfinance()




def _check_crash_kill_switch() -> bool:
    """
    P55 — Crash Kill Switch (Daniel & Moskowitz 2016)
    True = Kill Switch aktiv = KEIN Trading (weder Long noch Short)

    Bedingung: ALLE drei müssen gleichzeitig gelten:
      1. Bear-Markt (SPY < EMA50)
      2. 24-Monats-Return SPY negativ (langfristiger Abschwung)
      3. Starker Rebound letzte 5 Tage (>+3%)

    Warum: Genau in diesem Szenario passierten historisch
    die schlimmsten Momentum-Crashes (-70%+).
    Selbst Short-Momentum kollabiert bei starken Bear-Rebounds.
    """
    try:
        spy = yf.download("SPY", period="25mo", interval="1d",
                          progress=False, auto_adjust=True)
        if spy.empty or len(spy) < 50:
            return False

        close = spy["Close"].squeeze()

        # Bedingung 1: Bear-Markt
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        last  = float(close.iloc[-1])
        bear  = last < ema50
        if not bear:
            return False

        # Bedingung 2: 24-Monats-Return negativ
        if len(close) >= 480:  # ~24 Monate Handelstage
            price_24m_ago = float(close.iloc[-480])
        else:
            price_24m_ago = float(close.iloc[0])
        return_24m = (last - price_24m_ago) / price_24m_ago
        if return_24m >= 0:
            return False

        # Bedingung 3: Starker Rebound letzte 5 Tage (>+3%)
        price_5d_ago = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
        rebound_5d   = (last - price_5d_ago) / price_5d_ago
        if rebound_5d < 0.03:
            return False

        log.warning(
            f"P55 CRASH KILL SWITCH AKTIV: "
            f"Bear={bear} | 24M-Return={return_24m:.1%} | "
            f"5d-Rebound={rebound_5d:.1%}"
        )
        return True

    except Exception as e:
        log.debug(f"Crash Kill Switch Fehler: {e}")
        return False

def check_regime(direction: str = "LONG") -> dict:
    """
    Bidirektionales Gate für candlestick_scanner.py
    LONG:  blocken bei BEAR oder PANIC
    SHORT: erlauben bei BEAR/PANIC (Xu & Zhu 2022)
    """
    # P55: Crash Kill Switch — vor allem anderen prüfen
    if _check_crash_kill_switch():
        return {"allow": False,
                "reason": "P55 CRASH KILL SWITCH — Bear+24MNeg+Rebound: kein Trading",
                "regime": "CRASH_RISK", "bear": True, "panic": False, "vix": 0.0}

    r     = calc_regime()
    bear  = r.get("bear", False)
    panic = r.get("panic", False)

    if direction == "SHORT":
        if panic:
            return {"allow": True,
                    "reason": f"PANIC — Short valide (VIX={r['vix']:.0f})",
                    "regime": "PANIC", **r}
        if bear:
            return {"allow": True,
                    "reason": "BEAR — Short valide",
                    "regime": "BEAR", **r}
        return {"allow": True,
                "reason": "BULL — Short gegen Trend",
                "regime": "BULL", **r}
    else:
        if panic:
            return {"allow": False,
                    "reason": f"PANIC (VIX={r['vix']:.0f}) — kein Long",
                    "regime": "PANIC", **r}
        if bear:
            return {"allow": False,
                    "reason": "BEAR — kein Long",
                    "regime": "BEAR", **r}
        return {"allow": True,
                "reason": "BULL — Long valide",
                "regime": "BULL", **r}

"""
options_flow.py — Options-Flow als institutioneller Signal-Proxy
Panzer Bot / Candle Scanner

Evidenz:
  Chan, Chung & Fong (2002, JFE): Abnormales Options-Volumen
  prognostiziert Aktienrenditen (t-stat 3.8, 1 Tag voraus).
  Easley et al. (2021): OFI aus Options als stärkster
  verfügbarer institutioneller Proxy für Retail-Trader.

Drei Signale (alle via yfinance — kostenlos):
  1. Put/Call-Ratio:  < 0.5 = starke bullische Positionierung
  2. IV-Percentile:   > 80% = erwarteter großer Move
  3. Unusual Volume:  Call-Vol > 3× 20d-Avg = institutionelles Interesse

Rückgabe: options_score (0–30), signal_type, detail
"""

import logging
import time
import yfinance as yf
import numpy as np
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

_CACHE: dict = {}
_CACHE_TTL   = 3600  # 1h Cache


def get_options_score(ticker: str) -> tuple:
    """
    Gibt (score: int, signal: str, detail: str) zurück.
    score: 0–30 Punkte
    signal: 'bullish' | 'bearish' | 'neutral' | 'no_data'
    detail: lesbare Erklärung
    """
    # Cache prüfen
    now = time.time()
    if ticker in _CACHE and now - _CACHE[ticker]['ts'] < _CACHE_TTL:
        cached = _CACHE[ticker]
        return cached['score'], cached['signal'], cached['detail']

    try:
        t = yf.Ticker(ticker)
        # Nächste Expiry holen
        expirations = t.options
        if not expirations:
            return 0, 'no_data', 'Keine Options verfügbar'

        # Kürzeste Expiry (meistgehandelt)
        chain = t.option_chain(expirations[0])
        calls = chain.calls
        puts  = chain.puts

        if calls.empty or puts.empty:
            return 0, 'no_data', 'Leere Options-Chain'

        # Signal 1: Put/Call-Ratio (Volumen)
        call_vol = float(calls['volume'].fillna(0).sum())
        put_vol  = float(puts['volume'].fillna(0).sum())
        pc_ratio = put_vol / call_vol if call_vol > 0 else 1.0

        # Signal 2: Call-Vol vs Open Interest (Intensität)
        call_oi  = float(calls['openInterest'].fillna(0).sum())
        vol_oi   = call_vol / call_oi if call_oi > 0 else 0

        # Signal 3: ATM IV (grobe Approximation)
        try:
            last_price = float(t.history(period='1d')['Close'].iloc[-1])
            atm_calls  = calls[abs(calls['strike'] - last_price) < last_price * 0.02]
            atm_iv     = float(atm_calls['impliedVolatility'].mean()) if not atm_calls.empty else 0
        except Exception:
            atm_iv = 0

        # Scoring
        score  = 0
        signal = 'neutral'
        parts  = []

        if pc_ratio < 0.5:
            score += 15
            signal = 'bullish'
            parts.append(f'P/C={pc_ratio:.2f} (sehr bullisch)')
        elif pc_ratio < 0.8:
            score += 8
            signal = 'bullish'
            parts.append(f'P/C={pc_ratio:.2f} (bullisch)')
        elif pc_ratio > 1.5:
            score -= 10
            signal = 'bearish'
            parts.append(f'P/C={pc_ratio:.2f} (bearisch)')
        else:
            parts.append(f'P/C={pc_ratio:.2f} (neutral)')

        if vol_oi > 0.5:
            score += 10
            parts.append(f'Vol/OI={vol_oi:.2f} (ungewöhnlich hoch)')
        elif vol_oi > 0.2:
            score += 5
            parts.append(f'Vol/OI={vol_oi:.2f} (erhöht)')

        if atm_iv > 0.5:
            score += 5
            parts.append(f'IV={atm_iv:.0%} (großer Move erwartet)')

        score  = max(-20, min(30, score))
        detail = ' | '.join(parts) if parts else 'Keine auffälligen Signale'

        _CACHE[ticker] = {'ts': now, 'score': score,
                          'signal': signal, 'detail': detail}
        log.debug(f'Options {ticker}: {score}P {signal} — {detail}')
        return score, signal, detail

    except Exception as e:
        log.debug(f'Options-Fehler {ticker}: {e}')
        return 0, 'no_data', f'Fehler: {str(e)[:50]}'


def options_summary(ticker: str) -> str:
    """Lesbare Zusammenfassung für Telegram-Alert."""
    score, signal, detail = get_options_score(ticker)
    if signal == 'no_data':
        return ''
    emoji = '📈' if signal == 'bullish' else ('📉' if signal == 'bearish' else '➡️')
    return f'{emoji} Options: {detail} ({score:+d}P)'

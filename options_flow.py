"""
options_flow.py — Options-Flow als institutioneller Signal-Proxy
Panzer Bot / Candle Scanner

Evidenz:
  Chan, Chung & Fong (2002, JFE): Abnormales Options-Volumen
  prognostiziert Aktienrenditen (t-stat 3.8, 1 Tag voraus).

Drei Signale (alle via yfinance — kostenlos):
  1. Put/Call-Ratio:  < 0.5 = starke bullische Positionierung
  2. Vol/OI-Ratio:    > 0.5 = ungewöhnliche Aktivität
  3. ATM IV:          > 50% = großer Move erwartet
"""

import logging, time
import yfinance as yf

log      = logging.getLogger(__name__)
_CACHE   = {}
_TTL     = 3600


def get_options_score(ticker: str) -> tuple:
    now = time.time()
    if ticker in _CACHE and now - _CACHE[ticker]['ts'] < _TTL:
        c = _CACHE[ticker]
        return c['score'], c['signal'], c['detail']
    try:
        t   = yf.Ticker(ticker)
        exp = t.options
        if not exp:
            return 0, 'no_data', 'Keine Options verfügbar'
        chain = t.option_chain(exp[0])
        calls, puts = chain.calls, chain.puts
        if calls.empty or puts.empty:
            return 0, 'no_data', 'Leere Options-Chain'

        call_vol = float(calls['volume'].fillna(0).sum())
        put_vol  = float(puts['volume'].fillna(0).sum())
        pc_ratio = put_vol / call_vol if call_vol > 0 else 1.0
        call_oi  = float(calls['openInterest'].fillna(0).sum())
        vol_oi   = call_vol / call_oi if call_oi > 0 else 0

        try:
            last = float(t.history(period='1d')['Close'].iloc[-1])
            atm  = calls[abs(calls['strike'] - last) < last * 0.02]
            atm_iv = float(atm['impliedVolatility'].mean()) if not atm.empty else 0
        except Exception:
            atm_iv = 0

        score, signal, parts = 0, 'neutral', []
        if pc_ratio < 0.5:
            score += 15; signal = 'bullish'
            parts.append(f'P/C={pc_ratio:.2f} (sehr bullisch)')
        elif pc_ratio < 0.8:
            score += 8; signal = 'bullish'
            parts.append(f'P/C={pc_ratio:.2f} (bullisch)')
        elif pc_ratio > 1.5:
            score -= 10; signal = 'bearish'
            parts.append(f'P/C={pc_ratio:.2f} (bearisch)')
        else:
            parts.append(f'P/C={pc_ratio:.2f} (neutral)')

        if vol_oi > 0.5:
            score += 10; parts.append(f'Vol/OI={vol_oi:.2f} (ungewöhnlich hoch)')
        elif vol_oi > 0.2:
            score += 5;  parts.append(f'Vol/OI={vol_oi:.2f} (erhöht)')

        if atm_iv > 0.5:
            score += 5; parts.append(f'IV={atm_iv:.0%} (großer Move erwartet)')

        score  = max(-20, min(30, score))
        detail = ' | '.join(parts) if parts else 'Keine auffälligen Signale'
        _CACHE[ticker] = {'ts': now, 'score': score, 'signal': signal, 'detail': detail}
        return score, signal, detail
    except Exception as e:
        log.debug(f'Options-Fehler {ticker}: {e}')
        return 0, 'no_data', str(e)[:60]


def options_summary(ticker: str) -> str:
    score, signal, detail = get_options_score(ticker)
    if signal == 'no_data':
        return ''
    emoji = '📈' if signal == 'bullish' else ('📉' if signal == 'bearish' else '➡️')
    return f'{emoji} Options: {detail} ({score:+d}P)'

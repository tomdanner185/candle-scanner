"""
Standalone Regime-Check — ersetzt momentum_expert.calc_regime().
SPY EMA50 + VIX Panic Gate (Daniel & Moskowitz 2016).
"""
import logging
import yfinance as yf
import numpy as np

log = logging.getLogger(__name__)

def calc_regime(market: str = 'US') -> dict:
    """
    Returns dict mit keys: bear (bool), panic (bool), vix (float).
    bear  = SPY unter EMA50 (letzter Close)
    panic = VIX > 35
    """
    try:
        spy = yf.download('SPY', period='3mo', interval='1d',
                          progress=False, auto_adjust=True)
        if spy.empty or len(spy) < 50:
            return {'bear': False, 'panic': False, 'vix': 0}
        close = spy['Close'].squeeze()
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        last  = float(close.iloc[-1])
        bear  = last < ema50
    except Exception as e:
        log.warning(f'Regime SPY Fehler: {e}')
        bear = False

    vix_val = 0.0
    try:
        vix  = yf.download('^VIX', period='2d', interval='1d',
                            progress=False, auto_adjust=True)
        if not vix.empty:
            vix_val = float(vix['Close'].squeeze().iloc[-1])
    except Exception as e:
        log.warning(f'Regime VIX Fehler: {e}')

    panic = vix_val > 35.0
    log.info(f'Regime: {"BEAR" if bear else "BULL"} | VIX={vix_val:.1f} | Panic={panic}')
    return {'bear': bear, 'panic': panic, 'vix': vix_val}

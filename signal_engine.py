"""
signal_engine.py v1 — Stop/Target Calculator + Alert Formatter
Shared module: /root/panzer/ + /root/candle_scanner/

Verwendet von:
  · telegram_bot.py      — Swing-Alerts
  · spike_telegram.py    — Spike/Watchlist-Alerts
  · candlestick_scanner.py — Candle-Alerts
"""
import logging
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

_EU_SUFFIXES = (
    ".DE", ".PA", ".AS", ".MC", ".MI", ".SW",
    ".L",  ".ST", ".OL", ".CO", ".BR", ".LS", ".VI",
)
EURUSD_FALLBACK = 0.92
_eurusd_cache: dict = {"rate": None, "ts": None}


@dataclass
class StopState:
    stop_price: float
    stop_rule: str = "INITIAL"   # INITIAL | TRAIL | BREAK_EVEN


# ── EUR/USD ──────────────────────────────────────────────────────────────

def _is_eu_ticker(ticker: str) -> bool:
    return any(ticker.upper().endswith(s.upper()) for s in _EU_SUFFIXES)


def _get_eurusd() -> float:
    """EUR/USD mit 1h Cache. Fallback: config.EURUSD_FALLBACK oder 0.92."""
    cache = _eurusd_cache
    if cache["ts"] and (datetime.now() - cache["ts"]).seconds < 3600 and cache["rate"]:
        return cache["rate"]
    try:
        import yfinance as yf
        rate = float(yf.Ticker("EURUSD=X").fast_info["lastPrice"])
        if 0.5 < rate < 2.0:
            cache["rate"] = rate
            cache["ts"]   = datetime.now()
            return rate
    except Exception:
        pass
    try:
        import config
        return float(getattr(config, "EURUSD_FALLBACK", EURUSD_FALLBACK))
    except Exception:
        return cache["rate"] or EURUSD_FALLBACK


def _to_eur(price: float, ticker: str, rate: float) -> float:
    """EU-Ticker bereits in EUR; US-Ticker: USD ÷ rate."""
    if _is_eu_ticker(ticker):
        return price
    return price / rate


def _fmt_eur(price: float, ticker: str, rate: float) -> str:
    return f"€{_to_eur(price, ticker, rate):,.2f}"


# ── Zielberechnung ───────────────────────────────────────────────────────

def compute_targets(entry: float, mode: str, stop: StopState) -> dict:
    """
    Ziele basierend auf Stop-Distanz (R-Multiples).
    stop_dist = entry - stop_price  (Long-Annahme, immer positiv)
    """
    stop_dist = entry - stop.stop_price

    if mode == "swing":
        return {
            "target1":        round(entry + 2 * stop_dist, 2),
            "target1_pct":    round(2 * stop_dist / entry * 100, 1),
            "target1_action": "50% verkaufen",
            "target2":        round(entry + 4 * stop_dist, 2),
            "target2_pct":    round(4 * stop_dist / entry * 100, 1),
            "target2_action": "Rest laufen lassen",
        }
    else:  # spike | candle
        return {
            "target1":        round(entry + 2 * stop_dist, 2),
            "target1_pct":    round(2 * stop_dist / entry * 100, 1),
            "target1_action": "Vollständig verkaufen",
        }


# ── Formatter ────────────────────────────────────────────────────────────

def format_alert_message(
    ticker: str,
    mode: str,               # "swing" | "spike" | "candle"
    entry: float,
    stop: StopState,
    regime: str    = "BULL", # "BULL" | "BEAR"
    ml_prob: float = None,
    vol_ratio: float = 1.0,
    size_pct: float  = 0.0,
    spike_pct: float = 0.0,
) -> str:
    """
    Einheitlicher Alert-Formatter für Swing / Spike / Candle.
    Gibt leeren String zurück bei Fehler (Fallback im Caller).
    """
    try:
        rate      = _get_eurusd()
        stop_dist = entry - stop.stop_price
        if stop_dist <= 0:
            log.warning(f"format_alert_message: stop_dist <= 0 für {ticker} "
                        f"(entry={entry}, stop={stop.stop_price}) — übersprungen")
            return ""

        stop_pct  = round((stop.stop_price - entry) / entry * 100, 1)  # negativ
        targets   = compute_targets(entry, mode, stop)
        r_ratio   = round((targets["target1"] - entry) / stop_dist, 1)
        conf_str  = f"{ml_prob:.1f}" if ml_prob is not None else "n/a"

        def e(p: float) -> str:
            return _fmt_eur(p, ticker, rate)

        # ── SWING ────────────────────────────────────────────────
        if mode == "swing":
            t1     = targets["target1"]
            t1_pct = targets["target1_pct"]
            t2     = targets["target2"]
            t2_pct = targets["target2_pct"]
            return (
                f"🚨 SWING | {ticker} | {regime}\n\n"
                f"💶 Einstieg:  {e(entry)}\n"
                f"🛡️ Stop:      {e(stop.stop_price)}  ({stop_pct}%)\n"
                f"🎯 Ziel 1:    {e(t1)}    (+{t1_pct}%) → 50% verkaufen\n"
                f"🎯 Ziel 2:    {e(t2)}    (+{t2_pct}%) → Rest laufen lassen\n\n"
                f"📊 Confidence: {conf_str}% | R-Ratio: {r_ratio:.1f}\n"
                f"📈 Vol: {vol_ratio:.1f}x | Position: {size_pct:.1f}%\n"
                f"⚙️ Stop-Regel: {stop.stop_rule}"
            )

        # ── SPIKE / CANDLE ────────────────────────────────────────
        elif mode in ("spike", "candle"):
            t1     = targets["target1"]
            t1_pct = targets["target1_pct"]
            label  = "SPIKE" if mode == "spike" else "CANDLE"

            if regime == "BEAR":
                return (
                    f"👁️ WATCHLIST | {ticker}\n\n"
                    f"Intraday-Spike im bearischen Markt — beobachten\n"
                    f"📈 +{spike_pct:.1f}% | Vol: {vol_ratio:.1f}x\n"
                    f"💶 Kurs: {e(entry)} | Stop wenn Long: {e(stop.stop_price)} | Ziel: {e(t1)}"
                )
            return (
                f"👀 {label} | {ticker} | {regime}\n\n"
                f"💶 Einstieg:  {e(entry)}\n"
                f"🛡️ Stop:      {e(stop.stop_price)}  ({stop_pct}%)\n"
                f"🎯 Ziel:      {e(t1)}    (+{t1_pct}%) → Vollständig verkaufen\n\n"
                f"⚡ Spike: +{spike_pct:.1f}% | Vol: {vol_ratio:.1f}x\n"
                f"📊 Position: {size_pct:.1f}%"
            )

    except Exception as exc:
        log.warning(f"format_alert_message Fehler ({ticker}, {mode}): {exc}")

    return ""

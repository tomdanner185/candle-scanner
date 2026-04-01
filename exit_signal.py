"""
exit_signal.py  —  Automatisches Verkaufssignal-Modul
Panzer Bot / Candle Scanner — gemeinsam nutzbar

Drei Verkaufssignale (empirisch priorisiert):
  S1  VWAP-Unterschreitung auf Schlusskursbasis        [Maróy 2025: Sharpe >3.0]
  S2  Volumen-Kollaps <0.5× Durchschnitt               [Zarattini 2024]
  S3  Zeitlimit 13:30 ET (spätester Exit)              [Xu & Zhu 2022]

Bonus-Exit (Gewinn sichern):
  S4  TP1 erreicht → SL auf Break-Even ziehen
  S5  Bearish/Bullish Reversal-Muster nach Entry       [Doss 2008]

USAGE:
  from exit_signal import ExitMonitor, ExitSignal
  monitor = ExitMonitor(entry_price=186.30, direction='LONG',
                        stop_loss=184.10, take_profit1=189.50,
                        take_profit2=193.00)
  signal = monitor.check(current_price, vwap, current_vol, avg_vol, et_hour, et_min)
  if signal.should_exit:
      send_telegram(signal.alert_text)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    should_exit: bool
    reason: str          # Kurzcode: VWAP / VOL / TIME / TP1 / TP2 / SL / REVERSAL
    detail: str          # Lesbare Begründung
    urgency: str         # SOFORT / WARNUNG / INFO
    pnl_pct: float       # aktueller P&L in %
    alert_text: str      # fertiger Telegram-Text


class ExitMonitor:
    """
    Überwacht eine offene Position und gibt Exit-Signale aus.

    Initialisierung beim Entry-Alert, dann jeden Scan-Zyklus
    check() aufrufen.
    """

    def __init__(self,
                 entry_price: float,
                 direction: str,        # "LONG" | "SHORT"
                 stop_loss: float,
                 take_profit1: float,
                 take_profit2: float    = 0.0,
                 ticker: str            = "",
                 entry_time: str        = ""):
        self.entry      = entry_price
        self.direction  = direction
        self.sl         = stop_loss
        self.tp1        = take_profit1
        self.tp2        = take_profit2
        self.ticker     = ticker
        self.entry_time = entry_time
        self.tp1_hit    = False         # True nach TP1-Erreichen → SL auf BE
        self.sl_adjusted = False

    def check(self,
              price:       float,
              vwap:        float,
              vol_current: float,       # aktuelles 5-Min Volumen
              vol_avg:     float,       # 14d Durchschnitt pro 5-Min-Slot
              et_hour:     int,
              et_min:      int) -> ExitSignal:
        """
        Prüft alle Exit-Bedingungen.
        Gibt das dringlichste Signal zurück.
        """
        signals = []

        pnl = self._pnl(price)

        # ── S3: Zeitlimit (immer prüfen — hartes Gate) ────────
        mins = et_hour * 60 + et_min
        if mins >= 13 * 60 + 30:
            signals.append(("TIME", "SOFORT",
                            f"Zeitlimit 13:30 ET — spätester Exit erreicht",
                            pnl))

        # ── S1: VWAP-Unterschreitung ──────────────────────────
        if vwap > 0:
            if self.direction == "LONG" and price < vwap:
                margin = (vwap - price) / vwap * 100
                urg = "SOFORT" if margin > 0.3 else "WARNUNG"
                signals.append(("VWAP", urg,
                                f"Kurs ${price:.2f} unter VWAP ${vwap:.2f} "
                                f"({margin:.2f}% unter) — Long-Basis entfallen",
                                pnl))
            elif self.direction == "SHORT" and price > vwap:
                margin = (price - vwap) / vwap * 100
                urg = "SOFORT" if margin > 0.3 else "WARNUNG"
                signals.append(("VWAP", urg,
                                f"Kurs ${price:.2f} über VWAP ${vwap:.2f} "
                                f"({margin:.2f}% über) — Short-Basis entfallen",
                                pnl))

        # ── S2: Volumen-Kollaps ───────────────────────────────
        if vol_current > 0 and vol_avg > 0:
            vol_ratio = vol_current / vol_avg
            if vol_ratio < 0.5:
                signals.append(("VOL", "WARNUNG",
                                f"Volumen kollabiert: {vol_ratio:.2f}× "
                                f"(Momentum erschöpft)",
                                pnl))

        # ── SL: Stop-Loss ─────────────────────────────────────
        active_sl = self._active_sl()
        if self.direction == "LONG" and price <= active_sl:
            signals.append(("SL", "SOFORT",
                            f"Stop-Loss ${active_sl:.2f} getriggert "
                            f"({'BE' if self.sl_adjusted else 'original'})",
                            pnl))
        elif self.direction == "SHORT" and price >= active_sl:
            signals.append(("SL", "SOFORT",
                            f"Stop-Loss ${active_sl:.2f} getriggert "
                            f"({'BE' if self.sl_adjusted else 'original'})",
                            pnl))

        # ── TP2: Take-Profit 2 ────────────────────────────────
        if self.tp2 > 0:
            if self.direction == "LONG" and price >= self.tp2:
                signals.append(("TP2", "SOFORT",
                                f"TP2 ${self.tp2:.2f} erreicht — voll aussteigen",
                                pnl))
            elif self.direction == "SHORT" and price <= self.tp2:
                signals.append(("TP2", "SOFORT",
                                f"TP2 ${self.tp2:.2f} erreicht — voll aussteigen",
                                pnl))

        # ── TP1: Take-Profit 1 → SL auf Break-Even ───────────
        if not self.tp1_hit:
            tp1_hit = (self.direction == "LONG"  and price >= self.tp1) or \
                      (self.direction == "SHORT" and price <= self.tp1)
            if tp1_hit:
                self.tp1_hit     = True
                self.sl          = self.entry   # BE
                self.sl_adjusted = True
                signals.append(("TP1", "INFO",
                                f"TP1 ${self.tp1:.2f} erreicht — "
                                f"Hälfte verkaufen, SL auf Break-Even ${self.entry:.2f}",
                                pnl))

        # ── Stärkstes Signal auswählen ────────────────────────
        if not signals:
            return ExitSignal(
                should_exit=False,
                reason="HOLD", detail="Alle Bedingungen grün",
                urgency="INFO", pnl_pct=pnl,
                alert_text=""
            )

        priority = {"SOFORT": 0, "WARNUNG": 1, "INFO": 2}
        signals.sort(key=lambda x: priority.get(x[1], 9))
        best = signals[0]
        reason, urgency, detail, _ = best

        should_exit = urgency in ("SOFORT",) or reason in ("SL", "TP2", "TIME")
        alert = self._build_alert(reason, urgency, detail, price, pnl)

        return ExitSignal(
            should_exit=should_exit,
            reason=reason,
            detail=detail,
            urgency=urgency,
            pnl_pct=pnl,
            alert_text=alert
        )

    def _pnl(self, price: float) -> float:
        if not self.entry: return 0.0
        if self.direction == "LONG":
            return round((price - self.entry) / self.entry * 100, 2)
        else:
            return round((self.entry - price) / self.entry * 100, 2)

    def _active_sl(self) -> float:
        return self.sl

    def _build_alert(self, reason: str, urgency: str,
                     detail: str, price: float, pnl: float) -> str:
        icons = {"SOFORT": "🔴", "WARNUNG": "🟡", "INFO": "🔵"}
        reason_icons = {
            "SL": "🛑", "TP1": "🎯", "TP2": "🎯",
            "VWAP": "📉", "VOL": "📊", "TIME": "⏰", "REVERSAL": "🔄"
        }
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        dir_sym = "▲ LONG" if self.direction == "LONG" else "▼ SHORT"

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"{icons.get(urgency,'⚪')} VERKAUFSSIGNAL — {urgency}",
            f"{reason_icons.get(reason,'•')} {self.ticker}  |  {dir_sym}",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💵 Kurs:   ${price:.2f}",
            f"📊 Entry:  ${self.entry:.2f}",
            f"{pnl_icon} P&L:    {pnl:+.2f}%",
            f"",
            f"📋 Grund: {detail}",
        ]

        if self.tp1_hit and reason not in ("TP2", "SL"):
            lines.append(f"ℹ️  TP1 bereits erreicht — SL auf BE ${self.entry:.2f}")

        lines += [
            f"",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🔬 Maróy(2025)·Zarattini(2024)·Xu&Zhu(2022)",
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  POSITIONS-TRACKER  (in-memory + SQLite)
#  Verwaltet alle offenen Positionen und prüft Exit-Signale
# ═══════════════════════════════════════════════════════════════
class PositionTracker:
    """
    Verwaltet offene Positionen aus signals.db/candle_signals.db.
    Wird vom Candle Scanner nach jedem Scan-Lauf aufgerufen.
    """

    def __init__(self, db_path: str = "signals.db"):
        self.db_path  = db_path
        self.monitors = {}   # ticker → ExitMonitor
        self._init_db()

    def _init_db(self):
        import sqlite3
        con = sqlite3.connect(self.db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS open_positions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_entry     TEXT,
                ticker       TEXT,
                direction    TEXT,
                entry_price  REAL,
                stop_loss    REAL,
                take_profit1 REAL,
                take_profit2 REAL,
                score        INTEGER,
                pattern      TEXT,
                status       TEXT DEFAULT 'OPEN',
                ts_exit      TEXT,
                exit_reason  TEXT,
                pnl_pct      REAL
            )
        """)
        con.commit()
        con.close()

    def open_position(self, result) -> int:
        """
        Öffnet neue Position nach Entry-Alert.
        result = CandleResult oder ScanResult
        """
        import sqlite3
        d = getattr(result, 'data', None)
        if not d: return -1

        # SL = OR-Tief (Long) oder OR-Hoch (Short)
        if result.direction == "LONG":
            sl  = d.or_low  if d.or_low  else d.price * 0.98
            tp1 = d.or_high + (d.or_high - d.or_low) * 1.5 if d.or_high else d.price * 1.03
            tp2 = d.or_high + (d.or_high - d.or_low) * 3.0 if d.or_high else d.price * 1.06
        else:
            sl  = d.or_high if d.or_high else d.price * 1.02
            tp1 = d.or_low  - (d.or_high - d.or_low) * 1.5 if d.or_low  else d.price * 0.97
            tp2 = d.or_low  - (d.or_high - d.or_low) * 3.0 if d.or_low  else d.price * 0.94

        con = sqlite3.connect(self.db_path)
        cur = con.execute("""
            INSERT INTO open_positions
            (ts_entry, ticker, direction, entry_price, stop_loss,
             take_profit1, take_profit2, score, pattern)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now().isoformat(),
            result.ticker, result.direction, d.price,
            sl, tp1, tp2, result.score,
            result.pattern.name if result.pattern else None
        ))
        pos_id = cur.lastrowid
        con.commit()
        con.close()

        monitor = ExitMonitor(
            entry_price=d.price, direction=result.direction,
            stop_loss=sl, take_profit1=tp1, take_profit2=tp2,
            ticker=result.ticker
        )
        self.monitors[result.ticker] = monitor
        log.info(f"Position geöffnet: {result.ticker} {result.direction} "
                 f"@ ${d.price:.2f} | SL=${sl:.2f} TP1=${tp1:.2f}")
        return pos_id

    def check_exits(self, market_data: dict) -> list:
        """
        Prüft alle offenen Positionen auf Exit-Signale.
        market_data = {ticker: {'price': x, 'vwap': y, 'vol': z, ...}}
        Gibt Liste von ExitSignal zurück.
        """
        exit_signals = []
        et = _et_now()
        et_h, et_m = et.hour, et.minute

        for ticker, monitor in list(self.monitors.items()):
            if ticker not in market_data:
                continue
            md = market_data[ticker]
            sig = monitor.check(
                price       = md.get('price', 0),
                vwap        = md.get('vwap', 0),
                vol_current = md.get('vol_current', 0),
                vol_avg     = md.get('vol_avg', 0),
                et_hour     = et_h,
                et_min      = et_m,
            )
            if sig.should_exit:
                self._close_position(ticker, sig)
                del self.monitors[ticker]
                exit_signals.append(sig)
                log.info(f"EXIT: {ticker} | {sig.reason} | P&L {sig.pnl_pct:+.2f}%")

        return exit_signals

    def _close_position(self, ticker: str, sig: ExitSignal):
        import sqlite3
        con = sqlite3.connect(self.db_path)
        con.execute("""
            UPDATE open_positions
            SET status='CLOSED', ts_exit=?, exit_reason=?, pnl_pct=?
            WHERE ticker=? AND status='OPEN'
        """, (datetime.now().isoformat(), sig.reason, sig.pnl_pct, ticker))
        con.commit()
        con.close()

    def load_open_positions(self):
        """Lädt offene Positionen aus DB nach Container-Neustart."""
        import sqlite3
        con = sqlite3.connect(self.db_path)
        rows = con.execute("""
            SELECT ticker, direction, entry_price, stop_loss,
                   take_profit1, take_profit2
            FROM open_positions WHERE status='OPEN'
        """).fetchall()
        con.close()
        for row in rows:
            ticker, direction, entry, sl, tp1, tp2 = row
            self.monitors[ticker] = ExitMonitor(
                entry_price=entry, direction=direction,
                stop_loss=sl, take_profit1=tp1,
                take_profit2=tp2 or 0, ticker=ticker
            )
        if rows:
            log.info(f"PositionTracker: {len(rows)} offene Positionen geladen")


def _et_now():
    return datetime.now(timezone(timedelta(hours=-4)))

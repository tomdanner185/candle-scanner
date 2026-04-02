import asyncio
import logging
import config
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from candlestick_scanner import run_candle_scan

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)


async def candle_job():
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, run_candle_scan)
    except Exception as e:
        log.error(f'Candle Job Fehler: {e}')


async def main():
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    # 10:30 ET = 14:30 UTC (Sommer/EDT) — VWAP+EMA nach 60 Min stabil (Fix M3/M4)
    # Scheduler läuft in Europe/Berlin — ET+6h Sommer, ET+5h Winter
    # Verschoben von 09:45->10:30 ET: EMA20 braucht ~12 Bars, VWAP 60 Min Handelszeit
    scheduler.add_job(
        candle_job, 'cron',
        hour=16, minute=30,  # 16:30 CEST = 14:30 UTC = 10:30 ET — VWAP+EMA stabil nach 60 Min
        id='candle_scan',
        name='Candlestick Scanner Modell 3',
        replace_existing=True,
        misfire_grace_time=300,
    )

    # ── Pre-Market Scanner (täglich 08:00 ET = 14:00 CEST) ───────
    scheduler.add_job(
        lambda: __import__('premarket_scanner').run_premarket_scan(),
        'cron',
        hour=14, minute=0,
        id='premarket_scan',
        name='Pre-Market Scanner',
        replace_existing=True,
        misfire_grace_time=300,
    )

    # ── Exit-Monitor (alle 5 Min während Handelszeit) ─────────────
    import yfinance as yf
    from datetime import datetime, timezone, timedelta
    from exit_signal import PositionTracker
    _tracker = PositionTracker(db_path=getattr(config, 'DB_PATH', '/app/data/candle_signals.db'))
    _tracker.load_open_positions()

    def exit_monitor_job():
        et = datetime.now(timezone(timedelta(hours=-4)))
        mins = et.hour * 60 + et.minute
        if not (9*60+30 <= mins <= 13*60+30):
            return
        if not _tracker.monitors:
            return
        market_data = {}
        for ticker in list(_tracker.monitors.keys()):
            try:
                df = yf.Ticker(ticker).history(period='1d', interval='5m', prepost=False)
                if df.empty:
                    continue
                tp = (df['High'] + df['Low'] + df['Close']) / 3
                vwap = float((tp * df['Volume']).sum() / df['Volume'].sum())
                market_data[ticker] = {
                    'price':       float(df['Close'].iloc[-1]),
                    'vwap':        vwap,
                    'vol_current': float(df['Volume'].iloc[-1]),
                    'vol_avg':     float(df['Volume'].mean()),
                }
            except Exception:
                pass
        from candlestick_scanner import _send_telegram
        for sig in _tracker.check_exits(market_data):
            _send_telegram(sig.alert_text)

    scheduler.add_job(
        exit_monitor_job,
        'interval',
        minutes=5,
        id='exit_monitor',
        name='Exit Monitor',
        replace_existing=True,
    )


    # ── Outcome-Tracker täglich 16:15 ET (20:15 UTC = 22:15 CEST) ─
    from outcome_tracker import run_outcome_update, migrate_db
    migrate_db()
    scheduler.add_job(run_outcome_update, 'cron',
        hour=22, minute=15,
        id='outcome_tracker', name='Outcome Tracker P40b',
        replace_existing=True, misfire_grace_time=300)

    scheduler.start()
    log.info('Candle Scanner gestartet — Job: täglich 16:30 CEST (10:30 ET)')
    while True:
        await asyncio.sleep(3600)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info('Candle Scanner gestoppt.')

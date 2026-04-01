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
    # 09:45 ET = 15:45 CEST (Sommer) / 14:45 CET (Winter)
    # Scheduler läuft in Europe/Berlin — ET+6h Sommer, ET+5h Winter
    # Wir nutzen UTC-basierte ET-Zeit via _et_now() in candlestick_scanner
    scheduler.add_job(
        candle_job, 'cron',
        hour=15, minute=45,
        id='candle_scan',
        name='Candlestick Scanner Modell 3',
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    log.info('Candle Scanner gestartet — Job: täglich 15:45 CEST (09:45 ET)')
    while True:
        await asyncio.sleep(3600)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info('Candle Scanner gestoppt.')

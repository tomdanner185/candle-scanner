import os
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

DATA_DIR = os.getenv('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH  = os.getenv('DB_PATH', f'{DATA_DIR}/candle_signals.db')

CANDLE_MIN_SCORE       = int(os.getenv('CANDLE_MIN_SCORE',       '70'))
CANDLE_OR_MINUTES      = int(os.getenv('CANDLE_OR_MINUTES',      '15'))
CANDLE_UNIVERSE        = os.getenv('CANDLE_UNIVERSE',            None)
CANDLE_GAP_MIN_PCT     = float(os.getenv('CANDLE_GAP_MIN_PCT',  '5.0'))
CANDLE_GAP_MAX_TICKERS = int(os.getenv('CANDLE_GAP_MAX_TICKERS','30'))
MIN_AVG_VOLUME         = int(os.getenv('MIN_AVG_VOLUME',        '500000'))
TIMEZONE               = os.getenv('TIMEZONE', 'Europe/Berlin')

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
PREMARKET_MIN_SCORE   = int(os.getenv('PREMARKET_MIN_SCORE',   '60'))
PREMARKET_MIN_GAP_PCT = float(os.getenv('PREMARKET_MIN_GAP_PCT','3.0'))
EXIT_MONITOR_ENABLED  = os.getenv('EXIT_MONITOR_ENABLED', 'true').lower() == 'true'

# ── Signal Engine ───────────────────────────────────────────────────────
EURUSD_FALLBACK         = float(os.getenv('EURUSD_FALLBACK', '1.08'))

# ── Kelly-Sizing (identisch Panzer Bot P54) ─────────────────────────────
KELLY_MICRO  = float(os.getenv('KELLY_MICRO',  '0.0'))    # nicht handeln
KELLY_SMALL  = float(os.getenv('KELLY_SMALL',  '0.01'))   # 1% max
KELLY_MID    = float(os.getenv('KELLY_MID',    '0.02'))   # 2% max
KELLY_LARGE  = float(os.getenv('KELLY_LARGE',  '0.03'))   # 3% max
KELLY_MEGA   = float(os.getenv('KELLY_MEGA',   '0.025'))  # 2.5%
KELLY_BASE   = float(os.getenv('KELLY_BASE',   '0.02'))   # Default

# Regime-Multiplikatoren
REGIME_MULT_BULL   = 1.0
REGIME_MULT_YELLOW = 0.5
REGIME_MULT_BEAR   = 0.0
REGIME_MULT_PANIC  = 0.0

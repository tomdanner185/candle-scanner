FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends     gcc g++ &&     rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY candlestick_scanner.py .
COPY config.py .
COPY exit_signal.py .
COPY finnhub_feed.py .
COPY main.py .
COPY options_flow.py .
COPY outcome_tracker.py .
COPY telegram_bot.py .
COPY premarket_scanner.py .
COPY regime.py .

ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Berlin

CMD ["python3", "-u", "main.py"]

# MASTER SCANNER SPEC — Endgültiges Modell-Framework
Version: 1.0 | Stand: 2026-04-11

## Übersicht

Der Panzer Scanner besteht aus 3 Modellen:

### Modell 1 — Swing Scanner
- **Zweck**: Tägliche Momentum-Signale für Swing-Trades (2–10 Tage Haltedauer)
- **Universe**: 2189 Ticker (EU 532 + US 1657)
- **Scan-Zeiten**:
  - EU Morning: 09:15 (nach XETRA-Open)
  - US Morning: 15:45 (nach NYSE-Open)
  - EU Close: 17:45 (vollständige Tageskerze)
  - US Close: 22:15 (vollständige Tageskerze)
- **Scoring**: 100-Punkte-System (ML-aligned Gewichte)
  - EMA200 Distance: 20 Punkte
  - EMA50 Distance: 12 Punkte
  - MACD Histogram: 14 Punkte
  - BB Width/Squeeze: 13 Punkte
  - RS Rating, RSI, Volume: Rest
- **MIN_SCORE**: 60 Punkte
- **ML-Modell**: VotingClassifier (RF+GB), WF-AUC > 0.55 für Aktivierung
- **Regime Gate**: Scan nur wenn SPY > EMA200 (GREEN)

### Modell 2 — Spike Detector
- **Zweck**: Intraday-Spikes erkennen (≥3% in 60 Min + ≥2.5x Volume)
- **Frequenz**: Alle 5 Minuten
- **Sessions**: EU 07:00–15:30 UTC | US 13:30–20:00 UTC
- **Regime Gate**: Abbruch nur bei US PANIC (nicht bei BEAR)
- **ML-Modell**: Spike-ML, WF-AUC > 0.58 für Aktivierung (aktuell: 0.48, nicht aktiv)
- **Outcome**: ≥3% max high in 2h = Win

### Modell 3 — Candle Scanner
- **Zweck**: Candlestick-Pattern-Erkennung
- **Separater Container**: candle_scanner

## Regime-System (Daniel & Moskowitz 2016)

```
TREND   → pos_mult=1.0  | Voller Scan
CAUTION → pos_mult=0.75 | Reduzierte Positionen
BEAR    → pos_mult=0.3  | Nur Watchlist
PANIC   → pos_mult=0.0  | Spike Scanner stoppt
```

Regime wird bestimmt durch:
- SPY vs EMA200 (Bull/Bear)
- VIX-Level (Nervosität)
- 20d Performance

## Datenbank-Struktur

| DB | Pfad | Inhalt |
|----|------|--------|
| signals.db | /app/data/signals.db | Swing-Signale, Scan-Runs, Paper Trades, Watchlist |
| spikes.db | /app/data/spikes.db | Spike-Alerts, Outcomes, Historical Spikes |
| price_cache.db | /app/data/price_cache.db | yfinance Download-Cache |

## Kritische Invarianten

1. **SPIKE_DB** zeigt immer auf `/app/data/spikes.db` (NICHT `/app/spikes.db`)
2. **droplevel(0)** für yfinance MultiIndex (Level 0 = Ticker, Level 1 = Price)
3. **Volumen-Filter** `vol/vol_sma < 0.8` in passes_filters() — kann aggressiv sein bei niedrigem Marktvolumen
4. **EU_SPIKE_ACTIVE** muss `True` sein für EU-Spike-Scan
5. **MIN_SAMPLES** für Spike-ML: 200 (gesenkt von 500 für früheres Feedback)
6. **Cross-Dedup**: 4h Fenster verhindert Doppel-Alerts zwischen Modell 1 und 2

## Scheduler (main.py)

| Job | Zeit | Funktion | ID |
|-----|------|----------|----|
| EU Morning | 09:15 | scheduled_job_eu() | scan_eu_morning |
| US Morning | 15:45 | scheduled_job_us() | scan_us_morning |
| EU Close | 17:45 | scheduled_job_eu() | scan_eu |
| US Close | 22:15 | scheduled_job_us() | scan_us |
| Spike | alle 5 Min | spike_job() | spike |
| Health | 07:00 | health_report_job() | health |
| Spike Report | 22:00 | daily_spike_report_job() | spike_report |
| Backup | 03:00 | backup_job() | backup |
| Retrain | So 02:00 | retrain_job() | retrain |

## Monitoring (health_monitor.py)

Täglicher Report um 07:00 mit:
- Bot-Status + Drawdown-Guard
- Regime + VIX
- Letzte Scans + Pipeline-Stats
- ML-Modell Alter + AUC
- Spike Detector Stats + Winrate
- Candle Scanner Status
- Disk Alert (>85%)
- Dead Scanner Check
- Paper Trading Performance
- yfinance Download-Health
- Server-Ressourcen
- Backup-Status
- Fail2ban Security

## Deployment

- Server: root@49.13.157.1 (Hetzner, 38GB Disk, 79% belegt)
- Container: momentum_scanner (Docker Compose)
- Repo: github.com/tomdanner185/panzer-bot
- CI/CD: GitHub Actions → SSH Deploy + Smoketest
- Bot-User: panzer (non-root, Docker-Zugriff)

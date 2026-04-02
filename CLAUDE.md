# Candle Scanner — CLAUDE.md
Persistenter Kontext für Claude Code. Stand: 02.04.2026.

## Projekt-Übersicht
Separates Intraday Pattern-Discovery-System neben dem Panzer Bot.
Container: `candle_scanner` | Verzeichnis: `/root/candle_scanner/`
GitHub: github.com/tomdanner185/candle-scanner (privat)
**Vollständig unabhängig vom Panzer Bot — kein gemeinsamer Code.**

## Architektur

### Candle Scanner (Modell 3)
- Läuft täglich 15:45 CEST (09:45 ET)
- Universe: yf.screen('most_actives') → Gapper ≥5% + statisches CANDLE_UNIVERSE
- Score-Engine: CandleRecognizer + ScoreEngine — klassische Muster + Kontext
- Alert-Schwelle: Score ≥70/100 → Telegram | ≥60 → Batch-Summary

### Pre-Market Scanner
- Läuft 14:00 CEST (08:00 ET) — noch nicht im Scheduler aktiv
- 3 Signalquellen: Gap-Scan + Catalyst (FDA/Earnings/M&A) + Volume-Surge
- Aktivierung: nach erstem stabilen Candle-Scan Lauf

### Regime-Gate (standalone — regime.py)
# BEWUSSTES DESIGN: Candle Scanner nutzt SPY<EMA50 (kurzfristig konservativ)
# Panzer Bot nutzt 24M-Return (langfristiger). Divergenz ist gewollt.
- SPY EMA50 Check + VIX Check
- PANIC → Scan abgebrochen
- Zeitfenster-Gate: nur 09:00–13:30 ET

## ⚠️ KRITISCHE REGELN

### Wissenschaftliche Basis
- Isolierte Candlestick-Muster: KEINE signifikante Vorhersagekraft
  (Marshall/Young/Rose 2006, Duvinage et al. 2013)
- Score-Engine kombiniert Muster + Volumen + Zeitfenster + Regime
- Nächster Schritt (C3): LightGBM auf 50+ Features nach 30 Tagen Daten

### Morning vs Afternoon (P57 — pending)
- Erste 30-60 Min nach Open (09:30–10:30 ET) = Informationsphase → stärkeres Signal
- Afternoon (nach 13:00 ET) = oft Reversal → schwächer gewichten
- Im Score gewichten sobald genug Daten vorhanden

### yfinance Handling
- yf.screen('most_actives'): experimentell — immer try/except mit Fallback
- Fallback: statisches CANDLE_UNIVERSE wenn Screen fehlschlägt
- Keine Rate-Limit-Absicherung wie im Panzer Bot — Universe ist klein genug

### Unabhängigkeit
- KEIN Import aus /root/panzer/
- Regime-Check via regime.py (standalone) — nicht momentum_expert
- Eigene SQLite DB: /root/candle_scanner/data/

## Wichtige Dateien
```
/root/candle_scanner/
  candlestick_scanner.py   — Hauptlogik: Erkennung + Score + Alert
  regime.py                — Standalone Regime-Gate (SPY EMA50 + VIX)
  premarket_scanner.py     — Pre-Market Scan (noch nicht im Scheduler)
  config.py                — CANDLE_* + TELEGRAM_* Einstellungen
  main.py                  — APScheduler (15:45 CEST)
  data/                    — eigene SQLite DB (.gitignored)
```

## Deploy-Workflow
```bash
cd /root/candle_scanner
git add -A
git commit -m 'feat/fix: Beschreibung'
git push
docker build -t candle_scanner . && docker stop candle_scanner && \
docker rm candle_scanner && \
docker run -d --name candle_scanner \
  --env-file /root/candle_scanner/.env \
  -v /root/candle_scanner/data:/app/data \
  candle_scanner
docker logs candle_scanner --since 2m | tail -10
```

## Backlog Candle Scanner
- **C1**: Erster Lauf heute 15:45 auswerten — kein Alert erhalten, Log prüfen
- **C2**: Tägliche Ergebnisse in SQLite speichern (nach 7 Tagen)
- **P57**: Morning/Afternoon Gewichtung im Score implementieren
- **Pre-Market**: Scheduler-Eintrag aktivieren (nach stabilem C1)
- **C3**: LightGBM auf 50+ Features (nach 30 Tagen Daten)

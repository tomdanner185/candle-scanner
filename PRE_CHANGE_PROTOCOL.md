# PRE-CHANGE PROTOCOL — Pflichtchecks vor jeder Änderung
Version: 1.0 | Stand: 2026-04-11

## 5 Pflichtfragen vor jedem Code-Change

Bevor Code an momentum_expert.py, config.py, run_scan.py, main.py,
spike_detector.py oder health_monitor.py geändert wird, MÜSSEN diese
5 Fragen beantwortet werden:

### 1. Widerspricht die Änderung einem bestehenden Fix?
Prüfe die Widerspruchs-Registry unten. Wenn die Änderung einen
dokumentierten Fix rückgängig macht → STOPP.

### 2. Wurde der aktuelle Zustand gelesen?
Vor jeder Änderung: `cat` oder `grep` der betroffenen Datei auf dem Server.
Nie blind ändern basierend auf Annahmen.

### 3. Gibt es einen Smoketest?
Jede Änderung muss nach Deploy verifiziert werden:
```bash
docker exec momentum_scanner python3 -c "from run_scan import get_universe; u=get_universe(); assert len(u)>1800"
```

### 4. Ist die Syntax geprüft?
```bash
python3 -c "import ast; ast.parse(open('datei.py').read())"
```

### 5. Ist der Container neu gebaut?
Änderungen an Host-Dateien erfordern `docker compose down && docker compose up --build -d`.

---

## Widerspruchs-Registry

Dokumentierte Fixes die NICHT rückgängig gemacht werden dürfen:

### W-001: droplevel(0) statt droplevel(1)
- **Datei**: spike_detector.py (Zeilen 129, 185, 270)
- **Fix-Datum**: 2026-04-11
- **Problem**: yfinance MultiIndex hat (Ticker, Price) — Level 0 muss gedroppt werden
- **Symptom wenn kaputt**: `_check_ticker()` gibt silent `None` zurück, 0 Spikes
- **Commit**: b08385c

### W-002: SPIKE_DB Pfad = /app/data/spikes.db
- **Datei**: config.py
- **Fix-Datum**: 2026-04-11
- **Problem**: Zwei spikes.db existierten — /app/spikes.db (leer) vs /app/data/spikes.db (185 Spikes)
- **Config zeigt korrekt auf**: `f"{DATA_DIR}/spikes.db"` = `/app/data/spikes.db`

### W-003: EU_SPIKE_ACTIVE = True
- **Datei**: config.py
- **Fix-Datum**: 2026-04-11
- **Problem**: Wenn False, werden EU-Ticker im Spike-Scanner übersprungen

### W-004: MIN_SAMPLES = 200 (nicht 500)
- **Datei**: spike_ml_trainer.py (Zeile 27)
- **Fix-Datum**: 2026-04-11
- **Problem**: Bei 500 dauert es zu lange bis ML-Training starten kann
- **Achtung**: Nicht `200+` oder `200 —` schreiben (SyntaxError durch Sonderzeichen)

### W-005: Scan-Zeiten nach Börsenschluss
- **Datei**: config.py, main.py
- **Fix-Datum**: 2026-04-11
- **Werte**: EU Close 17:45, US Close 22:15, EU Morning 09:15, US Morning 15:45
- **Problem vorher**: Scan um 08:30 (vor EU-Open) und 14:30 (US Intraday) lieferte unvollständige Daten

### W-006: Volumen-Filter vol/vol_sma < 0.8
- **Datei**: run_scan.py, passes_filters()
- **Status**: BEKANNTES PROBLEM — filtert 70% der Top-Ticker bei niedrigem Marktvolumen
- **Entscheidung**: Noch nicht geändert, beobachten

### W-007: market_calendar Feiertage
- **Datei**: market_calendar.py
- **Problem**: Feiertage sind für 2026 hardcodiert — muss jährlich aktualisiert werden

### W-008: Cross-Dedup 4h Fenster
- **Datei**: cross_dedup.py
- **Fix-Datum**: 2026-04-11
- **Logik**: Prüft signals.db + spikes.db — verhindert Doppel-Alerts

---

## Checkliste für neue Widersprüche

Wenn ein neuer Fix gemacht wird:
1. Nummer vergeben: W-XXX
2. Datei + Zeile dokumentieren
3. Problem + Symptom beschreiben
4. Commit-Hash eintragen
5. In diese Registry eintragen

---

## Bekannte Einschränkungen

- **Spike ML AUC = 0.48**: Modell nicht aktiviert, braucht bessere Intraday-Outcomes
- **Swing Scanner**: Nur 1 Signal seit Launch (VIE.PA) — Marktumfeld + Filter zu restriktiv
- **Disk**: 79% belegt, 7.6 GB frei — Backup-Rotation auf 7 Tage gesetzt
- **Nordics/Helsinki/Oslo**: Viele Ticker auf Yahoo Finance nicht abrufbar (delisted-Fehler)

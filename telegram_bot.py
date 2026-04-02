"""
telegram_bot.py — /report Befehl via Long-Polling
Läuft als separater Thread in main.py.
"""
import threading, time, json, urllib.request, logging
log = logging.getLogger(__name__)

def _tg_get(token, method, params=None):
    url = f'https://api.telegram.org/bot{token}/{method}'
    if params:
        url += '?' + '&'.join(f'{k}={v}' for k,v in params.items())
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log.debug(f'TG API Fehler: {e}')
        return {}

def _tg_post(token, chat_id, text):
    data = json.dumps({'chat_id': chat_id, 'text': text}).encode()
    req  = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10): pass
    except Exception as e:
        log.debug(f'TG Send Fehler: {e}')

def start_bot(token, chat_id):
    """Startet Long-Polling in Background-Thread."""
    def poll():
        offset = 0
        while True:
            try:
                res = _tg_get(token, 'getUpdates', {'offset': offset, 'timeout': 30})
                for upd in res.get('result', []):
                    offset = upd['update_id'] + 1
                    msg    = upd.get('message', {})
                    text   = msg.get('text', '').strip()
                    cid    = str(msg.get('chat', {}).get('id', ''))
                    if text == '/report' and cid == str(chat_id):
                        from outcome_tracker import print_report
                        report = print_report(send_telegram=False)
                        _tg_post(token, chat_id, report)
                        log.info('/report Befehl ausgefuehrt')
                    elif text == '/status' and cid == str(chat_id):
                        from regime import check_regime, _check_crash_kill_switch
                        ks = _check_crash_kill_switch()
                        rl = check_regime('LONG')
                        rs = check_regime('SHORT')
                        status = (
                            f'System-Status\n'
                            f'Kill Switch: {"AKTIV" if ks else "inaktiv"}\n'
                            f'LONG: {"erlaubt" if rl["allow"] else "geblockt"} — {rl["reason"]}\n'
                            f'SHORT: {"erlaubt" if rs["allow"] else "geblockt"} — {rs["reason"]}'
                        )
                        _tg_post(token, chat_id, status)
                        log.info('/status Befehl ausgefuehrt')
            except Exception as e:
                log.debug(f'Poll Fehler: {e}')
                time.sleep(5)
    t = threading.Thread(target=poll, daemon=True)
    t.start()
    log.info('Telegram Bot Polling gestartet (/report + /status)')

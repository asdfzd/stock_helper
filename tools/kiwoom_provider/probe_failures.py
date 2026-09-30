"""Small public-only REST diagnostic; no account/order endpoints or response dumps."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from market_radar.config import CONFIG
from market_radar.models import ET, instant
from market_radar.providers.kiwoom_calendar import ExchangeCalendar
from market_radar.providers.kiwoom_rest import APIS, BASE, KiwoomRest
from market_radar.providers.kiwoom_lock import CollectorLock


def main():
    now = datetime.now(timezone.utc)
    session = ExchangeCalendar().session(now.astimezone(ET).date().isoformat())
    if not session or not instant(session.open_utc) <= now < instant(session.close_utc):
        print('not_run_market_closed')
        return 2
    lock = CollectorLock()
    lock.acquire()
    rest = KiwoomRest(CONFIG, threading.Event())
    output = []
    try:
        token = rest.token()
        queries = [('usa10099', {'stex_tp': ex}) for ex in ('ND', 'NY', 'NA')]
        for ticker in ('FFR', 'NVDA'):
            queries += [('usa20100', {'stex_tp': 'ND', 'stk_cd': ticker}),
                        ('usa06011', rest.example('usa06011', stex_tp='ND', stk_cd=ticker,
                         strt_dt=session.date.replace('-', '')))]
        for tr, body in queries:
            rest.limiter.acquire(tr)
            row = {'ticker': body.get('stk_cd', 'FFR'), 'TR': tr, 'exchange': body['stex_tp']}
            try:
                response = rest.http.post(BASE+APIS[tr]['path'], json=body,
                    headers={'api-id': tr, 'authorization': 'Bearer '+token},
                    timeout=(5, 10), allow_redirects=False)
                payload = response.json()
                rows = payload.get('list', payload.get('result_list', []))
                row.update(http_status=response.status_code, return_code=payload.get('return_code'),
                    return_msg=rest.sanitize(str(payload.get('return_msg', '')))[:300], row_count=len(rows))
                if tr == 'usa10099':
                    row['public_type_fields'] = [{k: r.get(k) for k in
                        ('stk_cd', 'stex_tp', 'stk_nm', 'stk_enm', 'isEtf', 'mkgb', 'upgb')}
                        for r in rows if r.get('stk_cd') == 'FFR']
            except Exception as exc:
                row['result_status'] = type(exc).__name__
            output.append(row)
    finally:
        rest.close()
        lock.close()
    dest = ROOT/'data/kiwoom_v2_1_validation/public_failure_probe.json'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    raise SystemExit(main())

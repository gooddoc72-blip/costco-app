"""매일 18:00 자동 정산 (관리자 PC에서 실행) — CJ 발송 파일 → 코코비즈 발송건 · 영수증 · 매칭 초안.

  1) CJ 기업고객 사이트(LOIS)에서 '일별 배송상세' 엑셀을 받는다 — 저장된 로그인 프로필 사용
  2) 코코비즈 /api/admin/auto/dispatch 로 발송건 정리 업로드
  3) 날짜마다 영수증 폴더의 그날 영수증(MMDD*.pdf/jpg) → /receipt, 이어서 /match(초안 저장)
범위는 '마지막으로 성공한 날 다음 날 ~ 오늘'이다 — 주말·PC가 꺼졌던 날을 놓치지 않는다.
서버 쪽 처리 규칙은 auto_settle.py 머리말.

사용:
  python daily_settle.py --setup        처음 한 번: 코코비즈 sid 입력 + CJ 로그인(브라우저가 열린다)
  python daily_settle.py                예약 작업이 부르는 본 실행
  python daily_settle.py --date 2026-10-21 --cj-file 경로.xlsx   날짜·파일 지정 재실행
설정·로그: %LOCALAPPDATA%\\CocobizDaily\\
"""
import argparse
import base64
import glob
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

import requests

HOME = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'CocobizDaily')
CONF = os.path.join(HOME, 'config.json')
PROFILE = os.path.join(HOME, 'cj_profile')
DEFAULTS = {
    'server': 'https://cocobiz.shop',
    'sid': '',
    'cj_url': 'https://loisparcel.cjlogistics.com/index.do',
    'receipt_root': r'F:\1 코스트코\01 코스트코 구매대행\영수증',
    'cj_save_dir': r'F:\1 코스트코\01 코스트코 구매대행\발송내역',
    'last_done': '',          # 마지막으로 끝까지 처리한 날짜
    'max_days': 7,            # 한 번에 거슬러 올라갈 최대 일수
}
_LOG = []


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    _LOG.append(line)
    print(line, flush=True)


def load_conf():
    c = dict(DEFAULTS)
    try:
        with open(CONF, encoding='utf-8') as f:
            c.update(json.load(f))
    except (OSError, ValueError):
        pass
    return c


def save_conf(c):
    os.makedirs(HOME, exist_ok=True)
    with open(CONF, 'w', encoding='utf-8') as f:
        json.dump(c, f, ensure_ascii=False, indent=2)


def notify(title, body):
    """윈도우 알림(토스트). 실패해도 로그에는 남는다."""
    ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
          "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
          "$t=$x.GetElementsByTagName('text');$t.Item(0).AppendChild($x.CreateTextNode($env:T1))|Out-Null;"
          "$t.Item(1).AppendChild($x.CreateTextNode($env:T2))|Out-Null;"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('코코비즈 자동정산').Show("
          "[Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', ps], timeout=20,
                       env=dict(os.environ, T1=title, T2=body[:240]), capture_output=True)
    except Exception:
        pass


# ── 코코비즈 API ─────────────────────────────────────────────
class Api:
    def __init__(self, conf):
        self.c = conf

    def post(self, path, payload, timeout=600):
        r = requests.post(self.c['server'].rstrip('/') + path, json=payload, timeout=timeout,
                          headers={'Authorization': 'Bearer ' + self.c['sid']})
        if r.status_code == 401:
            raise RuntimeError('코코비즈 로그인 만료 — --setup으로 sid를 다시 넣으세요')
        if r.status_code == 404:
            raise RuntimeError(f'{path} 가 서버에 없습니다 — 서버 배포가 아직 안 됐습니다')
        r.raise_for_status()
        return r.json()

    def renew(self):
        """sid를 새로 받아 저장 — 매일 돌면 30일 만료에 걸리지 않는다."""
        self.c['sid'] = self.post('/api/admin/auto/renew', {})['sid']
        save_conf(self.c)


def _file(path):
    with open(path, 'rb') as f:
        return {'name': os.path.basename(path), 'content_b64': base64.b64encode(f.read()).decode()}


# ── CJ 다운로드 ───────────────────────────────────────────────
def cj_download(conf, d_from, d_to, headed=False):
    """LOIS에서 접수일자 d_from~d_to '일별 배송상세' 엑셀을 받아 저장 경로를 돌려준다."""
    from cj_lois import download_daily_detail
    os.makedirs(conf['cj_save_dir'], exist_ok=True)
    return download_daily_detail(conf['cj_url'], PROFILE, conf['cj_save_dir'],
                                 d_from, d_to, headed=headed, log=log)


# ── 영수증 ───────────────────────────────────────────────────
def receipt_files(root, d):
    """영수증\\{월}월\\MMDD*.pdf|jpg… (반품 폴더 제외). 같은 날 여러 장은 함께 보낸다."""
    folder = os.path.join(root, f'{d.month}월')
    out = []
    for p in glob.glob(os.path.join(folder, f'{d:%m%d}*')):
        if os.path.isfile(p) and os.path.splitext(p)[1].lower() in (
                '.pdf', '.jpg', '.jpeg', '.png', '.webp', '.heic'):
            out.append(p)
    return sorted(out)


def run(conf, days, cj_file=None, headed=False):
    api = Api(conf)
    api.renew()
    log(f'처리 범위: {days[0]} ~ {days[-1]}')
    path = cj_file or cj_download(conf, days[0], days[-1], headed=headed)
    log(f'CJ 파일: {path}')
    r = api.post('/api/admin/auto/dispatch', _file(path))
    if not r.get('ok'):
        raise RuntimeError('발송건 업로드 실패: ' + str(r.get('error')))
    saved = sum((r.get('saved') or {}).values())
    log(f"발송건: 날짜별 {r.get('dates')} · 새로 저장 {saved}건 · 이미 있음 {r.get('skipped')} · "
        f"날짜 이동 {r.get('moved')} · 외부업체 {r.get('external')} · 미분류 {r.get('unknown_n')}")
    summary = [f'발송 {saved}건 저장' + (f" · 미분류 {r['unknown_n']}" if r.get('unknown_n') else '')]
    review = []
    for d in days:
        files = receipt_files(conf['receipt_root'], d)
        if files:
            rr = api.post('/api/admin/auto/receipt',
                          {'date': str(d), 'files': [_file(p) for p in files]})
            st = rr.get('status')
            log(f"{d} 영수증 {', '.join(os.path.basename(p) for p in files)} → {st} "
                f"({rr.get('items', 0)}종" + (f", {rr.get('amount', 0):,}원" if st == 'saved' else '')
                + ')' + (' · ' + ' / '.join(rr.get('why') or []) if rr.get('why') else ''))
            if st == 'review':
                review.append(f'{d:%m/%d} 영수증 확인 필요')
        else:
            log(f'{d} 영수증 파일 없음')
        m = api.post('/api/admin/auto/match', {'date': str(d)})
        ms = m.get('status')
        if ms == 'saved':
            log(f"{d} 매칭 초안 {m['rows']}건 저장 · {m.get('amount', 0):,}원 · 발송 {m.get('ship_count')} · "
                f"AI {m.get('ai')} · 재고이월 {m.get('stock_carry')} · "
                f"미매칭 주문 {m.get('unmatched_orders')} / 영수증 {m.get('unmatched_receipt')}")
            summary.append(f"{d:%m/%d} 매칭 {m['rows']}건(미매칭 {m.get('unmatched_orders')})")
        else:
            log(f'{d} 매칭 건너뜀: ' + {'settled': '이미 정산됨', 'no_receipt': '저장된 영수증 없음',
                                        'no_dispatch': '발송 기록 없음'}.get(ms, str(ms)))
    return summary, review


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--setup', action='store_true')
    ap.add_argument('--date', help='이 날짜 하나만 처리 (YYYY-MM-DD)')
    ap.add_argument('--cj-file', help='CJ 다운로드 대신 이 파일을 올린다')
    ap.add_argument('--headed', action='store_true', help='CJ 브라우저를 화면에 띄운다')
    a = ap.parse_args()
    conf = load_conf()

    if a.setup:
        sid = input('코코비즈 관리자 sid (주소창 ?sid= 뒤 값, 비우면 유지): ').strip()
        if sid:
            conf['sid'] = sid.split('sid=')[-1].split('&')[0]
        save_conf(conf)
        from cj_lois import login_once
        login_once(conf['cj_url'], PROFILE)
        print('설정 완료:', CONF)
        return

    today = date.today()
    if a.date:
        days = [date.fromisoformat(a.date)]
    else:
        start = today
        if conf.get('last_done'):
            start = max(date.fromisoformat(conf['last_done']) + timedelta(days=1),
                        today - timedelta(days=int(conf['max_days']) - 1))
        days = [start + timedelta(days=i) for i in range((today - start).days + 1)]

    os.makedirs(os.path.join(HOME, 'logs'), exist_ok=True)
    try:
        summary, review = run(conf, days, cj_file=a.cj_file, headed=a.headed)
        if not a.date:
            conf = load_conf() | {'sid': conf['sid'], 'last_done': str(days[-1])}
            save_conf(conf)
        notify('코코비즈 자동정산 완료' + (' — 확인 필요' if review else ''),
               ' · '.join(review + summary))
        code = 0
    except Exception as e:
        log(f'실패: {e}')
        notify('코코비즈 자동정산 실패', str(e))
        code = 1
    with open(os.path.join(HOME, 'logs', f'{today}.log'), 'a', encoding='utf-8') as f:
        f.write('\n'.join(_LOG) + '\n')
    sys.exit(code)


if __name__ == '__main__':
    main()

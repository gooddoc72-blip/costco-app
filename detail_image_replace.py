"""이미 등록된 네이버 상품의 상세페이지 상단 고정이미지 일괄 교체.

등록 때 상세HTML 맨 위에 박힌 `<img src="옛 상단이미지">`의 주소만 새 주소로
바꾼다. 상품명·가격·본문·태그 등 나머지는 GET 받은 원본 그대로 PUT 한다.

- 교체 대상은 **주소가 정확히 일치하는 이미지만**이다. '맨 앞 이미지' 같은
  위치 추정은 상품 본문 사진을 덮어쓸 수 있어 쓰지 않는다.
- 같은 작업을 다시 돌려도 안전하다 — 이미 바뀐 상품은 옛 주소가 없어 건너뛴다.
  서버 재시작으로 중간에 끊겨도 다시 누르면 남은 것만 처리된다.
- 바꾸기 전 상세HTML을 data/detail_backup/*.jsonl 에 한 줄씩 남겨 되돌릴 수 있다.
- 오래 걸리는 일(스토어당 수백~수천 건)이라 백그라운드 스레드로 돌고,
  진행 상황은 JOBS[username] 에 담긴다(같은 Streamlit 프로세스 안에서 공유).
"""
import os
import re
import json
import time
import threading
from datetime import datetime

import requests

from naver_api.core import get_token
from naver_api.products import get_product_list, _sanitize_for_put, _format_naver_err

_API = "https://api.commerce.naver.com/external/v2/products/origin-products/%s"
_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "detail_backup")
_IMG_SRC_RE = re.compile(r'''(<img\b[^>]*?\bsrc\s*=\s*)(["'])([^"']*)\2''', re.I)
# 호출 사이 간격 — 네이버 커머스 API 호출 한도(429)를 피한다
_PAUSE = 0.35

JOBS = {}              # username -> 진행 상황 dict
_LOCK = threading.Lock()


def replace_top_image(html, old_urls, new_url, insert_if_missing=False):
    """상세HTML에서 old_urls 중 하나와 주소가 같은 <img>의 src를 new_url로 바꾼다.

    반환: (new_html, action)  action = 'replaced' | 'inserted' | 'already' | 'none'
    """
    html = str(html or '')
    olds = {str(u).strip() for u in (old_urls or []) if str(u or '').strip()}
    new_url = str(new_url or '').strip()
    olds.discard(new_url)
    n = [0]

    def _sub(m):
        if m.group(3).strip() in olds:
            n[0] += 1
            return '%s%s%s%s' % (m.group(1), m.group(2), new_url, m.group(2))
        return m.group(0)

    out = _IMG_SRC_RE.sub(_sub, html)
    if n[0]:
        return out, 'replaced'
    if any(m.group(3).strip() == new_url for m in _IMG_SRC_RE.finditer(html)):
        return html, 'already'
    if insert_if_missing:
        return ('<img src="%s" style="display:block;max-width:100%%;margin:0 auto">'
                % new_url) + html, 'inserted'
    return html, 'none'


class _Session:
    """토큰을 한 번 받아 재사용하고, 401이면 한 번 다시 받는다."""

    def __init__(self, cid, secret):
        self.cid, self.secret = cid, secret
        self.token = None

    def _headers(self):
        if not self.token:
            self.token, err = get_token(self.cid, self.secret)
            if not self.token:
                raise RuntimeError(f"토큰 발급 실패: {err}")
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def call(self, method, url, **kw):
        for _try in range(4):
            r = requests.request(method, url, headers=self._headers(), timeout=20, **kw)
            if r.status_code == 401 and _try == 0:
                self.token = None
                continue
            if r.status_code == 429:
                time.sleep(2 + _try * 2)
                continue
            return r
        return r


def _put_detail(sess, pno, data, new_detail):
    """GET 원본(data)에서 detailContent만 바꿔 PUT. 연관태그는 원본 그대로 되살린다
    (_sanitize_for_put가 태그를 전부 지우므로 — 안 되살리면 교체와 함께 태그가 사라진다)."""
    op = data.get('originProduct') or {}
    _tags = (((op.get('detailAttribute') or {}).get('seoInfo') or {}).get('sellerTags')) or []
    origin = _sanitize_for_put(dict(op))
    origin['detailContent'] = new_detail
    if _tags:
        _clean = []
        for _t in _tags:
            _txt = str((_t or {}).get('text') or '').strip()
            if not _txt:
                continue
            _e = {'text': _txt}
            if (_t or {}).get('code') not in (None, '', 0):
                _e['code'] = int(_t['code'])
            _clean.append(_e)
        if _clean:
            _da = origin.setdefault('detailAttribute', {})
            _seo = _da.get('seoInfo') if isinstance(_da.get('seoInfo'), dict) else {}
            _seo['sellerTags'] = _clean
            _da['seoInfo'] = _seo
    body = {"originProduct": origin}
    if data.get('smartstoreChannelProduct'):
        body["smartstoreChannelProduct"] = _sanitize_for_put(dict(data['smartstoreChannelProduct']))
    r = sess.call('PUT', _API % pno, json=body)
    if r.status_code == 200:
        return None
    return f"수정 실패({r.status_code}: {_format_naver_err(r)})"


def _new_job(username, mode, total=0):
    return {'mode': mode, 'state': 'running', 'total': total, 'done': 0,
            'replaced': 0, 'inserted': 0, 'already': 0, 'none': 0, 'fail': 0,
            'errors': [], 'backup': '', 'started': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'finished': '', 'message': '', 'stop': False}


def is_running(username):
    return (JOBS.get(username) or {}).get('state') == 'running'


def stop(username):
    if username in JOBS:
        JOBS[username]['stop'] = True


def _run_replace(username, cid, secret, old_urls, new_url, insert_if_missing, limit):
    job = JOBS[username]
    try:
        items, err = get_product_list(cid, secret)
        if err:
            raise RuntimeError(f"상품 목록 조회 실패: {err}")
        pnos, seen = [], set()
        for it in items or []:
            p = str(it.get('originProductNo') or '').strip()
            if p and p not in seen:
                seen.add(p)
                pnos.append((p, it.get('productName') or ''))
        if limit:
            pnos = pnos[:int(limit)]
        job['total'] = len(pnos)
        os.makedirs(_BACKUP_DIR, exist_ok=True)
        job['backup'] = os.path.join(
            _BACKUP_DIR, f"{username}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
        sess = _Session(cid, secret)
        with open(job['backup'], 'a', encoding='utf-8') as bf:
            for pno, name in pnos:
                if job['stop']:
                    job['message'] = '사용자가 중지함'
                    break
                try:
                    g = sess.call('GET', _API % pno)
                    if g.status_code != 200:
                        raise RuntimeError(f"조회 실패({g.status_code}: {_format_naver_err(g)})")
                    data = g.json()
                    old_detail = (data.get('originProduct') or {}).get('detailContent') or ''
                    new_detail, action = replace_top_image(
                        old_detail, old_urls, new_url, insert_if_missing)
                    if action in ('replaced', 'inserted'):
                        bf.write(json.dumps({'pno': pno, 'name': name, 'detail': old_detail},
                                            ensure_ascii=False) + '\n')
                        bf.flush()
                        e = _put_detail(sess, pno, data, new_detail)
                        if e:
                            raise RuntimeError(e)
                        time.sleep(_PAUSE)
                    job[action] += 1
                except Exception as ex:
                    job['fail'] += 1
                    if len(job['errors']) < 200:
                        job['errors'].append(f"{pno} {name[:30]} — {str(ex)[:160]}")
                job['done'] += 1
                time.sleep(_PAUSE)
        job['state'] = 'done'
    except Exception as ex:
        job['state'] = 'error'
        job['message'] = str(ex)[:300]
    finally:
        job['finished'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _run_restore(username, cid, secret, backup_path):
    job = JOBS[username]
    try:
        rows = []
        with open(backup_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        # 같은 상품이 여러 번 기록됐으면 가장 먼저 남긴 원본으로 되돌린다
        first = {}
        for r in rows:
            first.setdefault(r['pno'], r)
        job['total'] = len(first)
        sess = _Session(cid, secret)
        for pno, r in first.items():
            if job['stop']:
                job['message'] = '사용자가 중지함'
                break
            try:
                g = sess.call('GET', _API % pno)
                if g.status_code != 200:
                    raise RuntimeError(f"조회 실패({g.status_code}: {_format_naver_err(g)})")
                e = _put_detail(sess, pno, g.json(), r['detail'])
                if e:
                    raise RuntimeError(e)
                job['replaced'] += 1
            except Exception as ex:
                job['fail'] += 1
                if len(job['errors']) < 200:
                    job['errors'].append(f"{pno} {str(r.get('name', ''))[:30]} — {str(ex)[:160]}")
            job['done'] += 1
            time.sleep(_PAUSE)
        job['state'] = 'done'
    except Exception as ex:
        job['state'] = 'error'
        job['message'] = str(ex)[:300]
    finally:
        job['finished'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def start_replace(username, cid, secret, old_urls, new_url, insert_if_missing=False, limit=0):
    """백그라운드 교체 시작. 이미 돌고 있으면 False."""
    with _LOCK:
        if is_running(username):
            return False
        JOBS[username] = _new_job(username, 'replace')
    threading.Thread(target=_run_replace, daemon=True,
                     args=(username, cid, secret, list(old_urls), new_url,
                           bool(insert_if_missing), int(limit or 0))).start()
    return True


def start_restore(username, cid, secret, backup_path):
    with _LOCK:
        if is_running(username):
            return False
        JOBS[username] = _new_job(username, 'restore')
    threading.Thread(target=_run_restore, daemon=True,
                     args=(username, cid, secret, backup_path)).start()
    return True


def list_backups(username):
    """이 사용자의 백업 파일(최신순)과 건수."""
    if not os.path.isdir(_BACKUP_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(_BACKUP_DIR), reverse=True):
        if fn.startswith(username + '_') and fn.endswith('.jsonl'):
            fp = os.path.join(_BACKUP_DIR, fn)
            try:
                with open(fp, encoding='utf-8') as f:
                    n = sum(1 for ln in f if ln.strip())
            except Exception:
                n = 0
            if n:
                out.append((fp, fn, n))
    return out

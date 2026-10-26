# -*- coding: utf-8 -*-
"""🔥 SNS 트렌드 — 지금 뜨는 코스트코 상품 TOP 30 + 판매 제안 (관리자 전용, 주 1회).

왜: 사용자들이 '무엇을 주력으로 팔지'를 감으로 정했다. SNS·검색에서 뜨는 상품을 먼저
찾아 주력상품으로 밀면 판매량을 늘릴 수 있다(2026-10-25 요청).

데이터(모두 공식 API):
  · 네이버 블로그·카페 검색 — '코스트코 추천템/신상/하울…' 최근 글로 후보를 찾고, 상품별
    최근 7일·이전 7일 언급 수를 센다
  · 유튜브 Data API — 최근 2주 '코스트코 …' 영상 제목·설명·조회수·채널 구독자 수 (키가 있을 때)
  · 네이버 뉴스 — 최근 2주 '코스트코 + 연예인·방송·대란·완판' 기사
  · 데이터랩 검색어 트렌드 — 최근 2주 ÷ 앞 6주
  · 검색광고 keywordstool — 월간 검색수·경쟁도
  ※ 우리 판매자들의 주문·판매가는 쓰지 않는다 — 목적은 SNS에서 뜨는 코스트코 상품의 트렌드를
    파악하는 것이다(10/26). 우리 판매 실적이 끼면 이미 파는 상품이 '트렌드'로 올라온다.
  ※ 네이버 쇼핑 검색 API는 2026-07-31 종료 — 최저가·판매처 수는 크롬 확장 수집으로 붙인다.
점수(100): 언급 증가 25 · 언급량 10 · 화제성 20(유명인·방송·대란 기사, 화제 표현 글, 구독자 10만+ 유튜버) · 검색 증가 20 · 월간 검색수 15 · 판매 가능성 10
상위 40개는 에이전트 회의(trend_council)가 서로 검토해 등급·이유·추천가·상품명·키워드·주의를
정하고('제외'는 빠진다) 30개를 남긴다. Claude 키가 없으면 AI 한 번 제안(ai_suggest)으로 대신한다.
결과는 auth.db trend_reports에 주 단위로 쌓는다.
"""
import json
import math
import re
import sqlite3
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests

from db_core import get_auth_db

SEEDS = ['코스트코 추천템', '코스트코 신상', '코스트코 하울', '코스트코 재구매',
         '코스트코 장보기', '코스트코 품절', '코스트코 꿀템', '코스트코 할인']
STOP = set('코스트코 커클랜드 시그니처 시그니춰 대용량 할인 추천 신상 상품 제품 세트 구매 개입 '
           '코스트코추천 정품 국내 무료 배송 아이스 아이스박스 포장 냉동 냉장'.split())


# ── 저장 ────────────────────────────────────────────────────
def _ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS trend_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week TEXT NOT NULL, created_at TEXT, created_by TEXT DEFAULT '',
        data_json TEXT NOT NULL)""")


def save_report(data, by=''):
    conn = get_auth_db()
    try:
        _ensure(conn)
        conn.execute("INSERT INTO trend_reports (week, created_at, created_by, data_json) VALUES (?,?,?,?)",
                     (data['week'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), by,
                      json.dumps(data, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def list_reports(limit=20):
    conn = get_auth_db()
    try:
        _ensure(conn)
        return [{'id': r[0], 'week': r[1], 'created_at': r[2]} for r in conn.execute(
            "SELECT id, week, created_at FROM trend_reports ORDER BY id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def get_report(rid=None):
    conn = get_auth_db()
    try:
        _ensure(conn)
        r = (conn.execute("SELECT id, data_json FROM trend_reports WHERE id=?", (int(rid),)).fetchone()
             if rid else conn.execute("SELECT id, data_json FROM trend_reports ORDER BY id DESC LIMIT 1").fetchone())
        if not r:
            return None
        d = json.loads(r[1])
        d['id'] = r[0]
        return d
    finally:
        conn.close()


# ── 관리자 승인 — 회의 결과를 실제로 밀지 정한다 (판정 성적의 기준) ──
def _ensure_dec(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS trend_decisions (
        report_id INTEGER NOT NULL, costco_no TEXT NOT NULL,
        decision TEXT NOT NULL, memo TEXT DEFAULT '', decided_by TEXT DEFAULT '', decided_at TEXT,
        UNIQUE(report_id, costco_no))""")


def get_decisions(rid):
    """{코스트코번호: {decision, memo, decided_by, decided_at}}"""
    conn = get_auth_db()
    try:
        _ensure_dec(conn)
        return {r[0]: {'decision': r[1], 'memo': r[2], 'decided_by': r[3], 'decided_at': r[4]} for r in conn.execute(
            "SELECT costco_no, decision, memo, decided_by, decided_at FROM trend_decisions WHERE report_id=?",
            (int(rid),))}
    finally:
        conn.close()


def set_decision(rid, costco_no, decision, memo='', by=''):
    """decision: approve(주력으로 민다) | hold(보류) | ''(취소)"""
    conn = get_auth_db()
    try:
        _ensure_dec(conn)
        if not decision:
            conn.execute("DELETE FROM trend_decisions WHERE report_id=? AND costco_no=?", (int(rid), str(costco_no)))
        else:
            conn.execute(
                "INSERT INTO trend_decisions (report_id, costco_no, decision, memo, decided_by, decided_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(report_id, costco_no) DO UPDATE SET "
                "decision=excluded.decision, memo=excluded.memo, decided_by=excluded.decided_by, "
                "decided_at=excluded.decided_at",
                (int(rid), str(costco_no), decision, memo, by, datetime.now().strftime('%Y-%m-%d %H:%M')))
        conn.commit()
    finally:
        conn.close()


# ── 키 ──────────────────────────────────────────────────────
def keys():
    from db import get_all_settings, get_global_setting
    s = get_all_settings('admin') or {}
    try:
        from ai_service import get_ai_keys
        ak, gk = get_ai_keys()
    except Exception:
        ak, gk = s.get('anthropic_api_key', ''), s.get('gemini_api_key', '')
    return {
        'open': (s.get('naver_open_client_id', ''), s.get('naver_open_client_secret', '')),
        'ad': (s.get('naver_ad_api_key', ''), s.get('naver_ad_secret', ''), s.get('naver_ad_customer_id', '')),
        'anthropic': ak, 'gemini': gk,
        'youtube': (get_global_setting('youtube_api_key') or s.get('youtube_api_key') or '').strip(),
    }


# ── 상품 목록 · 이름 토막 ─────────────────────────────────────
def _tokens(name):
    n = re.sub(r'[\(\[].*?[\)\]]', ' ', str(name or ''))
    n = re.sub(r'\d+(\.\d+)?\s*(g|kg|ml|l|개|입|매|팩|ct|ea|x|p|pr|매입)\b', ' ', n, flags=re.I)
    ws = re.findall(r'[가-힣]{2,}|[a-zA-Z]{3,}', n)
    return [w.lower() for w in ws if w.lower() not in STOP and w not in STOP]


def catalog():
    """{코스트코번호: {name, store, tokens, short}} — 공유 가격DB + 최근 영수증 품목."""
    a = get_auth_db()
    a.row_factory = sqlite3.Row
    out = {}
    try:
        for r in a.execute("SELECT product_no, costco_name, store_price, unit_price, category FROM shared_products "
                           "WHERE TRIM(COALESCE(product_no,''))<>''"):
            pn = str(r['product_no']).strip()
            out[pn] = {'name': str(r['costco_name'] or ''), 'store': int(r['store_price'] or r['unit_price'] or 0),
                       'category': str(r['category'] or '')}
        since = (date.today() - timedelta(days=90)).isoformat()
        for r in a.execute("SELECT product_no, product_name, unit_price FROM receipt_items WHERE receipt_date>=? "
                           "ORDER BY receipt_date", (since,)):
            pn = str(r['product_no'] or '').strip()
            if not pn:
                continue
            e = out.setdefault(pn, {'name': str(r['product_name'] or ''), 'store': 0, 'category': ''})
            e['receipt_name'] = str(r['product_name'] or '')
            e['store'] = int(r['unit_price'] or 0) or e['store']
    finally:
        a.close()
    for pn, e in out.items():
        e['tokens'] = list(dict.fromkeys(_tokens(e['name'])))[:4]
        e['short'] = ' '.join(e['tokens'][:3])
    return out


# ── 네이버·유튜브 수집 ────────────────────────────────────────
def _naver(kind, q, start, ck, sort='date'):
    try:
        r = requests.get(f"https://openapi.naver.com/v1/search/{kind}.json",
                         headers={"X-Naver-Client-Id": ck[0], "X-Naver-Client-Secret": ck[1]},
                         params={"query": q, "display": 100, "start": start, "sort": sort}, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def _strip(s):
    return re.sub(r'<[^>]+>|&[a-z]+;', ' ', str(s or ''))


# 화제성 — 유명인·방송·인플루언서·품절 대란을 다룬 기사와 글. 인기가 '번지는' 신호다.
NEWS_SEEDS = ['코스트코 연예인', '코스트코 방송', '코스트코 대란', '코스트코 품절', '코스트코 완판',
              '코스트코 유튜버', '코스트코 인플루언서', '코스트코 화제']
BUZZ = re.compile(r'연예인|배우|가수|아이돌|셀럽|스타|방송|예능|나혼자|나 혼자|편스토랑|전참시|유튜버|인플루언서|'
                  r'품절|대란|완판|오픈런|난리|핫템|인싸템|틱톡|릴스|인스타', re.I)
INFLUENCER_SUBS = 100_000      # 이 구독자 수 이상 채널을 인플루언서로 본다


def _news(ck, days, log):
    """네이버 뉴스 — 최근 days일 '코스트코 + 유명인·방송·대란' 기사."""
    from email.utils import parsedate_to_datetime
    since = datetime.now() - timedelta(days=days)
    out, seen = [], set()
    for q in NEWS_SEEDS:
        for start in (1, 101):
            j = _naver('news', q, start, ck)
            its = j.get('items') or []
            for it in its:
                link = it.get('originallink') or it.get('link') or ''
                if link in seen:
                    continue
                seen.add(link)
                try:
                    pdt = parsedate_to_datetime(it.get('pubDate')).replace(tzinfo=None)
                except Exception:
                    continue
                if pdt < since:
                    continue
                txt = _strip(it.get('title')) + ' ' + _strip(it.get('description'))
                out.append({'src': 'news', 'date': pdt.strftime('%Y%m%d'), 'text': txt, 'link': link,
                            'views': 0, 'buzz': True})
            if len(its) < 100:
                break
            time.sleep(0.1)
    log(f'뉴스 기사 {len(out)}건')
    return out


def collect_docs(ck, yt_key, days=14, log=print):
    """최근 days일 블로그·카페·뉴스·유튜브 글 — [{src, date, text, views, link, buzz, subs?, channel?}]"""
    cut = (date.today() - timedelta(days=days)).strftime('%Y%m%d')
    docs, seen = [], set()
    for q in SEEDS:
        for start in range(1, 1000, 100):
            j = _naver('blog', q, start, ck)
            its = j.get('items') or []
            old = 0
            for it in its:
                if it.get('link') in seen:
                    continue
                seen.add(it.get('link'))
                if str(it.get('postdate') or '') < cut:
                    old += 1
                    continue
                docs.append({'src': 'blog', 'date': it.get('postdate', ''),
                             'text': _strip(it.get('title')) + ' ' + _strip(it.get('description')),
                             'link': it.get('link', ''), 'views': 0})
            if not its or old > 60:
                break
            time.sleep(0.1)
        for start in (1, 101, 201):
            j = _naver('cafearticle', q, start, ck)
            for it in j.get('items') or []:
                if it.get('link') in seen:
                    continue
                seen.add(it.get('link'))
                docs.append({'src': 'cafe', 'date': '', 'text': _strip(it.get('title')) + ' ' + _strip(it.get('description')),
                             'link': it.get('link', ''), 'views': 0})
            time.sleep(0.1)
    log(f"블로그·카페 글 {len(docs)}건")
    docs += _news(ck, days, log)
    if yt_key:
        after = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        ids = []
        for q in SEEDS:
            try:
                r = requests.get("https://www.googleapis.com/youtube/v3/search", timeout=15, params={
                    'key': yt_key, 'q': q, 'part': 'snippet', 'type': 'video', 'maxResults': 50,
                    'publishedAfter': after, 'regionCode': 'KR', 'relevanceLanguage': 'ko'}).json()
                if r.get('error'):
                    log('유튜브 오류: ' + str(r['error'].get('message'))[:120])
                    break
                ids += [it['id']['videoId'] for it in r.get('items') or [] if it.get('id', {}).get('videoId')]
            except Exception as e:
                log(f'유튜브 오류: {e}')
                break
        ids = list(dict.fromkeys(ids))
        vids = []
        for i in range(0, len(ids), 50):
            try:
                r = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=15, params={
                    'key': yt_key, 'id': ','.join(ids[i:i + 50]), 'part': 'snippet,statistics'}).json()
            except Exception:
                continue
            for it in r.get('items') or []:
                sn, stt = it.get('snippet') or {}, it.get('statistics') or {}
                vids.append({'src': 'youtube', 'date': str(sn.get('publishedAt', ''))[:10].replace('-', ''),
                             'text': f"{sn.get('title', '')} {str(sn.get('description', ''))[:600]}",
                             'link': 'https://youtu.be/' + it['id'], 'views': int(stt.get('viewCount') or 0),
                             'channel': str(sn.get('channelTitle') or ''), '_cid': sn.get('channelId', '')})
        # 채널 구독자 수 — 구독자 많은 채널(인플루언서)이 다룬 상품은 인기가 번지는 신호다
        cids = list(dict.fromkeys(v['_cid'] for v in vids if v['_cid']))
        subs = {}
        for i in range(0, len(cids), 50):
            try:
                r = requests.get("https://www.googleapis.com/youtube/v3/channels", timeout=15, params={
                    'key': yt_key, 'id': ','.join(cids[i:i + 50]), 'part': 'statistics'}).json()
                for it in r.get('items') or []:
                    subs[it['id']] = int((it.get('statistics') or {}).get('subscriberCount') or 0)
            except Exception:
                continue
        for v in vids:
            v['subs'] = subs.get(v.pop('_cid'), 0)
        docs += vids
        log(f"유튜브 영상 {len(vids)}건 (구독자 {INFLUENCER_SUBS // 10000}만+ 채널 "
            f"{sum(1 for v in vids if v['subs'] >= INFLUENCER_SUBS)}건)")
    for d in docs:
        d['buzz'] = bool(d.get('buzz') or BUZZ.search(d['text']))
    return docs


def match_docs(docs, cat):
    """글마다 언급된 상품 — 상품 이름 토막 2개 이상(토막이 하나뿐인 상품은 그 하나)이 들어 있으면 언급."""
    idx = defaultdict(set)
    for pn, e in cat.items():
        for t in e['tokens']:
            idx[t].add(pn)
    hits = defaultdict(list)
    for d in docs:
        txt = d['text'].lower().replace(' ', '')
        cnt = defaultdict(int)
        for t, pns in idx.items():
            if len(pns) > 120:      # '치즈'·'초콜릿'처럼 너무 흔한 토막은 단독 근거가 못 된다
                continue
            if t in txt:
                for pn in pns:
                    cnt[pn] += 1
        for pn, c in cnt.items():
            need = 2 if len(cat[pn]['tokens']) >= 2 else 1
            if c >= need:
                hits[pn].append(d)
    return hits


def blog_week(ck, kw):
    """'코스트코 <상품>' 블로그 글 중 **상품 토막이 다 들어 있는 글**만, 최근 7일·그 전 7일 수.

    네이버 검색은 단어 하나만 걸려도 글을 준다. 그대로 세면 '가지'·'토마토'처럼 흔한 말이
    모든 코스트코 글에 걸려 언급이 한도(300)까지 차고, 이전 7일은 못 세서 '급상승'으로
    잘못 나왔다(시범 1차). 글 본문에 토막이 다 있을 때만 세고, 14일 전까지 넘겨 본다.
    검색 결과는 1,000건까지만 넘길 수 있어 최근 글이 아주 많으면 14일 전까지 못 간다 —
    그때는 이전 7일이 0으로 남아 '급상승'처럼 보인다(2차: 789/0). capped로 알려 준다.
    반환: (최근 7일, 이전 7일, 전체 글 수, capped)
    """
    t = date.today()
    c7, c14 = (t - timedelta(days=7)).strftime('%Y%m%d'), (t - timedelta(days=14)).strftime('%Y%m%d')
    toks = [w.lower() for w in kw.split() if w]
    a = b = 0
    total = 0
    reached = False
    for start in range(1, 1000, 100):
        j = _naver('blog', '코스트코 ' + kw, start, ck)
        total = int(j.get('total') or total)
        its = j.get('items') or []
        for it in its:
            txt = (_strip(it.get('title')) + _strip(it.get('description'))).lower().replace(' ', '')
            if not all(w in txt for w in toks):
                continue
            pd_ = str(it.get('postdate') or '')
            if pd_ >= c7:
                a += 1
            elif pd_ >= c14:
                b += 1
        if not its or str(its[-1].get('postdate') or '') < c14:
            reached = True
            break
        time.sleep(0.08)
    return a, b, total, not reached


def datalab(ck, kws):
    out = {}
    t = date.today()
    for i in range(0, len(kws), 5):
        body = {"startDate": (t - timedelta(weeks=8)).isoformat(), "endDate": t.isoformat(), "timeUnit": "week",
                "keywordGroups": [{"groupName": k, "keywords": ['코스트코 ' + k]} for k in kws[i:i + 5]]}
        try:
            j = requests.post("https://openapi.naver.com/v1/datalab/search", timeout=15,
                              headers={"X-Naver-Client-Id": ck[0], "X-Naver-Client-Secret": ck[1],
                                       "Content-Type": "application/json"}, data=json.dumps(body)).json()
        except Exception:
            continue
        if j.get('errorMessage'):
            print('데이터랩 오류:', j.get('errorMessage'))
        for res in j.get('results') or []:
            ds = [x['ratio'] for x in res.get('data') or []]
            if len(ds) >= 4:
                last, before = sum(ds[-2:]) / 2, sum(ds[:-2]) / max(1, len(ds) - 2)
                out[res['title']] = round(last / before, 2) if before else (3.0 if last else 1.0)
        time.sleep(0.2)
    return out


def ad_volume(adk, kw):
    """(월간 검색수, 경쟁도) — '코스트코<상품>' 또는 '<상품>' 중 정확히 맞는 줄."""
    if not all(adk):
        return 0, ''
    try:
        from naver_api.keywords import keyword_tool
        rows, err = keyword_tool(*adk, ['코스트코' + kw.replace(' ', ''), kw.replace(' ', '')])
    except Exception:
        return 0, ''
    want = {('코스트코' + kw).replace(' ', ''), kw.replace(' ', '')}
    best = max([r for r in rows or [] if r.get('키워드', '').replace(' ', '') in want],
               key=lambda r: r.get('총검색량', 0), default=None)
    return (int(best['총검색량']), str(best.get('경쟁도') or '')) if best else (0, '')


def suggested_price(store, margin=0.15):
    """네이버 판매가 공식: (매장가 + 택배 3,000) × (1+마진) ÷ 0.945, 10원 올림."""
    if not store:
        return 0
    p = (store + 3000) * (1 + margin) / 0.945
    return int(math.ceil(p / 10) * 10)


def _pct_rank(vals):
    s = sorted(vals)
    n = len(s)
    return lambda v: (sum(1 for x in s if x <= v) - 1) / max(1, n - 1) if n > 1 else 0.5


# ── AI 판매 제안 ──────────────────────────────────────────────
AI_SYSTEM = (
    "너는 코스트코 구매대행 판매자들의 상품 기획자다. 네이버 스마트스토어에서 코스트코 상품을 "
    "되파는 판매자에게, SNS·검색에서 뜨는 상품을 주력상품으로 삼을지 조언한다. "
    "데이터에 없는 사실(방송 출연, 품절 등)은 지어내지 말고, 글 예시에서 읽히는 이유만 쓴다. "
    "반드시 JSON 배열만 출력한다.")


def ai_suggest(items, ak, gk, log=print):
    from ai_service import ai_complete
    lines = []
    for i, x in enumerate(items, 1):
        lines.append(json.dumps({
            'no': i, 'costco_no': x['costco_no'], '상품': x['name'], '매장가': x['store'],
            '블로그_7일': x['blog7'], '블로그_이전7일': x['blog_prev7'], '유튜브영상': x['yt_count'],
            '유튜브조회': x['yt_views'], '검색추세': x['trend'], '월간검색수': x['volume'],
            '경쟁도': x['comp'], '공식추천가': x['suggest_price'],
            '글예시': x['samples'][:3]}, ensure_ascii=False))
    user = ("아래 상품 각각에 대해 JSON 배열로 답하라. 원소 형식:\n"
            '{"no":번호,"grade":"주력|시도|관찰","reason":"뜨는 이유 1~2문장",'
            '"price":추천 네이버 판매가(정수, 공식추천가 근처),'
            '"title":"네이버 상품명 제안(40자 이내, 코스트코 포함)","keywords":["검색 키워드 5개"],'
            '"caution":"배송·보관·경쟁 주의 1문장(없으면 빈 문자열)"}\n\n' + '\n'.join(lines))
    txt, err, prov = ai_complete(AI_SYSTEM, user, gemini_key=gk or '', anthropic_key=ak or '',
                                 max_tokens=6000, feature='trend')
    if not txt:
        log(f'AI 제안 실패: {err}')
        return {}
    m = re.search(r'\[.*\]', txt, re.S)
    try:
        arr = json.loads(m.group(0)) if m else []
    except Exception:
        log('AI 응답 해석 실패')
        return {}
    log(f'AI 제안 {len(arr)}건 ({prov})')
    return {int(a.get('no', 0)): a for a in arr if isinstance(a, dict)}


# ── 전체 실행 ─────────────────────────────────────────────────
def run(by='', top_n=30, log=print):
    t0 = time.time()
    k = keys()
    if not all(k['open']):
        raise RuntimeError('네이버 오픈 API 키(naver_open_client_id/secret)가 없습니다.')
    cat = catalog()
    log(f'코스트코 상품 {len(cat)}개')
    docs = collect_docs(k['open'], k['youtube'], log=log)
    hits = match_docs(docs, cat)
    # 후보: SNS 글에 많이 언급된 상품 — 기사·화제 표현 글·인플루언서 영상은 더 무겁게 본다
    #   (우리 판매 실적은 보지 않는다)
    def _weight(pn):
        hs = hits[pn]
        return (len(hs) + 3 * sum(1 for d in hs if d.get('buzz'))
                + 5 * sum(1 for d in hs if d.get('subs', 0) >= INFLUENCER_SUBS))
    cand = sorted(hits, key=lambda pn: -_weight(pn))[:90]
    cand = [pn for pn in dict.fromkeys(cand) if pn in cat and cat[pn]['short']
            and not re.search(r'상품권|멤버십|기프트카드|gift ?card', cat[pn]['name'], re.I)
            # 이름 토막이 '가지'처럼 흔한 한 단어뿐이면 글을 가려낼 수 없다
            and (len(cat[pn]['tokens']) >= 2 or len(cat[pn]['short']) >= 4)]
    log(f'후보 {len(cand)}개 — 상품별 언급·검색 확인 중')
    rows = []
    for pn in cand:
        e = cat[pn]
        kw = e['short']
        b7, bp, btot, capped = blog_week(k['open'], kw)
        vol, comp = ad_volume(k['ad'], kw)
        hs = hits.get(pn, [])
        yt = [d for d in hs if d['src'] == 'youtube']
        infl = sorted([d for d in yt if d.get('subs', 0) >= INFLUENCER_SUBS], key=lambda d: -d['subs'])
        # 근거 글 순서 — 인플루언서 영상 → 기사 → 화제 표현 글 → 조회수 → 최신
        _pri = sorted(hs, key=lambda d: (d.get('subs', 0) >= INFLUENCER_SUBS, d['src'] == 'news',
                                         bool(d.get('buzz')), d['views'], str(d['date'])), reverse=True)
        rows.append({
            'costco_no': pn, 'name': e.get('receipt_name') or e['name'], 'full_name': e['name'], 'kw': kw,
            'store': e['store'], 'category': e['category'],
            'mentions': len(hs), 'blog7': b7, 'blog_prev7': bp, 'blog_total': btot, 'blog_capped': capped,
            'yt_count': len(yt), 'yt_views': sum(d['views'] for d in yt),
            'news': sum(1 for d in hs if d['src'] == 'news'),
            'buzz': sum(1 for d in hs if d.get('buzz') and d['src'] != 'news'),
            'influencers': [{'channel': d.get('channel', ''), 'subs': d['subs'], 'views': d['views'], 'link': d['link']}
                            for d in infl[:5]],
            'volume': vol, 'comp': comp,
            'samples': [{'src': d['src'], 'text': d['text'][:120], 'link': d['link']} for d in _pri[:5]],
            # 회의 조사원이 볼 글 (보고서에는 저장 안 함)
            '_evid': [{'src': d['src'], 'text': d['text'][:160]} for d in _pri[:8]],
        })
        time.sleep(0.05)
    tr = datalab(k['open'], [r['kw'] for r in rows])
    for r in rows:
        r['trend'] = tr.get(r['kw'])
        r['suggest_price'] = suggested_price(r['store'])
        sp = r['suggest_price']
        r['margin'] = int(sp - r['store'] - 3000 - 300 - sp * 0.055) if sp and r['store'] else 0
        r['margin_rate'] = round(r['margin'] / sp, 3) if sp else 0
        r['cold'] = bool(re.search(r'냉동|냉장|아이스|요거트|치즈|우유|고기|갈비|연어|케이크', r['name'] + r['full_name']))
    # 점수
    # 증가율은 작은 수에 흔들리지 않게 +3을 더해 본다(0→3건이 '무한대 상승'이 되지 않게)
    # 이전 7일을 끝까지 못 센 상품(capped)은 증가율을 알 수 없다 — 중립(1.0)으로 둔다
    g_mention = [1.0 if r.get('blog_capped') else (r['blog7'] + 3) / (r['blog_prev7'] + 3) for r in rows]
    # 화제성 = 기사×3 + 화제 표현 글 + 인플루언서 영상×5 (+ 인플루언서 영상 조회수 규모)
    def _buzz(r):
        iv = sum(i['views'] for i in r['influencers'])
        return r['news'] * 3 + r['buzz'] + len(r['influencers']) * 5 + (math.log10(iv + 1) if iv else 0)
    pr = {
        'growth': _pct_rank(g_mention),
        'volume_m': _pct_rank([r['blog7'] + r['yt_count'] * 3 for r in rows]),
        'buzz': _pct_rank([_buzz(r) for r in rows]),
        'trend': _pct_rank([r['trend'] or 1.0 for r in rows]),
        'search': _pct_rank([r['volume'] for r in rows]),
    }
    for r, g in zip(rows, g_mention):
        sell = 0.5
        if r['margin_rate']:
            sell = max(0.0, min(1.0, r['margin_rate'] / 0.2))
        sell *= {'높음': 0.7, '중간': 0.9, '낮음': 1.0}.get(r['comp'], 0.9)
        if r['cold']:
            sell *= 0.85
        # 찾는 사람이 거의 없는 상품(월 검색 300 미만)은 팔기 어렵다 — 시즌 장식 등
        if r['volume'] < 300:
            sell *= 0.4
        # 화제성이 없는(0) 상품에 백분위 점수를 주지 않는다 — 대부분 0이면 0끼리 중간 등수를 받는다
        b = _buzz(r)
        parts = {
            '언급증가': round(25 * pr['growth'](g), 1),
            '언급량': round(10 * pr['volume_m'](r['blog7'] + r['yt_count'] * 3), 1),
            '화제성': round(20 * pr['buzz'](b), 1) if b else 0.0,
            '검색증가': round(20 * pr['trend'](r['trend'] or 1.0), 1),
            '검색수': round(15 * pr['search'](r['volume']), 1),
            '판매가능성': round(10 * sell, 1),
        }
        r['score_parts'] = parts
        r['score'] = round(sum(parts.values()), 1)
    rows.sort(key=lambda r: -r['score'])
    council = None
    if k['anthropic']:
        # 에이전트 회의: 30위 밖 20개까지 올려 '제외'로 빠지는 자리를 채운다
        import trend_council
        pool = rows[:top_n + 20]
        try:
            council = trend_council.run_council(pool, k, log=log)
            top = [r for r in pool if (r.get('ai') or {}).get('grade') != '제외'][:top_n]
            council['excluded'] = [{'costco_no': r['costco_no'], 'name': r['name'],
                                    'reason': (r.get('ai') or {}).get('reason', '')}
                                   for r in pool if (r.get('ai') or {}).get('grade') == '제외']
        except Exception as e:
            log(f'회의 실패 → AI 한 번 제안으로 대신: {e}')
            council = None
            for r in pool:
                r.pop('ai', None)
                r.pop('council', None)
    if council is None:
        top = rows[:top_n]
        sug = ai_suggest(top, k['anthropic'], k['gemini'], log=log) if (k['anthropic'] or k['gemini']) else {}
        for i, r in enumerate(top, 1):
            r['ai'] = sug.get(i, {})
    prev = get_report()
    prev_rank = {x['costco_no']: x['rank'] for x in (prev or {}).get('items', [])}
    for i, r in enumerate(top, 1):
        r['rank'] = i
        r['prev_rank'] = prev_rank.get(r['costco_no'])
    for r in rows:
        r.pop('_evid', None)
    data = {'week': date.today().isoformat(), 'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'docs': len(docs), 'sources': {s: sum(1 for d in docs if d['src'] == s) for s in ('blog', 'cafe', 'news', 'youtube')},
            'candidates': len(rows), 'youtube': bool(k['youtube']), 'elapsed': int(time.time() - t0),
            'council': council, 'items': top}
    save_report(data, by)
    log(f'완료 — TOP {len(top)} 저장 ({data["elapsed"]}초)')
    return data


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '.')
    d = run(by='cli')
    for x in d['items'][:30]:
        print(x['rank'], x['score'], x['name'][:20], x['ai'].get('grade', ''), x['ai'].get('reason', '')[:50])

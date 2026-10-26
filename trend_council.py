# -*- coding: utf-8 -*-
"""🧑‍⚖️ 트렌드 회의 — 에이전트 여럿이 서로 검토해 TOP 30 판매 제안을 정한다.

왜: AI 한 번 호출로 등급을 매기면 틀린 데이터(흔한 단어로 잘못 센 언급, 집계 한도에
걸린 급상승)를 그대로 믿고 '주력'을 붙였다(시범 1·2차: 월 검색 20인 시즌 장식이 주력,
블로그 789/0을 급상승으로 읽음). 서로 다른 모델이 따로 보고, 엇갈린 것만 토론하고,
검증관이 근거를 다시 조회해 판정하면 한 모델의 실수가 그대로 결론이 되지 않는다.

회의 순서
  0. 조사원(Claude Haiku)   — 글 예시가 정말 그 상품 이야기인지 가려 '관련 글 비율'을 단다
  1. 독립 의견               — 분석가 A(Claude Sonnet) · 분석가 B(Gemini, 없으면 Claude Haiku)가
                              서로의 답을 보지 않고 평가한다
  2. 토론                    — 등급이 엇갈리거나 데이터 이상을 짚은 상품만, 상대 의견을 보고
                              유지·수정·반박한다
  3. 판정(Claude Opus)       — 검증관이 도구(블로그 글·검색수·판매 추이 조회)로 근거를 다시
                              확인해 최종 등급을 정한다
  4. 규칙 검사(코드)         — 마진 마이너스 주력 금지, 월 검색 300 미만은 우리 판매가 없으면
                              관찰, 끝난 시즌은 관찰, 추천가 범위 — AI가 어겨도 코드가 막는다
  5. 관리자 승인             — 화면에서 승인·보류하고, 2주 뒤 실제 판매로 적중률을 매겨
                              다음 회의 검증관에게 '지난 판정 성적'으로 넘긴다

숫자는 언제나 코드(DB·API)가 만든다. 에이전트는 받은 숫자를 바꿀 수 없고, 등급·이유·
추천가·상품명만 쓴다.
"""
import json
import re
from datetime import date, datetime, timedelta

import ai_service
import trend_prompts as P

MODEL_JUDGE = 'claude-opus-5-5'       # 검증관 — 판정·종합
MODEL_ANALYST = 'claude-sonnet-5-5'   # 분석가 A
MODEL_SCOUT = 'claude-haiku-5-5'      # 조사원 · (Gemini가 없을 때) 분석가 B
MAX_DISPUTE = 20                      # 토론·판정에 올릴 최대 상품 수 — 비용 상한
JUDGE_TURNS = 10                      # 검증관 도구 호출 왕복 상한

GRADES = ['주력', '시도', '관찰', '제외']
EVIDENCE = ['언급증가', '언급량', '검색추세', '월간검색수', '우리판매', '마진', '글내용', '시즌', '경쟁도']
_RANK = {'주력': 3, '시도': 2, '관찰': 1, '제외': 0}


# ── Claude 호출 (공식 SDK) ─────────────────────────────────────
def _client(ak):
    import anthropic
    return anthropic.Anthropic(api_key=ak, max_retries=3, timeout=600)


def _track(model, usage):
    """사용량을 기존 AI 사용량 장부(ai_usage_log)에 그대로 적는다."""
    ai_service._track('claude', model, 'trend',
                      in_tokens=getattr(usage, 'input_tokens', 0) or 0,
                      out_tokens=getattr(usage, 'output_tokens', 0) or 0,
                      cache_read=getattr(usage, 'cache_read_input_tokens', 0) or 0,
                      cache_write=getattr(usage, 'cache_creation_input_tokens', 0) or 0)


def _gate():
    b = ai_service._usage_gate()
    if b:
        raise RuntimeError(b)


def _text(msg):
    if msg.stop_reason == 'refusal':
        raise RuntimeError('AI가 응답을 거절했습니다')
    if msg.stop_reason == 'max_tokens':
        raise RuntimeError('AI 응답이 길이 한도에서 잘렸습니다')
    return next((b.text for b in msg.content if b.type == 'text'), '')


def _claude_json(cl, model, system, user, schema, *, effort='medium', max_tokens=32000):
    """구조화 출력(JSON 스키마) 한 번. 긴 출력이라 스트리밍으로 받는다."""
    _gate()
    with cl.messages.stream(model=model, max_tokens=max_tokens, system=system,
                            messages=[{'role': 'user', 'content': user}],
                            output_config={'effort': effort,
                                           'format': {'type': 'json_schema', 'schema': schema}}) as s:
        msg = s.get_final_message()
    _track(model, msg.usage)
    return json.loads(_text(msg))


def _gemini_json(gk, system, user, max_tokens=16000):
    body = {
        'systemInstruction': {'parts': [{'text': system}]},
        'contents': [{'role': 'user', 'parts': [{'text': user}]}],
        'generationConfig': {'maxOutputTokens': max_tokens, 'temperature': 0,
                             'responseMimeType': 'application/json',
                             'thinkingConfig': {'thinkingBudget': 0}},
    }
    txt, err = ai_service._gemini_post(gk, ai_service.GEMINI_MODEL, body, timeout=240, feature='trend')
    if not txt:
        raise RuntimeError(f'Gemini 실패: {err}')
    m = re.search(r'\{.*\}|\[.*\]', txt, re.S)
    j = json.loads(m.group(0) if m else txt)
    return j if isinstance(j, dict) else {'items': j}


def _obj(props, req=None):
    return {'type': 'object', 'properties': props, 'required': req or list(props),
            'additionalProperties': False}


def _arr(item):
    return {'type': 'array', 'items': item}


S_STR, S_INT, S_BOOL = {'type': 'string'}, {'type': 'integer'}, {'type': 'boolean'}
S_GRADE = {'type': 'string', 'enum': GRADES}

SCHEMA_SCOUT = _obj({'items': _arr(_obj({'no': S_INT, 'relevant': S_INT, 'total': S_INT, 'note': S_STR}))})
SCHEMA_OPINION = _obj({'items': _arr(_obj({
    'no': S_INT, 'grade': S_GRADE, 'confidence': S_INT, 'reason': S_STR,
    'evidence': _arr({'type': 'string', 'enum': EVIDENCE}), 'data_issue': S_STR,
    'price': S_INT, 'title': S_STR, 'keywords': _arr(S_STR), 'caution': S_STR}))})
SCHEMA_DEBATE = _obj({'items': _arr(_obj({
    'no': S_INT, 'grade': S_GRADE, 'changed': S_BOOL, 'reason': S_STR, 'rebuttal': S_STR}))})
SCHEMA_VERDICT = _obj({'items': _arr(_obj({
    'no': S_INT, 'grade': S_GRADE, 'confidence': S_INT, 'reason': S_STR, 'ruling': S_STR,
    'price': S_INT, 'title': S_STR, 'keywords': _arr(S_STR), 'caution': S_STR}))})


# ── 회의 자료 ──────────────────────────────────────────────────
def _card(no, x, scout=None):
    """에이전트에게 보여 줄 상품 한 장 — 숫자는 코드가 만든 그대로."""
    c = {
        'no': no, '상품': x['name'], '코스트코번호': x['costco_no'], '매장가': x['store'],
        '블로그_최근7일': x['blog7'], '블로그_이전7일': x['blog_prev7'],
        '유튜브영상': x['yt_count'], '유튜브조회': x['yt_views'],
        '검색추세(최근2주÷앞6주)': x['trend'], '월간검색수': x['volume'], '경쟁도': x['comp'],
        '우리판매_14일': x['sales_recent'], '우리판매_직전14일': x['sales_prev'], '파는사용자': x['sellers'],
        '공식추천가': x['suggest_price'], '우리판매가': x['our_price'],
        '예상마진': x['margin'], '마진율': x['margin_rate'], '냉장냉동': x['cold'],
        '점수': x['score'], '글예시': [s['text'] for s in x.get('_evid', x['samples'])[:6]],
    }
    if x.get('blog_capped'):
        c['데이터주의'] = '블로그 최근 글이 많아 이전 7일을 끝까지 세지 못함 — 언급 증가율을 믿지 말 것'
    if scout:
        c['조사원_관련글'] = f"{scout.get('relevant', 0)}/{scout.get('total', 0)} — {scout.get('note', '')}"
    return c


def _system(role):
    """공통 사업 맥락 + 역할 지시 — 내용은 trend_prompts에 있다(거기만 고치면 판단 기준이 바뀐다)."""
    return P.domain() + role


# ── 0. 조사원 — 글 관련성 ────────────────────────────────────────
def scout(cl, rows, log):
    lines = []
    for i, x in enumerate(rows, 1):
        ev = [s['text'] for s in x.get('_evid', x['samples'])[:8]]
        if ev:
            lines.append(json.dumps({'no': i, '상품': x['name'], '글': ev}, ensure_ascii=False))
    if not lines:
        return {}
    j = _claude_json(cl, MODEL_SCOUT, _system(P.ROLE_SCOUT), '\n'.join(lines), SCHEMA_SCOUT, effort='low')
    out = {int(a['no']): a for a in j.get('items', [])}
    log(f'조사원: 글 관련성 {len(out)}개 판정')
    return out


# ── 1. 독립 의견 ─────────────────────────────────────────────────
def opinions(cl, gk, cards, log):
    user = '아래 상품 각각을 평가해 items 배열로 답하라.\n' + '\n'.join(json.dumps(c, ensure_ascii=False) for c in cards)
    a = _claude_json(cl, MODEL_ANALYST, _system(P.ROLE_ANALYST_A), user, SCHEMA_OPINION)
    a = {int(x['no']): x for x in a.get('items', [])}
    log(f'분석가 A(Claude Sonnet): {len(a)}개 의견')
    sys_b = _system(P.ROLE_ANALYST_B)
    b, b_name = {}, ''
    if gk:
        try:
            b = _gemini_json(gk, sys_b + P.GEMINI_OPINION_FORMAT, user)
            b_name = 'Gemini'
        except Exception as e:
            log(f'분석가 B Gemini 실패 → Claude Haiku로 대신: {e}')
    if not b:
        b = _claude_json(cl, MODEL_SCOUT, sys_b, user, SCHEMA_OPINION)
        b_name = 'Claude Haiku'
    b = {int(x.get('no', 0)): x for x in b.get('items', []) if isinstance(x, dict)}
    for x in b.values():
        if x.get('grade') not in GRADES:
            x['grade'] = '관찰'
    log(f'분석가 B({b_name}): {len(b)}개 의견')
    return a, b, b_name


def disputed(a, b):
    """토론할 상품 — 등급이 다르거나, 주력·시도인데 데이터 이상이 지적됐거나, 주력인데 확신이 낮은 것.

    데이터 이상 지적만으로 다 올리면 안 된다 — 분석가 B(감사관)는 거의 모든 상품에 무언가를 적어서,
    둘 다 '관찰'로 합의한 상품까지 검증관(Opus)에게 가 비용만 늘었다(10/26 시험: 20개 전부 판정).
    관찰·제외로 합의한 상품은 이상이 있어도 결론이 바뀌지 않는다.
    """
    out = []
    for no in sorted(set(a) | set(b)):
        x, y = a.get(no) or {}, b.get(no) or {}
        up = bool({x.get('grade'), y.get('grade')} & {'주력', '시도'})
        issue = (x.get('data_issue') or '').strip() or (y.get('data_issue') or '').strip()
        if (x.get('grade') != y.get('grade') or (issue and up)
                or ('주력' in (x.get('grade'), y.get('grade')) and min(x.get('confidence', 3), y.get('confidence', 3)) <= 2)):
            out.append(no)
    # 등급 차이가 큰 것부터
    out.sort(key=lambda n: -abs(_RANK.get((a.get(n) or {}).get('grade'), 1) - _RANK.get((b.get(n) or {}).get('grade'), 1)))
    return out[:MAX_DISPUTE]


# ── 2. 토론 ──────────────────────────────────────────────────────
def debate(cl, gk, cards, a, b, nos, b_name, log):
    by_no = {c['no']: c for c in cards}

    def _brief(me, other):
        return '\n'.join(json.dumps({'카드': by_no[n], '내_의견': me.get(n), '상대_의견': other.get(n)},
                                    ensure_ascii=False) for n in nos)
    ask = '토론할 상품(items 배열로 답하라):\n'
    ra = _claude_json(cl, MODEL_ANALYST, _system(P.ROLE_ANALYST_A + P.ROLE_DEBATE), ask + _brief(a, b), SCHEMA_DEBATE)
    ra = {int(x['no']): x for x in ra.get('items', [])}
    rb = {}
    if b_name == 'Gemini':
        try:
            rb = _gemini_json(gk, _system(P.ROLE_ANALYST_B + P.ROLE_DEBATE) + P.GEMINI_DEBATE_FORMAT, ask + _brief(b, a))
        except Exception as e:
            log(f'분석가 B 토론 실패 → Claude Haiku로 대신: {e}')
    if not rb:
        rb = _claude_json(cl, MODEL_SCOUT, _system(P.ROLE_ANALYST_B + P.ROLE_DEBATE), ask + _brief(b, a), SCHEMA_DEBATE)
    rb = {int(x.get('no', 0)): x for x in rb.get('items', []) if isinstance(x, dict)}
    moved = sum(1 for d in list(ra.values()) + list(rb.values()) if d.get('changed'))
    log(f'토론: {len(nos)}개 상품, 의견 수정 {moved}건')
    return ra, rb


# ── 3. 판정 (검증관 + 도구) ───────────────────────────────────────
TOOLS = [
    {'name': 'blog_posts',
     'description': ("네이버 블로그에서 '코스트코 <상품 키워드>' 최신 글 10개(날짜·제목·요약)를 가져온다. "
                     "글이 정말 그 상품 이야기인지, 최근에 늘었는지 직접 확인할 때 쓴다."),
     'input_schema': _obj({'no': S_INT}), 'eager_input_streaming': True},
    {'name': 'keyword_volume',
     'description': ("네이버 검색광고 키워드 도구로 키워드의 월간 검색수·경쟁도와 연관 키워드 상위 5개를 가져온다. "
                     "카드의 상품 키워드가 어색해서 검색수가 작게 나왔을 수 있을 때 다른 표현으로 다시 확인한다. "
                     "no에는 확인하려는 상품 번호를 넣는다 — 그 키워드와 정확히 같은 줄의 검색수는 규칙 검사에도 쓰인다."),
     'input_schema': _obj({'no': S_INT, 'keyword': S_STR}), 'eager_input_streaming': True},
    {'name': 'sales_history',
     'description': '우리 판매자들의 이 상품 주별 판매 수량(최근 8주, 오래된 주부터)을 가져온다.',
     'input_schema': _obj({'no': S_INT}), 'eager_input_streaming': True},
]


def _run_tool(name, inp, rows, k):
    import trend_research as tr
    if name in ('blog_posts', 'sales_history'):
        no = inp.get('no')
        if not isinstance(no, int) or not 1 <= no <= len(rows):
            return {'error': f'no는 1~{len(rows)} 사이 정수여야 한다'}
        x = rows[no - 1]
        if name == 'blog_posts':
            j = tr._naver('blog', '코스트코 ' + x['kw'], 1, k['open'])
            return {'query': '코스트코 ' + x['kw'], 'total': j.get('total', 0), 'posts': [
                {'date': it.get('postdate', ''), 'title': tr._strip(it.get('title'))[:80],
                 'desc': tr._strip(it.get('description'))[:120]} for it in (j.get('items') or [])[:10]]}
        return {'weeks': weekly_sales(x['costco_no'])}
    if name == 'keyword_volume':
        kw = str(inp.get('keyword') or '').strip()
        if not kw:
            return {'error': 'keyword가 비었다'}
        if not all(k['ad']):
            return {'error': '검색광고 키가 없다'}
        from naver_api.keywords import keyword_tool
        rows_, err = keyword_tool(*k['ad'], [kw.replace(' ', '')])
        if err and not rows_:
            return {'error': str(err)[:200]}
        top = sorted(rows_ or [], key=lambda r: -int(r.get('총검색량', 0) or 0))[:5]
        # 정확히 그 키워드인 줄의 검색수는 코드가 조회한 사실이다 — 그 상품의 '확인된 검색수'로 남겨
        #   규칙 검사(월 검색 300 미만)가 카드의 어색한 키워드 값 대신 쓰게 한다(보이러→보이로: 20→16,280)
        no = inp.get('no')
        exact = [int(r.get('총검색량', 0) or 0) for r in rows_ or []
                 if str(r.get('키워드') or '').replace(' ', '') == kw.replace(' ', '')]
        if exact and isinstance(no, int) and 1 <= no <= len(rows):
            x = rows[no - 1]
            if exact[0] > int(x.get('volume_checked') or 0):
                x['volume_checked'], x['volume_kw'] = exact[0], kw
        return {'rows': [{'키워드': r.get('키워드'), '월간검색수': r.get('총검색량'), '경쟁도': r.get('경쟁도')} for r in top]}
    return {'error': f'모르는 도구: {name}'}


def _valid(name, inp):
    """스트리밍으로 받은 도구 입력은 잘려 올 수 있다 — 스키마대로인지 직접 본다."""
    if not isinstance(inp, dict):
        return False
    if name in ('blog_posts', 'sales_history'):
        return isinstance(inp.get('no'), int)
    if name == 'keyword_volume':
        return isinstance(inp.get('keyword'), str) and isinstance(inp.get('no'), int)
    return False


def judge(cl, cards, rows, k, a, b, ra, rb, nos, record, log):
    by_no = {c['no']: c for c in cards}
    sys_ = _system(P.ROLE_JUDGE + P.track_record_note(record))
    user = ('판정할 상품(items 배열로 답하라):\n' + '\n'.join(json.dumps({
        '카드': by_no[n], 'A_처음': a.get(n), 'B_처음': b.get(n), 'A_토론': ra.get(n), 'B_토론': rb.get(n)},
        ensure_ascii=False) for n in nos))
    msgs = [{'role': 'user', 'content': user}]
    calls = 0
    for _ in range(JUDGE_TURNS):
        _gate()
        with cl.messages.stream(model=MODEL_JUDGE, max_tokens=32000, system=sys_, messages=msgs, tools=TOOLS,
                                output_config={'effort': 'high',
                                               'format': {'type': 'json_schema', 'schema': SCHEMA_VERDICT}}) as s:
            msg = s.get_final_message()
        _track(MODEL_JUDGE, msg.usage)
        if msg.stop_reason == 'tool_use':
            msgs.append({'role': 'assistant', 'content': msg.content})
            res = []
            for blk in msg.content:
                if blk.type != 'tool_use':
                    continue
                calls += 1
                if not _valid(blk.name, blk.input):
                    res.append({'type': 'tool_result', 'tool_use_id': blk.id, 'is_error': True,
                                'content': 'INVALID_JSON: 입력이 스키마와 맞지 않는다. 다시 호출하라.'})
                    continue
                try:
                    out = _run_tool(blk.name, blk.input, rows, k)
                except Exception as e:
                    out = {'error': str(e)[:200]}
                res.append({'type': 'tool_result', 'tool_use_id': blk.id,
                            'content': json.dumps(out, ensure_ascii=False)})
            msgs.append({'role': 'user', 'content': res})
            continue
        j = json.loads(_text(msg))
        log(f'검증관(Claude Opus): {len(j.get("items", []))}개 판정 · 근거 재조회 {calls}회')
        return {int(x['no']): x for x in j.get('items', [])}
    raise RuntimeError('검증관이 도구 호출 한도 안에 판정을 끝내지 못했습니다')


# ── 4. 규칙 검사 ─────────────────────────────────────────────────
def _season_over(name):
    """택배 2~3일 + 고객이 미리 사는 기간을 빼면 행사일 열흘 전에 판매 시즌이 끝난다."""
    m = date.today().month * 100 + date.today().day
    if re.search(r'할로윈|핼러윈|halloween', name, re.I):
        return not (901 <= m <= 1020)
    if re.search(r'크리스마스|성탄|christmas|xmas', name, re.I):
        return not (1001 <= m <= 1215)
    return False


def apply_rules(x, v):
    """AI 판정 위에 코드 하한선을 씌운다. 반환: 적용한 규칙 목록."""
    rules = []
    g = v.get('grade', '관찰')

    def cap(to, why):
        nonlocal g
        if _RANK.get(g, 1) > _RANK[to]:
            g = to
            rules.append(why)
    if x['margin'] < 0:
        cap('시도', f"예상 마진 {x['margin']:,}원(마이너스) — 주력 금지")
    vol = max(int(x['volume'] or 0), int(x.get('volume_checked') or 0))
    if vol < 300 and x['sales_recent'] < 5:
        cap('관찰', f"월간 검색 {vol}회·우리 판매 {x['sales_recent']}건 — 찾는 사람이 적음")
    if _season_over(x['name'] + ' ' + x.get('full_name', '')):
        cap('관찰', '시즌이 끝났거나 끝나 감')
    v['grade'] = g
    p = int(v.get('price') or 0)
    lo, hi = x['store'] + 3000, max(x['store'] * 2, x['store'] + 20000)
    if x['store'] and not lo <= p <= hi:
        v['price'] = x['suggest_price']
        rules.append(f'추천가 {p:,}원이 범위({lo:,}~{hi:,}) 밖 → 공식 추천가로')
    return rules


# ── 판정 성적 (2주 뒤 실제 판매) ───────────────────────────────────
def _sales_between(start, end):
    """{코스트코번호: 수량} — 결제일 start 이상 end 미만, 취소 제외."""
    import glob, os, sqlite3
    from collections import defaultdict
    from db_core import DATA_DIR
    out = defaultdict(int)
    for f in glob.glob(os.path.join(DATA_DIR, '*.db')):
        u = os.path.basename(f)[:-3]
        if u in ('auth', 'admin') or '.' in u:
            continue
        try:
            c = sqlite3.connect(f'file:{f}?mode=ro', uri=True)
            for cc, q in c.execute(
                    "SELECT COALESCE(d.costco_no,''), h.qty FROM order_history h "
                    "LEFT JOIN daily_orders d ON d.order_no=h.order_no "
                    "WHERE substr(h.order_date,1,10)>=? AND substr(h.order_date,1,10)<? "
                    "AND COALESCE(h.status,'') NOT LIKE '%취소%'", (start, end)):
                cc = str(cc or '').strip()
                if cc:
                    out[cc] += int(q or 1)
            c.close()
        except Exception:
            continue
    return out


def weekly_sales(pn, weeks=8):
    t = date.today()
    res = []
    for w in range(weeks, 0, -1):
        s, e = t - timedelta(days=7 * w), t - timedelta(days=7 * (w - 1))
        res.append({'주': s.isoformat(), '수량': _sales_between(s.isoformat(), e.isoformat()).get(pn, 0)})
    return res


def track_record():
    """14일 이상 지난 보고서의 주력·시도 판정이 실제 판매 증가로 이어졌는지.

    '적중' = 보고서 다음 14일 판매가 그 전 14일보다 늘었다. 관리자가 승인한 상품만 센다
    (승인 안 한 상품은 팔아 보지 않았으니 맞았는지 알 수 없다). 승인 기록이 없으면 전부.
    """
    import trend_research as tr
    cut = (date.today() - timedelta(days=14)).isoformat()
    stats = {'주력': [0, 0], '시도': [0, 0]}
    for meta in tr.list_reports(limit=12):
        rep = tr.get_report(meta['id'])
        if not rep or rep.get('week', '9') > cut or not rep.get('council'):
            continue
        d0 = date.fromisoformat(rep['week'])
        before = _sales_between((d0 - timedelta(days=14)).isoformat(), d0.isoformat())
        after = _sales_between(d0.isoformat(), (d0 + timedelta(days=14)).isoformat())
        dec = tr.get_decisions(meta['id'])
        for x in rep.get('items', []):
            g = (x.get('ai') or {}).get('grade')
            if g not in stats or (dec and dec.get(x['costco_no'], {}).get('decision') != 'approve'):
                continue
            stats[g][1] += 1
            if after.get(x['costco_no'], 0) > before.get(x['costco_no'], 0):
                stats[g][0] += 1
    parts = [f'{g} {h}/{n} 적중' for g, (h, n) in stats.items() if n]
    return ', '.join(parts)


# ── 전체 회의 ────────────────────────────────────────────────────
def run_council(rows, k, log=print):
    """rows(점수순 후보) → 각 행에 ai(최종)·council(회의록) 을 채운다. 반환: 회의 요약."""
    cl = _client(k['anthropic'])
    t0 = datetime.now()
    log(f'🧑‍⚖️ 회의 시작 — 후보 {len(rows)}개')
    sc = scout(cl, rows, log)
    cards = [_card(i, x, sc.get(i)) for i, x in enumerate(rows, 1)]
    a, b, b_name = opinions(cl, k['gemini'], cards, log)
    nos = disputed(a, b)
    log(f'의견 일치 {len(rows) - len(nos)}개 · 엇갈림/이상 {len(nos)}개')
    ra = rb = v = {}
    if nos:
        ra, rb = debate(cl, k['gemini'], cards, a, b, nos, b_name, log)
        try:
            record = track_record()
        except Exception:
            record = ''
        v = judge(cl, cards, rows, k, a, b, ra, rb, nos, record, log)
    n_rules = 0
    for i, x in enumerate(rows, 1):
        A, B = a.get(i) or {}, b.get(i) or {}
        if i in v:
            fin = dict(v[i])
            how = '검증관 판정'
        else:
            # 일치한 상품 — 분석가 A의 글(구조화 출력)을 쓰고, 추천가는 두 의견의 평균
            fin = {kk: A.get(kk) for kk in ('grade', 'confidence', 'reason', 'price', 'title', 'keywords', 'caution')}
            fin['grade'] = A.get('grade') or B.get('grade') or '관찰'
            ps = [int(p) for p in (A.get('price'), B.get('price')) if isinstance(p, (int, float)) and p > 0]
            fin['price'] = int(sum(ps) / len(ps) // 10 * 10) if ps else x['suggest_price']
            fin['ruling'] = ''
            how = '합의'
        rules = apply_rules(x, fin)
        n_rules += bool(rules)
        x['ai'] = fin
        x['council'] = {
            'how': how, 'scout': sc.get(i, {}),
            'a': {kk: A.get(kk) for kk in ('grade', 'confidence', 'reason', 'evidence', 'data_issue')},
            'b': {kk: B.get(kk) for kk in ('grade', 'confidence', 'reason', 'evidence', 'data_issue')},
            'a_debate': ra.get(i), 'b_debate': rb.get(i), 'ruling': fin.get('ruling', ''), 'rules': rules,
        }
    summ = {'prompt_version': P.PROMPT_VERSION, 'members': {'scout': MODEL_SCOUT, 'a': MODEL_ANALYST, 'b': b_name, 'judge': MODEL_JUDGE},
            'agreed': len(rows) - len(nos), 'disputed': len(nos), 'rules_applied': n_rules,
            'seconds': int((datetime.now() - t0).total_seconds())}
    log(f"회의 끝 — 합의 {summ['agreed']} · 판정 {summ['disputed']} · 규칙 적용 {n_rules} ({summ['seconds']}초)")
    return summ

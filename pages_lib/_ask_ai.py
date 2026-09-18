"""🤖 화면 맥락 질문 — 지금 보고 있는 표를 그대로 AI에게 물어본다.

왜 이 방식인가:
  "이 수량이 왜 5개지"처럼, 답이 **화면에 이미 있는 숫자들의 관계**에 있는
  질문이 계속 나온다. 사람이 코드를 뒤져야 알 수 있는 계산 규칙(주문 수량 =
  주문건수 × 묶음배수 같은 것)을 몰라서 생기는 질문이다.

  AI에게 DB 조회 권한을 주는 방법도 있지만 그러면 호출이 여러 번 돌아 비용이
  뛰고, 어디까지 읽을지 매번 제한을 걸어야 한다. **화면에 이미 떠 있는 것만**
  넘기면 비용이 예측 가능하고 열람 범위도 넓어지지 않는다. 화면이 답을 품고
  있는데 읽는 법을 모르는 것이 문제의 대부분이다.

쓰는 법:
    from pages_lib import _ask_ai
    _ask_ai.render({'표': rows, '메모': '...'}, key="rs_ov", settings=settings)
"""
import json

import streamlit as st

#: 도메인 규칙 — 이걸 안 주면 AI가 숫자 관계를 추측한다. 화면 열 이름과
#  실제 계산식을 같이 적어 둬야 "왜 5개인가"에 근거를 대고 답할 수 있다.
_SYSTEM = (
    "너는 코스트코 구매대행 정산 시스템(코코비즈)의 운영 도우미다. "
    "관리자가 지금 보고 있는 화면 데이터를 JSON으로 받고, 그 데이터만 근거로 "
    "한국어로 답한다.\n\n"
    "이 시스템의 계산 규칙:\n"
    "- 수량 단위는 **소분 단위**(1팩을 N개로 나눠 파는 상품은 낱개 기준)다.\n"
    "- 구입 수량(units_in) = 영수증 수량(팩) × 소분수(split_qty)\n"
    "- 주문 수량(units_used) = 주문 수량(qty) × 묶음배수(pack)\n"
    "  · 묶음배수는 '1주문이 몇 개를 소비하나'다. 제품DB의 pack_multiplier가 "
    "1 이상이면 그 값을, 0(미지정)이면 상품명의 'x N개'에서 뽑는다.\n"
    "  · 소분수는 '1팩을 몇 개로 나눠 파나'다.\n"
    "  · 그래서 주문이 1건이어도 묶음배수가 5면 주문 수량은 5가 된다.\n"
    "- 청구 근거(via/source): 영수증=그날 영수증 단가, 재고=이전 구입분(재고 lot), "
    "온라인몰=코스트코가 고객에게 직배송, 직접청구=주문 없이 관리자가 배정, "
    "수동=관리자가 금액 지정.\n"
    "- 재고 출고·온라인몰 건은 '그날 영수증으로 산 물건'이 아니라서 영수증 "
    "대조에서 제외된다.\n"
    "- 예치금 잔액 = 누적 입금 − 예치금 차감 (± 관리자 조정). 청구 합계에서 "
    "빼는 것이 아니다 — 계좌입금·미입금 청구는 예치금과 무관하다.\n\n"
    "업무 흐름(질문이 어느 단계 일인지 먼저 짚어라):\n"
    "  ① 주문 수집 → ② 송장 등록·발송처리(**이때 사용자별 재고가 차감된다**) → "
    "③ 영수증 업로드·저장 → ④ 자동배치 미리보기(매칭) → ⑤ 정산 요청"
    "(settle_item에 기록, **이때 배정 대기가 줄어든다**) → ⑥ 청구(상태만 바뀐다) → "
    "⑦ 입금완료 또는 예치금 차감\n"
    "  · 청구·입금은 재고를 바꾸지 않는다. 재고가 줄어드는 시점은 ②와 ⑤뿐이다.\n"
    "  · 온라인몰 직배송으로 지정한 건은 **미리보기를 다시 눌러야** 배치에 실린다.\n"
    "  · 다시 정산할 때 '통째 교체'를 고르지 않으면 이번 배치에 없는 옛 품목이 "
    "그대로 남아 금액이 커진다.\n"
    "  · 입금완료(paid) 청구서는 금액이 동결된다 — 품목을 더해도 청구액이 늘지 "
    "않는다. 입금완료 취소를 먼저 해야 한다.\n\n"
    "화면 이름: 영수증 정산 · 정산·청구(일별/예치금/미입금자/월별) · "
    "재고 관리(전체 재고/정산 장부/반품 대상/고객 반품) · 일일 주문 수집 · "
    "제품 DB · 송장번호 · 자동화 · 관리자.\n\n"
    "답변 규칙:\n"
    "1. **데이터에 없는 값은 지어내지 마라.** 모르면 '이 화면 데이터로는 알 수 "
    "없습니다'라고 하고, 어느 화면을 봐야 하는지 알려 줘라.\n"
    "2. 숫자를 말할 때는 **어떤 값에서 어떻게 나왔는지** 식으로 보여라 "
    "(예: 1건 × 묶음배수 5 = 5개).\n"
    "3. 원인이 설정 문제로 보이면 **어디를 고쳐야 하는지** 짚어라.\n"
    "4. 5문장 안으로 답하고, 필요할 때만 짧은 목록을 쓴다. 인사말은 쓰지 마라."
)

#: 컨텍스트 상한 — 넘치면 잘라 보낸다. 표가 길면 비용이 그만큼 늘어난다.
_MAX_CTX = 12000


def _pack(context):
    """컨텍스트를 JSON 문자열로. 길면 자르고 잘렸다고 알린다."""
    try:
        _t = json.dumps(context, ensure_ascii=False, default=str, indent=None)
    except Exception:
        _t = str(context)
    if len(_t) > _MAX_CTX:
        return _t[:_MAX_CTX] + "\n…(이하 생략 — 표가 길어 앞부분만 보냈습니다)", True
    return _t, False


def render(context, *, key, settings=None, username='',
           title="🤖 AI에게 물어보기 — 이 화면 데이터로 답합니다",
           hint='', expanded=False):
    """화면 데이터를 그대로 넘겨 질문하는 패널.

    context: 화면에 떠 있는 값(dict/list). 그대로 JSON으로 보낸다.
    key:     위젯 키 접두사. 화면마다 달라야 한다.
    """
    with st.expander(title, expanded=expanded):
        _ctx, _cut = _pack(context)
        st.caption("지금 화면에 보이는 값만 AI에게 보냅니다 — DB를 따로 뒤지지 "
                   "않으므로 비용이 예측 가능하고, 열람 범위도 넓어지지 않습니다. "
                   + (hint or ""))
        if _cut:
            st.caption("⚠️ 표가 길어 **앞부분만** 보냈습니다. 특정 상품을 물으려면 "
                       "화면에서 먼저 걸러 주세요.")

        _q = st.text_input("질문", key=f"{key}_ask_q",
                           placeholder="예: 주문은 1건인데 주문 수량이 왜 5개인가요?")
        _c1, _c2 = st.columns([1, 3])
        if _c1.button("🤖 물어보기", key=f"{key}_ask_go", type="primary",
                      disabled=not str(_q or '').strip(), use_container_width=True):
            try:
                import ai_service
                _ak, _ = ai_service.get_ai_keys(settings)
            except Exception as _e:
                st.error(f"AI 설정을 읽지 못했습니다: {_e}")
                return
            if not _ak:
                st.warning("Anthropic API 키가 없습니다 — 설정 탭 › 🤖 AI 설정에서 "
                           "넣어 주세요.")
                return
            try:
                if username:
                    ai_service.set_current_user(username)
            except Exception:
                pass
            with st.spinner("AI가 화면 데이터를 읽는 중..."):
                _txt, _err = ai_service.claude_complete(
                    _ak, _SYSTEM,
                    "[화면 데이터]\n" + _ctx + "\n\n[질문]\n" + str(_q).strip(),
                    max_tokens=700, model=ai_service.VISION_MODEL,
                    thinking={"type": "disabled"}, feature='ask')
            if _err or not _txt:
                st.error(f"답변 실패: {_err or '빈 응답'}")
            else:
                st.session_state[f"{key}_ask_a"] = {'q': str(_q).strip(), 'a': _txt}
                st.rerun()
        _c2.caption("질문 1건에 대략 **10~30원**입니다(표 길이에 따라 다름). "
                    "실제 금액은 관리자 › AI 사용량에서 '질문'으로 집계됩니다.")

        _prev = st.session_state.get(f"{key}_ask_a")
        if _prev:
            st.markdown(f"**Q.** {_prev['q']}")
            st.info(_prev['a'])
            if st.button("🗑 답변 지우기", key=f"{key}_ask_clr"):
                st.session_state.pop(f"{key}_ask_a", None)
                st.rerun()
            st.caption("⚠️ AI 답변은 **화면 데이터만 보고 쓴 설명**입니다. "
                       "금액을 바꾸기 전에 실제 화면·원장으로 확인하세요.")

# -*- coding: utf-8 -*-
"""화면 한 번 그리는 동안만 쓰는 조회 캐시.

Streamlit은 클릭할 때마다 화면 전체를 처음부터 다시 실행한다. 영수증 정산은 그
한 번에 같은 사용자·같은 날짜의 발송건을 89번, 직접구매 여부를 198번 다시 읽어
클릭마다 1초 넘게 걸렸다(10/25 실측). 화면이 시작될 때 begin()을 부르면 그
실행 동안만 같은 조회를 한 번만 하고, 다음 클릭에서 begin()이 다시 비운다.

begin()을 부르지 않은 곳(API·자동 작업)에서는 아무것도 캐시하지 않는다 —
오래 떠 있는 서버에서 옛 값이 남으면 안 되기 때문이다. 세션마다 스크립트가
자기 스레드에서 돌므로 캐시도 스레드별로 둔다.
"""
import functools
import threading

_tl = threading.local()


def begin():
    """이번 화면 실행 동안 캐시를 켠다(이전 값은 버린다)."""
    _tl.cache = {}


def clear():
    """DB에 쓴 직후 — 같은 실행 안에서 바뀐 값을 다시 읽어야 할 때."""
    if getattr(_tl, 'cache', None) is not None:
        _tl.cache = {}


def end():
    _tl.cache = None


def _copy(v):
    # 호출하는 쪽이 받은 목록·행을 고쳐 쓰는 곳이 있어 얕은 복사본을 준다
    if isinstance(v, list):
        return [dict(x) if isinstance(x, dict) else x for x in v]
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, set):
        return set(v)
    return v


def memo(fn):
    @functools.wraps(fn)
    def inner(*a, **kw):
        cache = getattr(_tl, 'cache', None)
        if cache is None:
            return fn(*a, **kw)
        try:
            key = (fn.__module__, fn.__qualname__, a, tuple(sorted(kw.items())))
            hash(key)
        except TypeError:
            return fn(*a, **kw)
        if key not in cache:
            cache[key] = fn(*a, **kw)
        return _copy(cache[key])
    return inner

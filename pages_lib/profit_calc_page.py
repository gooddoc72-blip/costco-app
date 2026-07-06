"""💰 수익 계산 페이지 — 서브패키지(pages_lib/profit_calc)로 분리됨.

하위호환 shim: app.py 가 `profit_calc_page.render` / `._set_cache_helpers` 를
그대로 호출하도록 page 모듈에서 re-export 한다. 기능별 모듈은 profit_calc/ 하위에 위치.
"""
from pages_lib.profit_calc.page import render, _set_cache_helpers  # noqa: F401

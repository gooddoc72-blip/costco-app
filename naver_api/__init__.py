"""네이버 API 패키지 — 기존 `import naver_api` 100% 호환 재수출 레이어.
기능별 모듈: core(토큰) / orders(주문·발송·정산) / products(상품·가격) /
keywords(순위·검색량·데이터랩) / messaging(카카오)"""
from .core import (
    get_token,
)
from .orders import (
    get_new_orders,
    get_last_status_dist,
    ship_orders,
    get_settlement_history,
    get_daily_settlement,
    get_purchase_decisions,
    get_daily_settlements_range,
    _build_params,
    _probe_settlement,
    debug_settlement_response,
    register_cj_order,
    fetch_order_details_by_ids,
    _last_status_dist,
    _SETTLE_CASE_PATH,
    _SETTLE_DAILY_PATH,
    _SETTLEMENT_PATH_CANDIDATES,
)
from .products import (
    _get_category_cache_path,
    load_naver_category_cache,
    search_naver_categories,
    upload_product_image,
    upload_images_batch,
    resize_square_bytes,
    register_product,
    search_recommend_tags,
    filter_restricted_tags,
    build_seller_tags,
    calc_min_price,
    get_product_list,
    debug_first_product_response,
    get_products_by_nos,
    _format_naver_err,
    _strip_tag_fields_deep,
    _sanitize_for_put,
    resolve_origin_product_no,
    update_product_price,
    update_product_name,
    update_product_tags,
    _READONLY_KEYS,
    _TAG_FIELDS_TO_STRIP,
)
from .keywords import (
    get_last_match_info,
    check_keyword_rank,
    keyword_tool,
    naver_shopping_search,
    naver_autocomplete,
    _norm_kw,
    keyword_volumes,
    keyword_research,
    datalab_search_trend,
    datalab_keyword_gender_age,
    _last_match_info,
)
from .messaging import (
    send_kakao,
    refresh_kakao_token,
    get_kakao_token_by_code,
)

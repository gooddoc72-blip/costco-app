"""
DB 레이어 — 하위 호환 re-export
기존 코드가 'from db import ...' / 'import db' 로 사용하던 모든 심볼을
도메인별 모듈에서 가져와 그대로 노출.

실제 로직은 아래 모듈에 있음:
  db_core.py     — 경로 상수, get_user_db
  db_auth.py     — 인증 / 세션
  db_products.py — 공유·개인 제품
  db_orders.py   — 주문 / 발송 이력
  db_stats.py    — 통계 / 영수증 / 가격 이력
  db_ranks.py    — 키워드 순위 추적
"""

from db_core import (
    BASE_DIR, DATA_DIR, AUTH_DB,
    get_user_db,
)

from db_auth import (
    hash_pw, _sha256,
    init_auth_db,
    check_login,
    get_global_setting, set_global_setting,
    register_user, get_pending_users,
    approve_user, reject_user,
    get_all_users, add_user, delete_user,
    change_password, get_user_info,
    create_session, get_session_user, delete_session,
)

from db_products import (
    get_shared_products,
    _upsert_shared_internal,
    upsert_shared_store_price,
    upsert_shared_online_price,
    upsert_shared_product,
    delete_shared_product,
    get_product_detail,
    init_user_db,
    get_all_settings, get_setting, set_setting,
    _ensure_products_columns,
    get_all_products,
    link_naver_to_shared, unlink_naver_from_shared,
    set_naver_origin_pno,
    bulk_update_category,
    upsert_user_private,
    get_all_products_merged,
    upsert_product,
)

from db_orders import (
    save_daily_orders,
    get_daily_orders,
    recalc_daily_orders_for_products,
    get_saved_dates,
    save_order_history,
    ACTIVE_ORDER_STATUSES,
    get_active_orders,
    update_order_status_bulk,
    _NAVER_EXCEL_COLUMNS,
    _db_row_to_naver_excel_row,
    active_orders_to_naver_excel_df,
    db_rows_to_orders_df,
    search_order_history,
)

from db_stats import (
    get_date_range_stats,
    get_monthly_stats,
    get_product_ranking,
    get_dashboard_kpi,
    get_cumulative_sales,
    get_daily_profit_trend,
    get_week_best_products,
    get_price_history_monthly,
    save_price_changes_to_history,
    get_price_change_history,
    save_receipt_items,
    get_recent_receipt_items,
    delete_receipt_items_by_date,
    get_receipt_dates,
)

from db_ranks import (
    _ensure_rank_tables,
    add_keyword_tracking,
    get_keyword_trackings,
    delete_keyword_tracking,
    save_rank_result,
    get_daily_ranks_in_month,
    get_yearly_rank_history,
    get_rank_drops,
    delete_trackings_bulk,
    get_rank_history,
    get_latest_ranks,
)

from db_shopping import (
    submit_shopping_list,
    get_recent_shopping_submissions,
    delete_shopping_submission,
)

from db_settlements import (
    save_naver_settlements,
    save_naver_settlements_from_csv,
    get_naver_settlements_by_date,
    get_naver_settlements_in_range,
    delete_naver_settlements_by_date,
)

from db_dispatch_log import (
    log_dispatch_success,
    get_dispatch_log_by_date,
    get_dispatched_orders_with_details,
    get_dispatch_dates,
)

from db_profit_calc import (
    save_profit_settlements,
    get_profit_settlements,
    get_saved_profit_dates,
    delete_profit_settlements,
    get_profit_history,
    save_settlement_override,
    get_settlement_overrides_map,
    delete_settlement_override,
)

"""저해상도(160px) 이미지 일괄 고해상도(1200px) 교체 — 1회성 백필.

목록 API로 수집된 기존 상품 이미지는 160px 썸네일로 저장돼 흐릿하다.
상세 API(products/{code})의 superZoom(1200px)으로 교체한다.

  · shared_products.image_url / extra_images 를 큰 URL로 갱신
  · data/images/{product_no}.jpg 로컬 캐시 재다운로드

사용:
    python backfill_hires_images.py            # 저해상도만 (기본, 15KB 미만)
    python backfill_hires_images.py --all      # 전체 재확인
    python backfill_hires_images.py --limit 50 # 앞 50건만 (테스트)
    python backfill_hires_images.py --dry-run  # 조회만, 변경 안 함
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costco_crawler as cc
from db_core import AUTH_DB

LOWRES_BYTES = 15_000


def _local_size(product_no: str) -> int:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = os.path.join(cc.DATA_DIR, "images", f"{product_no}{ext}")
        if os.path.exists(p):
            try:
                return os.path.getsize(p)
            except OSError:
                return 0
    return -1   # 로컬 파일 없음


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="저해상도 판정 없이 전체 재확인")
    ap.add_argument("--limit", type=int, default=0, help="처리 상한(테스트용)")
    ap.add_argument("--dry-run", action="store_true", help="조회만, 변경 없음")
    ap.add_argument("--sleep", type=float, default=0.1, help="상세 API 호출 간격(초)")
    args = ap.parse_args()

    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, product_no, image_url, local_image FROM shared_products "
        "WHERE product_no<>'' ORDER BY id"
    ).fetchall()

    # 대상 선별: --all 이 아니면 로컬 파일이 15KB 미만인 것만
    targets = []
    for r in rows:
        if args.all:
            targets.append(r)
        else:
            sz = _local_size(r["product_no"])
            if sz == -1 or sz < LOWRES_BYTES:   # 파일 없거나 저해상도
                targets.append(r)
    if args.limit:
        targets = targets[:args.limit]

    print(f"전체 {len(rows)}개 · 대상 {len(targets)}개"
          + (" [DRY-RUN]" if args.dry_run else ""))
    if not targets:
        print("갱신할 이미지가 없습니다.")
        return

    updated = failed = skipped = 0
    t0 = time.time()
    for i, r in enumerate(targets, 1):
        pno = r["product_no"]
        hi_main, hi_extra = cc.fetch_hires_images(pno)
        if not hi_main:
            failed += 1
            if args.dry_run or i % 100 == 0:
                print(f"  [{i}/{len(targets)}] {pno} 상세API 실패/이미지없음")
            time.sleep(args.sleep)
            continue

        if args.dry_run:
            print(f"  [{i}/{len(targets)}] {pno} → {hi_main[-40:]}")
            skipped += 1
            time.sleep(args.sleep)
            continue

        # 로컬 재다운로드 (byte-size 가드가 160px 캐시를 1200px으로 교체)
        local = cc.download_product_image(pno, hi_main)
        extra_json = ""
        try:
            import json as _json
            extra_json = _json.dumps(hi_extra, ensure_ascii=False) if hi_extra else ""
        except Exception:
            extra_json = ""
        conn.execute(
            "UPDATE shared_products SET image_url=?, local_image=?, extra_images=? "
            "WHERE id=?",
            (hi_main, local, extra_json, r["id"]),
        )
        updated += 1
        if i % 50 == 0:
            conn.commit()
            _el = time.time() - t0
            _eta = _el / i * (len(targets) - i)
            print(f"  [{i}/{len(targets)}] 갱신 {updated} 실패 {failed} "
                  f"· {_el:.0f}초 경과 · ETA {_eta:.0f}초")
        time.sleep(args.sleep)

    conn.commit()
    conn.close()
    print(f"\n완료 - 갱신 {updated} / 실패 {failed} / 건너뜀 {skipped} "
          f"/ {time.time()-t0:.0f}초")


if __name__ == "__main__":
    main()

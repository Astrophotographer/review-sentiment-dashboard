"""
데이터 정제(clean) 모듈
-----------------------
raw_reviews 중 아직 정제되지 않은 데이터를 읽어 다음 규칙을 적용한 뒤 clean_reviews 에 저장한다.
  1) 필수 필드 검증 (리뷰 텍스트 존재 여부)
  2) 텍스트 정규화 (공백/개행 정리)
  3) 별점 범위 검증 (1~5 벗어나면 NULL 처리)
  4) 날짜 형식 통일 (YYYY-MM-DD)
  5) 짧은 리뷰 필터링 (config.cleaning.min_review_length 미만이면 제외)
  6) 중복 처리 정책 적용 (skip / upsert)
"""
from .utils import normalize_text, normalize_date, normalize_rating, dedup_hash, detect_language, now_str


def clean_all(db, config, logger, dedup_policy: str = None):
    policy = dedup_policy or config.get("dedup_policy", "skip")
    min_len = config.get("cleaning", {}).get("min_review_length", 5)

    raw_rows = db.get_uncleaned_raw()
    logger.info(f"정제 대상 원본 리뷰: {len(raw_rows)}건")

    inserted, updated, skipped_short, skipped_dup = 0, 0, 0, 0

    for row in raw_rows:
        text = normalize_text(row["review_text"])
        if len(text) < min_len:
            skipped_short += 1
            db.mark_raw_cleaned(row["id"])
            continue

        rating = normalize_rating(row["rating"]) if row["rating"] is not None else None
        review_date = normalize_date(row["review_date"]) if row["review_date"] else None
        product = normalize_text(row["product"]) if row["product"] else None
        category = normalize_text(row["category"]) if "category" in row.keys() and row["category"] else None
        language = detect_language(text)
        h = dedup_hash(text, product)

        existing = db.clean_hash_exists(h)
        if existing and policy == "skip":
            skipped_dup += 1
            db.mark_raw_cleaned(row["id"])
            continue

        _id, action = db.upsert_clean(row["id"], text, rating, review_date, product, language, h, now_str(), category)
        if action == "inserted":
            inserted += 1
        else:
            updated += 1
        db.mark_raw_cleaned(row["id"])

    logger.info(
        f"정제 완료: 신규 {inserted}건, 갱신 {updated}건, "
        f"짧은 리뷰 제외 {skipped_short}건, 중복 스킵 {skipped_dup}건"
    )
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped_short": skipped_short,
        "skipped_dup": skipped_dup,
    }

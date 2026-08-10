"""
리뷰 데이터 수집(ingest) 모듈
-----------------------------
CSV/Excel 파일에서 리뷰를 읽어 raw_reviews 테이블에 저장한다.
필수 필드: review_text (리뷰 텍스트가 없는 행은 스킵)
선택 필드: rating(별점), review_date(작성일), product(제품명)
중복 처리 정책(skip/upsert)을 raw 단계에서도 1차 적용한다.
"""
import csv
import os
from .utils import normalize_text, normalize_date, normalize_rating, dedup_hash, now_str

TEXT_COLUMN_ALIASES = ["review_text", "text", "review", "content", "리뷰", "리뷰내용", "내용"]
RATING_ALIASES = ["rating", "star", "score", "별점"]
DATE_ALIASES = ["review_date", "date", "작성일", "날짜"]
PRODUCT_ALIASES = ["product", "product_name", "item", "제품", "제품명"]
CATEGORY_ALIASES = ["category", "product_category", "카테고리", "분류", "제품군"]


def _pick(row: dict, aliases):
    for key in row:
        if key and key.strip().lower() in [a.lower() for a in aliases]:
            return row[key]
    return None


def _read_rows(filepath: str):
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".csv",):
        with open(filepath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield row
    elif ext in (".xlsx", ".xls"):
        import openpyxl

        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h else "" for h in next(rows_iter)]
        for values in rows_iter:
            yield dict(zip(headers, values))
    else:
        raise ValueError(f"지원하지 않는 파일 형식입니다: {ext} (csv, xlsx만 지원)")


def import_file(db, config, logger, filepath: str, dedup_policy: str = None):
    """파일을 읽어 raw_reviews 에 저장. 반환: (총건수, 유효건수, 스킵건수)"""
    if not os.path.exists(filepath):
        logger.error(f"파일을 찾을 수 없습니다: {filepath}")
        return 0, 0, 0

    ext = os.path.splitext(filepath)[1].lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        logger.error(f"지원하지 않는 파일 형식입니다: '{ext}' (csv, xlsx만 지원합니다)")
        return 0, 0, 0

    policy = dedup_policy or config.get("dedup_policy", "skip")
    total, valid, skipped = 0, 0, 0

    logger.info(f"파일 로드: {filepath}")
    try:
        rows_iter = list(_read_rows(filepath))
    except Exception as e:  # noqa: BLE001 - 손상된 파일 등 어떤 이유로든 읽기 실패 시 안전하게 중단
        logger.error(f"파일을 읽는 중 오류가 발생하여 가져오기를 중단합니다: {e}")
        return 0, 0, 0

    for row in rows_iter:
        total += 1
        raw_text = _pick(row, TEXT_COLUMN_ALIASES)
        text = normalize_text(raw_text)
        if not text:
            skipped += 1
            continue

        rating = normalize_rating(_pick(row, RATING_ALIASES))
        review_date = normalize_date(_pick(row, DATE_ALIASES))
        product = normalize_text(_pick(row, PRODUCT_ALIASES)) or None
        category = normalize_text(_pick(row, CATEGORY_ALIASES)) or None

        h = dedup_hash(text, product)
        exists = db.raw_hash_exists(h)
        if exists and policy == "skip":
            skipped += 1
            continue

        if exists and policy == "upsert":
            db.upsert_raw(text, rating, review_date, product, os.path.basename(filepath), now_str(), h, category)
        else:
            db.insert_raw(text, rating, review_date, product, os.path.basename(filepath), now_str(), h, category)
        valid += 1

    logger.info(f"총 {total}건 감지, 유효 {valid}건, 스킵 {skipped}건 (중복/필수필드 누락, 정책={policy})")
    logger.info("raw 저장소에 저장 완료")
    return total, valid, skipped


def add_single_review(db, config, logger, text: str, rating=None, review_date=None, product=None, category=None):
    """CLI에서 리뷰 1건을 수동으로 추가한다 (관리자 등록 폼 등에서 활용)."""
    norm_text = normalize_text(text)
    if not norm_text:
        logger.error("리뷰 텍스트가 비어 있어 추가할 수 없습니다.")
        return None

    norm_rating = normalize_rating(rating) if rating is not None else None
    norm_date = normalize_date(review_date) if review_date else None
    norm_product = normalize_text(product) if product else None
    norm_category = normalize_text(category) if category else None

    h = dedup_hash(norm_text, norm_product)
    policy = config.get("dedup_policy", "skip")
    exists = db.raw_hash_exists(h)
    if exists and policy == "skip":
        logger.warning("동일한 리뷰가 이미 존재하여 스킵합니다 (dedup_policy=skip).")
        return None
    if exists and policy == "upsert":
        new_id, _ = db.upsert_raw(norm_text, norm_rating, norm_date, norm_product, "manual_add", now_str(), h, norm_category)
        logger.info(f"기존 리뷰를 갱신했습니다. (id={new_id})")
        return new_id

    new_id = db.insert_raw(norm_text, norm_rating, norm_date, norm_product, "manual_add", now_str(), h, norm_category)
    logger.info(f"리뷰 1건이 raw 저장소에 추가되었습니다. (id={new_id})")
    return new_id

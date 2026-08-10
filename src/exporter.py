"""
데이터 내보내기(export) 모듈
----------------------------
지원 포맷: csv, jsonl, xlsx (3종 모두 지원, 요구사항은 최소 2개)
필터링 옵션: --sentiment, --rating-min, --category
내보내기 컬럼에는 3분류 감정(sentiment)/신뢰도(confidence) 뿐 아니라,
그 둘을 조합해 계산한 감정 점수(1~5)/등급도 함께 포함한다.
"""
import csv
import json
import os
from .utils import sentiment_grade

BASE_FIELDS = ["id", "product", "category", "review_text", "rating", "review_date",
               "sentiment", "confidence", "sentiment_score", "sentiment_grade", "language"]


def _row_to_dict(r, threshold):
    d = {k: r[k] for k in ("id", "product", "category", "review_text", "rating",
                            "review_date", "sentiment", "confidence", "language")}
    grade = sentiment_grade(r["sentiment"], r["confidence"], threshold)
    d["sentiment_score"] = grade["score"] if r["sentiment"] else None
    d["sentiment_grade"] = grade["label"] if r["sentiment"] else None
    return d


def export_data(db, logger, fmt: str, output_dir: str, sentiment=None, rating_min=None,
                 category=None, filename=None, config=None):
    threshold = (config or {}).get("sentiment_grade", {}).get("strong_threshold", 0.75)
    result = db.query_clean(sentiment=sentiment, rating_min=rating_min, category=category, page=1, page_size=1_000_000)
    rows = [_row_to_dict(r, threshold) for r in result["rows"]]
    os.makedirs(output_dir, exist_ok=True)

    if not rows:
        logger.warning("내보낼 리뷰가 없습니다 (필터 조건을 확인하세요).")
        return None

    base_name = filename or "reviews_export"

    if fmt == "csv":
        path = os.path.join(output_dir, f"{base_name}.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=BASE_FIELDS)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
    elif fmt == "jsonl":
        path = os.path.join(output_dir, f"{base_name}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    elif fmt == "xlsx":
        import openpyxl

        path = os.path.join(output_dir, f"{base_name}.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "reviews"
        ws.append(BASE_FIELDS)
        for r in rows:
            ws.append([r[k] for k in BASE_FIELDS])
        wb.save(path)
    else:
        raise ValueError(f"지원하지 않는 포맷입니다: {fmt} (csv, jsonl, xlsx 중 선택)")

    logger.info(f"내보내기 완료: {len(rows)}건 -> {path}")
    return path

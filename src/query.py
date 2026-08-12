"""
데이터 조회(query) 모듈
-----------------------
list  : 조건 필터링 + 페이지네이션으로 리뷰 목록 출력 (표 형태)
show  : 리뷰 1건의 상세 정보(원문 + 분석결과 + 감정 점수) 출력
stats : 전체 통계 요약 출력 (감정 점수 분포 포함, 표 형태)
"""
import math
from . import ui
from .utils import sentiment_grade, SENTIMENT_GRADES

STARS = {5: "★★★★★", 4: "★★★★☆", 3: "★★★☆☆", 2: "★★☆☆☆", 1: "★☆☆☆☆", None: "☆☆☆☆☆"}
SENT_LABEL = {"positive": "긍정", "negative": "부정", "neutral": "중립"}


def list_reviews(db, config=None, sentiment=None, rating=None, rating_min=None, rating_max=None,
                  date_from=None, date_to=None, product=None, category=None, language=None,
                  page=1, page_size=10, sort_by="id", sort_dir="asc"):
    threshold = (config or {}).get("sentiment_grade", {}).get("strong_threshold", 0.75)
    result = db.query_clean(
        sentiment=sentiment, rating=rating, rating_min=rating_min, rating_max=rating_max,
        date_from=date_from, date_to=date_to, product=product, category=category, language=language,
        page=page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir,
    )
    total_pages = max(1, math.ceil(result["total"] / page_size))
    label = sentiment if sentiment and sentiment != "all" else "전체"
    ui.header(f"리뷰 목록 (감정: {label}, {page}/{total_pages} 페이지, 총 {result['total']}건)")

    rows_out = []
    for r in result["rows"]:
        stars = STARS.get(r["rating"], STARS[None])
        if r["sentiment"]:
            grade = sentiment_grade(r["sentiment"], r["confidence"], threshold)
            sent = f"{SENT_LABEL[r['sentiment']]} {grade['score']}/5"
        else:
            sent = "미분석"
        rows_out.append([r["id"], r["product"] or "-", r["review_text"], stars,
                          r["review_date"] or "-", sent])
    ui.table(["ID", "제품", "내용", "별점", "작성일", "감정"], rows_out, max_col_width=30)

    if total_pages > 1:
        ui.dim(f"  다음 페이지: --page {page + 1}" if page < total_pages else "  마지막 페이지입니다.")
    print()
    return result


def show_review(db, review_id, config=None):
    threshold = (config or {}).get("sentiment_grade", {}).get("strong_threshold", 0.75)
    row = db.get_clean_by_id(review_id)
    if not row:
        ui.error(f"ID={review_id} 리뷰를 찾을 수 없습니다.")
        return None
    stars = STARS.get(row["rating"], STARS[None])
    ui.header(f"리뷰 상세 (ID={row['id']})")
    print(f"  제품     : {row['product'] or '-'}")
    print(f"  카테고리 : {row['category'] or '-'}")
    print(f"  별점     : {stars} ({row['rating'] or '-'})")
    print(f"  작성일   : {row['review_date'] or '-'}")
    print(f"  언어     : {row['language'] or '-'}")
    print(f"  원문     : {row['review_text']}")
    if row["sentiment"]:
        grade = sentiment_grade(row["sentiment"], row["confidence"], threshold)
        print(f"  감정분류 : {SENT_LABEL[row['sentiment']]} (신뢰도 {row['confidence']} — 이 판단이 맞다고 확신하는 정도)")
        print(f"  감정점수 : {grade['score']}/5 ({grade['label']}) — 감정이 얼마나 강한지의 정도")
        print(f"  분석시각 : {row['analyzed_at']}")
    else:
        ui.warn("아직 분석되지 않았습니다. (analyze 커맨드를 실행하세요)")
    print()
    return row


def print_stats(db, config=None):
    threshold = (config or {}).get("sentiment_grade", {}).get("strong_threshold", 0.75)
    s = db.get_stats()
    total, analyzed = s["total"], s["analyzed"]
    rate = (analyzed / total * 100) if total else 0.0

    ui.header("리뷰 분석 통계")
    print(f"  총 리뷰 수: {total}건  ·  분석 완료: {analyzed}건 ({rate:.1f}%)")

    sent_rows = []
    for key in ("positive", "neutral", "negative"):
        c = s["sentiment_dist"].get(key, 0)
        pct = (c / analyzed * 100) if analyzed else 0.0
        sent_rows.append([SENT_LABEL[key], f"{c}건", f"{pct:.1f}%"])
    print("\n  감정 분포")
    ui.table(["감정", "건수", "비율"], sent_rows)

    grade_counts = {g["score"]: 0 for g in SENTIMENT_GRADES}
    grade_sum = 0
    for row in db.get_all_clean():
        if row["sentiment"]:
            g = sentiment_grade(row["sentiment"], row["confidence"], threshold)
            grade_counts[g["score"]] += 1
            grade_sum += g["score"]

    grade_rows = []
    for g in reversed(SENTIMENT_GRADES):
        c = grade_counts[g["score"]]
        pct = (c / analyzed * 100) if analyzed else 0.0
        grade_rows.append([f"{g['score']}점", g["label"], f"{c}건", f"{pct:.1f}%"])
    print("\n  감정 점수 분포 (1=아주나쁨 ~ 5=아주좋음)")
    ui.table(["점수", "등급", "건수", "비율"], grade_rows)

    rating_rows = []
    for star in (5, 4, 3, 2, 1):
        c = s["rating_dist"].get(star, 0)
        pct = (c / total * 100) if total else 0.0
        rating_rows.append([STARS[star], f"{c}건", f"{pct:.1f}%"])
    print("\n  별점 분포")
    ui.table(["별점", "건수", "비율"], rating_rows)

    avg_rating = s["avg_rating"] or 0
    avg_conf = s["avg_confidence"] or 0
    avg_grade = (grade_sum / analyzed) if analyzed else 0.0
    print(f"\n  평균 별점 {avg_rating:.2f}  ·  평균 감정점수 {avg_grade:.2f}/5  ·  평균 신뢰도 {avg_conf:.2f}")

    if s.get("language_dist"):
        lang_labels = {"ko": "한국어", "en": "영어", "zh": "중국어"}
        lang_rows = []
        for lang, c in s["language_dist"].items():
            pct = (c / total * 100) if total else 0.0
            lang_rows.append([lang_labels.get(lang, lang), f"{c}건", f"{pct:.1f}%"])
        print("\n  언어 분포 (보너스: 다국어 지원 확인용)")
        ui.table(["언어", "건수", "비율"], lang_rows)
    print()
    s["grade_dist"] = grade_counts
    s["avg_grade"] = avg_grade
    return s

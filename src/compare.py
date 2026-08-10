"""
[보너스 과제] 제품/카테고리별 비교 분석 모듈
--------------------------------------------
--by product   : 제품(product 필드) 기준으로 비교
--by category  : 카테고리(category 필드) 기준으로 비교
여러 대상의 리뷰 지표(리뷰 수, 평균 별점, 긍정 비율)를 나란히 비교한다.
"""


def compare_by(db, logger, by="product", targets=None):
    if by == "category":
        all_targets = targets or db.get_categories()
        field_label = "카테고리"
    else:
        all_targets = targets or db.get_products()
        field_label = "제품"

    if not all_targets:
        logger.warning(f"비교할 {field_label} 정보가 없습니다.")
        return []

    results = []
    for t in all_targets:
        t = t.strip() if isinstance(t, str) else t
        kwargs = {"category": t} if by == "category" else {"product": t}
        rows = db.query_clean(page=1, page_size=100000, **kwargs)["rows"]
        if not rows:
            continue
        total = len(rows)
        analyzed = [r for r in rows if r["sentiment"]]
        pos = sum(1 for r in analyzed if r["sentiment"] == "positive")
        neg = sum(1 for r in analyzed if r["sentiment"] == "negative")
        neu = sum(1 for r in analyzed if r["sentiment"] == "neutral")
        ratings = [r["rating"] for r in rows if r["rating"]]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0
        pos_ratio = (pos / len(analyzed) * 100) if analyzed else 0.0

        results.append({
            "name": t,
            "total_reviews": total,
            "avg_rating": round(avg_rating, 2),
            "positive_ratio": round(pos_ratio, 1),
            "positive": pos,
            "negative": neg,
            "neutral": neu,
        })

    results.sort(key=lambda x: x["positive_ratio"], reverse=True)
    return results


# 하위 호환용 (이전 버전 호출부 대비)
def compare_products(db, logger, products=None):
    return compare_by(db, logger, by="product", targets=products)


def print_comparison(results, by="product"):
    label = "카테고리" if by == "category" else "제품"
    if not results:
        print(f"비교할 {label} 데이터가 없습니다.")
        return
    print(f"\n=== {label}별 비교 분석 ===")
    header = f"{label + '명':<20}{'리뷰수':>8}{'평균별점':>10}{'긍정비율':>10}{'긍정':>6}{'중립':>6}{'부정':>6}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['name']:<20}{r['total_reviews']:>8}{r['avg_rating']:>10}{r['positive_ratio']:>9}%{r['positive']:>6}{r['neutral']:>6}{r['negative']:>6}")
    print()
    best = results[0]
    worst = results[-1]
    print(f"💡 긍정 비율이 가장 높은 {label}: {best['name']} ({best['positive_ratio']}%)")
    print(f"💡 긍정 비율이 가장 낮은 {label}: {worst['name']} ({worst['positive_ratio']}%)")
    print()

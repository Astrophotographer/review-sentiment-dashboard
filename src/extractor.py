"""
AI 키워드/요약 추출(extract) 모듈
---------------------------------
조건(감정/기간/제품)에 맞는 리뷰를 모아 AI에게 종합 분석을 요청하고,
긍정/부정 키워드, 전체 요약, 개선 제안을 생성하여 extractions 테이블에 저장한다.
"""
import json
from .utils import now_str


def build_condition_desc(sentiment=None, date_from=None, date_to=None, product=None, category=None):
    parts = []
    parts.append(f"감정={sentiment}" if sentiment and sentiment != "all" else "감정=전체")
    if date_from or date_to:
        parts.append(f"기간={date_from or '처음'}~{date_to or '지금'}")
    if product:
        parts.append(f"제품={product}")
    if category:
        parts.append(f"카테고리={category}")
    return ", ".join(parts)


def extract_insights(db, ai_client, logger, sentiment=None, date_from=None, date_to=None, product=None,
                      category=None, limit=None):
    result = db.query_clean(
        sentiment=sentiment, date_from=date_from, date_to=date_to, product=product, category=category,
        page=1, page_size=limit or 100000,
    )
    rows = result["rows"]
    if not rows:
        logger.warning("추출 대상 리뷰가 없습니다. 조건을 확인해주세요.")
        return None

    condition_desc = build_condition_desc(sentiment, date_from, date_to, product, category)
    logger.info(f"추출 대상: {len(rows)}건 ({condition_desc})")
    logger.info("AI 분석 요청 중...")

    reviews_payload = [
        {"review_text": r["review_text"], "sentiment": r["sentiment"], "rating": r["rating"]} for r in rows
    ]
    insights = ai_client.extract_insights(reviews_payload, condition_desc)
    insights["review_count"] = len(rows)
    insights["condition"] = condition_desc

    db.insert_extraction("keyword_summary", condition_desc, json.dumps(insights, ensure_ascii=False), now_str())
    logger.info("추출 완료")
    return insights


def _kw_text(item):
    """positive_keywords/negative_keywords 항목이 새 형식({'keyword':...,'count':...})이든
    예전 형식(그냥 문자열)이든 안전하게 키워드 텍스트만 꺼낸다."""
    return item.get("keyword", "") if isinstance(item, dict) else str(item)


def print_insights(insights: dict):
    print()
    print(f"=== 리뷰 키워드/요약 분석 ({insights.get('condition','')}) ===")
    print(f"대상 리뷰 수: {insights.get('review_count', 0)}건")
    print()
    pos_kw = ", ".join(_kw_text(k) for k in insights.get("positive_keywords", []))
    neg_kw = ", ".join(_kw_text(k) for k in insights.get("negative_keywords", []))
    print("[긍정 키워드]" if insights.get("positive_keywords") else "", pos_kw)
    print("[부정 키워드]" if insights.get("negative_keywords") else "", neg_kw)
    if insights.get("topic_breakdown"):
        print()
        print("[주요 불만/칭찬 유형]")
        for i, item in enumerate(insights["topic_breakdown"], start=1):
            examples = ", ".join(item.get("examples", []))
            print(f"{i}. {item.get('topic')} ({item.get('count')}건): {examples}")
    print()
    print("[전체 요약]")
    print(insights.get("summary", "-"))
    print()
    print("[개선 제안]")
    for s in insights.get("suggestions", []):
        print(f"- {s}")
    print()

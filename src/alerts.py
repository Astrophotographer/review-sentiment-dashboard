"""
[보너스 과제] 감정 변화 알림 모듈
---------------------------------
최근 N일간의 부정 리뷰 비율을 그 이전 기간과 비교하여,
비율이 급증한 경우 경고 메시지를 출력한다.

판단 기준(config.alert 로 조정 가능):
  - recent_ratio >= negative_ratio_threshold (예: 40% 이상) 이고
  - recent_ratio >= baseline_ratio * relative_increase_threshold (예: 이전 대비 1.3배 이상 증가)
  둘 다 만족하면 경고를 발생시킨다.
"""
from datetime import datetime, timedelta


def check_negative_spike(db, config, logger, days: int = None):
    alert_cfg = config.get("alert", {})
    days = days or alert_cfg.get("recent_days", 7)
    ratio_threshold = alert_cfg.get("negative_ratio_threshold", 0.4)
    relative_threshold = alert_cfg.get("relative_increase_threshold", 1.3)

    rows = db.get_all_clean()
    dated = [r for r in rows if r["review_date"] and r["sentiment"]]
    if not dated:
        logger.info("날짜/감정 정보가 있는 리뷰가 없어 알림 검사를 건너뜁니다.")
        return None

    max_date = max(datetime.strptime(r["review_date"], "%Y-%m-%d") for r in dated)
    cutoff = max_date - timedelta(days=days - 1)

    recent = [r for r in dated if datetime.strptime(r["review_date"], "%Y-%m-%d") >= cutoff]
    baseline = [r for r in dated if datetime.strptime(r["review_date"], "%Y-%m-%d") < cutoff]

    def neg_ratio(subset):
        if not subset:
            return 0.0
        neg = sum(1 for r in subset if r["sentiment"] == "negative")
        return neg / len(subset)

    recent_ratio = neg_ratio(recent)
    baseline_ratio = neg_ratio(baseline)

    triggered = recent_ratio >= ratio_threshold and (
        baseline_ratio == 0 or recent_ratio >= baseline_ratio * relative_threshold
    )

    result = {
        "days": days,
        "recent_count": len(recent),
        "recent_negative_ratio": round(recent_ratio, 3),
        "baseline_negative_ratio": round(baseline_ratio, 3),
        "triggered": triggered,
        "window_start": cutoff.strftime("%Y-%m-%d"),
        "window_end": max_date.strftime("%Y-%m-%d"),
    }

    if triggered:
        logger.warning(
            f"⚠ 부정 리뷰 급증 경고! 최근 {days}일({result['window_start']}~{result['window_end']}) "
            f"부정 비율 {recent_ratio*100:.1f}% (이전 대비 기준 {baseline_ratio*100:.1f}%) "
            f"- 원인 파악이 필요합니다."
        )
    else:
        logger.info(
            f"부정 리뷰 급증 없음. 최근 {days}일 부정 비율 {recent_ratio*100:.1f}% "
            f"(이전 {baseline_ratio*100:.1f}%)"
        )
    return result

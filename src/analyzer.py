"""
AI 감정 분석(analyze) 모듈
--------------------------
clean_reviews 테이블의 리뷰를 대상으로 AI(ai_client.AIClient)를 호출하여
감정(positive/negative/neutral)과 신뢰도(0.0~1.0)를 분석하고 DB에 저장한다.

옵션:
  --all         : 전체 리뷰 재분석
  --id ID       : 특정 ID 하나만 분석
  --unanalyzed  : 아직 분석되지 않은 리뷰만 분석 (기본값)
  --limit N     : 분석 건수 제한

이미 분석된 리뷰는 --all 이 아닌 이상 기본적으로 스킵한다.
API 호출 실패 시 로깅 후 해당 건은 스킵하고 다음 건으로 진행한다.

사용자 편의성: 대화형 터미널에서는 건별 로그 대신 진행률 바를 보여준다
(상세 건별 로그는 그대로 logs/app.log 파일에 남는다 — ui.suppressed_console_logging 참고).
"""
import contextlib
import sys
from . import ui
from .utils import now_str


def analyze_reviews(db, ai_client, logger, target="unanalyzed", review_id=None, limit=None, show_progress=True):
    if target == "id" and review_id is not None:
        row = db.get_clean_by_id(review_id)
        targets = [row] if row else []
    elif target == "all":
        targets = db.get_all_clean(limit=limit)
    else:  # unanalyzed (기본값)
        targets = db.get_unanalyzed(limit=limit)

    if not targets:
        logger.info("분석 대상 리뷰가 없습니다.")
        return {"success": 0, "failed": 0}

    total = len(targets)
    logger.info(f"분석 대상: {total}건")
    success, failed = 0, 0

    use_bar = show_progress and sys.stdout.isatty() and total > 1
    ctx = ui.suppressed_console_logging(logger) if use_bar else contextlib.nullcontext()

    with ctx:
        for idx, row in enumerate(targets, start=1):
            try:
                result = ai_client.analyze_sentiment(row["review_text"], row["language"] or "ko")
                db.update_analysis(row["id"], result["sentiment"], result["confidence"], now_str())
                logger.info(f"[{idx}/{total}] ID={row['id']} 분석 완료: {result['sentiment']} ({result['confidence']})")
                success += 1
            except Exception as e:  # noqa: BLE001 - 개별 건 실패는 전체 흐름을 막지 않는다
                logger.error(f"[{idx}/{total}] ID={row['id']} 분석 실패: {e}")
                failed += 1
            if use_bar:
                ui.progress(idx, total, label="AI 분석 중")

    logger.info(f"분석 완료: {success}건 성공, {failed}건 실패")
    return {"success": success, "failed": failed}

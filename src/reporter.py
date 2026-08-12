"""
리포트 생성(dashboard/report) 모듈
----------------------------------
- 콘솔에 종합 대시보드 리포트를 출력한다.
- 품질 지표 2개 이상(분석완료율, 평균 신뢰도, 저신뢰도 리뷰 비율),
  TOP N 집계(긍정/부정 키워드 TOP5), AI 추출 결과(요약/개선제안)를 포함한다.
- 결과를 TXT/MD 파일로도 저장한다.
- [보너스] 모든 차트와 통계를 포함한 단일 HTML 대시보드를 생성한다.
"""
import os
import json
from datetime import datetime
from collections import Counter
from .utils import SENTIMENT_GRADES, sentiment_grade


def _kw_text(item):
    """positive_keywords/negative_keywords 항목이 새 형식({'keyword':...,'count':...})이든
    예전 형식(그냥 문자열)이든 안전하게 키워드 텍스트만 꺼낸다 (과거에 저장된 extraction
    결과와의 하위호환용)."""
    return item.get("keyword", "") if isinstance(item, dict) else str(item)


def _kw_count(item):
    return item.get("count") if isinstance(item, dict) else None


def _quality_metrics(db):
    rows = db.get_all_clean()
    analyzed = [r for r in rows if r["sentiment"]]
    total = len(rows)
    completion_rate = (len(analyzed) / total * 100) if total else 0.0
    avg_confidence = (sum(r["confidence"] for r in analyzed) / len(analyzed)) if analyzed else 0.0
    low_conf = sum(1 for r in analyzed if r["confidence"] is not None and r["confidence"] < 0.5)
    low_conf_ratio = (low_conf / len(analyzed) * 100) if analyzed else 0.0
    return {
        "completion_rate": round(completion_rate, 1),
        "avg_confidence": round(avg_confidence, 2),
        "low_confidence_ratio": round(low_conf_ratio, 1),
    }


def _grade_metrics(db, threshold=0.75):
    """3분류(긍정/부정/중립)+신뢰도를 조합한 5단계 감정 점수 분포를 계산한다."""
    rows = db.get_all_clean()
    counts = {g["score"]: 0 for g in SENTIMENT_GRADES}
    total_score, analyzed = 0, 0
    for r in rows:
        if r["sentiment"]:
            g = sentiment_grade(r["sentiment"], r["confidence"], threshold)
            counts[g["score"]] += 1
            total_score += g["score"]
            analyzed += 1
    avg_grade = (total_score / analyzed) if analyzed else 0.0
    return {"counts": counts, "avg_grade": round(avg_grade, 2), "analyzed": analyzed}


def _is_fallback_payload(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("fallback") is True:
        return True
    return "규칙 기반" in str(data.get("summary") or "")


def _top_keywords(db, top_n=5):
    """성공한 AI 추출을 우선 쓰고, 그게 없을 때만 규칙 기반 폴백을 보여준다."""
    rows = db.list_extractions("keyword_summary")
    chosen = None
    source = "없음"
    for row in rows:
        try:
            data = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if not _is_fallback_payload(data):
            chosen = data
            source = "AI 추출 결과"
            break
        if chosen is None:
            chosen = data
            source = "규칙 기반 폴백"
    if chosen:
        return {
            "positive": chosen.get("positive_keywords", [])[:top_n],
            "negative": chosen.get("negative_keywords", [])[:top_n],
            "summary": chosen.get("summary", ""),
            "suggestions": chosen.get("suggestions", []),
            "topic_breakdown": chosen.get("topic_breakdown", []),
            "source": source,
        }
    return {"positive": [], "negative": [], "summary": "extract 커맨드를 먼저 실행하면 AI 요약이 표시됩니다.",
            "suggestions": [], "topic_breakdown": [], "source": "없음"}


def build_report_text(db, chart_paths, alert_result=None, threshold=0.75):
    stats = db.get_stats()
    quality = _quality_metrics(db)
    grade = _grade_metrics(db, threshold)
    keywords = _top_keywords(db)

    total, analyzed = stats["total"], stats["analyzed"]
    pos = stats["sentiment_dist"].get("positive", 0)
    pos_ratio = (pos / analyzed * 100) if analyzed else 0.0

    lines = []
    lines.append("=" * 60)
    lines.append("             고객 리뷰 감정 분석 대시보드")
    lines.append(f"                 생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("[핵심 지표]")
    lines.append(f"- 총 리뷰 수: {total}건")
    lines.append(f"- 분석 완료율: {quality['completion_rate']}%")
    lines.append(f"- 긍정 비율: {pos_ratio:.1f}%")
    lines.append(f"- 평균 별점: {(stats['avg_rating'] or 0):.2f}")
    lines.append(f"- 평균 감정 점수(1~5): {grade['avg_grade']}")
    lines.append("")
    lines.append("[감정 점수 분포] (1=아주나쁨 ~ 5=아주좋음, 신뢰도 반영)")
    for g in reversed(SENTIMENT_GRADES):
        c = grade["counts"][g["score"]]
        pct = (c / grade["analyzed"] * 100) if grade["analyzed"] else 0.0
        lines.append(f"- {g['score']}점 {g['label']}: {c}건 ({pct:.1f}%)")
    lines.append("")
    lines.append("[품질 지표]")
    lines.append(f"- 감정 분석 완료율: {quality['completion_rate']}%")
    lines.append(f"- 평균 신뢰도(Confidence, 판단의 확신 정도): {quality['avg_confidence']}")
    lines.append(f"- 저신뢰도(0.5 미만) 리뷰 비율: {quality['low_confidence_ratio']}%")
    lines.append("")
    lines.append(f"[TOP {len(keywords['positive']) or 5} 긍정 키워드] (출처: {keywords['source']})")
    for i, kw in enumerate(keywords["positive"], start=1):
        count = _kw_count(kw)
        suffix = f" ({count}회)" if count else ""
        lines.append(f"{i}. {_kw_text(kw)}{suffix}")
    lines.append("")
    lines.append(f"[TOP {len(keywords['negative']) or 5} 부정 키워드]")
    for i, kw in enumerate(keywords["negative"], start=1):
        count = _kw_count(kw)
        suffix = f" ({count}회)" if count else ""
        lines.append(f"{i}. {_kw_text(kw)}{suffix}")
    lines.append("")
    lines.append("[AI 인사이트 요약]")
    lines.append(keywords["summary"])
    if keywords.get("topic_breakdown"):
        lines.append("")
        lines.append("[주요 불만/칭찬 유형]")
        for i, item in enumerate(keywords["topic_breakdown"], start=1):
            examples = ", ".join(item.get("examples", []))
            lines.append(f"{i}. {item.get('topic')} ({item.get('count')}건): {examples}")
    if keywords["suggestions"]:
        lines.append("")
        lines.append("[개선 제안]")
        for s in keywords["suggestions"]:
            lines.append(f"- {s}")
    if stats.get("language_dist"):
        lines.append("")
        lines.append("[언어 분포] (보너스: 다국어 지원)")
        lang_labels = {"ko": "한국어", "en": "영어", "zh": "중국어"}
        for lang, c in stats["language_dist"].items():
            pct = (c / total * 100) if total else 0.0
            lines.append(f"- {lang_labels.get(lang, lang)}: {c}건 ({pct:.1f}%)")
    if alert_result:
        lines.append("")
        lines.append("[감정 변화 알림]")
        if alert_result["triggered"]:
            lines.append(
                f"⚠ 최근 {alert_result['days']}일간 부정 리뷰 비율 급증: "
                f"{alert_result['recent_negative_ratio']*100:.1f}% "
                f"(이전 {alert_result['baseline_negative_ratio']*100:.1f}%)"
            )
        else:
            lines.append(f"부정 리뷰 급증 없음 (최근 {alert_result['days']}일 기준)")
    lines.append("")
    lines.append("[생성된 차트 파일]")
    for p in chart_paths:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def save_report(text: str, output_dir: str, fmt: str = "md"):
    os.makedirs(output_dir, exist_ok=True)
    ext = "md" if fmt == "md" else "txt"
    path = os.path.join(output_dir, f"dashboard_report.{ext}")
    with open(path, "w", encoding="utf-8") as f:
        if fmt == "md":
            f.write("```\n" + text + "\n```\n")
        else:
            f.write(text)
    return path


# ---------------- [보너스] HTML 대시보드 (디자인 시스템 적용판) ----------------
_INK = "#12172B"
_MUTED = "#666B79"
_BORDER = "#E4E7ED"
_PAPER = "#F5F6F8"
_NAVY = "#1B2340"
_ACCENT = "#FF5A3C"
_AMBER = "#F2A93B"
_POSITIVE = "#1FAF6B"
_NEUTRAL = "#9BA3B4"
_NEGATIVE = "#E5484D"

def _all_reviews_payload(db):
    """대화형 대시보드가 브라우저에서 카테고리/제품별로 다시 집계할 수 있도록,
    분석된 리뷰 전체를 가벼운 JSON으로 직렬화한다 (원문 텍스트는 제외하고
    차트 계산에 필요한 필드만 담아 파일 용량을 아낀다)."""
    from .aspects import aspects_from_json, aspects_to_json, infer_aspects_from_text

    rows = db.get_all_clean()
    payload = []
    for r in rows:
        raw_aspect = r["aspect_json"] if "aspect_json" in r.keys() else None
        aspects = aspects_from_json(raw_aspect) if raw_aspect else None
        # 없거나 전부(전부 not_mentioned)이면 본문에서 규칙 기반으로 보완
        if not aspects or all(v == "not_mentioned" for v in aspects.values()):
            aspects = infer_aspects_from_text(r["review_text"] or "")
            try:
                db.update_aspects_only(r["id"], aspects_to_json(aspects))
            except Exception:  # noqa: BLE001
                pass
        payload.append({
            "id": r["id"],
            "product": r["product"],
            "category": r["category"],
            "sentiment": r["sentiment"],
            "confidence": r["confidence"],
            "rating": r["rating"],
            "date": r["review_date"],
            "language": r["language"],
            "aspects": aspects,
        })
    return payload


def _load_vendor_chartjs():
    """Chart.js를 CDN이 아니라 프로젝트에 내장된 파일에서 읽어와 HTML에 그대로
    삽입한다. 인터넷 연결 없이 오프라인에서 열어도 차트가 정상적으로 그려지게
    하기 위함이다 (단일 HTML 파일 하나로 완결되어야 한다는 취지에 맞춤)."""
    vendor_path = os.path.join(os.path.dirname(__file__), "vendor", "chart.umd.js")
    with open(vendor_path, encoding="utf-8") as f:
        return f.read()


def _load_dashboard_js(threshold: float, reviews_json: str) -> str:
    js_path = os.path.join(os.path.dirname(__file__), "dashboard_interactive.js")
    with open(js_path, encoding="utf-8") as f:
        template = f.read()
    return template.replace("__THRESHOLD__", str(threshold)).replace("__ALL_REVIEWS_JSON__", reviews_json)


def _load_model_controls_js() -> str:
    js_path = os.path.join(os.path.dirname(__file__), "dashboard_model_controls.js")
    with open(js_path, encoding="utf-8") as f:
        return f.read()


def build_html_dashboard(db, chart_paths, alert_result, output_dir, threshold=0.75):
    """[보너스] 카테고리/제품을 골라서 그 조건에 맞는 차트만 다시 그려주는
    대화형 HTML 대시보드를 생성한다. matplotlib PNG(정적 이미지, chart_paths)는
    그대로 output/ 폴더에 별도로 저장되어 있으므로(요구사항 충족용), 이 HTML은
    그 PNG를 그대로 붙여넣는 대신 Chart.js로 브라우저에서 직접 다시 그린다.
    리뷰 데이터를 통째로 파일 안에 넣어두고(서버 없이) 자바스크립트로 필터링만
    하는 방식이라, "실시간 웹 대시보드 금지" 제약과도 충돌하지 않는다
    (매번 새로 만드는 정적 스냅샷 파일 1개, 서버/DB 연결 없음)."""
    stats = db.get_stats()
    quality = _quality_metrics(db)
    grade = _grade_metrics(db, threshold)
    keywords = _top_keywords(db)
    reviews = _all_reviews_payload(db)

    if alert_result and alert_result.get("triggered"):
        signal_cls, signal_text = "signal-warn", (
            f"부정 리뷰 급증 · 최근 {alert_result['days']}일 "
            f"{alert_result['recent_negative_ratio']*100:.0f}%"
        )
    elif alert_result:
        signal_cls, signal_text = "signal-ok", (
            f"정상 · 최근 {alert_result['days']}일 부정 {alert_result['recent_negative_ratio']*100:.0f}%"
        )
    else:
        signal_cls, signal_text = "signal-ok", "알림 데이터 없음"
    signal_html = f'<span class="signal {signal_cls}">● {signal_text}</span>'

    def _pills(words, cls):
        if not words:
            return '<span class="empty">추출된 키워드가 없습니다</span>'
        out = []
        for w in words:
            text, count = _kw_text(w), _kw_count(w)
            badge = f' <b>{count}</b>' if count else ""
            out.append(f'<span class="pill {cls}">{text}{badge}</span>')
        return "".join(out)

    pos_kw_html = _pills(keywords["positive"], "pill-pos")
    neg_kw_html = _pills(keywords["negative"], "pill-neg")

    topics = keywords.get("topic_breakdown", [])
    max_count = max([t.get("count", 0) for t in topics], default=1) or 1
    topic_html = "".join(
        f"""<div class="topic-row">
              <div class="topic-head"><span>{t.get('topic')}</span><b>{t.get('count')}건</b></div>
              <div class="topic-bar-track"><div class="topic-bar" style="width:{max(6, t.get('count',0)/max_count*100):.0f}%"></div></div>
              <div class="topic-examples">{', '.join(t.get('examples', []))}</div>
            </div>"""
        for t in topics
    ) or '<span class="empty">extract 커맨드를 실행하면 유형별 집계가 표시됩니다</span>'

    suggestions_html = "".join(f"<li>{s}</li>" for s in keywords["suggestions"]) or "<li>-</li>"

    now_str = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    reviews_json = json.dumps(reviews, ensure_ascii=False, default=str)
    chartjs_source = _load_vendor_chartjs()
    dashboard_js = _load_dashboard_js(threshold, reviews_json)
    model_controls_js = _load_model_controls_js()

    css = f"""
  :root {{
    --ink:{_INK}; --muted:{_MUTED}; --border:{_BORDER}; --paper:{_PAPER}; --surface:#FFFFFF;
    --navy:{_NAVY}; --accent:{_ACCENT}; --amber:{_AMBER};
    --positive:{_POSITIVE}; --neutral:{_NEUTRAL}; --negative:{_NEGATIVE};
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:var(--paper); color:var(--ink);
    font-family:'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
    -webkit-font-smoothing:antialiased;
  }}
  .header {{ background:linear-gradient(135deg,#0F1526 0%,var(--navy) 100%); color:#fff; padding:36px 40px 30px; }}
  .eyebrow {{ font-size:11px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; color:var(--accent); margin-bottom:10px; }}
  .header-row {{ display:flex; justify-content:space-between; align-items:flex-end; flex-wrap:wrap; gap:14px; }}
  h1 {{ font-size:26px; font-weight:800; margin:0 0 6px; letter-spacing:-0.01em; }}
  .meta {{ color:rgba(255,255,255,.55); font-size:13px; }}
  .signal {{ display:inline-flex; align-items:center; gap:6px; font-size:13px; font-weight:600; padding:8px 14px; border-radius:999px; white-space:nowrap; }}
  .signal-ok {{ background:rgba(31,175,107,.16); color:#4ADE94; }}
  .signal-warn {{ background:rgba(229,72,77,.18); color:#FF8A8E; }}

  .wrap {{ max-width:1180px; margin:0 auto; padding:28px 40px 60px; }}

  .filter-bar {{
    display:flex; align-items:center; gap:12px; flex-wrap:wrap;
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:14px 18px; margin-bottom:22px;
  }}
  .filter-bar label {{ font-size:12px; font-weight:700; color:var(--muted); }}
  .filter-bar select {{
    font-family:inherit; font-size:13.5px; padding:8px 12px; border-radius:8px;
    border:1px solid var(--border); background:#fff; color:var(--ink); min-width:160px;
  }}
  .filter-bar button {{
    font-family:inherit; font-size:13px; font-weight:600; padding:8px 14px; border-radius:8px;
    border:1px solid var(--border); background:#fff; color:var(--ink); cursor:pointer;
  }}
  .filter-bar button:hover {{ background:var(--paper); }}
  .filter-current {{ margin-left:auto; font-size:13px; color:var(--muted); }}
  .filter-current b {{ color:var(--ink); }}
  .model-bar {{
    display:flex; align-items:center; gap:12px; flex-wrap:wrap;
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:14px 18px; margin-bottom:22px;
  }}
  .model-bar label {{ font-size:12px; font-weight:700; color:var(--muted); }}
  .model-bar select {{
    font-family:inherit; font-size:13.5px; padding:8px 12px; border-radius:8px;
    border:1px solid var(--border); background:#fff; color:var(--ink); min-width:160px;
  }}
  .model-bar button {{
    font-family:inherit; font-size:13px; font-weight:600; padding:8px 14px; border-radius:8px;
    border:1px solid var(--border); background:#fff; color:var(--ink); cursor:pointer;
  }}
  .model-bar button.primary {{
    background:var(--navy); color:#fff; border-color:var(--navy);
  }}
  .model-bar button:hover {{ filter:brightness(0.97); }}
  .model-bar button:disabled {{ opacity:.55; cursor:not-allowed; }}
  .model-bar.offline {{ opacity:.92; }}
  .spark-temp {{
    display:inline-flex; align-items:center; gap:6px;
    font-size:12.5px; font-weight:700; padding:6px 12px; border-radius:999px;
    border:1px solid var(--border); background:var(--paper); color:var(--ink);
  }}
  .spark-temp[hidden] {{ display:none !important; }}
  .spark-temp.ok {{ background:rgba(31,175,107,.1); color:#0E8A54; border-color:rgba(31,175,107,.25); }}
  .spark-temp.warn {{ background:rgba(245,166,35,.12); color:#B87A00; border-color:rgba(245,166,35,.3); }}
  .spark-temp.error {{ background:rgba(229,72,77,.12); color:#C7333A; border-color:rgba(229,72,77,.3); }}
  .spark-temp.offline {{ background:rgba(155,163,180,.12); color:var(--muted); border-color:rgba(155,163,180,.25); }}
  .model-status {{ font-size:12.5px; color:var(--muted); margin-left:auto; }}
  .model-status.ok {{ color:#0E8A54; }}
  .model-status.warn {{ color:#C7333A; }}
  .model-status.busy {{ color:#B87A00; }}
  .spark-key-bar {{
    display:none; align-items:center; gap:12px; flex-wrap:wrap;
    background:var(--surface); border:1px dashed var(--border); border-radius:12px;
    padding:12px 18px; margin:-10px 0 22px;
  }}
  .spark-key-bar.visible {{ display:flex; }}
  .spark-key-bar label {{ font-size:12px; font-weight:700; color:var(--muted); }}
  .spark-key-bar input {{
    font-family:inherit; font-size:13.5px; padding:8px 12px; border-radius:8px;
    border:1px solid var(--border); background:#fff; color:var(--ink); min-width:240px;
  }}
  .spark-key-bar button {{
    font-family:inherit; font-size:13px; font-weight:600; padding:8px 14px; border-radius:8px;
    border:1px solid var(--navy); background:var(--navy); color:#fff; cursor:pointer;
  }}
  .spark-key-bar button#deleteProviderKeyBtn {{
    background:#fff; color:#C7333A; border-color:rgba(229,72,77,.45);
  }}
  .spark-key-bar .key-status {{
    font-size:12px; font-weight:700; padding:4px 10px; border-radius:999px;
    background:rgba(155,163,180,.12); color:var(--muted); border:1px solid rgba(155,163,180,.25);
  }}
  .spark-key-bar .key-status.set {{
    background:rgba(31,175,107,.1); color:#0E8A54; border-color:rgba(31,175,107,.25);
  }}
  .spark-key-bar .hint {{ font-size:12px; color:var(--muted); }}
  .upload-bar {{
    display:flex; align-items:center; gap:12px; flex-wrap:wrap;
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:14px 18px; margin-bottom:22px;
  }}
  .upload-bar .upload-title {{ font-size:13px; font-weight:700; color:var(--ink); }}
  .upload-bar .upload-hint {{ font-size:12px; color:var(--muted); }}
  .upload-bar input[type=file] {{ font-family:inherit; font-size:13px; max-width:280px; }}
  .upload-bar button {{
    font-family:inherit; font-size:13px; font-weight:600; padding:8px 14px; border-radius:8px;
    border:1px solid var(--navy); background:var(--navy); color:#fff; cursor:pointer;
  }}
  .upload-bar button:disabled {{ opacity:.55; cursor:not-allowed; }}
  .upload-bar .upload-status {{ font-size:12.5px; color:var(--muted); margin-left:auto; }}
  .upload-bar .upload-status.ok {{ color:#0E8A54; }}
  .upload-bar .upload-status.warn {{ color:#C7333A; }}
  .upload-bar .upload-status.busy {{ color:#B87A00; }}
  .compare-link {{
    font-size:13px; font-weight:700; color:var(--navy); text-decoration:none;
    padding:8px 10px; white-space:nowrap;
  }}
  .compare-link:hover {{ text-decoration:underline; }}
  .empty-note {{
    display:none; text-align:center; color:var(--muted); font-size:13.5px;
    padding:14px; background:var(--surface); border:1px dashed var(--border); border-radius:10px; margin-bottom:20px;
  }}

  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin-bottom:30px; }}
  .kpi {{ background:var(--surface); border:1px solid var(--border); border-left:4px solid var(--navy); border-radius:10px; padding:16px 18px; box-shadow:0 1px 2px rgba(16,20,35,.04); }}
  .kpi .label {{ font-size:12px; color:var(--muted); font-weight:600; margin-bottom:6px; }}
  .kpi .value {{ font-size:26px; font-weight:800; letter-spacing:-0.02em; }}
  .kpi.c-total {{ border-left-color:var(--navy); }}
  .kpi.c-rate  {{ border-left-color:var(--neutral); }}
  .kpi.c-pos   {{ border-left-color:var(--positive); }}
  .kpi.c-grade {{ border-left-color:var(--amber); }}
  .kpi.c-rating{{ border-left-color:var(--amber); }}
  .kpi.c-conf  {{ border-left-color:var(--accent); }}

  .section-title {{ font-size:11px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); margin:36px 0 14px; }}

  .charts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(400px,1fr)); gap:16px; }}
  .chart-card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:18px; margin:0; }}
  .chart-card h3 {{ font-size:13.5px; margin:0 0 4px; }}
  .chart-card .desc {{ font-size:12px; color:var(--muted); margin-bottom:12px; }}
  .chart-card canvas {{ max-height:280px; }}
  .chart-card.dynamic-height canvas {{ max-height:none; }}
  .chart-wrap {{ position:relative; width:100%; }}
  .chart-card {{ transition: box-shadow .2s ease; }}
  .chart-card:hover {{ box-shadow:0 4px 16px rgba(16,20,35,.06); }}
  .compare-note {{ display:none; font-size:12.5px; color:var(--muted); padding:10px 4px; }}

  .panel {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:22px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  .cols h3 {{ font-size:13px; margin:0 0 12px; }}
  .pill {{ display:inline-block; padding:5px 12px; margin:0 6px 6px 0; border-radius:999px; font-size:12.5px; font-weight:600; }}
  .pill-pos {{ background:rgba(31,175,107,.1); color:#0E8A54; border:1px solid rgba(31,175,107,.25); }}
  .pill-neg {{ background:rgba(229,72,77,.1); color:#C7333A; border:1px solid rgba(229,72,77,.25); }}
  .empty {{ color:var(--muted); font-size:13px; }}

  .quote {{ border-left:3px solid var(--accent); background:rgba(255,90,60,.05); padding:14px 18px; border-radius:0 8px 8px 0; font-size:14px; line-height:1.6; margin:16px 0; }}
  ul.suggestions {{ margin:10px 0 0; padding-left:18px; font-size:13.5px; line-height:1.9; color:var(--ink); }}

  .topic-row {{ padding:12px 0; border-bottom:1px solid var(--border); }}
  .topic-row:last-child {{ border-bottom:none; }}
  .topic-head {{ display:flex; justify-content:space-between; font-size:13.5px; font-weight:700; margin-bottom:6px; }}
  .topic-bar-track {{ background:var(--paper); border-radius:6px; height:8px; overflow:hidden; }}
  .topic-bar {{ background:var(--accent); height:100%; border-radius:6px; }}
  .topic-examples {{ font-size:12px; color:var(--muted); margin-top:6px; }}

  .ai-note {{ font-size:12px; color:var(--muted); margin-top:14px; }}
  .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px; }}
  footer {{ text-align:center; font-size:12px; color:var(--muted); margin-top:44px; }}

  @media (max-width:720px) {{
    .header {{ padding:26px 20px; }}
    .wrap {{ padding:22px 20px 40px; }}
    .cols, .grid-2 {{ grid-template-columns:1fr; }}
    .filter-current {{ margin-left:0; width:100%; }}
  }}
"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>고객 리뷰 감정 분석 대시보드</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css" crossorigin>
<style>{css}</style>
</head>
<body>
  <div class="header">
    <div class="eyebrow">AI Customer Review Intelligence</div>
    <div class="header-row">
      <div>
        <h1>고객 리뷰 감정 분석 대시보드</h1>
        <div class="meta">생성일시 {now_str} · 카테고리/제품을 선택하면 아래 차트가 그 조건으로 다시 그려집니다</div>
      </div>
      {signal_html}
    </div>
  </div>

  <div class="wrap">
    <div class="filter-bar">
      <label for="catFilter">카테고리</label>
      <select id="catFilter"><option value="__all__">전체 카테고리</option></select>
      <label for="prodFilter">제품</label>
      <select id="prodFilter"><option value="__all__">전체 제품</option></select>
      <button id="resetFilterBtn" type="button">필터 초기화</button>
      <div class="filter-current">보는 중: <b id="filterLabel">전체</b></div>
    </div>

    <div class="upload-bar" id="uploadBar">
      <div>
        <div class="upload-title">리뷰 CSV 업로드</div>
        <div class="upload-hint">필수: 리뷰 텍스트 컬럼 · 선택: 별점/날짜/제품/카테고리 · 업로드 후 자동 정제·분석</div>
      </div>
      <input type="file" id="csvFileInput" accept=".csv,.xlsx,.xls,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />
      <button id="uploadCsvBtn" type="button">업로드 &amp; 분석</button>
      <span class="upload-status" id="uploadStatus"></span>
    </div>

    <div class="model-bar" id="modelBar">
      <label for="providerSelect">채점 엔진</label>
      <select id="providerSelect">
        <option value="spark">Spark (vLLM)</option>
        <option value="openai">OpenAI</option>
        <option value="gemini">Google Gemini</option>
        <option value="anthropic">Anthropic Claude</option>
        <option value="fallback">규칙 기반 폴백</option>
      </select>
      <label for="modelSelect">모델</label>
      <select id="modelSelect"><option value="qwen">qwen</option></select>
      <span class="spark-temp offline" id="sparkTemp" hidden>● 연결 중</span>
      <button id="reanalyzeBtn" class="primary" type="button">이 모델로 재분석</button>
      <a class="compare-link" href="/compare.html">모델 비교 →</a>
      <span class="model-status" id="modelStatus">모델 설정 불러오는 중…</span>
    </div>

    <div class="spark-key-bar" id="providerKeyBar">
      <label for="providerKeyInput" id="providerKeyLabel">SPARK_API_KEY</label>
      <span class="key-status" id="providerKeyStatus" hidden>미등록</span>
      <input type="password" id="providerKeyInput" autocomplete="off" placeholder="API 키 입력" />
      <button id="saveProviderKeyBtn" type="button">키 저장</button>
      <button id="deleteProviderKeyBtn" type="button" hidden>키 삭제</button>
      <span class="hint" id="providerKeyHint">.env에 저장되며, 저장 전까지 해당 provider 분석은 폴백됩니다.</span>
    </div>

    <div class="empty-note" id="emptyNote">선택한 조건에 해당하는 리뷰가 없습니다.</div>

    <div class="kpi-grid">
      <div class="kpi c-total"><div class="label">총 리뷰 수</div><div class="value" id="kpiTotal">0건</div></div>
      <div class="kpi c-rate"><div class="label">분석 완료율</div><div class="value" id="kpiRate">0%</div></div>
      <div class="kpi c-pos"><div class="label">긍정 비율</div><div class="value" id="kpiPos">0%</div></div>
      <div class="kpi c-rating"><div class="label">평균 별점</div><div class="value" id="kpiRating">0</div></div>
      <div class="kpi c-grade"><div class="label">평균 감정 점수(1~5)</div><div class="value" id="kpiGrade">0</div></div>
      <div class="kpi c-conf"><div class="label">평균 신뢰도</div><div class="value" id="kpiConf">0</div></div>
    </div>

    <div class="section-title">시각화 (선택한 카테고리/제품 기준)</div>
    <div class="charts">
      <div class="chart-card"><h3>만족도 측면별 감정</h3><div class="desc">상품 · 배송 · 응대 만족도별 긍정/중립/부정</div><canvas id="chartDonut"></canvas></div>
      <div class="chart-card"><h3>시간별 감정 추이</h3><div class="desc">날짜별 3일 이동평균</div><canvas id="chartTrend"></canvas></div>
      <div class="chart-card"><h3>감정 점수 분포 (1~5점)</h3><div class="desc">신뢰도까지 반영한 감정 강도</div><canvas id="chartGrade"></canvas></div>
      <div class="chart-card dynamic-height" id="cardProductComparison"><h3>제품별 비교</h3><div class="desc">제품별 긍정 비율</div><div class="chart-wrap" id="wrapProductComparison"><canvas id="chartProductComparison"></canvas></div></div>
      <div class="chart-card dynamic-height" id="cardProductBreakdown"><h3>제품별 감정 분포</h3><div class="desc">제품마다 긍정/중립/부정 실제 건수</div><div class="chart-wrap" id="wrapProductBreakdown"><canvas id="chartProductBreakdown"></canvas></div></div>
      <div class="chart-card"><h3>다국어 리뷰 분석</h3><div class="desc">언어(한/영/중)별 리뷰 수</div><canvas id="chartLanguage"></canvas></div>
    </div>
    <div class="compare-note" id="compareHiddenNote">💡 특정 제품을 선택하면 "제품별 비교/제품별 감정 분포" 차트는 비교 대상이 없어 숨겨집니다.</div>

    <div class="section-title">AI 키워드 &amp; 인사이트</div>
    <div class="panel">
      <div class="cols">
        <div><h3>👍 긍정 키워드</h3><div>{pos_kw_html}</div></div>
        <div><h3>👎 부정 키워드</h3><div>{neg_kw_html}</div></div>
      </div>
      <div class="quote">{keywords['summary']}</div>
      <div class="grid-2">
        <div><h3 style="font-size:13px;margin:0 0 8px;">주요 불만·칭찬 유형</h3>{topic_html}</div>
        <div><h3 style="font-size:13px;margin:0 0 8px;">개선 제안</h3><ul class="suggestions">{suggestions_html}</ul></div>
      </div>
      <div class="ai-note">💡 이 섹션은 마지막으로 성공한 AI 추출 결과입니다. 재분석 때 Spark 연결이 끊기면
      규칙 기반 폴백이 저장되어도 여기에는 이전 AI 요약을 유지합니다. 카테고리/제품 필터는 차트에만 적용되고
      이 블록은 전체 추출 기준입니다.</div>
    </div>

    <footer>Customer Review Sentiment Dashboard · Generated locally from clean_reviews · Chart.js 내장(오프라인 작동)</footer>
  </div>

<script>{chartjs_source}</script>
<script>{dashboard_js}</script>
<script>{model_controls_js}</script>
</body>
</html>"""

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def build_compare_html(output_dir: str) -> str:
    """모델 스냅샷 비교 페이지 HTML을 생성한다 (serve 모드 API와 함께 사용)."""
    chartjs_source = _load_vendor_chartjs()
    js_path = os.path.join(os.path.dirname(__file__), "dashboard_compare.js")
    with open(js_path, encoding="utf-8") as f:
        compare_js = f.read()

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>모델 채점 비교</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css" crossorigin>
<style>
  :root {{
    --ink:#12172B; --muted:#8A8F98; --border:#E4E7ED; --paper:#F5F6F8; --surface:#FFFFFF;
    --navy:#1B2340; --positive:#2A9B6A; --neutral:#A8B0BF; --negative:#E56B6F;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:var(--paper); color:var(--ink);
    font-family:'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif;
  }}
  .header {{ background:linear-gradient(135deg,#0F1526 0%,var(--navy) 100%); color:#fff; padding:28px 40px; }}
  .header a {{ color:#9BE7C4; font-weight:600; text-decoration:none; font-size:13px; }}
  .eyebrow {{ font-size:11px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; color:#FF8A6A; margin-bottom:8px; }}
  h1 {{ margin:0 0 6px; font-size:24px; font-weight:800; }}
  .meta {{ color:rgba(255,255,255,.55); font-size:13px; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:24px 40px 60px; }}
  .toolbar {{
    display:flex; flex-wrap:wrap; gap:12px; align-items:end;
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:14px 18px; margin-bottom:18px;
  }}
  label {{ display:block; font-size:12px; font-weight:700; color:var(--muted); margin-bottom:6px; }}
  select {{
    font:inherit; min-width:260px; padding:8px 12px; border-radius:8px;
    border:1px solid var(--border); background:#fff;
  }}
  button {{
    font:inherit; font-weight:700; padding:9px 16px; border-radius:8px; cursor:pointer;
    border:1px solid var(--navy); background:var(--navy); color:#fff;
  }}
  button:disabled {{ opacity:.5; cursor:not-allowed; }}
  .status {{ font-size:13px; color:var(--muted); margin-left:auto; }}
  .status.ok {{ color:#0E8A54; }}
  .status.warn {{ color:#C7333A; }}
  .field-hint {{ font-size:12px; color:var(--navy); font-weight:600; margin-top:6px; }}
  .snap-list {{
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:14px 18px; margin-bottom:18px;
  }}
  .snap-list h2 {{ margin:0 0 10px; font-size:14px; }}
  .snap-row {{
    display:flex; align-items:center; gap:12px; padding:10px 0;
    border-top:1px solid var(--border); font-size:13px;
  }}
  .snap-row:first-of-type {{ border-top:none; }}
  .snap-row .snap-id {{ color:var(--muted); font-weight:700; min-width:42px; }}
  .snap-row .snap-title {{ flex:1; font-weight:650; }}
  .snap-row .snap-meta {{ color:var(--muted); font-size:12px; }}
  .snap-row button.delete-snap {{
    border:1px solid #E56B6F; background:#fff; color:#C7333A;
    padding:6px 12px; font-size:12px;
  }}
  .snap-row button.delete-snap:hover {{ background:rgba(229,107,111,.08); }}
  .snap-empty {{ color:var(--muted); font-size:13px; padding:8px 0; }}
  .model-cards {{
    display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:18px;
  }}
  .model-card {{
    background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:16px 18px; border-top:3px solid var(--navy);
  }}
  .model-card.b {{ border-top-color:var(--positive); }}
  .model-card .side {{ font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
  .model-card .engine {{ font-size:13px; color:var(--muted); margin-top:6px; }}
  .model-card .model-name {{ font-size:22px; font-weight:800; margin-top:2px; letter-spacing:-.02em; }}
  .model-card .meta-line {{ font-size:12px; color:var(--muted); margin-top:8px; display:flex; gap:10px; flex-wrap:wrap; }}
  .model-card .temp {{
    display:inline-block; padding:2px 8px; border-radius:999px; background:rgba(42,155,106,.1); color:#0E8A54; font-weight:700;
  }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:18px; }}
  @media (max-width:720px) {{
    .model-cards {{ grid-template-columns:1fr; }}
  }}
  .kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; border-left:4px solid var(--navy); }}
  .kpi .label {{ font-size:12px; color:var(--muted); font-weight:600; margin-bottom:6px; }}
  .kpi .value {{ font-size:22px; font-weight:800; }}
  .panel {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:18px; margin-bottom:16px; }}
  .panel h2 {{ margin:0 0 12px; font-size:15px; }}
  .meta-row {{ display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:var(--muted); margin-bottom:8px; }}
  .meta-row b {{ color:var(--ink); }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:10px 8px; border-bottom:1px solid var(--border); vertical-align:top; }}
  th {{ color:var(--muted); font-size:12px; }}
  .excerpt {{ max-width:280px; color:var(--muted); }}
  .sent {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; font-weight:700; }}
  .sent.positive {{ background:rgba(42,155,106,.12); color:#0E8A54; }}
  .sent.neutral {{ background:rgba(168,176,191,.2); color:#5C6575; }}
  .sent.negative {{ background:rgba(229,107,111,.12); color:#C7333A; }}
  .sent.null {{ background:#f0f0f0; color:#888; }}
  .empty-cell {{ text-align:center; color:var(--muted); padding:20px !important; }}
  #emptyState {{
    display:none; background:var(--surface); border:1px dashed var(--border); border-radius:12px;
    padding:28px; text-align:center; color:var(--muted); margin-bottom:16px;
  }}
  #emptyState a {{ color:var(--navy); font-weight:700; }}
  @media (max-width:720px) {{
    .header, .wrap {{ padding-left:20px; padding-right:20px; }}
    select {{ min-width:100%; }}
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="eyebrow">Model Snapshot Compare</div>
    <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-end;">
      <div>
        <h1>모델 채점 비교</h1>
        <div class="meta">스냅샷마다 <b style="color:#fff">채점 엔진 · 모델명</b>이 표시됩니다. A/B를 골라 일치율을 확인하세요.</div>
      </div>
      <a href="/dashboard.html">← 대시보드</a>
    </div>
  </div>
  <div class="wrap">
    <div class="toolbar">
      <div>
        <label for="runA">스냅샷 A (채점 모델)</label>
        <select id="runA"></select>
        <div class="field-hint" id="hintA"></div>
      </div>
      <div>
        <label for="runB">스냅샷 B (채점 모델)</label>
        <select id="runB"></select>
        <div class="field-hint" id="hintB"></div>
      </div>
      <button id="compareBtn" type="button">비교하기</button>
      <span class="status" id="compareStatus">불러오는 중…</span>
    </div>

    <div class="snap-list" id="snapListPanel">
      <h2>스냅샷 목록</h2>
      <div id="snapList"></div>
    </div>

    <div id="emptyState">
      아직 스냅샷이 없습니다.<br/>
      <a href="/dashboard.html">대시보드</a>에서 「이 모델로 재분석」을 실행하면 여기에 쌓입니다.
    </div>

    <div id="resultPanel" style="display:none;">
      <div class="model-cards">
        <div class="model-card a">
          <div class="side">Snapshot A · 채점 모델</div>
          <div class="engine" id="aEngine">-</div>
          <div class="model-name" id="aModel">-</div>
          <div class="meta-line">
            <span id="aWhen">-</span>
            <span class="temp" id="aTemp" style="display:none;"></span>
            <span>평균 신뢰도 <b id="confA">-</b></span>
          </div>
        </div>
        <div class="model-card b">
          <div class="side">Snapshot B · 채점 모델</div>
          <div class="engine" id="bEngine">-</div>
          <div class="model-name" id="bModel">-</div>
          <div class="meta-line">
            <span id="bWhen">-</span>
            <span class="temp" id="bTemp" style="display:none;"></span>
            <span>평균 신뢰도 <b id="confB">-</b></span>
          </div>
        </div>
      </div>

      <div class="kpi-grid">
        <div class="kpi"><div class="label">공통 리뷰</div><div class="value" id="kpiCommon">-</div></div>
        <div class="kpi"><div class="label">일치율</div><div class="value" id="kpiAgree">-</div></div>
        <div class="kpi"><div class="label">비교 대상</div><div class="value" id="kpiCompared">-</div></div>
        <div class="kpi"><div class="label">불일치</div><div class="value" id="kpiDisagree">-</div></div>
        <div class="kpi"><div class="label">스냅샷 온도</div><div class="value" id="kpiTemp" style="font-size:16px;">-</div></div>
      </div>

      <div class="panel">
        <h2>감정 분포 비교</h2>
        <canvas id="chartCompare" height="120"></canvas>
      </div>

      <div class="panel">
        <h2>불일치 리뷰 (상위 50)</h2>
        <table id="disagreeTable">
          <thead>
            <tr>
              <th>ID</th><th>제품</th><th>리뷰</th>
              <th id="thA">A</th><th id="thB">B</th><th>|Δconf|</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>
<script>{chartjs_source}</script>
<script>{compare_js}</script>
</body>
</html>"""

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "compare.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path

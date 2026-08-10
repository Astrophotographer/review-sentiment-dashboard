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
import base64
from datetime import datetime
from collections import Counter
from .utils import SENTIMENT_GRADES, sentiment_grade


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


def _top_keywords(db, top_n=5):
    """가장 최근 extract 결과가 있으면 그것을 사용하고, 없으면 즉석에서 간단 집계한다."""
    latest = db.get_latest_extraction("keyword_summary")
    if latest:
        data = json.loads(latest["result_json"])
        return {
            "positive": data.get("positive_keywords", [])[:top_n],
            "negative": data.get("negative_keywords", [])[:top_n],
            "summary": data.get("summary", ""),
            "suggestions": data.get("suggestions", []),
            "topic_breakdown": data.get("topic_breakdown", []),
            "source": "AI 추출 결과 (extract 커맨드)",
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
        lines.append(f"{i}. {kw}")
    lines.append("")
    lines.append(f"[TOP {len(keywords['negative']) or 5} 부정 키워드]")
    for i, kw in enumerate(keywords["negative"], start=1):
        lines.append(f"{i}. {kw}")
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
        lang_labels = {"ko": "한국어", "en": "영어"}
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

_CHART_TITLES = {
    "sentiment_distribution": ("감정 분포", "리뷰가 긍정/중립/부정으로 어떻게 나뉘는지"),
    "sentiment_trend": ("시간별 감정 추이", "날짜별 긍정·중립·부정 리뷰 건수 변화"),
    "rating_sentiment_matrix": ("별점-감정 상관관계", "별점별로 감정이 어떻게 분포하는지"),
    "sentiment_grade": ("감정 점수 분포 (1~5점)", "신뢰도까지 반영한 감정 강도 등급"),
    "product_comparison": ("제품별 비교", "제품별 평균 별점과 긍정 비율"),
    "language_distribution": ("다국어 리뷰 분석", "언어(한/영)별 리뷰 수와 긍정 비율"),
}


def _img_to_base64(path):
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def build_html_dashboard(db, chart_paths, alert_result, output_dir, threshold=0.75):
    stats = db.get_stats()
    quality = _quality_metrics(db)
    grade = _grade_metrics(db, threshold)
    keywords = _top_keywords(db)
    total, analyzed = stats["total"], stats["analyzed"]
    pos = stats["sentiment_dist"].get("positive", 0)
    pos_ratio = (pos / analyzed * 100) if analyzed else 0.0

    chart_imgs_html = ""
    for p in chart_paths:
        b64 = _img_to_base64(p)
        if not b64:
            continue
        key = os.path.splitext(os.path.basename(p))[0]
        title, desc = _CHART_TITLES.get(key, (key, ""))
        chart_imgs_html += f"""
            <figure class="chart-card">
              <img src="data:image/png;base64,{b64}" alt="{title}" />
              <figcaption>{desc}</figcaption>
            </figure>"""

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
        return "".join(f'<span class="pill {cls}">{w}</span>' for w in words)

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

    lang_labels = {"ko": "한국어", "en": "영어"}
    lang_dist = stats.get("language_dist", {})
    lang_colors = [_NAVY, _ACCENT, _AMBER, _NEUTRAL]
    lang_segments, lang_legend = "", ""
    for i, (lang, c) in enumerate(sorted(lang_dist.items(), key=lambda x: -x[1])):
        pct = (c / total * 100) if total else 0
        color = lang_colors[i % len(lang_colors)]
        lang_segments += f'<div style="width:{pct:.2f}%;background:{color}" title="{lang_labels.get(lang, lang)} {pct:.1f}%"></div>'
        lang_legend += f'<span class="lang-legend-item"><i style="background:{color}"></i>{lang_labels.get(lang, lang)} {c}건 ({pct:.1f}%)</span>'
    if not lang_dist:
        lang_segments = f'<div style="width:100%;background:{_BORDER}"></div>'
        lang_legend = '<span class="empty">언어 데이터가 없습니다</span>'

    now_str = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>고객 리뷰 감정 분석 대시보드</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css" crossorigin>
<style>
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
  .header {{
    background:linear-gradient(135deg,#0F1526 0%,var(--navy) 100%);
    color:#fff; padding:36px 40px 30px;
  }}
  .eyebrow {{
    font-size:11px; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
    color:var(--accent); margin-bottom:10px;
  }}
  .header-row {{ display:flex; justify-content:space-between; align-items:flex-end; flex-wrap:wrap; gap:14px; }}
  h1 {{ font-size:26px; font-weight:800; margin:0 0 6px; letter-spacing:-0.01em; }}
  .meta {{ color:rgba(255,255,255,.55); font-size:13px; }}
  .signal {{
    display:inline-flex; align-items:center; gap:6px; font-size:13px; font-weight:600;
    padding:8px 14px; border-radius:999px; white-space:nowrap;
  }}
  .signal-ok {{ background:rgba(31,175,107,.16); color:#4ADE94; }}
  .signal-warn {{ background:rgba(229,72,77,.18); color:#FF8A8E; }}

  .wrap {{ max-width:1120px; margin:0 auto; padding:28px 40px 60px; }}

  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin-bottom:30px; }}
  .kpi {{
    background:var(--surface); border:1px solid var(--border); border-left:4px solid var(--navy);
    border-radius:10px; padding:16px 18px; box-shadow:0 1px 2px rgba(16,20,35,.04);
  }}
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
  .chart-card img {{ width:100%; border-radius:6px; display:block; }}
  .chart-card figcaption {{ font-size:12.5px; color:var(--muted); margin-top:10px; text-align:center; }}

  .panel {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:22px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  .cols h3 {{ font-size:13px; margin:0 0 12px; }}
  .pill {{ display:inline-block; padding:5px 12px; margin:0 6px 6px 0; border-radius:999px; font-size:12.5px; font-weight:600; }}
  .pill-pos {{ background:rgba(31,175,107,.1); color:#0E8A54; border:1px solid rgba(31,175,107,.25); }}
  .pill-neg {{ background:rgba(229,72,77,.1); color:#C7333A; border:1px solid rgba(229,72,77,.25); }}
  .empty {{ color:var(--muted); font-size:13px; }}

  .quote {{
    border-left:3px solid var(--accent); background:rgba(255,90,60,.05);
    padding:14px 18px; border-radius:0 8px 8px 0; font-size:14px; line-height:1.6; margin:16px 0;
  }}
  ul.suggestions {{ margin:10px 0 0; padding-left:18px; font-size:13.5px; line-height:1.9; color:var(--ink); }}

  .topic-row {{ padding:12px 0; border-bottom:1px solid var(--border); }}
  .topic-row:last-child {{ border-bottom:none; }}
  .topic-head {{ display:flex; justify-content:space-between; font-size:13.5px; font-weight:700; margin-bottom:6px; }}
  .topic-bar-track {{ background:var(--paper); border-radius:6px; height:8px; overflow:hidden; }}
  .topic-bar {{ background:var(--accent); height:100%; border-radius:6px; }}
  .topic-examples {{ font-size:12px; color:var(--muted); margin-top:6px; }}

  .lang-bar {{ display:flex; width:100%; height:14px; border-radius:999px; overflow:hidden; background:var(--border); margin-bottom:12px; }}
  .lang-legend {{ display:flex; flex-wrap:wrap; gap:14px; font-size:13px; color:var(--ink); }}
  .lang-legend-item {{ display:flex; align-items:center; gap:6px; }}
  .lang-legend-item i {{ width:10px; height:10px; border-radius:3px; display:inline-block; }}

  .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px; }}
  footer {{ text-align:center; font-size:12px; color:var(--muted); margin-top:44px; }}

  @media (max-width:720px) {{
    .header {{ padding:26px 20px; }}
    .wrap {{ padding:22px 20px 40px; }}
    .cols, .grid-2 {{ grid-template-columns:1fr; }}
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="eyebrow">AI Customer Review Intelligence</div>
    <div class="header-row">
      <div>
        <h1>고객 리뷰 감정 분석 대시보드</h1>
        <div class="meta">생성일시 {now_str}</div>
      </div>
      {signal_html}
    </div>
  </div>

  <div class="wrap">
    <div class="kpi-grid">
      <div class="kpi c-total"><div class="label">총 리뷰 수</div><div class="value">{total}건</div></div>
      <div class="kpi c-rate"><div class="label">분석 완료율</div><div class="value">{quality['completion_rate']}%</div></div>
      <div class="kpi c-pos"><div class="label">긍정 비율</div><div class="value">{pos_ratio:.1f}%</div></div>
      <div class="kpi c-rating"><div class="label">평균 별점</div><div class="value">{(stats['avg_rating'] or 0):.2f}</div></div>
      <div class="kpi c-grade"><div class="label">평균 감정 점수(1~5)</div><div class="value">{grade['avg_grade']}</div></div>
      <div class="kpi c-conf"><div class="label">평균 신뢰도</div><div class="value">{quality['avg_confidence']}</div></div>
    </div>

    <div class="section-title">시각화</div>
    <div class="charts">{chart_imgs_html}</div>

    <div class="section-title">AI 키워드 &amp; 인사이트</div>
    <div class="panel">
      <div class="cols">
        <div>
          <h3>👍 긍정 키워드</h3>
          <div>{pos_kw_html}</div>
        </div>
        <div>
          <h3>👎 부정 키워드</h3>
          <div>{neg_kw_html}</div>
        </div>
      </div>

      <div class="quote">{keywords['summary']}</div>

      <div class="grid-2">
        <div>
          <h3 style="font-size:13px;margin:0 0 8px;">주요 불만·칭찬 유형</h3>
          {topic_html}
        </div>
        <div>
          <h3 style="font-size:13px;margin:0 0 8px;">개선 제안</h3>
          <ul class="suggestions">{suggestions_html}</ul>
        </div>
      </div>
    </div>

    <div class="section-title">언어 분포 · 보너스: 다국어 지원</div>
    <div class="panel">
      <div class="lang-bar">{lang_segments}</div>
      <div class="lang-legend">{lang_legend}</div>
    </div>

    <footer>Customer Review Sentiment Dashboard · Generated locally from clean_reviews</footer>
  </div>
</body>
</html>"""

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path

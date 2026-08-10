"""
대시보드 시각화(dashboard) 모듈 — 디자인 시스템 적용판
-------------------------------------------------------
matplotlib으로 최소 3종 차트를 생성한다 (실제로는 5종).
  1) sentiment_distribution.png : 감정 분포 (도넛 차트)
  2) sentiment_trend.png        : 시간별 감정 추이 (라인 차트)
  3) rating_sentiment_matrix.png: 별점-감정 상관관계 (누적 막대 차트)
  4) product_comparison.png     : [보너스] 제품별 비교 (가로 막대)
  5) language_distribution.png  : [보너스] 언어별 분포 (가로 막대)

모든 차트는 동일한 색상 팔레트(PALETTE)와 타이포그래피 규칙을 공유하여
dashboard.html과 톤앤매너가 일관되도록 만들어졌다.
한글이 깨지지 않도록 config.visualization.font_candidates 중 사용 가능한 폰트를 찾아 적용한다.
"""
import os
import math
from collections import defaultdict
import matplotlib
from .utils import SENTIMENT_GRADES, sentiment_grade

matplotlib.use("Agg")  # 서버/CLI 환경에서 화면 없이 파일로 저장
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# ── 디자인 토큰 (dashboard.html과 색상을 공유한다) ──────────────────────────
PALETTE = {
    "ink": "#12172B",       # 본문/제목 텍스트
    "muted": "#8A8F98",     # 보조 텍스트, 축 라벨
    "border": "#E4E7ED",    # 그리드/테두리
    "surface": "#FFFFFF",
    "navy": "#1B2340",      # 중립적 데이터 바 색상(1)
    "accent": "#FF5A3C",    # 시그널 코럴 — 강조/경고용 시그니처 컬러
    "amber": "#F2A93B",     # 별점/보조 강조
    "positive": "#1FAF6B",  # 긍정 (에메랄드)
    "neutral": "#9BA3B4",   # 중립 (슬레이트)
    "negative": "#E5484D",  # 부정 (레드)
}
SENTIMENT_LABELS_KO = {"positive": "긍정", "neutral": "중립", "negative": "부정"}


def apply_theme(font_candidates):
    """전체 차트에 공통 적용되는 색/여백/그리드/폰트 스타일을 설정한다."""
    plt.rcParams.update({
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": PALETTE["border"],
        "axes.labelcolor": PALETTE["muted"],
        "axes.titlecolor": PALETTE["ink"],
        "axes.titleweight": "bold",
        "axes.titlesize": 14,
        "axes.titlepad": 16,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": PALETTE["border"],
        "grid.linewidth": 0.8,
        "grid.alpha": 0.9,
        "xtick.color": PALETTE["muted"],
        "ytick.color": PALETTE["muted"],
        "text.color": PALETTE["ink"],
        "font.size": 11,
        "legend.frameon": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
    })
    return apply_korean_font(font_candidates)


def apply_korean_font(font_candidates):
    """설정된 후보 중 사용 가능한 한글 폰트를 찾아 matplotlib 기본 폰트로 지정한다."""
    for candidate in font_candidates:
        try:
            if os.path.exists(candidate):
                fm.fontManager.addfont(candidate)
                font_name = fm.FontProperties(fname=candidate).get_name()
                plt.rcParams["font.family"] = font_name
                plt.rcParams["axes.unicode_minus"] = False
                return font_name
            else:
                available = {f.name for f in fm.fontManager.ttflist}
                if candidate in available:
                    plt.rcParams["font.family"] = candidate
                    plt.rcParams["axes.unicode_minus"] = False
                    return candidate
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    return None


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def _save(fig, path, dpi):
    fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.35, facecolor="white")
    plt.close(fig)
    return path


def _label_bars(ax, bars, fmt="{:.0f}", color=None, offset=0.01, horizontal=False):
    """막대 끝에 값 라벨을 달아 숫자를 한눈에 읽을 수 있게 한다."""
    for bar in bars:
        if horizontal:
            width = bar.get_width()
            if width == 0:
                continue
            ax.text(width + offset * max(ax.get_xlim()), bar.get_y() + bar.get_height() / 2,
                     fmt.format(width), va="center", ha="left",
                     fontsize=9.5, color=color or PALETTE["ink"])
        else:
            height = bar.get_height()
            if height == 0:
                continue
            ax.text(bar.get_x() + bar.get_width() / 2, height + offset * max(ax.get_ylim()),
                     fmt.format(height), ha="center", va="bottom",
                     fontsize=9.5, color=color or PALETTE["ink"])


# ── 1. 감정 분포: 도넛 차트 ──────────────────────────────────────────────
def chart_sentiment_distribution(db, output_dir, dpi=150):
    stats = db.get_stats()
    dist = stats["sentiment_dist"]
    order = [k for k in ("positive", "neutral", "negative") if dist.get(k)]

    fig, ax = plt.subplots(figsize=(6.4, 6.4))
    total = sum(dist.get(k, 0) for k in order)

    if order:
        sizes = [dist[k] for k in order]
        colors = [PALETTE[k] for k in order]
        wedges, _ = ax.pie(
            sizes, colors=colors, startangle=90, counterclock=False,
            wedgeprops=dict(width=0.42, edgecolor="white", linewidth=3),
        )
        for w, key, size in zip(wedges, order, sizes):
            ang = (w.theta1 + w.theta2) / 2
            x, y = 0.79 * math.cos(math.radians(ang)), 0.79 * math.sin(math.radians(ang))
            pct = size / total * 100
            ax.text(x, y, f"{pct:.0f}%", ha="center", va="center",
                     fontsize=11, fontweight="bold", color="white")

        ax.text(0, 0.08, f"{total}", ha="center", va="center",
                 fontsize=30, fontweight="bold", color=PALETTE["ink"])
        ax.text(0, -0.14, "전체 리뷰", ha="center", va="center",
                 fontsize=11.5, color=PALETTE["muted"])

        handles = [plt.Rectangle((0, 0), 1, 1, color=PALETTE[k]) for k in order]
        labels = [f"{SENTIMENT_LABELS_KO[k]}  {dist[k]}건" for k in order]
        ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.02),
                   ncol=len(order), fontsize=10.5, handlelength=1.1, handleheight=1.1)
    else:
        ax.text(0.5, 0.5, "분석된 데이터가 없습니다", ha="center", va="center", color=PALETTE["muted"])

    ax.set_title("감정 분포")
    ax.set_aspect("equal")
    ax.grid(False)
    return _save(fig, os.path.join(output_dir, "sentiment_distribution.png"), dpi)


# ── 2. 시간별 감정 추이: 라인 차트 ───────────────────────────────────────
def chart_sentiment_trend(db, output_dir, dpi=150):
    rows = db.get_all_clean()
    daily = defaultdict(lambda: {"positive": 0, "neutral": 0, "negative": 0})
    for r in rows:
        if r["review_date"] and r["sentiment"]:
            daily[r["review_date"]][r["sentiment"]] += 1
    dates = sorted(daily.keys())

    fig, ax = plt.subplots(figsize=(10, 5.4))
    if dates:
        for key in ("positive", "neutral", "negative"):
            values = [daily[d][key] for d in dates]
            ax.plot(dates, values, marker="o", markersize=4.5, linewidth=2.4,
                     label=SENTIMENT_LABELS_KO[key], color=PALETTE[key],
                     solid_capstyle="round")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.13), ncol=3, fontsize=10.5)
        ax.tick_params(axis="x", rotation=40)
        ax.grid(axis="y")
        ax.grid(axis="x", visible=False)
        for label in ax.get_xticklabels():
            label.set_ha("right")
    else:
        ax.text(0.5, 0.5, "날짜/분석 데이터가 없습니다", ha="center", va="center", color=PALETTE["muted"])

    ax.set_ylabel("리뷰 수")
    fig.suptitle("시간별 감정 추이", x=0.125, y=1.06, ha="left", fontsize=14, fontweight="bold", color=PALETTE["ink"])
    fig.tight_layout(rect=[0, 0, 1, 0.88])
    return _save(fig, os.path.join(output_dir, "sentiment_trend.png"), dpi)


# ── 3. 별점-감정 상관관계: 누적 막대 차트 ────────────────────────────────
def chart_rating_sentiment_matrix(db, output_dir, dpi=150):
    rows = db.get_all_clean()
    matrix = {r: {"positive": 0, "neutral": 0, "negative": 0} for r in range(1, 6)}
    for r in rows:
        if r["rating"] and r["sentiment"]:
            matrix[r["rating"]][r["sentiment"]] += 1

    ratings = [1, 2, 3, 4, 5]
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    bottom = [0] * len(ratings)
    for key in ("negative", "neutral", "positive"):
        values = [matrix[r][key] for r in ratings]
        bars = ax.bar([f"{r}점" for r in ratings], values, bottom=bottom,
                       label=SENTIMENT_LABELS_KO[key], color=PALETTE[key],
                       edgecolor="white", linewidth=1.6, width=0.62)
        for b, v, base in zip(bars, values, bottom):
            if v > 0:
                ax.text(b.get_x() + b.get_width() / 2, base + v / 2, str(v),
                         ha="center", va="center", fontsize=9.5, color="white", fontweight="bold")
        bottom = [b + v for b, v in zip(bottom, values)]

    ax.set_ylabel("리뷰 수")
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=10.5)
    ax.grid(axis="x", visible=False)
    fig.suptitle("별점-감정 상관관계", x=0.125, y=1.02, ha="left", fontsize=14, fontweight="bold", color=PALETTE["ink"])
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    return _save(fig, os.path.join(output_dir, "rating_sentiment_matrix.png"), dpi)


# ── 4. [보너스] 제품별 비교: 가로 막대 차트 ──────────────────────────────
def chart_product_comparison(db, output_dir, dpi=150):
    rows = db.get_all_clean()
    by_product = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0, "ratings": []})
    for r in rows:
        if not r["product"]:
            continue
        if r["sentiment"]:
            by_product[r["product"]][r["sentiment"]] += 1
        if r["rating"]:
            by_product[r["product"]]["ratings"].append(r["rating"])

    products = list(by_product.keys())
    if not products:
        return None

    def pos_ratio(p):
        d = by_product[p]
        total = d["positive"] + d["negative"] + d["neutral"]
        return (d["positive"] / total * 100) if total else 0

    products.sort(key=pos_ratio)
    avg_ratings = [sum(by_product[p]["ratings"]) / len(by_product[p]["ratings"]) if by_product[p]["ratings"] else 0 for p in products]
    pos_ratios = [pos_ratio(p) for p in products]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, max(3.2, 0.62 * len(products) + 1.4)))

    bars1 = ax1.barh(products, avg_ratings, color=PALETTE["navy"], height=0.55)
    ax1.set_title("제품별 평균 별점", loc="left")
    ax1.set_xlim(0, 5.6)
    _label_bars(ax1, bars1, fmt="{:.2f}", horizontal=True)
    ax1.grid(axis="y", visible=False)

    bar_colors = [PALETTE["positive"] if v >= 40 else PALETTE["amber"] if v >= 25 else PALETTE["negative"] for v in pos_ratios]
    bars2 = ax2.barh(products, pos_ratios, color=bar_colors, height=0.55)
    ax2.set_title("제품별 긍정 비율(%)", loc="left")
    ax2.set_xlim(0, 110)
    _label_bars(ax2, bars2, fmt="{:.0f}%", horizontal=True)
    ax2.set_yticklabels([])
    ax2.grid(axis="y", visible=False)

    fig.tight_layout()
    return _save(fig, os.path.join(output_dir, "product_comparison.png"), dpi)


# ── 5. [보너스] 언어별 분포: 가로 막대 차트 ──────────────────────────────
def chart_language_distribution(db, output_dir, dpi=150):
    rows = db.get_all_clean()
    lang_labels = {"ko": "한국어", "en": "영어"}
    counts = defaultdict(int)
    pos_counts = defaultdict(int)
    analyzed_counts = defaultdict(int)
    for r in rows:
        lang = r["language"] or "미상"
        counts[lang] += 1
        if r["sentiment"]:
            analyzed_counts[lang] += 1
            if r["sentiment"] == "positive":
                pos_counts[lang] += 1

    if not counts:
        return None

    langs = sorted(counts.keys(), key=lambda l: -counts[l])
    labels = [lang_labels.get(l, l) for l in langs]
    counts_vals = [counts[l] for l in langs]
    pos_ratio_vals = [(pos_counts[l] / analyzed_counts[l] * 100) if analyzed_counts[l] else 0 for l in langs]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, max(2.6, 0.6 * len(langs) + 1.2)))

    bars1 = ax1.barh(labels, counts_vals, color=PALETTE["navy"], height=0.5)
    ax1.set_title("언어별 리뷰 수", loc="left")
    _label_bars(ax1, bars1, fmt="{:.0f}건", horizontal=True)
    ax1.grid(axis="y", visible=False)

    bars2 = ax2.barh(labels, pos_ratio_vals, color=PALETTE["accent"], height=0.5)
    ax2.set_title("언어별 긍정 비율(%)", loc="left")
    ax2.set_xlim(0, 110)
    _label_bars(ax2, bars2, fmt="{:.0f}%", horizontal=True)
    ax2.grid(axis="y", visible=False)

    fig.suptitle("다국어 리뷰 분석", x=0.02, ha="left", fontsize=13, fontweight="bold", color=PALETTE["ink"])
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    return _save(fig, os.path.join(output_dir, "language_distribution.png"), dpi)


# ── 6. 감정 점수(1~5) 분포: 가로 막대 차트 ────────────────────────────────
def chart_sentiment_grade(db, output_dir, dpi=150, threshold=0.75):
    """confidence까지 반영한 5단계 감정 점수(아주나쁨~아주좋음) 분포를 시각화한다."""
    rows = db.get_all_clean()
    counts = {g["score"]: 0 for g in SENTIMENT_GRADES}
    analyzed = 0
    for r in rows:
        if r["sentiment"]:
            g = sentiment_grade(r["sentiment"], r["confidence"], threshold)
            counts[g["score"]] += 1
            analyzed += 1

    if analyzed == 0:
        return None

    ordered = SENTIMENT_GRADES  # score 1(아주나쁨) -> 5(아주좋음)
    labels = [f"{g['score']}점 · {g['label']}" for g in ordered]
    values = [counts[g["score"]] for g in ordered]
    colors = [g["color"] for g in ordered]

    fig, ax = plt.subplots(figsize=(9, 4.6))
    bars = ax.barh(labels, values, color=colors, height=0.6)
    _label_bars(ax, bars, fmt="{:.0f}건", horizontal=True)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("리뷰 수")
    fig.suptitle("감정 점수 분포 (1=아주나쁨 · 5=아주좋음)", x=0.06, y=1.04, ha="left",
                 fontsize=14, fontweight="bold", color=PALETTE["ink"])
    fig.tight_layout(rect=[0, 0, 1, 0.9])
    return _save(fig, os.path.join(output_dir, "sentiment_grade.png"), dpi)


def generate_all_charts(db, config, logger):
    vis_cfg = config.get("visualization", {})
    output_dir = _ensure_dir(vis_cfg.get("output_dir", "output"))
    dpi = vis_cfg.get("dpi", 150)
    threshold = config.get("sentiment_grade", {}).get("strong_threshold", 0.75)

    font_name = apply_theme(vis_cfg.get("font_candidates", []))
    if font_name:
        logger.info(f"한글 폰트 적용: {font_name}")
    else:
        logger.warning("한글 폰트를 찾지 못했습니다. 차트의 한글이 깨질 수 있습니다.")

    paths = [
        chart_sentiment_distribution(db, output_dir, dpi),
        chart_sentiment_trend(db, output_dir, dpi),
        chart_rating_sentiment_matrix(db, output_dir, dpi),
    ]
    grade_path = chart_sentiment_grade(db, output_dir, dpi, threshold)
    if grade_path:
        paths.append(grade_path)

    compare_path = chart_product_comparison(db, output_dir, dpi)
    if compare_path:
        paths.append(compare_path)

    lang_path = chart_language_distribution(db, output_dir, dpi)
    if lang_path:
        paths.append(lang_path)

    for p in paths:
        logger.info(f"차트 저장 완료: {p}")
    return paths

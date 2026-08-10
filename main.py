#!/usr/bin/env python3
"""
고객 리뷰 감정 분석 대시보드 - CLI 진입점
==========================================
사용 예:
    python main.py                                # 환영 화면 (처음이라면 이걸로 시작)
    python main.py setup                            # AI API 키를 .env에 저장 (한 번만 하면 계속 유지)
    python main.py menu                            # 번호로 고르는 대화형 메뉴
    python main.py quickstart --file reviews.csv   # 가져오기~대시보드까지 한 번에
    python main.py import --file sample_data/reviews_sample.csv
    python main.py clean
    python main.py analyze --unanalyzed
    python main.py list --sentiment negative
    python main.py search "배송 지연"                # 키워드로 리뷰 원문 검색
    python main.py stats
    python main.py extract --sentiment negative
    python main.py dashboard --html
    python main.py export --format csv --sentiment positive
    python main.py alert --days 7
    python main.py compare

각 커맨드가 끝나면 다음에 뭘 하면 좋을지 힌트를 보여준다. 처음 써보거나 명령어가
기억나지 않을 땐 그냥 `python main.py` 또는 `python main.py menu`로 시작하면 된다.
"""
import argparse
import glob
import json
import os
import sys
import logging
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.logger_setup import setup_logger
from src.db import Database
from src.ai_client import AIClient
from src import ingest, cleaner, analyzer, extractor, query, visualizer, reporter, exporter, alerts, compare, ui, envfile

# .env 파일이 있으면 여기서 가장 먼저 읽어둔다 (AIClient 등이 os.environ을 읽기 전에
# 실행되어야 하므로 import 직후, 다른 어떤 로직보다도 먼저 호출한다).
_ENV_LOADED = envfile.load_dotenv(".env")


# ============================================================
# 설정 로딩 & 파일 자동 탐지
# ============================================================
def load_config(path="config.json"):
    if not os.path.exists(path):
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_review_files():
    """sample_data/ 와 현재 폴더에서 가져올 만한 CSV/Excel 파일을 찾는다."""
    patterns = []
    for folder in ("sample_data", "."):
        for ext in ("*.csv", "*.xlsx", "*.xls"):
            patterns += glob.glob(os.path.join(folder, ext))
    seen, unique = set(), []
    for p in patterns:
        norm = os.path.normpath(p)
        if norm not in seen and os.path.isfile(norm):
            seen.add(norm)
            unique.append(norm)
    return unique


def resolve_import_file(explicit_path: str, allow_prompt: bool = True):
    """--file 이 명시되면 그대로 쓰고, 아니면 자동으로 찾아서 고르게 해준다."""
    if explicit_path:
        return explicit_path
    candidates = find_review_files()
    if not candidates:
        ui.error("가져올 CSV/Excel 파일을 찾지 못했습니다. --file 로 경로를 지정하세요.")
        return None
    if len(candidates) == 1:
        ui.info(f"파일을 자동으로 찾았습니다: {candidates[0]}")
        return candidates[0]
    if not allow_prompt or not sys.stdin.isatty():
        ui.error(f"리뷰 파일이 여러 개 있습니다({', '.join(candidates)}). --file 로 하나를 지정하세요.")
        return None
    ui.info("리뷰 파일을 여러 개 찾았습니다. 가져올 파일을 선택하세요.")
    idx = ui.choose("파일 번호", candidates)
    return candidates[idx]


# ============================================================
# 각 커맨드의 핵심 로직 (argparse 실행과 대화형 메뉴가 함께 재사용한다)
# ============================================================
def cmd_import(db, config, logger, file=None, dedup=None):
    path = resolve_import_file(file)
    if not path:
        return None
    result = ingest.import_file(db, config, logger, path, dedup)
    if result and result[1] > 0:
        ui.success(f"{result[1]}건 가져오기 완료")
    return result


def cmd_add(db, config, logger, text, rating=None, date=None, product=None, category=None):
    new_id = ingest.add_single_review(db, config, logger, text, rating, date, product, category)
    if new_id:
        ui.success(f"리뷰 1건 추가 완료 (id={new_id})")
    return new_id


def cmd_clean(db, config, logger, dedup=None):
    result = cleaner.clean_all(db, config, logger, dedup)
    ui.success(f"정제 완료: 신규 {result['inserted']}건, 갱신 {result['updated']}건")
    return result


def cmd_analyze(db, config, logger, ai_client, target="unanalyzed", review_id=None, limit=None, assume_yes=False):
    if target == "all":
        total = db.get_stats()["total"]
        msg = f"전체 {total}건을 다시 분석합니다 (이미 분석된 리뷰도 덮어씁니다)."
        if not assume_yes:
            ui.warn(msg)
            if not ui.confirm("계속하시겠습니까?", default=False):
                ui.info("취소되었습니다.")
                return None
    result = analyzer.analyze_reviews(db, ai_client, logger, target=target, review_id=review_id, limit=limit)
    if result:
        if result["failed"] == 0:
            ui.success(f"분석 완료: {result['success']}건 성공")
        else:
            ui.warn(f"분석 완료: {result['success']}건 성공, {result['failed']}건 실패 (logs/app.log 확인)")
    return result


def cmd_extract(db, config, logger, ai_client, sentiment="all", date_from=None, date_to=None,
                 product=None, category=None, limit=None):
    insights = extractor.extract_insights(
        db, ai_client, logger, sentiment=sentiment, date_from=date_from, date_to=date_to,
        product=product, category=category, limit=limit,
    )
    if insights:
        extractor.print_insights(insights)
        ui.success("키워드/요약 추출 완료")
    return insights


def cmd_dashboard(db, config, logger, fmt="md", html=False, alert_days=None):
    chart_paths = visualizer.generate_all_charts(db, config, logger)
    days = alert_days or config.get("alert", {}).get("recent_days", 7)
    alert_result = alerts.check_negative_spike(db, config, logger, days=days)
    threshold = config.get("sentiment_grade", {}).get("strong_threshold", 0.75)
    text_report = reporter.build_report_text(db, chart_paths, alert_result, threshold=threshold)
    print(text_report)
    output_dir = config.get("visualization", {}).get("output_dir", "output")
    saved_path = reporter.save_report(text_report, output_dir, fmt)
    html_path = None
    if html:
        html_path = reporter.build_html_dashboard(db, chart_paths, alert_result, output_dir, threshold=threshold)
    ui.success(f"대시보드 생성 완료: {saved_path}" + (f", {html_path}" if html_path else ""))
    return {"chart_paths": chart_paths, "report_path": saved_path, "html_path": html_path, "alert": alert_result}


def cmd_export(db, config, logger, fmt, sentiment=None, rating_min=None, category=None, output=None):
    output_dir = config.get("visualization", {}).get("output_dir", "output")
    path = exporter.export_data(
        db, logger, fmt, output_dir, sentiment=sentiment, rating_min=rating_min,
        category=category, filename=output, config=config,
    )
    if path:
        ui.success(f"내보내기 완료: {path}")
    return path


def cmd_search(db, config, keyword, page=1, page_size=10):
    """[사용자 편의] list의 구조적 필터(--sentiment, --rating 등)와 달리,
    리뷰 원문/제품명에서 자유롭게 키워드를 찾는다."""
    threshold = config.get("sentiment_grade", {}).get("strong_threshold", 0.75)
    result = db.search_reviews(keyword, page=page, page_size=page_size)
    import math as _math
    total_pages = max(1, _math.ceil(result["total"] / page_size))
    ui.header(f"검색 결과: '{keyword}' ({page}/{total_pages} 페이지, 총 {result['total']}건)")

    from src.utils import sentiment_grade as _grade
    rows_out = []
    for r in result["rows"]:
        stars = query.STARS.get(r["rating"], query.STARS[None])
        if r["sentiment"]:
            g = _grade(r["sentiment"], r["confidence"], threshold)
            sent = f"{query.SENT_LABEL[r['sentiment']]} {g['score']}/5"
        else:
            sent = "미분석"
        rows_out.append([r["id"], r["product"] or "-", r["review_text"], stars, sent])
    ui.table(["ID", "제품", "내용", "별점", "감정"], rows_out, max_col_width=32)
    if result["total"] == 0:
        ui.info("검색 결과가 없습니다. 다른 키워드로 시도해보세요.")
    print()
    return result


def cmd_setup():
    """[사용자 편의] 처음 쓰는 사용자를 위한 초기 설정 마법사.
    ANTHROPIC_API_KEY를 .env 파일에 저장해두면, 다음 실행부터는 매번
    `export ANTHROPIC_API_KEY=...`를 다시 입력하지 않아도 자동으로 적용된다."""
    ui.header("초기 설정 마법사")
    print("  Claude API 키를 설정하면 실제 AI 감정분석/키워드추출을 사용할 수 있습니다.")
    print("  키가 없어도 규칙 기반 폴백으로 전체 기능을 계속 사용할 수 있으니,")
    print("  나중에 설정하고 싶다면 그냥 엔터를 눌러 건너뛰어도 됩니다.")
    print(f"  {ui.dim_text('(입력한 값은 이 터미널 화면에 그대로 표시됩니다)')}\n")

    current = os.environ.get("ANTHROPIC_API_KEY", "")
    status = f"설정됨 ({current[:10]}...)" if current else "설정 안 됨"
    print(f"  현재 상태: {status}\n")

    key = ui.ask("ANTHROPIC_API_KEY 입력 (건너뛰려면 엔터)")
    updates = {}
    if key:
        updates["ANTHROPIC_API_KEY"] = key

    if ui.confirm("중복 리뷰 처리 기본 정책을 upsert(덮어쓰기)로 바꿀까요? (기본은 skip)", default=False):
        _patch_config_dedup_policy("upsert")
        ui.success("config.json의 dedup_policy를 upsert로 변경했습니다.")

    if updates:
        envfile.write_dotenv(".env", updates)
        added_gitignore = envfile.ensure_gitignored(".env")
        ui.success(".env 파일에 저장했습니다. 다음 실행부터 자동으로 적용됩니다.")
        if added_gitignore:
            ui.info(".env 파일이 실수로 git에 커밋되지 않도록 .gitignore에 추가했습니다.")
    else:
        ui.info("API 키 변경 사항이 없습니다.")

    ui.hint("python main.py quickstart  (바로 전체 파이프라인을 실행해보세요)")


def _patch_config_dedup_policy(policy: str, path: str = "config.json"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["dedup_policy"] = policy
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ============================================================
# 다음 단계 힌트
# ============================================================
def print_next_hint(command, db=None):
    hints = {
        "import": "python main.py clean  (가져온 데이터를 정제합니다)",
        "add": "python main.py clean  (추가한 리뷰를 정제합니다)",
        "clean": "python main.py analyze --unanalyzed  (AI로 감정을 분석합니다)",
        "extract": "python main.py dashboard --html  (종합 리포트를 확인합니다)",
        "dashboard": "python main.py export --format xlsx  (결과를 엑셀로 내보냅니다)",
    }
    if command == "analyze" and db is not None:
        remaining = len(db.get_unanalyzed())
        if remaining > 0:
            ui.hint(f"아직 {remaining}건이 남아 있습니다 → python main.py analyze --unanalyzed")
            return
        ui.hint("python main.py extract --sentiment all  (키워드/요약을 추출합니다)")
        return
    if command in hints:
        ui.hint(hints[command])


# ============================================================
# 환영 화면 (인자 없이 실행했을 때)
# ============================================================
def print_welcome():
    ui.header("고객 리뷰 감정 분석 대시보드")
    print("  터미널 명령어가 익숙하지 않다면 아래 중 하나로 시작하세요.\n")
    print(f"  {ui.bold('python main.py setup')}        AI API 키를 .env에 저장 (선택, 안 해도 동작함)")
    print(f"  {ui.bold('python main.py menu')}         번호로 고르는 대화형 메뉴 (추천)")
    print(f"  {ui.bold('python main.py quickstart')}   가져오기~대시보드까지 한 번에 실행")
    print(f"  {ui.bold('python main.py --help')}       전체 명령어 목록 보기\n")
    print("  이미 익숙하다면 예전처럼 개별 명령어를 바로 써도 됩니다:")
    print("  import · add · clean · analyze · extract · list · search · show · stats ·")
    print("  dashboard · export · alert · compare\n")


# ============================================================
# 대화형 메뉴
# ============================================================
def run_menu(config, logger):
    db = Database(config["storage"]["db_path"])
    ai_client = AIClient(config, logger)
    items = [
        ("리뷰 파일 가져오기 (import)", lambda: cmd_import(db, config, logger, file=resolve_import_file(None))),
        ("리뷰 1건 수동 추가 (add)", _menu_add),
        ("데이터 정제 (clean)", lambda: cmd_clean(db, config, logger)),
        ("AI 감정 분석 (analyze)", _menu_analyze),
        ("AI 키워드/요약 추출 (extract)", _menu_extract),
        ("리뷰 목록 조회 (list)", _menu_list),
        ("키워드로 리뷰 검색 (search)", _menu_search),
        ("리뷰 상세 조회 (show)", _menu_show),
        ("전체 통계 보기 (stats)", lambda: query.print_stats(db, config=config)),
        ("대시보드/리포트 생성 (dashboard)", _menu_dashboard),
        ("결과 내보내기 (export)", _menu_export),
        ("전체 파이프라인 한 번에 실행 (quickstart)", lambda: _menu_quickstart(db, config, logger, ai_client)),
    ]
    try:
        while True:
            ui.header("메인 메뉴")
            for i, (label, _) in enumerate(items, start=1):
                print(f"  {i:2d}. {label}")
            print(f"   0. 종료")
            choice = ui.ask("\n번호를 선택하세요", default="0")
            if choice == "0":
                ui.info("종료합니다.")
                break
            if not choice.isdigit() or not (1 <= int(choice) <= len(items)):
                ui.error("올바른 번호를 입력하세요.")
                continue
            _, fn = items[int(choice) - 1]
            try:
                if fn.__code__.co_argcount == 0:
                    fn()
                else:
                    fn(db, config, logger, ai_client)
            except Exception as e:  # noqa: BLE001 - 메뉴 도중 오류가 나도 메뉴는 계속 유지
                ui.error(f"오류가 발생했습니다: {e}")
            input(_dim_continue())
    finally:
        db.close()


def _dim_continue():
    return ui.dim_text("\n(엔터를 누르면 메뉴로 돌아갑니다)")


def _menu_add(db, config, logger, ai_client=None):
    text = ui.ask("리뷰 내용")
    if not text:
        ui.error("리뷰 내용은 필수입니다.")
        return
    rating = ui.ask("별점 (1~5, 생략 가능)")
    date = ui.ask("작성일 YYYY-MM-DD (생략 가능)")
    product = ui.ask("제품명 (생략 가능)")
    category = ui.ask("카테고리 (생략 가능)")
    cmd_add(db, config, logger, text, rating or None, date or None, product or None, category or None)


def _menu_analyze(db, config, logger, ai_client):
    remaining = len(db.get_unanalyzed())
    if remaining == 0:
        ui.info("이미 모든 리뷰가 분석되어 있습니다.")
        if not ui.confirm("전체를 다시 분석할까요?", default=False):
            return
        cmd_analyze(db, config, logger, ai_client, target="all", assume_yes=True)
    else:
        ui.info(f"미분석 리뷰 {remaining}건을 분석합니다.")
        cmd_analyze(db, config, logger, ai_client, target="unanalyzed")
    print_next_hint("analyze", db)


def _menu_extract(db, config, logger, ai_client):
    sentiment = ui.ask("추출할 감정 (all/positive/negative/neutral)", default="all")
    product = ui.ask("제품 필터 (생략 가능)")
    category = ui.ask("카테고리 필터 (생략 가능)")
    cmd_extract(db, config, logger, ai_client, sentiment=sentiment, product=product or None, category=category or None)


def _menu_list(db, config, logger, ai_client=None):
    sentiment = ui.ask("감정 필터 (all/positive/negative/neutral, 엔터=전체)")
    page = 1
    while True:
        result = query.list_reviews(db, config=config, sentiment=(sentiment or None), page=page, page_size=10)
        total_pages = max(1, -(-result["total"] // 10))  # 올림 나눗셈
        if total_pages <= 1:
            break
        nav = ui.ask(f"페이지 이동 ({page}/{total_pages}) — n=다음, p=이전, 엔터=그만", default="")
        if nav.lower() == "n" and page < total_pages:
            page += 1
        elif nav.lower() == "p" and page > 1:
            page -= 1
        elif nav == "":
            break


def _menu_search(db, config, logger, ai_client=None):
    keyword = ui.ask("검색할 키워드")
    if not keyword:
        return
    page = 1
    while True:
        result = cmd_search(db, config, keyword, page=page, page_size=10)
        total_pages = max(1, -(-result["total"] // 10))
        if total_pages <= 1:
            break
        nav = ui.ask(f"페이지 이동 ({page}/{total_pages}) — n=다음, p=이전, 엔터=그만", default="")
        if nav.lower() == "n" and page < total_pages:
            page += 1
        elif nav.lower() == "p" and page > 1:
            page -= 1
        elif nav == "":
            break


def _menu_show(db, config, logger, ai_client=None):
    rid = ui.ask("조회할 리뷰 ID")
    if rid.isdigit():
        query.show_review(db, int(rid), config=config)
    else:
        ui.error("숫자 ID를 입력하세요.")


def _menu_dashboard(db, config, logger, ai_client):
    make_html = ui.confirm("HTML 대시보드도 함께 만들까요?", default=True)
    cmd_dashboard(db, config, logger, html=make_html)


def _menu_export(db, config, logger, ai_client=None):
    fmt = ui.ask("포맷 (csv/xlsx/jsonl)", default="csv")
    if fmt not in ("csv", "xlsx", "jsonl"):
        ui.error("csv, xlsx, jsonl 중 하나를 입력하세요.")
        return
    cmd_export(db, config, logger, fmt)


def _menu_quickstart(db, config, logger, ai_client):
    path = resolve_import_file(None)
    if not path:
        return
    _run_pipeline(db, config, logger, ai_client, path)


# ============================================================
# quickstart: 전체 파이프라인 한 번에
# ============================================================
def _run_pipeline(db, config, logger, ai_client, file_path, dedup=None, html=True):
    ui.header("퀵스타트: 가져오기 → 정제 → AI 분석 → 키워드 추출 → 대시보드")
    if not cmd_import(db, config, logger, file=file_path, dedup=dedup):
        return
    cmd_clean(db, config, logger, dedup=dedup)
    cmd_analyze(db, config, logger, ai_client, target="unanalyzed", assume_yes=True)
    cmd_extract(db, config, logger, ai_client, sentiment="all")
    cmd_dashboard(db, config, logger, html=html)
    ui.success("퀵스타트 완료! output/ 폴더를 확인해보세요.")


# ============================================================
# argparse 설정
# ============================================================
def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py", description="AI 기반 고객 리뷰 감정 분석 대시보드 CLI"
    )
    parser.add_argument("--config", default="config.json", help="설정 파일 경로 (기본: config.json)")
    parser.add_argument("-y", "--yes", action="store_true", help="확인 프롬프트를 건너뛰고 진행한다")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("menu", help="번호로 고르는 대화형 메뉴를 실행한다 (처음 써보신다면 이걸로 시작하세요)")
    sub.add_parser("setup", help="[편의 기능] API 키를 .env 파일에 저장하는 초기 설정 마법사")

    p = sub.add_parser("quickstart", help="가져오기~대시보드까지 전체 파이프라인을 한 번에 실행한다")
    p.add_argument("--file", default=None, help="리뷰 파일 경로 (생략 시 자동 탐지)")
    p.add_argument("--dedup", choices=["skip", "upsert"], default=None)
    p.add_argument("--no-html", action="store_true", help="HTML 대시보드는 생성하지 않는다")

    # import
    p = sub.add_parser("import", help="CSV/Excel 파일에서 리뷰 데이터를 가져온다")
    p.add_argument("--file", default=None, help="리뷰 파일 경로 (.csv 또는 .xlsx, 생략 시 자동 탐지)")
    p.add_argument("--dedup", choices=["skip", "upsert"], default=None, help="중복 처리 정책")

    # add
    p = sub.add_parser("add", help="리뷰 1건을 수동으로 추가한다 (관리자 입력용)")
    p.add_argument("--text", required=True, help="리뷰 본문")
    p.add_argument("--rating", type=int, default=None, help="별점 (1~5)")
    p.add_argument("--date", default=None, help="작성일 (YYYY-MM-DD)")
    p.add_argument("--product", default=None, help="제품명")
    p.add_argument("--category", default=None, help="제품 카테고리")

    # clean
    p = sub.add_parser("clean", help="raw 데이터를 정제하여 clean 저장소로 옮긴다")
    p.add_argument("--dedup", choices=["skip", "upsert"], default=None, help="중복 처리 정책")

    # analyze
    p = sub.add_parser("analyze", help="AI로 리뷰 감정을 분석한다")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="전체 리뷰 재분석")
    g.add_argument("--id", type=int, default=None, help="특정 ID 하나만 분석")
    g.add_argument("--unanalyzed", action="store_true", help="미분석 리뷰만 분석 (기본값)")
    p.add_argument("--limit", type=int, default=None, help="분석 건수 제한")
    p.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS,
                    help="확인 프롬프트를 건너뛰고 진행한다 (--all 재분석 시)")

    # extract
    p = sub.add_parser("extract", help="AI로 키워드/요약/개선제안을 추출한다")
    p.add_argument("--sentiment", choices=["positive", "negative", "neutral", "all"], default="all")
    p.add_argument("--date-from", default=None)
    p.add_argument("--date-to", default=None)
    p.add_argument("--product", default=None)
    p.add_argument("--category", default=None, help="제품 카테고리 조건")
    p.add_argument("--limit", type=int, default=None)

    # list
    p = sub.add_parser("list", help="리뷰 목록을 조회한다")
    p.add_argument("--sentiment", choices=["positive", "negative", "neutral", "all"], default=None)
    p.add_argument("--rating", type=int, default=None, help="정확히 일치하는 별점")
    p.add_argument("--rating-min", type=int, default=None, help="별점 하한")
    p.add_argument("--rating-max", type=int, default=None, help="별점 상한")
    p.add_argument("--date-from", default=None)
    p.add_argument("--date-to", default=None)
    p.add_argument("--product", default=None)
    p.add_argument("--category", default=None)
    p.add_argument("--language", choices=["ko", "en"], default=None, help="[보너스] 언어 필터")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=None)
    p.add_argument("--sort", default="id", help="정렬 기준 컬럼 (id, rating, review_date, sentiment, confidence)")
    p.add_argument("--order", choices=["asc", "desc"], default="asc")

    # search [사용자 편의]
    p = sub.add_parser("search", help="[편의 기능] 리뷰 원문/제품명에서 키워드를 자유 검색한다")
    p.add_argument("keyword", help="검색할 키워드")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=None)

    # show
    p = sub.add_parser("show", help="리뷰 1건의 상세 정보를 조회한다")
    p.add_argument("--id", type=int, required=True)

    # stats
    sub.add_parser("stats", help="전체 통계 요약을 출력한다")

    # dashboard
    p = sub.add_parser("dashboard", help="차트/리포트를 생성한다 (콘솔 + 파일)")
    p.add_argument("--format", choices=["txt", "md"], default="md", help="리포트 파일 형식")
    p.add_argument("--html", action="store_true", help="[보너스] 단일 HTML 대시보드도 함께 생성")
    p.add_argument("--alert-days", type=int, default=None, help="감정 급증 알림 검사 기간(일)")

    # export
    p = sub.add_parser("export", help="분석 결과를 CSV/JSONL/Excel로 내보낸다")
    p.add_argument("--format", choices=["csv", "jsonl", "xlsx"], required=True)
    p.add_argument("--sentiment", choices=["positive", "negative", "neutral", "all"], default=None)
    p.add_argument("--rating-min", type=int, default=None)
    p.add_argument("--category", default=None, help="카테고리 필터")
    p.add_argument("--output", default=None, help="파일명 (확장자 제외)")

    # alert (보너스)
    p = sub.add_parser("alert", help="[보너스] 최근 N일간 부정 리뷰 급증 여부를 검사한다")
    p.add_argument("--days", type=int, default=None)

    # compare (보너스)
    p = sub.add_parser("compare", help="[보너스] 제품/카테고리별 비교 분석을 수행한다")
    p.add_argument("--by", choices=["product", "category"], default="product", help="비교 기준")
    p.add_argument("--targets", default=None, help="쉼표로 구분한 대상 목록 (미지정 시 전체)")

    return parser


# ============================================================
# 진입점
# ============================================================
def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        print_welcome()
        return

    config = load_config(args.config)
    logger = setup_logger(config)

    if args.command == "menu":
        run_menu(config, logger)
        return

    if args.command == "setup":
        cmd_setup()
        return

    db = Database(config["storage"]["db_path"])
    ai_client = AIClient(config, logger)

    try:
        if args.command == "quickstart":
            path = resolve_import_file(args.file)
            if not path:
                return
            _run_pipeline(db, config, logger, ai_client, path, dedup=args.dedup, html=not args.no_html)
            return

        if args.command == "import":
            result = cmd_import(db, config, logger, file=args.file, dedup=args.dedup)
            if result and result[1] > 0:
                print_next_hint("import")

        elif args.command == "add":
            new_id = cmd_add(db, config, logger, args.text, args.rating, args.date, args.product, args.category)
            if new_id:
                print_next_hint("add")

        elif args.command == "clean":
            cmd_clean(db, config, logger, args.dedup)
            print_next_hint("clean")

        elif args.command == "analyze":
            if args.id is not None:
                target = "id"
            elif args.all:
                target = "all"
            else:
                target = "unanalyzed"
            cmd_analyze(db, config, logger, ai_client, target=target, review_id=args.id,
                        limit=args.limit, assume_yes=args.yes)
            print_next_hint("analyze", db)

        elif args.command == "extract":
            insights = cmd_extract(db, config, logger, ai_client, sentiment=args.sentiment, date_from=args.date_from,
                                    date_to=args.date_to, product=args.product, category=args.category, limit=args.limit)
            if insights:
                print_next_hint("extract")

        elif args.command == "list":
            size = args.size or config.get("pagination", {}).get("default_page_size", 10)
            query.list_reviews(
                db, config=config, sentiment=args.sentiment, rating=args.rating,
                rating_min=args.rating_min, rating_max=args.rating_max,
                date_from=args.date_from, date_to=args.date_to, product=args.product,
                category=args.category, language=args.language,
                page=args.page, page_size=size, sort_by=args.sort, sort_dir=args.order,
            )

        elif args.command == "search":
            size = args.size or config.get("pagination", {}).get("default_page_size", 10)
            cmd_search(db, config, args.keyword, page=args.page, page_size=size)

        elif args.command == "show":
            query.show_review(db, args.id, config=config)

        elif args.command == "stats":
            query.print_stats(db, config=config)

        elif args.command == "dashboard":
            cmd_dashboard(db, config, logger, fmt=args.format, html=args.html, alert_days=args.alert_days)
            print_next_hint("dashboard")

        elif args.command == "export":
            sentiment = None if args.sentiment == "all" else args.sentiment
            cmd_export(db, config, logger, args.format, sentiment=sentiment,
                       rating_min=args.rating_min, category=args.category, output=args.output)

        elif args.command == "alert":
            alerts.check_negative_spike(db, config, logger, days=args.days)

        elif args.command == "compare":
            targets = args.targets.split(",") if args.targets else None
            results = compare.compare_by(db, logger, by=args.by, targets=targets)
            compare.print_comparison(results, by=args.by)

    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] 사용자에 의해 중단되었습니다.")
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 - 예기치 못한 오류도 traceback 대신 깔끔한 메시지로 안내
        _logger = logging.getLogger("review_dashboard")
        if _logger.handlers:  # 로거가 이미 설정된 경우에만 상세 traceback을 로그 파일에 남긴다
            _logger.error(f"예기치 못한 오류: {e}\n{traceback.format_exc()}")
        print(f"[ERROR] 예기치 못한 오류가 발생했습니다: {e}")
        print("[ERROR] 자세한 내용은 logs/app.log 를 확인하세요 (로그 파일이 아직 없다면 콘솔 출력이 전부입니다).")
        sys.exit(1)

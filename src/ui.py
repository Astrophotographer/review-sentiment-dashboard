"""
사용자 편의성 UI 헬퍼 모듈 (src/ui.py)
----------------------------------------
외부 라이브러리(rich 등) 없이 표준 라이브러리만으로 터미널 사용성을 높인다:
  - 색상 있는 상태 메시지 (성공/실패/경고/안내)
  - 진행률 바 (analyze처럼 여러 건을 처리할 때)
  - 박스 드로잉 테이블 (list/stats 출력용)
  - y/n 확인 프롬프트
  - "다음 단계 추천" 힌트

터미널이 색상을 지원하지 않는 환경(파일로 리다이렉트, Windows 구형 콘솔, CI 등)에서는
자동으로 색상 코드를 생략한다. `NO_COLOR` 환경변수로도 강제로 끌 수 있다
(https://no-color.org 관례를 따름).
"""
import os
import sys
import shutil
import unicodedata
import logging
import contextlib


class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"


def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR") is not None:
        return True
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _paint(text: str, *codes: str) -> str:
    if not _color_enabled():
        return text
    return "".join(codes) + text + _C.RESET


# ── 상태 메시지 ──────────────────────────────────────────────────────────
def success(msg: str):
    print(_paint("✔ ", _C.GREEN, _C.BOLD) + msg)


def error(msg: str):
    print(_paint("✘ ", _C.RED, _C.BOLD) + msg)


def warn(msg: str):
    print(_paint("⚠ ", _C.YELLOW, _C.BOLD) + msg)


def info(msg: str):
    print(_paint("ℹ ", _C.BLUE) + msg)


def header(title: str):
    width = min(shutil.get_terminal_size((80, 20)).columns, 70)
    line = "─" * max(4, width - len(title) - 4)
    print("\n" + _paint(f"── {title} {line}", _C.BOLD, _C.CYAN))


def bold(text: str) -> str:
    return _paint(text, _C.BOLD)


def dim_text(text: str) -> str:
    return _paint(text, _C.DIM)


def hint(msg: str):
    print(_paint("💡 다음 단계: ", _C.DIM) + msg)


def dim(msg: str):
    print(_paint(msg, _C.DIM))


# ── 사용자 입력 ──────────────────────────────────────────────────────────
def confirm(prompt: str, default: bool = False) -> bool:
    """대화형 터미널이 아니면(파이프/CI 등) default 값을 그대로 사용해 멈추지 않는다."""
    if not sys.stdin.isatty():
        return default
    suffix = "Y/n" if default else "y/N"
    try:
        ans = input(_paint(f"{prompt} ({suffix}): ", _C.YELLOW)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not ans:
        return default
    return ans in ("y", "yes")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(_paint(f"{prompt}{suffix}: ", _C.CYAN)).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return ans or default


def choose(prompt: str, options: list) -> int:
    """옵션 리스트를 번호와 함께 보여주고 선택한 인덱스(0-based)를 반환한다."""
    for i, opt in enumerate(options, start=1):
        print(f"  {_paint(str(i), _C.BOLD)}. {opt}")
    while True:
        raw = ask(prompt, default="1" if len(options) == 1 else "")
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        error(f"1~{len(options)} 사이 숫자를 입력하세요.")


# ── 진행률 바 ────────────────────────────────────────────────────────────
@contextlib.contextmanager
def suppressed_console_logging(logger):
    """진행률 바가 로그 줄과 뒤섞여 깨져 보이지 않도록, 블록 안에서는 콘솔 핸들러만
    잠시 WARNING 이상만 보이게 조용히 시킨다. 파일 핸들러는 그대로 유지되므로
    logs/app.log 에는 평소처럼 건별 상세 기록이 전부 남는다."""
    console_handlers = [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    original_levels = [h.level for h in console_handlers]
    for h in console_handlers:
        h.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for h, lvl in zip(console_handlers, original_levels):
            h.setLevel(lvl)


def progress(current: int, total: int, label: str = "", width: int = 28):
    if total <= 0 or not sys.stdout.isatty():
        return
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = current / total * 100
    line = f"\r{label} [{_paint(bar, _C.CYAN)}] {current}/{total} ({pct:.0f}%)"
    sys.stdout.write(line)
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


# ── 박스 드로잉 테이블 ────────────────────────────────────────────────────
def _display_width(text: str) -> int:
    """한글/한자 등 동아시아 넓은 문자는 터미널에서 2칸을 차지하므로,
    len() 대신 이 함수로 실제 표시 폭을 계산해야 표가 깨지지 않는다."""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _pad(text: str, target_width: int) -> str:
    return text + " " * max(0, target_width - _display_width(text))


def table(headers: list, rows: list, max_col_width: int = 28):
    """리스트/통계 출력을 깔끔한 표 형태로 그린다 (외부 라이브러리 불필요).
    한글이 섞여도 열이 어긋나지 않도록 표시 폭 기준으로 정렬한다."""
    if not rows:
        print(_paint("  (표시할 데이터가 없습니다)", _C.DIM))
        return

    str_rows = [[_truncate(str(c), max_col_width) for c in row] for row in rows]
    widths = [_display_width(h) for h in headers]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_width(cell))

    def _line(l, m, r):
        return l + m.join("─" * (w + 2) for w in widths) + r

    def _row(cells, is_header=False):
        parts = []
        for cell, w in zip(cells, widths):
            text = _pad(cell, w)
            if is_header:
                text = _paint(text, _C.BOLD)
            parts.append(" " + text + " ")
        return "│" + "│".join(parts) + "│"

    print(_line("┌", "┬", "┐"))
    print(_row(headers, is_header=True))
    print(_line("├", "┼", "┤"))
    for row in str_rows:
        print(_row(row))
    print(_line("└", "┴", "┘"))


def _truncate(text: str, max_len: int) -> str:
    """표시 폭 기준으로 자른다 (한글이 중간에서 안 잘리도록)."""
    if _display_width(text) <= max_len:
        return text
    out, w = "", 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > max_len - 1:
            return out + "…"
        out += ch
        w += cw
    return out

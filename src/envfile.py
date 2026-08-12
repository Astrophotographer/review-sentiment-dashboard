"""
.env 파일 지원 (src/envfile.py)
--------------------------------
매번 새 터미널을 열 때마다 `export ANTHROPIC_API_KEY=...`를 다시 치지 않아도
되도록, 프로젝트 루트의 `.env` 파일에서 KEY=VALUE 쌍을 읽어 환경변수로
등록해준다.

- 이미 설정되어 있는 실제 환경변수는 절대 덮어쓰지 않는다 (12-factor 관례:
  진짜 환경변수가 .env 파일보다 항상 우선한다).
- .env 파일은 API 키 같은 민감정보를 담을 수 있으므로 .gitignore에 등록해
  실수로 커밋되지 않도록 한다 (main.py에서 `python main.py setup` 실행 시 자동 확인).
- 외부 라이브러리(python-dotenv) 없이 표준 라이브러리만으로 구현했다
  (요구사항의 "API 키는 환경변수 또는 설정 파일로 관리한다"를 그대로 만족).
"""
import os


def parse_dotenv(text: str) -> dict:
    """'.env' 파일 내용을 파싱해 {키: 값} 딕셔너리로 반환한다.
    빈 줄과 '#' 주석 줄은 무시하고, 값 양끝의 따옴표는 제거한다."""
    result = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def load_dotenv(path: str = ".env") -> int:
    """.env 파일을 읽어 os.environ에 등록한다 (이미 설정된 값은 건드리지 않음).
    반환값: 새로 등록된 변수 개수. 파일이 없으면 조용히 0을 반환한다."""
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        pairs = parse_dotenv(f.read())
    applied = 0
    for key, value in pairs.items():
        if key not in os.environ:
            os.environ[key] = value
            applied += 1
    return applied


def write_dotenv(path: str, updates: dict):
    """.env 파일에 값을 저장한다. 이미 있는 키는 값만 갱신하고, 없는 키는 새로 추가한다."""
    existing_lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing_lines = f.read().splitlines()

    seen_keys = set()
    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                seen_keys.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in seen_keys:
            new_lines.append(f"{key}={value}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + ("\n" if new_lines else ""))


def remove_dotenv_keys(path: str, keys) -> int:
    """.env 에서 지정한 키 줄을 제거한다. 반환값: 실제로 지운 키 개수."""
    remove = {k for k in keys if k}
    if not remove or not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        existing_lines = f.read().splitlines()
    new_lines = []
    removed = 0
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remove:
                removed += 1
                continue
        new_lines.append(line)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + ("\n" if new_lines else ""))
    return removed


def ensure_gitignored(env_path: str = ".env", gitignore_path: str = ".gitignore"):
    """.env 가 실수로 git에 커밋되지 않도록 .gitignore에 등록되어 있는지 확인하고,
    없으면 자동으로 추가한다."""
    entry = os.path.basename(env_path)
    lines = []
    if os.path.exists(gitignore_path):
        with open(gitignore_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    if entry not in [l.strip() for l in lines]:
        with open(gitignore_path, "a", encoding="utf-8") as f:
            f.write(("\n" if lines else "") + entry + "\n")
        return True
    return False

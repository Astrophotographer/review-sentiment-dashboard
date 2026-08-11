# 모델 채점 스냅샷 비교 페이지 설계

**날짜:** 2026-08-11  
**상태:** 승인 대기 (구현 전)  
**관련:** DGX Spark / Anthropic / 규칙 기반 폴백 채점 결과 비교

## 목표

대시보드에서 이미 돌려 둔 **모델별 채점 스냅샷**을 골라, 같은 리뷰 집합에 대한 감정 판정을 나란히 비교한다.  
비교 시점에 새 LLM 호출은 하지 않는다 (옵션 2).

## 비목표

- 비교 페이지에서 여러 모델을 동시에 재실행하는 A/B 러너 (옵션 1)
- 벡터 유사도·임베딩 기반 비교
- 과제 제출용 정적 HTML만으로 비교 API를 대체하는 것 (비교 UI는 `serve` 모드에서 동작)

## 데이터 모델

SQLite에 스냅샷 2개 테이블을 추가한다.

### `model_runs`

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | INTEGER PK | 런 ID |
| `provider` | TEXT | `spark` / `anthropic` / `fallback` |
| `model` | TEXT | 예: `qwen`, `claude-haiku-…`, `규칙 기반` |
| `label` | TEXT | UI 표시명 (자동 생성 가능) |
| `created_at` | TEXT | ISO 시각 |
| `review_count` | INTEGER | 저장된 판정 수 |
| `analyzed_count` | INTEGER | sentiment 비어 있지 않은 수 |
| `temp_c` | REAL NULL | Spark 선택 시 저장 시점 GPU 온도 (없으면 NULL) |
| `notes` | TEXT NULL | 선택 메모 |

### `model_run_results`

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `run_id` | INTEGER | FK → model_runs.id |
| `review_id` | INTEGER | clean_reviews.id |
| `sentiment` | TEXT NULL | positive/negative/neutral |
| `confidence` | REAL NULL | 0~1 |
| PRIMARY KEY | `(run_id, review_id)` | |

`clean_reviews`의 현재 감정 컬럼은 **“지금 대시보드가 보여주는 활성 결과”**로 유지한다.  
스냅샷은 별도 보관이며, 재분석해도 과거 런은 덮어쓰지 않는다 (새 `model_runs` row 추가).

### 마이그레이션

앱/서버 기동 또는 DB 오픈 시 `CREATE TABLE IF NOT EXISTS`로 스키마를 만든다.  
기존 DB에 이미 분석된 `clean_reviews`가 있으면, **최초 1회** 다음 조건으로 시드 런을 만든다:

- `model_runs`가 비어 있고
- `clean_reviews`에 `sentiment IS NOT NULL`인 행이 1건 이상

시드 메타:

- `provider` / `model` / `label`: config의 현재 AI 설정이 있으면 그것을 쓰고, 없으면 `fallback` / `규칙 기반` / `기존 분석 결과`
- 모든 분석된 리뷰의 sentiment/confidence를 `model_run_results`에 복사

## 스냅샷 생성 시점

다음 경로에서 **재분석이 성공적으로 끝난 뒤** 스냅샷을 저장한다.

1. 대시보드 `POST /api/analyze` (모델 선택 후 「이 모델로 재분석」)
2. CLI `python main.py analyze` (선택: 성공 건수가 0보다 클 때). 구현 범위에 포함하되, UI 비교의 주 경로는 1번.

저장 내용:

- config의 `provider`, `sentiment_model`
- Spark이면 `spark_device_status().temp_c`를 가능하면 기록
- 당시 `clean_reviews` 전체(또는 분석된 행)의 `id, sentiment, confidence`

실패(전체 실패) 시 스냅샷을 만들지 않는다. 부분 성공이면 성공/실패 혼재 상태를 그대로 저장하고 `analyzed_count`에 반영한다.

## 비교 페이지 UI

경로: `http://127.0.0.1:8765/compare.html`  
생성: `serve`가 정적 파일을 제공. HTML은 `src/reporter.py` 또는 전용 빌더로 `output/compare.html`에 생성하거나, 서버가 템플릿을 직접 서빙한다.  
권장: **서버가 `output/compare.html`을 빌드/갱신**하고 메인 대시보드 헤더/모델바에 「모델 비교」 링크를 둔다.

### 레이아웃 (한 페이지, 카드 남발 금지)

1. **런 선택:** 드롭다운 A, 드롭다운 B (최신순). 같은 런 선택 시 비교 비활성 + 안내.
2. **요약 KPI (한 줄~한 블록):**
   - 공통 리뷰 수
   - 일치율 (%)
   - A/B 각각의 긍정·중립·부정 비율
   - 평균 신뢰도 A/B
   - (있으면) 스냅샷 온도 A/B
3. **불일치 표:** review_id, 제품, 리뷰 일부, A 감정/신뢰도, B 감정/신뢰도. 기본 상위 50건, 「더 보기」는 선택.
4. **간단 차트 (Chart.js 내장 재사용):** A vs B 감정 분포 막대 비교 1개면 충분.

필터(카테고리/제품)는 1차 범위에서 넣지 않는다. 필요하면 후속.

## API (`serve` 전용)

| 메서드 | 경로 | 역할 |
|--------|------|------|
| GET | `/api/runs` | 스냅샷 목록 (id, provider, model, label, created_at, counts, temp_c) |
| GET | `/api/compare?a=&b=` | 두 런 비교 JSON (summary + disagreements[]) |
| GET | `/compare.html` | 비교 페이지 |

`/api/analyze` 완료 후 스냅샷 저장이 끝나면 `/api/runs`에 새 항목이 보여야 한다.

비교 계산 (서버):

- 교집합: 양쪽에 모두 존재하는 `review_id`
- 일치: 양쪽 `sentiment`가 동일 (NULL은 서로 NULL일 때만 일치로 칠지 → **둘 다 NULL이면 비교 제외**, 한쪽만 NULL이면 불일치)
- 불일치 목록: sentiment가 다른 행만, `abs(confidence_a - confidence_b)` 내림차순 정렬

## 메인 대시보드 연동

- 모델바 근처 또는 헤더에 `모델 비교` 링크 → `/compare.html`
- 재분석 완료 후 새로고침 시 비교 페이지에서도 새 런이 보여야 함

## 오류·빈 상태

- 런이 0개: 「먼저 대시보드에서 재분석을 실행해 스냅샷을 만드세요」 + 메인 링크
- 런이 1개: B 선택 불가 안내, 시드만 있는 경우 추가 재분석 유도
- Spark 온도 NULL: KPI에서 온도 칸 숨김

## 테스트

- DB: 스냅샷 insert/조회, 시드 마이그레이션 1회성
- compare API: 고정 fixture 두 런 → 일치율·불일치 목록 검증
- 재분석 잡이 스냅샷 row를 추가하는지 (mock AI)

## 구현 순서 (참고)

1. `db.py` 스키마 + seed + save/list/compare 헬퍼  
2. `dashboard_server` analyze 완료 시 스냅샷 저장 + `/api/runs`, `/api/compare`  
3. `compare.html` + JS  
4. 메인 대시보드 링크  
5. 테스트

## 승인된 결정

- 비교 방식: **저장된 스냅샷끼리 비교** (옵션 2)
- 실시간 다중 모델 동시 채점은 하지 않음
- Spark 온도는 스냅샷 메타에 있을 때만 표시

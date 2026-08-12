# Multi-provider API 키 · 모델 표시명 설계

**날짜:** 2026-08-12  
**상태:** 승인됨  
**관련:** 모델 채점 스냅샷 비교, 대시보드 모델바

## 목표

로컬 대시보드에서 **Spark / OpenAI / Anthropic / 규칙 폴백**을 채점 엔진으로 고르고, 필요한 API 키를 모델바에서 바로 `.env`에 저장할 수 있게 한다.  
비교·모델바에는 짧은 별칭(`qwen`) 대신 **구체 표시명**(예: `qwen 3.5 122b`)을 쓰고, 연도·날짜 접미사는 표시에서 제거한다.

## 비목표

- Google Gemini 등 A 범위 밖 provider
- 임의 OpenAI 호환 커스텀 base URL UI (Spark의 기존 `base_url`은 유지)
- 비교 페이지에서 실시간 다중 모델 동시 재채점
- API 키를 config.json / DB / 화면에 재표시

## Provider 구조

| provider | 호출 | 기본 base URL | 키 (`.env`) | 모델 목록 |
| -------- | ---- | ------------- | ----------- | --------- |
| `spark` | OpenAI 호환 chat completions | `config.ai.base_url` | `SPARK_API_KEY` | `{base}/models` |
| `openai` | 위와 동일 | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `{base}/models` |
| `anthropic` | Anthropic Messages API | 고정 | `ANTHROPIC_API_KEY` | config 후보 + 선택값 |
| `fallback` | 없음 | — | 불필요 | `규칙 기반` |

아키텍처: **OpenAI 호환 공통 경로 + Anthropic만 별도.**  
`AIClient`에서 `spark` / `openai`는 동일 `_call_openai`(또는 공용 chat-completions) 경로를 쓰고, 키·URL·목록만 provider별로 분기한다.

## 대시보드 키 입력

### UI

Spark 전용 키 바를 **provider 공통 키 바**로 교체한다.

- `spark` / `openai` / `anthropic`이고 해당 키가 없으면 표시
- 라벨·placeholder는 provider별 (`SPARK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`)
- 저장 후 input 비움. 키 값은 다시 보여주지 않고 `*_key_set`만 status에 반영
- `fallback`이면 숨김

### API

| 메서드 | 경로 | 역할 |
| ------ | ---- | ---- |
| POST | `/api/provider-key` | `{ provider, key }` → `.env`에 해당 env 저장 + `os.environ` 갱신 |
| POST | `/api/spark-key` | 호환 유지: 내부에서 `provider=spark`로 위임 |

`GET /api/ai-status`(또는 기존 status 엔드포인트)에 `openai_key_set` 추가, `providers`에 OpenAI 포함.  
키는 **config.json에 쓰지 않는다**.

## 선택 · 재분석 흐름

1. provider 선택 → 키 없으면 키 바 표시  
2. 키 저장 → OpenAI/Spark는 `/models` 목록 갱신, Anthropic은 후보 목록  
3. 모델 선택 → 「이 모델로 재분석」  
4. `config.json`에 **호출용 model id**와 provider 저장 후 재채점  
5. 성공 시 스냅샷: 표시용 모델명 규칙 적용 후 `model` / `label` 저장  
6. 비교 페이지는 스냅샷끼리만 비교 (기존)

## 모델 표시명

API 호출용 id와 화면 표시명을 구분한다.

- 공통 `format_model_display(name)`:
  - `-` / `_`를 공백 등으로 가독성 있게 정리
  - 끝의 날짜·연도 토큰 제거 (`20251001`, `-2026`, `_2024` 등)
  - 예: `claude-haiku-4-5-20251001` → `claude haiku 4.5` (동등한 가독 형태 허용)
- Spark 스냅샷·표시: health의 `model`(예: `qwen 3.5 122b`)이 있으면 우선, 없으면 `/models` id
- 비교 카드·드롭다운·런 label·모델바 status에 표시명 사용
- 이미 저장된 옛 스냅샷은 표시만 포맷; health를 소급 적용하지 않으면 `qwen`으로 남을 수 있음 (신규 런부터 구체명)

## 오류 처리

- 키 없음 / 401: status 안내, 키 바 재표시, 재분석 시작 전 차단 가능
- `/models` 실패: 현재 config 모델 id만 후보 + warn
- 분석 중 API 실패: 기존처럼 건별 스킵 + 로그, 전체 0건이면 스냅샷 미생성
- OpenAI/Anthropic 네트워크 오류: Spark와 같은 status 메시지 패턴

## 테스트

- OpenAI provider 설정·키 저장·`.env` 반영
- spark/openai 공통 chat completions 경로
- `format_model_display` 날짜 제거·Spark health 이름 우선
- 비교 UI에 구체 표시명

## 승인된 결정

- Provider 범위: Spark + Anthropic + OpenAI + 폴백 (A)
- OpenAI 모델 목록: `/v1/models` 원격 (A)
- 키 저장: `.env` (A, Spark와 동일)
- 구현 방식: OpenAI 호환 공통 경로 + Anthropic 별도 (2)
- 표시명: Spark health 구체명 우선, 연도/날짜 접미사 표시에서 제거

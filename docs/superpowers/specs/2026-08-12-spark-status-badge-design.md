# Spark 상태 뱃지 디자인

**날짜:** 2026-08-12  
**상태:** 승인됨  
**관련:** 대시보드 모델 바의 Spark GPU 온도 표시

## 목표

모델 바의 `Spark GPU 69°C` 색상 칩을, 상태 점 + 한글 상태문구 + 온도 형식의 뱃지로 바꾼다.  
밝은 대시보드 테마는 유지한다 (Approach A).

## 비목표

- 헤더 알림 시그널(부정 리뷰 급증) 변경
- 어두운 네이비 알약 UI 복제
- 서버에서 status 필드를 계산해 내려주는 방식

## 표시 형식

`● {상태문구} {온도}°C` (온도를 알 수 없으면 온도 생략)

| 상태 | 클래스 | 색 | 문구 |
|------|--------|----|------|
| 정상 | `ok` | 초록 | 연결됨 |
| 이상 | `warn` | 노랑 | 이상있음 |
| 심각 | `error` | 빨강 | 심각한 오류발생 |
| 끊김 | `offline` | 회색 | 접속끊김 |

## 판정 규칙

provider가 `spark`일 때만 뱃지를 표시한다.

1. **접속끊김 (`offline`):** health 실패 (`spark.ok`가 아님) 또는 응답 없음
2. **심각한 오류발생 (`error`):** health OK + `temp_c >= 90`
3. **이상있음 (`warn`):** health OK + (`75 <= temp_c < 90` 또는 `temp_c` 없음)
4. **연결됨 (`ok`):** health OK + `temp_c < 75`

임계값 근거: 기존 65°C warm은 정상 추론 온도(예: 69°C)까지 노랑으로 보이게 해 오해를 낳음.  
75°C를 주의, 90°C를 열 위험으로 둔다.

## 구현 범위

- `src/reporter.py` — `.spark-temp` CSS 클래스 정리, 초기 placeholder 문구
- `src/dashboard_model_controls.js` — `updateSparkTemp()` 판정·문구
- `output/dashboard.html` — 재생성 후 Vercel 정적 배포용 (gitignored)

## 배포

- Git: 소스 변경을 `origin`에 push
- Vercel: `reviewdash.vercel.app`에 `output/` 정적 배포

# 실행 검증 기록 (2026-11-19)

연습 프로젝트를 로컬 Docker로 실행해 확인한 결과입니다.
공공데이터 API 키는 `.env`로만 넣었고 저장소에는 포함하지 않았습니다.

## 1. 컨테이너 실행 상태
명령: `docker compose ps`

```
SERVICE     IMAGE                                STATUS
api         mini_scholarship_v2-main-api         Up
collector   mini_scholarship_v2-main-collector   Up
mailpit     axllent/mailpit                      Up (healthy)
nginx       nginx:1.27                           Up
postgres    pgvector/pgvector:pg16               Up (healthy)
redis       redis:7                              Up
worker      mini_scholarship_v2-main-worker      Up
```

## 2. 한국장학재단 API로 수집한 공고 수
명령: `docker compose exec postgres psql -U app -d app -c "SELECT count(*) FROM notices;"`

```
 공고수
--------
     28
(1 row)
```

- 수집 조건: 모집 마감이 지나지 않은 공고만 저장, 최대 2,000건 조회 (`MAX_PAGES = 20`)

## 3. 학생 매칭 결과
테스트 학생 1명 생성 (`POST /api/students`): 3학년, 서울, 소득 5분위 (가짜 데이터)

명령: `GET /api/matches/1`

| 판정 | 건수 | 공고 번호 |
|---|---|---|
| 가능 | 2 | 25, 28 |
| 불가 | 2 | 11, 18 (지역 조건 불일치) |
| 확인 필요 | 24 | 나머지 |

응답 예시 (일부):
```json
{ "notice_id": 11, "verdict": "불가",      "details": { "region": "FAIL" } }
{ "notice_id": 25, "verdict": "가능",      "details": { "region": "PASS" } }
{ "notice_id": 1,  "verdict": "확인 필요", "details": { "grade": "UNKNOWN", "income": "PASS", "region": "PASS" } }
```

## 확인된 것
- 공공데이터 API → 수집 서버 → DB 저장 → 매칭 API까지 실제 데이터로 동작
- 지역 조건이 다른 공고는 불가로, 판단할 수 없는 조건은 확인 필요로 분류

## 발견한 문제
- 공고 대부분이 학년 조건 UNKNOWN → 공공데이터의 학년 표기 형식("대학2학기…")을 현재 추출 코드가 인식하지 못함
- 개선 코드는 작성했으나 재수집 결과 확인 전이라 반영하지 않음

## 확인하지 못한 것
- LLM 호출: API 키를 쓰지 않아 실제 호출은 검증하지 않음

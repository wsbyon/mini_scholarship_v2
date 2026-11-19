"""수집 서버: 내장 스케줄러로 주기 실행 (1개만 띄운다).
⑨ 공공데이터포털 Open API (HTTPS)  →  조건 추출(규칙)  →  ⑩ PostgreSQL UPSERT
조건이 불확실하면 NEEDS_REVIEW 로 저장하고 관리자 검토(REQUIREMENT)를 만든다."""
import os
import re
import time
import traceback
from datetime import date

import httpx

import db

API_URL = os.environ.get("PUBLIC_API_URL", "")       # 활용신청 후 Swagger 화면에 나오는 주소
API_KEY = os.environ.get("PUBLIC_API_KEY", "")       # 일반 인증키(Decoding)
INTERVAL = int(os.environ.get("COLLECT_INTERVAL_SECONDS", "300"))
PER_PAGE, MAX_PAGES = 100, 20                         # 모집 중 공고를 찾기 위해 최대 2,000건 조회

SIDO = {"서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천", "광주": "광주", "대전": "대전",
        "울산": "울산", "세종": "세종", "경기": "경기", "강원": "강원", "충청북": "충북", "충북": "충북",
        "충청남": "충남", "충남": "충남", "전라북": "전북", "전북": "전북", "전라남": "전남", "전남": "전남",
        "경상북": "경북", "경북": "경북", "경상남": "경남", "경남": "경남", "제주": "제주"}
NONE_WORDS = ("해당없음", "해당 없음", "제한없음", "제한 없음", "무관", "전체")

# 키가 없을 때 쓰는 가짜 행 (실제 API 응답과 같은 칸 이름)
SAMPLE_ROWS = [
    {"운영기관명": "샘플재단", "상품명": "희망장학금", "학년구분": "대학2학년,대학3학년",
     "소득기준 상세내용": "학자금지원구간 6구간 이하", "지역거주여부 상세내용": "해당없음", "모집종료일": "2099-12-31"},
    {"운영기관명": "샘플시청", "상품명": "지역인재장학금", "학년구분": "제한없음",
     "소득기준 상세내용": "기준중위소득 120% 이하", "지역거주여부 상세내용": "본인 또는 부모가 부산광역시 거주", "모집종료일": "2099-12-31"},
]


# ── ⑨ 가져오기 ──
def fetch_rows():
    if not (API_URL and API_KEY):
        print("[수집] 키 없음 → 샘플 데이터 사용 (⑨ 미검증)", flush=True)
        return SAMPLE_ROWS
    rows = []
    for page in range(1, MAX_PAGES + 1):
        res = httpx.get(API_URL, params={"page": page, "perPage": PER_PAGE, "returnType": "JSON",
                                         "serviceKey": API_KEY}, timeout=30)
        res.raise_for_status()                         # 401(키 오류) 등이면 여기서 에러
        body = res.json()
        rows += body.get("data", [])
        print(f"[수집] ⑨ HTTPS {res.status_code} · {page}페이지 {len(body.get('data', []))}건 "
              f"(전체 {body.get('totalCount')}건)", flush=True)
        if page * PER_PAGE >= body.get("totalCount", 0):
            break
    return rows


# ── 조건 추출 (규칙). 확신이 없으면 value=None, status=NEEDS_REVIEW ──
def is_none(text):
    return (not text) or any(w in text for w in NONE_WORDS)


def extract_grade(text):
    if is_none(text):
        return None                                   # 학년 제한 없음 → 조건을 만들지 않음
    grades = sorted(set(re.findall(r"([1-4])\s*학년", text)))
    if grades:
        return ("grade", ",".join(grades), "CONFIRMED")
    return ("grade", None, "NEEDS_REVIEW")


def extract_income(text):
    if is_none(text):
        return None
    m = re.search(r"(\d{1,2})\s*구간\s*이하", text) or re.search(r"소득\s*(\d{1,2})\s*분위\s*이하", text)
    if m and "%" not in text:
        return ("income", m.group(1), "CONFIRMED")
    return ("income", None, "NEEDS_REVIEW")          # 예: '기준중위소득 120%' → 단위가 달라 사람이 판단


def extract_region(text):
    if is_none(text):
        return ("region", "전국", "CONFIRMED")
    found = {short for long, short in SIDO.items() if long in text}
    if len(found) == 1 and "부모" not in text:
        return ("region", found.pop(), "CONFIRMED")
    return ("region", None, "NEEDS_REVIEW")          # 여러 지역 / '부모 거주' 같은 조건 → 사람이 판단


def extract(row):
    reqs = [extract_grade(row.get("학년구분", "")),
            extract_income(row.get("소득기준 상세내용", "")),
            extract_region(row.get("지역거주여부 상세내용", ""))]
    return [r for r in reqs if r is not None]


def parse_date(text):
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


# ── ⑩ 저장 ──
def save(row):
    source_id = f"{row.get('운영기관명', '')}|{row.get('상품명', '')}"
    title = f"{row.get('운영기관명', '')} {row.get('상품명', '')}".strip()
    body = "\n".join(f"{k}: {v}" for k, v in row.items() if v)
    with db.get_conn() as conn:
        # UPSERT: 없으면 넣고, 있으면 내용만 갱신. xmax = 0 이면 이번에 새로 들어간 행
        notice = conn.execute(
            "INSERT INTO notices (source_id, title, body, deadline) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (source_id) DO UPDATE SET title = EXCLUDED.title, body = EXCLUDED.body, "
            "  deadline = EXCLUDED.deadline, collected_at = now() "
            "RETURNING id, (xmax = 0) AS inserted",
            (source_id, title, body, parse_date(row.get("모집종료일")))).fetchone()
        if not notice["inserted"]:
            return "updated", 0                       # 이미 있던 공고: 관리자가 확정한 조건을 덮어쓰지 않는다
        reviews = 0
        for field, value, status in extract(row):
            req = conn.execute(
                "INSERT INTO notice_requirements (notice_id, field, value, status) VALUES (%s, %s, %s, %s) RETURNING id",
                (notice["id"], field, value, status)).fetchone()
            if status == "NEEDS_REVIEW":
                conn.execute("INSERT INTO reviews (kind, target_id) VALUES ('REQUIREMENT', %s)", (req["id"],))
                reviews += 1
    return "inserted", reviews


def collect_once():
    today = date.today()
    counts = {"inserted": 0, "updated": 0, "closed": 0, "reviews": 0}
    for row in fetch_rows():
        end = parse_date(row.get("모집종료일"))
        if end and end < today:
            counts["closed"] += 1                     # 마감된 공고는 건너뛴다
            continue
        result, reviews = save(row)
        counts[result] += 1
        counts["reviews"] += reviews
    print(f"[수집] ⑩ 저장 완료: 새 공고 {counts['inserted']} · 갱신 {counts['updated']} · "
          f"마감 제외 {counts['closed']} · 검토 요청 {counts['reviews']}", flush=True)


def main():
    print(f"수집 서버 시작 — {INTERVAL}초마다 수집", flush=True)
    while True:
        try:
            collect_once()
        except Exception:
            traceback.print_exc()                     # 실패해도 꺼지지 않고 다음 주기에 다시 시도
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

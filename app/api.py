"""API 서버: 요청을 받아 DB에 쓰고 읽는다. (3단계부터 큐 등록 추가)"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import db
import job_queue
from matching import judge

# /api/docs 로 열리게 해서 Nginx를 거쳐도 테스트 화면을 쓸 수 있게 한다
app = FastAPI(title="미니 장학금 v2", docs_url="/api/docs", openapi_url="/api/openapi.json")


class StudentIn(BaseModel):
    name: str
    email: str | None = None
    grade: int | None = None
    region: str | None = None
    income: int | None = None

class ApplicationIn(BaseModel):
    student_id: int
    notice_id: int


class AnswerIn(BaseModel):
    answer: str

@app.get("/api/health")
def health():
    with db.get_conn() as conn:
        conn.execute("SELECT 1")              # ③ DB까지 실제로 다녀온다
    return {"api": "ok", "db": "ok"}


@app.post("/api/students")
def create_student(s: StudentIn):
    with db.get_conn() as conn:
        row = conn.execute(
            "INSERT INTO students (name, email, grade, region, income) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (s.name, s.email, s.grade, s.region, s.income),
        ).fetchone()
    return {"student_id": row["id"]}


@app.get("/api/students")
def list_students():
    with db.get_conn() as conn:
        return conn.execute("SELECT * FROM students ORDER BY id").fetchall()


@app.get("/api/matches/{student_id}")
def matches(student_id: int):
    with db.get_conn() as conn:
        student = conn.execute("SELECT * FROM students WHERE id = %s", (student_id,)).fetchone()
        if student is None:
            raise HTTPException(404, "학생이 없어요")
        notices = conn.execute("SELECT id, title FROM notices ORDER BY id").fetchall()
        reqs = conn.execute("SELECT * FROM notice_requirements").fetchall()

    results = []
    for n in notices:
        my_reqs = [r for r in reqs if r["notice_id"] == n["id"]]
        verdict, details = judge(my_reqs, student)
        results.append({"notice_id": n["id"], "title": n["title"], "verdict": verdict, "details": details})
    return {"student_id": student_id, "results": results}


# ── 테스트용: 수집 서버(5단계)가 생기기 전까지 공고를 직접 넣는다 ──
SAMPLES = [
    ("S-101", "A재단 장학금", [("income", "8", "CONFIRMED"), ("grade", "1,2,3,4", "CONFIRMED"), ("region", "전국", "CONFIRMED")]),
    ("S-102", "B시 장학금",   [("region", "경기", "CONFIRMED")]),
    ("S-103", "C재단 장학금", [("grade", "3,4", "CONFIRMED")]),
    ("S-104", "D재단 장학금", [("region", "서울", "CONFIRMED"), ("income", None, "NEEDS_REVIEW")]),
    ("S-105", "E재단 장학금", [("grade", "1,2", "CONFIRMED"), ("region", "전국", "CONFIRMED")]),
]


@app.post("/api/dev/seed")
def seed():
    added = 0
    with db.get_conn() as conn:
        for source_id, title, reqs in SAMPLES:
            row = conn.execute(
                "INSERT INTO notices (source_id, title) VALUES (%s, %s) "
                "ON CONFLICT (source_id) DO NOTHING RETURNING id",   # 이미 있으면 건너뜀 (중복 방지)
                (source_id, title),
            ).fetchone()
            if row is None:
                continue
            added += 1
            for field, value, status in reqs:
                conn.execute(
                    "INSERT INTO notice_requirements (notice_id, field, value, status) VALUES (%s, %s, %s, %s)",
                    (row["id"], field, value, status),
                )
    return {"added": added}

# ── 신청 지원: 접수만 하고 202, 실제 일은 Worker 가 한다 ──
@app.post("/api/applications", status_code=202)
def apply(a: ApplicationIn):
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM students WHERE id = %s", (a.student_id,)).fetchone() is None:
            raise HTTPException(404, "학생이 없어요")
        if conn.execute("SELECT 1 FROM notices WHERE id = %s", (a.notice_id,)).fetchone() is None:
            raise HTTPException(404, "공고가 없어요")
        row = conn.execute(
            "INSERT INTO applications (student_id, notice_id) VALUES (%s, %s) RETURNING id",   # ③ status = QUEUED
            (a.student_id, a.notice_id),
        ).fetchone()
    job_queue.enqueue("ai_jobs", {"kind": "apply_run", "application_id": row["id"]})          # ④ LPUSH
    return {"application_id": row["id"], "status": "QUEUED"}


@app.get("/api/applications/{app_id}")
def get_application(app_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM applications WHERE id = %s", (app_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "신청이 없어요")
    return row


ANSWER_TYPES = {"income": int, "grade": int, "region": str}


@app.post("/api/applications/{app_id}/answer", status_code=202)
def answer(app_id: int, a: AnswerIn):
    with db.get_conn() as conn:
        # 상태 선점: WAITING_USER 일 때만 QUEUED 로 바꾼다 → 두 번 보내도 한 번만 처리
        row = conn.execute(
            "UPDATE applications SET status = 'QUEUED', question = NULL, updated_at = now() "
            "WHERE id = %s AND status = 'WAITING_USER' RETURNING student_id, pending_field",
            (app_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(409, "답변을 기다리는 상태가 아니에요")
        field = row["pending_field"]
        try:
            value = ANSWER_TYPES[field](a.answer)
        except ValueError:
            raise HTTPException(400, "형식이 맞지 않아요 (숫자를 입력해 주세요)")   # with 안의 에러 → 위 UPDATE 도 취소(rollback)
        # 답을 학생 정보에 저장 → 다른 공고에 신청해도 같은 질문을 다시 하지 않는다
        conn.execute(f"UPDATE students SET {field} = %s WHERE id = %s", (value, row["student_id"]))
    job_queue.enqueue("ai_jobs", {"kind": "apply_run", "application_id": app_id})             # ④ LPUSH
    return {"application_id": app_id, "status": "QUEUED"}

# ── 관리자 검토 (REQUIREMENT / DRAFT) ──
class DecisionIn(BaseModel):
    decision: str                 # APPROVED / REJECTED
    value: str | None = None      # REQUIREMENT 승인 시 기준값 (예: "8", "부산")


@app.get("/api/admin/reviews")
def list_reviews():
    with db.get_conn() as conn:
        reqs = conn.execute(
            "SELECT r.id, r.kind, q.field, n.title, n.body AS context "
            "FROM reviews r "
            "JOIN notice_requirements q ON q.id = r.target_id "
            "JOIN notices n ON n.id = q.notice_id "
            "WHERE r.kind = 'REQUIREMENT' AND r.status = 'PENDING'").fetchall()
        drafts = conn.execute(
            "SELECT r.id, r.kind, NULL AS field, n.title, a.result AS context "
            "FROM reviews r "
            "JOIN applications a ON a.id = r.target_id "
            "JOIN notices n ON n.id = a.notice_id "
            "WHERE r.kind = 'DRAFT' AND r.status = 'PENDING'").fetchall()
    return sorted(reqs + drafts, key=lambda r: r["id"])


@app.post("/api/admin/reviews/{review_id}/decide")
def decide(review_id: int, d: DecisionIn):
    if d.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(400, "decision 은 APPROVED 또는 REJECTED")
    resume_app = None
    with db.get_conn() as conn:
        # 상태 선점: PENDING 일 때만 처리 → 두 번 눌러도 한 번만
        review = conn.execute(
            "UPDATE reviews SET status = %s WHERE id = %s AND status = 'PENDING' RETURNING kind, target_id",
            (d.decision, review_id)).fetchone()
        if review is None:
            raise HTTPException(409, "이미 처리된 검토예요")

        if review["kind"] == "REQUIREMENT":
            if d.decision == "APPROVED":
                req = conn.execute("SELECT field FROM notice_requirements WHERE id = %s",
                                   (review["target_id"],)).fetchone()
                try:
                    ANSWER_TYPES[req["field"]](d.value or "")
                    if not d.value:
                        raise ValueError
                except ValueError:
                    raise HTTPException(400, "기준값을 올바르게 입력해 주세요")   # with 안 → 위 UPDATE 도 취소
                conn.execute("UPDATE notice_requirements SET value = %s, status = 'CONFIRMED' WHERE id = %s",
                             (d.value, review["target_id"]))
            else:
                conn.execute("UPDATE notice_requirements SET status = 'REJECTED' WHERE id = %s",
                             (review["target_id"],))
        else:  # DRAFT: 멈춰 있던 신청을 다시 큐로
            row = conn.execute(
                "UPDATE applications SET status = 'QUEUED', updated_at = now() "
                "WHERE id = %s AND status = 'WAITING_ADMIN' RETURNING id", (review["target_id"],)).fetchone()
            if row:
                resume_app = row["id"]
    if resume_app:
        job_queue.enqueue("ai_jobs", {"kind": "apply_run", "application_id": resume_app})   # ④ LPUSH
    return {"review_id": review_id, "status": d.decision}
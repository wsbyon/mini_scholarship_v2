"""신청 지원 LangGraph. 흐름(순서·분기·멈춤/재개)만 관리하고 실제 일은 ai_tasks 함수가 한다.

START → recheck ─불가────────────────────────────────→ END
            ├─학생 정보 빈칸 → ask ⏸ (WAITING_USER) → recheck
            └─다 채워짐 → draft → check_draft ─문제 없음──→ END
                                    └─[확인 필요] 남음 → wait_admin ⏸ (WAITING_ADMIN)
                                                          ├─승인 → END
                                                          └─반려 → draft (다시 작성)
멈춘 지점은 PostgreSQL 체크포인트에 저장된다 (화살표 ⑥). thread_id = 신청 1건.
"""
from typing import TypedDict

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row

import ai_tasks
import db
from matching import judge


class State(TypedDict, total=False):
    application_id: int
    student_id: int
    notice_id: int
    verdict: str
    details: dict
    missing: list
    draft: str
    needs_review: bool
    admin_decision: str


def load(student_id, notice_id):
    with db.get_conn() as conn:
        student = conn.execute("SELECT * FROM students WHERE id = %s", (student_id,)).fetchone()
        notice = conn.execute("SELECT * FROM notices WHERE id = %s", (notice_id,)).fetchone()
        reqs = conn.execute("SELECT * FROM notice_requirements WHERE notice_id = %s", (notice_id,)).fetchall()
    return student, notice, reqs


def missing_fields(student, reqs):
    # 공고 조건은 확실한데(CONFIRMED) 학생 정보가 비어 있는 항목 → 학생에게 물어볼 것
    return [r["field"] for r in reqs
            if r["status"] == "CONFIRMED" and r["value"] is not None and student.get(r["field"]) is None]


# ── 노드 ──
def recheck(state: State):
    student, notice, reqs = load(state["student_id"], state["notice_id"])
    verdict, details = judge(reqs, student)
    return {"verdict": verdict, "details": details, "missing": missing_fields(student, reqs)}


def ask(state: State):
    field = state["missing"][0]
    # 여기서 멈춘다. 학생의 답은 API 가 students 테이블에 저장한다 → 돌아가서 recheck 가 DB 를 다시 읽는다
    interrupt({"kind": "QUESTION", "field": field, "question": ai_tasks.make_question(field)})
    return {}


def draft(state: State):
    student, notice, reqs = load(state["student_id"], state["notice_id"])
    return {"draft": ai_tasks.make_draft(student, notice, state["details"])}


def check_draft(state: State):
    return {"needs_review": "[확인 필요" in state["draft"]}


def wait_admin(state: State):
    # 여기서 멈춘다. resume 하면 이 노드가 처음부터 다시 실행되므로, 검토 요청 생성 같은 '쓰기'는 여기 두지 않는다
    # (검토 요청은 Worker 가 만든다). 관리자 결정은 API 가 reviews 테이블에 저장한다 → 다시 읽는다
    interrupt({"kind": "DRAFT", "question": "관리자가 초안을 검토하고 있어요"})
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM reviews WHERE kind = 'DRAFT' AND target_id = %s ORDER BY id DESC LIMIT 1",
            (state["application_id"],)).fetchone()
    return {"admin_decision": row["status"] if row else "REJECTED"}


def route_after_recheck(state: State):
    if state["verdict"] == "불가":
        return "end"
    if state["missing"]:
        return "ask"
    return "draft"


def route_after_check(state: State):
    return "wait_admin" if state["needs_review"] else "end"


def route_after_admin(state: State):
    return "end" if state["admin_decision"] == "APPROVED" else "draft"


builder = StateGraph(State)
builder.add_node("recheck", recheck)
builder.add_node("ask", ask)
builder.add_node("draft", draft)
builder.add_node("check_draft", check_draft)
builder.add_node("wait_admin", wait_admin)
builder.add_edge(START, "recheck")
builder.add_conditional_edges("recheck", route_after_recheck, {"end": END, "ask": "ask", "draft": "draft"})
builder.add_edge("ask", "recheck")
builder.add_edge("draft", "check_draft")
builder.add_conditional_edges("check_draft", route_after_check, {"wait_admin": "wait_admin", "end": END})
builder.add_conditional_edges("wait_admin", route_after_admin, {"end": END, "draft": "draft"})

# 체크포인트 저장소 = PostgreSQL. 테이블(checkpoints 등)은 setup() 이 처음 한 번 만든다.
_conn = psycopg.connect(db.DATABASE_URL, autocommit=True, prepare_threshold=0, row_factory=dict_row)
_saver = PostgresSaver(_conn)
_saver.setup()
graph = builder.compile(checkpointer=_saver)


def _pending_interrupt(snapshot):
    for task in snapshot.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def run(application):
    """신청 1건을 실행한다. 처음이면 처음부터, 멈춰 있었으면 멈춘 곳부터 이어서."""
    app_id = application["id"]
    config = {"configurable": {"thread_id": f"application:{app_id}"}}

    snapshot = graph.get_state(config)
    if _pending_interrupt(snapshot) is not None:
        graph_input = Command(resume="continue")        # 학생 답변 / 관리자 결정을 받았다 → 이어서
    elif snapshot.next:
        graph_input = None                              # 중간에 죽었다 → 마지막 체크포인트부터
    else:
        graph_input = {"application_id": app_id,
                       "student_id": application["student_id"],
                       "notice_id": application["notice_id"]}   # 처음 실행

    graph.invoke(graph_input, config)

    snapshot = graph.get_state(config)
    values = snapshot.values
    waiting = _pending_interrupt(snapshot)
    if waiting is not None:
        if waiting.get("kind") == "DRAFT":
            return {"status": "WAITING_ADMIN", "question": waiting["question"], "result": values.get("draft")}
        return {"status": "WAITING_USER", "question": waiting["question"], "pending_field": waiting["field"]}
    if values.get("draft"):
        return {"status": "DONE", "result": values["draft"]}
    return {"status": "DONE", "result": f"신청 대상이 아니에요 (판정: {values.get('verdict')})"}
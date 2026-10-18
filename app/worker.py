"""비동기 Worker: Redis 에서 일을 꺼내(⑤) 실행하고, 결과를 DB 에 기록한다(⑥).
  ai_jobs     → 신청 지원 그래프 실행 → 끝나거나 멈추면 notify_jobs 에 알림 작업 등록(⑫)
  notify_jobs → 알림 메일 발송(⑧) + notifications 기록"""
import time
import traceback

import ai_tasks
import db
import graph
import job_queue


def claim(app_id):
    # 상태 선점: QUEUED 인 경우에만 RUNNING 으로 바꾼다. 같은 일이 두 번 들어와도 한 번만 실행된다.
    with db.get_conn() as conn:
        return conn.execute(
            "UPDATE applications SET status = 'RUNNING', updated_at = now() "
            "WHERE id = %s AND status = 'QUEUED' RETURNING *", (app_id,)).fetchone()


def save_result(app_id, r):
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE applications SET status = %s, question = %s, pending_field = %s, result = %s, "
            "updated_at = now() WHERE id = %s",
            (r["status"], r.get("question"), r.get("pending_field"), r.get("result"), app_id))


def sweep():
    # 복구: 큐에 있어야 할 일이 DB 에서 오래 멈춰 있으면 다시 큐에 넣는다. (진짜 상태 = DB)
    with db.get_conn() as conn:
        rows = conn.execute(
            "UPDATE applications SET status = 'QUEUED', updated_at = now() "
            "WHERE (status = 'QUEUED'  AND updated_at < now() - interval '30 seconds') "
            "   OR (status = 'RUNNING' AND updated_at < now() - interval '120 seconds') "
            "RETURNING id").fetchall()
    for row in rows:
        job_queue.enqueue("ai_jobs", {"kind": "apply_run", "application_id": row["id"]})
        print(f"[sweeper] 멈춘 신청 {row['id']} 다시 큐에 넣음", flush=True)


def handle_apply(job):
    app_id = job["application_id"]
    application = claim(app_id)
    if application is None:
        print(f"  신청 {app_id}: 이미 처리 중이거나 끝남 → 건너뜀", flush=True)
        return
    print(f"  신청 {app_id}: RUNNING", flush=True)
    result = graph.run(application)
    save_result(app_id, result)
    print(f"  신청 {app_id}: {result['status']}", flush=True)
    if result["status"] == "WAITING_ADMIN":
        # 관리자 검토 요청(DRAFT) 생성 → 관리자 화면에 나타난다
        with db.get_conn() as conn:
            conn.execute("INSERT INTO reviews (kind, target_id) VALUES ('DRAFT', %s)", (app_id,))
        print(f"  신청 {app_id}: DRAFT 검토 요청 생성", flush=True)
    if result["status"] in ("WAITING_USER", "DONE"):
        # ⑫ 알림은 따로 큐에 넣는다 → 메일 서버가 느리거나 실패해도 신청 처리에는 영향 없음
        job_queue.enqueue("notify_jobs", {"kind": "notify", "application_id": app_id, "status": result["status"]})


def handle_notify(job):
    app_id = job["application_id"]
    with db.get_conn() as conn:
        # JOIN: 신청 + 학생(이메일) + 공고(제목)를 한 번에 읽는다
        row = conn.execute(
            "SELECT a.question, a.result, s.name, s.email, n.title "
            "FROM applications a "
            "JOIN students s ON s.id = a.student_id "
            "JOIN notices  n ON n.id = a.notice_id "
            "WHERE a.id = %s", (app_id,)).fetchone()
    if row is None or not row["email"]:
        print(f"  알림 {app_id}: 이메일이 없어 건너뜀", flush=True)
        return
    if job["status"] == "WAITING_USER":
        subject = f"[장학금] {row['title']} — 확인할 질문이 있어요"
        body = f"{row['name']}님, 신청 지원 중에 질문이 생겼어요.\n\n질문: {row['question']}\n\n화면에서 답변해 주세요."
    else:
        subject = f"[장학금] {row['title']} — 신청서 초안이 준비됐어요"
        body = f"{row['name']}님, 신청서 초안이 완성됐어요.\n\n{row['result']}"
    ai_tasks.send_mail(row["email"], subject, body)                                  # ⑧ SMTP
    with db.get_conn() as conn:
        conn.execute("INSERT INTO notifications (application_id, to_email, subject) VALUES (%s, %s, %s)",
                     (app_id, row["email"], subject))
    print(f"  알림 {app_id}: 메일 발송 → {row['email']}", flush=True)


HANDLERS = {"apply_run": handle_apply, "notify": handle_notify}


def main():
    print("Worker 시작 — ai_jobs, notify_jobs 를 기다립니다", flush=True)
    last_sweep = 0.0
    while True:
        if time.time() - last_sweep > 30:
            sweep()
            last_sweep = time.time()
        item = job_queue.dequeue(["ai_jobs", "notify_jobs"], timeout=5)   # 앞에 적은 ai_jobs 를 먼저 꺼낸다
        if item is None:
            continue
        queue, job = item
        print(f"[{queue}] 꺼냄: {job}", flush=True)
        try:
            HANDLERS[job["kind"]](job)
        except Exception as e:
            traceback.print_exc()
            if job["kind"] == "apply_run":
                save_result(job["application_id"], {"status": "FAILED", "result": str(e)})

if __name__ == "__main__":
    main()
"""그래프 밖 AI 작업 (함수). 그래프 노드와 Worker 는 이 함수를 부르기만 한다. (피드백 5번)
⑦ Worker → LLM API (HTTPS · REST)   ⑧ Worker → 메일 서버 (SMTP)"""
import os
import smtplib
import time
from email.message import EmailMessage

import httpx

LLM_MODE = os.environ.get("LLM_MODE", "fake")            # fake = 가짜 응답, real = 진짜 Claude 호출
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "mailpit")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "1025"))

QUESTIONS = {
    "income": "소득분위를 숫자로 입력해 주세요 (예: 3)",
    "grade": "학년을 숫자로 입력해 주세요 (예: 2)",
    "region": "거주지(시·도)를 입력해 주세요 (예: 서울)",
}


def make_question(field):
    return QUESTIONS.get(field, f"{field} 값을 입력해 주세요")


def call_llm(prompt):
    """⑦ HTTPS 로 JSON 을 보내고 JSON 을 받는다. 키가 없거나 fake 모드면 None."""
    if LLM_MODE != "real" or not API_KEY:
        time.sleep(3)  # LLM 이 생각하는 시간 흉내
        print("  [LLM] fake 응답 사용 (⑦ 미검증)", flush=True)
        return None
    res = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": LLM_MODEL, "max_tokens": 400, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    res.raise_for_status()                                # 401(키 오류), 404(모델 이름 오류) 등이면 여기서 에러
    text = res.json()["content"][0]["text"]
    print(f"  [LLM] {LLM_MODEL} 응답 {len(text)}자 (⑦ HTTPS {res.status_code})", flush=True)
    return text


def make_draft(student, notice, details):
    unknown = [f for f, v in details.items() if v == "UNKNOWN"]
    # 이름·이메일 같은 개인정보는 LLM 에 보내지 않는다
    prompt = (
        "다음 장학금에 지원하는 대학생의 신청서 '지원 동기' 초안을 한국어로 5문장 이내로 써 줘.\n"
        f"장학금: {notice['title']}\n"
        f"학생: {student['grade']}학년, {student['region']} 거주, 소득분위 {student['income']}\n"
        "모르는 정보는 지어내지 말고 [확인 필요]라고 적어."
    )
    text = call_llm(prompt)
    if text is None:
        text = (f"[{notice['title']}] 신청서 초안 — {student['grade']}학년, {student['region']} 거주 학생으로서 "
                "지원 자격을 갖추었습니다. (fake)")
    if unknown:
        text += f"\n[확인 필요: {', '.join(unknown)}]"
    return text


def send_mail(to, subject, body):
    """⑧ SMTP 로 메일을 보낸다. 로컬은 Mailpit(:1025), 운영은 Amazon SES."""
    msg = EmailMessage()
    msg["From"] = "noreply@scholarship.test"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
        s.send_message(msg)
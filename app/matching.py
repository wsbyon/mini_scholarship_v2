"""Rule Engine: 조건 하나씩 PASS / FAIL / UNKNOWN, 모르면 빼지 않는다."""


def check(req, student):
    field, value = req["field"], req["value"]
    if req["status"] != "CONFIRMED" or value is None:
        return "UNKNOWN"                      # 공고 조건이 불확실 → 관리자 검토 대상
    mine = student.get(field)
    if mine is None:
        return "UNKNOWN"                      # 학생 정보가 비어 있음 → 나중에 질문
    if field == "income":
        return "PASS" if mine <= int(value) else "FAIL"
    if field == "grade":
        return "PASS" if str(mine) in value.split(",") else "FAIL"
    if field == "region":
        return "PASS" if value in ("전국", mine) else "FAIL"
    return "UNKNOWN"


def judge(reqs, student):
    reqs = [r for r in reqs if r["status"] != "REJECTED"]   # ← 추가: 관리자가 "조건 아님"으로 반려한 것은 무시
    details = {r["field"]: check(r, student) for r in reqs}
    if "FAIL" in details.values():
        verdict = "불가"
    elif "UNKNOWN" in details.values():
        verdict = "확인 필요"
    else:
        verdict = "가능"
    return verdict, details
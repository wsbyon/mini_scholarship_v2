# 연결 확인 기록

| # | 구간 | 프로토콜 / 포트 | 확인 방법 | 결과 |
|---|---|---|---|---|
| 0-a | 내 PC → PostgreSQL | PostgreSQL :5432 | psql로 INSERT/SELECT | ✅ 10/16 |
| 0-b | 내 PC → Redis | RESP :6379 | redis-cli LPUSH/RPOP | ✅ 10/16 |
| 0-c | 내 PC → Mailpit | SMTP :1025 | Send-MailMessage → 8025 화면 | ✅ 10/16 |
| ① | 브라우저 → Nginx | HTTP :80 | | |✅
| ② | Nginx → API | HTTP :8000 | | |✅
| ③ | API → PostgreSQL | :5432 | | |✅
| ④ | API → Redis | RESP :6379 | | |✅
| ⑤ | Redis → Worker | RESP :6379 | | |✅
| ⑥ | Worker → PostgreSQL | :5432 | | |✅
| ⑦ | Worker → LLM | HTTPS :443 | | |fake로 대체
| ⑧ | Worker → 메일 | SMTP :1025 | | |✅
| ⑨ | 수집 → 공공데이터 | HTTPS :443 | | |샘플로 대체
| ⑩ | 수집 → PostgreSQL | :5432 | | |✅
| ⑪ | 브라우저 polling | HTTP GET | | |✅
| ⑫ | Worker → Redis | RESP :6379 · LPUSH notify_jobs | | |✅
| ⑬ | 메일 → 학생 | 이메일 | | |✅
| ⑭ | Worker → S3 (선택) | HTTPS · S3 API (MinIO :9000) | | |제외
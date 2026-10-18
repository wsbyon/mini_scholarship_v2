CREATE EXTENSION IF NOT EXISTS vector;

-- 공고 (수집 서버가 채움)
CREATE TABLE notices (
    id           SERIAL PRIMARY KEY,
    source_id    TEXT UNIQUE,                -- 원본 고유번호 → UPSERT 기준
    title        TEXT NOT NULL,
    body         TEXT,
    deadline     DATE,
    embedding    vector(8),                  -- 확장용
    collected_at TIMESTAMPTZ DEFAULT now()
);

-- 공고 조건 (Rule Engine이 읽음)
CREATE TABLE notice_requirements (
    id         SERIAL PRIMARY KEY,
    notice_id  INT REFERENCES notices(id),
    field      TEXT NOT NULL,                -- income / grade / region
    value      TEXT,                         -- 기준값 (모르면 NULL)
    status     TEXT NOT NULL DEFAULT 'CONFIRMED'   -- CONFIRMED / NEEDS_REVIEW
);

-- 학생
CREATE TABLE students (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    email   TEXT,
    grade   INT,
    region  TEXT,
    income  INT
);

-- 신청
CREATE TABLE applications (
    id          SERIAL PRIMARY KEY,
    student_id  INT REFERENCES students(id),
    notice_id   INT REFERENCES notices(id),
    status      TEXT NOT NULL DEFAULT 'QUEUED',
    -- QUEUED / RUNNING / WAITING_USER / WAITING_ADMIN / DONE / FAILED
    question    TEXT,
    pending_field TEXT,          -- ← 추가: 지금 물어본 항목 (income 등)
    result      TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- 관리자 검토 요청 (REQUIREMENT / DRAFT)
CREATE TABLE reviews (
    id         SERIAL PRIMARY KEY,
    kind       TEXT NOT NULL,                -- REQUIREMENT / DRAFT
    target_id  INT NOT NULL,                 -- 조건 id 또는 신청 id
    status     TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING / APPROVED / REJECTED
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 알림 발송 기록
CREATE TABLE notifications (
    id              SERIAL PRIMARY KEY,
    application_id  INT REFERENCES applications(id),
    to_email        TEXT,
    subject         TEXT,
    sent_at         TIMESTAMPTZ DEFAULT now()
);
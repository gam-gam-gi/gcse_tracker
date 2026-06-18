-- ─────────────────────────────────────────────────────────────────────────────
-- GCSE Tracker · Supabase Schema
-- Paste this entire file into your Supabase project → SQL Editor → Run
-- ─────────────────────────────────────────────────────────────────────────────

-- Subjects (Maths, Chemistry, Physics, Biology)
create table if not exists subjects (
    id   serial primary key,
    name varchar(50) unique not null
);

-- Topic areas (e.g. "Algebra", "Number")
create table if not exists topic_areas (
    id         serial primary key,
    subject_id integer references subjects(id) on delete cascade,
    name       varchar(100) not null
);

-- Topics (e.g. "Completing the square")
create table if not exists topics (
    id         serial primary key,
    subject_id integer references subjects(id) on delete cascade,
    area_id    integer references topic_areas(id) on delete cascade,
    name       varchar(150) not null
);

-- Papers (one row per PDF file)
create table if not exists papers (
    id               serial primary key,
    subject_id       integer references subjects(id),
    paper_type       varchar(100),           -- "Paper 1", "Paper 2", etc.
    filename         varchar(255) not null,
    file_path        text         not null,
    total_questions  integer      default 0,
    processed        boolean      default false,
    processed_at     timestamp,
    created_at       timestamp    default now()
);

-- Questions extracted from papers
create table if not exists questions (
    id                  serial primary key,
    paper_id            integer references papers(id) on delete cascade,
    topic_id            integer references topics(id),
    question_number     varchar(10),          -- "1", "2", "3" etc
    brief_description   text,                 -- one-line summary
    image_url           text,                 -- Supabase Storage public URL
    page_number         integer,
    difficulty          varchar(10),          -- Bronze / Silver / Gold
    marks               integer default 1,
    answer_guide        text,                 -- brief answer for marking
    created_at          timestamp default now()
);

-- Assignments (which questions are given to which student)
create table if not exists assignments (
    id            serial primary key,
    question_id   integer references questions(id) on delete cascade,
    student_name  varchar(5) not null,        -- M1 or M2
    assigned_date date       default current_date,
    due_date      date,
    created_at    timestamp  default now(),
    unique (question_id, student_name)        -- no duplicate assignments
);

-- Attempts (student answers and scores)
create table if not exists attempts (
    id               serial primary key,
    question_id      integer references questions(id) on delete cascade,
    student_name     varchar(5) not null,
    attempt_date     timestamp  default now(),
    student_answer   text,
    score            integer    default 0,
    max_score        integer,
    is_correct       boolean,
    claude_feedback  text,
    week_number      integer    generated always as (
                         extract(week from attempt_date)::integer
                     ) stored
);

-- ── Indexes for common queries ─────────────────────────────────────────────
create index if not exists idx_questions_paper    on questions(paper_id);
create index if not exists idx_questions_topic    on questions(topic_id);
create index if not exists idx_assignments_student on assignments(student_name);
create index if not exists idx_attempts_student   on attempts(student_name);
create index if not exists idx_attempts_date      on attempts(attempt_date);

-- ── Row Level Security (leave open for local use) ─────────────────────────
alter table subjects     enable row level security;
alter table topic_areas  enable row level security;
alter table topics       enable row level security;
alter table papers       enable row level security;
alter table questions    enable row level security;
alter table assignments  enable row level security;
alter table attempts     enable row level security;

create policy "allow all" on subjects     for all using (true) with check (true);
create policy "allow all" on topic_areas  for all using (true) with check (true);
create policy "allow all" on topics       for all using (true) with check (true);
create policy "allow all" on papers       for all using (true) with check (true);
create policy "allow all" on questions    for all using (true) with check (true);
create policy "allow all" on assignments  for all using (true) with check (true);
create policy "allow all" on attempts     for all using (true) with check (true);

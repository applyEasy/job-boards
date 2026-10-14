-- Boards bank: every posting on every employer job board the job-boards
-- crawler (github.com/applyEasy/job-boards) has found. The crawler writes it;
-- Backend's nightly ingest reads what changed, keeps the roles it wants and
-- writes those into job_bank. Nothing here is filtered.
--
-- Apply once in the Supabase SQL editor (or psql), then give the crawler's
-- role a password (see the end of this file). Re-running is a no-op.
-- Rollback: 20261014_boards_bank_ROLLBACK.sql (drops the tables and the role).

begin;

-- One employer board on one job platform, and when to read it next.
create table if not exists public.bank_boards (
  id              text primary key,             -- '<provider>:<key>' lowercased, e.g. 'greenhouse:stripe'
  provider        text not null,                -- greenhouse lever ashby smartrecruiters workday oracle icims
  key             text not null,                -- slug, host, or host/site as first found
  company_name    text not null default '',
  evidence_url    text not null,                -- the public link that proved the board exists
  evidence_source text not null,                -- directory, feed:<name>, commoncrawl:<crawl>
  status          text not null default 'new' check (status in ('new', 'active', 'empty', 'dead')),
  next_check_at   timestamptz not null default now(),
  interval_hours  real not null default 6,
  fail_count      integer not null default 0,
  failing_since   timestamptz,
  last_error      text,
  last_ok_at      timestamptz,
  list_hash       text,                         -- hash of the last complete listing; same hash = nothing to write
  open_postings   integer not null default 0,
  created_at      timestamptz not null default now()
);
create index if not exists bank_boards_due on public.bank_boards (next_check_at);

-- One posting. id and source use job_bank's own (source, source_id), so a
-- bank posting and the job_bank row made from it are the same identity.
create table if not exists public.bank_postings (
  id                text primary key,           -- job_bank.source_id, e.g. 'gh-stripe-123'
  source            text not null,              -- job_bank.source, e.g. 'ats_greenhouse'
  board_id          text not null references public.bank_boards (id) on delete cascade,
  company_name      text not null,
  title             text not null,
  locations         text[] not null default '{}',
  workplace         text,
  employment_type   text,
  department        text,
  url               text not null,
  apply_url         text,
  posted_at         timestamptz,
  source_updated_at timestamptz,                -- the platform's own last-modified, when it gives one
  content_hash      text not null,
  first_seen_at     timestamptz not null default now(),
  changed_at        timestamptz not null default now(),  -- any insert, edit, close or reopen
  closed_at         timestamptz
);
create index if not exists bank_postings_open_by_board on public.bank_postings (board_id) where closed_at is null;
create index if not exists bank_postings_changes on public.bank_postings (changed_at, id);

-- How far a reader (Backend's ingest) has read bank_postings.changed_at.
create table if not exists public.bank_cursors (
  consumer   text primary key,
  changed_at timestamptz not null
);

-- Discovery work already done (a Common Crawl index page, a feed), so a day
-- that fails part way picks up where it stopped.
create table if not exists public.bank_discovery (
  source  text not null,
  item    text not null,
  done_at timestamptz not null default now(),
  primary key (source, item)
);

-- One row per crawl/discover run: what it did, for the run log.
create table if not exists public.bank_runs (
  id          bigserial primary key,
  kind        text not null,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  stats       jsonb not null default '{}'
);

-- Nobody but the crawler and the service role (Backend) reads or writes these.
alter table public.bank_boards    enable row level security;
alter table public.bank_postings  enable row level security;
alter table public.bank_cursors   enable row level security;
alter table public.bank_discovery enable row level security;
alter table public.bank_runs      enable row level security;
revoke all on public.bank_boards, public.bank_postings, public.bank_cursors,
              public.bank_discovery, public.bank_runs from anon, authenticated;
revoke all on sequence public.bank_runs_id_seq from anon, authenticated;

-- The crawler's own login: these tables only, no other data in the project.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'job_boards_crawler') then
    create role job_boards_crawler nologin;
  end if;
end $$;
alter role job_boards_crawler set statement_timeout = '120s';
grant usage on schema public to job_boards_crawler;
grant select, insert, update, delete on public.bank_boards, public.bank_postings,
      public.bank_discovery, public.bank_runs to job_boards_crawler;
grant usage, select on sequence public.bank_runs_id_seq to job_boards_crawler;

drop policy if exists crawler_all on public.bank_boards;
drop policy if exists crawler_all on public.bank_postings;
drop policy if exists crawler_all on public.bank_discovery;
drop policy if exists crawler_all on public.bank_runs;
create policy crawler_all on public.bank_boards    for all to job_boards_crawler using (true) with check (true);
create policy crawler_all on public.bank_postings  for all to job_boards_crawler using (true) with check (true);
create policy crawler_all on public.bank_discovery for all to job_boards_crawler using (true) with check (true);
create policy crawler_all on public.bank_runs      for all to job_boards_crawler using (true) with check (true);

commit;

-- Then, once, with a password you generate (e.g. `openssl rand -hex 24`):
--   alter role job_boards_crawler login password '<password>';
-- The crawler connects through the Supabase pooler as user
-- 'job_boards_crawler.<project-ref>' (Dashboard > Connect > Session pooler shows the host).

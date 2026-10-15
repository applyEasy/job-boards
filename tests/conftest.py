"""The throwaway Postgres the bank tests share (BANK_TEST_DATABASE_URL; skipped without it)."""

import os
from pathlib import Path

import pytest

DSN = os.getenv("BANK_TEST_DATABASE_URL")
SQL = Path(__file__).parent.parent / "sql"
# The columns of Backend's tables the step reads (job_bank, job_bank_read_v1).
BACKEND_TABLES = """
create table job_bank (
  id uuid default gen_random_uuid() primary key, source text not null, source_id text not null,
  company_name text not null, title text not null, locations text[] default '{}', url text not null,
  apply_url text, description text, category text, workplace text, date_posted timestamptz,
  date_updated timestamptz, first_seen_at timestamptz default now(), last_seen_at timestamptz default now(),
  is_active boolean default true, is_ghost boolean default false, ghost_reason text,
  consecutive_gone_checks integer default 0, ats_platform text, seniority text, company_website text,
  unique (source, source_id));
create table job_bank_read_v1 (
  job_id uuid primary key, link_kind text, link_url text, visible boolean not null, hidden_reason text)"""


@pytest.fixture
def bank():
    if not DSN:
        pytest.skip("BANK_TEST_DATABASE_URL not set")
    import psycopg

    from jobboards.bank import Bank

    with psycopg.connect(DSN, autocommit=True) as conn:
        for role in ("anon", "authenticated", "service_role"):
            conn.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname = '{role}') then "
                         f"create role {role} nologin; end if; end $$")
        conn.execute("drop table if exists job_bank_jobright_links, bank_postings, bank_boards, bank_cursors, "
                     "bank_discovery, bank_runs, job_bank_read_v1, job_bank")
        conn.execute(BACKEND_TABLES)
        for name in ("20261014_boards_bank.sql", "20261014_bank_publish.sql", "20261015_jobright_links.sql",
                     "20261016_agent_finder.sql"):
            conn.execute((SQL / name).read_text())
    b = Bank(DSN)
    yield b
    b.close()

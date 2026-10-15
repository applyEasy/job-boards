-- The employer's own posting for a Jobright row that opens only the
-- employer's homepage (Backend's read model, job_bank_read_v1.link_kind =
-- 'company_site'). Jobright's lists don't carry the employer's link; the
-- crawler finds it among the boards it reads (jobboards/resolve.py) and
-- saves it here, and Backend's read model links the row to it.
--
-- Run after 20261014_bank_publish.sql. Re-running is a no-op.
-- Rollback: 20261015_jobright_links_ROLLBACK.sql.
--
--   job_bank_jobright_links            one row per Jobright row the crawler looked up
--   bank_jobright_unresolved(p_limit)  Jobright rows to look up: homepage-only (or hidden
--                                      for no link at all), newest first, not looked up
--                                      in the last 6 hours, none already linked

begin;

create table if not exists public.job_bank_jobright_links (
  job_id     uuid primary key,   -- job_bank.id of the Jobright row
  posting_id text,               -- bank_postings.id whose link it takes; null: looked, none found
  url        text,               -- that posting's page; null with posting_id
  score      real,
  tried_at   timestamptz not null default now()
);

alter table public.job_bank_jobright_links enable row level security;
revoke all on public.job_bank_jobright_links from anon, authenticated;
grant select, insert, update, delete on public.job_bank_jobright_links to job_boards_crawler;
drop policy if exists crawler_all on public.job_bank_jobright_links;
create policy crawler_all on public.job_bank_jobright_links for all to job_boards_crawler using (true) with check (true);

create or replace function public.bank_jobright_unresolved(p_limit integer)
returns table (job_id uuid, company_name text, title text, locations text[], company_website text)
language sql stable security definer set search_path = public as $$
  select j.id, j.company_name, j.title, j.locations, j.company_website
  from job_bank j
  join job_bank_read_v1 r on r.job_id = j.id
  where j.source = 'jobright' and j.is_active
    and (r.link_kind = 'company_site' or r.hidden_reason = 'no_employer_link')
    and not exists (select 1 from job_bank_jobright_links l
                    where l.job_id = j.id and (l.url is not null or l.tried_at > now() - interval '6 hours'))
  order by j.date_posted desc nulls last, j.id
  limit p_limit
$$;

revoke all on function public.bank_jobright_unresolved(integer) from public, anon, authenticated;
grant execute on function public.bank_jobright_unresolved(integer) to job_boards_crawler, service_role;

commit;

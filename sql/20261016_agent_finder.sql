-- What the AI posting finder (jobboards/agent.py) reads: Jobright rows still opening only
-- the employer's homepage, whenever it last looked, and the links it found itself, to check
-- them again. It writes job_bank_jobright_links (20261015_jobright_links.sql), with no
-- posting_id: the page it checked, not a bank posting.
--
-- Run after 20261015_jobright_links.sql. Re-running is a no-op.
-- Rollback: 20261016_agent_finder_ROLLBACK.sql.
--
--   bank_jobright_homepage_only(p_limit)  homepage-only (or no-link) Jobright rows with no link, newest first
--   bank_jobright_agent_links(p_limit)    the finder's links not checked for 3 days, oldest check first

begin;

create or replace function public.bank_jobright_homepage_only(p_limit integer)
returns table (job_id uuid, company_name text, title text, locations text[], company_website text)
language sql stable security definer set search_path = public as $$
  select j.id, j.company_name, j.title, j.locations, j.company_website
  from job_bank j
  join job_bank_read_v1 r on r.job_id = j.id
  where j.source = 'jobright' and j.is_active
    and (r.link_kind = 'company_site' or r.hidden_reason = 'no_employer_link')
    and not exists (select 1 from job_bank_jobright_links l where l.job_id = j.id and l.url is not null)
  order by j.date_posted desc nulls last, j.id
  limit p_limit
$$;

create or replace function public.bank_jobright_agent_links(p_limit integer)
returns table (job_id uuid, url text, company_name text, title text)
language sql stable security definer set search_path = public as $$
  select l.job_id, l.url, j.company_name, j.title
  from job_bank_jobright_links l
  join job_bank j on j.id = l.job_id
  where l.posting_id is null and l.url is not null and l.tried_at < now() - interval '3 days'
  order by l.tried_at
  limit p_limit
$$;

revoke all on function public.bank_jobright_homepage_only(integer), public.bank_jobright_agent_links(integer)
  from public, anon, authenticated;
grant execute on function public.bank_jobright_homepage_only(integer), public.bank_jobright_agent_links(integer)
  to job_boards_crawler, service_role;

commit;

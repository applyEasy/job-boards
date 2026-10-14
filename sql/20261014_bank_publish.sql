-- The crawler puts intern and new-grad postings on applyfinch.com as it reads
-- them: these functions write job_bank (the table the site reads) for the
-- job_boards_crawler role, which can't touch job_bank itself.
--
-- Run after 20261014_boards_bank.sql. Re-running is a no-op.
--
--   bank_publish(postings)  upsert job_bank rows the crawler built (jobboards/publish.py)
--   bank_unpublish(ids)     take down the job_bank rows of closed bank postings
--   bank_touch_published()  mark live rows the bank still holds open as seen, at most
--                           once a day each, so Backend's 7-day stale sweep leaves them up

begin;

create or replace function public.bank_publish(postings jsonb) returns integer
language sql security definer set search_path = public as $$
  with up as (
    insert into job_bank as j (source, source_id, company_name, title, locations, url, apply_url, category,
                               workplace, date_posted, date_updated, ats_platform, seniority, last_seen_at,
                               is_active, is_ghost, ghost_reason, consecutive_gone_checks)
    select r.source, r.source_id, r.company_name, r.title, coalesce(r.locations, '{}'), r.url,
           coalesce(r.apply_url, r.url), r.category, r.workplace, r.date_posted, r.date_posted, r.ats_platform,
           r.seniority, now(), true, false, null, 0
    from jsonb_to_recordset(postings) as r(source text, source_id text, company_name text, title text,
                                        locations text[], url text, apply_url text, category text, workplace text,
                                        date_posted timestamptz, ats_platform text, seniority text)
    where r.source like 'ats\_%' and r.source_id <> '' and r.company_name <> '' and r.title <> '' and r.url <> ''
    -- Only the columns a crawl knows: descriptions, pay and enrichment stay as Backend wrote them.
    on conflict (source, source_id) do update set
      company_name = excluded.company_name, title = excluded.title, locations = excluded.locations,
      url = excluded.url, apply_url = excluded.apply_url, category = excluded.category,
      workplace = coalesce(excluded.workplace, j.workplace),
      date_posted = coalesce(excluded.date_posted, j.date_posted),
      date_updated = coalesce(excluded.date_updated, j.date_updated),
      ats_platform = excluded.ats_platform, seniority = excluded.seniority, last_seen_at = excluded.last_seen_at,
      is_active = true, is_ghost = false, ghost_reason = null, consecutive_gone_checks = 0
    returning 1)
  select count(*)::integer from up
$$;

create or replace function public.bank_unpublish(ids text[]) returns integer
language sql security definer set search_path = public as $$
  with down as (
    update job_bank j set is_active = false
    from bank_postings b
    where b.id = any(ids) and j.source = b.source and j.source_id = b.id and j.is_active
    returning 1)
  select count(*)::integer from down
$$;

create or replace function public.bank_touch_published() returns integer
language sql security definer set search_path = public as $$
  with seen as (
    update job_bank j set last_seen_at = now()
    from bank_postings b
    where b.id = j.source_id and b.source = j.source and b.closed_at is null and j.is_active
      and j.last_seen_at < now() - interval '1 day'
    returning 1)
  select count(*)::integer from seen
$$;

revoke all on function public.bank_publish(jsonb), public.bank_unpublish(text[]), public.bank_touch_published()
  from public, anon, authenticated;
grant execute on function public.bank_publish(jsonb), public.bank_unpublish(text[]), public.bank_touch_published()
  to job_boards_crawler, service_role;

commit;

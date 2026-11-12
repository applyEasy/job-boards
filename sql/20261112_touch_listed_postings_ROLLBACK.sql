-- Undo 20261112_touch_listed_postings.sql: bank_touch_published as it was, marking
-- every live row whose bank posting is open. Rows the sweep took down meanwhile
-- stay down until their posting changes or Backend reads them again.
begin;

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

commit;

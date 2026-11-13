-- Final activation step, after:
--   1. deploying crawler code with two-answer posting confirmation;
--   2. applying 20261112_last_listed_at.sql and
--      20261113_posting_gone_confirmation.sql between crawl runs; and
--   3. collecting at least 7 days of listing stamps and reviewing the count below.
-- bank_touch_published, which marks live job_bank rows as
-- seen, stops vouching for postings nobody has listed:
--
--   a board whose last read listed everything (bank_boards.list_hash is set)
--     vouches for every posting it holds open, as before;
--   a board too big to list in full (list_hash is null) vouches only for the
--     postings a read listed in the last 7 days (bank_postings.last_listed_at).
--
-- The rest are no longer refreshed, so Backend's 7-day stale sweep takes them
-- down like any other row nobody has seen.
--
-- BEFORE APPLYING, count what stops being refreshed (read-only):
--
--   select j.source, count(*) as live_rows, count(*) filter (
--            where b.last_listed_at is null or b.last_listed_at <= now() - interval '7 days') as left_to_the_sweep
--   from job_bank j
--   join bank_postings b on b.id = j.source_id and b.source = j.source and b.closed_at is null
--   join bank_boards d on d.id = b.board_id and d.list_hash is null
--   where j.is_active group by 1 order by 2 desc;
--
-- left_to_the_sweep holds dead postings (the point) and live ones a capped read
-- can never reach. Do not apply this function until the per-posting checks have
-- completed enough coverage to keep this number within the reviewed rollout
-- bound; a 200-posting check budget can take many crawls to cover a large board.
--
-- Only bank_touch_published changes (the crawler's, 20261014_bank_publish.sql).
-- bank_touch_job_bank (20261014_boards_bank.sql), the one Backend's ingest calls
-- when JOB_BANK_BOARDS_BANK is on, has the same gap and is not in the live
-- database today (checked 2026-11-12): give it this rule before that switch
-- goes on, or the switch undoes this.
--
-- create or replace keeps the grants. Re-running is a no-op.
-- Rollback: 20261112_touch_listed_postings_ROLLBACK.sql.

begin;

create or replace function public.bank_touch_published() returns integer
language sql security definer set search_path = public as $$
  with seen as (
    update job_bank j set last_seen_at = now()
    from bank_postings b join bank_boards d on d.id = b.board_id
    where b.id = j.source_id and b.source = j.source and b.closed_at is null and j.is_active
      and j.last_seen_at < now() - interval '1 day'
      and (d.list_hash is not null or b.last_listed_at > now() - interval '7 days')
    returning 1)
  select count(*)::integer from seen
$$;

commit;

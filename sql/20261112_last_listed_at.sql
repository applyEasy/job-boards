-- bank_postings.last_listed_at: when a read that could not list its whole board
-- (Workday past 2,000 results, SmartRecruiters or Oracle past 5,000, any listing
-- that came back short) last listed this posting. Such a read closes nothing, so
-- this is the only sign that one of its postings is still on the employer's site.
-- Null: never listed by such a read (every posting, on the day this is applied).
--
-- Step 1 of 2. On its own this changes nothing anybody sees: the crawler starts
-- stamping on its next run once the column is there (jobboards/bank.py asks the
-- catalog, so the same code runs with or without it), at most once a day per
-- posting. Step 2 is 20261112_touch_listed_postings.sql; read its header first.
--
-- A nullable column with no default: no table rewrite, a catalog change only.
-- lock_timeout: give up rather than queue the crawl's writes behind this.
-- Re-running is a no-op. Rollback: 20261112_last_listed_at_ROLLBACK.sql.

begin;
set local lock_timeout = '5s';
alter table public.bank_postings add column if not exists last_listed_at timestamptz;
commit;

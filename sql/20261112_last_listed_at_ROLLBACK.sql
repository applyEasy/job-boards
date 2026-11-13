-- Undo 20261112_last_listed_at.sql. Run 20261112_touch_listed_postings_ROLLBACK.sql
-- first if step 2 was applied: its function reads this column. Run it between crawl
-- runs: a run that started with the column checks for it once, so dropping it
-- mid-run fails that run's writes on capped boards (nothing closes; the next run
-- stops stamping by itself).
begin;
set local lock_timeout = '5s';
alter table public.bank_postings drop column if exists last_listed_at;
commit;

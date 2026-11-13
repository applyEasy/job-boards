-- Persist the first definitive per-posting ABSENT answer from a capped board.
-- The crawler closes only after a second ABSENT at least one hour later. A
-- PRESENT answer clears the timestamp. Deploy the matching crawler code first:
-- it ignores ABSENT answers until this column exists. The older c514197 code
-- closes after one answer and is not made safe by adding this column alone.
--
-- Apply between crawl runs after 20261112_last_listed_at.sql. This is a nullable
-- catalog-only addition with no table rewrite. Rollback only after deploying
-- code that no longer reads it.

begin;
set local lock_timeout = '5s';
alter table public.bank_postings add column if not exists gone_first_seen_at timestamptz;
commit;

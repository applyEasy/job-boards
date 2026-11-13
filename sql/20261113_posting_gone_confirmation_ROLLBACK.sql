-- Deploy code that no longer reads gone_first_seen_at first, then run this
-- between crawl runs. Pending first ABSENT observations are discarded.
begin;
set local lock_timeout = '5s';
alter table public.bank_postings drop column if exists gone_first_seen_at;
commit;

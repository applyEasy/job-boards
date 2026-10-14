-- Removes the boards bank and the crawler's role. Destructive: every board and
-- posting the crawler found is gone. job_bank rows made from it stay (their
-- stale sweep takes them down once Backend stops reading the bank).
begin;
drop function if exists public.bank_touch_job_bank();
drop table if exists public.bank_postings, public.bank_boards, public.bank_cursors,
                     public.bank_discovery, public.bank_runs;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'job_boards_crawler') then
    revoke all on schema public from job_boards_crawler;
    drop role job_boards_crawler;
  end if;
end $$;
commit;

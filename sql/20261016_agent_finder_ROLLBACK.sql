-- Undo 20261016_agent_finder.sql. The finder's links stay in job_bank_jobright_links.
begin;
drop function if exists public.bank_jobright_homepage_only(integer);
drop function if exists public.bank_jobright_agent_links(integer);
commit;

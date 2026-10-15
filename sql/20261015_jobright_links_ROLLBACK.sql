-- Undo 20261015_jobright_links.sql. Backend's read model then falls back to
-- the employer's homepage for every Jobright row it had linked.
begin;
drop function if exists public.bank_jobright_unresolved(integer);
drop table if exists public.job_bank_jobright_links;
commit;

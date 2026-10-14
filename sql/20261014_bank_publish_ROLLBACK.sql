-- Stops the crawler writing job_bank. Rows it already published stay; Backend's
-- nightly sweep ages them out like any other row it no longer sees.
begin;
drop function if exists public.bank_publish(jsonb);
drop function if exists public.bank_unpublish(text[]);
drop function if exists public.bank_touch_published();
commit;

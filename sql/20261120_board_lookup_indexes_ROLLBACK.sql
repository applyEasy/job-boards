-- Drops the two board-lookup indexes. Safe with any crawler version: without
-- bank_postings_gone_by_board the crawler clears absence marks as it did
-- before. Run with psql (DROP INDEX CONCURRENTLY cannot run in a transaction).

drop index concurrently if exists public.bank_postings_gone_by_board;
drop index concurrently if exists public.bank_postings_by_board;

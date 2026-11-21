-- Two indexes for the crawler's per-board reads, which were the biggest share of
-- Supabase disk IO from 11/9 to 11/19.
--
-- bank_postings_by_board: Bank.stored() reads every posting of a board, open
-- or closed. The only board_id index (bank_postings_open_by_board) holds open
-- postings only, so that read scanned the whole table on every changed board
-- (about 17% of disk IO). This one holds every posting with the three
-- columns that read needs, so the read touches only that board's entries.
--
-- bank_postings_gone_by_board: a posting one capped read found gone
-- (20261113_posting_gone_confirmation.sql). Almost none are, so this index is
-- tiny. Once it is valid the crawler asks it which of a board's postings
-- carry the mark and clears only those, instead of reading every listed
-- posting on every read (367k updates changed 142 rows). Before it exists the
-- crawler clears as it did.
--
-- Apply after 20261113_posting_gone_confirmation.sql, with psql (each
-- statement runs on its own): CREATE INDEX CONCURRENTLY cannot run inside a
-- transaction, so the Supabase SQL editor's one-transaction run refuses it.
-- Run one statement at a time there. Concurrent builds keep the crawl's writes
-- flowing. A failed build leaves an invalid index that a rerun skips (IF NOT
-- EXISTS). Check pg_index.indisvalid, and if it is false, drop that index with
-- the rollback file and run again.
-- Rollback: 20261120_board_lookup_indexes_ROLLBACK.sql.

set statement_timeout = 0;
create index concurrently if not exists bank_postings_by_board
  on public.bank_postings (board_id) include (id, content_hash, closed_at);
create index concurrently if not exists bank_postings_gone_by_board
  on public.bank_postings (board_id) where gone_first_seen_at is not null;

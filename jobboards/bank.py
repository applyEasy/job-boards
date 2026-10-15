"""The only code that talks to Postgres (the bank_* tables)."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from jobboards.boards import Board
from jobboards.publish import job_bank_rows
from jobboards.readers import Posting

_UPSERT = """
insert into bank_postings (id, source, board_id, company_name, title, locations, workplace, employment_type,
                           department, url, apply_url, posted_at, source_updated_at, content_hash,
                           first_seen_at, changed_at, closed_at)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, null)
on conflict (id) do update set
  source = excluded.source, board_id = excluded.board_id, company_name = excluded.company_name,
  title = excluded.title, locations = excluded.locations, workplace = excluded.workplace,
  employment_type = excluded.employment_type, department = excluded.department, url = excluded.url,
  apply_url = excluded.apply_url, posted_at = coalesce(bank_postings.posted_at, excluded.posted_at),
  source_updated_at = excluded.source_updated_at, content_hash = excluded.content_hash,
  changed_at = excluded.changed_at, closed_at = null
-- One employer's posting on two of its Workday sites has one id: the board that
-- holds it keeps it unless it was closed.
where bank_postings.board_id = excluded.board_id or bank_postings.closed_at is not null
"""


class Bank:
    def __init__(self, dsn: str):
        # prepare_threshold=None: the Supabase pooler can't keep prepared statements.
        self.conn = psycopg.connect(dsn, autocommit=True, prepare_threshold=None, row_factory=dict_row)
        self.lock = threading.Lock()  # one connection, many crawl threads

    def close(self) -> None:
        self.conn.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self.lock:
            cur = self.conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def add_boards(self, items: Iterable[Tuple[Board, str, str]]) -> int:
        """(board, evidence_url, evidence_source) -> how many were new. The first evidence stays."""
        rows: Dict[str, tuple] = {}
        for board, url, source in items:
            rows.setdefault(board.id, (board.id, board.provider, board.key, board.company_name or "", url, source))
        if not rows:
            return 0
        columns = list(zip(*rows.values()))
        with self.lock:
            cur = self.conn.execute(
                "insert into bank_boards (id, provider, key, company_name, evidence_url, evidence_source) "
                "select * from unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[]) "
                "on conflict (id) do nothing", [list(c) for c in columns])
            return cur.rowcount

    def due_boards(self, limit: int, early_minutes: float = 0) -> List[Dict[str, Any]]:
        return self.query("select * from bank_boards where next_check_at <= now() + make_interval(mins => %s) "
                          "order by next_check_at limit %s", (early_minutes, limit))

    def stored(self, board_id: str) -> Dict[str, Tuple[str, bool]]:
        return {r["id"]: (r["content_hash"], r["open"]) for r in self.query(
            "select id, content_hash, closed_at is null as open from bank_postings where board_id = %s", (board_id,))}

    def known(self, board_id: str) -> Dict[str, Optional[str]]:
        return {r["id"]: r["source_updated_at"].isoformat() if r["source_updated_at"] else None for r in self.query(
            "select id, source_updated_at from bank_postings where board_id = %s and closed_at is null", (board_id,))}

    def _board_update(self, board_id: str, sched: Dict[str, Any], extra: str = "", params: Sequence[Any] = ()) -> None:
        self.conn.execute(
            "update bank_boards set status = %s, next_check_at = %s, interval_hours = %s, fail_count = %s, "
            f"failing_since = %s{extra} where id = %s",
            (sched["status"], sched["next_check_at"], sched["interval_hours"], sched["fail_count"],
             sched["failing_since"], *params, board_id))

    def apply(self, board_id: str, upserts: List[Posting], closed: List[str], sched: Dict[str, Any],
              list_hash: Optional[str], now: datetime) -> None:
        with self.lock, self.conn.transaction():
            if upserts:
                with self.conn.cursor() as cur:
                    cur.executemany(_UPSERT, [
                        (p.id, p.source, board_id, p.company_name, p.title, p.locations, p.workplace, p.employment_type,
                         p.department, p.url, p.apply_url, p.posted_at, p.source_updated_at, p.content_hash(), now, now)
                        for p in upserts])
            if closed:
                self.conn.execute("update bank_postings set closed_at = %s, changed_at = %s "
                                  "where board_id = %s and id = any(%s) and closed_at is null",
                                  (now, now, board_id, closed))
            if upserts:
                # Publish only writes this board owns; another site's duplicate is ignored above.
                owned = {r["id"] for r in self.conn.execute(
                    "select id from bank_postings where board_id = %s and id = any(%s)",
                    (board_id, [p.id for p in upserts]))}
                upserts = list({p.id: p for p in upserts if p.id in owned}.values())
            self._publish(upserts, closed)
            self._board_update(board_id, sched, ", last_error = null, last_ok_at = %s, list_hash = %s, open_postings = "
                               "(select count(*) from bank_postings where board_id = %s and closed_at is null)",
                               (now, list_hash, board_id))

    def _publish(self, upserts: List[Posting], closed: List[str]) -> None:
        """Same transaction as the bank write: the site never misses a change the bank took."""
        rows = job_bank_rows(upserts)
        if rows:
            self.conn.execute("select bank_publish(%s)", (Jsonb(rows),))
        published = {r["source_id"] for r in rows}
        removed = list(dict.fromkeys([*closed, *(p.id for p in upserts if p.id[:300] not in published)]))
        if removed:
            self.conn.execute("select bank_unpublish(%s)", (removed,))

    def publish_open(self, page: int = 5000) -> int:
        """Every open posting the site would show, onto it: the catch-up for postings the bank
        took before crawls published. A re-run rewrites the same rows."""
        sent, after = 0, ""
        while True:
            batch = self.query("select id, source, company_name, title, url, locations, workplace, employment_type, "
                               "apply_url, posted_at from bank_postings where closed_at is null and id > %s "
                               "order by id limit %s", (after, page))
            if not batch:
                return sent
            after = batch[-1]["id"]
            rows = job_bank_rows([Posting(**{**r, "posted_at": r["posted_at"] and r["posted_at"].isoformat()})
                                  for r in batch])
            if rows:
                sent += self.query("select bank_publish(%s) as n", (Jsonb(rows),))[0]["n"]

    def touch_published(self) -> int:
        return self.query("select bank_touch_published() as n")[0]["n"]

    # --- Jobright rows that open only the employer's homepage (jobboards/resolve.py) ---

    def jobright_unresolved(self, limit: int) -> List[Dict[str, Any]]:
        return self.query("select * from bank_jobright_unresolved(%s)", (limit,))

    def board_owners(self) -> List[Dict[str, Any]]:
        """Each board: the company it was found for, where, whether it was ever read,
        and the company names its open postings carry (a Common Crawl board has none of its own)."""
        return self.query(
            "select b.id, b.company_name, b.evidence_source, b.last_ok_at, "
            "coalesce(array_agg(distinct p.company_name) filter (where p.company_name is not null), '{}') "
            "as posting_names from bank_boards b "
            "left join bank_postings p on p.board_id = b.id and p.closed_at is null group by b.id")

    def open_postings_of(self, board_ids: List[str]) -> List[Dict[str, Any]]:
        return self.query("select id, board_id, title, locations, url from bank_postings "
                          "where closed_at is null and board_id = any(%s) order by id", (board_ids,))

    def save_jobright_links(self, rows: List[Tuple[Any, Optional[str], Optional[str], Optional[float]]]) -> None:
        """(job_id, posting_id, url, score) per Jobright row looked up; no posting: none found yet."""
        if not rows:
            return
        with self.lock, self.conn.cursor() as cur:
            cur.executemany(
                "insert into job_bank_jobright_links (job_id, posting_id, url, score, tried_at) "
                "values (%s, %s, %s, %s, now()) on conflict (job_id) do update set posting_id = excluded.posting_id, "
                "url = excluded.url, score = excluded.score, tried_at = excluded.tried_at", rows)

    def drop_closed_jobright_links(self) -> int:
        """Links whose posting closed or was pruned: those rows are looked up again."""
        with self.lock:
            return self.conn.execute(
                "delete from job_bank_jobright_links l where l.posting_id is not null and not exists "
                "(select 1 from bank_postings b where b.id = l.posting_id and b.closed_at is null)").rowcount

    def mark_unchanged(self, board_id: str, sched: Dict[str, Any], now: datetime) -> None:
        with self.lock:
            self._board_update(board_id, sched, ", last_error = null, last_ok_at = %s", (now,))

    def fail(self, board_id: str, error: str, sched: Dict[str, Any], now: datetime) -> None:
        with self.lock, self.conn.transaction():
            dead = sched["status"] == "dead"
            if dead:  # the board is gone or has failed for a week: its postings come down
                closed = [r["id"] for r in self.conn.execute(
                    "update bank_postings set closed_at = %s, changed_at = %s "
                    "where board_id = %s and closed_at is null returning id", (now, now, board_id))]
                self._publish([], closed)
            self._board_update(board_id, sched, ", last_error = %s" + (", open_postings = 0" if dead else ""),
                               (error,))

    def start_run(self, kind: str) -> int:
        return self.query("insert into bank_runs (kind) values (%s) returning id", (kind,))[0]["id"]

    def finish_run(self, run_id: int, stats: Dict[str, Any]) -> None:
        self.query("update bank_runs set finished_at = now(), stats = %s where id = %s", (Jsonb(stats), run_id))

    def prune(self, days: int = 30) -> int:
        with self.lock:
            return self.conn.execute("delete from bank_postings where closed_at < now() - make_interval(days => %s)",
                                     (days,)).rowcount

    def done_items(self, source: str) -> set:
        return {r["item"] for r in self.query("select item from bank_discovery where source = %s", (source,))}

    def mark_done(self, source: str, item: str) -> None:
        self.query("insert into bank_discovery (source, item) values (%s, %s) on conflict do nothing", (source, item))

    def stats(self) -> List[Dict[str, Any]]:
        return self.query("select provider, status, count(*) as boards, sum(open_postings) as open_postings, "
                          "count(*) filter (where fail_count > 0) as failing from bank_boards group by 1, 2 order by 1, 2")

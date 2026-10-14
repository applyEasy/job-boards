"""Bank writes against a real, throwaway Postgres.

Set BANK_TEST_DATABASE_URL to an empty database you own (it is wiped);
without it these tests are skipped.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
DSN = os.getenv("BANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="BANK_TEST_DATABASE_URL not set")

from jobboards.bank import Bank  # noqa: E402
from jobboards.boards import Board  # noqa: E402
from jobboards.crawl import crawl_board  # noqa: E402
from jobboards.readers import Listing, NotABoard, Posting, ReadFailed  # noqa: E402

SQL = Path(__file__).parent.parent / "sql"
T0 = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def bank():
    with psycopg.connect(DSN, autocommit=True) as conn:
        for role in ("anon", "authenticated", "service_role"):  # Supabase's roles the migration grants to
            conn.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname = '{role}') then "
                         f"create role {role} nologin; end if; end $$")
        # Tables only: the role may hold grants in another database of this cluster.
        conn.execute("drop table if exists bank_postings, bank_boards, bank_cursors, bank_discovery, bank_runs")
        conn.execute((SQL / "20261014_boards_bank.sql").read_text())
    b = Bank(DSN)
    b.add_boards([(Board("greenhouse", "acme", "Acme"), "https://boards.greenhouse.io/acme", "test")])
    yield b
    b.close()


def post(n, title="Engineer"):
    return Posting(id=f"gh-acme-{n}", source="ats_greenhouse", company_name="Acme", title=title,
                   url=f"https://boards.greenhouse.io/acme/jobs/{n}", locations=["NYC"], posted_at="2026-10-01")


def reader_of(*answers):
    answers = list(answers)

    def read(board, http, known):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    return read


def rows(bank):
    return {r["id"]: r for r in bank.query("select * from bank_postings order by id")}


def the_board(bank):
    return bank.query("select * from bank_boards where id = 'greenhouse:acme'")[0]


def test_first_read_stores_every_posting_and_activates_the_board(bank):
    crawl_board(bank, the_board(bank), None, T0, read=reader_of(Listing([post(1), post(2)], True)))
    got = rows(bank)
    assert set(got) == {"gh-acme-1", "gh-acme-2"} and got["gh-acme-1"]["locations"] == ["NYC"]
    b = the_board(bank)
    assert b["status"] == "active" and b["open_postings"] == 2 and b["list_hash"]


def test_unchanged_listing_writes_no_posting(bank):
    crawl_board(bank, the_board(bank), None, T0, read=reader_of(Listing([post(1)], True)))
    first = rows(bank)["gh-acme-1"]["changed_at"]
    crawl_board(bank, the_board(bank), None, T0 + timedelta(hours=6), read=reader_of(Listing([post(1)], True)))
    assert rows(bank)["gh-acme-1"]["changed_at"] == first
    assert the_board(bank)["interval_hours"] == 12


def test_edit_close_and_reopen(bank):
    crawl_board(bank, the_board(bank), None, T0, read=reader_of(Listing([post(1), post(2)], True)))
    t1 = T0 + timedelta(hours=6)
    crawl_board(bank, the_board(bank), None, t1, read=reader_of(Listing([post(1, "Lead")], True)))
    got = rows(bank)
    assert got["gh-acme-1"]["title"] == "Lead" and got["gh-acme-1"]["changed_at"] == t1
    assert got["gh-acme-2"]["closed_at"] == t1 and the_board(bank)["open_postings"] == 1
    t2 = t1 + timedelta(hours=6)
    crawl_board(bank, the_board(bank), None, t2, read=reader_of(Listing([post(1, "Lead"), post(2)], True)))
    got = rows(bank)
    assert got["gh-acme-2"]["closed_at"] is None and got["gh-acme-2"]["changed_at"] == t2
    assert got["gh-acme-2"]["first_seen_at"] == T0


def test_incomplete_listing_closes_nothing_and_stores_no_hash(bank):
    crawl_board(bank, the_board(bank), None, T0, read=reader_of(Listing([post(1), post(2)], True)))
    crawl_board(bank, the_board(bank), None, T0 + timedelta(hours=1), read=reader_of(Listing([post(1)], False)))
    assert rows(bank)["gh-acme-2"]["closed_at"] is None and the_board(bank)["list_hash"] is None


def test_failures_keep_postings_until_the_board_is_dead(bank):
    crawl_board(bank, the_board(bank), None, T0, read=reader_of(Listing([post(1)], True)))
    crawl_board(bank, the_board(bank), None, T0 + timedelta(hours=1), read=reader_of(ReadFailed("HTTP 500")))
    b = the_board(bank)
    assert b["fail_count"] == 1 and b["last_error"] == "ReadFailed: HTTP 500" and rows(bank)["gh-acme-1"]["closed_at"] is None
    late = T0 + timedelta(days=8)
    crawl_board(bank, the_board(bank), None, late, read=reader_of(ReadFailed("HTTP 500")))
    assert the_board(bank)["status"] == "dead" and rows(bank)["gh-acme-1"]["closed_at"] == late


def test_never_read_board_that_is_not_found_is_dead_at_once(bank):
    crawl_board(bank, the_board(bank), None, T0, read=reader_of(NotABoard("HTTP 404")))
    assert the_board(bank)["status"] == "dead"


def test_add_boards_keeps_the_first_evidence(bank):
    added = bank.add_boards([(Board("greenhouse", "ACME"), "https://job-boards.greenhouse.io/ACME", "other"),
                             (Board("lever", "zoox"), "https://jobs.lever.co/zoox", "test")])
    assert added == 1 and the_board(bank)["evidence_source"] == "test"


def test_due_boards_are_oldest_first_and_skip_future_ones(bank):
    bank.add_boards([(Board("lever", "zoox"), "https://jobs.lever.co/zoox", "test")])
    bank.query("update bank_boards set next_check_at = now() + interval '1 day' where id = 'lever:zoox'")
    assert [b["id"] for b in bank.due_boards(10)] == ["greenhouse:acme"]


def test_stats_counts_boards_postings_and_failing_ones(bank):
    crawl_board(bank, the_board(bank), None, T0, read=reader_of(Listing([post(1), post(2)], True)))
    bank.add_boards([(Board("lever", "zoox"), "https://jobs.lever.co/zoox", "test")])
    crawl_board(bank, bank.query("select * from bank_boards where id = 'lever:zoox'")[0], None, T0,
                read=reader_of(ReadFailed("HTTP 500")))
    got = {(r["provider"], r["status"]): r for r in bank.stats()}
    assert got[("greenhouse", "active")]["open_postings"] == 2 and got[("lever", "new")]["failing"] == 1

from datetime import datetime, timedelta, timezone

from jobboards.crawl import diff, list_hash, schedule
from jobboards.readers import Listing, Posting

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def post(n, title="Engineer"):
    return Posting(id=f"gh-acme-{n}", source="ats_greenhouse", company_name="Acme", title=title,
                   url=f"https://boards.greenhouse.io/acme/jobs/{n}")


def stored(*postings, closed=()):
    return {p.id: (p.content_hash(), p.id not in closed) for p in postings}


def test_new_changed_reopened_and_closed():
    a, b, c, d = post(1), post(2), post(3), post(4)
    before = stored(a, b, c, d, closed={d.id})
    listing = Listing([a, post(2, "Senior Engineer"), post(5), d], complete=True)
    upserts, closed = diff(before, listing)
    assert {p.id for p in upserts} == {b.id, "gh-acme-5", d.id}  # changed, new, reopened
    assert closed == [c.id]


def test_incomplete_listing_never_closes():
    a, b = post(1), post(2)
    upserts, closed = diff(stored(a, b), Listing([a], complete=False))
    assert upserts == [] and closed == []


def test_kept_postings_are_neither_written_nor_closed():
    a, b = post(1), post(2)
    upserts, closed = diff(stored(a, b), Listing([], complete=True, keep={a.id, b.id}))
    assert upserts == [] and closed == []


def test_list_hash_ignores_order_and_dates():
    a, b = post(1), post(2)
    assert list_hash(Listing([a, b], True)) == list_hash(Listing([b, a], True))
    from dataclasses import replace
    assert list_hash(Listing([replace(a, posted_at="2020-01-01"), b], True)) == list_hash(Listing([a, b], True))
    assert list_hash(Listing([a], True)) != list_hash(Listing([a, b], True))


def board(**kw):
    row = {"status": "active", "interval_hours": 6.0, "fail_count": 0, "failing_since": None, "last_ok_at": NOW}
    row.update(kw)
    return row


def test_changed_board_is_read_again_in_an_hour():
    s = schedule(board(interval_hours=24), "changed", NOW)
    assert s["status"] == "active" and s["next_check_at"] == NOW + timedelta(hours=1) and s["fail_count"] == 0


def test_unchanged_board_waits_twice_as_long_up_to_two_days():
    assert schedule(board(interval_hours=1), "unchanged", NOW)["interval_hours"] == 2
    assert schedule(board(interval_hours=6), "unchanged", NOW)["interval_hours"] == 12
    assert schedule(board(interval_hours=48), "unchanged", NOW)["interval_hours"] == 48


def test_empty_board_is_read_weekly():
    s = schedule(board(), "empty", NOW)
    assert s["status"] == "empty" and s["next_check_at"] == NOW + timedelta(days=7)


def test_failures_back_off_and_keep_postings_for_a_week():
    s = schedule(board(fail_count=2, failing_since=NOW - timedelta(days=1)), "failed", NOW)
    assert s["status"] == "active" and s["fail_count"] == 3 and s["next_check_at"] == NOW + timedelta(hours=4)
    assert s["failing_since"] == NOW - timedelta(days=1)


def test_a_week_of_failures_makes_a_board_dead():
    s = schedule(board(fail_count=9, failing_since=NOW - timedelta(days=7, minutes=1)), "failed", NOW)
    assert s["status"] == "dead" and s["next_check_at"] == NOW + timedelta(days=30)


def test_first_read_not_found_is_dead_at_once_but_a_known_board_gets_the_week():
    assert schedule(board(status="new", last_ok_at=None), "not_a_board", NOW)["status"] == "dead"
    s = schedule(board(), "not_a_board", NOW)
    assert s["status"] == "active" and s["fail_count"] == 1 and s["failing_since"] == NOW


def test_a_dead_board_that_answers_again_is_active():
    s = schedule(board(status="dead", fail_count=12, failing_since=NOW - timedelta(days=40)), "changed", NOW)
    assert s["status"] == "active" and s["failing_since"] is None


# --- one read of one board, against a bank kept in memory ---

from jobboards.crawl import DEAD_AFTER_DAYS, DUE_EARLY_MINUTES, crawl_board  # noqa: E402
from jobboards.readers import ReadFailed  # noqa: E402

EMPTY = Listing([], complete=True)


class MemoryBank:
    """The board row and the postings crawl_board reads and writes, as jobboards/bank.py keeps them."""

    def __init__(self):
        self.row = board(id="greenhouse:acme", provider="greenhouse", key="acme", status="new", last_ok_at=None,
                         list_hash=None, last_error=None)
        self.postings = {}

    def stored(self, board_id):
        return dict(self.postings)

    def apply(self, board_id, upserts, closed, sched, list_hash, now, listed=(), listed_postings=()):
        self.listed = list(listed)
        self.listed_postings = [p.id for p in listed_postings]
        self.postings.update({p.id: (p.content_hash(), True) for p in upserts})
        self.postings.update({i: (self.postings[i][0], False) for i in closed})
        self.row.update(sched, list_hash=list_hash, last_error=None, last_ok_at=now)

    def mark_unchanged(self, board_id, sched, now):
        self.row.update(sched, last_error=None, last_ok_at=now)

    def fail(self, board_id, error, sched, now):
        if sched["status"] == "dead":
            self.postings = {i: (h, False) for i, (h, _) in self.postings.items()}
        self.row.update(sched, last_error=error)

    def open(self):
        return sorted(i for i, (_, is_open) in self.postings.items() if is_open)

    def read(self, answer, hours):
        """One read of the board `hours` after NOW; an exception is a read that failed."""
        def reader(board, http, known):
            if isinstance(answer, Exception):
                raise answer
            return answer
        return crawl_board(self, dict(self.row), None, NOW + timedelta(hours=hours), read=reader)


def test_one_empty_answer_closes_nothing_and_the_board_is_read_again_in_an_hour():
    bank = MemoryBank()
    bank.read(Listing([post(1), post(2)], complete=True), hours=0)
    assert bank.read(EMPTY, hours=1) == ("empty_unconfirmed", 0, 0)
    assert bank.open() == ["gh-acme-1", "gh-acme-2"]
    assert bank.row["status"] == "active" and bank.row["fail_count"] == 1
    assert bank.row["next_check_at"] == NOW + timedelta(hours=2, minutes=DUE_EARLY_MINUTES)
    assert bank.row["last_error"].startswith("EmptyListing: ")


def test_a_second_empty_answer_in_a_row_closes_the_postings_and_the_board_goes_weekly():
    bank = MemoryBank()
    bank.read(Listing([post(1), post(2)], complete=True), hours=0)
    bank.read(EMPTY, hours=1)
    assert bank.read(EMPTY, hours=2) == ("empty", 0, 2)
    assert bank.open() == []
    assert bank.row["status"] == "empty" and bank.row["fail_count"] == 0 and bank.row["last_error"] is None
    assert bank.row["next_check_at"] == NOW + timedelta(hours=2, days=7)


def test_a_failed_read_or_a_listing_between_two_empty_answers_starts_the_count_again():
    for between in (ReadFailed("HTTP 500"), Listing([post(1), post(2)], complete=True)):
        bank = MemoryBank()
        bank.read(Listing([post(1), post(2)], complete=True), hours=0)
        bank.read(EMPTY, hours=1)
        bank.read(between, hours=2)
        assert bank.read(EMPTY, hours=3) == ("empty_unconfirmed", 0, 0)
        assert bank.open() == ["gh-acme-1", "gh-acme-2"]


def test_a_board_failing_for_a_week_is_not_declared_dead_by_one_empty_answer():
    """The 7-day dead rule must not turn one empty answer into closing every posting."""
    bank = MemoryBank()
    bank.read(Listing([post(1), post(2)], complete=True), hours=0)
    bank.read(ReadFailed("HTTP 500"), hours=1)
    week_later = 1 + DEAD_AFTER_DAYS * 24 + 1
    assert bank.read(EMPTY, hours=week_later) == ("empty_unconfirmed", 0, 0)
    assert bank.row["status"] != "dead"
    assert bank.open() == ["gh-acme-1", "gh-acme-2"]
    assert bank.read(EMPTY, hours=week_later + 2) == ("empty", 0, 2)  # a second empty answer in a row closes


def test_the_second_read_after_an_empty_answer_is_not_due_for_a_full_hour():
    """A run reads boards DUE_EARLY_MINUTES before they fall due, so the recheck carries that margin."""
    bank = MemoryBank()
    bank.read(Listing([post(1), post(2)], complete=True), hours=0)
    bank.read(EMPTY, hours=1)
    due_from = bank.row["next_check_at"] - timedelta(minutes=DUE_EARLY_MINUTES)
    assert due_from >= NOW + timedelta(hours=2)


def test_a_board_that_was_already_empty_stays_on_its_weekly_schedule():
    bank = MemoryBank()
    assert bank.read(EMPTY, hours=0) == ("empty", 0, 0)  # a first read that finds nothing has nothing to protect
    assert bank.row["status"] == "empty" and bank.row["next_check_at"] == NOW + timedelta(days=7)
    assert bank.read(EMPTY, hours=7 * 24) == ("empty", 0, 0)
    assert bank.row["status"] == "empty" and bank.row["fail_count"] == 0
    assert bank.row["next_check_at"] == NOW + timedelta(days=14)


def test_a_read_that_could_not_list_the_whole_board_says_which_postings_it_did_list():
    bank = MemoryBank()
    bank.read(Listing([post(1), post(2), post(3)], complete=True), hours=0)
    assert bank.listed == []  # a complete listing closes what it dropped: nothing to vouch for
    short = Listing([post(1), post(4)], complete=False, keep={"gh-acme-2"})
    assert bank.read(short, hours=1) == ("changed", 1, 0)
    assert bank.listed == ["gh-acme-1", "gh-acme-2", "gh-acme-4"]  # read or kept, not the unlisted gh-acme-3
    assert bank.listed_postings == ["gh-acme-1", "gh-acme-4"]  # full records are safe to republish; kept ids are not
    assert bank.open() == ["gh-acme-1", "gh-acme-2", "gh-acme-3", "gh-acme-4"]


# --- parallel crawlers: shards split the due boards by the host they are read from ---

from jobboards import crawl as crawl_module  # noqa: E402
from jobboards.crawl import in_shard, rate_host  # noqa: E402

ROWS = ([{"id": f"greenhouse:co{i}", "provider": "greenhouse", "key": f"co{i}"} for i in range(40)]
        + [{"id": f"workday:t{i}", "provider": "workday", "key": f"t{i}.wd5.myworkdayjobs.com/site"} for i in range(40)]
        + [{"id": f"icims:x{i}", "provider": "icims", "key": f"careers-x{i}.icims.com"} for i in range(20)])


def test_every_board_is_in_exactly_one_shard_and_a_hosts_boards_stay_together():
    shards = [[r["id"] for r in ROWS if in_shard(r, k, 4)] for k in range(4)]
    assert sorted(sum(shards, [])) == sorted(r["id"] for r in ROWS)
    assert sum(1 for s in shards if s) > 1
    # One API host (Greenhouse's) is read by one shard, so its politeness gap holds across shards.
    assert sum(1 for s in shards if any(i.startswith("greenhouse:") for i in s)) == 1
    assert all(in_shard(r, 0, 1) for r in ROWS)


class FakeBank:
    def __init__(self):
        self.touched = 0

    def start_run(self, kind):
        return 1

    def due_boards(self, limit, early_minutes=0):
        return ROWS

    def touch_published(self):
        self.touched += 1
        return 0

    def finish_run(self, run_id, stats):
        self.stats = stats


def test_a_shard_reads_only_its_boards_and_only_shard_zero_touches_the_site(monkeypatch):
    read = []
    monkeypatch.setattr(crawl_module, "crawl_board", lambda bank, row, http, now: read.append(row["id"]) or ("unchanged", 0, 0))
    for shard in range(4):
        bank = FakeBank()
        stats = crawl_module.crawl(bank, None, shard=shard, shards=4)
        assert stats["due"] == sum(1 for r in ROWS if in_shard(r, shard, 4))
        assert bank.touched == (1 if shard == 0 else 0)
    assert sorted(read) == sorted(r["id"] for r in ROWS)
    assert len({rate_host(r) for r in ROWS if r["provider"] == "greenhouse"}) == 1

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


def test_changed_board_is_read_again_in_six_hours():
    s = schedule(board(interval_hours=24), "changed", NOW)
    assert s["status"] == "active" and s["next_check_at"] == NOW + timedelta(hours=6) and s["fail_count"] == 0


def test_unchanged_board_waits_twice_as_long_up_to_two_days():
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

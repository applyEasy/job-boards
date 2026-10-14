"""Read the boards that are due, write only what changed, and decide when
each board is read next."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from jobboards.boards import Board
from jobboards.readers import READERS, Listing, NotABoard, Posting

log = logging.getLogger("jobboards.crawl")

CHANGED_HOURS, MAX_HOURS = 6.0, 48.0
EMPTY_DAYS, DEAD_AFTER_DAYS, DEAD_RECHECK_DAYS = 7, 7, 30
NEEDS_KNOWN = {"icims"}  # readers that skip postings the bank already holds unchanged

# Boards on these platforms share one API host; the rest are their own host.
API_HOST = {"greenhouse": "boards-api.greenhouse.io", "lever": "api.lever.co", "ashby": "api.ashbyhq.com",
            "smartrecruiters": "api.smartrecruiters.com"}
GAPS = {"boards-api.greenhouse.io": 0.25, "api.lever.co": 0.25, "api.ashbyhq.com": 0.25,
        "api.smartrecruiters.com": 0.5}


def list_hash(listing: Listing) -> str:
    parts = sorted(f"{p.id}:{p.content_hash()}" for p in listing.postings) + sorted(f"{i}:kept" for i in listing.keep)
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()


def diff(stored: Dict[str, Tuple[str, bool]], listing: Listing) -> Tuple[List[Posting], List[str]]:
    """(postings to write: new, changed or back again; ids to close: only from a complete listing)."""
    upserts = [p for p in listing.postings
               if p.id not in stored or stored[p.id] != (p.content_hash(), True)]
    present = {p.id for p in listing.postings} | listing.keep
    closed = sorted(i for i, (_, is_open) in stored.items() if is_open and i not in present) if listing.complete else []
    return upserts, closed


def schedule(row: Dict[str, Any], outcome: str, now: datetime) -> Dict[str, Any]:
    """outcome: changed | unchanged | empty | failed | not_a_board."""
    interval = float(row.get("interval_hours") or CHANGED_HOURS)
    ok = {"fail_count": 0, "failing_since": None}
    if outcome == "changed":
        return {"status": "active", "interval_hours": CHANGED_HOURS,
                "next_check_at": now + timedelta(hours=CHANGED_HOURS), **ok}
    if outcome == "unchanged":
        interval = min(MAX_HOURS, max(CHANGED_HOURS, interval * 2))
        return {"status": "active", "interval_hours": interval, "next_check_at": now + timedelta(hours=interval), **ok}
    if outcome == "empty":
        return {"status": "empty", "interval_hours": EMPTY_DAYS * 24.0,
                "next_check_at": now + timedelta(days=EMPTY_DAYS), **ok}
    fails = int(row.get("fail_count") or 0) + 1
    since = row.get("failing_since") or now
    dead = (outcome == "not_a_board" and row.get("last_ok_at") is None) \
        or since <= now - timedelta(days=DEAD_AFTER_DAYS)
    if dead:
        return {"status": "dead", "interval_hours": interval, "fail_count": fails, "failing_since": since,
                "next_check_at": now + timedelta(days=DEAD_RECHECK_DAYS)}
    return {"status": row.get("status") or "new", "interval_hours": interval, "fail_count": fails,
            "failing_since": since, "next_check_at": now + timedelta(hours=min(24, 2 ** (fails - 1)))}


def crawl_board(bank: Any, row: Dict[str, Any], http: Any, now: datetime,
                read: Optional[Callable] = None) -> Tuple[str, int, int]:
    """Read one board and write what changed. Returns (outcome, postings written, postings closed)."""
    board = Board(row["provider"], row["key"], row.get("company_name") or "")
    read = read or READERS[row["provider"]]
    known = bank.known(row["id"]) if row["provider"] in NEEDS_KNOWN else {}
    try:
        listing = read(board, http, known)
    except NotABoard as exc:
        outcome, error = "not_a_board", f"NotABoard: {exc}"
    except Exception as exc:  # ReadFailed, Throttled, TooLarge, timeouts: the board waits, nothing closes
        outcome, error = "failed", f"{type(exc).__name__}: {exc}"
    else:
        digest = list_hash(listing) if listing.complete else None
        empty = listing.complete and not listing.postings and not listing.keep
        if digest and digest == row.get("list_hash"):
            outcome = "empty" if empty else "unchanged"
            bank.mark_unchanged(row["id"], schedule(row, outcome, now), now)
            return outcome, 0, 0
        upserts, closed = diff(bank.stored(row["id"]), listing)
        outcome = "empty" if empty else "changed" if (upserts or closed or not listing.complete) else "unchanged"
        bank.apply(row["id"], upserts, closed, schedule(row, outcome, now), digest, now)
        return outcome, len(upserts), len(closed)
    bank.fail(row["id"], error[:500], schedule(row, outcome, now), now)
    return outcome, 0, 0


def rate_host(row: Dict[str, Any]) -> str:
    return API_HOST.get(row["provider"]) or row["key"].split("/")[0].lower()


def crawl(bank: Any, http: Any, budget_s: float = 2400, limit: int = 50000, workers: int = 16) -> Dict[str, int]:
    """Every due board, one worker per API host, until the admission budget runs out."""
    run_id, deadline = bank.start_run("crawl"), time.monotonic() + budget_s
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in bank.due_boards(limit):
        groups[rate_host(row)].append(row)
    stats: Counter = Counter(due=sum(len(g) for g in groups.values()))
    lock = threading.Lock()

    def work(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            if time.monotonic() > deadline:
                with lock:
                    stats["left_for_next_run"] += 1
                continue
            try:
                outcome, written, closed = crawl_board(bank, row, http, datetime.now(timezone.utc))
            except Exception:  # a bank error on one board must not stop the others
                log.exception("board %s could not be saved", row["id"])
                outcome, written, closed = "bank_error", 0, 0
            with lock:
                stats[outcome] += 1
                stats[f"{row['provider']}:{outcome}"] += 1
                stats["postings_written"] += written
                stats["postings_closed"] += closed

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, groups.values()))
    bank.finish_run(run_id, dict(stats))
    return dict(stats)

"""Find boards. Every source yields (Board, evidence_url, evidence_source):
a public link on an exact ATS host, never a guessed slug."""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple
from urllib.parse import urlencode

from jobboards.boards import Board, board_from_url

log = logging.getLogger("jobboards.discover")
Found = Tuple[Board, str, str]

DIRECTORY = Path(__file__).parent / "data" / "lastround-ats-company-directory-2026-08.csv"
_DIRECTORY_URL = {"greenhouse": "https://boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
                  "ashby": "https://jobs.ashbyhq.com/{}"}

# The public GitHub job lists Backend already reads.
FEEDS = {
    "simplify_internships": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "simplify_newgrad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "speedyapply_intern": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
    "speedyapply_newgrad": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md",
}
_URL = re.compile(r"""https?://[^\s<>"'()\[\]|]+""")

CC_INDEX = "https://index.commoncrawl.org"
# CDX URL pattern -> optional CDX filter (keeps the noisy hosts to job pages).
CC_PATTERNS: Dict[str, Optional[str]] = {
    "boards.greenhouse.io/*": None,
    "job-boards.greenhouse.io/*": None,
    "jobs.lever.co/*": None,
    "jobs.ashbyhq.com/*": None,
    "jobs.smartrecruiters.com/*": None,
    "*.myworkdayjobs.com": None,
    "*.icims.com": "~url:.*/jobs/.*",
    "*.oraclecloud.com": "~url:.*CandidateExperience.*",
}


def from_directory(path: Path = DIRECTORY) -> Iterator[Found]:
    """The pinned LastRound ATS directory (CC BY 4.0, see data/*LICENSE*)."""
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            template = _DIRECTORY_URL.get((row.get("ats_vendor") or "").strip().lower())
            url = template.format(row.get("board_slug", "").strip()) if template else ""
            board = board_from_url(url)
            if board:
                yield Board(board.provider, board.key, (row.get("company_name") or "").strip()), url, \
                    "directory:lastround-2026-08"


def from_feeds(http: Any) -> Iterator[Found]:
    for name, feed_url in FEEDS.items():
        try:
            reply = http.get(feed_url)
            if reply.status_code != 200:
                log.warning("feed %s answered %d", name, reply.status_code)
                continue
            text = reply.text
        except Exception as exc:
            log.warning("feed %s failed: %s", name, exc)
            continue
        items = []
        if feed_url.endswith(".json"):
            try:
                data = json.loads(text)
                items = [(str(i.get("url") or ""), str(i.get("company_name") or "")) for i in data if isinstance(i, dict)]
            except ValueError:
                pass
        items = items or [(url, "") for url in _URL.findall(text)]
        for url, company in items:
            board = board_from_url(url)
            if board:
                yield Board(board.provider, board.key, company), url, f"feed:{name}"


def from_bank(bank: Any) -> Iterator[Found]:
    """Links inside recently changed postings that point at another board."""
    for row in bank.query("select distinct coalesce(apply_url, url) as link from bank_postings "
                          "where changed_at > now() - interval '2 days'"):
        board = board_from_url(row["link"] or "")
        if board:
            yield board, row["link"], "bank"


def _cc_get(http: Any, crawl: str, params: Dict[str, Any]) -> Optional[str]:
    try:
        reply = http.get(f"{CC_INDEX}/{crawl}-index?{urlencode(params)}")
    except Exception as exc:
        log.info("commoncrawl %s %s: %s", crawl, params.get("page", "pages"), exc)
        return None
    return reply.text if reply.status_code == 200 else None


def from_commoncrawl(http: Any, bank: Any, crawls: int = 12, budget_s: float = 1200) -> int:
    """Walk the CDX index of the newest `crawls` crawls, one URL pattern at a time,
    saving each page's boards as it goes. Finished pages are recorded in the bank
    so a day that stops part way (the index often answers 502) resumes there."""
    deadline = time.monotonic() + budget_s
    try:
        crawl_ids = [c["id"] for c in http.get(f"{CC_INDEX}/collinfo.json").json()][:crawls]
    except Exception as exc:
        log.warning("commoncrawl crawl list unavailable: %s", exc)
        return 0
    done, added = bank.done_items("commoncrawl"), 0
    for crawl in crawl_ids:
        for pattern, cdx_filter in CC_PATTERNS.items():
            if f"{crawl}|{pattern}|*" in done:
                continue
            base = {"url": pattern, **({"filter": cdx_filter} if cdx_filter else {})}
            meta = _cc_get(http, crawl, {**base, "output": "json", "showNumPages": "true"})
            try:
                pages = int(json.loads(meta)["pages"]) if meta else None
            except (ValueError, KeyError, TypeError):
                pages = None
            if pages is None:
                continue
            finished = 0
            for page in range(pages):
                item = f"{crawl}|{pattern}|{page}"
                if item in done:
                    finished += 1
                    continue
                if time.monotonic() > deadline:
                    return added
                body = _cc_get(http, crawl, {**base, "output": "text", "fl": "url", "page": page})
                if body is None:
                    continue  # tomorrow
                first: Dict[str, Found] = {}
                for url in body.split():
                    board = board_from_url(url)
                    if board and board.id not in first:
                        first[board.id] = (board, url, f"commoncrawl:{crawl}")
                added += bank.add_boards(first.values())
                bank.mark_done("commoncrawl", item)
                finished += 1
            if finished == pages:
                bank.mark_done("commoncrawl", f"{crawl}|{pattern}|*")
    return added
